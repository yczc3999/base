#!/usr/bin/env bash
# One-command downstream project bootstrap. This script must run only in a fork/clone.
set -euo pipefail

readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/provision-postgres-database.sh
source "$ROOT/scripts/lib/provision-postgres-database.sh"

usage() {
  cat <<'EOF'
usage: scripts/bootstrap-project.sh PROJECT_SLUG [PROJECT_NAME] [--plan]

PROJECT_SLUG must match [a-z][a-z0-9_]{1,30}. It becomes:
  database: PROJECT_SLUG
  role:     PROJECT_SLUG_app

The real bootstrap requires a public github.com Base `upstream` and a distinct
project (`origin` or `project`) Git remote, proving this is a downstream fork/clone.
It installs dependencies, creates the isolated PostgreSQL identity,
writes ignored runtime env files, runs migrations/tests/build, and writes PROJECT.md.
EOF
}

[[ "${1:-}" != "--help" && "${1:-}" != "-h" ]] || { usage; exit 0; }

project_slug="${1:-}"
project_name="${2:-${1:-}}"
mode="${3:-}"
if [[ "$project_name" == "--plan" ]]; then
  project_name="$project_slug"
  mode="--plan"
fi
[[ -n "$project_slug" ]] || { usage >&2; exit 2; }
[[ "$project_slug" =~ ^[a-z][a-z0-9_]{1,30}$ ]] || db_die \
  'PROJECT_SLUG must match [a-z][a-z0-9_]{1,30}'
case "$project_slug" in
  base|base_platform|base_platform_app) db_die \
    'PROJECT_SLUG is reserved by the Base Platform repository' ;;
esac
[[ -n "$project_name" && "$project_name" != *$'\n'* && "$project_name" != *$'\r'* ]] || db_die \
  'PROJECT_NAME must be one line'
[[ -z "$mode" || "$mode" == "--plan" ]] || { usage >&2; exit 2; }

db_name="$project_slug"
db_role="${project_slug}_app"

if [[ "$mode" == "--plan" ]]; then
  printf 'Project bootstrap plan: slug=%s database=%s role=%s\n' \
    "$project_slug" "$db_name" "$db_role"
  exit 0
fi

upstream_url="$(git -C "$ROOT" remote get-url upstream 2>/dev/null || true)"
project_url="$(git -C "$ROOT" remote get-url origin 2>/dev/null || \
  git -C "$ROOT" remote get-url project 2>/dev/null || true)"
[[ -n "$upstream_url" ]] || db_die \
  'missing upstream remote; this command runs only in a Base fork/clone'
[[ -n "$project_url" ]] || db_die \
  'missing project remote (origin or project)'
[[ "$project_url" != "$upstream_url" ]] || db_die \
  'project remote must differ from the Base upstream remote'
[[ "$db_name" != "base_platform" && "$db_role" != "base_platform_app" ]] || db_die \
  'downstream projects may not use the Base database identity'

command -v python3 >/dev/null || db_die 'python3 is required'
base_upstream_repository="$(
  python3 "$ROOT/scripts/base-update-ledger.py" normalize-repository \
    --remote-url "$upstream_url"
)" || db_die \
  'upstream remote must identify a public github.com OWNER/REPO Base repository'
base_version="$(tr -d '\n' < "$ROOT/VERSION")"
if [[ -f "$ROOT/PROJECT.md" ]]; then
  PROJECT_FILE="$ROOT/PROJECT.md" PROJECT_SLUG_VALUE="$project_slug" \
    DB_NAME_VALUE="$db_name" DB_ROLE_VALUE="$db_role" BASE_VERSION_VALUE="$base_version" \
    BASE_REPOSITORY_VALUE="$base_upstream_repository" \
    python3 - <<'PY'
import os
from pathlib import Path

values = {}
for line in Path(os.environ["PROJECT_FILE"]).read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key in values:
            raise SystemExit(f"PROJECT.md contains duplicate key: {key}")
        values[key] = value
expected = {
    "PROJECT_SLUG": os.environ["PROJECT_SLUG_VALUE"],
    "DATABASE_NAME": os.environ["DB_NAME_VALUE"],
    "DATABASE_USER": os.environ["DB_ROLE_VALUE"],
    "BASE_UPSTREAM_REPOSITORY": os.environ["BASE_REPOSITORY_VALUE"],
    "BASE_UPSTREAM_VERSION": os.environ["BASE_VERSION_VALUE"],
}
if values.get("BASE_UPSTREAM_REPOSITORY", "").casefold() != expected[
    "BASE_UPSTREAM_REPOSITORY"
].casefold():
    raise SystemExit("PROJECT.md Base upstream repository does not match upstream remote")
expected.pop("BASE_UPSTREAM_REPOSITORY")
if any(values.get(key) != value for key, value in expected.items()):
    raise SystemExit("PROJECT.md identity/version does not match this bootstrap invocation")
PY
fi

command -v npm >/dev/null || db_die 'npm is required'
command -v openssl >/dev/null || db_die 'openssl is required'

if [[ "${BOOTSTRAP_SKIP_INSTALL:-0}" != 1 ]]; then
  if [[ ! -x "$ROOT/serve/.venv/bin/python" ]]; then
    python3 -m venv "$ROOT/serve/.venv"
  fi
  "$ROOT/serve/.venv/bin/pip" install -r "$ROOT/serve/requirements-dev.txt"
  (cd "$ROOT/admin" && npm ci)
fi

[[ -x "$ROOT/serve/.venv/bin/python" ]] || db_die \
  'serve/.venv is missing; unset BOOTSTRAP_SKIP_INSTALL or install dependencies'

db_password="$(openssl rand -base64 36 | tr -d '\n')"
provision_postgres_database \
  "$db_name" "$db_role" "$db_password" "$project_slug" "Project"

PROJECT_TITLE="$project_name" python3 - "$ROOT/admin" <<'PY'
import os
import sys
from pathlib import Path

admin = Path(sys.argv[1])
path = admin / ".env"
source = path.read_text() if path.exists() else (admin / ".env.production").read_text()
updates = {"VITE_APP_TITLE": os.environ["PROJECT_TITLE"]}
lines = []
seen = set()
for line in source.splitlines():
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

if [[ "${BOOTSTRAP_SKIP_CHECKS:-0}" != 1 ]]; then
  (
    cd "$ROOT/serve"
    .venv/bin/python -m app.routes check
    .venv/bin/pytest
  )
  (
    cd "$ROOT/admin"
    npm run lint
    npm run build
  )
fi

if [[ ! -f "$ROOT/PROJECT.md" ]]; then
  verification_status="PASS: route check, backend pytest, frontend lint/build"
  if [[ "${BOOTSTRAP_SKIP_CHECKS:-0}" == 1 ]]; then
    verification_status="SKIPPED: BOOTSTRAP_SKIP_CHECKS=1 fixture/CI mode"
  fi
  python3 "$ROOT/scripts/base-update-ledger.py" initialize \
    --project "$ROOT/PROJECT.md" \
    --history "$ROOT/BASE_UPDATES.md" \
    --project-slug "$project_slug" \
    --project-name "$project_name" \
    --db-name "$db_name" \
    --db-user "$db_role" \
    --upstream-repository "$base_upstream_repository" \
    --version "$base_version" \
    --commit "$(git -C "$ROOT" rev-parse HEAD)" \
    --verification-status "$verification_status"
fi

printf 'Project ready: %s; database=%s@%s; ledger=PROJECT.md\n' \
  "$project_slug" "$db_role" "$db_name"
