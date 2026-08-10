# Polymarket V2 逐文件实施合同

> 状态：施工入口。更新时间：2026-08-08。目标执行模型：DeepSeek V4 Flash。
> 本文把架构约束落到具体文件；它不修改业务目标，也不允许一次性生成整个系统。

## 1. 使用方式

每次只给实现模型一个 `WP-*` 工作包，并附上：允许修改的文件、上游 manifest、输入 fixture、
验收命令。模型不得顺手重构 Base、增加交易路线、读取 V1 代码或修改工作包外文件。

规范优先级：

```text
ARCHITECTURE.md
→ polymarket-v2-platform-design.md
→ polymarket-integration-design.md
→ ai-observability-replay-design.md
→ performance-cache-database-design.md
→ 本实施合同
```

发现冲突时停止受影响文件并记录 `PRODUCT_DECISION_REQUIRED`；不自行选择语义。一个工作包只有
在测试、迁移、数据约束和 manifest 均通过后才完成。

## 2. 依赖方向

```text
schemas ← drivers
models ← repositories ← logic ← controllers/handlers
                       ↑
             drivers/services

runtime → handlers/logic；runtime 不写业务 SQL
admin view → query hook → api module；view 不直接调用 request.ts
```

禁止反向依赖：Model 不导入 Logic；Driver 不导入 Repository；Repository 不调用外部 API；
Controller 不计算概率、edge、仓位或状态转换；Vue 页面不重算后端财务/风险结论。

### 2.1 规范命名

以下物理命名唯一有效；其他文档中的近义词只表示概念，不得再建第二套表/目录：

```text
PostgreSQL schema: trading
Python domain package: app/{models,repositories,logics,schemas}/trading
Provider wire package: app/schemas/polymarket + app/services/polymarket

decision: trade_decisions
action intent: economic_action_intents
book: pm_book_checkpoints + pm_book_levels
quote: pm_quote_bindings
ledger: ledger_transactions + ledger_postings
outbox: transactional_outbox + outbox_delivery_history + job_completions
```

禁止创建 `decision_records、trade_intents、pm_book_snapshots、cash_ledger、ledger_entries、
outbox_pending` 等平行物理表。

## 3. 后端基础文件

下表的测试文件名均位于 `serve/tests/trading/` 的对应子目录；不得放进 legacy Base tests。

| 文件 | 唯一职责 | 明确禁止 | 对应测试 |
|---|---|---|---|
| `app/config.py` | 静态基础设施 typed env；各进程 pool/Redis/存储/OTel 参数 | strategy、permission、secret 明文、运行中 latest 配置 | `test_v2_config.py` |
| `.env.example` | 基础设施变量与 `SECRET_REF` 示例 | 真实 key、策略 JSON | `test_v2_config.py` |
| `requirements.txt` | 锁定运行时所需 DB/HTTP/WS/crypto/OTel 依赖范围 | 未使用 SDK、开发工具 | dependency smoke |
| `requirements-dev.txt` | pytest、contract/perf/lint 工具 | 运行时依赖 | dependency smoke |
| `app/services/database.py` | engine/session factory、进程 profile、timeout、startup/dispose | 业务查询、内部 commit、全进程统一 20+10 | `test_v2_database_profiles.py` |
| `app/services/redis_control.py` | control Redis 连接、Stream/lease 原语 | 业务事实、可淘汰 cache | `test_v2_redis_control.py` |
| `app/services/redis_cache.py` | disposable versioned cache、CAS/pipeline | queue、kill 权威、secret、资金状态 | `test_v2_redis_cache.py` |
| `app/services/artifact_store/service.py` | content-addressed put/get/range、hash/size/MIME | 判断 artifact 业务有效性 | `test_v2_artifact_store.py` |
| `app/services/artifact_store/contracts.py` | `ArtifactRef/ArtifactDriver` Protocol 与统一错误 | 选择 retention/Gate | `test_v2_artifact_driver_contract.py` |
| `app/services/artifact_store/drivers/local.py` | 开发/测试原子落盘、safe path | 生产共享存储假设 | `test_v2_artifact_local.py` |
| `app/services/artifact_store/drivers/s3.py` | S3 条件写（`IfNoneMatch=*`）+SHA256 checksum、put/get/head/range、pm-* 元数据完整性、有界读取、传输未知 HEAD reconcile | multipart/streaming、delete/retention、业务判断、content hash 判断 | `test_v2_artifact_s3.py` |
| `app/services/vault/service.py` | 以 secret ref/version 做授权解密、轮换与审计 | 明文投影到 settings/Redis/log/API | `test_v2_vault.py` |
| `app/services/vault/envelope.py` | KMS/env keyring、AES-GCM、identity-bound AAD | 业务判断、保存 master key | `test_v2_vault_crypto.py` |
| `app/db/uow.py` | 外层事务、commit/rollback、after-commit hooks | 外部网络请求、业务 Gate | `test_v2_uow.py` |
| `app/db/bulk.py` | asyncpg COPY、压缩 event batch 写入 | ORM 逐行 commit、状态转换 | `test_v2_bulk.py` |
| `app/db/cursor.py` | `(sort_time,id,filter_hash,as_of)` cursor 编解码/签名 | offset、任意字段排序 | `test_v2_cursor.py` |
| `app/db/partitioning.py` | UTC 分区边界/命名/存在性检查 | 运行期导入 Alembic DDL | `test_v2_partitioning.py` |
| `app/outbox/contracts.py` | 版本化 envelope、优先级、Handler Protocol | 业务实现 | `test_v2_outbox_contracts.py` |
| `app/outbox/repository.py` | UoW 内 enqueue/claim/dispatched/complete/requeue SQL | 自行 commit、Redis ACK 定案 | `test_v2_outbox_repository.py` |
| `app/outbox/publisher.py` | `FOR UPDATE SKIP LOCKED` claim、Stream publish、visibility | 把 publish 当完成 | `test_v2_outbox_publisher.py` |
| `app/outbox/consumer.py` | owner/lease/续租/fencing、handler dispatch、completion | 无 idempotency 执行 | `test_v2_outbox_consumer.py` |
| `app/outbox/sweeper.py` | 超 visibility 重投、dead job 归档 | 删除未知状态任务 | `test_v2_outbox_recovery.py` |
| `app/observability/logging.py` | JSON 日志、secret/signature redaction、业务 ID 注入 | 保存 prompt/body 明文到日志 | `test_v2_log_redaction.py` |
| `app/observability/metrics.py` | 低基数 Prometheus metrics | business ID label | `test_v2_metric_cardinality.py` |
| `app/observability/tracing.py` | OTel span、trace/chain 关联、采样规则 | 代替业务 event | `test_v2_trace_context.py` |
| `app/models/__init__.py` | 显式导入 `models.trading` metadata | 连接数据库或动态扫描 | `test_v2_model_imports.py` |
| `alembic/env.py` | include schemas/type/default compare、advisory lock、trading metadata；public 仅放行 `alembic_version`，Base 兼容合同由 `v2_0001` validator 检查 | 业务种子、调用旧 SQL runner、反射/接管 Base 表 | `test_v2_alembic_env.py` |
| `app/main.py` | include 一个 Trading Admin router、API lifespan 初始化/释放 | 启动 ingest/AI/execution loop | `test_v2_router_registration.py` |

这些文件只扩展 Base，不改变 legacy SEO、用户、RBAC 和通用 CRUD 行为。

## 4. ORM 与迁移文件

所有新模型位于 `app/models/trading/`，物理表位于独立 PostgreSQL schema `trading`；
`public` 只保留 Base 旧表。`__init__.py` 必须显式导入全部 metadata，供 Alembic 发现。

| 文件 | 拥有的表/对象 |
|---|---|
| `models/trading/artifact.py` | `artifact_objects、artifact_lineage_edges、archive_manifests、retention_manifests` |
| `models/trading/control.py` | `runtime_config_versions、strategy_objective_contracts、strategy_versions、model_role_bindings、execution_spec_versions、capital_permission_manifests、release_manifests、policy_type_scopes、policy_freezes` |
| `models/trading/vault.py` | `secret_vault_entries、secret_vault_versions、secret_access_events` |
| `models/trading/market.py` | `pm_universe_frames、pm_universe_frame_pages、pm_events、pm_markets、pm_market_versions、pm_tokens、pm_token_versions、pm_market_lifecycle_events、pm_market_current` |
| `models/trading/market_stream.py` | `pm_connection_epochs、pm_source_event_batches、pm_source_event_index、pm_book_checkpoints、pm_book_levels、pm_book_current、pm_quote_bindings` |
| `models/trading/semantics.py` | `contract_snapshots、contract_specs、payout_functions、forecast_components、world_schema_versions、forecast_component_versions、forecast_component_contract_specs、portfolio_dependency_edges` |
| `models/trading/cohort.py` | `evaluation_cohorts、universe_memberships、screening_episodes、audit_samples` |
| `models/trading/workflow.py` | `decision_opportunities、decision_opportunity_markets、episode_memberships、forecast_episodes、episode_contract_specs、information_snapshots、information_snapshot_items、gate_decisions` |
| `models/trading/ai.py` | `ai_invocations、ai_tool_calls、ai_validation_results` |
| `models/trading/forecast.py` | `priors、evidence_coverage_policies、evidence_revisions、evidence_bundles、evidence_bundle_items、forecast_input_manifests、forecast_submissions、payout_projections、coherence_checks、forecast_challenges、forecast_leases` |
| `models/trading/decision.py` | `market_relative_decisions、discrepancy_reviews、trade_decisions、action_candidates、action_sets、action_set_legs、resolution_cashflows、underwriting_plans、economic_action_intents` |
| `models/trading/execution.py` | `pm_accounts、pm_balance_allowance_snapshots、account_funds_current、capital_reservations、execution_authorization_envelopes、executions、exchange_order_attempts、exchange_orders、order_state_events、exchange_trades、positions、position_lots、account_reconciliations、execution_leases` |
| `models/trading/ledger.py` | `ledger_transactions、ledger_postings、operating_cost_entries` |
| `models/trading/settlement.py` | `contract_registry、chain_operations、chain_operation_state_history、settlement_observations、resolution_labels` |
| `models/trading/evaluation.py` | `resolution_clusters、resolution_cluster_memberships、score_targets、score_target_memberships、score_observations、metric_runs、experiments、experiment_variants、challenger_variants、error_reviews、ablation_runs、promotion_decisions` |
| `models/trading/audit.py` | `workflow_events、external_call_attempts、replay_runs、alert_events` |
| `models/trading/outbox.py` | `idempotency_claims、transactional_outbox、outbox_delivery_history、job_completions` |
| `models/trading/projection.py` | `ops_health_current、pipeline_funnel_hourly、account_risk_current、provider_cost_daily、latest_chain_summary` |

辅助文件：`constants.py` 只定义 `TRADING_SCHEMA`；`types.py` 只定义 UTC/NUMERIC/hash/
C-collated ID 类型；`mixins.py` 只提供 BIGINT identity、timestamps、optimistic version；
`__init__.py` 导入所有模型。现有 `app/models/__init__.py` 必须再导入 trading package，
`alembic/env.py` 必须开启 `include_schemas/compare_type/compare_server_default`。

模型文件只声明字段、约束、关系和枚举；不做 I/O 或状态变更。金额用 base-unit integer/
`NUMERIC`，时间用 UTC `TIMESTAMPTZ`，事实更正使用 supersede/reversal。

Alembic revision 按以下逻辑拆分，实际文件名前缀由 Alembic 生成：

| Revision 名称 | 内容 |
|---|---|
| `v2_0001_freeze_base_schema_contract` | 冻结 Base legacy schema 兼容合同（只读边界）：EMPTY/COMPATIBLE 不做 Base DDL，partial/incompatible 在 version 前进前抛错回滚；**不**对齐 model/DB 漂移、不建 V2 表 |
| `v2_0002_trading_foundation` | `trading` schema、control、artifact、vault skeleton、idempotency/outbox 基础 |
| `v2_0010_p1a_market_master` | Gamma/CLOB master、frame、version、lifecycle |
| `v2_0011_p1a_evidence_partitions` | source/book 分区根、未来分区、本地索引 |
| `v2_0012_p1a_semantics` | contract、payout、component、world schema |
| `v2_0013_p1a_cohort_episode` | cohort、screening、opportunity、episode、policy freeze |
| `v2_0020_p1b_cognition` | prior、evidence、bundle、manifest、submission、coherence/challenge |
| `v2_0021_p1b_ai_observability` | AI 月分区、tool、validator、lineage |
| `v2_0030_p2_decision_shadow` | market-relative decision、action、intent、underwriting |
| `v2_0031_p2_ledger` | position、shadow execution、双分录账本和 deferred balance trigger |
| `v2_0040_p3_learning` | clusters、labels、metrics、experiment、promotion |
| `v2_0041_read_projections` | 可重建后台/健康 projection |
| `v2_0050_execution_vault_accounts` | envelope vault、account、balance/reservation、leader lease |
| `v2_0051_execution_orders` | authorization envelope、order/trade/reconciliation |
| `v2_0052_chain_settlement` | registry、chain operation、settlement observation |
| `v2_0090_online_indexes` | 存量大表 concurrent indexes；独立 autocommit 段 |

每个 revision 必须支持空库升级、已有 Base 库升级、重复检查、`upgrade→downgrade→upgrade`；
分区表不得声明不含分区键的“全局唯一”，全局唯一走 `idempotency_claims`。

### 4.1 数据库硬约束

以下不变量必须由 PostgreSQL `UNIQUE/CHECK/FK/partial index/deferred trigger` 或同一 UoW
内的原子封账函数保证，不得只写在 Prompt、Pydantic 或 Logic 中：

- `condition_id、token_id` 按 chain 唯一；contract spec 的 outcome/token 映射唯一且完整。
- component version 必须引用唯一 world schema；episode 的 contract spec 集合必须与该 component
  version 全等；G2 失败的 episode 不得进入 `BLIND_COMMITTED`。
- blind submission 提交后禁止 `UPDATE/DELETE`；evidence、Gate、workflow、order event、trade、label、
  score、promotion 和 ledger 均为 append-only，更正只能 supersede/reversal。
- AI attempt 唯一键为 `episode + stage + role + experiment_variant + attempt_no`；retry/fallback 新增
  attempt 并引用前项，不覆盖失败记录。
- `economic_action_intents.intent_hash`、authorization envelope idempotency key、账户内 external
  order/trade ID 唯一；同一 opportunity/action role 最多一个活动增仓 intent。
- 资金预留使用条件 `UPDATE` 或 `SELECT ... FOR UPDATE`；禁止“先查余额、后写预留”。
- 每笔 ledger transaction 对每种 asset 的 signed base-unit 合计必须为零；封账后不可修改，冲销
  必须写相反 posting。
- 分区表的唯一键必须含分区时间；跨分区幂等统一认领非分区表
  `idempotency_claims(scope,key)`。不设 default partition，缺分区即失败并告警。

对应 PostgreSQL 集成测试必须覆盖：并发写、重复、乱序、失败回滚、append-only、分区边界、
deferred balance 和 `SKIP LOCKED`；SQLite 结果不得作为这些约束的验收证据。

## 5. Polymarket 协议文件

内部 API/event DTO 位于 `app/schemas/trading/`：`control.py、market.py、semantics.py、
workflow.py、ai.py、forecast.py、decision.py、execution.py、settlement.py、evaluation.py、
admin.py`。它们只表达 typed command/query/event，不复用 ORM class，也不实现 Gate。

### 5.1 外部 schema

`app/schemas/polymarket/` 中每个文件只做 Pydantic 解析与规范化，不发请求：

| 文件 | 输入/输出 |
|---|---|
| `common.py` | Decimal、UTC、ID、分页 cursor、provider error 公共类型 |
| `gamma.py` | event/market/keyset；解析 outcomes/prices/token JSON string arrays |
| `clob_public.py` | book/price/tick/fee/CLOB market/time |
| `clob_private.py` | L1/L2 credential、signed order、ack/order/trade/cancel/heartbeat |
| `market_ws.py` | book/price change/tick/new/resolved 判别联合 |
| `user_ws.py` | order/trade 判别联合 |
| `data_api.py` | position/activity/holder 的核对结构 |
| `chain.py` | registry、payout、allowance、receipt、relayer state |

未知字段保留于 `raw_extra`，已知字段类型错误必须 rejected，不能默认成 0/false。

### 5.2 Driver

| 文件 | 唯一职责 | 不得做 |
|---|---|---|
| `services/polymarket/base.py` | timeout/retry/rate-limit/request-attempt 公共框架 | 业务重试决定 |
| `gamma_driver.py` | Gamma keyset/detail/search HTTP | cohort/R0 决策 |
| `clob_public_driver.py` | public book/config/time HTTP | edge/quote 有效性决定 |
| `clob_trading_driver.py` | SDK 封装、L1/L2/order 签名、private REST | 仓位/资金/重试状态机 |
| `market_ws_driver.py` | 连接、订阅、PING/PONG、raw frame 交付 | 维护权威 book |
| `user_ws_driver.py` | auth subscription、PING/PONG、raw private frame | 直接改 order/ledger |
| `data_api_driver.py` | position/activity 核对 HTTP | 充当本地账本 |
| `geoblock_driver.py` | geoblock HTTP 与响应解析 | 绕过或推断权限 |
| `polygon_driver.py` | read RPC、receipt、balance/payout/allowance | 业务 settlement 定案 |
| `relayer_driver.py` | submit/status/nonce 的 wire contract | 盲重发 chain operation |
| `service.py` | 按 release/provider 构造短生命周期 Driver | 模块级有状态 singleton |

每个 Driver 必须有官方 golden fixture、成功/4xx/429/5xx/timeout 测试，并证明敏感 header 已脱敏。

## 6. Repository 与业务 Logic

`app/repositories/trading/` 只拥有 SQL、显式列投影、keyset 查询和 CAS；不得 commit 或调用网络：

```text
artifact.py, control.py, market.py, market_stream.py, semantics.py,
cohort.py, workflow.py, ai.py, forecast.py, decision.py,
execution.py, ledger.py, settlement.py, evaluation.py, projection.py
```

`app/domain/trading/` 是无数据库、无网络、无隐式 clock 的确定性函数：

| 文件 | 纯函数职责 |
|---|---|
| `payout.py` | resolution state → token payout；truth table |
| `probability.py` | Q/U coherence、push-forward、区间与联合约束 |
| `valuation.py` | executable depth walk、fee/cost、robust EV/ROI |
| `portfolio.py` | scenario payout、caps、边际风险/效用 |
| `rounding.py` | tick、size、base units 的官方精度规则 |
| `scoring.py` | proper loss、paired delta、cluster bootstrap 输入 |
| `hashing.py` | canonical serialization 与 artifact/state hash |

这些函数接收显式输入和 policy/clock value，返回可序列化结果；不得读取数据库、Redis、env
或网络。Logic 负责编排它们并生成 reason code。

`app/logics/trading/` 拥有业务规则：

| 文件 | 唯一问题 |
|---|---|
| `release.py` | 草稿能否发布、manifest 是否完整、permission 是否可切换 |
| `universe.py` | frame 是否完整、market version/disposition 如何追加 |
| `market_data.py` | epoch/book/checkpoint/quote binding 是否有效 |
| `contract.py` | contract snapshot 能否编译为完整 payout spec |
| `component.py` | 局部依赖、world schema、projection 是否一致 |
| `screening.py` | R0/R1 candidate/defer/reject 与 reject audit 分配 |
| `evidence.py` | cutoff、来源、三时态、taint 和 snapshot eligibility |
| `forecast.py` | prior/预测/challenge/commit/lease 状态机，不看 quote |
| `decision.py` | 揭价后的全成本 valuation、WAIT/ABSTAIN/action Gate |
| `portfolio.py` | 资金预留、组合 cap、风险降低例外与边际风险 |
| `execution.py` | 两次 preflight、intent/order 状态机和能力检查 |
| `reconciliation.py` | User WS/REST/chain 与本地订单/账本如何收敛 |
| `settlement.py` | payout/label conflict、split/merge/redeem eligibility |
| `evaluation.py` | cohort/split、五层指标、promotion/rollback Gate |
| `replay.py` | frozen artifact 重放、hash/diff；默认无网无执行 |

跨阶段顺序由 `app/orchestrator/trading_state_machine.py` 唯一定义；它只允许合法 transition，
不实现各 Gate 内部算法。每个 Logic 方法接收固定 version/manifest ID，不读取 latest 配置。

## 7. AI 文件

现有 `services/ai.py` 属于 Base/SEO，V2 不复用其 prompt 或隐式 fallback。新文件：

| 文件 | 职责 |
|---|---|
| `app/services/model_gateway/contracts.py` | provider-neutral request/result、usage、tool receipt、网络 capability Protocol |
| `app/services/model_gateway/service.py` | 只按冻结 model binding 构造 Driver；禁止读取 latest 配置 |
| `app/services/model_gateway/registry.py` | provider/model/route allowlist；禁止字符串任意导入 |
| `app/services/model_gateway/drivers/xai.py` | xAI 官方 API 与 Web/X tool wire mapping |
| `app/services/model_gateway/drivers/grok_build.py` | Grok Build 无状态调用与禁网策略映射 |
| `app/services/model_gateway/drivers/packy.py` | Packy OpenAI-compatible 无搜索 relay mapping |
| `app/services/model_gateway/drivers/deepseek.py` | DeepSeek direct/relay wire mapping |
| `app/services/model_gateway/drivers/kimi.py` | Kimi direct/CLI gateway mapping |
| `app/ai_runtime/runner.py` | 先落 invocation→调用→raw artifact→validate→terminal state |
| `app/ai_runtime/tool_runner.py` | allowlist 工具调用和逐次 tool record |
| `app/ai_runtime/validator.py` | JSON Schema + deterministic semantic validator |
| `app/ai_runtime/cache.py` | exact content-addressed success cache；失败不缓存 |
| `app/ai_runtime/redaction.py` | secret/quote taint/PII 检查 |

Prompt 位于 `app/prompts/v2/<role>/v1.md`，输出 schema 位于
`app/prompts/v2/<role>/v1.schema.json`。首版角色目录固定：

```text
r0_semantic, contract_schema, planner_prior, researcher, verifier,
joint_forecaster, blind_challenger, predictor_revision,
discrepancy_critic, label_auditor
```

Prompt 文件不包含模型名、密钥或动态阈值；运行记录必须保存 prompt/schema hash。

## 8. Runtime 与 Handler

| 文件 | 运行内容 | 专属资源 |
|---|---|---|
| `runtimes/trading/market_ingest.py` | Gamma scheduler、Market WS shard、batch/checkpoint | market DB pool、market Stream |
| `runtimes/trading/execution.py` | User WS、heartbeat leader、submit/cancel | execution DB pool、P0/P1 Stream |
| `runtimes/trading/reconciliation.py` | orders/trades/balance/chain recovery | reconciliation pool |
| `runtimes/trading/cognition.py` | screening/research/forecast handlers | AI concurrency/provider bucket |
| `runtimes/trading/evaluation.py` | labels/metrics/promotion | P3 pool |
| `runtimes/trading/replay.py` | hydrate/temp schema/rebuild/diff | replay pool，默认并发 2 |
| `runtimes/trading/outbox.py` | publisher/sweeper/consumer process | control Redis + outbox pool |

`app/handlers/trading/` 固定为 `market.py、cognition.py、decision.py、execution.py、
settlement.py、evaluation.py`；Handler 只解析 event、调用一个 Logic/UoW、返回 completion。
Heartbeat 和 WS receive loop 不通过普通 Job。

维护任务固定为 `app/jobs/performance/partition_manager.py、archive_partitions.py、
hydrate_archive.py`：分别负责预建分区、带 manifest 的归档、只读解冻。它们由 evaluation/replay
runtime 显式注册，不依赖 Base 的非递归任务扫描；不得与 execution 共用 worker、DB pool 或并发池。

## 9. Admin API 文件

`controllers/admin/trading/router.py` 是唯一汇总入口，`main.py` 只 include 该 router。各文件必须
服务端校验对应权限，并调用专用 read Logic：

```text
dashboard.py            v2:dashboard:view
markets.py              v2:markets:view
components.py           v2:components:view
episodes.py             v2:episodes:view
decisions.py            v2:decisions:view
execution.py            v2:execution:view / v2:execution:kill
model_routes.py          v2:models:view
ai_invocations.py        v2:ai:view / v2:ai:artifact
costs.py                 v2:costs:view
strategy_config.py       v2:config:view / draft / publish
releases.py              v2:release:view / publish / rollback
evaluation.py            v2:evaluation:view / label_adjudicate / promote
replay.py                v2:replay:view / create / cancel
integrity.py             v2:integrity:view
artifacts.py             v2:artifact:read
```

列表统一 keyset cursor，不返回大 payload；artifact 单独鉴权并 Range/stream。所有 mutation 都写
operation audit、workflow event 和操作者 ID。

## 10. Admin 前端文件

UI 代码分两步：API/types/query scaffolding 可先做；页面视觉实现必须等用户确认产品色板、
语义色、字体/密度/圆角 token 和一张真实 Episode 详情高保真预览。

### 10.1 数据层

```text
admin/src/api/v2/
  types.ts, cursor.ts, dashboard.ts, markets.ts, components.ts,
  episodes.ts, decisions.ts, execution.ts, models.ts, ai.ts,
  costs.ts, configuration.ts, evaluation.ts, replay.ts, integrity.ts

admin/src/queries/v2/
  queryKeys.ts 及与上述域同名的 useQuery/useMutation 文件
```

API module 只发请求和解析统一响应；query hook 负责缓存、AbortSignal、`as_of/filter_hash`、
失效；View 不直接导入 `request.ts`。

### 10.2 页面与组件

14 个菜单页一一对应：

```text
views/v2/dashboard/index.vue
views/v2/markets/index.vue
views/v2/components/index.vue
views/v2/episodes/index.vue
views/v2/decisions/index.vue
views/v2/execution/index.vue
views/v2/model-routes/index.vue
views/v2/ai-invocations/index.vue
views/v2/costs/index.vue
views/v2/strategy-config/index.vue
views/v2/releases/index.vue
views/v2/evaluation/index.vue
views/v2/audit/index.vue
views/v2/integrity/index.vue
```

5 个隐藏详情页：

```text
views/v2/markets/detail.vue
views/v2/components/detail.vue
views/v2/episodes/detail.vue
views/v2/decisions/execution-detail.vue
views/v2/ai-invocations/detail.vue
```

通用组件限定为：`V2CursorTable、V2StatusBadge、V2FreshnessBadge、V2GateList、
V2EventTimeline、V2ArtifactViewer、V2MetricPanel、V2ConfigDiff`。它们只表现数据，不复制
后端 Gate/财务算法。低容量 config/model route 可复用 SchemaCrudPage；高容量事实页不得使用
CrudTable 的 offset/count 模式。

## 11. 测试文件

```text
serve/tests/trading/unit/             # Logic、schema、Decimal、状态机、cache key
serve/tests/trading/contract/         # 官方 Polymarket/AI golden wire fixture
serve/tests/trading/integration/      # 真 PostgreSQL/Redis/object store、UoW/outbox/partition
serve/tests/trading/replay/           # frozen chain/hash 与断线恢复
serve/tests/trading/execution/        # duplicate/timeout/partial/cancel/heartbeat/ledger
serve/tests/trading/performance/      # 只放性能 harness assertion，数据在 serve/perf
admin/src/**/__tests__/          # query cancellation、cursor、权限与关键交互
```

生产文件必须至少有同域测试；涉及 Gate/状态机时每条 transition 包含成功、拒绝、重复、乱序和
恢复路径。HTTP mock 只能验证协议；数据库约束必须在真实 PostgreSQL 验证。

## 12. 工作包

| WP | 允许范围 | 完成证据 | 依赖 |
|---|---|---|---|
| `WP-00` | 依赖、typed config、DB/Redis/artifact/OTel 骨架 | startup/health、pool budget、secret redaction | 无 |
| `WP-01A` | 0001/0002、control/artifact/outbox Models、UoW/Outbox | migration roundtrip、原子提交、crash recovery | WP-00 |
| `WP-01B` | 0010/0011、Gamma/CLOB public schema/Driver、universe/book ingest | keyset 完整、WS resync/hash、stale hard-stop | WP-01A |
| `WP-01C` | 0012/0013、contract/component/cohort/screening | payout truth table、mapping/coherence、reject audit | WP-01B |
| `WP-02` | 0020/0021、AI invocation/model gateway/evidence/forecast | 每次调用全链、blind taint=0、exact cache、immutable commit | WP-01C |
| `WP-03` | 0030/0031、decision/portfolio/shadow execution/ledger | quote binding、全成本 action、账本平衡、重复 effect=0 | WP-02 |
| `WP-04` | 0040/0041、label/evaluation/replay/promotion/read projections | payout conflict、hash replay、holdout isolation、keyset query | WP-03 |
| `WP-05` | 0050/0051、vault/account/private CLOB/User WS/reconcile | no-plaintext、unknown-submit、heartbeat、资金原子预留 | WP-04 + execution secrets |
| `WP-06` | 0052、Polygon/relayer/settlement | registry code hash、receipt finality、active redeem 唯一 | WP-05 |
| `WP-07A` | Admin API + frontend types/query scaffolding | typecheck、服务端权限、cursor/API contract | 各域对应 WP |
| `WP-07B` | 14 菜单页、5 详情页与交互 | 浏览器/device 验收、无控制台错误 | 用户视觉确认 + WP-07A |
| `WP-08` | 0090、partition/archive、perf harness、alerts、soak | 性能文档全部门槛与 acceptance manifest | 各域对应 WP |

WP-05 之前不需要交易密钥，且不得有真实下单路径。WP-07B 之前只能建设数据层和一张预览，
禁止批量生成页面。

每个表格行是里程碑，不是一次模型调用。为减少交接和重复审查，DeepSeek V4 Flash 按一个完整
里程碑施工，可在同一任务内分 3–5 个内部 checkpoint，通常允许 12–20 个紧密相关生产文件及其
直接测试。checkpoint 只跑定向验证并记录进度，不单独生成 task/manifest；整个里程碑只在集成
验证后生成一份 completion manifest。只有存在独立部署边界、产品决策或无法同一事务验收的领域
边界时才拆任务，不得仅按文件数量拆分。

## 13. DeepSeek 单次任务模板

任务文件统一位于 `serve/docs/tasks/<task-id>-<slug>.md`；当前唯一任务由
`serve/docs/tasks/README.md` 指向。实现者必须把完成证据写入任务文件预先指定的
`serve/docs/manifests/<task-id>-<slug>.md`，不得临时改名。

用户只说“完成”时，审查者必须直接读取当前任务、completion manifest、Git 状态与实际改动，
先复跑最能证明核心不变量的定向证据，再跑里程碑集成/全量回归。发现任务范围内的 P0/P1 时，
审查者直接修改并复验，继续归入同一里程碑，不再制造 `-r1/-r2` 文档链。只有需要产品裁定、外部
状态变化或超出当前允许范围的架构重做时，才记录 `BLOCKED/REMEDIATION_REQUIRED` 并创建独立
任务。通过则记录 `ACCEPTED` 并落地下一里程碑。实现者声明 `DONE` 只表示待审，不等于审查通过。

```text
Work package: WP-XX / 子任务名称
Objective and user value: ...
Required reading: [精确文档章节]
Allowed files: [精确路径]
Forbidden files/non-goals: ...
Inputs/fixtures: ...
Required invariants: ...
Acceptance commands: ...
Expected artifacts: ...

先读取，后实现；不得修改 allowed files 外内容。结束时输出：
1) changed files；2) commands/results；3) invariant evidence；
4) unresolved blockers；5) rollback；6) COMPLETION_MANIFEST path/hash。
```

不得跨越表中不同 WP 的业务依赖链来伪造“快速完成”；允许把同一 WP 内共享一个验收边界的
子任务合并为里程碑。模型失败或上下文不足时保持状态未完成，不允许用 TODO、mock、假数据或
“后续补充”通过验收。

## 14. 风险与回滚

- Migration：expand/backfill/verify/contract；失败使用 Alembic roll-forward/rollback，不手改生产表。
- Ingest：新 projection 可丢弃后从 source batch/checkpoint 重建；不可删除 raw artifact。
- AI：新 prompt/model 是 challenger/version；回滚只切 release，不覆盖 invocation。
- Execution：任何不确定状态先停止增仓和 reconcile；不通过重新部署掩盖未知订单。
- Admin：页面/API 可独立回滚；权限种子必须与后端检查同时发布。
- 性能：缓存、索引和并发调整绑定 release；退化超过门槛回滚，不放宽 correctness Gate。

## 15. 完成定义

整体实现完成必须同时存在：所有 WP completion manifest、全量测试、迁移证据、官方协议 golden
fixture、Shadow/Canary 资格报告、性能 acceptance manifest、真实浏览器 UI 验收、secret/权限
审计和可执行回滚记录。文件存在、接口返回 200 或编译成功均不等于系统完成。
