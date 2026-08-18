#!/usr/bin/env bash
# Shared PostgreSQL provisioning core. Source this file; do not execute it directly.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf 'ERROR: source this library from a provisioning entrypoint\n' >&2
  exit 2
fi

readonly DATABASE_LIB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly DATABASE_LIB_SERVE="$DATABASE_LIB_ROOT/serve"

db_die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

db_admin_psql() {
  sudo -n -u postgres psql -X -v ON_ERROR_STOP=1 "$@"
}

_validate_database_identifier() {
  local value="$1"
  [[ "$value" =~ ^[a-z][a-z0-9_]{1,62}$ ]] || db_die \
    "invalid PostgreSQL identifier: $value"
}

_write_runtime_env() {
  local app_name="$1"
  local db_name="$2"
  local db_role="$3"
  local db_password="$4"

  APP_NAME_VALUE="$app_name" DB_NAME_VALUE="$db_name" DB_ROLE_VALUE="$db_role" \
    DB_PASSWORD_VALUE="$db_password" python3 - "$DATABASE_LIB_SERVE" <<'PY'
import os
import sys
from pathlib import Path

serve = Path(sys.argv[1])
path = serve / ".env"
text = path.read_text() if path.exists() else (serve / ".env.example").read_text()
updates = {
    "APP_NAME": os.environ["APP_NAME_VALUE"],
    "DATABASE_NAME": os.environ["DB_NAME_VALUE"],
    "DATABASE_USER": os.environ["DB_ROLE_VALUE"],
    "DATABASE_PASSWORD": os.environ["DB_PASSWORD_VALUE"],
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
}

provision_postgres_database() {
  local db_name="$1"
  local db_role="$2"
  local db_password="$3"
  local app_name="$4"
  local output_label="$5"

  _validate_database_identifier "$db_name"
  _validate_database_identifier "$db_role"
  [[ -n "$db_password" ]] || db_die 'database password must not be empty'
  [[ -n "$app_name" && "$app_name" != *$'\n'* && "$app_name" != *$'\r'* ]] || db_die \
    'application name must be one line'
  command -v psql >/dev/null || db_die 'psql is required'
  command -v sudo >/dev/null || db_die \
    'passwordless sudo access to the postgres OS user is required'
  [[ -x "$DATABASE_LIB_SERVE/.venv/bin/python" ]] || db_die \
    'serve/.venv is missing; install backend dependencies first'
  [[ -x "$DATABASE_LIB_SERVE/.venv/bin/alembic" ]] || db_die \
    'alembic is missing from serve/.venv'

  # Send the password over psql stdin; never place it in process arguments or output.
  local sql_password_literal
  sql_password_literal="$(DB_PASSWORD_VALUE="$db_password" python3 - <<'PY'
import os

print("'" + os.environ["DB_PASSWORD_VALUE"].replace("'", "''") + "'")
PY
)"
  {
    printf "SELECT format('CREATE ROLE %%I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %%L', '%s', %s) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '%s') \\gexec\n" \
      "$db_role" "$sql_password_literal" "$db_role"
    printf "SELECT format('ALTER ROLE %%I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %%L', '%s', %s) \\gexec\n" \
      "$db_role" "$sql_password_literal"
  } | db_admin_psql -d postgres >/dev/null
  unset sql_password_literal

  if ! db_admin_psql -d postgres -Atqc \
    "SELECT 1 FROM pg_database WHERE datname = '$db_name'" | grep -qx 1; then
    sudo -n -u postgres createdb --owner="$db_role" --encoding=UTF8 \
      --template=template0 "$db_name"
  fi

  local owner
  owner="$(db_admin_psql -d postgres -Atqc \
    "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$db_name'")"
  [[ "$owner" == "$db_role" ]] || db_die \
    "$db_name owner is $owner, expected $db_role"

  # Only the dedicated application role may connect. PostgreSQL superusers retain
  # their intrinsic administrative authority.
  db_admin_psql -d postgres >/dev/null <<SQL
REVOKE ALL ON DATABASE $db_name FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE $db_name TO $db_role;
SELECT format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I', '$db_name', rolname)
FROM pg_roles
WHERE rolname <> '$db_role'
  AND rolname NOT LIKE 'pg\_%' ESCAPE '\'
  AND NOT rolsuper
\gexec
SELECT format('REVOKE %I FROM %I', '$db_role', member_role.rolname)
FROM pg_auth_members memberships
JOIN pg_roles granted_role ON granted_role.oid = memberships.roleid
JOIN pg_roles member_role ON member_role.oid = memberships.member
WHERE granted_role.rolname = '$db_role'
\gexec
SQL

  db_admin_psql -d "$db_name" >/dev/null <<SQL
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO $db_role;
ALTER DEFAULT PRIVILEGES FOR ROLE $db_role IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE $db_role IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE $db_role IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM PUBLIC;
SQL

  export PGPASSWORD="$db_password"
  local app_psql=(psql -X -h localhost -U "$db_role" -d "$db_name" -v ON_ERROR_STOP=1)
  local table_count
  table_count="$("${app_psql[@]}" -Atqc \
    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"

  if [[ "$table_count" == 0 ]]; then
    "${app_psql[@]}" -f "$DATABASE_LIB_SERVE/databases/init.sql" >/dev/null
    "${app_psql[@]}" -f "$DATABASE_LIB_SERVE/databases/article.sql" >/dev/null
    "${app_psql[@]}" -f "$DATABASE_LIB_SERVE/databases/tag.sql" >/dev/null
    "${app_psql[@]}" -f "$DATABASE_LIB_SERVE/databases/seo.sql" >/dev/null
    "${app_psql[@]}" -f "$DATABASE_LIB_SERVE/databases/seo_simplify.sql" >/dev/null

    local migration version prefix
    for migration in "$DATABASE_LIB_SERVE"/databases/migrations/*.sql; do
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
    for migration in "$DATABASE_LIB_SERVE"/databases/migrations/*.sql; do
      version="$(basename "$migration")"
      printf '%s\n' \
        "INSERT INTO schema_migrations(version) VALUES (:'version') ON CONFLICT DO NOTHING;" \
        | "${app_psql[@]}" -v version="$version" >/dev/null
    done
  else
    local has_ledger
    has_ledger="$("${app_psql[@]}" -Atqc \
      "SELECT to_regclass('public.schema_migrations') IS NOT NULL")"
    [[ "$has_ledger" == t ]] || db_die \
      'non-empty database has no schema_migrations ledger; refusing to adopt it'
  fi

  _write_runtime_env "$app_name" "$db_name" "$db_role" "$db_password"

  (
    cd "$DATABASE_LIB_SERVE"
    DATABASE_HOST=localhost DATABASE_PORT=5432 DATABASE_NAME="$db_name" \
      DATABASE_USER="$db_role" DATABASE_PASSWORD="$db_password" \
      .venv/bin/python -m app.migrate
    DATABASE_HOST=localhost DATABASE_PORT=5432 DATABASE_NAME="$db_name" \
      DATABASE_USER="$db_role" DATABASE_PASSWORD="$db_password" \
      .venv/bin/alembic upgrade head
  )

  local expected_migrations actual_migrations model_tables missing_tables
  expected_migrations="$(find "$DATABASE_LIB_SERVE/databases/migrations" -name '*.sql' | wc -l | tr -d ' ')"
  actual_migrations="$("${app_psql[@]}" -Atqc 'SELECT count(*) FROM schema_migrations')"
  model_tables='admin_login_logs,admin_operation_logs,admin_user_roles,admin_users,article_keywords,articles,db_backups,dict_items,dicts,files,keywords,menus,messages,publish_log,role_menus,roles,settings,users'
  missing_tables="$("${app_psql[@]}" -At -v expected="$model_tables" <<'SQL'
SELECT string_agg(name, ',' ORDER BY name)
FROM unnest(string_to_array(:'expected', ',')) AS name
WHERE to_regclass('public.' || name) IS NULL;
SQL
)"
  [[ -z "$missing_tables" ]] || db_die "missing Base tables: $missing_tables"
  [[ "$actual_migrations" == "$expected_migrations" ]] || db_die \
    "schema_migrations has $actual_migrations entries, expected $expected_migrations"

  local role_flags membership_count database_acl_violations schema_acl_violations
  role_flags="$(db_admin_psql -d postgres -Atqc \
    "SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls) FROM pg_roles WHERE rolname = '$db_role'")"
  [[ "$role_flags" == t ]] || db_die "$db_role has forbidden PostgreSQL privileges"
  membership_count="$(db_admin_psql -d postgres -Atqc \
    "SELECT count(*) FROM pg_auth_members memberships JOIN pg_roles granted_role ON granted_role.oid = memberships.roleid WHERE granted_role.rolname = '$db_role'")"
  [[ "$membership_count" == 0 ]] || db_die "$db_role is granted to another role"
  database_acl_violations="$(db_admin_psql -d postgres -Atqc \
    "SELECT count(*) FROM pg_database database, LATERAL aclexplode(database.datacl) acl LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee WHERE database.datname = '$db_name' AND (acl.grantee = 0 OR grantee.rolname <> '$db_role')")"
  [[ "$database_acl_violations" == 0 ]] || db_die \
    "$db_name grants privileges outside $db_role"
  schema_acl_violations="$(db_admin_psql -d "$db_name" -Atqc \
    "SELECT count(*) FROM pg_namespace namespace, LATERAL aclexplode(namespace.nspacl) acl LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee WHERE namespace.nspname = 'public' AND (acl.grantee = 0 OR grantee.rolname NOT IN ('$db_role', 'pg_database_owner'))")"
  [[ "$schema_acl_violations" == 0 ]] || db_die \
    "public schema grants privileges outside $db_role"

  unset PGPASSWORD
  printf '%s database ready: %s@%s; ACL=owner-only; migrations=%s\n' \
    "$output_label" "$db_role" "$db_name" "$actual_migrations"
}
