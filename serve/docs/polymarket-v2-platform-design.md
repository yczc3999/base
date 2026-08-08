# Polymarket V2 平台实现设计

> 状态：首版实现设计。业务逻辑以 `/code/pollymarket/docs/v2/ARCHITECTURE.md`
> 为准；本文只规定它如何落到 Base Platform，并重点保证**充分可配置、可观察、可回溯**。
> Polymarket 协议、订单、对账与结算细节见
> [`polymarket-integration-design.md`](polymarket-integration-design.md)。
> 热路径、缓存、数据库、容量与压测细节见
> [`performance-cache-database-design.md`](performance-cache-database-design.md)。
> 具体文件职责和施工顺序见
> [`v2-implementation-contract.md`](v2-implementation-contract.md)。

## 1. 设计结论

V2 不做黑盒定时脚本，而是由四个平面组成：

```text
配置平面：版本化策略、模型路由、成本、风控和资金权限
执行平面：市场感知 → 盲态认知 → 揭价估值 → 组合 → Shadow/Canary/Live
证据平面：不可变事件、快照、模型调用、订单、账本和标签
观察平面：指标、日志、Trace、告警、审计和确定性回放
```

PostgreSQL 是业务事实源；Redis 只承担队列、锁、缓存和短期心跳，不能承担审计事实。
Base 的 `Controller → Logic → Model/DB` 与 `Service + Driver` 保持不变。

## 2. 进程与模块

```text
Gamma REST ─┐
CLOB REST ──┼→ market-data Service/Driver → source events + quote store
CLOB WS ────┘                                  ↓
                                      Orchestrator + Outbox
                                                ↓
                                  cognition / decision workers
                                                ↓
                         shadow driver | canary/live execution driver
                                                ↓
                              orders/fills/ledger/labels/metrics

Admin API ← read projections + config release + audit/replay
```

进程职责：

- **API/Admin**：查询、配置发布、人工停机和只读回放；Controller 不编排交易。
- **Market ingest**：REST 管全量和回补，WS 管实时变化；原始 source sequence 可追踪。
- **Orchestrator/Worker**：按状态机推进 Gate；每个 Job 幂等、可断点续跑。
- **Execution worker**：独立进程和独立资金权限；Shadow 永远没有真实下单能力。
- **Metrics worker**：标签审计、指标和成本归因，不修改历史预测。

外部系统全部置于 Driver 后：`gamma`、`clob_rest`、`clob_ws`、`xai`、
`deepseek`、`gemini`、`kimi`、`packy`、`shadow_execution`、`polymarket_execution`。

## 3. 配置设计

### 3.1 三类配置严格分离

1. **基础设施配置**：数据库、Redis、KMS、静态服务凭据，只来自服务端 secret/env。
2. **策略配置**：R0/R1、模型角色、prompt、证据、收缩、quote TTL、edge、成本和风险；
   使用不可变版本。
3. **资金权限**：`shadow/canary/live`、授权资本、类别/component/global cap、kill switch；
   与策略配置分离，晋级不能顺便换预测逻辑。

API key、Token、Cookie 不进入通用 `settings`、日志、事件或配置 JSON。配置只保存
`secret_ref + secret_version`，凭据由 envelope-encrypted vault/KMS 解密给对应 Driver。

### 3.2 发布模型

```text
config draft
→ schema 校验
→ 语义与依赖校验
→ 显示 diff
→ operator 发布
→ immutable config version
→ release manifest 激活
```

核心对象：

- `runtime_config_versions`：canonical JSON、schema version、hash、状态、创建人；
- `strategy_versions`：目标、R0/R1、forecast、shrinkage、估值和风险政策 FK；
- `model_role_bindings`：role、provider、route、exact model、effort、tool/network policy、
  prompt/schema、timeout、预算与 fallback 行为；
- `capital_permission_manifests`：mode、授权资本、capability、limits、kill switch；
- `release_manifests`：git SHA、image digest、DB revision、上述版本和总 hash。

每个任务入队时就固定 `release_manifest_id`；Worker 禁止运行中读取 “latest config”。
修改已发布配置会创建新版本。回滚是重新激活旧内容形成一条新发布记录，不覆盖历史。

### 3.3 首版配置数量

首版固定为 **10 个策略配置分区、66 个 typed fields**，另有 **6 条模型角色绑定**
和 **1 份资金权限 manifest**。不做任意 key/value 配置；每个字段都有类型、范围、默认值、
变更影响和校验规则。

| 分区 | 字段数 | 字段范围 |
|---|---:|---|
| Sensing | 6 | REST 周期、WS 开关/重连、回补窗口、quote TTL、orderbook 深度 |
| R0 | 8 | volume、spread、depth、最短/最长到期、类别、batch、reject audit rate |
| Contract/Component | 5 | rules refresh、component 上限、歧义行为、人工复核、重建触发器 |
| Research/VoI | 7 | shallow/standard/deep 预算、search 次数、时延、coverage、停止阈值 |
| Evidence | 6 | source allowlist、禁用类型、新鲜度、冲突行为、odds taint、验真门槛 |
| Forecast | 6 | prior、uncertainty、lease、invalidation、shrinkage、projection tolerance |
| Decision/Cost | 8 | robust edge、安全边际、fee/slippage/capital cost、最小容量、动作、持有范式 |
| Portfolio/Risk | 7 | reference capital、单笔/component/global cap、日损、回撤、现金保留 |
| Evaluation | 7 | horizon、cohort/split、label、主指标、`n_eff`、CI、promotion gate |
| Operations/Alerts | 6 | retry、timeout、queue lag、provider error、每日成本、通知路由 |

模型角色绑定固定 6 条：`R0 semantic、contract/schema、planner/prior、researcher、
verifier、joint forecaster`。每条包含 provider、route、exact model、effort、网络/工具权限、
prompt/schema version、timeout、token/search/cost budget 和 fallback policy。

资金权限 manifest 单独保存 mode、evaluation capital、authorized capital、capability、
类别/component/global 上限、kill switch 和自动回退条件。基础设施地址和 secrets 不计入上述
业务配置，也不出现在配置页面。

## 4. 可观察性

### 4.1 两套追踪同时存在

- **业务追踪**回答“系统为什么做出这个决定”；使用持久业务 ID 和事件账本。
- **技术追踪**回答“这次调用在哪里慢或失败”；使用 OpenTelemetry
  `trace_id/span_id`，接入结构化日志和 metrics。

技术 Trace 不能替代业务事实，Redis 中的最新状态也不能作为审计记录。

业务主链 ID：

```text
cohort_id
→ screening_episode_id
→ decision_opportunity_id
→ component_version_id
→ forecast_episode_id
→ submission_id
→ trade_decision_id
→ economic_intent_id
→ execution_id
→ ledger_entry_id
→ label_id / metric_run_id
```

横向统一携带 `chain_id、causation_event_id、attempt_id、idempotency_key、
release_manifest_id、trace_id/span_id`。重试必须创建新 attempt，不能覆盖失败记录。

### 4.2 必看指标

**运行健康**：REST/WS freshness、断线与回补 gap、queue lag、oldest job、各 Gate
吞吐/失败/重试、阶段 p50/p95/p99。

**模型与成本**：按 role/provider/model 的调用数、tokens、search/tool、schema failure、
延迟、缓存和人民币实际成本；可下钻到 episode/attempt。

**业务漏斗**：universe → R0 → G1/G2 → R1 → blind commit → reveal → action；每个
reject/WAIT/ABSTAIN 必须有 reason code。

**预测与利润**：prediction loss、selection coverage、net edge、fill/slippage、交易 PnL、
System Net Profit、drawdown、CVaR、capital-days；五层指标不相互替代。

**硬完整性**以下目标恒为零：Gate 越序、blind taint、commit/reveal 倒序、过期 quote、
过期 forecast lease、terminal market 增加敞口、重复 intent/order、config drift、
execution/position/ledger 不平、inadmissible label 进入评分。

Prometheus 标签只放低基数的 stage/gate/result/role/provider/version；业务 ID 进入日志和 Trace。

## 5. 可回溯与回放

### 5.1 不可变事件账本

使用三类 append-only 事实：

1. `source_events`：REST frame、WS event、quote/orderbook observation；
2. `workflow_events`：Gate、状态转换、模型 attempt、blind commit、reveal、decision；
3. `ledger_transactions` / `ledger_postings` / `operating_cost_entries`：双分录交易现金流和
   全部系统运营成本。

`workflow_events` 至少保存：

```text
event_id, aggregate_type/id/seq, event_type/schema_version
chain_id, causation_event_id, attempt_id, trace_id/span_id
occurred_at, recorded_at, release_manifest_id, strategy/cohort ids
input_manifest_hash, output_artifact_hash, result, reason_code
idempotency_key, prev_event_hash, event_hash, payload_json
```

事件不更新、不删除；更正使用 revision/supersede/reversal。大对象进入 content-addressed
artifact store，数据库保存 URI、SHA-256、MIME、size 和产生它的 attempt。

一次阶段成功必须在同一数据库事务内写入：业务 artifact 引用、workflow event、
current-state projection 和 transactional outbox。Outbox 再投递 Redis，避免“数据库成功但
下个任务丢失”。

### 5.2 必须冻结的证据

- contract/rules/resolution source 原文与 `contract_spec`；
- component membership、world schema、prior；
- evidence source/revision、三时态与 information snapshot；
- exact provider/route/model/prompt/tool policy、请求/响应 hash、usage/cost；
- blind input manifest、immutable submission、forecast lease；
- reveal 使用的 bid/ask/depth/fee quote ID；
- Gate 结果、decision probability、action、仓位计算和 permission；
- order/ack/fill/cancel/reject、position、cash/redeem 和成本账；
- label evidence/version、metric run 和 promotion manifest。

### 5.3 回放语义

```text
replay chain <chain_id>                 # 查看完整因果链
replay decision <trade_decision_id>     # 重算确定性估值/组合
replay projection <event-range>         # 在临时 schema 重建投影
```

回放默认 `network=false、search=false、execution=false`，使用封存的 REST/WS/网页/LLM
artifact 与 fixed clock/random seed。LLM 原响应只能验证 hash/schema/下游处理；重新调用模型
属于新 experiment attempt，不冒充精确回放。确定性 payout、projection、shrinkage、edge、
portfolio、ledger 和 metrics 必须重算得到相同 hash。

## 6. 权威状态与数据分组

| 分组 | 主要表/对象 |
|---|---|
| Control | config versions、strategy/objective、model bindings、release、permission |
| Market | universe frames、cohort memberships、markets、quotes/orderbooks、source events |
| Semantics | contract snapshots/specs、tokens/payouts、components、world schemas |
| Cognition | plans、priors、facts/revisions、information snapshots、episodes、attempts、submissions |
| Decision | quote bindings、gate results、trade decisions、action sets、underwriting plans |
| Execution | intents、orders、acks、fills、positions、cash/asset ledger、reconciliation |
| Learning | resolution labels、score observations、metric runs、experiments、promotion decisions |
| Audit | workflow events、artifacts、outbox、cost entries、alerts、replay runs |

写模型为“不可变事实 + 可重建 current projection”。Admin 查询 projection，审计与回放读取事实。
Evidence、submission、decision、execution receipt、ledger、label、metric 均不提供编辑/删除 API。

## 7. 状态机和双时钟

主状态固定为：

```text
Screening: CREATED → G0 → R0_SELECTED | DEFERRED | REJECTED
Opportunity: CREATED → G1 → G2 → EPISODE_CREATED | PRE_COMMIT_TERMINAL
Episode: CREATED → ROUTED → PRIOR_READY → EVIDENCE_FROZEN
       → BLIND_COMMITTED → REVEALED → DECIDED | SUPERSEDED_NEW_EVIDENCE
Decision: CREATED → QUOTE_BOUND → G7A → G7B → ACTION | WAIT | ABSTAIN
Execution: INTENT → SUBMITTED → ACK/PARTIAL/FILLED | CANCELLED/REJECTED/UNKNOWN
Label: PENDING → PROVISIONAL → DISPUTED | FINAL_ADMISSIBLE | FINAL_EXCLUDED
```

- 新事实、规则/schema 变化、forecast lease 到期：创建新 cognition episode；
- 只有 quote/depth/cost/position 变化：复用仍有效 submission，只创建新 trade decision；
- terminal market 永久禁止增加敞口；risk-reducing action 不被正 edge Gate 阻断；
- 首版固定 `HOLD_TO_RESOLUTION`。

## 8. Admin 信息架构

首版新增 **14 个菜单页面 + 5 个隐藏详情页面，共 19 个业务页面**；Base 已有的用户、
角色、菜单、任务、会话、备份等系统页不计入。

| # | 菜单页面 | 主要内容 |
|---:|---|---|
| 1 | 总览 | 健康、风险、最新完整链、决策、PnL、成本、异常入口 |
| 2 | Markets / Cohorts | 全量市场、R0 去向、拒绝抽样、数据新鲜度 |
| 3 | Components | contract/component/schema 版本、成员和一致性状态 |
| 4 | Episodes | cognition episode、阶段、lease、状态、耗时和成本 |
| 5 | Decisions | quote binding、edge、Gate、WAIT/ABSTAIN/ACTION |
| 6 | Execution / Portfolio | intents、orders、fills、positions、ledger、风险占用 |
| 7 | Model Routes | 6 个角色绑定、权限、健康检查和候选状态 |
| 8 | AI Invocations | 每次调用的输入、工具、原始/结构化输出、校验和下游影响 |
| 9 | Usage & Cost | token/search、延迟、失败率、运营成本和下钻 |
| 10 | Strategy Config | 10 分区草稿、校验和 diff |
| 11 | Releases / Permissions | 发布、manifest、资金权限、回滚和 kill switch |
| 12 | Labels / Evaluation | labels、五层指标、experiments、promotion |
| 13 | Audit / Replay | chain 时间线、artifact、确定性回放和 diff |
| 14 | Integrity / Health | blind taint、陈旧数据、重复执行、账本、队列和 provider 告警 |

隐藏详情页固定为：`Market/Contract Detail`、`Component Detail`、`Episode Detail`、
`Decision/Execution Detail`、`AI Invocation Detail`。它们从列表或告警进入，不占侧边菜单。

Dashboard 只回答：系统是否健康、当前是否有资金风险、最近一次完整链何时成功、
今天花了多少钱、做了什么决定、为什么没有交易。任一异常必须一跳进入对应 chain/episode。

配置页使用“草稿 → 校验 → diff → 发布”，不能直接覆盖当前值。Episode/decision/attempt/
ledger 页面全部只读。复杂详情页使用 `PageShell + TanStack Query`；普通列表复用
`SchemaCrudPage/CrudTable` 的只读能力。

本阶段只锁定信息架构；批量 UI 实现前仍需按项目规则确认产品色板、语义色、字体、密度、
圆角和一张真实 Episode 详情页高保真预览。

## 9. Base Platform 落点

```text
serve/app/
├── controllers/admin/trading/     # 薄查询、发布、回放命令
├── logics/trading/                 # 配置、cohort、episode、decision、ledger 业务逻辑
├── models/trading/                 # 上述权威对象与 projections
├── schemas/trading/                # API 与 event schema
├── repositories/trading/           # 专用 SQL、keyset、CAS；不含业务判断
├── domain/trading/                  # payout/projection/valuation/risk 纯确定性函数
├── services/
│   ├── polymarket/                 # Gamma/CLOB/Data/WS/Polygon/Relayer Driver
│   ├── model_gateway/              # xAI/DeepSeek/Kimi/Packy
│   ├── artifact_store/             # content-addressed artifact
│   └── vault/                      # envelope-encrypted execution secret
├── handlers/trading/               # outbox event → 单个 Logic/UoW
└── runtimes/trading/                # ingest/execution/cognition/evaluation/replay 独立进程
```

现有 admin operation log 和 Redis task status 只作辅助观察，不承担业务审计。业务查询必须
通过 Logic，所有 filter/sort/field 显式 allowlist；资金和配置发布权限由服务端 RBAC 校验。

## 10. 实现顺序与完成标准

1. **Evidence Spine**：迁移、ID、配置版本、artifact、workflow event、outbox、source/quote。
2. **Minimal Cognition**：contract/component、evidence、blind submission、物理隔离。
3. **Decision + Shadow Ledger**：reveal、edge、risk、shadow intent、position/cost ledger。
4. **Observability/Admin**：Dashboard、episode/decision trace、配置发布、cost、alerts、replay。
5. **Canary/Live**：只有 Evidence Spine、回放、账本和 kill switch 全部验收后接真实 Driver。

首版完成必须证明：

- 任一 action 可一键追到原始市场、证据、模型、配置、代码、quote 和 Gate；
- 新配置只影响新 episode，旧 episode 的 release manifest 永久可读；
- 相同事件 fixture 可重建相同 deterministic projection/hash；
- Worker 崩溃、重复消息和重试不会重复 commit、intent、order 或 ledger；
- V1 Gold 事故回归 fixture 全部被拒绝：terminal re-entry、陈旧 quote、重复 signal、
  `unreviewed` 冒充 `passed`；
- 业务 PnL 与模型/搜索/基础设施成本可下钻并汇总为 System Net Profit；
- secrets 不出 vault，敏感字段不进入日志、artifact、配置 diff 或 API。

## 11. 非目标

- 不复用或修补 V1 Python/SQLite 流程；
- 不扩展为多策略平台；
- 首版不实现 ACTIVE_REVALUE、ensemble 或高频做市；
- 不用普通日志代替事件账本，不用缓存代替数据库事实；
- 不在视觉方案确认前批量实现后台页面。
