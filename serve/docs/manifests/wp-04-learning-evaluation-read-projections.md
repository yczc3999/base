# COMPLETION MANIFEST — WP-04 · 标签审计、五层评价、科学回放、G8 与只读投影

- Work package: `WP-04`
- 状态: **DONE（待审）**
- 完成日期: 2026-08-11 EDT
- 任务合同: `serve/docs/tasks/wp-04-learning-evaluation-read-projections.md`
- Alembic: `b1000040 → b1000041 (head)`（前置 `b1000031`）
- 实现: DeepSeek V4 Flash
- 前置: WP-03 manifest SHA `996869e2…`（accepted manifest `wp-03-market-relative-decision-shadow-ledger.md`）

## 1. 交付范围（修改文件）

### 生产（Checkpoint A —— P3 evaluation spec 与纯确定性函数）

```text
serve/tests/trading/fixtures/p3_learning/p_evaluation_spec_v1.json
serve/tests/trading/fixtures/p3_learning/{bernoulli,multiclass,mean_only,label_conflict,reject_audit,holdout_tamper}.json
serve/tests/trading/fixtures/p3_learning/p3_helpers.py
serve/app/domain/trading/scoring.py
serve/app/domain/trading/inference.py
serve/app/domain/trading/__init__.py
serve/tests/trading/unit/test_v2_scoring.py
serve/tests/trading/unit/test_v2_inference.py
```

### 生产（Checkpoint B —— b1000040 Learning facts）

```text
serve/alembic/versions/b1000040_v2_0040_p3_learning.py
serve/app/models/trading/{settlement,evaluation,audit}.py
serve/app/models/trading/workflow.py              # G8 gate 扩展（GATE_NAMES + 3 CHECK）
serve/app/schemas/trading/{settlement,evaluation}.py
serve/app/repositories/trading/{settlement,evaluation,audit}.py
serve/app/models/trading/__init__.py / app/schemas/trading/__init__.py / app/repositories/trading/__init__.py
serve/tests/trading/integration/test_v2_0040_learning_migration.py
```

### 生产（Checkpoint C —— Label / Evaluation / Replay / G8）

```text
serve/app/logics/trading/{settlement,evaluation,replay}.py
serve/app/handlers/trading/{settlement,evaluation}.py
serve/runtimes/trading/{evaluation,replay}.py
serve/app/orchestrator/trading_state_machine.py     # 仅追加 review_promotion_g8 / g8_approved（ORDER 不插 G8）
serve/app/logics/trading/__init__.py / app/handlers/trading/__init__.py / runtimes/trading/__init__.py
serve/tests/trading/unit/test_v2_evaluation_logic.py
serve/tests/trading/integration/test_v2_label_evaluation.py
serve/tests/trading/integration/test_v2_promotion_gate.py
serve/tests/trading/replay/test_v2_p3_learning_replay.py
```

### 生产（Checkpoint D —— b1000041 Read projections）

```text
serve/alembic/versions/b1000041_v2_0041_read_projections.py
serve/app/models/trading/projection.py
serve/app/repositories/trading/projection.py
serve/app/logics/trading/projection.py
serve/app/models/trading/__init__.py / app/repositories/trading/__init__.py / app/logics/trading/__init__.py
serve/tests/trading/unit/test_v2_projection_logic.py
serve/tests/trading/integration/test_v2_0041_projection_migration.py
serve/tests/trading/integration/test_v2_read_projections.py
serve/tests/trading/performance/evaluation_projection_smoke.py
```

### 透明必要更新（head bump / 新模型跟进）

`app/models/__init__.py`、`tests/trading/integration/test_v2_0001_base_schema_contract.py`（HEAD_REVISION→b1000041）、
`tests/trading/integration/test_v2_alembic_env_integration.py`、`tests/trading/test_v2_model_imports.py`、
`tests/trading/test_v2_trading_foundation_models.py`（99→104 表）。

未修改 V1、Admin、settlement 之外无越界；未创建账户/钱包/权限/私有 CLOB/真实下单路径（属 WP-05）。

## 2. P_EVALUATION_SPEC_MANIFEST

- evaluation fixture: `serve/tests/trading/fixtures/p3_learning/p_evaluation_spec_v1.json`
- raw fixture SHA-256: `244887c27f2b1c8a6fc56b5483e308e9469bf2fde5cb9af1d17cf7d0541b77a4`
- spec `content_hash`（删除哈希行后的 canonical hash）: `a7d89c1150b702cdad00686391d1e93271b98d29c3dac28c704c5a9ba9d143f2`
- frozen_at: `2026-08-10T00:00:00Z` → **严格早于首个 evaluation assignment（2026-08-11 测试运行期）** ✓
- policy hashes（`spec_policy_hashes()`）：
  - `label_policy_hash`: `4ff6f251915007150ff8fb4a1558baeb885d1ed62d2c5a5fde8e31e614567197`
  - `target_policy_hash`: `bdcd2cf91793c8d41eee58df932bc7404dbdd1c5270d278a27fafdfd093c906a`
  - `baseline_policy_hash`: `670246d7e408e32f4442650e1eca928de484f242c67f5359dde212ecaff5bd9b`
  - `split_policy_hash`: `ddd31b216926db79d838e9f3129cf3ac4a3ef5c08038009f7973f443ef60b178`
  - `bootstrap_policy_hash`: `ba19e6fa54d73cb8f38f12fa750f4ea1871bb9ee8842678692d4fe2a409332fb`
  - `metric_policy_hash`: `9e4653f4a55ee719634d0398fc0007640451a9950ae48abf484d816d181db7a6`
  - `promotion_policy_hash`: `1acfc1d21b1b15c49677deede45477ba42dcb66211c38a2e8db6fb80aa0eb0e7`
- 冻结语义（六种 policy）：
  - label：`pending→provisional→disputed|final_admissible|final_excluded`；final_admissible 要求 resolution_state∈R_c 且全部 token payout 可由冻结 h/g 重算并等于 actual cashflow；final_excluded 必填 exclusion_reason；disputed 必填 conflict_set；settlement 冲突固定 `SETTLEMENT_CONFLICT→disputed`。
  - target：互补 YES/NO=Bernoulli、完整互斥穷尽=multiclass、不同 partial/scalar payout=各一个 mean_only；禁按 token 数放大样本量。
  - baseline：权威 quote checkpoint 当时基线价；缺失/陈旧显式 excluded，禁未来 quote 回填。
  - split：cluster 结果未知时分配 train|validation|forward_holdout；一个 cluster 永不跨 split；holdout 开封/重分组/改 policy 使候选 metric run 失效。
  - bootstrap：resolution-cluster + time-block bootstrap，固定 seed，点估计+95%CI+n_eff；不得按裸 market 数宣称显著。
  - promotion：capital promotion 恒 fail closed（authorized_capital=0）；strategy approval 只未来 shadow；任一 hard guardrail 失败即拒绝；低功效继续 shadow 不 APPROVED。
- 冻结顺序证明：`frozen_at` < 首个 assignment；policy hashes 与测试快照一致（`test_v2_scoring.py`/`test_v2_inference.py` 断言）。

## 3. 已冻结的工作逻辑

1. **audit_label_revision**（SettlementLogic）：确定性 label compiler，零 AI。读取冻结 contract spec/payout IR/token cashflow；证据 artifact 必须存在且 hash 可验；冲突（规则/source/mapping/cashflow 任一）→ `SETTLEMENT_CONFLICT`→disputed fail-closed；无证据保持 pending。revision 只 INSERT，supersede 同 contract+version 连续，每 contract 同时一个 current。
2. **create_cluster / split integrity**：cluster 创建时 outcome 未知并分配 split；membership 追加后不可搬移；cluster 不跨 split；holdout tamper 检测。
3. **score_observation**：只接受 final_admissible（`proper_loss_guard`）；权威 baseline/quote、exact blind submission（禁调用方替换）、target/split/algorithm hash；同 `submission×target×label_version×metric_id` 唯一；Brier/log loss/MSE 与 `ΔLoss`（delta_loss）。
4. **run_metric**：固定 cohort query/strategy/release/label versions/split/time blocks/code/config/seed；`n_market/n_episode/n_resolution_cluster/n_eff`；五层结果（Prediction/Selection/Edge/Portfolio/Execution）+ cluster_bootstrap 95%CI + artifact hash；COMPLETED 后不可改；Portfolio 缺 operating-cost/ledger/action-set → `not_evaluable` 不 0 填充。
5. **五层不互相替代**：全 forecast-set 与 selected action-set 分开报告；prediction loss 是 guardrail 不是 PnL 替代。
6. **promote**：capital promotion 恒拒（DB CHECK `NOT(capital AND APPROVED)` + Logic 前置）；strategy approval 只创建未来 shadow assignment（future_effective_at），不回写历史 cohort/assignment；引用单一 COMPLETED metric run + 未污染 forward holdout + P3 evidence manifest；train/validation-only、inadmissible label、篡改 holdout → REJECTED。
7. **replay_original/new_code/ablation/error_review_selection**：只读原 artifact/snapshot/事实，写新 replay/ablation/metric artifact；同 manifest+code+seed 两次 hash 全等；新 code/variant 写新 run 不覆盖原事实；crash 任一点无半条有效证据；未来 label/quote taint=0；top-loss/regret+随机成功按冻结 seed 入 review；root-cause taxonomy 只允许架构集合。
8. **G8（review_promotion_g8/g8_approved）**：绑 `(G8, metric_run, metric_run_id)`，不在 episode 线性链；DB deferred trigger 强约束 target 必须 COMPLETED 且 release==version_manifest；policy_hash 必须等于 spec promotion_policy_hash；只 future-effective。
9. **投影（0041）**：五张 read model 每行带 as_of/source_high_watermark/projection_version/projection_hash；可删除重建、重建 hash 精确相同、重复/乱序 effect=0；keyset `(as_of,id)` 无 OFFSET/深页 COUNT(*)/大 JSON 默认加载；account_risk 仅 shadow namespace；投影不是事实源（资金/permission/G8/label/账本/审计详情不读投影）。
10. **quote-only 零 AI**：本 WP 全部确定性 label compiler/metric/replay/projection 零 provider 调用、零 key。

## 4. 命令与真实结果

```bash
cd /code/pollymarket/v2/serve

python3 -m compileall -q app runtimes tests alembic
# exit 0

.venv/bin/pytest -q \
  tests/trading/unit/test_v2_scoring.py tests/trading/unit/test_v2_inference.py \
  tests/trading/unit/test_v2_evaluation_logic.py tests/trading/unit/test_v2_projection_logic.py
# 57 passed in 0.58s

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0040_learning_migration.py \
  tests/trading/integration/test_v2_0041_projection_migration.py \
  tests/trading/integration/test_v2_label_evaluation.py \
  tests/trading/integration/test_v2_promotion_gate.py \
  tests/trading/integration/test_v2_read_projections.py \
  tests/trading/replay/test_v2_p3_learning_replay.py
# 49 passed in 33.90s（0 skip）

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/python -m tests.trading.performance.evaluation_projection_smoke
# hard_assertions=PASS；输出 /tmp/pm_v2_perf_smoke_4.json

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
# 1361 passed, 8 warnings in 147.32s（0 skip，0 failure）

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
# 1572 passed, 8 warnings in 153.67s（0 skip，0 failure）

.venv/bin/alembic heads
# b1000041 (head)

.venv/bin/alembic upgrade b1000041 --sql > /tmp/wp041.sql
# 6099 lines；secret hits=19（与 b1000040 基线一致，全为列/表名 false-positive，无新增）

git diff --check
# exit 0
```

## 5. 性能（真 PostgreSQL、有界 evaluation pool=3+1、真实 Logic/Repository/UoW/constraints）

`/tmp/pm_v2_perf_smoke_4.json`，`hard_assertions=PASS`：

| 门 | 结果 | 门槛 |
|---|---:|---:|
| keyset 列表（100,006 条 source facts）p50/p95/p99 | 1.93ms / 2.50ms / 2.93ms | p95≤500ms、p99≤1s |
| keyset 最大响应 | 193,001 B | ≤200KiB |
| keyset SQL plan | Index Scan（(as_of,id) keyset） | — |
| keyset pool wait p95 | 0.091ms | ≤20ms |
| 单条完整决策 replay p50/p95/p99 | 37.9ms / 57.9ms / 57.9ms（5 次） | p95≤5s、p99≤15s |
| replay pool wait p95 | 0.134ms | ≤20ms |
| projection rebuild lost / duplicate | 0 / 0 | 全 0 |
| rebuild hash identical | True（两次重建全等） | 精确相同 |
| metric workload throughput（10s，4 worker） | 470 q/s（复跑 469.9） | 固定 workload 报告 |
| metric workload 10s 窗口 | [470.1] | 报告 |
| metric workload WAL / RSS / CPU | 335,406,936 B / 507,144 KiB / 16.473 s | 报告 |
| metric workload pool wait p95 | 0.087ms | ≤20ms |
| 有界 pool | pool_size=3, max_overflow=1（peak_checked_out=4） | 3+1 |
| env | git b1000041、PostgreSQL 18.4、16 CPU | — |

临时测试/性能数据库残留：`0`。

## 6. 数据库约束

- 0040：14 张 learning 事实表（settlement/evaluation/audit）+ gate G8（绑 metric_run，COMPLETED+release 一致 deferred trigger）+ resolution_labels 状态机/supersede/single-current guard + score_target_memberships 权重归一/token 双计 deferred trigger + metric_runs lifecycle guard + promotion `NOT(capital AND APPROVED)` CHECK。
- 0041：5 张只读投影表（每行 as_of/watermark/version/hash）+ keyset `(as_of,id)` 索引 + immutable 投影行 guard（重建=TRUNCATE+重插）。
- `alembic check` modeled drift=0；0040/0041 均支持 literal-empty roundtrip 与 downgrade fail-closed（未知对象 preflight）。

## 7. 审查整改记录

- 初次全量回归发现 `test_v2_0040_learning_migration.py::test_tables_indexes_and_alembic_drift` 头硬编码失配：`alembic check` 要求目标库在 head（b1000041），故恢复 `upgrade head` + `HEAD_REVISION=b1000041`。
- `test_v2_0001_base_schema_contract.py` `HEAD_REVISION` b1000040→b1000041；`test_v2_trading_foundation_models.py` 表集 99→104（补 5 张投影表）。
- 以上均为 0041 引入新 head 的机械跟进（与 WP-03 合并时同一模式）。

## 8. P3_COMPLETION_MANIFEST（逐项对应 ARCHITECTURE §10.5 P3 的 7 条 DoD）

1. **reject-audit 设计加权可重建**：accepted/rejected/deferred/failed/expired/superseded 全部进入 cohort；reject reason 的 inclusion probability/seed/未完成状态与设计加权可重建（`horvitz_thompson_weight`/`ht_estimate`），coverage/机会成本可报告；无 audit 样本只报 unknown。证据：`test_v2_label_evaluation.py`（reject_audit fixture）、`test_v2_evaluation_logic.py`。
2. **label audit fail closed + canonical targets + 五层不互相替代**：label 五态/冲突/wrong payout/wrong mapping/证据缺失/final update/delete 全 fail closed；canonical score targets（Bernoulli/multiclass/mean_only）+ component 全投影 + cluster-normalized 权重 + contract-type `ΔLoss` 可重算；Prediction/Selection/Edge/Portfolio/Execution 分开存、分开报告。证据：`test_v2_label_evaluation.py`、`test_v2_0040_learning_migration.py`。
3. **split/holdout 隔离 + time-block/n_eff/stopping/multiple-testing 冻结**：resolution cluster 不跨 train/validation/forward；time-block inference、`n_eff`（Kish `total²/Σ n_c²`）、stopping/multiple-testing rule 已冻结进 spec（split_policy/bootstrap_policy）；holdout 修改 fixture 使 metric/promotion 失效。证据：`holdout_tamper.json` + `test_v2_label_evaluation.py`/`test_v2_promotion_gate.py`。
4. **top-loss/regret + 随机成功审查、root-cause taxonomy、冻结 bundle ablation 可重放**：`error_review_selection` 按冻结 seed 入 review；taxonomy 只允许架构集合；`ablation` 冻结 bundle。证据：`test_v2_evaluation_logic.py`、`test_v2_p3_learning_replay.py`。
5. **portfolio dependency stress、机会成本、净敞口/CVaR/资本天数、替代机会比较可重算**：`portfolio_summary`（trading PnL + operating cost → system net + drawdown + CVaR + capital-days，缺失 `not_evaluable`）；组件/token 大小不放大主指标权重（权重归一 deferred trigger）。证据：`test_v2_evaluation_logic.py`、`test_v2_projection_logic.py`。
6. **资金评价主门 = selected action-set 的 forward system-net + 风险；全/selected ΔLoss/calibration 分开作为认知 guardrail**：`run_metric` 固定 cohort/strategy/release/split/time blocks/seed；五层结果 + CI + artifact hash；`score_observation_guardrails` 分开 full vs selected。证据：`test_v2_label_evaluation.py`、`test_v2_evaluation_logic.py`。
7. **manifest 记录 P2 引用、cohort/split/label/metric code、fixture、命令、报告/log hashes**：本 manifest §2（spec+policy hashes）、§4（命令/结果）、§5（性能）、§6（DB 约束）、P2 引用（前置 WP-03 `996869e2…`）。测试与性能全部 0 skip/0 fail；无未通过项。

P3 七条 DoD 全部满足；无硬项失败，不阻塞 shadow qualification。

## 9. Blocker / 非目标

无 P0/P1 blocker。非目标（任务 §8）：不修改 WP-03 在线 G7B、不做 P4 ensemble/challenger/bias 激活、不做 P-stability/P-execution-readiness/canary/live、不接 Vault/account/private CLOB/User WS/Data API/Polygon/relayer/redeem、不建设 Admin Controller/API/frontend、不改 V1、不运行新的 AI label auditor。

## 10. 回滚

- 数据库：`alembic downgrade b1000041`；0041 只删可重建 projections，0040 在未知下游对象存在时 fail closed；append-only label/metric/promotion 先导出 artifact manifest 再回滚。
- 代码：revert WP-04 提交；WP-03 forecast/decision/shadow/ledger 事实完整保留。
- promotion 回滚只追加新 `promotion_decision`，不修改旧记录或历史 assignment。

## 11. Manifest SHA-256

口径：删除本文件中“恰好 64 位十六进制”的哈希行后计算。

```text
1d8a9083b26edde31b0d4e5eb586d3cc38afef701d2aa95c0778148fa91ccf9b
```

```bash
sed -e '/^[0-9a-f]\{64\}$/d' \
  serve/docs/manifests/wp-04-learning-evaluation-read-projections.md | sha256sum
```
