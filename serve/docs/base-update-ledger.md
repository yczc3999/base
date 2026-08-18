# Base 发布节点与下游更新账本合同

## 三个权威层

### 1. Base 发布节点：`releases/base-vX.Y.Z.json`

每个 Base tag 必须有同版本 Manifest，并逐项记录：

- `nodes`：稳定 node id、added/changed/fixed/removed/security 类型、作用域、说明、涉及文件；
- compatibility 与数据库 migrations；
- 下游必须执行的 actions；
- conflict hotspots、verification 与 rollback。

`scripts/check-base-release.py` 强制检查 Manifest schema、当前最高版本、CHANGELOG
日期和版本一致性。缺 Manifest、空节点、无文件范围或账本漂移时禁止发布。

### 2. 下游当前状态：`PROJECT.md`

下游仓库必须提交并维护：

```text
BASE_UPSTREAM_VERSION=X.Y.Z
BASE_UPSTREAM_TAG=base/vX.Y.Z
BASE_UPSTREAM_COMMIT=<immutable commit>
BASE_LAST_SYNCED_AT=<UTC timestamp>
BASE_UPDATE_LEDGER=BASE_UPDATES.md
BASE_NEXT_UPDATE_PLAN=python3 scripts/base-update-ledger.py plan ...
BASE_NEXT_UPDATE_COMMAND=./scripts/sync-base-release.sh <TARGET_VERSION>
```

它回答“当前用了哪个版本”和“下次怎么更新”。数据库密码及其他 secret 严禁进入该文件。

### 3. 下游追加历史：`BASE_UPDATES.md`

每次采用或更新自动追加：跨过的每个 Base 版本、全部更新节点、相关文件、兼容性、
迁移、下游动作、冲突点、验证、回滚、准确 Base 文件 diff、同步时间和 Base commit。
历史只追加，不覆盖旧记录。

## 更新前查看“更新哪些”

```bash
git fetch upstream --tags --prune
python3 scripts/base-update-ledger.py plan \
  --from CURRENT_VERSION \
  --to TARGET_VERSION \
  --ref refs/tags/base/vTARGET_VERSION
```

计划从目标 tag 读取机器 Manifest，并使用两个 Base tag 的 Git diff 列出精确
新增/修改/删除文件，不把下游产品改动混入 Base 文件清单。

## 标准更新

```bash
./scripts/sync-base-release.sh TARGET_VERSION
```

脚本顺序固定：

1. 要求工作树（含 untracked）完全干净；
2. 从 `PROJECT.md` 读取 CURRENT_VERSION；
3. fetch 并验证当前 Base tag 是下游分支祖先；
4. 在合并前打印完整跨版本计划；
5. `git merge --no-ff --no-commit` 目标 tag；
6. 运行 route check、后端 pytest、前端 lint/build 和 `git diff --check`；
7. 验证通过后更新 `PROJECT.md`，追加含 PASS 证据的 `BASE_UPDATES.md`；
8. 把 Base 合并和两个下游账本提交为同一个 merge commit。

有冲突、验证失败或 commit hook 失败时，修复问题并 `git add` 后执行：

```bash
./scripts/sync-base-release.sh TARGET_VERSION --continue
```

`--continue` 会校验 MERGE_HEAD 正是目标 Base tag、从 merge 前的 HEAD 恢复源版本
依据、确认不存在未解决冲突，然后重新执行完整验证、无重复账本记录的 merge commit。
也可执行 `git merge --abort` 整体退出。
不允许代码已升级而版本账本仍停在旧版本。

## v3.2.0 首次接入说明

- v3.2.0 及之后创建的新项目由 bootstrap 自动生成两个下游账本。
- 已有 `PROJECT.md` 的 v3.1.0 项目首次合并 v3.2.0 后执行一次：

```bash
python3 scripts/base-update-ledger.py record \
  --from 3.1.0 --to 3.2.0 --ref refs/tags/base/v3.2.0
git add PROJECT.md BASE_UPDATES.md
git commit --amend --no-edit
```

- 更早且没有 `PROJECT.md` 的项目，在确认自己实际采用的 Base tag 后执行
  以下命令建立初始账本；不得猜测版本：

```bash
python3 scripts/base-update-ledger.py initialize \
  --project-slug PROJECT_SLUG --project-name "Project Name" \
  --db-name PROJECT_DATABASE --db-user PROJECT_DATABASE_USER \
  --version ACTUAL_VERSION --ref refs/tags/base/vACTUAL_VERSION
git add PROJECT.md BASE_UPDATES.md && git commit -m "chore: record Base baseline"
```

## Base 发版要求

每次 Base 改动必须先确定 SemVer，然后新增一个 Manifest。更新节点必须按可独立
解释/回滚的能力边界拆分，不能只写“若干优化”。`CHANGELOG.md` 面向人阅读，
Manifest 面向同步工具；两者版本、日期、迁移和动作必须一致。

## 回滚

- 合并未提交：`git merge --abort`；
- 已提交：revert 整个同步 merge commit；
- `BASE_UPDATES.md` 追加回滚记录，不删除既有升级事实；
- 数据库按目标 Manifest 的 rollback 执行，只操作下游自己的数据库。
