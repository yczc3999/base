# WP-07A — Admin Read API、Keyset 查询与 Typed Frontend Data Layer — Completion Manifest

> 状态：**DONE（待审）**
> 实现 commit：`a1718c2`
> 唯一交付：本 manifest；任务 `serve/docs/tasks/wp-07a-admin-read-api-typed-data-layer.md`
> 前置：`WP-06` ACCEPTED；head=`b1000070`（唯一）

## 1. 修改文件

**生产代码（后端 14 + 迁移 1）**
```
alembic/versions/b1000070_v2_0070_admin_read_permissions_indexes.py   # 新增 0070 迁移
app/db/cursor.py                                                      # HMAC keyset cursor codec
app/schemas/trading/admin.py                                          # typed DTO（CursorPage/Authoritative/Artifact）
app/repositories/trading/admin_read.py                                # 全 endpoint 静态 SQL + keyset + 查询链
app/logics/trading/admin_read.py                                      # cursor/filter/as_of 编排 + allowlist
app/repositories/trading/__init__.py                                  # 导出 AdminReadRepository
app/logics/trading/__init__.py                                        # 导出 AdminReadLogic
app/deps.py                                                           # require_all_perms（AND 语义）
app/controllers/admin/trading.py  →  app/controllers/admin/trading/  # trading.py 迁移为 package（15 子模块）
app/controllers/admin/trading/{router,common,runtime,dashboard,markets,components,episodes,
  decisions,execution,model_routes,ai_invocations,costs,strategy_config,releases,evaluation,
  replay,integrity,artifacts}.py
app/main.py                                                           # 惰性 include router + admin observe middleware
app/observability/metrics.py                                          # pm_admin_query_seconds/pm_admin_response_bytes
```

**前端（32）**
```
admin/src/api/request.ts                      # requestV2（AbortSignal，复用同一 instance/refresh）
admin/src/api/v2/{types,cursor,dashboard,markets,components,episodes,decisions,execution,
  models,ai,costs,configuration,evaluation,replay,integrity}.ts
admin/src/queries/v2/{queryKeys,dashboard,markets,components,episodes,decisions,execution,
  models,ai,costs,configuration,evaluation,replay,integrity}.ts
admin/package.json / package-lock.json        # + vitest
admin/vitest.config.ts
admin/src/api/v2/__tests__/{contract,cursor}.spec.ts
admin/src/queries/v2/__tests__/{queryKeys,cancellation}.spec.ts
```

**回归同步（15）**：head b1000052→b1000070 + drift 白名单（`migration_helpers` 加
`ix_v2_admin_*`）+ router_registration 惰性展开。

## 2. 实现内容

- **Checkpoint A**：0070 迁移（16 个 `v2:*:view`/`v2:ai:artifact`/`v2:artifact:read` 权限 +
  不可见目录 slug `v2-admin` + 17 个 keyset composite index + downgrade fail-closed
  preflight：role_menus 绑定/未知表/索引缺失/权限篡改整次拒绝）；HMAC cursor codec
  （注入式 key 派生、payload 7 字段、tamper/mismatch/超长/坏签名 400）；`require_all_perms`
  AND 语义不改 OR；Controller 薄、SQL 在 Repository、read 语义在 Logic。
- **Checkpoint B**：全部 endpoint（dashboard/markets/components/episodes/decisions/execution/
  model-routes/ai/costs/config/releases/evaluation/replay/integrity/artifacts）独立静态 SQL；
  filter/sort/响应字段显式 allowlist；列表 keyset（无 OFFSET/COUNT/任意排序）；detail 链
  （market→spec/snapshot/cohort、episode→prior/evidence/submission/gate、decision→quote/
  plan/action/intent、execution trace、AI→binding/tool/validator、release→exact parts、
  integrity→workflow/outbox/call/alert）；BIGINT/NUMERIC 全字符串；authoritative 标注；
  artifact metadata/content 分离 + 单段 Range 206/416/ETag/1MiB；AI detail 复合身份
  `(occurred_at,id)`。
- **Checkpoint C**：frontend typed data layer（AbortSignal 穿透、query key 含
  domain/filters/cursor/asOf、filter 改变清 cursor、opaque cursor 不解码/重签、翻页
  keepPreviousData、cancellation 不 toast/不重试/不污染 cache）；View 不 import request.ts。
- **Checkpoint D**：见 §3。

## 3. 命令与真实结果

```bash
python3 -m compileall -q app tests alembic            # OK
.venv/bin/pip check                                    # No broken requirements
.venv/bin/alembic heads                                # b1000070 (head) 唯一
.venv/bin/alembic upgrade b1000070 --sql               # 8,866 行；secret marker=0
git diff --check                                       # OK
cd admin && npm run test -- --run                      # 4 files / 15 passed
cd admin && npm run lint                               # 0 errors（2 既有 v-html warnings）
cd admin && npm run build                              # vue-tsc + vite ✓
```

- WP-07A 定向（unit/contract/integration/replay）：**64 passed**。
- `tests/trading` 里程碑回归：**1781 passed**（含 head-bumped 0051/0001/alembic_env 等）。
- **全仓**：**1992 passed, 0 skip/fail**（WP-06 后 1928 → +64）。
- 临时库残留：**0**。

## 4. 性能与容量（`/tmp/pm_v2_perf_smoke_7a.json`，`hard_assertions=PASS`）

| Gate | 结果 | 门槛 |
|---|---:|---:|
| G1 深页 keyset p95 / p99 | **77.2ms / 220.0ms**（100,008 行） | ≤500ms / ≤1s |
| G2 32 并发（60s） | **74.2 req/s**（2,560 req） | ≥1 req/s per worker |
| G3 DB pool wait p95 | **0.73ms**（空闲池稳态） | ≤20ms |
| G2 traversal | 33,336 items，lost/dup=**0** | =0 |
| 响应大小 | 逐请求 ≤200KiB 断言 | ≤200KiB |
| RSS | 175,004 KB | 报告 |

| 阶段 | p50 | p95 | p99 |
|---|---:|---:|---:|
| 深页 keyset | 70.8ms | 77.2ms | 220.0ms |
| 32 并发 | 409.6ms | 518.1ms | 705.3ms |
| pool wait | — | 0.73ms | — |

perf JSON SHA-256：`9baac0cd1920bd3ad354c290398a564203c33a01a05d9d453a014d30a6435846`

## 5. 关键证据

- **RBAC**：401/403/单域授权/跨域拒绝/超管通过；AI artifact 双权限
  （v2:artifact:read + v2:ai:artifact）；migration 不隐式授权（无 role_menus 绑定）。
- **Keyset**：并发插入期间完整翻页 lost/dup=0、所有页 as_of/filter_hash 全等；cursor
  tamper/endpoint/filter/direction mismatch/超长 → 400；limit 不进 cursor 身份。
- **查询链**：market/episode/decision/execution-trace/AI/release/integrity 链 ID 全等；
  摘要投影无 raw prompt/response/body/book levels。
- **Artifact Range**：单段 206/Content-Range/ETag；无 Range/多 Range/越界/超 1MiB → 416。
- **Secret/no-egress**：源文件/offline SQL/API 响应 secret marker=0；Repository 无
  httpx/redis/network；authorized_capital=0。
- **Frontend**：AbortSignal 到达 axios、query key 隔离、取消不污染 cache。

## 6. 未解决 blocker

无 P0/P1。Strategy Config 的 66-field schema 未冻结（任务 §1 明确不补写未定义字段）；
config/release 的 draft/publish 等 mutation 语义未形成可执行合同，本 WP 只读。WP-07B
视觉/页面被任务 §13 门禁为 `BLOCKED_PRODUCT_VISUAL_DECISION`（需先展示色板/字体/圆角
token 与一张 Episode Detail 高保真预览）。

## 7. 非目标

不创建任何 `.vue` 业务页面/路由/视觉 token；不实现 config draft/publish、release
rollback、kill、label adjudicate、replay create/cancel；不补写 66-field schema；不执行
交易/回放/AI/rebuild/链操作；不改 V1；不建第二套事实表/账本/缓存事实源。

## 8. 回滚

先撤销 V2 role bindings（存在绑定时 0070 downgrade 拒绝）；`alembic downgrade b1000052`
删除 0070 索引与权限 seed，不修改 trading facts；Controller package / frontend API/query
可独立 revert；cursor 无状态 token 代码回滚后自然失效。

COMPLETION_MANIFEST_SHA256: ac777dd365b78736988069676a53bd1f0a9c5e3f4374ca6a043d1081bd9f8130
