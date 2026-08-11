# WP-04 — 标签审计、五层评价、科学回放、G8 与只读投影

> 状态：**ACCEPTED（审查通过）**
> 前置：`WP-03` 已接受；Alembic head=`b1000031`  
> 执行模型：DeepSeek V4 Flash  
> 唯一完成交付：`serve/docs/manifests/wp-04-learning-evaluation-read-projections.md`  
> 最后更新：2026-08-11 EDT

## 1. 目标与用户价值

把已产生的 forecast、decision、shadow execution、ledger 和全部 operating cost 转成**可审计、
可重放、不会污染 forward holdout 的科学证据**，回答五个彼此不可替代的问题：

1. 预测概率是否可靠；
2. R0/R1/ABSTAIN 是否漏掉了有价值机会；
3. 预测优势是否被可执行价格转换成正 edge；
4. action set 在统一风险预算下是否产生正的 system net profit；
5. shadow execution 是否吞掉优势。

本任务同时建设后台将来读取的五张可重建 projection，但不建设 Admin API/UI。任何 projection、
metric 或 PnL 都不得反向修改 label、forecast、decision、ledger 或 permission 事实。

## 2. 已确认决策

1. **一条路线不变**：新闻类、HOLD_TO_RESOLUTION、shadow-only；本任务不引入做市、短窗、套利、
   ACTIVE_REVALUE、私有 CLOB 或真实资金。
2. **标签先审计后计分**：状态机固定为
   `pending → provisional → disputed | final_admissible | final_excluded`。状态变化追加新 revision，
   不覆盖旧 label。平台 resolved event 只生成候选证据，不能直接成为最终标签。
3. **结算冲突 fail closed**：规则、resolution source、token mapping 或实际 cashflow 任一冲突，固定
   `SETTLEMENT_CONFLICT → disputed`；没有证据时保持 pending。WP-04 不接 WP-06 链上 redeem。
4. **经济事实与 proper loss 分离**：只有 `final_admissible` 进入 prediction proper-loss；
   disputed/excluded 仍保留 cohort、selection、edge、execution、ledger 与经济现金流报告。
5. **canonical score target**：互补 YES/NO 是一个 Bernoulli target；完整互斥穷尽集合是一个
   multiclass target；不同 partial/scalar payout function 各一个 target。禁止按 token 数放大样本量。
6. **唯一主聚合**：先在 `forecast_episode × resolution_cluster` 内对 canonical targets 等权，
   再使每个 resolution cluster 在同一 horizon/time block 总权重为 1。重复 episode 使用冻结的
   episode-weight policy；替代权重只能是 challenger。
7. **loss 方向唯一**：`ΔLoss = Loss_candidate - Loss_baseline`，越小越好。Bernoulli 用 Brier 为
   primary、log loss 为 tail guardrail；multiclass 用 multiclass Brier；mean-only payout 固定平方误差。
8. **五层互不替代**：Prediction / Selection / Edge / Portfolio / Execution 分开存、分开报告。
   资金证据主门是 selected action-set 的 forward 全成本 system-net 与风险；预测 loss 是 guardrail，
   不是 PnL 的替代品。
9. **split 先于结果冻结**：resolution cluster 在未知结果时分配到
   `train | validation | forward_holdout`；一个 cluster 永不跨 split。holdout 开封、重分组、修改
   strategy/metric/label policy 都使候选 metric run 失效，不能就地修补。
10. **实验只改一个变量**：champion/challenger 必须共享同 decision opportunity、component/spec set、
    trigger/cutoff/horizon、prior 和 evidence bundle；除预注册变化字段外 manifest 全等。
11. **G8 只 future-effective**：promotion 引用不可变 metric run + evidence manifest。WP-04 最多批准
    新 strategy 在**未来 shadow episode**生效；`authorized_capital` 仍为 0。缺少 P-stability、
    P-execution-readiness 或 canary evidence 时，capital promotion 必须固定拒绝。
12. **投影不是事实源**：0041 的五张 read model 可删除重建；G8、权限、账本、标签与回放永远查询
    append-only facts。不得从 projection 生成交易或批准 promotion。
13. **不使用 AI 猜标签**：明确 resolution evidence 走确定性 label compiler；歧义保留 disputed。
    本 WP 不新增 AI provider 调用，也不需要任何 key。

## 3. 依赖与必读

- `/code/pollymarket/docs/v2/ARCHITECTURE.md`：Stage 7、§4.4–4.5、§10.5 P3 DoD；
- `serve/docs/v2-implementation-contract.md`：§4、§8、§11–14；
- `serve/docs/ai-observability-replay-design.md`：原样/模型/下游三种回放与未来信息隔离；
- `serve/docs/performance-cache-database-design.md`：keyset/read projection/SLO；
- `serve/docs/tasks/wp-03-market-relative-decision-shadow-ledger.md` 与 accepted manifest；
- 当前 `b1000031` ORM、migration、UoW、Artifact Store、Outbox 和 append-only 约定。

禁止从 V1 prompt/schema/DB 推断 V2 标签或评分。V1 数据只能作为显式外部 fixture。

## 4. 内部 checkpoint 与精确文件范围

本里程碑连续完成 A→D，只生成一个 completion manifest。

### A — P3 evaluation spec 与纯确定性函数

```text
serve/tests/trading/fixtures/p3_learning/p_evaluation_spec_v1.json
serve/tests/trading/fixtures/p3_learning/{bernoulli,multiclass,mean_only,label_conflict,reject_audit,holdout_tamper}.json
serve/tests/trading/fixtures/p3_learning/p3_helpers.py
serve/app/domain/trading/evaluation_policy.py
serve/app/domain/trading/p_evaluation_spec_v1.json
serve/app/domain/trading/scoring.py
serve/app/domain/trading/inference.py
```

fixture 冻结 label policy、target canonicalization、baseline convention、horizon/episode weight、split、
cluster/time-block bootstrap、`n_eff`、multiple-testing/stopping、五层 primary/guardrail 与 promotion policy。
内容 hash 和 `frozen_at` 必须早于首个 evaluation assignment。

> 审查批准的范围内必要扩展：生产运行时不得依赖 `tests/` 被打包，因此修复提交
> `8ff2067f10779921970ef76eef7e9e11c7c0da18` 增加 deployment-owned、启动即自校验 content hash 的
> `evaluation_policy.py + p_evaluation_spec_v1.json`；生产副本与冻结测试 fixture 字节全等。该扩展不改变
> policy，只关闭部署缺少 tests 目录时的错误依赖。

### B — `b1000040_v2_0040_p3_learning`

```text
serve/alembic/versions/b1000040_v2_0040_p3_learning.py
serve/app/models/trading/{settlement,evaluation,audit,workflow}.py
serve/app/schemas/trading/{settlement,evaluation}.py
serve/app/repositories/trading/{settlement,evaluation,audit}.py
```

0040 只创建以下 frozen facts；不得导入 live ORM 生成 DDL：

```text
resolution_labels
resolution_clusters
resolution_cluster_memberships
score_targets
score_target_memberships
score_observations
experiments
experiment_variants
challenger_variants
metric_runs
error_reviews
ablation_runs
promotion_decisions
```

`serve/app/models/trading/audit.py` 只增加 `replay_runs`；不得顺手创建未来的 workflow-event、
external-call 或 alert 系统。0040 同时把 `gate_decisions` 扩展为
`G8 + target_kind=metric_run`，并由 DB 校验 target/metric/policy/release 一致。

### C — Label / Evaluation / Replay / G8

```text
serve/app/logics/trading/{settlement,evaluation,replay}.py
serve/app/handlers/trading/{settlement,evaluation}.py
serve/runtimes/trading/{evaluation,replay}.py
serve/app/orchestrator/trading_state_machine.py
```

Handler 只解析一个 typed event、调用一个 Logic/UoW、返回 completion。Runtime 使用 P3 独立 pool；
不得复用 execution worker，也不得直接更新 strategy、permission 或历史事实。

### D — `b1000041_v2_0041_read_projections`

```text
serve/alembic/versions/b1000041_v2_0041_read_projections.py
serve/app/models/trading/projection.py
serve/app/repositories/trading/projection.py
serve/app/logics/trading/projection.py
```

固定五张 projection：

```text
ops_health_current
pipeline_funnel_hourly
account_risk_current          # WP-04 仅 shadow portfolio namespace；不创建账户/钱包
provider_cost_daily
latest_chain_summary
```

允许同步更新现有 `__init__.py` 显式 exports、`app/models/__init__.py` 与任务/manifest 索引；禁止其他
生产文件。若实现必须越过此清单，先在任务文档登记原因，不能静默扩展。

## 5. 数据库与状态机不变量

### 5.1 Label

- label identity=`contract_spec_id + label_key + version_no`，每个 revision 引用冻结 contract spec、
  resolution evidence Artifact、raw outcome、resolution state、token cashflow、policy/code hash、
  `supersedes_id` 与 auditor identity；证据 artifact 必须存在且 hash 可验。
- revision 只 INSERT；UPDATE/DELETE 全拒绝。supersedes 必须同 contract、version 连续且前一状态允许；
  一个 contract 同时最多一个 current revision。
- `final_admissible` 要求 `resolution_state ∈ R_c`、所有 token payout 可由冻结 `h/g` 重算且等于
  actual cashflow；`final_excluded` 必填 exclusion reason；disputed 必填冲突集合。
- market closed/resolved、标签存在或 PnL 非零都不能绕过上述 Gate。

### 5.2 Cluster / split / target

- resolution cluster 创建时 outcome 未知，绑定 version、time block 与唯一 split；membership 追加后不可
  搬移。相同 contract/spec 不得属于两个 active cluster version，cluster 不跨 split。
- score target 必须覆盖 episode/component 的 exact canonical set；target type 与 payout 类型一致；
  Bernoulli canonical side、multiclass members、mean-only payout 不得混型或重复。
- target/member 权重用 NUMERIC 定点数，总和/cluster normalization 由 deferred trigger 验证；
  token/component 大小不得放大 cluster 权重。

### 5.3 Score / metric

- score observation 必须引用 `final_admissible` label、exact blind submission/decision、target、baseline
  quote/policy、split 和算法 hash；同一 `submission × target × label_version × metric_id` 唯一。
- market baseline 缺失/陈旧时按冻结 policy 显式 excluded，禁止未来 quote 回填。
- metric run 固定 cohort query、strategy/release、label versions、split、time blocks、code/config、seed、
  `n_market/n_episode/n_resolution_cluster/n_eff`、五层结果/CI 与完整 artifact hash；COMPLETED 后不可改。
- operating-cost period 与 ledger/action set 不完整时 Portfolio 指标必须 `not_evaluable`，不能用 0 填充。

### 5.4 Experiment / replay / promotion

- experiment 在 assignment 前冻结 hypothesis、唯一变化项、primary metric、guardrails、sample/time/stopping/
  rollback。challenger 与 champion 的 immutable input manifest 除唯一变化字段外必须全等。
- replay 只读原 artifact/snapshot/事实，输出新 replay/ablation/metric artifact；不能写回原 episode、
  submission、decision、execution、label 或 ledger。相同 manifest+code+seed 重跑 hash 全等。
- top-loss/top-regret 与随机成功样本都必须按冻结 seed 入 review；root-cause taxonomy 只允许架构定义集合。
- promotion 必须引用单一 COMPLETED metric run、未污染 forward holdout、P3 evidence manifest 与 from/to；
  变更 objective、同时换多因素、引用 train/validation-only 结果、inadmissible label 或篡改 holdout 一律拒绝。
- WP-04 的 capital promotion 恒 fail closed；strategy approval 仅创建未来生效的 shadow decision，不回写
  历史 cohort/assignment，不改变 `authorized_capital=0`。

### 5.5 Projection

- 每行保存 `as_of/source_high_watermark/projection_version/projection_hash`；consumer 幂等，乱序/重复 event
  effect=0。删除全部 projection 后从事实重建，排序内容 hash 必须全等。
- keyset 固定 `(sort_ts,id)` 或域内等价复合键；filter/sort 使用 typed allowlist；禁止 OFFSET、深页
  `COUNT(*)`、raw artifact/prompt/book levels/大 JSON 默认加载。
- 资金、permission、G8、label、账本与审计详情不读 projection；projection lag 只降级页面，不改变业务。

### 5.6 通用约束

- 事实表修正只用 supersede/reversal；FK 默认 RESTRICT；所有时间 TIMESTAMPTZ UTC、金额/概率 NUMERIC；
- 0040/0041 transactional、advisory lock、offline-safe、固定 literal DDL；downgrade 在任何 DROP 前检测未知
  relation/index/trigger/dependency，发现即整次回滚；
- 所有重试有全局 idempotency claim；事实写、workflow event、artifact binding 与 outbox 同 UoW；
- 不存在 `canary/live` permission、真实 order、vault secret 或链上对象。

## 6. 必须实现的确定性评价

1. Bernoulli Brier/log loss、multiclass Brier/log loss、mean squared payout loss；概率 0/1 的 log loss
   使用冻结 epsilon，不临场选择。
2. `ΔLoss` paired comparison、calibration intercept/slope、sharpness、tail loss；输出全 forecast-set 与
   selected action-set 两组。
3. reject-audit Horvitz–Thompson（或 evaluation fixture 冻结的等价设计加权）coverage/机会成本，保存
   inclusion probability；无 audit 样本时只报告 unknown。
4. resolution-cluster + time-block bootstrap，固定 seed，输出点估计/95% CI 与 `n_eff`；不得按裸 market
   数宣称显著。
5. Edge 分桶单调性、相对 no-action regret、blind→decision 延迟侵蚀；Portfolio 的 trading PnL、全部
   operating cost、system net、drawdown、CVaR、capital-days；Execution 的 fill/partial/reject/fee/slippage。
6. 同一 condition 全 token 一起结算；rebate 本期固定 0，不能用未到账奖励让 system net 转正。
7. promotion evaluator：Prediction/Selection/Edge/Portfolio/Execution 任一 hard guardrail 失败即拒绝；
   低功效继续 shadow，不把“趋势不错”写成 APPROVED。

## 7. 测试文件与必须证明的事实

新增/修改：

```text
serve/tests/trading/unit/test_v2_{scoring,inference,evaluation_logic,projection_logic}.py
serve/tests/trading/integration/test_v2_{0040_learning_migration,0041_projection_migration}.py
serve/tests/trading/integration/test_v2_{label_evaluation,promotion_gate,read_projections}.py
serve/tests/trading/replay/test_v2_p3_learning_replay.py
serve/tests/trading/performance/evaluation_projection_smoke.py
```

至少证明：

1. label 五态、冲突、wrong payout、wrong token mapping、证据缺失、final update/delete 全 fail closed；
2. final_excluded/disputed 不进入 proper-loss，但经济/selection/edge 记录仍存在；
3. Bernoulli/multiclass/mean-only golden 数值精确；未来 baseline、token 双计、cluster 权重放大被拒绝；
4. accepted/rejected/deferred/failed/expired/superseded 全部有 disposition；reject-audit 设计加权可重算；
5. split crossing、结果揭晓后分组、holdout tamper、同一数据发现并激活、无效 multiple-test 全拒绝；
6. champion/challenger exact paired，唯一变化字段以外任何 hash 差异使实验无效；
7. full forecast-set 与 selected action-set 的五层报告同时生成，prediction loss 与 system net 不能互相替代；
8. capital promotion 恒拒绝；合格 strategy promotion 只对未来 shadow assignment 生效；
9. 原样 replay 两次 hash 全等；新 code/variant 写新 run，不覆盖原事实；未来 label/quote taint=0；
10. projection 重复/乱序 effect=0；清空重建 hash 相同；keyset 无重/漏，非法 filter/sort 拒绝；
11. downgrade `0041→0040→0031` roundtrip、未知对象 fail closed、ORM modeled drift=0；
12. crash 在 artifact/metric/promotion/outbox 任一点都不产生半条有效证据。

## 8. 性能硬门

真 PostgreSQL、有界 evaluation pool=`3+1`；使用真实 Logic/Repository/UoW/constraints，不能直接批量
INSERT 冒充业务路径：

- 在至少 100,000 条 read-model source facts 上，keyset 列表 p95≤500ms、p99≤1s，响应≤200KiB；
- 单条完整决策的 deterministic replay p95≤5s、p99≤15s；
- DB pool wait p95≤20ms，projection rebuild lost/duplicate=0，rebuild hash 精确相同；
- 固定 metric workload 报告 throughput、WAL、RSS、CPU/连接峰值和 10s 窗口；不虚构未冻结收益门槛；
- 输出 `/tmp/pm_v2_perf_smoke_4.json`，含 seed、实际 git commit、数据规模、p50/p95/p99、SQL plan 摘要、
  hard assertions；临时数据库必须清理为 0。

## 9. 验收命令

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app runtimes tests alembic

.venv/bin/pytest -q \
  tests/trading/unit/test_v2_scoring.py \
  tests/trading/unit/test_v2_inference.py \
  tests/trading/unit/test_v2_evaluation_logic.py \
  tests/trading/unit/test_v2_projection_logic.py

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0040_learning_migration.py \
  tests/trading/integration/test_v2_0041_projection_migration.py \
  tests/trading/integration/test_v2_label_evaluation.py \
  tests/trading/integration/test_v2_promotion_gate.py \
  tests/trading/integration/test_v2_read_projections.py \
  tests/trading/replay/test_v2_p3_learning_replay.py

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/python -m tests.trading.performance.evaluation_projection_smoke

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
.venv/bin/alembic heads
.venv/bin/alembic upgrade b1000041 --sql > /tmp/wp04.sql
git diff --check
```

真 PostgreSQL 必须 0 skip；offline SQL secret hits=0；最终 manifest 只记录真实输出。

## 10. P_EVALUATION_SPEC_MANIFEST 与 P3_COMPLETION_MANIFEST

唯一 completion manifest 内必须有两个固定小节（不是额外 manifest）：

### P_EVALUATION_SPEC_MANIFEST

记录 evaluation fixture 路径/SHA、frozen_at、首次 assignment、label/target/baseline/split/bootstrap/
promotion policy hashes、代码/release/Alembic 版本与冻结顺序证明。

### P3_COMPLETION_MANIFEST

逐项对应 `/code/pollymarket/docs/v2/ARCHITECTURE.md` §10.5 P3 的 7 条 DoD，记录 WP-03/P2 manifest
引用、cohort/split/label/metric/replay/projection hashes、测试/性能/SQL plan、全部未通过项。任一硬项失败，
WP-04 不得标完成，也不得进入 shadow qualification。

## 11. Blocker、非目标与回滚

### Blocker

- resolution evidence 无法绑定冻结 contract/payout 或无法确定 token cashflow；
- score target 无法 canonicalize，cluster/split/holdout 无法在 DB fail closed；
- metric run 缺 operating cost/ledger/action-set lineage；
- promotion 需要修改既有 objective/action ontology/4%/6%/30% 产品规则；
- 需要私有 CLOB、钱包、链上 finality 或真实资金才能通过。

遇到 blocker 如实写唯一 manifest 并停止；不得降低 label admissibility、回填未来数据、删样本或绕过
holdout 求通过。

### 非目标

- 不修改 WP-03 在线 G7B，不实现学习型 portfolio optimizer；本期只做离线 stress/机会成本评价；
- 不做 P4 ensemble/challenger/bias 激活，不做 P-stability/P-execution-readiness/canary/live；
- 不接 Vault/account/private CLOB/User WS/Data API/Polygon/relayer/redeem；
- 不建设 Admin Controller/API/frontend，不改 V1；不运行新的 AI label auditor。

### 回滚

- 数据库：`alembic downgrade b1000031`；0041 只删可重建 projections，0040 在未知下游对象存在时
  fail closed；append-only label/metric/promotion 先导出 artifact manifest 再回滚；
- 代码：revert WP-04 提交；WP-03 forecast/decision/shadow/ledger 事实完整保留；
- promotion 回滚只追加新 `promotion_decision`，不修改旧记录或历史 assignment。

## 12. 交付约束

完成时只生成 `serve/docs/manifests/wp-04-learning-evaluation-read-projections.md`，同步两个 README 索引。
manifest 必须列出修改文件、实现事实、全部真实命令/输出、P_EVALUATION_SPEC_MANIFEST、
P3_COMPLETION_MANIFEST、性能 JSON、blocker/非目标/回滚、路径与 SHA-256。不得自建 WP-05；用户回复
“完成”后由审查者直接复验、范围内修复、接受并创建下一任务。
