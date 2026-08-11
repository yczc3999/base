# WP-03 — Market-Relative Decision、Minimum Portfolio、Shadow Execution 与双分录账本

> 状态：**READY**<br>
> 执行模型：DeepSeek V4 Flash<br>
> 前置：`WP-02` 已 ACCEPTED；Alembic head=`b1000021`<br>
> 唯一完成交付：`serve/docs/manifests/wp-03-market-relative-decision-shadow-ledger.md`<br>
> 最后更新：2026-08-11 EDT

## 0. 快车道执行规则

1. 本任务是一个里程碑、四个连续 Checkpoint；不得拆 `-rN`，不得等待用户逐段确认。
2. A 冻结 P2 execution spec 后，才允许创建首个 trade decision。P2 spec 的 JSON、数据库版本、
   hash 和检查结果写入最终 WP-03 manifest 的 `P_EXECUTION_SPEC_MANIFEST` 小节；**不另建第二份 manifest**。
3. A/B/C 可按文件所有权并行，中途只跑定向测试；完整 trading/full/performance 仅在最终交付前各跑一次。
4. 发现范围内会造成错误决策、重复经济效果、账本不平、回放漂移或迁移破坏的 P0/P1，直接修复并复验。
5. 只打通这一条新闻市场、小时至天的纵向链：

```text
BLIND_COMMITTED + valid lease
→ reveal exact book/fee/cost snapshot
→ deterministic market-relative decision
→ G7A full-cost action valuation
→ G7B minimum portfolio/caps
→ ACTION | WAIT | ABSTAIN
→ immutable economic intent
→ conservative shadow fill
→ position lot + balanced ledger + operating cost
```

## 1. 目标与用户价值

把 WP-02 的独立、市场盲联合预测，转换成一份可执行价格下可重算的 shadow 决策，证明：

- 模型概率与市场价格的分歧在 fee、spread、depth、slippage、资金占用和系统运营成本后是否仍有价值；
- 交易是否优于 `NO_ACTION`、持有现金和当前组合中的替代机会；
- quote-only 变化能否复用仍有效 belief、零 AI 重估；
- 重复消息、崩溃和并发不会重复 action、fill、持仓或 ledger 经济效果；
- `trading_pnl`、operating cost 和 `system_net_profit` 能否分层重建。

首版持有范式固定为 `HOLD_TO_RESOLUTION`；所有 execution 均为 shadow，授权真实资本为 0。

## 2. 已确认产品与技术决策

1. **独立 belief 不覆盖**：`forecast_submissions.BLIND_COMMITTED` 永不修改。揭价后另建
   `trade_decision`；基础决策 belief=`BLIND_ONLY`，精确复用已提交 `Q/U`。
2. **不伪造 shrinkage**：本期不学习权重。可实现 `LINEAR_SHRINKAGE` shadow challenger，但仅当冻结
   policy 明确给出固定 `w_blind∈[0,1]` 且能构造 coherent `Q_market`；否则 challenger
   `ABSTAIN_MARKET_REFERENCE_UNIDENTIFIED`，不得阻塞 `BLIND_ONLY` 基线。
3. **市场相对不是 midpoint 差值**：买入按 ask depth walk，卖出按 bid depth walk；保存 gross edge、
   all-in break-even、net edge、robust EV、ROI、capital-days 和 delay erosion。
4. **AI 不参与确定性链**：reveal、coherence、payout projection、edge、cost、portfolio、fill、ledger
   全部使用代码。quote/depth/cost/position trigger 新增 0 个 AI invocation、0 个 forecast episode。
5. **双时钟**：只有新 evidence、规则/schema 变化或 lease 到期才让旧 episode 失去增仓资格；纯行情、
   成本或持仓变化只创建新 trade decision。
6. **动作本体**：`BUY_TOKEN/ADD_TOKEN/SELL_TO_REDUCE/SELL_TO_CLOSE/HOLD/FLIP`；YES/NO 只是
   Bernoulli 展示别名；short/sell-to-open 不支持。`FLIP=close leg + open opposite leg`，open 不通过
   G7A 不得阻断 risk-reducing close。
7. **无动作语义**：`NO_ACTION` 是计算基准；`WAIT` 必须有 wake condition；`ABSTAIN` 必须有终局
   reason 且无 action set；`HOLD` 是 `ACTION` 下的零腿组合动作，必须已有仓位和仍有效 underwriting，
   且不生成 intent/execution。硬 forecast failure 标记 `decision_class=RISK_REVIEW`，只允许
   reduce/close/HOLD，最终仍收敛到 `ACTION|WAIT|ABSTAIN`，不新增平行状态机。
8. **组合保险丝**：同 market 净风险资本≤4%、同 forecast component≤6%、全局≤30%；capital
   permission 可以更低，不能更高。并发决策必须原子占用 shadow reference capacity。
9. **资金权限**：只接受 active `capital_permission_manifest.mode='shadow'`、`authorized_capital=0`、
   `kill_switch=false`；本期不得产生 canary/live execution 或真实 order。
10. **费用/返利**：G7A baseline 的 rebate 恒为 0；fee、spread/slippage、funding、discount、capital
    charge、allocated operating cost 互斥计账。返利只有收到真实独立事实后才可单列，不能把负的
    pre-rebate action 改为通过。本期 fixture 同时报 gross、net-pre-rebate、system-net。
11. **Intent 与 mode 分离**：`economic_action_intent_hash` 不含 mode/permission id/authority；shadow
    authorization 单独绑定 intent + permission。本期只有 shadow authority，重复 intent effect=0。
12. **V1 教训固化**：terminal market、陈旧 quote、重复旧 signal、end 后重入一律 0 增仓；
    `UNREVIEWED` 不等于 PASS；同一 forecast/quote 不得每轮 settle 后再次开仓。
13. **账户样本只吸收会计思想**：同一 condition 的全部 token/action leg 合并评价；paired inventory、
    方向 residual、fee 和 rebate 分账。不得加入 BTC 5m、返利驱动高周转或跟单策略。

## 3. 内部 Checkpoint 与精确文件

### A — 冻结 P2 execution spec 与 fixture

新增：

```text
serve/tests/trading/fixtures/p2_decision/p_execution_spec_v1.json
serve/tests/trading/fixtures/p2_decision/bernoulli.json
serve/tests/trading/fixtures/p2_decision/time_nested.json
serve/tests/trading/fixtures/p2_decision/mutually_exclusive.json
serve/tests/trading/fixtures/p2_decision/conditional.json
serve/tests/trading/fixtures/p2_decision/void_partial.json
serve/tests/trading/fixtures/p2_decision/shadow_book_depth.json
serve/tests/trading/fixtures/p2_decision/v1_gold_reentry.json
```

`p_execution_spec_v1.json` 必须是 canonical JSON，至少冻结：

- objective/strategy/release/execution spec/capital permission identity 与 content hash；
- `HOLD_TO_RESOLUTION`、allowed actions、short=false、shadow-only、authorized capital=0；
- executable price convention、depth walk、fee、slippage、latency、TTL/staleness、capacity；
- discount、funding、capital charge、operating cost 分配及互斥会计恒等式；
- 4%/6%/30% caps、kill switch、rounding/base units；
- `BLIND_ONLY` baseline；可选固定 shrinkage challenger 的算法 id/w，无默认学习值；
- spec 的 `frozen_at`，必须严格早于首个 P2 decision trigger。

通过测试 fixture 创建 active `execution_spec_versions` 与 `SHADOW_REFERENCE`
`capital_permission_manifests`，再由 release manifest 精确引用；禁止只在 Python 常量里假装冻结。
最终 completion manifest 内必须有 `P_EXECUTION_SPEC_MANIFEST` 小节与 JSON SHA-256。

### B — `b1000030` Decision、G7A、Action Set

生产文件（新增或按注释透明强化）：

```text
serve/app/models/trading/decision.py
serve/app/models/trading/workflow.py
serve/app/models/trading/market_stream.py
serve/app/models/trading/__init__.py
serve/app/models/__init__.py
serve/alembic/versions/b1000030_v2_0030_p2_decision_shadow.py
serve/app/schemas/trading/decision.py
serve/app/schemas/trading/__init__.py
serve/app/domain/trading/valuation.py
serve/app/domain/trading/rounding.py
serve/app/domain/trading/__init__.py
serve/app/repositories/trading/decision.py
serve/app/repositories/trading/__init__.py
serve/app/logics/trading/decision.py
serve/app/logics/trading/__init__.py
serve/app/orchestrator/trading_state_machine.py
```

`decision.py` 唯一拥有：

```text
market_relative_decisions
  每个 trade decision 最多一条；保存 blind/reference/decision mode、w、Q/U hashes、token gaps、
  reference identifiability、input/output manifest hash；不得覆盖 blind submission。

discrepancy_reviews
  确定性 reveal 检查与结构化 reason；本期不调用 LLM、不静默改 belief。

trade_decisions
  episode/submission/lease/objective/strategy/release/execution spec/permission/variant/trigger；
  状态严格采用平台主状态机：`CREATED→QUOTE_BOUND→G7A→G7B→ACTION|WAIT|ABSTAIN`，三个结果
  terminal。G7A/G7B 的 pass/fail 与 reason 只写对应 gate_decisions，不另造状态；风险复核写
  `decision_class=RISK_REVIEW`，HOLD 写 selected action type，不另造 terminal status。

action_candidates / resolution_cashflows
  每个 token/action 的 executable depth、全部互斥成本、逐 world-state cashflow、EV/ROI/log-growth、
  robust bounds、capacity/capital-days 与 reconciliation residual。

action_sets / action_set_legs
  被选择的完整 condition/component action；FLIP 必须显式 close/open 两 leg；HOLD 为零 leg 且必须
  有现存 position，其他 ACTION 至少一 leg。

underwriting_plans
  entry range、hold-to-resolution、thesis hash、invalidation、wake/recheck、edge-close、time stop。

economic_action_intents
  mode-independent immutable intent、intent hash、TTL、selected action set、preflight hash。
```

`pm_quote_bindings` 是唯一 quote 物理表，不创建 `episode_quote_bindings` 或第二套 quote 表：

- 增加 nullable `trade_decision_id` FK（旧 0011 行可为 null）；
- 删除旧的 `(token_id,checkpoint_id,checkpoint_received_at)` 唯一约束，允许不同 decision 复用同一
  checkpoint；新增 partial unique `(trade_decision_id,token_id) WHERE trade_decision_id IS NOT NULL`；
- 新 P2 写入必须有 trade_decision_id；`decision_ref` 只保留 legacy provenance，不得再写；
- quote row 仍精确 FK 到 checkpoint，保存 bid/ask/as_of/received/stale_at；terminal 后不可修改/删除。

### C — `b1000031` Minimum Portfolio、Shadow Execution、Ledger

生产文件：

```text
serve/app/models/trading/execution.py
serve/app/models/trading/ledger.py
serve/app/models/trading/__init__.py
serve/app/models/__init__.py
serve/alembic/versions/b1000031_v2_0031_p2_shadow_ledger.py
serve/app/schemas/trading/execution.py
serve/app/schemas/trading/__init__.py
serve/app/domain/trading/portfolio.py
serve/app/domain/trading/ledger.py
serve/app/domain/trading/__init__.py
serve/app/repositories/trading/execution.py
serve/app/repositories/trading/ledger.py
serve/app/repositories/trading/__init__.py
serve/app/logics/trading/portfolio.py
serve/app/logics/trading/execution.py
serve/app/logics/trading/__init__.py
```

本期 `execution.py` 只拥有：

```text
executions
positions
position_lots
```

execution 状态只允许 `PENDING→PARTIAL|FILLED|REJECTED|FAILED`；四个结果 terminal。`positions`
是可重建 current projection，可用 optimistic version 更新；`position_lots` 与所有 fill 事实 append-only。

本期 `ledger.py` 只拥有：

```text
ledger_transactions
ledger_postings
operating_cost_entries
```

不得提前创建 `pm_accounts`、balance/allowance、capital reservation、authorization envelope、
exchange order/trade、User WS/reconciliation 或 Vault 表；这些属于 WP-05。

### D — Handler、Runtime、Replay、Performance、Manifest

```text
serve/app/handlers/trading/decision.py
serve/app/handlers/trading/execution.py
serve/app/handlers/trading/__init__.py
serve/runtimes/trading/execution.py
serve/runtimes/trading/__init__.py
```

Runtime 只注册 DB-backed decision/shadow handlers；不得 import 私有 CLOB SDK、vault、wallet、签名、
Data API 或真实下单 Driver。每个 Handler 只做一次 UoW；外部/长计算不持有 DB transaction。

透明必要更新允许修改：migration helper 的冻结对象白名单、现有 trading model/import/state-machine tests
及 test fixture helper。除此之外不得顺手重构 Base/V1/Admin。

## 4. 数据库硬约束

以下必须由 PostgreSQL constraint/trigger/deferred trigger/同一 UoW 原子封账保证，不能只靠 Pydantic：

1. trade decision 的 episode、committed submission、active lease、objective、strategy、release 全等；
   execution spec 与 permission 必须正是 release 引用的 active 行。
2. `trigger_at < quote_bound_at ≤ decided_at`；未发生阶段时间必须 null。lease 在 trigger/decision 均有效。
3. `QUOTE_BOUND` 前必须按 decision 所需 exact token set 写齐 `pm_quote_bindings`；每条未 stale、未 crossed、
   checkpoint/token/received_at FK 精确。stale quote 永远不得向后补未来 observation。
4. episode `BLIND_COMMITTED→REVEALED→DECIDED|SUPERSEDED_NEW_EVIDENCE` 顺序固定；quote-only refresh
   不修改 episode cognition 状态、不新建 forecast episode。
5. G7A/G7B Gate kind、target type/id、版本/policy hash、顺序与 trade decision 终态一致。
6. action candidate 的 token 必须属于 episode exact contract spec set；每个 cashflow world state 必须属于
   component schema；非 HOLD 的 ACTION action set 至少 1 leg，HOLD 为零 leg，WAIT/ABSTAIN 无 action set。
7. `(action_set_id,contract_spec_id,token_id,leg_role)` 唯一；quantity>0；BUY/ADD 为正 exposure，
   REDUCE/CLOSE 为负，FLIP close/open 成对且不可原子吞掉 close。
8. selected OPEN/ADD/HOLD/FLIP-open 必须绑定完整 underwriting；WAIT 有 wake condition；ABSTAIN 有
   terminal reason；`decision_class=RISK_REVIEW` 不能增加 exposure。
9. terminal decision/action/cashflow/underwriting/intent 禁 UPDATE/DELETE；更正创建新 decision/supersede。
10. `economic_action_intents.intent_hash` 通过非分区 `idempotency_claims` 全局唯一；同一 opportunity /
    action role 最多一个 active exposure-increasing intent；重试 effect=0。
11. shadow capacity/caps 以 `SELECT ... FOR UPDATE` 或条件 UPDATE 原子计算；并发候选合计不得突破
    permission、4%/6%/30%。没有现成 position row 时，以
    `(portfolio_namespace,component_id,market_id)` 固定顺序取得 transaction-scoped advisory lock，再读写
    projection，禁止“先查后写”。不同 shadow variant 使用独立 portfolio namespace，禁止相互合并 PnL/风险。
12. execution/lot/ledger/outbox 同一 UoW；partial/failed fill 只影响实际 shadow fill，position 不得为负。
13. ledger posting 使用整数 base units；POSTED 前每个 `(asset_type,asset_key)` 的 signed postings 合计为
    0 且至少两条，由 deferred trigger/封账函数强制。posted transaction/posting 禁 UPDATE/DELETE；
    纠错只写 exact reversal。
14. operating cost append-only，类别仅 `DATA|LLM|SEARCH|INFRASTRUCTURE|HUMAN|OPERATIONAL_LOSS`；
    关联 release/episode/decision/period 与 allocation policy hash，禁止把缺失成本写成 0。
15. migration DDL 为 literal frozen snapshot，不 import live ORM；支持 literal empty/existing Base、
    `upgrade→downgrade→upgrade`、重复检查和 exact 0031→0030→0021 unknown-object fail-closed。

## 5. 确定性计算合同

### 5.1 Market reference 与 decision belief

- `BLIND_ONLY`：`Q_decision=Q_blind`、`U_decision=U_blind`；市场仅用于 edge/cost，不进入 blind belief。
- `LINEAR_SHRINKAGE` challenger：仅当 frozen constructor 能从完整 token quote set 得到 coherent
  `Q_market` 时，`Q_decision=w_blind·Q_blind+(1-w_blind)·Q_market`；U 对每个成员做同一映射并重新
  coherence。没有可识别 market joint distribution 时必须 abstain challenger，不能逐 token 独立拼接。
- 初版必须支持可识别的 isolated Bernoulli 和完整 mutually-exclusive outcome set；其他 component
  仍可走 `BLIND_ONLY`。不得引入新的数值优化依赖来伪造通用 market joint distribution。
- 所有 Decimal、排序、tie-break、hash 和 projection 必须确定性；保存 input/output manifest。

### 5.2 G7A — 单机会全成本价值

对 action `a` 和 world state `ω`：

```text
ΔW_a(ω)
= settlement_cashflow(ω)
- executable_entry_cashflow
- explicit_fee
- execution_adjustment
- funding_or_discount_adjustment
- capital_charge
- allocated_marginal_operating_cost
```

- 每项只出现一次；如果 executable cashflow 已含某项，不得重复扣；保存 accounting components 与
  `cashflow_reconciliation_residual=0`。
- `robust_EV = min_{Q∈U_decision} E_Q[ΔW]`；同时保存 point EV、ROI、expected log-growth、worst loss、
  capital-days、break-even payout probability 和 edge delay erosion。
- exposure-increasing action 必须在最不利 U 下越过 frozen safety margin，并满足最小可成交容量；
  REDUCE/CLOSE 由 G7B 的边际降险决定，不受 standalone positive EV 阻断。
- baseline rebate=0；任何未落地返利不进入 pass/fail。

### 5.3 G7B — Minimum Portfolio

比较候选与 `NO_ACTION`、现金、当前 positions/ledger、同批其他候选，并执行：

- market/component/global exposure caps；
- marginal expected-log growth、worst loss/CVaR、capital-days、dependency concentration；
- permission capability/limits/kill switch；
- action 优先级不是先到先得；同批排序和 tie-break 必须冻结、可回放。

`HOLD` 以 ACTION 终态保存但不产生 intent/execution；WAIT/ABSTAIN 分别保存 wake/terminal reason；
只有具有经济变化 leg 的 ACTION 产生 intent/execution。

### 5.4 Shadow execution 与账本

- 按 exact quote checkpoint 的 depth 做 deterministic walk；frozen latency/slippage policy 可降低 fill，
  不得制造 book 中不存在的数量或 midpoint fill。
- shadow fill 输出 quantity、VWAP、fee、unfilled reason；partial/failed 是合法结果。
- position/lot 和 cash/token postings 同一 UoW；BUY 至少形成 portfolio↔shadow venue 的 cash 与 token
  两组对手 posting（至少 4 postings），每种 asset 分别归零。settlement/reversal 使用相同恒等式；不得用
  “一条净 PnL posting”冒充双分录。condition 内所有 token leg 一起报告，不只看赢家腿。
- 系统收益分三层：`trading_pnl`、`operating_cost`、`system_net_profit=trading_pnl-operating_cost`；
  未结算 shadow 只能报告 expected/mark-free exposure，fixture settlement 才报告 realized。

## 6. 测试文件与必须证明的事实

新增/修改：

```text
serve/tests/trading/unit/test_v2_valuation.py
serve/tests/trading/unit/test_v2_portfolio.py
serve/tests/trading/unit/test_v2_decision_logic.py
serve/tests/trading/unit/test_v2_shadow_execution.py
serve/tests/trading/unit/test_v2_trading_state_machine.py
serve/tests/trading/integration/test_v2_0030_decision_migration.py
serve/tests/trading/integration/test_v2_0031_shadow_ledger_migration.py
serve/tests/trading/integration/test_v2_decision_shadow_workflow.py
serve/tests/trading/integration/test_v2_ledger_invariants.py
serve/tests/trading/replay/test_v2_p2_decision_replay.py
serve/tests/trading/performance/decision_shadow_smoke.py
```

必须覆盖：

1. 五类 semantic fixture 的 BLIND_ONLY payout/cashflow/edge 可重算；Bernoulli 与完整互斥集合另验证
   coherent fixed shrinkage；不可识别 market reference 的 challenger 明确 abstain。
2. stale/crossed/missing quote、空 U、缺 frozen policy/spec/cost、非法 token、unsupported action 全 fail closed。
3. spread/slippage/fee/discount/funding/capital/operating-cost 双计或 residual 非零被拒绝。
4. 4%/6%/30% 与更低 permission cap 精确执行；两个并发 decision 不可共同越限。
5. ACTION/WAIT/ABSTAIN 主状态与 HOLD/RISK_REVIEW/FLIP 的 action/reason/leg 映射正确，不出现平行状态。
6. partial/failed BUY/REDUCE/CLOSE 使用真实 depth fixture，不产生负仓位或虚假 full fill。
7. 每个写入点 crash 整体 rollback；重复 event、并发重复、worker 重启的 intent/execution/lot/ledger
   economic effect=0。
8. ledger orphan/unbalanced/cross-asset imbalance、terminal update/delete 全拒绝；reversal 精确相反。
9. quote/depth/cost/position refresh 前后 AI invocation 与 forecast episode count 均不增加。
10. 两次 offline replay 的 reference、Q/U decision、cashflow、portfolio、action、intent、fill、position、
    ledger hash 全等。
11. `v1_gold_reentry`：end 后/terminal、13h+ stale quote、重复旧 forecast、UNREVIEWED 四路径均 0 增仓。
12. condition 多腿报告同时输出 gross、fee、rebate(本期0)、trading PnL、operating cost、system net；
    不允许用单一赢家腿冒充策略收益。
13. 数据库不存在 canary/live execution、真实 order、私有凭据或 short position 路径。

性能硬门（真 PostgreSQL、有界 pool，使用真实 domain/repository/UoW/constraint）：

- deterministic decision valuation ≥100/s 持续 60s；
- 原子 shadow terminalization ≥10/s 持续 60s，每次含 decision、intent、execution、lot、balanced ledger、
  workflow event/outbox；
- lost/duplicate/unbalanced/negative-position=0；
- DB transaction p99≤50ms，pool wait p95≤20ms；
- JSON 记录 p50/p95/p99、10s 窗口、连接峰值、WAL delta、RSS、seed、硬件和实际 git commit。

## 7. 验收命令

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app runtimes tests alembic

.venv/bin/pytest -q \
  tests/trading/unit/test_v2_valuation.py \
  tests/trading/unit/test_v2_portfolio.py \
  tests/trading/unit/test_v2_decision_logic.py \
  tests/trading/unit/test_v2_shadow_execution.py \
  tests/trading/unit/test_v2_trading_state_machine.py

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0030_decision_migration.py \
  tests/trading/integration/test_v2_0031_shadow_ledger_migration.py \
  tests/trading/integration/test_v2_decision_shadow_workflow.py \
  tests/trading/integration/test_v2_ledger_invariants.py \
  tests/trading/replay/test_v2_p2_decision_replay.py

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/python -m tests.trading.performance.decision_shadow_smoke

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
.venv/bin/alembic heads
.venv/bin/alembic upgrade b1000031 --sql > /tmp/wp03.sql
git diff --check
```

真 PostgreSQL 测试必须 0 skip；最终 manifest 只能记录真实输出。

## 8. Blocker、非目标与回滚

### Blocker

- P2 execution spec/permission 未冻结，或其 `frozen_at` 不早于首个 decision trigger；
- quote 与 decision 无法用真实 FK 建立完整 token 关联；
- cashflow、intent、ledger 或 system-net 无法确定性重算；
- deferred ledger balance、并发 cap、重复 economic effect=0 无法由真 PostgreSQL 证明；
- 需要改变既定 action ontology、`HOLD_TO_RESOLUTION`、4%/6%/30% 产品规则。

出现 blocker 必须在唯一 manifest 如实记录；不得新增第二套表、降低约束或偷接真实账户求通过。

### 非目标

- 不扩展 BTC/加密货币短窗、做市、套利、返利驱动或跟单；
- 不实现 `ACTIVE_REVALUE`、动态退出路径模型、学习型 shrinkage/portfolio optimizer；
- 不接私有 CLOB、User WS、Data API、Polygon、relayer、Vault 或真实账户；
- 不创建 canary/live execution envelope，不真实下单；
- 不做 WP-04 label/evaluation/promotion、Admin UI、V1 兼容。

### 回滚

- 数据库：`alembic downgrade b1000021`；0031→0030→0021，各 revision 在 destructive DDL 前检查
  未知下游对象并整次回滚。
- 代码：revert WP-03 提交；WP-02 blind submissions 保留。
- Shadow 账本更正只追加 reversal；不得改旧 transaction/posting。

## 9. 唯一 Completion Manifest 合同

完成后只写：

```text
serve/docs/manifests/wp-03-market-relative-decision-shadow-ledger.md
```

必须包含：

- 精确 changed files；
- `P_EXECUTION_SPEC_MANIFEST` 小节：fixture path/SHA、DB execution spec/permission/release ids+hash、
  frozen_at 与首个 decision trigger 对比；
- 0030/0031 表、`pm_quote_bindings` 强化和数据库约束；
- 五类 semantic + V1 Gold fixture；
- G7A/G7B、ACTION/WAIT/ABSTAIN 与 action 语义、cashflow reconciliation、caps、并发、幂等、
  rollback、ledger balance；
- quote-only 零 AI/零 forecast、shadow fill、replay、system-net 与性能 JSON；
- 全部验收命令真实结果、blocker/non-goal/rollback；
- 删除“恰好 64 位十六进制行”后计算的 manifest SHA-256。

完成时同步：

1. `serve/docs/manifests/README.md` 的 WP-03 行改为 `DONE（待审）` 并写入 manifest SHA；
2. `serve/docs/tasks/README.md` 当前任务仍指向 WP-03，但状态改为 `DONE（待审）`、写入真实证据；
3. 不创建 WP-04 task，不把 WP-03 自行标为 ACCEPTED；等待用户回复“完成”后由审查者验收。
