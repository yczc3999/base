#!/usr/bin/env bash
set -euo pipefail

# Dynamic Git-only acceptance harness for the downstream upgrade receiver.  The
# repositories and verification toolchain below are anonymous local fixtures;
# no database, package registry, or provider is contacted.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/base-upgrade-e2e.XXXXXX")"
SECRET_VALUE="fixture-secret-do-not-log-927451"
trap '[[ "${BASE_UPGRADE_E2E_KEEP_TMP:-0}" == "1" ]] || rm -rf "$TMP_ROOT"' EXIT

export GIT_AUTHOR_NAME="Base E2E Fixture"
export GIT_AUTHOR_EMAIL="base-e2e@example.invalid"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
export GIT_CONFIG_NOSYSTEM=1
mkdir -p "$TMP_ROOT/bin"
cat >"$TMP_ROOT/bin/npm" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "fixture npm PASS: $*"
SH
chmod +x "$TMP_ROOT/bin/npm"
export PATH="$TMP_ROOT/bin:$PATH"

fail() {
  printf 'E2E assertion failed: %s\n' "$*" >&2
  exit 1
}

assert_eq() {
  [[ "$1" == "$2" ]] || fail "expected '$2', got '$1'${3:+ ($3)}"
}

assert_contains() {
  grep -Fq -- "$2" "$1" || fail "$1 does not contain: $2"
}

assert_not_contains() {
  if grep -Fq -- "$2" "$1"; then
    fail "$1 unexpectedly contains: $2"
  fi
}

json_field() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
for component in sys.argv[2].split("."):
    value = value[component]
if value is None:
    print("null")
elif isinstance(value, bool):
    print(str(value).lower())
else:
    print(value)
PY
}

write_manifest() {
  local path="$1" version="$2" node_id="$3" changed_file="$4"
  cat >"$path" <<JSON
{
  "schema_version": 1,
  "version": "$version",
  "tag": "base/v$version",
  "released_at": "2026-08-18",
  "semver": "MINOR",
  "summary": "Anonymous fixture release $version",
  "nodes": [{
    "id": "$node_id",
    "kind": "changed",
    "scope": "fixture",
    "summary": "Exercise generic downstream synchronization",
    "files": ["$changed_file"]
  }],
  "compatibility": ["Compatible anonymous fixture."],
  "migrations": ["No database migration."],
  "downstream_actions": ["Run the generic receiver."],
  "conflict_hotspots": ["$changed_file"],
  "verify": ["Run fixture verification."],
  "rollback": ["Abort or revert the atomic merge commit."]
}
JSON
}

write_verification_stubs() {
  local repo="$1"
  mkdir -p "$repo/serve/.venv/bin" "$repo/admin" "$repo/scripts/lib"
  cat >"$repo/serve/.venv/bin/python" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == "-m pip install"* ]]; then
  [[ -z "${FIXTURE_INSTALL_LOG:-}" ]] || printf 'pip install invoked\n' >>"$FIXTURE_INSTALL_LOG"
  if [[ "${FIXTURE_RESTORE_PYTEST:-0}" == "1" ]]; then
    cat >"$(dirname "$0")/pytest" <<'PYTEST'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${FIXTURE_VERIFY_FAIL:-0}" == "1" ]]; then
  echo "fixture pytest failed: Bearer ${GH_TOKEN:-unset}" >&2
  exit 19
fi
echo "fixture pytest PASS"
PYTEST
    chmod +x "$(dirname "$0")/pytest"
  fi
fi
if [[ "${FIXTURE_VERIFY_FAIL:-0}" == "1" && "$*" == *"pytest"* ]]; then
  echo "fixture pytest failed: Bearer ${GH_TOKEN:-unset}" >&2
  exit 19
fi
echo "fixture python PASS: $*"
SH
  cat >"$repo/serve/.venv/bin/pip" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "fixture pip PASS"
SH
  cat >"$repo/serve/.venv/bin/pytest" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${FIXTURE_VERIFY_FAIL:-0}" == "1" ]]; then
  echo "fixture pytest failed: Bearer ${GH_TOKEN:-unset}" >&2
  exit 19
fi
echo "fixture pytest PASS"
SH
  chmod +x "$repo/serve/.venv/bin/python" "$repo/serve/.venv/bin/pip" \
    "$repo/serve/.venv/bin/pytest"
  : >"$repo/scripts/requirements.txt"
  : >"$repo/serve/requirements-dev.txt"
  cat >"$repo/admin/package.json" <<'JSON'
{"name":"base-e2e-fixture","version":"1.0.0","scripts":{"lint":"true","build":"true"}}
JSON
  cat >"$repo/admin/package-lock.json" <<'JSON'
{"name":"base-e2e-fixture","version":"1.0.0","lockfileVersion":3,"packages":{}}
JSON
  cat >"$repo/scripts/check-database-boundary.py" <<'PY'
#!/usr/bin/env python3
print("fixture database boundary PASS")
PY
}

create_base_repository() {
  local name="$1" ancestry_mode="${2:-linear}"
  local work="$TMP_ROOT/$name-base-work" bare="$TMP_ROOT/$name-base.git"
  git init --bare --initial-branch=main "$bare" >/dev/null
  git init --initial-branch=main "$work" >/dev/null
  git -C "$work" remote add origin "$bare"
  mkdir -p "$work/scripts/lib" "$work/scripts/schemas" "$work/releases" "$work/serve" "$work/admin"
  cp "$ROOT/scripts/run-base-upgrade.sh" "$work/scripts/run-base-upgrade.sh"
  cp "$ROOT/scripts/sync-base-release.sh" "$work/scripts/sync-base-release.sh"
  cp "$ROOT/scripts/base-update-ledger.py" "$work/scripts/base-update-ledger.py"
  cp "$ROOT/scripts/lib/base_release.py" "$work/scripts/lib/base_release.py"
  cp "$ROOT/scripts/schemas/base-upgrade-result.schema.json" "$work/scripts/schemas/"
  chmod +x "$work/scripts/run-base-upgrade.sh" "$work/scripts/sync-base-release.sh" \
    "$work/scripts/base-update-ledger.py"
  write_verification_stubs "$work"
  printf '__pycache__/\n*.py[cod]\n' >"$work/.gitignore"
  write_manifest "$work/releases/base-v1.0.0.json" "1.0.0" \
    "fixture.initial-release" "fixture.txt"
  printf 'base fixture v1\n' >"$work/fixture.txt"
  mkdir -p "$work/docs"
  printf 'base fixture note v1\n' >"$work/docs/[fixture]\`note\`.md"
  git -C "$work" add .
  git -C "$work" commit -m "fixture: base v1.0.0" >/dev/null
  git -C "$work" tag -a "base/v1.0.0" -m "immutable fixture v1.0.0"
  if [[ "$ancestry_mode" == "divergent" ]]; then
    git -C "$work" checkout --orphan divergent-target >/dev/null 2>&1
    git -C "$work" rm -rf . >/dev/null
    mkdir -p "$work/scripts/lib" "$work/scripts/schemas" "$work/releases"
    cp "$ROOT/scripts/run-base-upgrade.sh" "$work/scripts/run-base-upgrade.sh"
    cp "$ROOT/scripts/sync-base-release.sh" "$work/scripts/sync-base-release.sh"
    cp "$ROOT/scripts/base-update-ledger.py" "$work/scripts/base-update-ledger.py"
    cp "$ROOT/scripts/lib/base_release.py" "$work/scripts/lib/base_release.py"
    cp "$ROOT/scripts/schemas/base-upgrade-result.schema.json" "$work/scripts/schemas/"
    chmod +x "$work/scripts/run-base-upgrade.sh" "$work/scripts/sync-base-release.sh" \
      "$work/scripts/base-update-ledger.py"
    write_verification_stubs "$work"
    write_manifest "$work/releases/base-v1.1.0.json" "1.1.0" \
      "fixture.divergent-release" "fixture.txt"
    printf 'divergent base fixture v1.1\n' >"$work/fixture.txt"
    git -C "$work" add .
    git -C "$work" commit -m "fixture: divergent base v1.1.0" >/dev/null
  else
    write_manifest "$work/releases/base-v1.1.0.json" "1.1.0" \
      "fixture.upgrade-release" "fixture.txt"
    printf 'base fixture v1.1\n' >"$work/fixture.txt"
    printf 'base fixture note v1.1\n' >"$work/docs/[fixture]\`note\`.md"
    git -C "$work" add .
    git -C "$work" commit -m "fixture: base v1.1.0" >/dev/null
  fi
  git -C "$work" tag -a "base/v1.1.0" -m "immutable fixture v1.1.0"
  git -C "$work" push origin 'refs/heads/*:refs/heads/*' --tags >/dev/null
  printf '%s\n' "$bare"
}

create_downstream_repository() {
  local name="$1" base_bare="$2" mode="${3:-normal}"
  local seed="$TMP_ROOT/$name-seed" bare="$TMP_ROOT/$name-downstream.git"
  git init --bare --initial-branch=main "$bare" >/dev/null
  if [[ "$mode" == "nonancestor-current" ]]; then
    git init --initial-branch=main "$seed" >/dev/null
    printf 'independent downstream root\n' >"$seed/fixture.txt"
    mkdir -p "$seed/scripts/lib" "$seed/scripts/schemas"
    cp "$ROOT/scripts/run-base-upgrade.sh" "$seed/scripts/run-base-upgrade.sh"
    cp "$ROOT/scripts/sync-base-release.sh" "$seed/scripts/sync-base-release.sh"
    cp "$ROOT/scripts/base-update-ledger.py" "$seed/scripts/base-update-ledger.py"
    cp "$ROOT/scripts/lib/base_release.py" "$seed/scripts/lib/base_release.py"
    cp "$ROOT/scripts/schemas/base-upgrade-result.schema.json" "$seed/scripts/schemas/"
    chmod +x "$seed/scripts/"*.sh "$seed/scripts/base-update-ledger.py"
    write_verification_stubs "$seed"
    printf '__pycache__/\n*.py[cod]\n' >"$seed/.gitignore"
  else
    git clone --quiet --branch main "$base_bare" "$seed"
    git -C "$seed" switch -C main refs/tags/base/v1.0.0 >/dev/null 2>&1
  fi
  git -C "$seed" remote remove origin 2>/dev/null || true
  git -C "$seed" remote add upstream "$base_bare"
  git -C "$seed" remote add origin "$bare"
  local base_commit
  base_commit="$(git --git-dir="$base_bare" rev-parse 'refs/tags/base/v1.0.0^{commit}')"
  git -C "$seed" fetch upstream --tags >/dev/null 2>&1
  (
    cd "$seed"
    python3 scripts/base-update-ledger.py initialize \
      --project-slug fixture_project \
      --project-name "Anonymous Fixture Project" \
      --db-name fixture_project \
      --db-user fixture_project_app \
      --version 1.0.0 \
      --ref refs/tags/base/v1.0.0 \
      --commit "$base_commit" \
      --upstream-repository fixture-owner/base-fixture \
      --verification-status "PASS: fixture baseline"
  ) >/dev/null
  git -C "$seed" add .
  git -C "$seed" commit -m "fixture: initialize downstream ledgers" >/dev/null
  git -C "$seed" push -u origin main >/dev/null
  git --git-dir="$bare" symbolic-ref HEAD refs/heads/main
  printf '%s\n' "$bare"
}

clone_case() {
  local name="$1" downstream_bare="$2" base_bare="$3"
  local clone="$TMP_ROOT/$name-run"
  git clone --quiet "$downstream_bare" "$clone"
  git -C "$clone" remote add upstream "$base_bare"
  # .venv is correctly absent from Git; populate only executable fixture shims
  # after clone so --install-deps never contacts a registry.
  write_verification_stubs "$clone"
  printf '%s\n' "$clone"
}

run_receiver() {
  local clone="$1" campaign="$2" target="${3:-1.1.0}"
  local artifact_dir="$clone/.base-upgrade"
  local result="$artifact_dir/result.json" summary="$artifact_dir/summary.md" log="$artifact_dir/receiver.log"
  # Result paths are intentionally inside the checkout to exercise caller paths,
  # but CI artifacts are not repository changes and must not trip the clean-tree
  # gate (the redirection creates the log before the runner starts).
  cat >>"$(git -C "$clone" rev-parse --absolute-git-dir)/info/exclude" <<'EOF'
/.base-upgrade/
EOF
  mkdir -p "$artifact_dir"
  rm -f "$result" "$summary" "$log"
  (
    cd "$clone"
    env -u CI -u GITHUB_ACTIONS \
      BASE_UPGRADE_FIXTURE_MODE=1 \
      BASE_UPGRADE_DEFAULT_BRANCH=main \
      GITHUB_REPOSITORY=fixture-owner/downstream-fixture \
      GH_TOKEN="$SECRET_VALUE" \
      ./scripts/run-base-upgrade.sh \
        --project-id fixture_project \
        --target-version "$target" \
        --campaign-id "$campaign" \
        --allow-major false \
        --result-file "$result" \
        --summary-file "$summary"
  ) >"$log" 2>&1
}

assert_failure_result() {
  local clone="$1" expected_status="$2" expected_stage="$3"
  local result="$clone/.base-upgrade/result.json"
  [[ -f "$result" ]] || fail "runner did not write result.json"
  assert_eq "$(json_field "$result" status)" "$expected_status"
  assert_eq "$(json_field "$result" failed_stage)" "$expected_stage"
  assert_eq "$(json_field "$result" branch)" "null"
  assert_eq "$(json_field "$result" pr_url)" "null"
  assert_not_contains "$clone/BASE_UPDATES.md" "v1.1.0"
  assert_not_contains "$clone/BASE_UPDATES.md" "Verification result: PASS: route check"
}

echo "[1/9] clean atomic upgrade"
BASE_BARE="$(create_base_repository clean linear)"
DOWNSTREAM_BARE="$(create_downstream_repository clean "$BASE_BARE")"
CLEAN="$(clone_case clean "$DOWNSTREAM_BARE" "$BASE_BARE")"
DEFAULT_BEFORE="$(git --git-dir="$DOWNSTREAM_BARE" rev-parse refs/heads/main)"
run_receiver "$CLEAN" campaign-clean
assert_eq "$(json_field "$CLEAN/.base-upgrade/result.json" status)" "pr_opened"
assert_eq "$(json_field "$CLEAN/.base-upgrade/result.json" branch)" "chore/base-v1.1.0"
assert_eq "$(json_field "$CLEAN/.base-upgrade/result.json" rollback_command)" \
  "gh pr close chore/base-v1.1.0 --repo fixture-owner/downstream-fixture --delete-branch" \
  "first run may roll back only the branch and PR it created"
[[ "$(json_field "$CLEAN/.base-upgrade/result.json" verification_summary)" == \
   *"branch created by this run; pull request created by this run"* ]] || \
  fail "first run did not record its newly created branch and PR"
assert_eq "$(git --git-dir="$DOWNSTREAM_BARE" rev-parse refs/heads/main)" "$DEFAULT_BEFORE"
UPGRADE_COMMIT="$(git --git-dir="$DOWNSTREAM_BARE" rev-parse refs/heads/chore/base-v1.1.0)"
assert_eq "$(git --git-dir="$DOWNSTREAM_BARE" rev-list --parents -n1 "$UPGRADE_COMMIT" | awk '{print NF-1}')" "2" "upgrade must be one merge commit"
git --git-dir="$DOWNSTREAM_BARE" diff-tree --no-commit-id --name-only -r \
  "$UPGRADE_COMMIT^1" "$UPGRADE_COMMIT" | \
  grep -Fxq PROJECT.md || fail "atomic merge commit omitted PROJECT.md"
git --git-dir="$DOWNSTREAM_BARE" diff-tree --no-commit-id --name-only -r \
  "$UPGRADE_COMMIT^1" "$UPGRADE_COMMIT" | \
  grep -Fxq BASE_UPDATES.md || fail "atomic merge commit omitted BASE_UPDATES.md"
assert_contains "$CLEAN/PROJECT.md" "BASE_UPSTREAM_VERSION=1.1.0"
assert_contains "$CLEAN/BASE_UPDATES.md" "Base update: v1.0.0 → v1.1.0"

echo "[2/9] repeated campaign and execution are idempotent"
HISTORY_HASH="$(git --git-dir="$DOWNSTREAM_BARE" show "$UPGRADE_COMMIT:BASE_UPDATES.md" | git hash-object --stdin)"
git -C "$CLEAN" switch main >/dev/null 2>&1
run_receiver "$CLEAN" campaign-clean
assert_eq "$(json_field "$CLEAN/.base-upgrade/result.json" status)" "pr_opened"
assert_eq "$(json_field "$CLEAN/.base-upgrade/result.json" rollback_command)" \
  "no rollback required; this run created no upgrade branch or pull request" \
  "repeat run must not delete its reused branch or PR"
[[ "$(json_field "$CLEAN/.base-upgrade/result.json" verification_summary)" == \
   *"branch reused after ownership proof; pull request reused without changing Draft or Ready state"* ]] || \
  fail "repeat run did not record resource reuse and PR-state preservation"
assert_eq "$(git --git-dir="$DOWNSTREAM_BARE" for-each-ref --format='%(refname)' 'refs/heads/chore/base-v1.1.0' | wc -l | tr -d ' ')" "1"
assert_eq "$(git --git-dir="$DOWNSTREAM_BARE" for-each-ref --format='%(refname)' refs/heads | wc -l | tr -d ' ')" "2" "only main and one upgrade branch may exist"
assert_eq "$(git --git-dir="$DOWNSTREAM_BARE" rev-parse refs/heads/chore/base-v1.1.0)" "$UPGRADE_COMMIT"
assert_eq "$(git --git-dir="$DOWNSTREAM_BARE" show "$UPGRADE_COMMIT:BASE_UPDATES.md" | git hash-object --stdin)" "$HISTORY_HASH"

echo "[3/9] dirty worktree is blocked"
DIRTY="$(clone_case dirty "$DOWNSTREAM_BARE" "$BASE_BARE")"
printf 'dirty\n' >"$DIRTY/untracked.fixture"
if run_receiver "$DIRTY" campaign-dirty; then fail "dirty receiver unexpectedly succeeded"; fi
assert_failure_result "$DIRTY" blocked checkout

echo "[4/9] missing target tag is blocked"
MISSING="$(clone_case missing "$DOWNSTREAM_BARE" "$BASE_BARE")"
if run_receiver "$MISSING" campaign-missing 1.2.0; then fail "missing-tag receiver unexpectedly succeeded"; fi
assert_failure_result "$MISSING" blocked target_tag

echo "[5/9] recorded current tag must be an ancestor"
NONANCESTOR_BARE="$(create_downstream_repository nonancestor "$BASE_BARE" nonancestor-current)"
NONANCESTOR="$(clone_case nonancestor "$NONANCESTOR_BARE" "$BASE_BARE")"
NONANCESTOR_HEAD="$(git --git-dir="$NONANCESTOR_BARE" rev-parse refs/heads/main)"
if run_receiver "$NONANCESTOR" campaign-nonancestor; then fail "nonancestor receiver unexpectedly succeeded"; fi
assert_failure_result "$NONANCESTOR" blocked project_ledger
assert_eq "$(git --git-dir="$NONANCESTOR_BARE" rev-parse refs/heads/main)" "$NONANCESTOR_HEAD"

echo "[6/9] target tag ancestry error is blocked"
DIVERGENT_BASE="$(create_base_repository divergent divergent)"
DIVERGENT_DOWNSTREAM="$(create_downstream_repository divergent "$DIVERGENT_BASE")"
DIVERGENT="$(clone_case divergent "$DIVERGENT_DOWNSTREAM" "$DIVERGENT_BASE")"
if run_receiver "$DIVERGENT" campaign-divergent; then fail "divergent target unexpectedly succeeded"; fi
assert_failure_result "$DIVERGENT" blocked target_tag

echo "[7/9] conflict aborts without modifying default branch or PASS ledger"
CONFLICT_DOWNSTREAM="$(create_downstream_repository conflict "$BASE_BARE")"
CONFLICT_SEED="$TMP_ROOT/conflict-product"
git clone --quiet "$CONFLICT_DOWNSTREAM" "$CONFLICT_SEED"
printf 'downstream conflicting edit\n' >"$CONFLICT_SEED/fixture.txt"
printf 'downstream hostile-markdown filename edit\n' >"$CONFLICT_SEED/docs/[fixture]\`note\`.md"
git -C "$CONFLICT_SEED" add fixture.txt "docs/[fixture]\`note\`.md"
git -C "$CONFLICT_SEED" commit -m "fixture: downstream conflict" >/dev/null
git -C "$CONFLICT_SEED" push origin main >/dev/null
CONFLICT="$(clone_case conflict "$CONFLICT_DOWNSTREAM" "$BASE_BARE")"
CONFLICT_HEAD="$(git --git-dir="$CONFLICT_DOWNSTREAM" rev-parse refs/heads/main)"
if run_receiver "$CONFLICT" campaign-conflict; then fail "conflicting receiver unexpectedly succeeded"; fi
assert_failure_result "$CONFLICT" conflict merge
assert_eq "$(git --git-dir="$CONFLICT_DOWNSTREAM" rev-parse refs/heads/main)" "$CONFLICT_HEAD"
assert_eq "$(git -C "$CONFLICT" rev-parse HEAD)" "$CONFLICT_HEAD"
[[ ! -f "$(git -C "$CONFLICT" rev-parse --absolute-git-dir)/MERGE_HEAD" ]] || fail "conflict merge was not aborted"
[[ "$(json_field "$CONFLICT/.base-upgrade/result.json" conflict_files)" == *"fixture.txt"* ]] || fail "conflict file not reported"
[[ "$(json_field "$CONFLICT/.base-upgrade/result.json" conflict_files)" == *'docs/[fixture]`note`.md'* ]] || \
  fail "hostile-markdown conflict filename not reported"
assert_not_contains "$CONFLICT/.base-upgrade/summary.md" 'docs/[fixture]`note`.md'
assert_contains "$CONFLICT/.base-upgrade/summary.md" 'docs/&#x5B;fixture&#x5D;&#x60;note&#x60;.md'

echo "[8/9] verification failure creates no commit and leaks no secret"
VERIFY_DOWNSTREAM="$(create_downstream_repository verify "$BASE_BARE")"
VERIFY="$(clone_case verify "$VERIFY_DOWNSTREAM" "$BASE_BARE")"
VERIFY_HEAD="$(git --git-dir="$VERIFY_DOWNSTREAM" rev-parse refs/heads/main)"
export FIXTURE_VERIFY_FAIL=1
if run_receiver "$VERIFY" campaign-verify; then fail "failed verification unexpectedly succeeded"; fi
unset FIXTURE_VERIFY_FAIL
assert_failure_result "$VERIFY" verification_failed verification
assert_eq "$(git --git-dir="$VERIFY_DOWNSTREAM" rev-parse refs/heads/main)" "$VERIFY_HEAD"
assert_eq "$(git -C "$VERIFY" rev-parse HEAD)" "$VERIFY_HEAD" "verification failure must leave no local commit"
[[ ! -f "$(git -C "$VERIFY" rev-parse --absolute-git-dir)/MERGE_HEAD" ]] || fail "failed verification left an active merge"
if git --git-dir="$VERIFY_DOWNSTREAM" show-ref --verify --quiet refs/heads/chore/base-v1.1.0; then
  fail "verification failure pushed a partial upgrade branch"
fi

echo "[9/9] first adoption resumes an old no-commit merge with target dependencies"
ADOPTION_DOWNSTREAM="$(create_downstream_repository adoption "$BASE_BARE")"
ADOPTION_SEED="$TMP_ROOT/adoption-legacy"
git clone --quiet "$ADOPTION_DOWNSTREAM" "$ADOPTION_SEED"
python3 - "$ADOPTION_SEED/PROJECT.md" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = [
    line for line in path.read_text(encoding="utf-8").splitlines()
    if not line.startswith("BASE_UPSTREAM_REPOSITORY=")
]
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
git -C "$ADOPTION_SEED" add PROJECT.md
git -C "$ADOPTION_SEED" commit -m "fixture: retain legacy pre-receiver ledger" >/dev/null
git -C "$ADOPTION_SEED" push origin main >/dev/null

ADOPTION="$TMP_ROOT/adoption-run"
git clone --quiet "$ADOPTION_DOWNSTREAM" "$ADOPTION"
git -C "$ADOPTION" remote add upstream "$BASE_BARE"
git -C "$ADOPTION" fetch upstream --tags >/dev/null 2>&1
ADOPTION_BASELINE="$(git -C "$ADOPTION" rev-parse HEAD)"
ADOPTION_TARGET="$(git -C "$ADOPTION" rev-parse 'refs/tags/base/v1.1.0^{commit}')"
ADOPTION_GIT_DIR="$(git -C "$ADOPTION" rev-parse --absolute-git-dir)"
git -C "$ADOPTION" merge --no-ff --no-commit refs/tags/base/v1.1.0 >/dev/null
[[ -f "$ADOPTION_GIT_DIR/MERGE_HEAD" ]] || \
  fail "old sync fixture did not retain its no-commit merge"
rm -f "$ADOPTION/serve/.venv/bin/pytest"
if "$ADOPTION/serve/.venv/bin/pytest" >/dev/null 2>&1; then
  fail "old sync fixture unexpectedly had target verification dependencies"
fi

# The target tree now owns the new sync/ledger implementation. Supplying its
# dependencies and a trusted GitHub identity must make --continue fully atomic.
git -C "$ADOPTION" remote set-url upstream \
  https://github.com/fixture-owner/base-fixture.git
ADOPTION_INSTALL_LOG="$TMP_ROOT/adoption-install.log"
(
  cd "$ADOPTION"
  FIXTURE_RESTORE_PYTEST=1 FIXTURE_INSTALL_LOG="$ADOPTION_INSTALL_LOG" \
    ./scripts/sync-base-release.sh 1.1.0 --continue --install-deps
) >/dev/null
assert_contains "$ADOPTION_INSTALL_LOG" "pip install invoked"

ADOPTION_COMMIT="$(git -C "$ADOPTION" rev-parse HEAD)"
assert_eq "$(git -C "$ADOPTION" rev-list --first-parent --count \
  "${ADOPTION_BASELINE}..${ADOPTION_COMMIT}")" \
  "1" "first adoption recovery must add exactly one commit"
assert_eq "$(git -C "$ADOPTION" rev-list --parents -n1 "$ADOPTION_COMMIT" | awk '{print NF-1}')" \
  "2" "first adoption recovery must create one merge commit"
assert_eq "$(git -C "$ADOPTION" rev-parse "${ADOPTION_COMMIT}^1")" "$ADOPTION_BASELINE"
assert_eq "$(git -C "$ADOPTION" rev-parse "${ADOPTION_COMMIT}^2")" "$ADOPTION_TARGET"
assert_contains "$ADOPTION/PROJECT.md" \
  "BASE_UPSTREAM_REPOSITORY=fixture-owner/base-fixture"
assert_contains "$ADOPTION/PROJECT.md" "BASE_UPSTREAM_VERSION=1.1.0"
assert_contains "$ADOPTION/BASE_UPDATES.md" "Base update: v1.0.0 → v1.1.0"
git -C "$ADOPTION" diff-tree --no-commit-id --name-only -r \
  "${ADOPTION_COMMIT}^1" "$ADOPTION_COMMIT" | \
  grep -Fxq PROJECT.md || fail "first adoption merge omitted PROJECT.md"
git -C "$ADOPTION" diff-tree --no-commit-id --name-only -r \
  "${ADOPTION_COMMIT}^1" "$ADOPTION_COMMIT" | \
  grep -Fxq BASE_UPDATES.md || fail "first adoption merge omitted BASE_UPDATES.md"
[[ -x "$ADOPTION/serve/.venv/bin/pytest" ]] || \
  fail "--install-deps did not restore target verification dependencies"
[[ ! -f "$ADOPTION_GIT_DIR/MERGE_HEAD" ]] || \
  fail "first adoption recovery left an active merge"

for artifact in "$TMP_ROOT"/*-run/.base-upgrade/receiver.log \
  "$TMP_ROOT"/*-run/.base-upgrade/result.json "$TMP_ROOT"/*-run/.base-upgrade/summary.md; do
  [[ -e "$artifact" ]] || continue
  assert_not_contains "$artifact" "$SECRET_VALUE"
done

python3 - "$ROOT/scripts/schemas/base-upgrade-result.schema.json" "$TMP_ROOT" <<'PY'
import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator

schema = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
validator = Draft7Validator(schema)
for result_path in Path(sys.argv[2]).glob("*-run/.base-upgrade/result.json"):
    validator.validate(json.loads(result_path.read_text(encoding="utf-8")))
PY

echo "base upgrade dynamic Git E2E: PASS"
