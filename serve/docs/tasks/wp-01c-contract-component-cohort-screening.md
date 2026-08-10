# WP-01C — Contract、Component、Cohort 与 Screening 完整里程碑

> 状态：**READY**。执行模型：DeepSeek V4 Flash。
> 完成 manifest 固定为
> `serve/docs/manifests/wp-01c-contract-component-cohort-screening.md`。
> 最后更新：2026-08-10 EDT。

## 1. 目标与用户价值

在调用 AI、生成概率或计算 edge 之前，建立可重算的合约语义与全市场选择脊柱：

```text
COMPLETE universe frame
→ prospective cohort → G0 → R0
→ parent opportunity → per-contract G1
→ local component / per-component G2
→ component forecast episode → R1
```

本里程碑解决四个根问题：先理解真实兑付再预测；相关合约形成局部联合边界；所有市场在筛选
前登记；失败的 contract/component 有独立去向且不会被成功 sibling 吞掉。连续完成 4 个
checkpoint，中间不等待用户、不拆 task/manifest。

## 2. 已确认决策

1. `contract_snapshot` 只是 G1 输入 provenance；G1 后唯一语义身份是 `contract_spec_id`。
2. 本期不让模型猜自然语言。Logic 接收 typed candidate，确定性校验并持久化；缺规则、歧义、
   outcome/token 冲突或关键 clarification 缺失均 G1 fail-closed。
3. 每个 spec 有有限 `K_c/R_c`；每个 token 恰有一个完整
   `g_{c,t}:R_c→Decimal`。禁止 float、Python `eval`、动态 SQL 或任意表达式。
4. payout 首版 IR 固定为 canonical lookup truth table；`VOID/PARTIAL` 是 resolution state，
   `OTHER` 必须可判定，`UNKNOWN` 不得作为世界或裁决终态。
5. world schema 只保存有限 `Ω/domain/constraint/factorization/h_c`；不得保存
   `Q/U/μ/V`、市场价、赔率、概率或 edge。超状态预算或无法证明 total mapping 即 G2 fail。
6. 需要共同概率恒等式的 contracts 必须属于同一 component；
   `portfolio_dependency_edges` 只能表示组合 stress，不得冒充联合预测。
7. 所有发现市场先进入 cohort，再执行 R0。R0 只读冻结 allowlist，不解析完整 schema、不形成
   prior/probability/edge，也不能据盘口方向定向搜证据。
8. reject-audit 使用 canonical content hash 派生的确定性抽样，保存算法版本、seed、stratum、
   inclusion probability；输入顺序变化或重试不得改变结果。
9. 只有 R0 `SELECT` 或 audit-selected 才创建 parent opportunity。G1 每 contract 独立；G2 每
   component child 独立，失败 sibling 不终止其他 child。
10. 只有 G2 PASS child 可创建一个 component-level episode；episode 的 contract spec 集必须与
    component version membership 全等。
11. R1 只输出 `reject|shallow|standard|deep`、理由、重判条件和 audit assignment；不调用模型、
    不生成 forecast。`shallow` 只是未来 WP-02 的评价路线，不获得动作权。
12. 所有 Logic 接收明确 objective/strategy/release/policy/version ID；禁止读取 `latest` 或 generic
    settings。网络与 Artifact 读取在 UoW 外；Repository 只做 SQL。

## 3. 依赖与必读

- `/code/pollymarket/v2/AGENTS.md`
- `/code/pollymarket/docs/v2/ARCHITECTURE.md`：权威状态机、Stage 0/1、§4.2、§10.3 A2–A5
- `serve/docs/v2-implementation-contract.md` §2、§4、§6、§11–§14
- `serve/docs/performance-cache-database-design.md` §2、§3、§13–§15
- 已接受 `WP-01B` manifest；Alembic head 必须是 `b1000011`

## 4. 精确文件范围

### 4.1 生产文件（24 个）

```text
serve/app/models/trading/control.py
serve/app/models/trading/semantics.py
serve/app/models/trading/cohort.py
serve/app/models/trading/workflow.py
serve/app/models/trading/__init__.py
serve/app/models/__init__.py
serve/alembic/versions/b1000012_v2_0012_p1a_semantics.py
serve/alembic/versions/b1000013_v2_0013_p1a_cohort_episode.py
serve/app/schemas/trading/__init__.py
serve/app/schemas/trading/semantics.py
serve/app/schemas/trading/workflow.py
serve/app/domain/trading/__init__.py
serve/app/domain/trading/hashing.py
serve/app/domain/trading/payout.py
serve/app/repositories/trading/__init__.py
serve/app/repositories/trading/semantics.py
serve/app/repositories/trading/cohort.py
serve/app/repositories/trading/workflow.py
serve/app/logics/trading/__init__.py
serve/app/logics/trading/contract.py
serve/app/logics/trading/component.py
serve/app/logics/trading/screening.py
serve/app/orchestrator/__init__.py
serve/app/orchestrator/trading_state_machine.py
```

不得新增平行命名、第二套 Base 或通用框架。若共享 test fixture 确需调整，可修改
`serve/tests/trading/conftest.py` 和 `serve/tests/trading/fixtures/poly_fixtures.py`，并在 manifest
逐项说明；其他生产文件禁止修改。

### 4.2 测试与交付

```text
serve/tests/trading/fixtures/p1a_semantics/*.json
serve/tests/trading/fixtures/p1a_fixtures.py
serve/tests/trading/unit/test_v2_payout.py
serve/tests/trading/unit/test_v2_contract_logic.py
serve/tests/trading/unit/test_v2_component_logic.py
serve/tests/trading/unit/test_v2_screening_logic.py
serve/tests/trading/unit/test_v2_trading_state_machine.py
serve/tests/trading/integration/test_v2_0012_semantics_migration.py
serve/tests/trading/integration/test_v2_0013_cohort_episode_migration.py
serve/tests/trading/integration/test_v2_cohort_screening.py
serve/tests/trading/integration/test_v2_semantic_workflow.py
serve/tests/trading/replay/test_v2_p1a_semantics_replay.py
serve/tests/trading/performance/cohort_screening_smoke.py
serve/docs/manifests/wp-01c-contract-component-cohort-screening.md
serve/docs/tasks/README.md
serve/docs/manifests/README.md
```

## 5. Checkpoint A — `b1000012` Contract 与 Component

创建且只创建 8 表：

```text
contract_snapshots
contract_specs
payout_functions
forecast_components
world_schema_versions
forecast_component_versions
forecast_component_contract_specs
portfolio_dependency_edges
```

### 5.1 数据身份与安全 IR

- `contract_snapshots` 必须引用确切 `pm_market_versions.id`、该时点两条
  `pm_token_versions.id`、raw `artifact_objects.id`、question/rules/clarification/resolution source、
  cutoff/timezone、raw outcome mapping 和 content hash；snapshot 不得被下游当 spec FK。
- `contract_specs` 保存 canonical `K_c/R_c`、token/state count、compiler/schema version、status、
  content hash 和 G1 reason；一个 PASS spec 恰引用一个 snapshot。
- `payout_functions` 使用内部 `pm_token_id` + exact token-version ID；`function_ir` 只能是
  `{resolution_state: decimal-string}` 的全量 lookup，测试向量、algorithm hash、content hash 必填。
- `world_schema_versions` 的 IR 固定为：typed finite variables/domains、显式有效 world-state
  assignment、结构约束及 factorization metadata。`h_c` 固定为
  `{world_state_id: resolution_state}` lookup；不得保存可执行代码。
- schema JSON 顶层与递归对象均按 allowlist 校验；出现 `probability, odds, quote, price, edge,
  belief, Q, U, mu, expected_value` 等认知/盘口字段直接拒绝。

### 5.2 PostgreSQL 硬约束

- revision=`b1000012`、down=`b1000011`；固定 DDL，不导入 live ORM。
- snapshot/spec/schema/component version、payout、membership、dependency edge 均 append-only，禁止
  `UPDATE/DELETE`；修正必须新版本/supersede。
- `contract_spec×pm_token` 唯一。deferred constraint trigger 在提交时核验：payout 数量=`|K_c|`、
  token 均属于 snapshot market 与 exact token-version set、每个 truth table key 集=`R_c`、值是有限
  Decimal 且 `0≤payout≤1`。
- component version 非空、恰引用一个属于同 component 的 world schema；
  `component_version×contract_spec` 唯一。
- 每个 member 的 `h_c` 必须覆盖全部有效 world states，结果只属于该 spec 的 `R_c`；schema/domain
  不可满足、时间单调/互斥/条件约束冲突或 state count 超冻结预算时只能保存 G2 FAIL，不能发布
  PASS version。
- dependency edge 两端不同且 canonical 排序去重；任何标为 probability/coherence constraint 的
  cross-component edge 直接拒绝。

### 5.3 必须覆盖的 fixture

1. Bernoulli YES/NO；2. Iran 类时间嵌套；3. Musk 类互斥多结果；4. 条件市场；
5. VOID/PARTIAL payout。每类同时包含正确 truth table 与至少一个歧义/非 total/错误 token 或
非法 `UNKNOWN` 反例。相同输入重复计算 hash 完全一致；Decimal 数值差为 0，不用 float tolerance。

## 6. Checkpoint B — `b1000013` Cohort、Policy Freeze、G0/R0

创建 cohort 4 表：

```text
evaluation_cohorts
universe_memberships
screening_episodes
audit_samples
```

同时对既有 `policy_type_scopes` / `policy_freezes` 做可逆强化，不建替代表：

- 一个 `policy_type` 只能映射一个合法 scope；scope type 使用固定 CHECK。
- freeze 保存 exact policy type/scope/version/content hash/release/frozen_at；同 scope/type/version 唯一，
  不允许层级 fallback。
- cohort 从 `DRAFT→OPEN` 前必须引用 active objective、strategy、release，并具备以下冻结 policy：
  `eligibility, taxonomy, horizon, r0, r1, evidence_coverage, shrinkage, baseline_scoring,
  split_inference, reject_audit`。首个 membership 后不得改 objective/policy/seed；变化新建 cohort。

### 6.1 G0 Objective Gate

Objective typed validator 必须要求：

```text
objective_fn_version, units, decision_horizon, HOLD_TO_RESOLUTION,
discount_policy, capital_charge_policy, NO_ACTION, allowed_actions,
trading/data/LLM/search/infrastructure/human/operational cost scope,
robustness_policy, hard_constraint_ordering
```

字段缺失、objective/strategy/release hash 不一致或未在首个 assignment 前冻结，只能得到
`G0_FAIL/PREDICTION_RESEARCH_ONLY`；不得执行 R0 或创建可交易 opportunity。

### 6.2 Prospective membership 与 R0

- `cohort×market` 唯一；保存 `REST_FRAME|WS_HINT`、首次 observed/ingested、metadata hash、nullable
  confirmed frame/time。REST confirmation 只能从 NULL 原子补为 COMPLETE frame；first-seen 不改写。
- 每个 COMPLETE frame 的 canonical market list 必须由 Artifact Store 在事务外 hydrate 后传入
  `ScreeningLogic.enroll_frame`；本期不从 mutable current 或时间戳猜 frame membership。
- 对验收 cohort 必须满足：`frame markets = confirmed memberships = screening episodes = R0
  dispositions`，重复/空 disposition 均为 0。WS hint 后续 confirmation 幂等。
- R0 输入 DTO 严格 allowlist：metadata、end/resolution time、rule completeness、bid/ask/depth/fee、
  minimum deployable capacity、speed window、estimated research cost/latency、objective/resource envelope。
  不接受 prior/schema/probability/edge 或定向 evidence。
- R0 结果只能 `SELECT|DEFER|REJECT`；DEFER/REJECT 必须有结构化 reason + `recheck_at` 或明确
  recheck condition。audit 分配以冻结 hash 算法生成，保存 `u`、seed hash、stratum、inclusion
  probability、selected、algorithm hash；同输入/乱序/重试完全一致。

## 7. Checkpoint C — Opportunity、G1/G2、Episode 与 R1

创建 workflow 8 表：

```text
decision_opportunities
decision_opportunity_markets
episode_memberships
forecast_episodes
episode_contract_specs
information_snapshots
information_snapshot_items
gate_decisions
```

- `information_snapshots/items` 本期只冻结 G0/R0/G1/G2/R1 的结构化输入、artifact/version/hash；
  它不是 WP-02 的 evidence bundle，也不得含 forecast/quote-derived blind 内容。
- `gate_decisions` append-only；G0/R0 只能绑定 screening，G1/G2 只能绑定对应 opportunity，R1
  只能绑定 episode。每个 Gate 保存 input/policy/version hash、result、reason 和 committed_at。
- R0 SELECT/audit-selected 才可创建 parent；未抽中的 REJECT/DEFER opportunity 数必须为 0。
- parent 对 contracts 逐一创建 G1 child；G1-fail child 独立 terminal。G1-pass specs 以冻结
  dependency 输入确定性划分 components，再 fan-out 每 component 一个 G2 child。
- G2 fail child=`PRE_COMMIT_TERMINAL`、episode 数=0。G2 PASS 后同一 UoW 创建唯一 episode、
  episode membership 与全部 `episode_contract_specs`；deferred trigger 核验其集合与 component
  membership 完全相等。
- episode key=`H(opportunity,component_version,sorted spec set,trigger,cutoff,horizon,strategy,
  objective,experiment_variant)`；重试 effect=0，单 episode 只能引用一个 component version。
- R1 每 episode 恰有一个 route 与 processing disposition。reject 必须有 reason/recheck；audit-selected
  reject、R0 reject audit 和 `RESEARCH_EVAL` 永久
  `action_eligible=false, qualification_eligible=false, capital_evidence_eligible=false`。
- 状态机只能按架构顺序 `G0→R0→G1→G2→R1`；hard fail 后不得越 Gate。此 WP episode 终态只到
  `ROUTED|PRE_COMMIT_TERMINAL`，禁止 `BLIND_COMMITTED`、price reveal、decision、edge 或 action。

## 8. Checkpoint D — 真 PG、Replay 与性能

### 8.1 正确性

1. 0012/0013：literal empty / existing Base 升级、`upgrade→downgrade→upgrade`、固定 DDL、
   metadata parity、未知对象 downgrade preflight、失败整次保持 `b1000011`。
2. 真 PG 绕过 Logic 验证重复、并发、乱序、FK/CHECK、deferred completeness、append-only、非法状态
   和整 UoW rollback；不能以 Pydantic 测试代替 DB 约束。
3. 同一 frozen frame/objective/policies/spec/schema/seed 重放两次，membership、R0/audit、
   opportunity tree、component/episode key、route/disposition 与业务 hash 完全一致。
4. 在 membership、R0、G1、G2 后分别注入 crash；重跑 effect=0。输入顺序变化不得改变 component、
   抽样或 hash。
5. G1/G2 失败 sibling 不影响合法 sibling；WS-first membership 后续 REST confirmation 幂等；
   `RESEARCH_EVAL` 无法进入后续资金/动作状态。
6. 测试禁止公网、model gateway、prompt、forecast submission、decision、下单或 V1 数据。

### 8.2 性能硬门

使用真 PostgreSQL、实际 INSERT/constraint/UoW、有界 QueuePool；禁止 mock、NullPool、sleep-only
或只计纯函数：

1. 50,000 markets prospective enrollment + R0 在 60 秒内完成；missing/duplicate disposition=0。
2. 100 个 component pipeline commits/s 持续 60 秒，覆盖 G1/G2/episode/R1；丢失、重复 effect、
   spec-set mismatch=0。
3. DB pool wait p95≤20ms；记录 stage p50/p95/p99、连接峰值、WAL、RSS、seed、硬件与 commit。
4. 不通过无限 worker/连接数达标；Redis 不承担事实，也不作为 DB 验收替代。

## 9. 验收命令

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests alembic

.venv/bin/pytest -q \
  tests/trading/unit/test_v2_payout.py \
  tests/trading/unit/test_v2_contract_logic.py \
  tests/trading/unit/test_v2_component_logic.py \
  tests/trading/unit/test_v2_screening_logic.py \
  tests/trading/unit/test_v2_trading_state_machine.py

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0012_semantics_migration.py \
  tests/trading/integration/test_v2_0013_cohort_episode_migration.py \
  tests/trading/integration/test_v2_cohort_screening.py \
  tests/trading/integration/test_v2_semantic_workflow.py

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/replay/test_v2_p1a_semantics_replay.py

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/python -m tests.trading.performance.cohort_screening_smoke

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
.venv/bin/alembic heads
.venv/bin/alembic upgrade b1000013 --sql > /tmp/wp01c.sql
git diff --check
```

所有 PostgreSQL 测试必须 0 skip；性能脚本必须输出机器可读 JSON 和硬断言结果。

## 10. Blocker、非目标与回滚

- Blocker：权威设计无法唯一确定 payout/schema、真 PostgreSQL 不可用、DB 无法证明集合全等或
  deterministic replay 失败时标 `BLOCKED`；不得用 JSON 自报、SQLite、TODO 或未来 AI 代替。
- 非目标：AI invocation/model gateway、prompt、自然语言自动解释、prior/evidence bundle、
  `Q/U/μ/V` submission projection、G4–G8、market-relative decision、edge、portfolio、订单、账本、
  Admin UI、常驻 cognition runtime。frame artifact 的调度/水化由 WP-02 runtime 接线；本 WP 的
  Logic 必须接受 exact hydrated frame manifest，禁止从 mutable current 反推。
- 回滚：停止下游 cognition 消费，执行 `alembic downgrade b1000011`（先 0013 后 0012）。若存在
  未知下游 FK/对象则 destructive DDL 前 fail-closed，改走 roll-forward，不手工删事实。

## 11. 完成 manifest

唯一写入：

`serve/docs/manifests/wp-01c-contract-component-cohort-screening.md`

必须记录 24 个生产文件、20 张新表与 2 张强化表矩阵、5 类 semantic fixture/hash、4 个
checkpoint、DB constraint/crash 证据、Replay hash、性能 p50/p95/p99、命令真实输出、blocker、
回滚和去除 SHA 行后的 manifest SHA-256。状态只能写 `DONE（待审）`，不得自行写 `ACCEPTED`。
