# Repository Rules

## Project identity — mandatory

This repository is the reusable **Base Platform foundation**. It is not a product
application, not a customer implementation, and not a workspace for developing a
specific downstream project.

**ONLY FORK OR CLONE THIS REPOSITORY FOR PRODUCT DEVELOPMENT. DO NOT DEVELOP A
PRODUCT DIRECTLY IN THIS REPOSITORY.**

Every concrete project must have its own fork or clone before adding any of the
following:

- product or customer business rules;
- product-specific models, controllers, services, routes, menus, migrations, or jobs;
- provider/exchange/vendor integrations that exist only for one product;
- product prompts, strategy code, workflows, fixtures, screenshots, branding, or copy;
- product-specific deployment configuration, secrets, data, or runtime artifacts.

After establishing the downstream `upstream` remote, initialize a new project with
`scripts/bootstrap-project.sh PROJECT_SLUG "Project Name"`. The script must never
be executed in this Base repository and rejects the Base database identity.

An agent must not treat a request naming a concrete product or business domain as
permission to extend this repository. The request belongs in a fork/clone. Only
changes that remain useful, generic, documented, and product-agnostic belong here.

## Mandatory reading order

Before changing anything, read:

1. `AGENTS.md` — this repository boundary and delivery rules;
2. `CLAUDE.md` — project map and the fork/clone workflow;
3. `VERSION`, `CHANGELOG.md`, and `UPSTREAM.md` — current release and downstream
   synchronization contract;
4. `serve/README.md` — reusable backend capabilities;
5. `admin/README.md` — reusable frontend capabilities;
6. only the relevant generic design document under `serve/docs/`;
7. only the target source files and their existing tests.

Do not use downstream project documents, copied business specifications, or
product implementation plans as authority for this repository. If a document
describes a concrete product rather than a reusable Base capability, it does not
belong in this repository and must be removed rather than adopted.

## Architecture

Preserve the generic structure:

```text
app.routes.register_routes(app)
        → Route Group middleware/permission
        → undecorated Controller Handler → Logic → Model/DB
                                                 ↕
                                            Service + Driver
```

`serve/app/routes/` 是权威路由清单（URL/Method/prefix/鉴权/权限/名称/Tag），
Controller 只保留未装饰 Handler，不再创建 APIRouter 或声明 URL。新增端点
先在 Manifest 声明，再补 Handler；运行时禁止 glob 自动扫描 Controller。
Logic owns reusable business behavior and state transitions. Models own data
shape and constraints. Repositories or model access layers own persistence.
External providers stay behind generic Service + Driver boundaries. Prefer
existing Base capabilities, factories, declarative CRUD, and hooks over
duplication.

## Repository boundaries

- Keep the backend and frontend product-agnostic.
- This Base checkout uses only `base_platform_app@base_platform`; the password
  exists only in ignored `serve/.env`. Downstream projects must use their own
  database and role and must never connect to, migrate, or test this database.
- Keep secrets server-side and out of source, fixtures, logs, and generic settings.
- Keep migrations transactional, preconditioned, and reversible.
- Keep authorization on the server; hiding a frontend control is not authorization.
- Do not add a product-specific shortcut to the Base APIs, generic CRUD, worker,
  settings, queue, storage, notification, SMS, SEO, or RBAC layers.
- Do not add product data or generated runtime output to the repository.

## Validation

```bash
cd serve && pip install -r requirements-dev.txt && pytest
cd serve && alembic upgrade head
cd serve && .venv/bin/python -m app.routes check
cd admin && npm ci && npm run lint && npm run build
python3 scripts/check-base-release.py
python3 scripts/check-database-boundary.py
scripts/bootstrap-project.sh fixture_project --plan
git diff --check
```

Run targeted tests during development, then the complete applicable suite. A
generic improvement must remain valid without any product-specific environment,
credential, database schema, or external service.

## Delivery

Every change must state why it is reusable by multiple downstream projects.
Every releasable change must bump `VERSION`, update `CHANGELOG.md`, synchronize
the frontend package metadata, add `releases/base-vX.Y.Z.json`, pass
`scripts/check-base-release.py`, and receive an immutable `base/vX.Y.Z` Git tag.
Every Manifest must assign stable update-node IDs and list exact scope/files,
compatibility, migrations, downstream actions, conflict hotspots, verification,
and rollback. A release without this machine-readable node ledger is incomplete.

Downstream projects update only through the `upstream` remote and a Base release
tag, preferably with `scripts/sync-base-release.sh`. Never copy random files,
merge an unversioned Base branch, or silently overwrite product code. Do not
create a product task, product manifest, or product roadmap in this repo. Product
work starts only after creating a fork/clone and moving to that repository.
Every downstream must commit `PROJECT.md` (current Base version/tag/commit and next
update command) and append every adoption/update to `BASE_UPDATES.md`.
