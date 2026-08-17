#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" || ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
  echo "usage: $0 X.Y.Z" >&2
  exit 2
fi

TAG="base/v${VERSION}"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "工作树必须干净；先提交或暂存下游项目自己的变更。" >&2
  exit 1
fi

if ! git remote get-url upstream >/dev/null 2>&1; then
  echo "缺少 upstream remote；先执行: git remote add upstream <BASE_REPOSITORY_URL>" >&2
  exit 1
fi

git fetch upstream --tags --prune
git rev-parse --verify "refs/tags/${TAG}" >/dev/null
git merge --no-ff "refs/tags/${TAG}" -m "chore(sync): update Base to v${VERSION}"

echo "Base 已同步到 ${TAG}。请继续执行 UPSTREAM.md §4 的验证命令。"
