#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-}"
if [[ -z "$VERSION" || ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: $0 X.Y.Z [--continue] [--install-deps]" >&2
  exit 2
fi

CONTINUE=0
INSTALL_DEPS=0
for option in "${@:2}"; do
  case "$option" in
    --continue)
      [[ "$CONTINUE" == 0 ]] || {
        echo "duplicate option: --continue" >&2
        exit 2
      }
      CONTINUE=1
      ;;
    --install-deps)
      [[ "$INSTALL_DEPS" == 0 ]] || {
        echo "duplicate option: --install-deps" >&2
        exit 2
      }
      INSTALL_DEPS=1
      ;;
    *)
      echo "usage: $0 X.Y.Z [--continue] [--install-deps]" >&2
      exit 2
      ;;
  esac
done

TAG="base/v${VERSION}"
PROJECT_LEDGER="$ROOT/PROJECT.md"
UPDATE_LEDGER="$ROOT/BASE_UPDATES.md"
LEDGER_TOOL="$ROOT/scripts/base-update-ledger.py"

if [[ "$CONTINUE" == 0 && -n "$(git -C "$ROOT" status --porcelain --untracked-files=all)" ]]; then
  echo "工作树必须完全干净；先提交下游项目自己的变更。" >&2
  exit 1
fi
if ! git -C "$ROOT" remote get-url upstream >/dev/null 2>&1; then
  echo "缺少 upstream remote；先执行: git remote add upstream <BASE_REPOSITORY_URL>" >&2
  exit 1
fi
[[ -f "$PROJECT_LEDGER" ]] || {
  echo "缺少 PROJECT.md；先记录当前 BASE_UPSTREAM_VERSION。" >&2
  exit 1
}

# HEAD remains the pre-sync downstream commit throughout an unfinished merge. Read
# the current Base version from there so --continue also recovers after a failed
# validation or commit hook, even if a previous attempt already rewrote PROJECT.md.
CURRENT="$(python3 "$LEDGER_TOOL" current --project PROJECT.md --ref HEAD)"
if [[ "$CONTINUE" == 0 ]]; then
  git -C "$ROOT" fetch upstream --tags --prune
fi
TARGET_COMMIT="$(git -C "$ROOT" rev-parse "refs/tags/${TAG}^{commit}")"
CURRENT_TAG="refs/tags/base/v${CURRENT}"
git -C "$ROOT" rev-parse "${CURRENT_TAG}^{commit}" >/dev/null 2>&1 || {
  echo "PROJECT.md 记录的当前 Base tag base/v${CURRENT} 不存在。" >&2
  exit 1
}
git -C "$ROOT" merge-base --is-ancestor "refs/tags/base/v${CURRENT}" HEAD || {
  echo "PROJECT.md 记录的 base/v${CURRENT} 不是当前分支祖先，停止同步。" >&2
  exit 1
}
git -C "$ROOT" merge-base --is-ancestor "$CURRENT_TAG" "refs/tags/${TAG}" || {
  echo "目标 ${TAG} 不是当前 Base tag base/v${CURRENT} 的后代，停止同步。" >&2
  exit 1
}

echo "===== Base 更新计划：v${CURRENT} -> v${VERSION} ====="
python3 "$LEDGER_TOOL" plan \
  --from "$CURRENT" --to "$VERSION" --ref "refs/tags/${TAG}"

# Merge and downstream ledger changes are committed atomically. On conflicts, resolve
# and rerun with --continue, or run `git merge --abort`.
if [[ "$CONTINUE" == 1 ]]; then
  MERGE_HEAD_PATH="$(cd "$ROOT" && git rev-parse --git-path MERGE_HEAD)"
  [[ -f "$MERGE_HEAD_PATH" ]] || {
    echo "当前没有待继续的 merge。" >&2
    exit 1
  }
  MERGE_COMMIT="$(git -C "$ROOT" rev-parse 'MERGE_HEAD^{commit}')"
  [[ "$MERGE_COMMIT" == "$TARGET_COMMIT" ]] || {
    echo "MERGE_HEAD 不是 ${TAG}，停止记录。" >&2
    exit 1
  }
  [[ -z "$(git -C "$ROOT" diff --name-only --diff-filter=U)" ]] || {
    echo "仍有未解决冲突；解决并 git add 后再继续。" >&2
    exit 1
  }
else
  git -C "$ROOT" merge --no-ff --no-commit "refs/tags/${TAG}"
fi

# A fresh receiver checkout has no project dependencies. Install only after the
# target tree has been merged, so the target release owns every lock/requirement.
if [[ "$INSTALL_DEPS" == 1 ]]; then
  if [[ ! -x "$ROOT/serve/.venv/bin/python" ]]; then
    python3 -m venv "$ROOT/serve/.venv"
  fi
  "$ROOT/serve/.venv/bin/python" -m pip install \
    -r "$ROOT/scripts/requirements.txt" \
    -r "$ROOT/serve/requirements-dev.txt"
  (cd "$ROOT/admin" && npm ci)
fi

# Validate the merged Base before recording or committing the new version.
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
python3 "$ROOT/scripts/check-database-boundary.py"
git -C "$ROOT" diff --check
git -C "$ROOT" diff --cached --check

# A previous attempt can reach ledger generation and then fail in a commit hook.
# Regenerate both derived ledgers from the pre-merge HEAD rather than duplicating
# the history entry or treating the uncommitted target version as the source.
WORKTREE_CURRENT="$(python3 "$LEDGER_TOOL" current --project "$PROJECT_LEDGER")"
if [[ "$WORKTREE_CURRENT" == "$VERSION" ]]; then
  git -C "$ROOT" restore --source=HEAD --staged --worktree -- PROJECT.md
  if git -C "$ROOT" cat-file -e HEAD:BASE_UPDATES.md 2>/dev/null; then
    git -C "$ROOT" restore --source=HEAD --staged --worktree -- BASE_UPDATES.md
  else
    git -C "$ROOT" rm -f --cached --ignore-unmatch BASE_UPDATES.md >/dev/null
    rm -f "$UPDATE_LEDGER"
  fi
elif [[ "$WORKTREE_CURRENT" != "$CURRENT" ]]; then
  echo "PROJECT.md 版本既不是源版本 v${CURRENT}，也不是目标版本 v${VERSION}，停止记录。" >&2
  exit 1
fi

python3 "$LEDGER_TOOL" record \
  --project "$PROJECT_LEDGER" \
  --history "$UPDATE_LEDGER" \
  --from "$CURRENT" \
  --to "$VERSION" \
  --ref "refs/tags/${TAG}" \
  --verification-status "PASS: route check, backend pytest, frontend lint/build, git diff --check (worktree+index)"
git -C "$ROOT" add PROJECT.md BASE_UPDATES.md
git -C "$ROOT" commit -m "chore(sync): update Base v${CURRENT} to v${VERSION}"

echo "Base 已同步到 ${TAG}；PROJECT.md 与 BASE_UPDATES.md 已进入同一 merge commit。"
echo "继续执行目标版本 Manifest 的 Verification 命令。"
