# Base Upstream 同步合同

本文件定义所有以 Base Platform 为基座的 Fork/Clone 项目如何及时、可重复、低冲突地同步更新。

## 1. 固定关系

下游项目必须保留两个 remote：

```bash
git remote rename origin project
git remote add upstream <BASE_REPOSITORY_URL>
git remote -v
```

- `upstream`：只指向本 Base 仓库。
- `project`：指向下游项目自己的仓库。
- 下游业务代码只提交到 `project`，不直接提交到 `upstream`。

## 2. Base 发布合同

每次 Base 更新必须按以下顺序完成：

1. 只修改通用、产品无关的代码和文档。
2. 按 SemVer 更新 `VERSION`。
3. 同步 `admin/package.json` 和 `admin/package-lock.json` 的版本号。
4. 在 `CHANGELOG.md` 新增同版本条目，写清变更、兼容性、迁移、同步和回滚。
5. 运行 `python3 scripts/check-base-release.py`、后端测试、前端 lint/build 和 `git diff --check`。
6. 创建发布提交：`release(base): vX.Y.Z`。
7. 创建不可变 tag：`base/vX.Y.Z`，再推送提交和 tag。

禁止复用旧版本号、移动已发布 tag、覆盖已发布账本或只提交代码不写升级说明。

## 3. 版本号规则

- **MAJOR**：不兼容的 API、数据模型、目录或升级流程变化。
- **MINOR**：向后兼容的通用能力新增或扩展。
- **PATCH**：向后兼容的 bug、安全、性能、文档或测试修复。

任何需要下游手工改业务代码的变化必须升级 MAJOR，或在账本中提供明确的兼容层和分步迁移。

## 4. 下游同步步骤

同步前必须提交或暂存自己的项目变更，保持工作树干净：

```bash
git status --short
git fetch upstream --tags --prune
./scripts/sync-base-release.sh X.Y.Z
```

脚本会检查工作树、获取 tag，并执行：

```bash
git merge --no-ff refs/tags/base/vX.Y.Z
```

没有脚本时使用上面的命令手动同步。同步完成后：

```bash
python3 scripts/check-base-release.py
cd serve && pytest
cd ../admin && npm run lint && npm run build
cd .. && git diff --check
git push project <PROJECT_BRANCH>
```

## 5. 迁移与冲突

- 先读目标版本的 `CHANGELOG.md`，再执行任何数据库迁移。
- Base 迁移和下游产品迁移分开执行；禁止用产品迁移替换 Base 迁移。
- 冲突时保留下游产品代码，重新应用 Base 的通用变更；不得用整目录覆盖方式解决冲突。
- 冲突热点必须记录在下游项目自己的同步记录中，不能反向写入 Base。
- 同步失败时保留同步前 tag/分支，修复冲突后重新运行完整验证。

## 6. 下游版本账本

每个下游项目应在自己的仓库记录：

```text
BASE_UPSTREAM_VERSION=1.0.0
BASE_UPSTREAM_TAG=base/v1.0.0
BASE_SYNCED_AT=YYYY-MM-DDTHH:MM:SSZ
```

完成一次同步后更新这三项。下游项目不得修改 Base 的 `CHANGELOG.md`；产品变更写入下游自己的账本。
