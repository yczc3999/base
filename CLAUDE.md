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

T0～T2 自动升级合同、只读计划器与下游 receiver 已以不可变
`base/v3.3.0`（`abc907682edd55d0b58624c6b0fc78c73b8e41e1`）发布。
`serve/docs/downstream-upgrade-automation.md` 定义 registry、单项目 result、batch 与
operator evidence 的 Draft-07 JSON Schema。`scripts/base-upgrade-campaign.py` 提供
`validate-registry`、只读 `plan`、`summarize` 与 T3 `dispatch`；从下游默认分支
`PROJECT.md` 发现真实版本，且只处理匹配 channel 的 enabled 项目。registry
只接受 `OWNER/REPO`，不接受 `current_version` 或 secret/Token 字段；降级始终
停止，跨 MAJOR 必须显式 `--allow-major`。

`.github/workflows/base-upgrade-receiver.yml` 在下游 fresh checkout 中以
`BASE_UPSTREAM_REPOSITORY=OWNER/REPO` 恢复公开 Base upstream，串行调用
`scripts/run-base-upgrade.sh` 与 `scripts/sync-base-release.sh TARGET --install-deps`。只有
原子同步和完整验证通过后才 non-force push `chore/base-vTARGET` 并创建或更新
PR；PR 正文从受信 Release Manifest 渲染 CURRENT/TARGET、全部 update nodes、
migrations、conflict hotspots、downstream actions、release/runtime verification、retry 与
rollback。冲突/验证失败只产出脱敏 result 后 abort，不写默认分支；回滚只
操作本次创建的资源。

T3 operator 实现使用 GitHub REST `2026-03-10`；dispatch POST 不发送已移除的
`return_run_details`，严格接受 `200` run locator，响应丢失时先按唯一
run-name/event/ref/时间窗口恢复，然后按 run ID 轮询并下载唯一 result artifact。
`.github/workflows/base-upgrade-campaign-example.yml` 是要复制到私有 operator/ops 仓库的
人工批准模板；该仓库管理 registry、Token、不可变 Base tools ref 与 evidence。
`project_id` 必须是 operator 预分配的稳定、非敏感 opaque ID，并不会被工具自动匿名化；
run URL 仍会暴露 repository identity，因此真实 evidence 只存私有 ops 仓库/受保护
artifact。evidence 用 `operator_commit` 绑定实际工具 checkout，并在获得 run ID 后立即
保留条目；后续 poll/artifact 失败使用 nullable 字段加 `failure_stage` 保留部分证据。
T3/T4 工程验证已完成（322 targeted、后端 613、动态 Git E2E 9/9，Alembic/
routes/frontend/release/boundary/bootstrap/diff 全部 PASS），但 T3 尚未发布。由于
workflow dispatch 运行下游当前默认分支上的 receiver/runner，已从 v3.3.0
严格 backport 并发布 `base/v3.3.1`（`8721b75a8906d36072c72c54d767aba8802ecdff`）：
只含 runner PR 正文 Manifest 分节、对应测试和标准 PATCH 元数据，不含
dispatcher。真实同步随后发现嵌套 E2E 误读外层 `MERGE_HEAD`，已以
`base/v3.3.2`（`36fb626e578db78198bb61620177280dae18191f`）PATCH 修复。三个试点
手工采用 v3.3.2 后，主线 T3 基于该 tag 发布 `base/v3.4.0`，再演练自动
v3.3.2→v3.4.0 campaign。

## Clean repository rule

The repository must remain free of product names, product business terminology,
product fixtures, product screenshots, product prompts, product credentials,
product runtime data, and product-only dependencies. When such material appears,
remove it or move the work to a fork/clone before continuing.
