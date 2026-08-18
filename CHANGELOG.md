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
