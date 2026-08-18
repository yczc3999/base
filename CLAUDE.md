# Base Platform

This repository is the reusable **Base Platform foundation**. It is intentionally
product-agnostic and may be reused by many projects.

> **ONLY FORK OR CLONE THIS REPOSITORY FOR PRODUCT DEVELOPMENT. DO NOT DEVELOP A
> PRODUCT DIRECTLY IN THIS REPOSITORY.**

## First rule for every agent

Read `AGENTS.md` first. It is the local authority for repository boundaries. A
concrete product feature, product brand, business workflow, provider integration,
strategy, prompt, fixture, migration, page, or deployment setup must be created
in a separate fork or clone, never here.

If this repository is the starting point for a project:

```bash
git clone <BASE_REPOSITORY_URL> <PROJECT_DIRECTORY>
cd <PROJECT_DIRECTORY>
git remote rename origin upstream
git remote add origin <PROJECT_REPOSITORY_URL>
scripts/bootstrap-project.sh PROJECT_SLUG "Project Name"
```

Development then happens only in `<PROJECT_DIRECTORY>`. Keep this repository as
the upstream generic foundation. The bootstrap command creates the downstream-only
database/role, ignored runtime env files, dependencies, migrations, validation and
`PROJECT.md` ledger; it rejects the Base database identity.

## What belongs here

`serve/` is the reusable FastAPI/PostgreSQL/Redis backend. `admin/` is the
reusable Vue 3/TypeScript/Element Plus administration frontend. The generic
capabilities include authentication, RBAC, CRUD, queue and worker primitives,
storage, notification, SMS, SEO, export, file management, settings, and shared
UI components.

## Reading order

1. `AGENTS.md`;
2. `VERSION`, `CHANGELOG.md`, and `UPSTREAM.md`;
3. `serve/README.md` and/or `admin/README.md`, depending on the target;
4. the relevant generic document in `serve/docs/`;
5. the target source files and tests.

The generic design documents are the authority for reusable Base behavior. Do
not import requirements from a downstream product into this repository.

## Architecture

```text
Request → app.routes.register_routes(app) → Route Group middleware/permission
        → undecorated Controller Handler → Logic → Model/DB
                                                 ↕
                                            Service + Driver
```

`serve/app/routes/` 是路由的权威清单（URL/Method/prefix/鉴权/权限/名称/Tag），
Controller 只保留未装饰 Handler，不再创建 APIRouter 或声明 URL。
新增端点先在 `serve/app/routes/` 下的 Manifest 声明，再在对应 Controller
补充未装饰 Handler；运行时禁止 glob 自动扫描 Controller。

Use existing factories, declarative CRUD, allowlists, hooks, and shared services.
Keep authorization server-side, persistence constraints explicit, and migrations
reversible. Product-specific code does not belong in any Base layer.

### 数据库唯一边界

本 Base checkout 只使用 `base_platform_app@base_platform`。权威合同为
`serve/docs/database-boundary.md`，静态门禁为
`scripts/check-database-boundary.py`。下游 Fork/Clone 必须使用自己的专属
database/role，严禁连接或迁移 Base 的 `base_platform`。

### 路由专项验证

```bash
cd serve
.venv/bin/python -m app.routes check
.venv/bin/python -m pytest tests/test_route_contract.py \
  tests/test_route_shadow.py tests/test_route_registry.py \
  tests/test_crud_routes.py tests/test_controller_route_boundary.py
```

- `test_route_contract.py`：159 条 OpenAPI 契约零差异基线。
- `test_controller_route_boundary.py`：AST 强制 Controller 零 APIRouter。

## Validation commands

```bash
cd serve && pytest
cd serve && alembic upgrade head
cd admin && npm run lint && npm run build
python3 scripts/check-base-release.py
python3 scripts/check-database-boundary.py
git diff --check
```

## Release and downstream update ledger

Every releasable Base change receives a new SemVer in `VERSION`, matching
`admin/package.json`, `admin/package-lock.json`, and a frozen `CHANGELOG.md`
entry. Publish it as the immutable tag `base/vX.Y.Z`. Downstream projects keep
an `upstream` remote and merge only a named Base tag; use
`scripts/sync-base-release.sh X.Y.Z` and follow `UPSTREAM.md`.

Each Base release also has `releases/base-vX.Y.Z.json`, whose stable update nodes
are the machine-readable authority for what changed. Downstream `PROJECT.md`
records the exact current Base version/tag/commit and next command;
`BASE_UPDATES.md` is the append-only detailed adoption/update history. The sync
script prints the cross-version plan before merge and commits code plus both
ledgers atomically.

T0～T2 自动升级合同、只读计划器与下游 receiver（已本地验收、尚未发布）：
`serve/docs/downstream-upgrade-automation.md` 定义 registry、单项目 result 与 batch
JSON Schema（`scripts/schemas/`）。`scripts/base-upgrade-campaign.py` 提供
`validate-registry`、只读 `plan` 与 `summarize`；只处理匹配 channel 的 enabled
项目，从下游默认分支 `PROJECT.md` 发现真实版本，输出稳定排序且集中脱敏的
JSON/Markdown artifact。registry 只接受 `OWNER/REPO`，不接受
`current_version` 或任何 secret/Token 字段；跨 MAJOR 计划必须显式
`--allow-major`，降级始终停止。`.github/workflows/base-upgrade-receiver.yml` 在下游
fresh checkout 中以 `PROJECT.md` 的 `BASE_UPSTREAM_REPOSITORY=OWNER/REPO` 恢复公开
GitHub Base upstream，串行调用 `scripts/run-base-upgrade.sh` 与
`scripts/sync-base-release.sh TARGET --install-deps`；只有原子同步和完整验证通过后才写
`chore/base-vTARGET` 并创建或更新 PR，冲突/验证失败只产出脱敏、schema-valid result
后 abort，不写默认分支。checkout/setup/pip/runner 未产出 result 时，workflow 对合法
输入以 `if: always()` 生成 `dispatch_failed` fallback；runner 精确关联账本 timestamp/
verification，PR 正文列出 update nodes、逐项 PASS、retry 与 rollback。自动回滚只
操作本次创建资源，复用的既有分支/PR 不删除。中央 Provider 派发器和批次执行仍属于
T3+；receiver 基座尚待 T2.5 commit/tag/push 为不可变 `base/v3.3.0`。
最终本地证据：组合定向 272 passed、T2 四文件 78 passed、后端 563 passed、动态 Git
E2E 9/9；159 routes + 1 mount、Alembic、release/database boundary、bootstrap、前端
lint/build、runner `bash -n` 与 diff check 全部通过。

## Clean repository rule

The repository must remain free of product names, product business terminology,
product fixtures, product screenshots, product prompts, product credentials,
product runtime data, and product-only dependencies. When such material appears,
remove it or move the work to a fork/clone before continuing.
