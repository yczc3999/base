# V2 性能、缓存与数据库设计

> 状态：首版施工规范。本文服务于一条既定交易链，不增加新的交易路线。目标是在保证
> **不丢证据、不重复下单、不使用陈旧状态**的前提下，把行情、重估、执行、查询和回放做快。

## 1. 核心结论

V2 使用现有 PostgreSQL + Redis + 对象存储，不在首版引入 Kafka、ClickHouse 或 TimescaleDB。
性能来自正确分层，而不是把一切塞进缓存：

```text
L0 进程内：订单簿、不可变配置、解析器结果（微秒级）
L1 Redis：跨进程热点投影、合并触发、队列/租约（毫秒级、非事实源）
L2 PostgreSQL：业务事实、当前投影、幂等与资金约束（权威）
L3 对象存储：压缩原始流、网页/AI 大对象、长期回放（低成本）
```

实时路径和认知路径必须分离：

```text
Market WS → 内存 book → 批量持久化 → Redis top-of-book → 合并重估信号
                                                        ↓
有效 forecast lease → 确定性 edge/portfolio 重算 → 候选 action
                                                        ↓
CLOB REST 新鲜 book → 原子资金预留 → 签名/下单 → User WS + REST 对账

研究/AI：独立队列，绝不阻塞 heartbeat、订单管理和降险动作
```

## 2. 初始性能目标

以下是工程验收预算，不是收益假设。计时统一使用 monotonic clock；来源时间、接收时间、
持久化时间分别保存。

| 路径 | p95 | p99 | 失败行为 |
|---|---:|---:|---|
| Market WS 收到 → 内存 book 生效 | 25 ms | 100 ms | token 转 STALE/SYNCING |
| Market WS 收到 → durable batch commit | 100 ms | 250 ms | 先背压，不能静默丢弃 |
| book 变化 → 重估任务可见 | 100 ms | 250 ms | 合并最新版本，不堆积旧任务 |
| 候选 action → REST 新鲜 book 绑定 | 500 ms | 1.5 s | 超时则 WAIT，不沿用旧 quote |
| 本地 preflight/资金预留 | 150 ms | 500 ms | 不通过则不签名 |
| CLOB submit → ack | 2 s | 5 s | 超时进入 SUBMIT_UNKNOWN |
| User WS 收到 → 订单投影更新 | 100 ms | 300 ms | 暂停增仓并 REST 对账 |
| 普通账户断线对账 | 10 s | 30 s | 完成前 execution=RECONCILING |
| Admin 列表/聚合 API | 500 ms | 1 s | 返回降级投影，不扫原始表 |
| 单条完整决策回放 | 5 s | 15 s | 异步生成 artifact |

默认正确性门槛：最终 preflight 的 book age 不超过 `1.5s`、本机与 CLOB 时钟偏差不超过
`500ms`、heartbeat 调度漂移不超过 `500ms`。它们全部配置化，但 Live 只能改得更严格；
放宽必须形成新 release manifest 和 Canary 证据。

容量验收使用两者较大值：**静态基线**或**最近 7 天实测 p99 的 3 倍**。静态基线：

- 10,000 个订阅 token；
- 50,000 个 market、100,000 个 token current projection；
- 2,000 WS frame/s 持续 30 分钟，10,000 frame/s 突发 60 秒；
- 20,000 orderbook level mutation/s；
- 合并后 100 次 component revaluation/s；
- 10 个 execution intent/s、1,000 个同时 live order；
- 1,000 万条 source event/日的数据集上仍满足后台与回放预算。

若实盘规模低于此值也照此压测；若高于则以实测峰值自动抬高测试档位。
稳态 CPU/数据库连接/Redis 内存与磁盘 I/O 不超过 60%/70%/70%/60%，突发期任一资源不超过
80%，数据库与对象上传 spool 始终保留至少 30% 磁盘余量。Worker 稳态消费能力至少为输入
速率 2 倍，P0/P1 积压必须在 5 分钟内排空。

## 3. 工作负载隔离

首版部署为六类独立进程，不能使用一个通用 Worker 混跑：

| 进程 | 任务 | 可否被 AI 阻塞 |
|---|---|---|
| `market-ingest` | Gamma、Market WS、book 状态与批写 | 否 |
| `execution` | preflight、下单、撤单、heartbeat、User WS | 否 |
| `reconciliation` | orders/trades/余额/链上收敛 | 否 |
| `cognition` | research、AI、forecast/challenger | 可以独立排队 |
| `evaluation` | label、metric、回放、归档 | 可以延迟 |
| `api-admin` | 后台读模型、配置发布、人工 kill | 不执行交易 |

优先级固定：

```text
P0: heartbeat / cancel / user event / reconciliation / kill
P1: market lifecycle / fresh-book preflight / execution intent
P2: quote revaluation / contract refresh / cognition
P3: evaluation / replay / export / archive
```

P0/P1 使用独立连接池、队列和并发额度。AI 限流、网页超时、报表导出或大回放不能占用其
event loop、数据库连接或 Redis consumer。

## 4. 行情热路径

### 4.1 单写者与分片

每个 token 在一个 `connection_epoch` 内只由一个 market-ingest shard 维护。使用一致性哈希
分配 token，数据库 `ingest_leases` 保存 owner、lease、fencing token；失去 fencing 的旧进程
不得继续发布投影。

订单簿在 shard 内存中使用 `Decimal/integer ticks` 和有序 price map：

- full `book` 原子替换；
- `price_change` 定位 price level 更新，`size=0` 删除；
- 每次变更生成递增的本地 `book_version`；
- best bid/ask 从有序结构读取，不遍历全簿；
- 非法 tick、负 size、crossed book、未知 epoch 立即失效并 REST 重同步。

不为每个 WS delta 创建 Redis Job，也不把完整 book 每次复制到 PostgreSQL。

### 4.2 持久化策略

为兼顾速度和精确回放，保存三种对象：

1. **原始 delta**：按 25ms 或 500 条先到者压缩成一个 batch，写入
   `pm_source_event_batches`；batch 保存 connection epoch、首尾 seq/time、count、前后 hash 和
   原始 frame bytes，避免为高频流制造一行一事件；
2. **book checkpoint**：保留上游 `book` 全量消息；仅对发生过变化的 token，在归档 segment
   边界、15 分钟或累计 50,000 个 delta 先到时生成一次合成 checkpoint；
3. **决策快照**：每次 action 候选通过前，用 CLOB REST 重新取完整 book，并独立永久保存。

`pm_source_event_index` 只索引 lifecycle、full book、contract change 和被 decision 引用的
material event，指向 `batch_id + ordinal`。checkpoint + 后续 delta 必须可重建任意时点；断线
epoch 之间不拼接。决策、订单和结算记录立即同步提交，不进入行情 micro-batch。

### 4.3 重估合并

高频行情变化只更新 `component_latest_book_version`。每个 component 同时最多一个 revaluation
任务：

```text
SET revalue:pending:{component_id} latest_version NX/merge
worker 取最新版本计算
完成时若版本已前进，只再跑一次最新值
```

中间版本仍保留在事件流，但不做无意义的重复计算。事实/规则/lease 变化不能被合并掉，必须
创建新的 cognition episode；quote/depth 变化只重算确定性决策。

## 5. 缓存设计

### 5.1 两个 Redis 角色

生产环境使用两个 Redis 角色；本地开发可以共享实例但使用不同 namespace：

- `redis-control`：Stream、调度、租约提示、幂等提示、kill 状态；`noeviction`，AOF everysec。
- `redis-cache`：可丢热点投影；`allkeys-lfu`，不承担业务事实。

PostgreSQL outbox 才保证任务不会丢；Redis Control 故障时禁止增加敞口，但订单管理进程继续
撤单/对账。Redis Cache 故障只降低速度，不允许改变业务结论。

### 5.2 缓存矩阵

| 对象 | L0 | Redis | 权威源 | TTL/失效 |
|---|---|---|---|---|
| contract/ID map | 按 hash | version key | PostgreSQL | 新 snapshot/spec 才失效 |
| current book | shard 内完整簿 | top/depth summary | event + checkpoint | `2 × quote TTL` |
| release/config | 按 manifest ID | immutable version | PostgreSQL | 不用 latest 内容覆盖 |
| forecast submission | lease 内 | immutable hash | PostgreSQL/artifact | `valid_until` |
| permission manifest | 按 ID | immutable副本 | PostgreSQL | preflight 同时查动态 kill |
| balance/allowance | 执行进程短缓存 | 仅展示副本 | DB reservation + CLOB/chain | 2s，动作前刷新 |
| geoblock | 执行进程 | 不共享 | 官方 endpoint | 最长 30s/IP 变化立即失效 |
| AI artifact cache | metadata | hot pointer | PostgreSQL + object | content-addressed |
| order/trade/ledger | 不作判定缓存 | 只做通知 | PostgreSQL | 不适用 |

Redis key 必须包含 `env + schema_version + entity + immutable version`，例如：

```text
pm:v2:prod:book:{token_id}
pm:v2:prod:contract:{contract_spec_hash}
pm:v2:prod:release:{release_manifest_id}
pm:v2:prod:forecast:{submission_id}
pm:v2:prod:revalue:pending:{component_id}
```

禁止在热路径调用 `SCAN`/`cache_del_pattern`；发布新 namespace/version 即完成整体失效。
缓存写采用 pipeline/Lua compare-and-set，旧 `book_version/fencing_token` 不得覆盖新值。
交易域不复用 Base 当前“commit 前删缓存 + request session 后台二次删除”的方式；所有缓存
失效都由同事务 outbox 在 commit 后触发，消费者携带实体版本做幂等更新。

### 5.3 禁止缓存

- signer、API secret、passphrase、认证 header、原始签名；
- Base 通用 `settings:all` 或任意“最新配置”缓存不得承载 strategy、capital permission、
  kill switch；每个 Job 必须固定 `release_manifest_id`；
- “允许下单”的最终结论；每次都重新执行 preflight；
- `SUBMIT_UNKNOWN` 的推测结果；
- label、payout、订单和账本的可变副本；
- 未来信息、过期 quote 或已被 supersede 的 forecast；
- 失败/截断/未通过 schema 的 AI 输出。

## 6. PostgreSQL 数据分层

### 6.1 四类表

1. **Master**：market/token/account/config/current projection；小表，不分区。
2. **Evidence stream**：source/workflow/book/API/AI tool events；大表，按时间分区。
3. **Economic truth**：intent/order/trade/position/ledger/permission；中等规模，高约束。
4. **Read models**：Dashboard、chain summary、funnel、成本和健康投影；面向后台查询。

大对象不直接堆进热表：表中保存 typed filter columns、artifact URI、SHA-256、size、MIME；
原始 JSON/网页/AI body/完整 book 进入对象存储。低于 16KiB 的小 payload 可 inline `BYTEA`，
高于阈值只保存引用。JSONB 仅存低频扩展字段，不给 raw payload 建 GIN。

### 6.2 分区与留存

| 表 | PostgreSQL 分区 | 热数据 | 长期去向 |
|---|---|---:|---|
| `pm_source_event_batches/index` | 日 RANGE(`observed_at`) | 14 天 | zstd NDJSON 原文 + Parquet 派生 |
| `pm_book_checkpoints` / `pm_book_levels` | 日 RANGE | 30 天 | 对象存储 |
| `external_call_attempts` | 月 RANGE | 90 天 | 压缩归档 |
| `workflow_events` | 月 RANGE | 1 年 | 永久归档 |
| `ai_invocations/tool_calls` | 月 RANGE | 1 年 | 永久 artifact |
| `metric_observations` | 月 RANGE | 2 年 | 永久汇总/可重算原始引用 |

contract、forecast、decision、intent、order、trade、ledger、label、promotion 及所有被决策引用的
book/evidence/AI artifact 永久保留。非决策公共行情在对象存储中默认永久保留，90 天后转冷；
任何删除必须是单独的产品决议并生成 retention manifest，不允许定时任务直接 `DELETE` 大表。
归档流程为：封区 → 导出 → hash/行数核对 → manifest committed → detach/drop 热分区。

原始精确回放使用 zstd NDJSON；Parquet 只是分析派生，不能替代原文。归档对象启用内容 hash、
版本化和生命周期策略。

PostgreSQL 分区表的 PK/UNIQUE 必须包含分区键，因此全局幂等不能错误地声明在日分区事件表上。
另设不分区的小表 `idempotency_claims(scope, key, owner_id, created_at)` 提供全局唯一声明；
Outbox 使用小型 `transactional_outbox` 热表，完成后搬入按月分区的
`outbox_delivery_history`。

### 6.3 类型

- 内部高频主键：`BIGINT GENERATED ... AS IDENTITY`；外部 chain ID 另存 UUID/ULID。
- `token_id`、`condition_id`、order/trade hash：`TEXT COLLATE "C"`，禁止数值或 float。
- pUSD/shares：6 位 base unit 使用 `BIGINT/NUMERIC(38,0)`；价格使用定标 Decimal，禁止 float。
- 时间：`TIMESTAMPTZ`；上游毫秒时间另存 `BIGINT`，不可混用。
- 状态和 reason code 使用受控 enum/check；原始 provider 值另存，避免信息丢失。

事实表的应用角色禁止 `UPDATE/DELETE`，更正使用 reversal/supersede；外键默认 `RESTRICT`，
只有纯 junction/current projection 可 `CASCADE`。账本采用
`ledger_transactions + ledger_postings`，
每笔 transaction 的借贷/资产变化由 deferred constraint trigger 或封账函数验证平衡。

## 7. 索引设计

只为真实访问路径建索引，所有外键列有 btree；低选择性布尔值使用 partial index，不单独索引。

```sql
-- 当前可交易市场/R0 扫描
CREATE INDEX ON pm_market_current (end_at, gamma_market_id)
WHERE active AND NOT closed AND accepting_orders AND enable_order_book;

-- token/condition 映射
CREATE UNIQUE INDEX ON pm_tokens (chain_id, token_id);
CREATE UNIQUE INDEX ON pm_markets (chain_id, condition_id);

-- 时间流：每个分区本地索引
CREATE INDEX ON pm_source_event_batches (connection_epoch_id, first_ingest_seq);
CREATE INDEX ON pm_source_event_batches USING BRIN (observed_at);
CREATE INDEX ON pm_source_event_index (token_id, observed_at DESC, batch_id, ordinal);
CREATE INDEX ON pm_source_event_index (condition_id, event_type, observed_at DESC);

-- 最新有效 book
CREATE INDEX ON pm_book_checkpoints (token_id, observed_at DESC)
WHERE validity = 'VALID';

-- 待投递 outbox
CREATE INDEX ON transactional_outbox (available_at, id)
WHERE status = 'PENDING';

-- 活跃订单与未知提交
CREATE INDEX ON exchange_orders (account_id, condition_id, updated_at DESC)
WHERE state IN ('SUBMITTING','SUBMIT_UNKNOWN','LIVE','DELAYED','CANCELLING');

-- 订单/成交时间线
CREATE INDEX ON order_state_events (exchange_order_id, occurred_at, id);
CREATE INDEX ON exchange_trades (account_id, match_at DESC, id DESC);

-- AI/决策下钻
CREATE INDEX ON ai_invocations (forecast_episode_id, role, attempt_no);
CREATE INDEX ON trade_decisions (component_id, decided_at DESC, id DESC);
```

每个 migration 附带样本规模下的 `EXPLAIN (ANALYZE, BUFFERS)`；无使用证据的索引不进入生产。
并发建立大表索引使用 `CREATE INDEX CONCURRENTLY` 的独立迁移阶段及失败清理，不能伪装成普通
事务内 DDL。

## 8. 写入、事务与并发

### 8.1 批写

- 公共行情使用 `asyncpg COPY`/prepared batch，每批 100–1,000 行，上限等待 25ms；
- order/user/ledger/permission/config 逐事件同步 commit；
- 同一 row 的状态变化使用 compare-and-swap `WHERE version=:expected`；
- 外部网络调用绝不放在数据库事务内。

订单提交采用三段式：

```text
TX1: preflight + 原子资金预留 + 保存 signed body/order hash + outbox → COMMIT
HTTP: 发送 exact body
TX2: 保存 ack 或 SUBMIT_UNKNOWN + 状态事件 + outbox → COMMIT
之后: User WS/REST/chain reconciliation 追加事实，不覆盖历史
```

资金预留使用单条条件更新或 `SELECT ... FOR UPDATE`，保证
`available - reserved >= requested`；依赖应用层先查再写不成立。每个 intent、external order、
trade 和 ledger transaction 都有唯一约束，at-least-once 消息只能产生一次经济效果。

### 8.2 Outbox 与队列

业务事务同时写 `transactional_outbox`。Publisher 用：

```sql
SELECT ... FROM transactional_outbox
WHERE status='PENDING' AND available_at<=now()
ORDER BY available_at,id
FOR UPDATE SKIP LOCKED
LIMIT :batch;
```

发布到 Redis Streams 后记录 `DISPATCHED + visibility_deadline`；消费者处理完成后用
idempotency key 在 PostgreSQL 写 `job_completion`，Outbox 才进入 `COMPLETED`。超过 visibility
deadline 仍无 completion 的消息重新发布，确保 Redis AOF 丢失、consumer crash 或 ACK 丢失时
仍可恢复。Redis ACK 不是业务完成证据。

Base 当前单一 `BRPOPLPUSH` 队列只保留给低风险导出/通知；交易 P0/P1 必须使用上述 outbox +
分优先级 Stream/leased job。Heartbeat 和 User WS 是 execution 进程内独立 monotonic task，
绝不排普通队列。

现有 Worker 的无 owner/lease `processing` 回捞、非原子 delayed claim、固定并发和只消费
`default` 队列均不能沿用到交易域；新 consumer 必须有 owner、visibility lease、续租、fencing、
deadline、job timeout、release manifest 和 completion record。

### 8.3 连接池

连接预算按整个部署计算：

```text
Σ(replica_count × (pool_size + max_overflow))
≤ PostgreSQL max_connections - 20 管理/迁移保留连接
```

首版建议每实例：API `5+2`、market-ingest `8+2`、execution `5+1`、reconciliation `5+1`、
cognition `3+2`、evaluation `3+1`，根据压测调整，而不是沿用 Base 每进程 `20+10`。

SQLAlchemy 增加 `pool_pre_ping、pool_timeout=3s、pool_recycle=1800s、application_name`。
数据库角色分别设置：API statement timeout 2s、热 worker 5s、batch/replay 30s；
`lock_timeout=1s`、`idle_in_transaction_session_timeout=5s`。生产可用 PgBouncer transaction
pool，但必须通过 asyncpg prepared-statement 兼容测试后启用。

## 9. 查询与后台性能

Base `BaseLogic.get_list()` 的 offset + `COUNT(*)` 只用于低容量配置/字典表。以下页面必须使用
专用 Logic + keyset cursor：Markets、Events、Episodes、AI Invocations、Decisions、Orders、
Trades、Audit Timeline、Source Events。

交易写入也不复用内部自行 `commit()` 的通用 CRUD；由外层 Unit of Work 唯一控制事务边界，
Logic 只 flush/返回。这样 intent、资金预留、workflow event 和 outbox 才能原子提交。

Cursor 固定为稳定复合键，例如 `(observed_at,id)`；查询条件与排序字段必须 allowlist，并有匹配
索引。列表默认不加载 raw payload、完整 prompt/response、book levels 或大 JSON。

Dashboard 不临时扫事实表，读取持续维护的 projection：

- `ops_health_current`
- `pipeline_funnel_hourly`
- `account_risk_current`
- `provider_cost_daily`
- `latest_chain_summary`

projection 由事件消费者幂等更新，可从事实重建。总数允许异步/近似展示并标记 as-of；资金、
订单、权限和账本数字必须从权威表精确查询。导出和大型回放走 P3 队列并写对象存储，不能占用
API request transaction。

复杂后台页使用 TanStack Query：query key 包含 `cursor + filter_hash + as_of`，翻页保留上一页，
AbortSignal 取消旧请求，搜索 300ms debounce；时间线超过 500 行虚拟滚动。列表响应目标
`≤200KiB`，raw payload/book/prompt 详情按需分块或 Range 下载。

当 PostgreSQL 持续 CPU>70%、buffer hit<99%、后台查询 p95 超预算且索引/投影已优化后，才为
Admin/评价增加只读副本；execution 永远读 primary。

## 10. 背压与降级

每个队列维护 `depth、oldest_age、in_rate、out_rate、retry_rate`。超过 high watermark：

1. 合并同 component 的旧 quote revaluation，只保留最新版本；
2. 暂停 deep research、新 replay、归档和导出；
3. R0 降为 metadata-only，候选 defer 而非静默 reject；
4. quote freshness 无法保证时停止新 decision/intents；
5. P0 cancel/reconcile/heartbeat 保留独占资源。

永不丢弃：contract/rules 变化、market resolved、User WS order/trade、order ack、资金/账本、
permission/kill、label/payout。可以合并但不能伪造：公共 quote 重估通知。可以暂停：AI、报表、
回放、导出。

组件级 circuit breaker 不能扩大风险：provider 故障时只允许 WAIT/ABSTAIN/REDUCE/CLOSE/CANCEL。

## 11. 故障语义

| 故障 | 系统行为 |
|---|---|
| Redis Cache 不可用 | 从 L0/PostgreSQL 恢复；性能下降，不改变结果 |
| Redis Control 不可用 | 暂停增仓；继续订单管理并由 outbox 保留任务 |
| PostgreSQL 不可用 | 立即停止新单；尝试 cancel-all，随后停止 dead-man heartbeat；恢复后全量对账 |
| Object Storage 不可用 | 阻止需要新大 artifact 的 AI/decision；订单管理继续 |
| Market WS 落后 | token=STALE；REST 重同步；旧 quote 不可交易 |
| User WS 落后 | execution=RECONCILING；REST orders/trades 定案 |
| cognition 堵塞 | 已有效 forecast 可继续 reprice；新事实触发的机会 WAIT |
| Evaluation 堵塞 | 不影响订单管理，但禁止 promotion |

本地磁盘只允许有界、加密的临时 spool；它不是事实源。spool 达上限时先停公共数据消费和新
认知，不能挤占 execution 日志或让进程 OOM。

## 12. 性能观测与告警

落地 OpenTelemetry Collector + Prometheus + Grafana；结构化日志进入现有日志后端。业务事件
仍由 PostgreSQL/artifact 承担，Trace/metrics 不代替证据。正常技术 Trace 采样 1%–10%；
execution、ledger、reconciliation、错误和慢调用 100% 保留。Prometheus 标签只使用
stage/provider/result/version 等低基数字段，业务 ID 只进日志和 Trace。

核心指标：

```text
pm_ingest_events_total / pm_ingest_batch_commit_seconds
pm_book_apply_seconds / pm_quote_age_seconds / pm_ws_resync_seconds
pm_outbox_oldest_age_seconds / pm_queue_oldest_age_seconds
pm_db_pool_wait_seconds / pm_db_deadlocks_total / pm_db_rows_scanned
pm_redis_command_seconds / pm_cache_hit_ratio / pm_redis_evictions_total
pm_revalue_coalesced_total / pm_decision_seconds
pm_order_ack_seconds / pm_submit_unknown_total / pm_reconciliation_diff
pm_ai_duration_seconds / pm_ai_tokens_total / pm_ai_cost_cny_total
pm_admin_query_seconds / pm_admin_response_bytes
pm_replay_events_per_second / pm_archive_backlog_partitions
```

可用性按滚动 28 天计算：Admin/API 99.9%，ingest/execution 内部可用性 99.95%。Provider
端到端延迟与本地内部延迟分开报告，不能通过排除慢请求粉饰 SLO。

| 级别 | 触发 | 自动动作 |
|---|---|---|
| P0 | heartbeat age>10s、leader fencing 冲突、账本差异或任一硬完整性指标>0 | 停止增仓、撤单/对账、退 Shadow |
| P1 | quote age>TTL 30s、WS 恢复>60s、P0/P1 oldest job>2s、outbox>5s | 失效相关 token、暂停 execution |
| P1 | DB pool wait p95>50ms、磁盘>85%、Redis eviction>0 | 限流 Admin/replay，保留 P0/P1 |
| P2 | Admin p95>预算 15m、归档落后>24h、未来分区不足 48h | 阻止归档/发布并创建工单 |

告警使用 5m/1h 快烧与 30m/6h 慢烧窗口，避免瞬时抖动；P0 不等待 burn-rate。
技术日志热留 30 天，正常 Trace 7 天、错误/慢 Trace 30 天；Metrics 保存 15 秒粒度 15 天、
1 分钟粒度 90 天、1 小时粒度 2 年。业务事实按第 6.2 节留存。

## 13. 数据库维护

- V2 schema 迁移只走 Alembic；`app/migrate.py`/SQL 文件只保留 Base 旧基线与菜单种子，不再
  承担交易 DDL。首个 revision 先对齐 model↔DB 的类型、默认值、CHECK 和索引；Alembic 开启
  `include_schemas、compare_type、compare_server_default`，迁移进程使用 advisory lock。
- 所有 V2 表位于 PostgreSQL schema `trading`；Base 旧表继续位于 `public`。应用角色和
  Alembic 必须显式设置 schema/search path，禁止同名表依赖隐式搜索顺序。
- 在线变更采用 `expand → backfill → verify → contract`；大索引 `CONCURRENTLY` 单独阶段执行，
  每步有 precondition、锁时长预算、重跑判断和 roll-forward/rollback 方案。
- 每日提前创建未来 7 天分区；缺分区时写入失败并告警，不落 default partition 无限膨胀；
- hot partition 单独调高 autovacuum/analyze 频率，监控 dead tuples、freeze age、WAL 和复制延迟；
- 批量状态更新避免逐行 ORM flush；删除通过 detach/drop partition；
- 每周采集 `pg_stat_statements` Top SQL，慢查询必须定位到 endpoint/job/release；
- 备份使用 Base 的 PostgreSQL 备份能力，但增加每日 restore drill、WAL/PITR 和 artifact manifest
  校验；只有“成功恢复并核对 hash”才算备份有效；
- migration 必须有 precondition、事务边界、锁时长预算、幂等检查和 rollback/roll-forward 方案。

## 14. 性能配置

性能配置与策略 66 项分离，属于基础设施/运行配置；任务仍绑定 immutable release manifest：

| 分区 | 配置项 |
|---|---|
| DB Pool | 各进程 pool/overflow、timeout、recycle、statement/lock timeout |
| Batch | source batch rows、max wait、checkpoint interval、segment size |
| Redis | control/cache URL、memory policy、socket pool、command timeout |
| Queue | 每级并发、high/critical watermark、visibility/lease、retry/backoff |
| Book | shard 数、subscription batch、quote TTL、stale、REST refresh、depth |
| Revalue | debounce、每 component pending 上限、全局并发 |
| AI | role concurrency、token/cost budget、provider rate bucket、cache policy |
| Storage | inline threshold、compression、segment duration、retention |
| Query | page limit、cursor lifetime、API timeout、export/replay concurrency |
| SLO | 各阶段 p95/p99、告警窗口、自动降级/恢复阈值 |

配置发布前做交叉校验，例如 `book_cache_ttl >= 2×quote_ttl`、P0 DB pool 不得为 0、总连接预算
不得超限、archive 未验证时不得 drop partition。

## 15. 压测与验收证据

### 15.1 固定测试

1. **Market replay**：真实封存 WS 流以 1×/3×/10×回放，校验 batch hash chain、最终 book
   hash、延迟和零丢失。
2. **断线风暴**：随机断 WS、乱序本地任务、重复消息，验证 epoch 重同步和幂等。
3. **数据库负载**：在 1,000 万 source event、100 万 AI/tool event、10 万 decision/order 数据上
   跑固定 `EXPLAIN ANALYZE` 与 API benchmark。
4. **队列饱和**：压满 P2/P3，证明 P0 heartbeat/cancel/reconcile SLO 不退化。
5. **未知提交**：在 socket write/response/read 各点断网，证明只产生一个经济订单。
6. **缓存失效**：kill 两类 Redis，证明无陈旧权限、quote 或订单状态被当权威。
7. **数据库故障**：证明停止新单、撤单/dead-man 生效，恢复后账本收敛。
8. **归档恢复**：从 checkpoint + delta 重建决策 book，hash 与原决策绑定一致。
9. **后台查询**：真实浏览器并发下列表、下钻和 dashboard 满足预算，无深 offset/全表 count。

### 15.2 完成门槛

- 所有第 2 节 p95/p99 在目标容量下通过；
- DB-only preflight p99≤50ms、outbox claim lag p99≤250ms、DB pool wait p95≤20ms；
- `lost_event、duplicate_economic_effect、stale_quote_trade、terminal_market_trade=0`；
- 订单、成交、仓位和账本 reconciliation 差异为 0；
- 任一 cache 清空后可从 PostgreSQL/artifact 重建相同投影 hash；
- 关键 SQL 无全表扫描、临时磁盘排序或未命中索引；
- 连接总预算、Redis memory、DB/WAL、对象存储增长有 30/90/365 天容量预测；
- 生成不可变 `PERFORMANCE_ACCEPTANCE_MANIFEST`，包含数据集、命令、配置、release、结果、
  flamegraph/SQL plan/指标 artifact hash 和所有未通过项。

## 16. 文件落点

```text
serve/app/
├── config.py                         # pool/batch/queue/cache/SLO typed settings
├── services/
│   ├── database.py                  # 分进程 engine profile、timeout、dispose
│   ├── redis_control.py             # Stream/lease/fencing；noeviction
│   ├── redis_cache.py               # 可淘汰热点缓存
│   ├── artifact_store/              # service/contracts/drivers；content-addressed artifact
│   └── vault/                       # envelope encryption；execution-only decrypt
├── db/
│   ├── uow.py                       # 外层事务边界
│   ├── bulk.py                      # COPY/micro-batch
│   └── repositories/                # 专用 keyset/read/write repository
├── outbox/                          # publisher、leased consumer、completion sweeper
├── projections/                     # book/current/dashboard/read models
├── handlers/trading/                # outbox event handler
├── runtimes/trading/                # 独立 ingest/execution/replay 进程
├── jobs/performance/                # 仅低优先级 archive/hydrate wrapper
└── observability/                   # OTel、metrics、SLO/health

serve/perf/
├── datasets/                        # 固定 seed 与封存流
├── ingest/ api/ replay/ sql/
└── results/                         # 基准 manifest，不提交大结果本体

ops/observability/
├── dashboards/
└── alerts/
```

V2 DDL 放入 `serve/alembic/versions/`；旧 SQL migration runner 不再接收交易表。

## 17. 首版明确不做

- 不因“以后可能很大”先引入 Kafka、ClickHouse、TimescaleDB 或多区域写入；
- 不用 Redis 取代订单、资金、权限或审计事实；
- 不为每个行情 tick 创建普通 Queue Job 或完整数据库 book 副本；
- 不让后台通用 CRUD 的 offset/count 查询直接访问高容量事实表；
- 不以平均延迟掩盖 p99、背压、丢事件或陈旧报价；
- 不为了速度削弱 preflight、幂等、账本事务和可回放证据。
