#!/usr/bin/env bash
# Provision the one database reserved for the Base Platform repository itself.
set -euo pipefail

readonly DB_NAME=base_platform
readonly DB_ROLE=base_platform_app
readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=scripts/lib/provision-postgres-database.sh
source "$ROOT/scripts/lib/provision-postgres-database.sh"

[[ -n "${BASE_PLATFORM_DB_PASSWORD:-}" ]] || db_die \
  'set BASE_PLATFORM_DB_PASSWORD; it is written only to the PostgreSQL role and serve/.env'

provision_postgres_database \
  "$DB_NAME" "$DB_ROLE" "$BASE_PLATFORM_DB_PASSWORD" "base" "Base"
