# V2 后台页面设计梳理（WP-07B）

> 目标：把 WP-07A 的只读 API + typed 数据层，变成能现场演示、能支撑日常运维的 16 个页面。
> 现状：页面骨架已搭（PageShell + 表格 + filter + tabs + 五态），但**业务字段普遍只露出 3–4 个通用列（ID/状态/时间）**，核心业务信息没有铺开。本文件逐一捋清每个页面「干什么 / 展示什么 / 怎么下钻」。

## 0. 信息架构总览

V2 是一条 **8-Stage 的多模型联合预测交易流水线**：感知 → 合约 → 目标 → 研究 → 盲预测 → 揭价 → 执行 → 评估学习。后台页面按流水线段落组织：

```
Stage 0-1 感知/合约   Markets · Tags · Components
Stage 2-5 认知/预测   Episodes · Models & AI · AI Invocations
Stage 6   执行         Decisions · Execution
Stage 7   评估学习     Evaluation · Replay
横切运维              Dashboard · Costs · Strategy Config · Releases · Integrity · Artifacts(隐藏)
```

**统一约定**（WP-07B §2 已冻结）：
- 所有页面只读，经 `api/v2/*` + `queries/v2/*` 取数，不新增 mutation，不重算 Gate/PnL/edge。
- 列表统一 `CursorPage<T>`（keyset 翻页，无 OFFSET/COUNT）；BIGINT/NUMERIC 为字符串；时间 UTC ISO；hash 64-hex。
- 每页五态：loading 骨架 / empty 空态 / partial(降级) / error 重试 / denied 权限面板，切换不跳动。
- 状态用 `StatusBadge` 双编码（文本 + 色块/图标），不重算。
- 权限：列表 `v2:<domain>:view`，artifact `v2:artifact:read`；无权限显示 denied 态、不发请求。

---

## 1. Dashboard 总览 `/v2/dashboard`

**干什么**：运维驾驶舱，一眼看清系统是否健康、有无风险、最近是否成功结算、今天花了多少钱、决策吞吐如何。不扫事实大表，只读 WP-04 的五张 projection。

**内容**（5 张卡片 + 明细表）：
| projection | 标题 | 说明 |
|---|---|---|
| `ops_health_current` | 系统健康 | 运行时与管道可用性 |
| `account_risk_current` | 资金风险 | 账户占用与限额 |
| `latest_chain_summary` | 最近完整链 | 最近一次成功结算 |
| `provider_cost_daily` | 今日成本 | 模型与检索费用 |
| `pipeline_funnel_hourly` | 决策漏斗 | 本小时筛选与决策 |

每张卡显示：主值 + 新鲜度标签（fresh/stale/missing）+ 提示语。下方明细表列出五张投影的 `as_of` / 行数 / 新鲜度。顶部 hero 显示 `as_of` 时间戳与「只读投影」标识。partial 态时提示「部分投影已降级：…」。

---

## 2. Markets 市场 `/v2/markets` → `/v2/markets/:id`

**干什么**：查看 Polymarket 市场（合约）的发现、生命周期与流动性，下钻看合约语义快照。

**列表列**：`question`（下钻链接）/ `slug` / `ticker` / `active` / `closed` / `accepting_orders` / `neg_risk` / `volume` / `liquidity` / `end_date`。
**filter**：`neg_risk`、`closed`（布尔下拉）。

**详情 `MarketDetail`** 分四块：
- **market**：基础身份 + `content_hash` / `raw_artifact_ref`；
- **snapshot**：合约规则/澄清/resolution source 的原始快照；
- **specs**：`contract_specs` 列表（每个 version 的 K_c/R_c、payout functions 引用）；
- **current**：最新 quote/状态；**cohort**：所属 universe cohort 与成员关系。

---

## 3. Tags 标签 `/v2/tags`（WP-07A 已建）

**干什么**：管理 Gamma 标签的收录裁决（SELECT/DEFER/REJECT），决定哪些标签进入后续 cohort。

**列表列**：`slug` / `label` / `gamma_tag_id` / `seen_in_catalog` / `seen_in_event` / `disposition`（SELECT/DEFER/REJECT 三色徽章）/ `event_count` / `observed_at`。
**filter**：`slug`、`seen_in_catalog`、`disposition`。

---

## 4. Components 组件 `/v2/components` → `/v2/components/:id`

**干什么**：联合预测的组件边界（哪些合约一起预测），及其版本与成员合约。

**列表列**：`component_key`（下钻）/ `description` / `cost_budget` / `created_at`。
**filter**：至少一个业务 filter（如 key 关键词）。

**详情 `ComponentDetail`**：component 基础 + `versions`（版本列表）+ `member_contracts`（每个版本的合约成员清单）。

---

## 5. Episodes 回合 `/v2/episodes` → `/v2/episodes/:id`

**干什么**：认知回合（一次联合预测的单位），贯穿 Stage 3–5。核心监控对象。

**列表列**：`episode_key`（下钻）/ `status`（DRAFT→ROUTED→BLIND_COMMITTED→REVEALED→DECIDED→…）/ `cognition_status` / `trigger` / `horizon` / `cutoff_at` / `prior_frozen_at` / `forecast_committed_at`。
**filter**：`status`（下拉）。

**详情 `EpisodeDetail`**（对应已确认高保真预览，阅读顺序固定）：
identity → 状态条 → **Blind vs Market** 对比 → **Gate 条带**（逐道 Gate 结果）→ **Evidence**（evidence bundles）→ **AI**（invocations）→ **Decision + action** → **Timeline**（submission/gate/info_snapshot）→ 审计。
字段组：`priors` / `evidence_bundles` / `submissions` / `gates`。

---

## 6. Decisions 决策 `/v2/decisions` → `/v2/decisions/:id`

**干什么**：市场相对决策（Stage 6 入口），从盲预测到可行动 edge 的产物。

**列表列**：`decision_key`（下钻）/ `decision_class` / `status` / `selected_action_type` / `reason_code` / `trigger_at` / `decided_at`。
**filter**：`status`、`decision_class`。

**详情 `DecisionDetail`**：decision 基础（含 `quote_bound_at`）+ `quote_bindings`（引用盲提交/决策时点的报价）+ `underwriting_plans`（承保计划）+ `action_sets`（动作集）+ `intents`（经济意图）。

---

## 7. Execution 执行 `/v2/execution`（4 tabs）

**干什么**：跟踪交易从意图到账本的落地链路。

| tab | 实体 | 关键列 |
|---|---|---|
| 意图 | `IntentRow` | `intent_key` / `status` / `trade_decision_id` / `action_set_id` / `ttl_at` |
| 订单 | `OrderRow` | `order_key` / `external_order_id` / `token_id` / `side` / `price` / `size` / `filled_size` / `status` |
| 持仓 | `PositionRow` | `portfolio_namespace` / `token_id` / `market_id` / `quantity` / `cost_basis` |
| 账本 | `LedgerRow` | `transaction_key` / `kind` / `status` / `trade_decision_id` / `posted_at` |

filter 按 tab 独立（如 orders.status、ledger.kind）。

---

## 8. Models & AI 模型 `/v2/models-ai`

**干什么**：查看模型角色 → 供应商 → 路由 → 模型引用的绑定，以及网络策略与内容哈希。

**列表列**：`role` / `provider` / `route` / `model_ref` / `network_policy` / `binding_version` / `content_hash`。
**filter**：`role`、`network_policy`。

---

## 9. AI Invocations AI 调用 `/v2/ai-invocations` → `/v2/ai-invocations/:id`

**干什么**：每一次 AI 调用的可观测记录（Stage 2–5 的模型证据）。

**列表列**：`invocation_key`（下钻）/ `stage` / `role` / `attempt_no` / `lifecycle_state` / `requested_model` / `returned_model` / `input_tokens` / `output_tokens` / `cost_estimated` / `occurred_at`。
**filter**：`role`、`lifecycle_state`。

**详情 `AiDetail`**：invocation 全字段（binding/cache/prompt/schema/request/raw/normalized 等 artifact 引用，**不内联 raw**）+ `model_role_binding` + `tool_calls` + `validations` + `downstream`（retry/fallback 链）。

---

## 10. Costs 成本 `/v2/costs`

**干什么**：运维成本（模型/检索/基础设施/人工）的 append-only 明细。

**列表列**：`cost_key` / `cost_kind` / `amount` / `release_manifest_id` / `episode_id` / `period_start` / `period_end`。
**filter**：`cost_kind`。

---

## 11. Strategy Config 策略配置 `/v2/config`

**干什么**：策略配置的版本清单（只读，不实现 draft/publish）。

**列表列**：`config_key` / `version_no` / `schema_version` / `content_hash` / `status` / `creator` / `created_at`。
**交互**：可展开查看 `content`（`ConfigDetail`），但无编辑。

---

## 12. Releases 发布 `/v2/releases`

**干什么**：发布版本与不可变部件（config/strategy/execution_spec/capital_permission）的绑定。

**列表列**：`release_name` / `git_sha` / `image_digest` / `db_revision` / `total_hash` / `status` / `creator`。
**详情**（`ReleaseDetail`）：release 基础 + `exact_parts`（四类部件的 ref/version/content_hash/status）。**不做** release rollback/kill。

---

## 13. Evaluation 评估 `/v2/evaluation`（3 tabs）

**干什么**：标签审计、指标运行与策略/资金晋级（Stage 7 学习闭环）。

| tab | 实体 | 关键列 |
|---|---|---|
| 标签 | `ResolutionLabelRow` | `label_key` / `version_no` / `state` / `resolution_state` / `policy_code_hash` |
| 指标 | `MetricRunRow` | `run_key` / `split` / `status` / `n_market` / `n_episode` / `n_eff` / `artifact_hash` |
| 晋级 | `PromotionRow` | `promotion_key` / `promotion_type` / `from_ref` / `to_ref` / `status` / `reason_code` |

**不做** label adjudicate / promotion 批准。

---

## 14. Replay 回放 `/v2/replay`

**干什么**：确定性重放的运行记录（代码/输入/输出/种子可复现）。

**列表列**：`run_key` / `replay_kind` / `manifest_hash` / `code_hash` / `seed` / `input_artifact_hash` / `output_artifact_hash`。
**不做** replay create/cancel。

---

## 15. Integrity 完整性 `/v2/integrity`（3 tabs + 运行时块）

**干什么**：系统完整性审计——告警、工作流事件、外部调用链，以及运行时快照。

| 区块 | 内容 |
|---|---|
| 运行时 | `RuntimeSnapshot`（status 等，顶部 DetailSection） |
| Alerts | `AlertRow`：`alert_key` / `severity` / `code` / `message_redacted` |
| Workflows | `WorkflowRow`：`event_key` / `event_type` / `aggregate_type` / `aggregate_id` |
| External Calls | 外部调用链（聚合类型 + ID 反查） |

Workflows/External Calls 支持按 `aggregate_type` + ID 下钻反查链。

---

## 16. Artifacts 制品（隐藏路由 `/v2/artifacts/:content_hash`）

**干什么**：任意哈希/引用的内容元数据与血缘（从详情页的 hash 下钻进入）。

**内容** `ArtifactMetadata`：`content_hash` / `content_type` / `content_length` / `stored_at` / `lineage`（from/to artifact + relation + invocation_ref）。

---

## 17. 详情页导航链路（下钻图）

```
Markets ──> Market Detail ──> artifact hash
Components ──> Component Detail
Episodes ──> Episode Detail ──> AI Invocation Detail / Decision Detail / artifact
Decisions ──> Decision Detail ──> Execution(intents) / artifact
AI Invocations ──> AI Detail ──> artifact（request/raw/normalized）
任意 hash ──> Artifacts
```

## 18. 落地优先级（Checkpoint 顺序，同 WP-07B）

1. **A 视觉 token + 菜单/路由基建**（v2-tokens.scss + 0071 菜单 seed + _shared 组件）。
2. **B 14 列表页**：先铺全业务字段（每页列见上），再补 filter + keyset + 下钻。
3. **C 5 详情页**：按 §5–§6 的 section 结构，Episode Detail 严格对齐已确认高保真预览。
4. **D 浏览器验收**：三 viewport、console=0、无横向溢出、200% zoom、键盘走查。

---

## 附：现状差距清单（骨架 vs 设计）

| 页面 | 现状 | 需补 |
|---|---|---|
| dashboard | 5 卡 + 明细已完整 | 基本到位，核对 hero as_of |
| markets | 列偏少 | 补 question/volume/liquidity/neg_risk/end_date |
| components | 列偏少 | 补 cost_budget/description、详情 versions/member_contracts |
| episodes | 4 列 | 补 trigger/horizon/cutoff 等，详情按高保真预览铺开 |
| decisions | 列偏少 | 补 decision_class/selected_action_type/reason_code |
| execution | 4 tabs 已有但列少 | 每 tab 补业务字段（order 的 price/size/filled） |
| models-ai / costs / config / releases / replay | 列偏少 | 按上表补字段 + filter |
| evaluation / integrity | tabs 已有 | 补指标字段、workflow/external 反查 |
| 5 详情页 | 部分仅有骨架 | 按 section 结构补齐 |
