# Repository Guidelines

## Project Structure & Authority

`serve/` is the FastAPI/PostgreSQL/Redis backend; `admin/` is the Vue 3 admin application.
V2 is greenfield and must not import or migrate V1 trading code or SQLite data paths. Read these documents
in order before implementation:

1. `/code/pollymarket/docs/v2/ARCHITECTURE.md` — business logic authority.
2. `serve/docs/polymarket-v2-platform-design.md` — Base integration and UI information architecture.
3. `serve/docs/polymarket-integration-design.md` — Polymarket protocol.
4. `serve/docs/ai-observability-replay-design.md` — AI evidence and replay.
5. `serve/docs/performance-cache-database-design.md` — performance and persistence.
6. `serve/docs/v2-implementation-contract.md` — exact files and work packages.

Conflicts stop the affected work package; do not invent a product decision.

## Architecture & Coding Rules

Preserve `Controller → Logic → Repository/Model`; providers stay behind `Service + Driver`.
Controllers validate and authorize only. Drivers implement wire protocols only. Logic owns gates and state
transitions. Repositories own SQL and never decide trades. Use async Python, explicit types, UTC
`TIMESTAMPTZ`, `Decimal`/base-unit integers, and structured reason codes. Never use float for money,
prices, or shares.

PostgreSQL is the business fact source. Redis is disposable coordination/cache. Secrets belong only in the
server vault. Every external call, AI attempt, decision, order and state transition must be traceable and
append-only. Do not use Base generic CRUD, offset pagination, generic settings cache, or the legacy Worker
for V2 hot tables and execution.

## Build & Validation Commands

```bash
cd serve && pip install -r requirements-dev.txt && pytest
cd serve && alembic upgrade head
cd admin && npm ci && npm run lint && npm run build
git diff --check
```

Run targeted tests during development, then the complete applicable suite. Database/performance claims
require real PostgreSQL/Redis fixtures, not SQLite mocks.

## Delivery Contract

Implement one work package from `v2-implementation-contract.md` at a time. Modify only its allowed files.
Record exact commands and reproducible evidence; never mark a package complete on prose alone. Commit
messages follow repository history: `type(scope): concise summary`, for example
`feat(v2-market): persist Gamma keyset frames`. UI implementation remains blocked until the user approves
the product palette/tokens and one high-fidelity business-page preview.

