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
5. 新增 `releases/base-vX.Y.Z.json`，为每个更新节点记录稳定 ID、类型、范围、
   文件、兼容性、迁移、下游动作、冲突点、验证与回滚。
6. 运行 `python3 scripts/check-base-release.py`、后端测试、前端 lint/build 和 `git diff --check`。
7. 创建发布提交：`release(base): vX.Y.Z`。
8. 创建不可变 tag：`base/vX.Y.Z`，再推送提交和 tag。

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
git merge --no-ff --no-commit refs/tags/base/vX.Y.Z
```

合并前脚本从目标 tag 聚合 CURRENT→TARGET 之间的所有 Manifest，打印“更新哪些”；
随后执行 route/pytest/lint/build/diff 验证；通过后更新 `PROJECT.md`、追加含 PASS
证据的 `BASE_UPDATES.md`，把代码合并和版本账本放进同一个 merge commit。
发生冲突或验证失败时在账本变更前停止，可修复或 `git merge --abort`。
冲突或验证/commit hook 问题修复并 `git add` 后使用
`./scripts/sync-base-release.sh X.Y.Z --continue`；脚本会核对 MERGE_HEAD、从 merge
前的 HEAD 恢复源版本依据、避免重复历史、重新验证并完成同一个原子 merge commit。

没有脚本时使用上面的命令手动同步。同步完成后：

```bash
python3 scripts/check-base-release.py
cd serve && pytest
cd ../admin && npm run lint && npm run build
cd .. && git diff --check
git push project <PROJECT_BRANCH>
```

### 数据库隔离前置检查

Base 仓库本机保留的 `base_platform_app@base_platform` 不随代码同步给下游使用。
每个下游必须在自己的 secret 配置中设置项目专属 `DATABASE_NAME`、
`DATABASE_USER`、`DATABASE_PASSWORD`，并确认其值均不是 `base_platform`、
`base_platform_app`。下游严禁运行 `scripts/provision-base-database.sh`，因为该脚本
只服务于 Base 仓库自身的固定数据库身份。

新 Fork/Clone 完成 `upstream` remote 后，使用：

```bash
scripts/bootstrap-project.sh PROJECT_SLUG "Project Name"
```

脚本自动完成项目专属 database/role、环境文件、依赖、迁移、测试/build 和
`PROJECT.md` 当前账本和 `BASE_UPDATES.md` 追加历史。已存在的下游项目继续使用
自己的数据库，不重新 bootstrap。

## 5. 迁移与冲突

- 先读目标版本的 `CHANGELOG.md`，再执行任何数据库迁移。
- Base 迁移和下游产品迁移分开执行；禁止用产品迁移替换 Base 迁移。
- 冲突时保留下游产品代码，重新应用 Base 的通用变更；不得用整目录覆盖方式解决冲突。
- 冲突热点必须记录在下游项目自己的同步记录中，不能反向写入 Base。
- 同步失败时保留同步前 tag/分支，修复冲突后重新运行完整验证。

## 6. 下游版本账本

每个下游项目应在自己的仓库记录：

```text
BASE_UPSTREAM_VERSION=3.2.0
BASE_UPSTREAM_TAG=base/v3.2.0
BASE_UPSTREAM_COMMIT=<immutable commit>
BASE_LAST_SYNCED_AT=YYYY-MM-DDTHH:MM:SSZ
BASE_UPDATE_LEDGER=BASE_UPDATES.md
BASE_NEXT_UPDATE_COMMAND=./scripts/sync-base-release.sh <TARGET_VERSION>
```

`PROJECT.md` 保存当前状态；每次采用/同步的全部节点、文件差异、迁移、动作、验证
和回滚追加到 `BASE_UPDATES.md`。下游不得修改 Base 的 `CHANGELOG.md`；产品变更写入
下游自己的产品账本。完整格式和 v3.2.0 首次接入见
`serve/docs/base-update-ledger.md`。
