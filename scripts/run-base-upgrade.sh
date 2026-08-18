#!/usr/bin/env bash
# Bounded GitHub receiver for one downstream Base release update.
set -uo pipefail

readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat >&2 <<'EOF'
usage: scripts/run-base-upgrade.sh \
  --project-id ID --target-version X.Y.Z --campaign-id ID \
  --allow-major true|false --result-file PATH --summary-file PATH

The runner accepts identifiers and policy only. It never accepts a command,
remote URL, branch name, ref, token, or validation override.
EOF
}

project_id=""
target_version=""
campaign_id=""
allow_major=""
result_file=""
summary_file=""

while (( $# )); do
  case "$1" in
    --project-id|--target-version|--campaign-id|--allow-major|--result-file|--summary-file)
      (( $# >= 2 )) || { usage; exit 2; }
      value="$2"
      case "$1" in
        --project-id) project_id="$value" ;;
        --target-version) target_version="$value" ;;
        --campaign-id) campaign_id="$value" ;;
        --allow-major) allow_major="$value" ;;
        --result-file) result_file="$value" ;;
        --summary-file) summary_file="$value" ;;
      esac
      shift 2
      ;;
    *) usage; exit 2 ;;
  esac
done

if [[ -z "$project_id" || -z "$target_version" || -z "$campaign_id" || \
      -z "$allow_major" || -z "$result_file" || -z "$summary_file" ]]; then
  usage
  exit 2
fi

readonly branch="chore/base-v${target_version}"
readonly fixture_mode="${BASE_UPGRADE_FIXTURE_MODE:-0}"
source_version=""
status="blocked"
failed_stage="input_validation"
verification_summary="receiver input validation failed"
pr_url=""
conflict_file=""
exit_code=1
new_branch_pushed=0
new_pr_created=0

# Outputs may live in the checkout for local use or in GitHub's isolated
# RUNNER_TEMP. No other absolute path is accepted.
canonical_output_path() {
  python3 - "$ROOT" "${RUNNER_TEMP:-}" "$1" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
runner_temp = Path(sys.argv[2]).resolve() if sys.argv[2] else None
candidate = Path(sys.argv[3])
if not candidate.is_absolute():
    candidate = root / candidate
parent = candidate.parent.resolve()
checkout_output = root / ".base-upgrade"
boundaries = []
if not checkout_output.is_symlink():
    resolved_checkout_output = checkout_output.resolve()
    try:
        resolved_checkout_output.relative_to(root)
        boundaries.append(resolved_checkout_output)
    except ValueError:
        pass
if runner_temp is not None:
    boundaries.append(runner_temp)
if not any(parent == boundary or boundary in parent.parents for boundary in boundaries):
    raise SystemExit("output path must remain inside .base-upgrade or RUNNER_TEMP")
if candidate.name in {"", ".", ".."}:
    raise SystemExit("invalid output filename")
output = parent / candidate.name
if output.is_symlink():
    raise SystemExit("output path must not be a symlink")
protected = [
    root / "PROJECT.md",
    root / "BASE_UPDATES.md",
    root / "scripts" / "schemas" / "base-upgrade-result.schema.json",
]
if output.exists() and any(path.exists() and output.samefile(path) for path in protected):
    raise SystemExit("output path collides with a protected contract file")
print(output)
PY
}

if ! result_file="$(canonical_output_path "$result_file")" || \
   ! summary_file="$(canonical_output_path "$summary_file")"; then
  usage
  exit 2
fi
[[ "$result_file" != "$summary_file" ]] || { usage; exit 2; }
if [[ -e "$result_file" && -e "$summary_file" && "$result_file" -ef "$summary_file" ]]; then
  usage
  exit 2
fi
mkdir -p "$(dirname "$result_file")" "$(dirname "$summary_file")"

retry_command() {
  printf 'gh workflow run base-upgrade-receiver.yml --repo %s -f project_id=%s -f target_version=%s -f campaign_id=%s -f allow_major=%s' \
    "${GITHUB_REPOSITORY:-OWNER/REPO}" "$project_id" "$target_version" "$campaign_id" "$allow_major"
}

rollback_command() {
  if (( new_pr_created == 1 )); then
    printf 'gh pr close %s --repo %s' "$branch" "${GITHUB_REPOSITORY:-OWNER/REPO}"
    if (( new_branch_pushed == 1 )); then
      printf ' --delete-branch'
    fi
  elif (( new_branch_pushed == 1 )); then
    printf 'git push origin --delete %s' "$branch"
  else
    printf 'no rollback required; this run created no upgrade branch or pull request'
  fi
}

prospective_pr_rollback_command() {
  if (( pr_count == 0 )); then
    printf 'gh pr close %s --repo %s' "$branch" "${GITHUB_REPOSITORY:-OWNER/REPO}"
    if (( new_branch_pushed == 1 )); then
      printf ' --delete-branch'
    fi
  else
    printf 'no automated rollback; this run is reusing the existing branch and pull request'
  fi
}

render_update_nodes() {
  ROOT_VALUE="$ROOT" SOURCE_VERSION_VALUE="$source_version" \
    TARGET_VERSION_VALUE="$target_version" TARGET_COMMIT_VALUE="$target_commit" \
    python3 - <<'PY'
import os
import sys
from pathlib import Path

root = Path(os.environ["ROOT_VALUE"])
sys.path.insert(0, str(root / "scripts"))
from lib.base_release import load_manifests, select_manifests

manifests = load_manifests(root, os.environ["TARGET_COMMIT_VALUE"])
selected = select_manifests(
    manifests,
    os.environ["SOURCE_VERSION_VALUE"],
    os.environ["TARGET_VERSION_VALUE"],
)
if not selected:
    raise SystemExit("upgrade manifest range is empty")

def markdown(value):
    """Render trusted manifest text as inert Markdown, never as syntax or code."""

    return "".join(
        character
        if character.isascii()
        and (character.isalnum() or character in " .,:;_-/()+")
        else f"&#x{ord(character):X};"
        for character in str(value)
    )


def render_list(title, values, *, code=False):
    print(f"\n##### {title}")
    if not values:
        print("- None declared.")
        return
    for value in values:
        rendered = markdown(value)
        print(f"- `{rendered}`" if code else f"- {rendered}")

for manifest in selected:
    print(f"#### Base v{markdown(manifest['version'])}")
    print("\n##### Update nodes")
    for node in manifest["nodes"]:
        print(
            f"- `{markdown(node['id'])}` "
            f"[{markdown(node['kind'])}/{markdown(node['scope'])}]: "
            f"{markdown(node['summary'])}"
        )
    render_list("Migrations", manifest["migrations"])
    render_list("Conflict hotspots", manifest["conflict_hotspots"], code=True)
    render_list("Downstream actions", manifest["downstream_actions"])
    render_list("Release verification", manifest["verify"], code=True)
    print()
PY
}

emit_result() {
  local conflicts_json='[]'
  if [[ -n "$conflict_file" && -f "$conflict_file" ]]; then
    conflicts_json="$(python3 - "$conflict_file" <<'PY'
import json
import sys
from pathlib import Path

items = sorted({item.decode("utf-8") for item in Path(sys.argv[1]).read_bytes().split(b"\0") if item})
print(json.dumps(items, separators=(",", ":")))
PY
)"
  fi
  CAMPAIGN_ID_VALUE="$campaign_id" PROJECT_ID_VALUE="$project_id" \
    SOURCE_VERSION_VALUE="$source_version" TARGET_VERSION_VALUE="$target_version" \
    STATUS_VALUE="$status" BRANCH_VALUE="$([[ "$status" == "pr_opened" ]] && printf '%s' "$branch")" \
    PR_URL_VALUE="$pr_url" FAILED_STAGE_VALUE="$failed_stage" \
    CONFLICTS_JSON_VALUE="$conflicts_json" VERIFY_VALUE="$verification_summary" \
    RETRY_VALUE="$(retry_command)" ROLLBACK_VALUE="$(rollback_command)" \
    RESULT_FILE_VALUE="$result_file" SUMMARY_FILE_VALUE="$summary_file" \
    ROOT_VALUE="$ROOT" python3 - <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft7Validator

sys.path.insert(0, str(Path(os.environ["ROOT_VALUE"]) / "scripts"))
from lib.base_release import redact

def optional(name):
    value = os.environ.get(name, "")
    return value or None

result = {
    "campaign_id": os.environ["CAMPAIGN_ID_VALUE"],
    "project_id": os.environ["PROJECT_ID_VALUE"],
    "source_version": optional("SOURCE_VERSION_VALUE"),
    "target_version": os.environ["TARGET_VERSION_VALUE"],
    "status": os.environ["STATUS_VALUE"],
    "branch": optional("BRANCH_VALUE"),
    "pr_url": optional("PR_URL_VALUE"),
    "failed_stage": optional("FAILED_STAGE_VALUE"),
    "conflict_files": json.loads(os.environ["CONFLICTS_JSON_VALUE"]),
    "verification_summary": optional("VERIFY_VALUE"),
    "retry_command": os.environ["RETRY_VALUE"],
    "rollback_command": os.environ["ROLLBACK_VALUE"],
}
secrets = tuple(
    value for key, value in os.environ.items()
    if ("TOKEN" in key.upper() or key.upper() in {"GH_TOKEN", "GITHUB_TOKEN"}) and value
)
result = redact(result, secrets=secrets)

schema_path = Path(os.environ["ROOT_VALUE"]) / "scripts" / "schemas" / "base-upgrade-result.schema.json"
schema = json.loads(schema_path.read_text(encoding="utf-8"))
errors = sorted(Draft7Validator(schema).iter_errors(result), key=lambda error: list(error.path))
if errors:
    raise SystemExit(f"receiver result violates schema: {errors[0].message}")
expected = {
    "campaign_id": os.environ["CAMPAIGN_ID_VALUE"],
    "project_id": os.environ["PROJECT_ID_VALUE"],
    "target_version": os.environ["TARGET_VERSION_VALUE"],
}
if any(result[key] != value for key, value in expected.items()):
    raise SystemExit("redaction changed a structural receiver result field")
if result["status"] == "pr_opened":
    expected_repository = os.environ.get("GITHUB_REPOSITORY", "").casefold()
    parsed = urlsplit(result["pr_url"])
    actual_repository = "/".join(parsed.path.strip("/").split("/")[:2]).casefold()
    if actual_repository != expected_repository:
        raise SystemExit("pull request repository does not match GITHUB_REPOSITORY")

result_path = Path(os.environ["RESULT_FILE_VALUE"])
summary_path = Path(os.environ["SUMMARY_FILE_VALUE"])
with tempfile.NamedTemporaryFile(
    mode="w", encoding="utf-8", dir=result_path.parent,
    prefix=f".{result_path.name}.", delete=False,
) as handle:
    json.dump(result, handle, indent=2, sort_keys=True)
    handle.write("\n")
    result_tmp = Path(handle.name)

def markdown_text(value):
    value = str(value)
    return "".join(
        character
        if character.isascii() and (character.isalnum() or character in " .,-/")
        else f"&#x{ord(character):X};"
        for character in value
    )

lines = [
    "## Base upgrade receiver",
    "",
    f"- Campaign: {markdown_text(result['campaign_id'])}",
    f"- Project: {markdown_text(result['project_id'])}",
    f"- Version: {markdown_text(result['source_version'] or 'unknown')} to {markdown_text(result['target_version'])}",
    f"- Status: {markdown_text(result['status'])}",
    f"- Failed stage: {markdown_text(result['failed_stage'] or 'none')}",
    f"- Branch: {markdown_text(result['branch'] or 'none')}",
    f"- Pull request: {markdown_text(result['pr_url'] or 'none')}",
    f"- Verification: {markdown_text(result['verification_summary'] or 'not run')}",
]
if result["conflict_files"]:
    lines.extend(["", "### Conflict files", *[f"- {markdown_text(item)}" for item in result["conflict_files"]]])
with tempfile.NamedTemporaryFile(
    mode="w", encoding="utf-8", dir=summary_path.parent,
    prefix=f".{summary_path.name}.", delete=False,
) as handle:
    handle.write("\n".join(lines) + "\n")
    summary_tmp = Path(handle.name)
os.replace(result_tmp, result_path)
os.replace(summary_tmp, summary_path)
PY
}

finish() {
  emit_result || {
    printf 'receiver failed to write its result artifact\n' >&2
    exit 1
  }
  exit "$exit_code"
}

fail() {
  status="$1"
  failed_stage="$2"
  verification_summary="$3"
  exit_code=1
  finish
}

valid_id='^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
valid_semver='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
valid_ref='^[A-Za-z0-9]([A-Za-z0-9._/-]*[A-Za-z0-9_-])?$'

normalize_repository_slug() {
  python3 "$ROOT/scripts/base-update-ledger.py" normalize-repository \
    --remote-url "https://github.com/${1}.git" 2>/dev/null
}

validate_latest_history() {
  python3 - "$1" "$2" "$3" "$4" <<'PY'
from datetime import datetime
import re
import sys
from pathlib import Path

version, commit, synced_at, path = sys.argv[1:]
text = Path(path).read_text(encoding="utf-8")
entry = re.split(r"^---\s*$", text, flags=re.MULTILINE)[-1]
headings = re.findall(r"^## (.+)$", entry, flags=re.MULTILINE)
if len(headings) != 1:
    raise SystemExit(1)
title = headings[0]
initial = f"Initial Base adoption: v{version}"
updated = re.fullmatch(
    rf"Base update: v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*) → v{re.escape(version)}",
    title,
)
if title != initial and updated is None:
    raise SystemExit(1)
commit_lines = re.findall(r"^- Base commit: `([0-9a-f]{40})`$", entry, flags=re.MULTILINE)
if commit_lines != [commit]:
    raise SystemExit(1)
synced_lines = re.findall(r"^- Synced at: `([^`\r\n]+)`$", entry, flags=re.MULTILINE)
if synced_lines != [synced_at]:
    raise SystemExit(1)
try:
    parsed_synced_at = datetime.strptime(synced_at, "%Y-%m-%dT%H:%M:%SZ")
except ValueError:
    raise SystemExit(1)
if parsed_synced_at.strftime("%Y-%m-%dT%H:%M:%SZ") != synced_at:
    raise SystemExit(1)
verification_lines = re.findall(
    r"^- Verification result: ([^\r\n]*)$", entry, flags=re.MULTILINE
)
if (
    len(verification_lines) != 1
    or not verification_lines[0].strip()
    or len(verification_lines[0].splitlines()) != 1
):
    raise SystemExit(1)
PY
}

verify_atomic_upgrade_ref() {
  local ref="$1"
  local expected_baseline="$2"
  local expected_target_commit="$3"
  local expected_version="$4"
  local parents=()
  read -r -a parents <<<"$(git show -s --format=%P "$ref" 2>/dev/null)"
  (( ${#parents[@]} == 2 )) || return 1
  [[ "${parents[0]}" == "$expected_baseline" && \
     "${parents[1]}" == "$expected_target_commit" ]] || return 1

  local project_snapshot history_snapshot
  project_snapshot="$(mktemp)"
  history_snapshot="$(mktemp)"
  if ! git show "${ref}:PROJECT.md" >"$project_snapshot" 2>/dev/null || \
     ! git show "${ref}:BASE_UPDATES.md" >"$history_snapshot" 2>/dev/null; then
    rm -f "$project_snapshot" "$history_snapshot"
    return 1
  fi
  local expected_synced_at
  if ! expected_synced_at="$(ROOT_VALUE="$ROOT" PROJECT_SNAPSHOT="$project_snapshot" \
       EXPECTED_VERSION="$expected_version" EXPECTED_COMMIT="$expected_target_commit" \
       python3 - <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["ROOT_VALUE"]) / "scripts"))
from lib.base_release import parse_project

values = parse_project(Path(os.environ["PROJECT_SNAPSHOT"]), strict=True)
version = os.environ["EXPECTED_VERSION"]
expected = {
    "BASE_UPSTREAM_VERSION": version,
    "BASE_UPSTREAM_TAG": f"base/v{version}",
    "BASE_UPSTREAM_COMMIT": os.environ["EXPECTED_COMMIT"],
    "BASE_UPDATE_LEDGER": "BASE_UPDATES.md",
}
if any(values.get(key) != value for key, value in expected.items()):
    raise SystemExit(1)
synced_at = values.get("BASE_LAST_SYNCED_AT", "")
if "\n" in synced_at or "\r" in synced_at:
    raise SystemExit(1)
print(synced_at)
PY
  )"; then
    rm -f "$project_snapshot" "$history_snapshot"
    return 1
  fi
  validate_latest_history \
    "$expected_version" "$expected_target_commit" "$expected_synced_at" "$history_snapshot"
  local result=$?
  rm -f "$project_snapshot" "$history_snapshot"
  return "$result"
}

conflicts_are_representable() {
  ROOT_VALUE="$ROOT" python3 - "$1" <<'PY'
import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator

try:
    items = [item.decode("utf-8") for item in Path(sys.argv[1]).read_bytes().split(b"\0") if item]
except UnicodeDecodeError:
    raise SystemExit(1)
if not items:
    raise SystemExit(1)
schema_path = Path(__import__("os").environ["ROOT_VALUE"]) / "scripts/schemas/base-upgrade-result.schema.json"
schema = json.loads(schema_path.read_text(encoding="utf-8"))["properties"]["conflict_files"]
errors = list(Draft7Validator(schema).iter_errors(items))
if errors:
    raise SystemExit(1)
PY
}

[[ "$project_id" =~ $valid_id ]] || fail blocked input_validation "project_id is invalid"
[[ "$campaign_id" =~ $valid_id ]] || fail blocked input_validation "campaign_id is invalid"
(( ${#target_version} <= 64 )) && [[ "$target_version" =~ $valid_semver ]] || \
  fail blocked input_validation "target_version is not canonical SemVer"
[[ "$allow_major" == "true" || "$allow_major" == "false" ]] || \
  fail blocked input_validation "allow_major must be true or false"
[[ "$fixture_mode" == "0" || "$fixture_mode" == "1" ]] || \
  fail blocked input_validation "BASE_UPGRADE_FIXTURE_MODE must be 0 or 1"
if [[ "$fixture_mode" == "1" && \
      ( "${GITHUB_ACTIONS:-false}" != "false" && -n "${GITHUB_ACTIONS:-}" || \
        "${CI:-false}" != "false" && "${CI:-0}" != "0" && -n "${CI:-}" ) ]]; then
  fail blocked input_validation "fixture mode is forbidden in GitHub Actions and CI"
fi
[[ -f "$ROOT/PROJECT.md" ]] || fail blocked project_ledger "PROJECT.md is missing"
[[ -f "$ROOT/BASE_UPDATES.md" ]] || fail blocked project_ledger "BASE_UPDATES.md is missing"
[[ -x "$ROOT/scripts/sync-base-release.sh" ]] || \
  fail blocked receiver_contract "scripts/sync-base-release.sh is missing or not executable"

# Strictly read the committed identity. Duplicate keys and malformed values fail closed.
if ! ledger_json="$(ROOT_VALUE="$ROOT" python3 - <<'PY'
import json
import sys
from pathlib import Path

root = Path(__import__("os").environ["ROOT_VALUE"])
sys.path.insert(0, str(root / "scripts"))
from lib.base_release import parse_project

values = parse_project(root / "PROJECT.md", strict=True)
keys = (
    "PROJECT_SLUG", "BASE_UPSTREAM_REPOSITORY", "BASE_UPSTREAM_VERSION",
    "BASE_UPSTREAM_TAG", "BASE_UPSTREAM_COMMIT", "BASE_LAST_SYNCED_AT",
    "BASE_UPDATE_LEDGER",
)
print(json.dumps({key: values.get(key, "") for key in keys}))
PY
)"; then
  fail blocked project_ledger "PROJECT.md could not be parsed strictly"
fi

mapfile -d '' -t ledger_values < <(LEDGER_JSON="$ledger_json" python3 - <<'PY'
import json
import os
import sys

values = json.loads(os.environ["LEDGER_JSON"])
for key in (
    "PROJECT_SLUG", "BASE_UPSTREAM_REPOSITORY", "BASE_UPSTREAM_VERSION",
    "BASE_UPSTREAM_TAG", "BASE_UPSTREAM_COMMIT", "BASE_LAST_SYNCED_AT",
    "BASE_UPDATE_LEDGER",
):
    sys.stdout.write(values[key] + "\0")
PY
)
(( ${#ledger_values[@]} == 7 )) || fail blocked project_ledger "PROJECT.md identity is incomplete"
ledger_project_id="${ledger_values[0]}"
base_repository="${ledger_values[1]}"
source_version="${ledger_values[2]}"
source_tag="${ledger_values[3]}"
source_commit="${ledger_values[4]}"
source_synced_at="${ledger_values[5]}"
history_path="${ledger_values[6]}"

[[ "$ledger_project_id" == "$project_id" ]] || \
  fail blocked project_identity "project_id does not match PROJECT_SLUG"
normalized_base_repository="$(normalize_repository_slug "$base_repository" || true)"
[[ "$normalized_base_repository" == "$base_repository" ]] || \
  fail blocked upstream_identity "BASE_UPSTREAM_REPOSITORY is not canonical OWNER/REPO"
if (( ${#source_version} > 64 )) || [[ ! "$source_version" =~ $valid_semver ]]; then
  source_version=""
  fail blocked project_ledger "BASE_UPSTREAM_VERSION is not canonical SemVer"
fi
[[ "$source_tag" == "base/v${source_version}" ]] || \
  fail blocked project_ledger "BASE_UPSTREAM_TAG does not match BASE_UPSTREAM_VERSION"
[[ "$source_commit" =~ ^[0-9a-f]{40}$ ]] || \
  fail blocked project_ledger "BASE_UPSTREAM_COMMIT is not an immutable commit"
[[ "$history_path" == "BASE_UPDATES.md" ]] || \
  fail blocked project_ledger "BASE_UPDATE_LEDGER must be BASE_UPDATES.md"
validate_latest_history \
  "$source_version" "$source_commit" "$source_synced_at" "$ROOT/BASE_UPDATES.md" || \
  fail blocked project_ledger "latest BASE_UPDATES.md entry does not match PROJECT.md"

github_repository="${GITHUB_REPOSITORY:-}"
default_branch="${BASE_UPGRADE_DEFAULT_BRANCH:-}"
normalized_github_repository="$(normalize_repository_slug "$github_repository" || true)"
[[ "$normalized_github_repository" == "$github_repository" ]] || \
  fail blocked repository_identity "GITHUB_REPOSITORY is not canonical OWNER/REPO"
[[ "$default_branch" =~ $valid_ref && "$default_branch" != *".."* && \
   "$default_branch" != */.* && "$default_branch" != .* && \
   "$default_branch" != *//* && "$default_branch" != *@\{* && \
   "$default_branch" != *.lock ]] || \
  fail blocked repository_identity "repository default branch is not a safe Git ref"

cd "$ROOT" || fail blocked checkout "repository checkout is unavailable"
[[ -z "$(git status --porcelain --untracked-files=all -- ':!.base-upgrade')" ]] || \
  fail blocked checkout "default-branch checkout is not clean"

if [[ "$fixture_mode" == "0" ]]; then
  [[ "${GITHUB_SERVER_URL:-https://github.com}" == "https://github.com" ]] || \
    fail blocked provider "receiver supports GitHub.com only"
  command -v gh >/dev/null 2>&1 || fail blocked provider "GitHub CLI is unavailable"
  [[ -n "${GH_TOKEN:-}" ]] || fail blocked provider "GitHub token is unavailable"
  origin_url="$(git remote get-url origin 2>/dev/null || true)"
  origin_repository="$(python3 "$ROOT/scripts/base-update-ledger.py" \
    normalize-repository --remote-url "$origin_url" 2>/dev/null || true)"
  [[ "${origin_repository,,}" == "${github_repository,,}" ]] || \
    fail blocked repository_identity "origin remote does not match GITHUB_REPOSITORY"
  git remote remove upstream >/dev/null 2>&1 || true
  git remote add upstream "https://github.com/${base_repository}.git" || \
    fail blocked upstream_identity "could not restore public Base upstream"
else
  git remote get-url upstream >/dev/null 2>&1 || \
    fail blocked upstream_identity "fixture repository has no upstream remote"
fi

git fetch origin "$default_branch" --no-tags >/dev/null 2>&1 || \
  fail blocked checkout "could not fetch the repository default branch"
baseline_commit="$(git rev-parse "refs/remotes/origin/${default_branch}^{commit}" 2>/dev/null || true)"
[[ -n "$baseline_commit" && "$(git rev-parse 'HEAD^{commit}')" == "$baseline_commit" ]] || \
  fail blocked checkout "checkout is not the current repository default branch"

if [[ "$fixture_mode" == "0" ]]; then
  GIT_TERMINAL_PROMPT=0 git -c http.https://github.com/.extraheader= \
    fetch upstream --tags --prune >/dev/null 2>&1 || \
    fail blocked upstream_fetch "Base upstream is not publicly readable from GitHub.com"
else
  git fetch upstream --tags --prune >/dev/null 2>&1 || \
    fail blocked upstream_fetch "could not fetch the fixture Base upstream"
fi
target_commit="$(git rev-parse "refs/tags/base/v${target_version}^{commit}" 2>/dev/null || true)"
[[ -n "$target_commit" ]] || fail blocked target_tag "target Base tag does not exist"
actual_source_commit="$(git rev-parse "refs/tags/base/v${source_version}^{commit}" 2>/dev/null || true)"
[[ "$actual_source_commit" == "$source_commit" ]] || \
  fail blocked project_ledger "BASE_UPSTREAM_COMMIT does not match the recorded Base tag"
git merge-base --is-ancestor "$source_commit" HEAD >/dev/null 2>&1 || \
  fail blocked project_ledger "recorded Base commit is not an ancestor of the default branch"
git merge-base --is-ancestor "$source_commit" "$target_commit" >/dev/null 2>&1 || \
  fail blocked target_tag "target Base tag is not a descendant of the recorded Base tag"

if ! version_relation="$(ROOT_VALUE="$ROOT" SOURCE_VERSION="$source_version" \
    TARGET_VERSION="$target_version" python3 - <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["ROOT_VALUE"]) / "scripts"))
from lib.base_release import parse_core_semver

source = parse_core_semver(os.environ["SOURCE_VERSION"])
target = parse_core_semver(os.environ["TARGET_VERSION"])
relation = "equal" if source == target else "upgrade" if source < target else "downgrade"
print(relation, "cross-major" if source[0] != target[0] else "same-major")
PY
)"; then
  fail blocked version_gate "Base versions could not be compared"
fi
read -r relation major_relation <<<"$version_relation"
if [[ "$relation" == "downgrade" ]]; then
  fail blocked version_gate "Base downgrade is not permitted"
fi
if [[ "$relation" == "equal" ]]; then
  status="up_to_date"
  failed_stage=""
  verification_summary="PROJECT.md already records target Base v${target_version}"
  exit_code=0
  finish
fi
if [[ "$major_relation" == "cross-major" && "$allow_major" != "true" ]]; then
  fail blocked version_gate "cross-major Base update requires allow_major=true"
fi

# Existing remote state is never overwritten. A branch is reusable only when its
# target-tag ancestry and committed downstream ledger prove the exact target.
remote_branch=0
if git ls-remote --exit-code --heads origin "refs/heads/${branch}" >/dev/null 2>&1; then
  remote_branch=1
  git fetch origin "refs/heads/${branch}:refs/remotes/origin/${branch}" >/dev/null 2>&1 || \
    fail blocked branch_ownership "could not inspect the existing upgrade branch"
  verify_atomic_upgrade_ref "refs/remotes/origin/${branch}" "$baseline_commit" \
    "$target_commit" "$target_version" || \
    fail blocked branch_ownership "existing upgrade branch ownership could not be proven"
fi

if [[ "$fixture_mode" == "0" ]]; then
  pr_rows="$(gh pr list --repo "$github_repository" --state open --base "$default_branch" \
    --head "$branch" --json number,url,isDraft,headRefName,baseRefName 2>/dev/null)" || \
    fail blocked provider "could not inspect existing pull requests"
  if ! pr_count="$(PR_ROWS="$pr_rows" EXPECTED_REPOSITORY="$github_repository" \
      EXPECTED_HEAD="$branch" EXPECTED_BASE="$default_branch" python3 - <<'PY'
import json
import os
import re

rows = json.loads(os.environ["PR_ROWS"])
if not isinstance(rows, list):
    raise SystemExit(1)
for row in rows:
    if not isinstance(row, dict) or set(row) != {"number", "url", "isDraft", "headRefName", "baseRefName"}:
        raise SystemExit(1)
    if not isinstance(row["number"], int) or isinstance(row["number"], bool) or row["number"] < 1:
        raise SystemExit(1)
    if not isinstance(row["isDraft"], bool):
        raise SystemExit(1)
    if row["headRefName"] != os.environ["EXPECTED_HEAD"] or row["baseRefName"] != os.environ["EXPECTED_BASE"]:
        raise SystemExit(1)
    expected_url = rf"https://github\.com/{re.escape(os.environ['EXPECTED_REPOSITORY'])}/pull/{row['number']}"
    if not isinstance(row["url"], str) or re.fullmatch(expected_url, row["url"], flags=re.IGNORECASE) is None:
        raise SystemExit(1)
print(len(rows))
PY
)"; then
    fail blocked provider "Provider returned malformed pull request data"
  fi
  (( pr_count <= 1 )) || fail blocked branch_ownership "multiple open pull requests claim the upgrade branch"
else
  pr_count="$remote_branch"
fi

if (( remote_branch == 0 )); then
  git switch -c "$branch" >/dev/null 2>&1 || fail blocked branch_create "could not create the upgrade branch"
  sync_log="$(mktemp)"
  if ! "$ROOT/scripts/sync-base-release.sh" "$target_version" --install-deps >"$sync_log" 2>&1; then
    conflict_capture_file="$(mktemp)"
    conflict_file="$conflict_capture_file"
    git diff --name-only -z --diff-filter=U >"$conflict_capture_file"
    if [[ -s "$conflict_capture_file" ]]; then
      if conflicts_are_representable "$conflict_capture_file"; then
        status="conflict"
        failed_stage="merge"
        verification_summary="Base merge reported conflicts; no branch or pull request was published"
      else
        status="blocked"
        failed_stage="conflict_capture"
        verification_summary="conflict paths cannot be represented by the result contract; no branch or pull request was published"
        conflict_file=""
      fi
    else
      status="verification_failed"
      failed_stage="verification"
      verification_summary="Base synchronization or verification failed; no branch or pull request was published"
    fi
    # Persist the evidence while MERGE_HEAD and the conflict index still exist;
    # only then clean the ephemeral checkout. Nothing has been pushed.
    emit_rc=0
    emit_result || emit_rc=$?
    merge_head="$(git rev-parse --git-path MERGE_HEAD)"
    if [[ -f "$merge_head" ]]; then
      git merge --abort >/dev/null 2>&1 || true
    else
      git reset --hard "$baseline_commit" >/dev/null 2>&1 || true
    fi
    rm -f "$sync_log" "$conflict_capture_file"
    (( emit_rc == 0 )) || {
      printf 'receiver failed to write its result artifact\n' >&2
      exit 1
    }
    exit 1
  fi
  rm -f "$sync_log"
  verify_atomic_upgrade_ref HEAD "$baseline_commit" "$target_commit" "$target_version" || \
    fail verification_failed verification "successful sync did not produce the expected atomic ledger merge"
  if ! git push origin "HEAD:refs/heads/${branch}" >/dev/null 2>&1; then
    fail blocked push "upgrade commit passed verification but the non-force push was rejected"
  fi
  new_branch_pushed=1
fi

pr_body="$(mktemp)"
update_nodes="$(mktemp)"
if ! render_update_nodes >"$update_nodes"; then
  rm -f "$pr_body" "$update_nodes"
  fail blocked manifest_plan "could not render the trusted cross-version update-node plan"
fi
retry_value="$(retry_command)"
rollback_value="$(prospective_pr_rollback_command)"
{
  printf '## Base upgrade\n\n'
  printf -- '- Project: `%s`\n' "$project_id"
  printf -- '- Campaign: `%s`\n' "$campaign_id"
  printf -- '- Current: `base/v%s`\n' "$source_version"
  printf -- '- Target: `base/v%s`\n' "$target_version"
  printf -- '- Branch: `%s`\n\n' "$branch"
  printf '### Cross-version update nodes\n\n'
  cat "$update_nodes"
  printf '\n### Verification evidence\n\n'
  printf -- '- [x] Target tag resolution and source-to-target ancestry — **PASS**\n'
  printf -- '- [x] Trusted release Manifest range and update-node selection — **PASS**\n'
  if (( new_branch_pushed == 1 )); then
    printf -- '- [x] `sync-base-release.sh %s --install-deps` full validation — **PASS**\n' "$target_version"
    printf -- '- [x] Non-force publication of the upgrade branch — **PASS**\n'
  else
    printf -- '- [x] Existing upgrade branch ownership and immutable parent proof — **PASS**\n'
  fi
  printf -- '- [x] Atomic two-parent merge and exact PROJECT/BASE_UPDATES ledger state — **PASS**\n\n'
  printf '### Operations\n\n'
  printf -- '- Retry: `%s`\n' "$retry_value"
  printf -- '- Rollback before merge: `%s`\n' "$rollback_value"
} >"$pr_body"
rm -f "$update_nodes"

if [[ "$fixture_mode" == "1" ]]; then
  pr_url="https://github.com/${github_repository}/pull/1"
  if (( pr_count == 0 )); then
    new_pr_created=1
  fi
else
  if (( pr_count == 1 )); then
    if ! pr_number="$(PR_ROWS="$pr_rows" python3 - <<'PY'
import json, os
row = json.loads(os.environ["PR_ROWS"])[0]
print(row["number"])
PY
)"; then
      fail blocked provider "Provider pull request number could not be parsed"
    fi
    gh pr edit "$pr_number" --repo "$github_repository" \
      --title "chore(base): update to v${target_version}" --body-file "$pr_body" >/dev/null 2>&1 || \
      fail blocked provider "could not update the existing upgrade pull request"
    pr_url="$(gh pr view "$pr_number" --repo "$github_repository" --json url --jq .url 2>/dev/null)" || \
      fail blocked provider "could not read the updated upgrade pull request"
  else
    if ! pr_url="$(gh pr create --repo "$github_repository" --draft --base "$default_branch" \
      --head "$branch" --title "chore(base): update to v${target_version}" \
      --body-file "$pr_body" 2>/dev/null)"; then
      fail blocked provider "could not create the Draft pull request"
    fi
    new_pr_created=1
  fi
fi
rm -f "$pr_body"

[[ "$pr_url" =~ ^https://github\.com/[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*$ ]] || \
  fail blocked provider "Provider returned an invalid pull request URL"
status="pr_opened"
failed_stage=""
if (( new_branch_pushed == 1 )); then
  branch_evidence="created by this run"
else
  branch_evidence="reused after ownership proof"
fi
if (( new_pr_created == 1 )); then
  pr_evidence="created by this run"
else
  pr_evidence="reused without changing Draft or Ready state"
fi
verification_summary="atomic Base sync PASS; target tag and project ledger verified; branch ${branch_evidence}; pull request ${pr_evidence}"
exit_code=0
finish
