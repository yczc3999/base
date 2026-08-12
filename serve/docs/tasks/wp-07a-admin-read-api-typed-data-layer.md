# WP-07A — Admin Read API、Keyset 查询与 Typed Frontend Data Layer

> 状态：**ACCEPTED（审查通过）**
> 前置：`WP-06` 已 ACCEPTED；Alembic head=`b1000052`；manifest SHA=`a2280e003d02a9799e263efbef5f1de504f79e2a5e0f94564b6c9a133263f868`
> 执行模型：DeepSeek V4 Flash
> 唯一完成交付：`serve/docs/manifests/wp-07a-admin-read-api-typed-data-layer.md`
> 初交 commits：`a1718c2`（实现）/ `8bbc1d5` / `d984f23`（manifest/索引）
> 审查修复 commit：`280afccc0adc8695b6a2508f27557c737571db16`
> 最后更新：2026-08-12 EDT

## 审查接受记录

- 初交的 `64/1781/1992 passed` 只证明原测试集通过，未覆盖首屏过滤与 `as_of` 快照、三个
  列表端点的运行时错误、AI artifact metadata 双权限、0070 exact seed、BIGINT DTO、timeline
  keyset、zstd Range、GET 只读事务，以及 frontend 完整路由/query/401 合同；原 performance
  harness 也没有真实执行合同规定的 32 并发持续 60 秒、完整 traversal、RBAC、serialization、
  pool 与 response-byte 证明。因此旧结果不作为接受证据。
- `280afcc` 已关闭全部范围内 P1：统一首屏/后续页 filter + frozen `as_of` tuple keyset，修复
  components/positions/model-routes 与 timeline，落实 exact permission seed、20 个对应 query 的索引、
  AI artifact metadata/content lineage 双门、BIGINT/NUMERIC 字符串 DTO、zstd 单 Range、READ ONLY/no-store
  read plane，以及完整 typed frontend facade/query/shared 401 refresh/cancellation。
- 最终 clean 复验：unit/config **135 passed**；真 PostgreSQL **50 passed**；router **19 passed**；
  frontend **20 passed**、lint 0 error、build 通过；`tests/trading` **1794 passed**；全仓
  **2005 passed**，全部 0 skip/fail。head=`b1000070` 唯一，offline SQL 8,870 行、secret hits=0。
- clean perf `/tmp/pm_v2_perf_smoke_7a.json` SHA-256
  `0138af193bfcf430fb2472ec0c06db27e3d7ae3b39d53a78b3d241b08cf13ddb`：20/20 gates PASS；
  100,008 行，深页 p95/p99=14.911/18.545ms，32 workers 持续 60.177s、97.429 req/s，
  pool wait p95=.048ms，traversal lost/duplicate/out-of-snapshot=0。
- 完整证据、精确审查 changed files、blocker 与回滚见 completion manifest；审查结论：
  **ACCEPTED**。`WP-07B` 继续保持 `BLOCKED_PRODUCT_VISUAL_DECISION`，未创建任务文件。

## 0. 快车道执行规则

1. 连续完成四个 Checkpoint：A API/cursor/RBAC freeze → B PostgreSQL read plane →
   C frontend typed API/query → D integration/performance/security/manifest。只生成一份 completion manifest。
2. WP-07A 只建设数据层，不创建 `views/v2/**`、路由页面、页面菜单、视觉 token 或业务 UI 组件。
3. Controller 只负责 DTO、鉴权、UoW 与响应；SQL 在 Repository；read 语义、cursor/filter/freshness
   在 Logic。禁止 Controller 计算 Gate、edge、PnL、风险或状态转换。
4. 列表统一 keyset，不允许 `OFFSET`、深页 `COUNT(*)`、任意字段排序、默认加载 raw artifact、
   prompt/response、book levels 或大 JSON。
5. Dashboard 只读 WP-04 五张 projection；资金、订单、权限、账本等权威数字必须显式标注
   authoritative，并从事实表精确查询。
6. 范围内 P0/P1 直接修复并复验，不拆 `-rN`。最终代码上只跑一次 full/perf/security。
7. WP-07A 接受前不创建 WP-07B。WP-07B 开始前必须先由用户确认产品色板、语义颜色、字体、
   密度、圆角 token 和一张真实 Episode Detail 高保真预览。

## 1. 目标与用户价值

提供稳定、安全、可分页、可下钻的 V2 Admin 数据边界，使后续 UI 不直接理解数据库或重算业务结论：

1. 14 个业务域均有 typed read API；
2. 任一 action 可追到 market、component、episode、submission、decision、quote、Gate、execution、
   ledger、release 和 artifact；
3. 大表在并发写入下翻页无重复、无遗漏，同一 `as_of` 快照稳定；
4. 非超级管理员只能访问明确授予的域；
5. 前端 API/query 层具备强类型、AbortSignal、稳定 query key 和 cursor 失效规则；
6. 页面实现前即可独立验证 API 合同、权限、安全与性能。

WP-07A 明确收口为 **read plane + typed query data layer**。架构提到的 Strategy Config “10 分区、
66 typed fields”尚未冻结精确字段名、类型、默认值与范围；kill/release rollback/label adjudicate/
replay cancel 的完整 command 语义也未形成可执行合同。本任务不得自行发明这些产品规则或开放半成品 mutation。

## 2. 已确认决策

### 2.1 数据表示

- 所有 BIGINT ID 在 JSON/TypeScript 中为十进制字符串，禁止 JS number。
- `NUMERIC` 为 decimal string，禁止 float。
- 时间为 UTC ISO-8601。
- hash 为 64 位小写十六进制。
- 列表统一响应：

  ```json
  {
    "items": [],
    "next_cursor": null,
    "has_more": false,
    "as_of": "2026-08-12T00:00:00Z",
    "filter_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  }
  ```

- 列表不返回 `page/pageSize/total`；统一 response envelope 继续使用 Base 的
  `{code,msg,data}`，不得创建第二套错误协议。

### 2.2 Cursor

Cursor v1 为 HMAC 签名 opaque token，payload 固定：

```text
version
endpoint
sort_time
id
direction
filter_hash
as_of
```

1. 使用注入式 codec；生产从既有服务端 `APP_KEY` 通过独立 context label 派生 key，不新增浏览器可见
   secret，不在 cursor 中保存 key id、原始 filter 或身份信息。
2. 首屏由服务端冻结 `as_of=statement_timestamp()`；后续页必须复用。
3. `filter_hash=H(endpoint + query_version + canonical_filters + direction)`。
4. cursor tamper、endpoint/filter/direction mismatch、非 UTC、超长 token、坏签名统一 400。
5. 每个 endpoint 只允许一套已声明的 `(sort_time,id)` 索引和固定方向。
6. 默认 limit=50，允许范围 1–200；limit 不进入 cursor 身份，改变 limit 不改变 snapshot/filter。

### 2.3 Read projection

Dashboard 只读：

```text
ops_health_current
pipeline_funnel_hourly
account_risk_current
provider_cost_daily
latest_chain_summary
```

每块返回：

```text
as_of
source_high_watermark
projection_version
projection_hash
freshness_status
```

不得在 Dashboard request 中临时聚合 AI invocation、ledger、order 或 event 大表。资金、订单、权限、
账本的 detail/list 查询必须从权威事实读取，并在 DTO 中返回 `authoritative=true` 与明确 `as_of`。

### 2.4 Artifact

1. metadata 和 content 分离；content 必须提供单段 `Range`，单次最大 1 MiB。
2. 正确返回 `206/Content-Range/Accept-Ranges/ETag`；多 Range、越界、无 Range 或超限 fail closed。
3. generic read 要求 `v2:artifact:read`。
4. AI request/raw/parsed artifact 还必须同时具备 `v2:ai:artifact`。
5. API 不返回存储路径、bucket credential、签名 URL、request header 或 secret。
6. 列表和普通 detail 永不内联 artifact body。

## 3. 依赖与必读

- `/code/pollymarket/docs/v2/ARCHITECTURE.md` 的不变量、阶段与资金权限边界；
- `serve/docs/v2-implementation-contract.md` §2、§3、§6、§9–§12；
- `serve/docs/polymarket-v2-platform-design.md` §3、§8–§10；
- `serve/docs/performance-cache-database-design.md` 的 Admin SLO、projection/keyset、api pool 与响应预算；
- `serve/docs/api-convention.md` 的 Base response/auth 约定；
- WP-06 accepted manifest SHA `a2280e003d02a9799e263efbef5f1de504f79e2a5e0f94564b6c9a133263f868`。

发现规范冲突时，只停止受影响端点并记录 `PRODUCT_DECISION_REQUIRED`；不得扩大成页面工作，也不得
用 generic CRUD offset/count 降级替代 keyset。

## 4. Checkpoint A — API、cursor、权限与 schema freeze

### 4.1 精确生产文件

```text
serve/alembic/versions/b1000070_v2_0070_admin_read_permissions_indexes.py

serve/app/db/cursor.py
serve/app/schemas/trading/admin.py
serve/app/schemas/trading/__init__.py
serve/app/repositories/trading/admin_read.py
serve/app/repositories/trading/__init__.py
serve/app/logics/trading/admin_read.py
serve/app/logics/trading/__init__.py
serve/app/deps.py

serve/app/controllers/admin/trading.py                 # 删除，迁移为 package
serve/app/controllers/admin/trading/__init__.py
serve/app/controllers/admin/trading/router.py
serve/app/controllers/admin/trading/common.py
serve/app/controllers/admin/trading/runtime.py
serve/app/controllers/admin/trading/dashboard.py
serve/app/controllers/admin/trading/markets.py
serve/app/controllers/admin/trading/components.py
serve/app/controllers/admin/trading/episodes.py
serve/app/controllers/admin/trading/decisions.py
serve/app/controllers/admin/trading/execution.py
serve/app/controllers/admin/trading/model_routes.py
serve/app/controllers/admin/trading/ai_invocations.py
serve/app/controllers/admin/trading/costs.py
serve/app/controllers/admin/trading/strategy_config.py
serve/app/controllers/admin/trading/releases.py
serve/app/controllers/admin/trading/evaluation.py
serve/app/controllers/admin/trading/replay.py
serve/app/controllers/admin/trading/integrity.py
serve/app/controllers/admin/trading/artifacts.py

serve/app/main.py
serve/app/observability/metrics.py
```

`main.py` 只 include `controllers/admin/trading/router.py` 一次；旧
`/api/admin/trading/runtime` 保持兼容。允许同步直接受影响的 import/head/model-count/router tests；
禁止顺手重构 legacy Admin CRUD。

### 4.2 RBAC seed

0070 创建不可见权限目录和以下 BUTTON 权限，不创建业务页面菜单，不自动授予普通角色：

```text
v2:dashboard:view
v2:markets:view
v2:components:view
v2:episodes:view
v2:decisions:view
v2:execution:view
v2:models:view
v2:ai:view
v2:ai:artifact
v2:costs:view
v2:config:view
v2:release:view
v2:evaluation:view
v2:replay:view
v2:integrity:view
v2:artifact:read
```

要求：

- slug/perms 冲突但内容不全等时 migration fail；
- 不依赖固定 menu ID；
- 不自动扩大现有 admin 角色权限；
- 超级管理员仍按 Base 规则绕过；
- downgrade 前如存在 `role_menus` 绑定则整次拒绝；
- `require_all_perms()` 新增为 AND 语义，不改变现有 `require_perms()` 的 OR 语义；
- 0070 只创建实际 read query 所需且能由 performance/EXPLAIN 证明的 composite index，不预建 WP-08
  的通用归档/在线索引；index 名、列序与 SQL query 必须一一对应。

## 5. Checkpoint B — Read API 与 PostgreSQL 查询合同

### 5.1 Endpoint

```text
GET /api/admin/v2/dashboard

GET /api/admin/v2/markets
GET /api/admin/v2/markets/{market_id}

GET /api/admin/v2/components
GET /api/admin/v2/components/{component_id}

GET /api/admin/v2/episodes
GET /api/admin/v2/episodes/{episode_id}
GET /api/admin/v2/episodes/{episode_id}/timeline

GET /api/admin/v2/decisions
GET /api/admin/v2/decisions/{decision_id}

GET /api/admin/v2/execution/intents
GET /api/admin/v2/execution/orders
GET /api/admin/v2/execution/positions
GET /api/admin/v2/execution/ledger
GET /api/admin/v2/execution/{decision_id}/trace

GET /api/admin/v2/model-routes

GET /api/admin/v2/ai-invocations
GET /api/admin/v2/ai-invocations/{id}?occurred_at=<UTC>

GET /api/admin/v2/costs

GET /api/admin/v2/strategy-config
GET /api/admin/v2/strategy-config/{id}

GET /api/admin/v2/releases
GET /api/admin/v2/releases/{id}

GET /api/admin/v2/evaluation/labels
GET /api/admin/v2/evaluation/metrics
GET /api/admin/v2/evaluation/promotions

GET /api/admin/v2/replay
GET /api/admin/v2/replay/{id}

GET /api/admin/v2/integrity/runtime
GET /api/admin/v2/integrity/alerts
GET /api/admin/v2/integrity/workflows/{aggregate_type}/{aggregate_id}

GET /api/admin/v2/artifacts/{content_hash}/metadata
GET /api/admin/v2/artifacts/{content_hash}/content
```

AI invocation detail 必须使用复合身份 `(occurred_at,id)`，不得只按分区内 id 猜记录。

### 5.2 Query 约束

1. `admin_read.py` 每个 endpoint 使用独立静态 SQL；禁止客户端传表名、列名或 SQL 片段。
2. filter、sort、响应字段全部显式 allowlist；未知 filter 返回 400，不静默忽略。
3. 列表 SELECT 仅投影摘要列；detail 也不默认返回 raw prompt、raw response、signed body、book levels
   或大 JSON。
4. timeline 按 `(created_at,id)` keyset；超过 500 项仍分页。
5. workflow `aggregate_type` 使用固定 allowlist，不允许任意字符串扩展查询面。
6. 所有 query 在 `api` pool/statement timeout 内执行；不得占 execution/reconciliation pool。
7. GET 使用只读事务，不写 cache、业务 event 或 projection；响应头 `Cache-Control: private, no-store`。
8. 添加 `pm_admin_query_seconds`、`pm_admin_response_bytes`，label 只含 endpoint/result，不含业务 ID。
9. Repository 不 commit、不读 env、不调用 Redis/network；Logic 不读取“latest”策略决定业务结果。
10. 事实查询的 `as_of` 是该 read snapshot，不得伪装为 projection 高水位或 provider observed time。

### 5.3 必须证明的查询链

- Market detail → contract snapshot/spec/payout/current/cohort；
- Component detail → schema/version/member contract；
- Episode detail → frozen release/prior/evidence/submission/Gates；
- Decision detail → quote binding/valuation/action set/intent；
- Execution trace → envelope/order/attempt/trade/position/ledger/chain operation；
- AI detail → model binding/tool/validator/artifact refs/downstream effect；
- Release detail → exact config/strategy/execution spec/permission/hash；
- Integrity timeline → workflow/outbox/external-call/alert/artifact lineage。

## 6. Checkpoint C — Typed frontend API/query data layer

### 6.1 精确生产文件

```text
admin/src/api/request.ts
admin/src/api/v2/types.ts
admin/src/api/v2/cursor.ts
admin/src/api/v2/dashboard.ts
admin/src/api/v2/markets.ts
admin/src/api/v2/components.ts
admin/src/api/v2/episodes.ts
admin/src/api/v2/decisions.ts
admin/src/api/v2/execution.ts
admin/src/api/v2/models.ts
admin/src/api/v2/ai.ts
admin/src/api/v2/costs.ts
admin/src/api/v2/configuration.ts
admin/src/api/v2/evaluation.ts
admin/src/api/v2/replay.ts
admin/src/api/v2/integrity.ts

admin/src/queries/v2/queryKeys.ts
admin/src/queries/v2/dashboard.ts
admin/src/queries/v2/markets.ts
admin/src/queries/v2/components.ts
admin/src/queries/v2/episodes.ts
admin/src/queries/v2/decisions.ts
admin/src/queries/v2/execution.ts
admin/src/queries/v2/models.ts
admin/src/queries/v2/ai.ts
admin/src/queries/v2/costs.ts
admin/src/queries/v2/configuration.ts
admin/src/queries/v2/evaluation.ts
admin/src/queries/v2/replay.ts
admin/src/queries/v2/integrity.ts

admin/package.json
admin/package-lock.json
admin/vitest.config.ts
```

### 6.2 类型合同

```ts
type EntityId = string
type DecimalString = string
type UtcIsoString = string
type Sha256 = string
type OpaqueCursor = string

interface CursorPage<T> {
  items: T[]
  next_cursor: OpaqueCursor | null
  has_more: boolean
  as_of: UtcIsoString
  filter_hash: Sha256
}
```

要求：

1. API module 只发请求并解析统一 response；不吞 401/403/contract errors。
2. `request.ts` 暴露支持 `AbortSignal` 的 typed request，不复制第二套 refresh/error transport。
3. query hook 负责 query key、cache、AbortSignal、cursor/as_of 与失效。
4. query key 至少包含 domain、endpoint、normalized filters、cursor、as_of。
5. filter 改变时清空 cursor/as_of；不得把旧 cursor 发送给新 filter。
6. 翻页保留上一页数据，但不把上一 filter 的数据闪回。
7. cancellation 不显示错误 toast、不重试、不污染 cache。
8. View 不直接 import `request.ts`；WP-07A 不新增任何 `.vue` 页面、路由或视觉样式。
9. TypeScript 类型不得把 BIGINT/NUMERIC 降为 `number`；opaque cursor 不在浏览器解码或重签。

## 7. Checkpoint D — 测试、性能、安全与唯一 manifest

### 7.1 精确测试/交付文件

```text
serve/tests/trading/unit/test_v2_admin_cursor.py
serve/tests/trading/unit/test_v2_admin_schema.py
serve/tests/trading/unit/test_v2_admin_query_allowlists.py
serve/tests/trading/integration/test_v2_0070_admin_permissions_migration.py
serve/tests/trading/integration/test_v2_admin_api_rbac.py
serve/tests/trading/integration/test_v2_admin_api_keyset.py
serve/tests/trading/integration/test_v2_admin_read_contract.py
serve/tests/trading/integration/test_v2_admin_artifact_range.py
serve/tests/trading/integration/test_v2_admin_secret_boundary.py
serve/tests/trading/performance/admin_api_smoke.py

admin/src/api/v2/__tests__/contract.spec.ts
admin/src/api/v2/__tests__/cursor.spec.ts
admin/src/queries/v2/__tests__/queryKeys.spec.ts
admin/src/queries/v2/__tests__/cancellation.spec.ts

serve/docs/manifests/wp-07a-admin-read-api-typed-data-layer.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

允许同步 `test_v2_router_registration.py`、Alembic head、model import/count、dependency lock 等直接受影响测试；
不得修改 V1、WP-06 accepted manifest 或创建 WP-07B 文件。

## 8. 必测事实

1. 401、缺权限 403、单域授权、跨域拒绝、超级管理员通过；
2. AI artifact 同时要求两项权限，generic artifact hash 不得绕过；
3. cursor tamper、换 endpoint、换 filter、换 direction、坏 UTC、超长 payload 全拒绝；
4. 并发插入期间完整翻页 lost=0、duplicate=0，所有页 `as_of/filter_hash` 全等；
5. 未知 filter/sort/field 返回 400，SQL 无 OFFSET、任意标识符拼接或深页 COUNT；
6. Dashboard 只命中五张 projection，不扫事实大表；
7. money/permission/order/ledger 字段明确标识 authoritative/as_of；
8. BIGINT/NUMERIC 不以 JSON number 返回；
9. detail 链 ID 全等，不由 Controller 重算 Gate、PnL、edge 或风险；
10. artifact Range 的 206/416/ETag/MIME/1 MiB 上限正确，无路径泄露；
11. raw prompt/response/book/secret 不出现在 list/detail；
12. TypeScript query key 隔离 filter/cursor/as_of；
13. AbortSignal 到达 Axios，取消请求不 retry、不 toast、不污染 cache；
14. runtime 旧路由兼容；新 integrity 路由使用 `v2:integrity:view`；
15. 0070 空库/Base 升级、重复验证、downgrade preflight、ORM drift、offline SQL 全过；
16. WP-01～06 回归不变；真实外部 network/chain/money=0；
17. 非超级管理员未获显式 role binding 时新增 V2 endpoint 全部 403，migration 不隐式授权；
18. API error/log/trace/metrics 不含 cursor secret、raw filter payload、Authorization 或 artifact body。

## 9. 性能与容量

真 PostgreSQL、真实 Repository/Logic/FastAPI serialization，认证边界使用本地 deterministic fixture：

- 至少 100,006 行高容量数据集；代表域覆盖 Markets、Episodes、AI Invocations；
- keyset 深页 query p95≤500ms、p99≤1s；
- 32 并发持续 60s；
- 每个列表响应≤200KiB；
- pool wait p95≤20ms，连接峰值不超过 api profile；
- 完整 traversal lost/duplicate=0；
- deep cursor `EXPLAIN` 使用目标 composite index，不出现 OFFSET；
- 报告 query/serialization/permission/total p50/p95/p99；
- 报告 RSS/CPU/event-loop lag/连接峰值/response bytes；临时库、文件、Node server 残留=0；
- 输出 `/tmp/pm_v2_perf_smoke_7a.json`，含 clean code commit、seed、数据规模、query/index plan 摘要、
  测试计数和 `hard_assertions=PASS`。

不得用关闭 RBAC、绕过 API serialization、直接 Repository microbenchmark 或预装全部结果到内存冒充
Admin API SLO。

## 10. 验收命令

```bash
cd /code/pollymarket/v2/serve

python3 -m compileall -q app tests alembic
.venv/bin/pip check

.venv/bin/pytest -q \
  tests/trading/unit/test_v2_admin_cursor.py \
  tests/trading/unit/test_v2_admin_schema.py \
  tests/trading/unit/test_v2_admin_query_allowlists.py

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0070_admin_permissions_migration.py \
  tests/trading/integration/test_v2_admin_api_rbac.py \
  tests/trading/integration/test_v2_admin_api_keyset.py \
  tests/trading/integration/test_v2_admin_read_contract.py \
  tests/trading/integration/test_v2_admin_artifact_range.py \
  tests/trading/integration/test_v2_admin_secret_boundary.py

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/python -m tests.trading.performance.admin_api_smoke

cd /code/pollymarket/v2/admin
npm ci
npm run test -- --run
npm run lint
npm run build

cd /code/pollymarket/v2/serve
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
.venv/bin/alembic heads
.venv/bin/alembic upgrade b1000070 --sql > /tmp/wp07a.sql
git diff --check
```

必须 0 skip/0 fail，唯一 Alembic head=`b1000070`；offline SQL/日志/API/artifact secret-value marker=0；
真实 outbound/network/chain/money counters=0。manifest 只写最终 clean commit 的真实数字。

## 11. Completion manifest 合同

只创建：

```text
serve/docs/manifests/wp-07a-admin-read-api-typed-data-layer.md
```

至少记录：状态与实现 commit、精确 changed files、0070 permission/index/precondition/downgrade、
endpoint→permission→query→DTO 矩阵、cursor schema/filter hash/as_of/tamper 证据、projection 与 authoritative
fact 查询、artifact Range/lineage/双权限、frontend types/query/cancellation/build、性能 JSON/SHA、
secret/raw payload scan、full regression、blocker/non-goal/rollback。

完成后按本 task 冻结口径计算唯一 self SHA：删除且只删除最后一行

```text
COMPLETION_MANIFEST_SHA256: <64 lowercase hex>
```

（含行尾 LF）后 SHA-256；将同一值同步两个 README，再独立复验。

## 12. Blocker、非目标与回滚

### Blocker

- WP-06 accepted head/manifest SHA 与本任务前置不一致；
- 任务被要求实现 mutation，但 66-field schema/command 语义未获产品确认；
- cursor 不能由非空服务端 secret 派生；
- permission seed 与既有 slug/perms 冲突；
- 高容量 query 必须使用 OFFSET/COUNT 或扫描 raw payload 才能达标；
- artifact 无法按 lineage 做附加权限判断；
- frontend 数据层必须依赖页面或视觉产品决策才能完成。

遇到 blocker 如实写唯一 manifest 并停止，不放宽 RBAC/cursor/Range，不用 generic CRUD 冒充完成。

### 非目标

- 不创建 14 菜单页、5 详情页或任何 `.vue` 业务页面；
- 不修改 palette、语义颜色、字体、密度、圆角或通用 UI；
- 不实现 config draft/publish、release publish/rollback、kill、label adjudicate/promote、replay create/cancel；
- 不补写未定义的 66-field config schema；
- 不执行交易、回放、AI、projection rebuild 或链操作；
- 不建第二套事实表、账本、projection 或缓存事实源；
- 不接真实 RPC/CLOB/Relayer/资金；
- 不做 WP-07B/08，不改 V1。

### 回滚

- 先撤销所有 V2 permission role bindings；存在绑定时 0070 downgrade 必须拒绝；
- downgrade 0070 删除专用索引与 permission seed，不修改任何 trading facts；
- Controller package、frontend API/query 可独立 revert；
- 不删除 artifact、ledger、projection 或 accepted manifest；
- cursor 是无状态 token，代码回滚后自然失效，不需数据迁移。

## 13. 交接与 WP-07B 门禁

1. 全部 Checkpoint 完成后，创建唯一 manifest 并把两个 README 的 WP-07A 标为 `DONE（待审）`。
2. 不自行写 ACCEPTED，不创建 WP-07B，不生成业务页面或视觉方案。
3. 用户回复“完成”后，审查者读取 task/manifest/Git，复跑 RBAC、keyset/as_of、projection、artifact
   Range、frontend type/query、perf/security；范围内 P0/P1 直接修复。
4. WP-07A 审查接受并冻结 manifest 后，创建 WP-07B READY 合同前必须先向用户展示并取得明确确认：
   - 产品专属 palette；
   - semantic color roles；
   - typography token；
   - density/spacing token；
   - radius token；
   - 一张使用真实 Episode 数据结构的高保真 Episode Detail 预览。
5. 预览必须严格平面：大块纯色、无阴影、无渐变、无 glass/blur、无 highlight、无浮卡、
   无 lift/scale interaction。
6. 未取得上述确认时，WP-07B 保持 `BLOCKED_PRODUCT_VISUAL_DECISION`，不得批量生成页面。
