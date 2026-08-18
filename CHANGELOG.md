# Base Platform 变更账本

本文件是 Base Platform 的发布账本。`VERSION`、`admin/package.json`、
`admin/package-lock.json` 和本文件的最新发布标题必须保持同一版本号。

## 发布规则

- 每一次可被下游同步的更新都必须创建新的 SemVer 版本。
- 每个发布版本都必须有唯一的 Git tag：`base/vX.Y.Z`。
- 已发布版本的账本内容冻结；修正内容必须进入下一个版本。
- 每个版本必须写清：变更范围、兼容性、迁移命令、下游同步方式和回滚方式。
- `Unreleased` 只记录尚未发布的内容，不作为下游同步目标。

## [Unreleased]

暂无。

## [3.2.0] - 2026-08-18

### 变更范围

- 新增 `releases/base-vX.Y.Z.json` 发布 Manifest；每个版本把变化拆成稳定更新
  node，逐项记录类型、范围、文件、兼容性、迁移、下游动作、冲突点、验证与回滚。
- 回填 v1.0.0、v2.0.0、v3.0.0、v3.1.0 Manifest；新发布缺 Manifest、节点为空、
  版本/日期与 CHANGELOG 不一致时，`check-base-release.py` 阻止发布。
- 新增 `scripts/base-update-ledger.py`：可在合并前聚合 CURRENT→TARGET 跨过的每个
  Base 版本，输出完整节点和两个 Base tag 之间的精确文件差异。
- 下游 `PROJECT.md` 现在记录准确 Base version/tag/commit、最后同步时间、历史账本
  位置、下次计划命令和更新命令；`BASE_UPDATES.md` 追加保存每次采用/更新详情。
- `scripts/sync-base-release.sh` 改为更新前打印计划，使用 `--no-commit` 合并，随后
  运行 route/pytest/lint/build/diff 验证，再将代码、`PROJECT.md` 和含 PASS 证据的
  `BASE_UPDATES.md` 原子提交为同一个 merge commit；`--continue` 可从冲突、验证或
  commit hook 失败恢复，且不会重复追加历史。
- bootstrap 自动建立两个下游账本；新增完整设计合同与 6 项账本/同步测试。

### 兼容性（MINOR）

- HTTP、schema 与运行配置不变；已有下游可以继续运行。
- v3.2.0 起下游同步合同新增两个必须提交的版本账本。v3.1.0 项目首次合并
  v3.2.0 后按 `serve/docs/base-update-ledger.md` 执行一次 ledger record/amend；
  后续版本由新 sync 脚本自动维护。

### 迁移

- 无数据库 migration。
- 账本迁移（v3.1.0 → v3.2.0）：
  ```bash
  python3 scripts/base-update-ledger.py record \
    --from 3.1.0 --to 3.2.0 --ref refs/tags/base/v3.2.0
  git add PROJECT.md BASE_UPDATES.md
  git commit --amend --no-edit
  ```

### 下游同步

- 推荐源版本：`base/v3.2.0`。
- 更新前方案：
  `python3 scripts/base-update-ledger.py plan --from CURRENT --to 3.2.0 --ref refs/tags/base/v3.2.0`。
- 标准更新：`./scripts/sync-base-release.sh 3.2.0`。
- 冲突热点：`scripts/sync-base-release.sh`、`scripts/bootstrap-project.sh`、
  `PROJECT.md`、`BASE_UPDATES.md`、`CHANGELOG.md`。

### 回滚

- 合并未提交：`git merge --abort`。
- 已提交：revert 整个同步 merge commit，并向 `BASE_UPDATES.md` 追加回滚事实，
  不删除已有历史。
- 无数据库变更；已发布 Base tag 不移动。

## [3.1.0] - 2026-08-18

### 变更范围

- 新增 `scripts/bootstrap-project.sh`：下游 Fork/Clone 一条命令完成项目标识、
  专属 PostgreSQL database/role、随机密码、ACL、环境文件、依赖安装、完整
  schema/migration、路由检查、后端测试和前端 lint/build。
- 新增共享 `scripts/lib/provision-postgres-database.sh`；Base 专属 provision 与
  下游 bootstrap 复用同一初始化内核，消除数据库安装/ACL/验收逻辑重复。
- bootstrap 强制要求 URL 不同的 Base upstream/project remotes，拒绝 Base 保留
  标识；未知非空数据库、owner 不匹配或迁移账本缺失时立即停止，不使用
  Base/其他项目数据库兜底。
- 自动生成 Git 忽略且 `0600` 的前后端 runtime env，以及不含密码、供下游提交的
  `PROJECT.md` 同步账本。
- 修正备份锁/任务锁测试对 `base:` Redis 前缀的硬编码，改为跟随
  `settings.APP_NAME`，确保下游项目名下全量测试仍成立。
- 新增 `serve/docs/project-bootstrap.md`、静态发布门禁与 4 项自动测试。

### 兼容性（MINOR）

- 新能力向后兼容；现有 Base 与下游数据库身份、HTTP API 和 schema 不变。
- 已存在的下游项目无需重新 bootstrap，继续按自己的环境执行同步和迁移。

### 迁移

- 无新增数据库 migration。
- 新项目：
  ```bash
  scripts/bootstrap-project.sh PROJECT_SLUG "Project Name"
  ```
- 仅预览派生身份：
  `scripts/bootstrap-project.sh PROJECT_SLUG "Project Name" --plan`。

### 下游同步

- 推荐源版本：`base/v3.1.0`。
- 冲突热点：`scripts/`、`README.md`、`CLAUDE.md`、`UPSTREAM.md`。
- 同步后继续使用下游现有专属数据库，运行完整 release/test/lint/build 验证。

### 回滚

- 已存在项目回滚只需撤销 bootstrap/common-library 文件，不改变数据库身份。
- 新项目首次初始化失败时，只删除该项目生成的 database/role、ignored env 和
  `PROJECT.md`；严禁操作 Base 或其他项目数据库。
- 已发布 Base tag 不移动；修正通过新的 SemVer 发布。

## [3.0.0] - 2026-08-18

### 变更范围

- Base 仓库本机数据库身份统一为固定的
  `base_platform_app@base_platform`；`app.config`、`.env.example`、运行时、
  SQL migration、Alembic 和健康检查不再使用共享 `base_user@base`。
- 新增 `scripts/provision-base-database.sh`：只允许创建/维护上述固定身份，强制
  `PUBLIC` 无 database CONNECT/schema 权限，专属角色无 superuser/createdb/
  createrole/replication/bypassrls 且不得授予其他普通角色。
- 新增 `scripts/check-database-boundary.py` 与测试，并接入发布门禁；默认配置、
  示例配置、建库脚本和数据库边界文档发生漂移时发布直接失败。
- 新增迁移 `028_1_normalize_legacy_menu_seeds.sql`，在新装库执行 029 菜单卫生
  迁移前，事务化清理 SEO/settings 的已知旧种子 ID，解决唯一 slug 冲突。
- 新增 `serve/docs/database-boundary.md`；AGENTS、CLAUDE、README、后端文档、
  UPSTREAM 和问题账本同步记录同一边界。

### 兼容性（MAJOR）

- 未显式配置数据库的运行环境，其默认目标由 `base_user@base` 改为
  `base_platform_app@base_platform`，属于运行配置不兼容变更，因此发布 MAJOR。
- 已正确使用项目专属 `DATABASE_*` 的下游运行时不受默认值变化影响。
- 下游不得使用 Base 专属 database/role，也不得运行 Base 本机 provision 脚本。

### 迁移

- Base 仓库本机：
  ```bash
  export BASE_PLATFORM_DB_PASSWORD="$(openssl rand -base64 36)"
  scripts/provision-base-database.sh
  python3 scripts/check-database-boundary.py
  ```
- 下游：同步前确认 `.env` 指向下游项目专属 database/role，再在该专属库执行
  `cd serve && .venv/bin/python -m app.migrate && .venv/bin/alembic upgrade head`。
- 新迁移只删除精确匹配旧 Base 种子的菜单 ID/slug 组合及其权限子项；使用事务，
  不匹配的下游菜单不受影响。

### 下游同步

- 推荐源版本：`base/v3.0.0`。
- 冲突热点：`serve/app/config.py`、`serve/.env.example`、
  `serve/databases/migrations/`、`scripts/check-base-release.py`、`UPSTREAM.md`。
- 同步后验证：
  ```bash
  python3 scripts/check-base-release.py
  cd serve && pytest
  cd ../admin && npm run lint && npm run build
  cd .. && git diff --check
  ```

### 回滚

- 应用代码可回滚到同步前提交，但不得把下游切到 Base 的 `base_platform`。
- 迁移回滚时从下游自己的备份恢复被清理的已知旧菜单种子；Base 的数据库身份和
  ACL 不回退到共享账户。
- 已发布 Base tag 不移动；修正通过新的 SemVer 发布。

## [2.0.0] - 2026-08-17

### 变更范围

- 引入集中式路由注册表（`serve/app/routes/`），`main.py` 仅保留
  `app.routes.register_routes(app)` 唯一入口。
- 新增 Laravel 风格 DSL：`RouteRegistry.group()` / `get/post/put/patch/delete/
  options/head/match/any/fallback/mount/crud` 与 `RouteBuilder` 链式元数据。
- 27 个 Controller 移除 `APIRouter`、路由装饰器与 `include_router()`，
  只保留未装饰 Handler；鉴权/权限策略移至 Route Manifest。
- 新增 `python -m app.routes list|check|json` CLI 全局路由目录。
- 新增编译阶段强制校验（重复路径、Route ID、operationId、fallback 遮蔽、
  access 边界、any()/CORS 保护、mount 重复等）。
- 新增 `current_auth` / `current_auth_optional`（`serve/app/deps.py`），
  鉴权策略由 Route middleware 写入 `request.state.auth`。
- 补齐测试环境直接依赖 `aiosqlite`，保证全量测试可在新建开发环境中离线运行。
- `serve/app/controllers/crud.py` 拆分 `CrudController`，`register_legacy_crud()`
  生成 5 契约端点，`controllers.base.crud_router` 保留兼容 re-export。

### 删除范围

- 删除 `controllers/admin/log.py` 与 `controllers/admin/dict.py`
  （其 CRUD 直接在 Manifest 中声明）。

### 兼容性（MAJOR）

- 拆除各 Controller 模块对外暴露的 `router` / `file_proxy_router` 对象，
  Python 集成面不兼容，本版本升级为 MAJOR SemVer。
- `controllers.base.crud_router` 保留为兼容层并发出 DeprecationWarning；
  移除兼容层属于 MAJOR 变更，不在本版本执行。
- HTTP/Method/path/operationId/tags/response schema 与 v1 完全一致
  （159 operations / 159 paths 零差异，见 `serve/tests/fixtures/route-catalog-v1.json`）。
- 路由契约快照 `route-catalog-v1.json` 作为不可变基线。

### 迁移

- 后端：`cd serve && pip install -r requirements-dev.txt && pytest`
  （本版本不包含 DB migration）。
- 前端：`cd admin && npm ci && npm run lint && npm run build`。
- 路由专项验证：
  ```bash
  cd serve
  .venv/bin/python -m app.routes check
  .venv/bin/python -m app.routes json > /tmp/base-routes.json
  ```
- 下游若直接引用 Controller 的 router 对象，需改为
  `app.routes.register_routes(app)` 或跟随 Base 的 Route Manifest。

### 下游同步

- 推荐源版本：`base/v2.0.0`。
- 冲突热点：`serve/app/main.py`、`serve/app/controllers/`、`serve/app/deps.py`、
  `serve/app/routes/`。
- 同步后验证：
  ```bash
  python3 scripts/check-base-release.py
  cd serve && pytest
  cd admin && npm run lint && npm run build
  git diff --check
  ```

### 回滚

- 切换前 Tag/提交作为回滚点；若新 Registry 实施失败，整体回退
  `main.py`、Controller、`deps.py` 与新 `routes/` 目录，不做半切换。
- 本重构不包含 DB migration，回滚不涉及数据修复。
- 已发布 Base Tag 不移动；回滚以新 SemVer 修复版本发布。

## [1.0.0] - 2026-08-17

### 变更范围

- 建立干净的、可复用的 Base Platform 基线。
- 明确本仓库只能作为通用基础项目维护；具体项目必须 Fork/Clone 后开发。
- 恢复通用 FastAPI 后端、Vue Admin、CRUD、RBAC、队列、存储、通知、SMS、SEO、导出和文件管理能力。

### 删除范围

- 删除所有 Polymarket V2 业务代码、迁移、测试、文档、页面、fixture、运行时和产品配置。
- 删除与 Base 无关的本地 PHP 项目和数据库备份。

### 兼容性与迁移

- 本版本不包含产品业务迁移。
- 下游项目必须先确认自己的工作树干净，再按 `UPSTREAM.md` 同步。
- 同步后执行后端测试、前端 lint/build 和 `git diff --check`。

### 下游同步

```bash
git fetch upstream --tags --prune
git merge --no-ff refs/tags/base/v1.0.0 -m "chore(sync): update Base to v1.0.0"
```

后续版本优先使用：

```bash
./scripts/sync-base-release.sh X.Y.Z
```

### 回滚

- 同步合并尚未推送时：回到同步前的分支或撤销同步 merge commit。
- 已推送后：在下游项目创建明确的回滚提交，不修改已发布的 Base tag。

## 后续版本条目模板

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added / Changed / Fixed
- TBD

### 兼容性
- TBD

### 迁移
- 命令：
- 顺序：
- 是否需要停机：

### 下游同步
- 推荐源版本：
- 冲突热点文件：
- 同步后验证：

### 回滚
- TBD
```
