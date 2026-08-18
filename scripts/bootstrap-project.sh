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

The real bootstrap requires distinct `upstream` and project (`origin` or `project`)
Git remotes, proving this is a downstream fork/clone. It installs dependencies,
creates the isolated PostgreSQL identity,
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

base_version="$(tr -d '\n' < "$ROOT/VERSION")"
bootstrapped_at="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
cat > "$ROOT/PROJECT.md" <<EOF
# $project_name

PROJECT_SLUG=$project_slug
DATABASE_NAME=$db_name
DATABASE_USER=$db_role
BASE_UPSTREAM_VERSION=$base_version
BASE_UPSTREAM_TAG=base/v$base_version
BOOTSTRAPPED_AT=$bootstrapped_at
EOF

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

printf 'Project ready: %s; database=%s@%s; ledger=PROJECT.md\n' \
  "$project_slug" "$db_role" "$db_name"
