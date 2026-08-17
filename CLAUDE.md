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
```

Development then happens only in `<PROJECT_DIRECTORY>`. Keep this repository as
the upstream generic foundation.

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
Request → Controller → Logic → Model/DB
                         ↕
                    Service + Driver
```

Use existing factories, declarative CRUD, allowlists, hooks, and shared services.
Keep authorization server-side, persistence constraints explicit, and migrations
reversible. Product-specific code does not belong in any Base layer.

## Validation commands

```bash
cd serve && pytest
cd serve && alembic upgrade head
cd admin && npm run lint && npm run build
python3 scripts/check-base-release.py
git diff --check
```

## Release and downstream update ledger

Every releasable Base change receives a new SemVer in `VERSION`, matching
`admin/package.json`, `admin/package-lock.json`, and a frozen `CHANGELOG.md`
entry. Publish it as the immutable tag `base/vX.Y.Z`. Downstream projects keep
an `upstream` remote and merge only a named Base tag; use
`scripts/sync-base-release.sh X.Y.Z` and follow `UPSTREAM.md`.

## Clean repository rule

The repository must remain free of product names, product business terminology,
product fixtures, product screenshots, product prompts, product credentials,
product runtime data, and product-only dependencies. When such material appears,
remove it or move the work to a fork/clone before continuing.
