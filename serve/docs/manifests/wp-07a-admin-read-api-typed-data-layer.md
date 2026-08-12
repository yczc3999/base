# WP-07A — Admin Read API、Keyset 查询与 Typed Frontend Data Layer — Completion Manifest

> 状态：**ACCEPTED（审查通过）**
> 初交 commits：`a1718c2`（实现，93 files）、`8bbc1d5` / `d984f23`（初版 manifest/索引）
> 审查修复 commit：`280afccc0adc8695b6a2508f27557c737571db16`
> Alembic head：`b1000070`（唯一）
> 接受日期：2026-08-12 EDT

## 1. 审查结论与整改记录

初交测试全部通过，但原测试与 performance harness 没有覆盖任务合同的关键运行路径。审查复验发现：

1. 列表首屏没有应用业务 filter，`as_of` 只进入响应/cursor、没有约束 SQL 快照；
   components、positions、model-routes 的调用签名/keyset NULL 处理会导致 500 或空首屏。
2. episode timeline 固定 DESC 且 cursor 类型/比较错误，会重复页面；AI artifact metadata 只有 generic
   permission，未执行 `v2:ai:artifact` lineage 双门；zstd artifact 的合法单 Range 会变成 500。
3. 0070 只验证 permission slug，slug/label/perms 错配仍可能通过；Dashboard/AI/replay 等 BIGINT 字段
   仍可能投影为 JSON/TypeScript number；GET session 不是数据库 READ ONLY，RBAC 查询可能写 Redis cache，
   且响应没有统一 `private, no-store` 边界。
4. frontend 使用不存在的聚合路径，缺少 detail/timeline/trace/release/artifact 等 facade/query，query
   anchor 失效与 placeholder 隔离不完整；V2 transport 绕开既有 401 refresh，token/replay BIGINT 被声明为
   number。
5. 原 performance harness 没有真实持续 60 秒、没有完整 snapshot traversal、没有真实 RBAC/FastAPI
   serialization/response bytes/EXPLAIN/连接峰值与 pool wait 证明；旧 `77.2/220.0ms`、`74.2 req/s`、
   `0.73ms` 不再作为接受证据。

`280afcc` 已关闭上述全部范围内 P1：

- 所有列表统一 `(sort_time,id)` tuple keyset，首屏和后续页均应用 allowlisted filter 与
  `sort_time <= as_of`；cursor 继续绑定 endpoint/direction/filter hash/frozen `as_of`，limit 不进入身份。
  components、positions、model-routes 与双向 episode timeline 均走同一合同。
- 0070 对 directory/permission 的 slug、label、perms、type、父子关系执行 exact-set fail-closed 校验；
  使用与真实 query 一一对应的 20 个 composite index，保留 role binding downgrade preflight。
- V2 Admin GET 使用独立 READ ONLY request UoW；read-only RBAC 直接查询数据库且不写 Redis，legacy
  `require_perms()` 的 OR/写路径保持不变；统一 `Cache-Control: private, no-store`。API pool 为
  24 + overflow 8，`application_name` 通过 asyncpg `server_settings` 传递；全系统连接预算 60/80。
- Dashboard 改为五张 projection 的显式字段投影；事实 DTO 带 authoritative/as_of；BIGINT 与 NUMERIC
  在 JSON/TypeScript 中保持字符串。AI artifact metadata/content 先查 lineage，再强制 generic + AI
  两项权限。zstd content 在既有 body 上实现有界单 Range，DB 事务不跨 artifact store I/O。
- frontend 补齐 33 个 endpoint 的 typed API/query facade，路径与后端路由全等；query key 隔离
  endpoint/filter/direction/cursor/asOf，filter/direction 改变会清 anchor，placeholder 只用于同一
  snapshot 的 cursor transition；AbortSignal、统一 envelope、shared 401 refresh 与 cancellation 全部复用
  既有 transport；`Int64String` 覆盖 token counters 与 replay seed。
- performance harness 重写为真实 FastAPI + non-super-admin PostgreSQL RBAC + READ ONLY UoW +
  Logic/Repository/asyncpg/ORJSON 全路径，包含 100,008 行、完整 traversal、并发写、EXPLAIN、stage
  latency、response bytes、CPU/RSS/event-loop lag、连接预算与资源清理。

审查者在 clean commit `280afccc0adc8695b6a2508f27557c737571db16` 上复跑定向、真 PostgreSQL、
frontend、全仓与正式 60 秒容量门；全部满足 task §8～§11，因此 WP-07A **ACCEPTED**。

## 2. 审查修复 changed files（精确，74）

以下列表与
`git show --format='' --name-only 280afccc0adc8695b6a2508f27557c737571db16`
全等。初交 93 文件及初版文档分别可由 `git show --name-only a1718c2`、`8bbc1d5`、`d984f23`
独立复现；本节不把初交文件重复记作审查修改。

**后端生产/配置（25）**

```text
serve/.env.example
serve/alembic/versions/b1000070_v2_0070_admin_read_permissions_indexes.py
serve/app/config.py
serve/app/controllers/admin/trading/ai_invocations.py
serve/app/controllers/admin/trading/artifacts.py
serve/app/controllers/admin/trading/components.py
serve/app/controllers/admin/trading/costs.py
serve/app/controllers/admin/trading/dashboard.py
serve/app/controllers/admin/trading/decisions.py
serve/app/controllers/admin/trading/episodes.py
serve/app/controllers/admin/trading/evaluation.py
serve/app/controllers/admin/trading/execution.py
serve/app/controllers/admin/trading/integrity.py
serve/app/controllers/admin/trading/markets.py
serve/app/controllers/admin/trading/model_routes.py
serve/app/controllers/admin/trading/releases.py
serve/app/controllers/admin/trading/replay.py
serve/app/controllers/admin/trading/strategy_config.py
serve/app/deps.py
serve/app/logics/admin_user.py
serve/app/logics/trading/admin_read.py
serve/app/main.py
serve/app/repositories/trading/admin_read.py
serve/app/schemas/trading/admin.py
serve/app/services/database.py
```

**前端 API/query 与测试（39）**

```text
admin/src/api/request.ts
admin/src/api/v2/__tests__/contract.spec.ts
admin/src/api/v2/__tests__/cursor.spec.ts
admin/src/api/v2/ai.ts
admin/src/api/v2/artifacts.ts
admin/src/api/v2/components.ts
admin/src/api/v2/configuration.ts
admin/src/api/v2/costs.ts
admin/src/api/v2/cursor.ts
admin/src/api/v2/dashboard.ts
admin/src/api/v2/decisions.ts
admin/src/api/v2/episodes.ts
admin/src/api/v2/evaluation.ts
admin/src/api/v2/execution.ts
admin/src/api/v2/integrity.ts
admin/src/api/v2/markets.ts
admin/src/api/v2/path.ts
admin/src/api/v2/releases.ts
admin/src/api/v2/replay.ts
admin/src/api/v2/types.ts
admin/src/queries/v2/__tests__/cancellation.spec.ts
admin/src/queries/v2/__tests__/queryKeys.spec.ts
admin/src/queries/v2/ai.ts
admin/src/queries/v2/artifacts.ts
admin/src/queries/v2/components.ts
admin/src/queries/v2/configuration.ts
admin/src/queries/v2/costs.ts
admin/src/queries/v2/dashboard.ts
admin/src/queries/v2/decisions.ts
admin/src/queries/v2/episodes.ts
admin/src/queries/v2/evaluation.ts
admin/src/queries/v2/execution.ts
admin/src/queries/v2/integrity.ts
admin/src/queries/v2/markets.ts
admin/src/queries/v2/models.ts
admin/src/queries/v2/page.ts
admin/src/queries/v2/queryKeys.ts
admin/src/queries/v2/releases.ts
admin/src/queries/v2/replay.ts
```

**后端测试/performance（10）**

```text
serve/tests/trading/integration/test_v2_0070_admin_permissions_migration.py
serve/tests/trading/integration/test_v2_admin_api_keyset.py
serve/tests/trading/integration/test_v2_admin_api_rbac.py
serve/tests/trading/integration/test_v2_admin_artifact_range.py
serve/tests/trading/integration/test_v2_admin_read_contract.py
serve/tests/trading/performance/admin_api_smoke.py
serve/tests/trading/test_v2_config.py
serve/tests/trading/test_v2_database_profiles.py
serve/tests/trading/unit/test_v2_admin_query_allowlists.py
serve/tests/trading/unit/test_v2_admin_schema.py
```

## 3. Endpoint → permission → query → DTO 合同

| API 域/endpoint | 服务端权限 | Repository/query 事实源 | Typed DTO/facade |
|---|---|---|---|
| dashboard | `v2:dashboard:view` | 五张 read projection，显式 watermark/version/hash/freshness | `DashboardData` / dashboard query |
| markets list/detail | `v2:markets:view` | `pm_markets` keyset + contract/snapshot/spec/payout/cohort chain | `MarketSummary/MarketDetail` |
| components list/detail | `v2:components:view` | `forecast_components` keyset + schema/version/member chain | `ComponentSummary/ComponentDetail` |
| episodes list/detail/timeline | `v2:episodes:view` | `forecast_episodes` + prior/evidence/submission/Gate；timeline tuple keyset | `EpisodeSummary/Detail/TimelineItem` |
| decisions list/detail | `v2:decisions:view` | `trade_decisions` + quote/valuation/action/intent chain | `DecisionSummary/DecisionDetail` |
| execution intents/orders/positions/ledger/trace | `v2:execution:view` | 四张 authoritative fact keyset + envelope/attempt/trade/chain/ledger trace | execution summaries + `ExecutionTrace` |
| model-routes | `v2:models:view` | `model_role_bindings` keyset | `ModelRouteSummary` |
| AI list/detail | `v2:ai:view` | `(occurred_at,id)` partition identity + binding/tool/validator/artifact/downstream | `AiInvocationSummary/Detail` |
| costs | `v2:costs:view` | `operating_cost_entries` authoritative keyset | `CostSummary` |
| strategy-config list/detail | `v2:config:view` | `runtime_config_versions` read-only projection | `StrategyConfigSummary/Detail` |
| releases list/detail | `v2:release:view` | `release_manifests` + exact parts/hash chain | `ReleaseSummary/Detail` |
| evaluation labels/metrics/promotions | `v2:evaluation:view` | three fixed evaluation fact queries | typed evaluation summaries |
| replay list/detail | `v2:replay:view` | `replay_runs` authoritative keyset/detail | `ReplaySummary/Detail` |
| integrity runtime/alerts/workflows | `v2:integrity:view` | runtime snapshot + alert keyset + allowlisted aggregate chain | integrity runtime/alert/workflow DTOs |
| artifact metadata/content | `v2:artifact:read`；AI lineage 追加 `v2:ai:artifact` | metadata + lineage query；content 由 bounded ArtifactStore Range 读取 | `ArtifactMetadata/ArtifactByteRange` |

所有列表使用固定 SQL identifier/projection/filter allowlist；未知 filter/direction/aggregate type 返回 400，
无 `OFFSET`、深页 `COUNT(*)` 或客户端标识符拼接。AI detail 只接受 `(occurred_at,id)`；list/detail 不内联
raw prompt、raw response、book levels 或 artifact body。Controller 不重算 Gate、edge、PnL、风险或状态转换。

## 4. Cursor、RBAC、projection 与 artifact 证据

- Cursor v1 精确包含 `version/endpoint/sort_time/id/direction/filter_hash/as_of`，HMAC key 从服务端
  `APP_KEY` 独立 context 派生；tamper、endpoint/filter/direction mismatch、非 UTC、超长/坏签名均 400。
- `filter_hash` 绑定 endpoint + query version + canonical filters + direction；limit 允许 1–200 且不进入
  cursor 身份。首屏由数据库 statement time 冻结 `as_of`，所有 SQL 都以该值限制 snapshot；并发插入
  traversal 的 as_of/filter hash 各只有一个值。
- 0070 seed 为 1 个不可见目录 + 16 个 BUTTON permission exact set；不向普通角色写 `role_menus`。
  slug/label/perms/type/parent 任一不全等则失败；downgrade 前有绑定、未知/缺失/篡改对象时整次拒绝。
- `require_all_perms()` 保持 AND 语义，legacy `require_perms()` 保持 OR 语义；401、单域 403/允许、
  跨域拒绝、超级管理员、AI artifact 双权限均由真实 DB 路径覆盖。
- V2 GET transaction 为 PostgreSQL READ ONLY；权限查询不写 Redis，Repository 不 commit、不读 env、
  不调用 Redis/network。所有 V2 Admin 响应为 `Cache-Control: private, no-store`。
- Dashboard 仅查询 `ops_health_current`、`pipeline_funnel_hourly`、`account_risk_current`、
  `provider_cost_daily`、`latest_chain_summary`；每块显式返回 watermark/version/hash/freshness。
- artifact 无 Range、多 Range、越界或 >1 MiB 均 416；合法单 Range 返回 206、`Content-Range`、
  `Accept-Ranges`、ETag 与 MIME。compressed zstd 与 uncompressed body 都服从同一边界；API 不投影路径、
  credential、signed URL、request header 或 body。

## 5. Frontend typed data layer 证据

- API facade 与后端 33 个 endpoint path 全等，包含 detail、episode timeline、execution trace、release、
  artifact metadata/content、AI composite identity；统一解析 Base `{code,msg,data}` envelope。
- `EntityId/Int64String/DecimalString/UtcIsoString/Sha256/OpaqueCursor` 均保持字符串；AI token counters、
  cache/reasoning tokens、replay seed 与 artifact byte offset 不降为 JS number。
- query key 结构包含 scope/domain/endpoint/normalized filters/direction/limit/cursor/asOf；cursor identity
  不含 limit。filter/direction 改变时 `reconcilePageAnchor` 清 cursor/asOf。
- placeholder 只在 endpoint/filter/direction/snapshot 全等的 cursor transition 生效，不闪回旧 filter；
  AbortSignal 到达 Axios，cancel 不 retry、不 toast、不写入成功 cache。
- `requestV2` 复用 shared axios/envelope/401 refresh/error transport，不创建第二套 token refresh；View 层没有
  新增 `.vue` 页面或直接 import transport。

## 6. 最终 clean 验收证据

全部命令在 clean commit `280afccc0adc8695b6a2508f27557c737571db16` 上运行：

| 验收范围 | 结果 |
|---|---:|
| WP-07A unit/config 定向 | **135 passed**，0 skip/fail |
| 真 PostgreSQL migration/RBAC/keyset/read/Range/security | **50 passed**，0 skip/fail，72 warnings，40.40s |
| router registration/compatibility | **19 passed**，0 skip/fail，8 warnings，1.19s |
| frontend Vitest | **4 files / 20 passed** |
| frontend ESLint | **0 errors**，2 个既有 `v-html` warnings |
| frontend `vue-tsc + vite` build | **PASS** |
| `tests/trading` 全回归 | **1794 passed**，0 skip/fail，79 warnings，323.66s |
| 全仓 | **2005 passed**，0 skip/fail，79 warnings，332.71s |

```text
python3 -m compileall -q app runtimes tests alembic   OK
.venv/bin/pip check                                   No broken requirements
git diff --check                                      exit 0
.venv/bin/alembic heads                               b1000070 (head)，唯一
.venv/bin/alembic upgrade b1000070 --sql              8,870 lines；secret-value hits=0
临时测试/性能数据库残留                               0
```

真实外部 network/chain/order/trade/ledger/money 副作用均为 0。Repository/Logic/FastAPI/serialization、
PostgreSQL RBAC、migration roundtrip 与 legacy router compatibility 均包含在上述结果中。

## 7. 最终 clean 性能与容量证据

Artifact：`/tmp/pm_v2_perf_smoke_7a.json`

SHA-256：`0138af193bfcf430fb2472ec0c06db27e3d7ae3b39d53a78b3d241b08cf13ddb`

Git：commit=`280afccc0adc8695b6a2508f27557c737571db16`，before/after clean=`true`

Schema=`wp07a-admin-api-perf-v2`；`hard_assertions=PASS`；hard gates=**20/20**

| Gate | 最终真实结果 | 门槛 |
|---|---:|---:|
| 代表数据集 | markets/episodes/AI 各 33,336，合计 **100,008** | ≥100,006；三域 |
| 深页 API total p95 / p99 | **14.911 / 18.545ms**（42 samples，最后 25%） | ≤500 / ≤1,000ms |
| query plan | `ix_v2_admin_pm_markets_keyset`，Index Only Scan，OFFSET=false | 目标 index；无 OFFSET |
| 32 workers 持续时间 | **60.177s** | ≥60s |
| 并发吞吐 | **5,863 requests / 97.429 req/s**；最慢 worker **2.842 req/s** | 每 worker ≥1 req/s |
| pool wait p95 / peak | **0.048ms / 32** | ≤20ms / ≤32 |
| 完整 snapshot traversal | **33,336** items；167 pages；lost/dup/out-of-snapshot=**0/0/0** | 全 0 |
| response bytes | 全阶段最大 **116,812 B** | ≤204,800 B |
| 全局连接预算 | api=32；全部 profile **60/80**，余量 20 | ≤80 |
| side effects | outbound/call/chain/order/trade/ledger=**0** | 全 0 |
| cleanup | temp DB removed；额外 temp files/process=0 | 无残留 |

| 阶段 | p50 | p95 | p99 |
|---|---:|---:|---:|
| deep API total | 10.808ms | 14.911ms | 18.545ms |
| deep business query | 3.503ms | 5.871ms | 8.134ms |
| concurrent permission | 6.805ms | 25.626ms | 39.501ms |
| concurrent business query | 275.421ms | 436.775ms | 504.457ms |
| concurrent serialization | 1.479ms | 1.542ms | 1.932ms |
| concurrent total | 315.930ms | 499.268ms | 572.965ms |

资源：CPU 45.862s（单核 76.212%），RSS peak 197,332 KiB，event-loop lag p95/p99
11.162/140.509ms；PostgreSQL connection samples=896，open/checkout/PostgreSQL peak 均为 32。
Harness 使用 non-super-admin DB role/menu lookup；permission query 每次至少 2，business query 每次至少 3，
serialization 5,863/5,863 非零，不以 Repository microbenchmark 冒充 API SLO。

## 8. Secret、边界与剩余 blocker

- offline SQL、API envelope、artifact metadata/content、log/metric label 与 source scan 的 secret-value hits=0；
  cursor secret、Authorization、raw filter、raw prompt/response/body 不出现在投影、日志或指标 label。
- `authorized_capital=0`；真实 external network/CLOB/RPC/Relayer/chain/money=0。
- WP-07A 范围内无剩余 P0/P1。Strategy Config 的 66-field schema 与 mutation command 语义仍未冻结，
  按 task §1/§12 保持非目标，不以本只读数据层发明产品规则。
- WP-07B 为 `BLOCKED_PRODUCT_VISUAL_DECISION`：创建任务前必须向用户展示并确认产品专属 palette、
  semantic color roles、typography、density/spacing、radius token 与一张真实 Episode Detail 高保真预览。
  当前没有创建 WP-07B task、页面、路由、菜单或视觉 token。

## 9. 非目标与回滚

- 不实现 config draft/publish、release rollback、kill、label adjudicate/promote、replay create/cancel；
  不补写 66-field schema；不执行交易/回放/AI/projection rebuild/链操作；不改 V1。
- 不创建任何 `.vue` 业务页面、菜单或产品视觉系统；不提前开始 WP-07B/WP-08。
- 回滚代码时先 revert 审查修复与初交实现；0070 downgrade 前必须解除 V2 role bindings，否则整次拒绝。
  downgrade 只删除 20 个专用 index、16 个 permission 与不可见目录，不修改 trading facts。
- frontend API/query 可独立回滚；cursor 为无状态 token，代码回滚后自然失效；artifact、ledger、projection
  与 WP-00～06 accepted manifest 均保留。

COMPLETION_MANIFEST_SHA256: 881ab05c448fc6b345d0df97738e756a50bd6af2064cefc6c3968b72fff9feb1
