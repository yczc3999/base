#!/usr/bin/env bash
# Provision the one database reserved for the Base Platform repository itself.
set -euo pipefail

readonly DB_NAME=base_platform
readonly DB_ROLE=base_platform_app
readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SERVE="$ROOT/serve"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ -n "${BASE_PLATFORM_DB_PASSWORD:-}" ]] || die \
  'set BASE_PLATFORM_DB_PASSWORD; it is written only to the PostgreSQL role and serve/.env'
command -v psql >/dev/null || die 'psql is required'
command -v sudo >/dev/null || die 'passwordless sudo access to the postgres OS user is required'

admin_psql() {
  sudo -n -u postgres psql -X -v ON_ERROR_STOP=1 "$@"
}

# Create/rotate only the fixed Base role. Database/role overrides are deliberately unsupported.
# The secret is sent over psql stdin, never placed in its command-line arguments.
sql_password_literal="$(python3 - <<'PY'
import os

print("'" + os.environ["BASE_PLATFORM_DB_PASSWORD"].replace("'", "''") + "'")
PY
)"
{
  printf "SELECT format('CREATE ROLE %%I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %%L', '%s', %s) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '%s') \\gexec\n" \
    "$DB_ROLE" "$sql_password_literal" "$DB_ROLE"
  printf "SELECT format('ALTER ROLE %%I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %%L', '%s', %s) \\gexec\n" \
    "$DB_ROLE" "$sql_password_literal"
} | admin_psql -d postgres >/dev/null
unset sql_password_literal

if ! admin_psql -d postgres -Atqc \
  "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -qx 1; then
  sudo -n -u postgres createdb --owner="$DB_ROLE" --encoding=UTF8 \
    --template=template0 "$DB_NAME"
fi

owner="$(admin_psql -d postgres -Atqc \
  "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$DB_NAME'")"
[[ "$owner" == "$DB_ROLE" ]] || die "$DB_NAME owner is $owner, expected $DB_ROLE"

# PostgreSQL superusers remain administrative principals by definition. Every normal
# role is excluded: PUBLIC has no CONNECT and nobody may inherit/set the app role.
admin_psql -d postgres >/dev/null <<SQL
REVOKE ALL ON DATABASE $DB_NAME FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE $DB_NAME TO $DB_ROLE;
SELECT format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I', '$DB_NAME', rolname)
FROM pg_roles
WHERE rolname <> '$DB_ROLE'
  AND rolname NOT LIKE 'pg\_%' ESCAPE '\'
  AND NOT rolsuper
\gexec
SELECT format('REVOKE %I FROM %I', '$DB_ROLE', member_role.rolname)
FROM pg_auth_members memberships
JOIN pg_roles granted_role ON granted_role.oid = memberships.roleid
JOIN pg_roles member_role ON member_role.oid = memberships.member
WHERE granted_role.rolname = '$DB_ROLE'
\gexec
SQL

admin_psql -d "$DB_NAME" >/dev/null <<SQL
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO $DB_ROLE;
ALTER DEFAULT PRIVILEGES FOR ROLE $DB_ROLE IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE $DB_ROLE IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE $DB_ROLE IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM PUBLIC;
SQL

export PGPASSWORD="$BASE_PLATFORM_DB_PASSWORD"
app_psql=(psql -X -h localhost -U "$DB_ROLE" -d "$DB_NAME" -v ON_ERROR_STOP=1)
table_count="$("${app_psql[@]}" -Atqc \
  "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"

if [[ "$table_count" == 0 ]]; then
  "${app_psql[@]}" -f "$SERVE/databases/init.sql" >/dev/null
  "${app_psql[@]}" -f "$SERVE/databases/article.sql" >/dev/null
  "${app_psql[@]}" -f "$SERVE/databases/tag.sql" >/dev/null
  "${app_psql[@]}" -f "$SERVE/databases/seo.sql" >/dev/null
  "${app_psql[@]}" -f "$SERVE/databases/seo_simplify.sql" >/dev/null

  for migration in "$SERVE"/databases/migrations/*.sql; do
    version="$(basename "$migration")"
    prefix="${version%%_*}"
    if (( 10#$prefix >= 15 )); then
      "${app_psql[@]}" -f "$migration" >/dev/null
    fi
  done

  "${app_psql[@]}" >/dev/null <<'SQL'
CREATE TABLE schema_migrations (
  version VARCHAR(255) PRIMARY KEY,
  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
SQL
  for migration in "$SERVE"/databases/migrations/*.sql; do
    version="$(basename "$migration")"
    printf '%s\n' \
      "INSERT INTO schema_migrations(version) VALUES (:'version') ON CONFLICT DO NOTHING;" \
      | "${app_psql[@]}" -v version="$version" >/dev/null
  done
else
  has_ledger="$("${app_psql[@]}" -Atqc \
    "SELECT to_regclass('public.schema_migrations') IS NOT NULL")"
  [[ "$has_ledger" == t ]] || die \
    'non-empty database has no schema_migrations ledger; refusing to adopt it'
fi

# Record the secret only in the ignored local runtime file, never in source or output.
DB_PASSWORD="$BASE_PLATFORM_DB_PASSWORD" python3 - "$SERVE" <<'PY'
import os
import sys
from pathlib import Path

serve = Path(sys.argv[1])
path = serve / ".env"
text = path.read_text() if path.exists() else (serve / ".env.example").read_text()
updates = {
    "DATABASE_NAME": "base_platform",
    "DATABASE_USER": "base_platform_app",
    "DATABASE_PASSWORD": os.environ["DB_PASSWORD"],
}
seen = set()
lines = []
for line in text.splitlines():
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in updates:
        lines.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        lines.append(line)
for key, value in updates.items():
    if key not in seen:
        lines.append(f"{key}={value}")
path.write_text("\n".join(lines) + "\n")
path.chmod(0o600)
PY

# Existing Base databases receive only pending, versioned SQL migrations. A non-empty
# database without the Base ledger was rejected above, so unrelated schemas are never adopted.
(
  cd "$SERVE"
  DATABASE_HOST=localhost DATABASE_PORT=5432 DATABASE_NAME="$DB_NAME" \
    DATABASE_USER="$DB_ROLE" DATABASE_PASSWORD="$BASE_PLATFORM_DB_PASSWORD" \
    .venv/bin/python -m app.migrate
)

(
  cd "$SERVE"
  DATABASE_HOST=localhost DATABASE_PORT=5432 DATABASE_NAME="$DB_NAME" \
    DATABASE_USER="$DB_ROLE" DATABASE_PASSWORD="$BASE_PLATFORM_DB_PASSWORD" \
    .venv/bin/alembic upgrade head
)

expected_migrations="$(find "$SERVE/databases/migrations" -name '*.sql' | wc -l | tr -d ' ')"
actual_migrations="$("${app_psql[@]}" -Atqc 'SELECT count(*) FROM schema_migrations')"
model_tables='admin_login_logs,admin_operation_logs,admin_user_roles,admin_users,article_keywords,articles,db_backups,dict_items,dicts,files,keywords,menus,messages,publish_log,role_menus,roles,settings,users'
missing_tables="$("${app_psql[@]}" -At -v expected="$model_tables" <<'SQL'
SELECT string_agg(name, ',' ORDER BY name)
FROM unnest(string_to_array(:'expected', ',')) AS name
WHERE to_regclass('public.' || name) IS NULL;
SQL
)"
[[ -z "$missing_tables" ]] || die "missing Base tables: $missing_tables"
[[ "$actual_migrations" == "$expected_migrations" ]] || die \
  "schema_migrations has $actual_migrations entries, expected $expected_migrations"

role_flags="$(admin_psql -d postgres -Atqc \
  "SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls) FROM pg_roles WHERE rolname = '$DB_ROLE'")"
[[ "$role_flags" == t ]] || die "$DB_ROLE has forbidden PostgreSQL privileges"
membership_count="$(admin_psql -d postgres -Atqc \
  "SELECT count(*) FROM pg_auth_members memberships JOIN pg_roles granted_role ON granted_role.oid = memberships.roleid WHERE granted_role.rolname = '$DB_ROLE'")"
[[ "$membership_count" == 0 ]] || die "$DB_ROLE is granted to another role"
database_acl_violations="$(admin_psql -d postgres -Atqc \
  "SELECT count(*) FROM pg_database database, LATERAL aclexplode(database.datacl) acl LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee WHERE database.datname = '$DB_NAME' AND (acl.grantee = 0 OR grantee.rolname <> '$DB_ROLE')")"
[[ "$database_acl_violations" == 0 ]] || die "$DB_NAME grants privileges outside $DB_ROLE"
schema_acl_violations="$(admin_psql -d "$DB_NAME" -Atqc \
  "SELECT count(*) FROM pg_namespace namespace, LATERAL aclexplode(namespace.nspacl) acl LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee WHERE namespace.nspname = 'public' AND (acl.grantee = 0 OR grantee.rolname NOT IN ('$DB_ROLE', 'pg_database_owner'))")"
[[ "$schema_acl_violations" == 0 ]] || die "public schema grants privileges outside $DB_ROLE"

printf 'Base database ready: %s@%s; ACL=owner-only; migrations=%s\n' \
  "$DB_ROLE" "$DB_NAME" "$actual_migrations"
