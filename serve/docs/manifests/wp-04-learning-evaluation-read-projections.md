# COMPLETION MANIFEST — WP-04 · 标签审计、五层评价、科学回放、G8 与只读投影

- Work package: `WP-04`
- 状态: **ACCEPTED（审查者已直接关闭范围内 P0/P1）**
- 完成日期: 2026-08-11 EDT
- 任务合同: `serve/docs/tasks/wp-04-learning-evaluation-read-projections.md`
- Alembic: `b1000040 → b1000041 (head)`（前置 `b1000031`）
- 初次实现: DeepSeek V4 Flash，commit `34fe444f98f4b877c0594db625c478cc4f10ccd4`
- 审查修复: commit `8ff2067f10779921970ef76eef7e9e11c7c0da18`
- 前置: WP-03 accepted manifest SHA `996869e25bf818d0fe58b2463a6784a477f43c15b508fa1ec78d0e28621822b5`

## 1. 交付范围（修改文件）

### Checkpoint A — P3 evaluation spec 与确定性函数

```text
serve/app/domain/trading/evaluation_policy.py
serve/app/domain/trading/p_evaluation_spec_v1.json
serve/app/domain/trading/{scoring,inference}.py
serve/tests/trading/fixtures/p3_learning/p_evaluation_spec_v1.json
serve/tests/trading/fixtures/p3_learning/{bernoulli,multiclass,mean_only,label_conflict,reject_audit,holdout_tamper}.json
serve/tests/trading/fixtures/p3_learning/p3_helpers.py
serve/tests/trading/unit/test_v2_{scoring,inference,evaluation_logic}.py
```

生产运行时使用 deployment-owned policy resource，并在读取时校验 schema/content hash；生产 JSON 与测试
fixture 字节全等，不再依赖部署镜像包含 `tests/`。该必要扩展已登记回任务合同 §4。

### Checkpoint B — `b1000040` Learning facts

```text
serve/alembic/versions/b1000040_v2_0040_p3_learning.py
serve/app/models/trading/{settlement,evaluation,audit,workflow}.py
serve/app/schemas/trading/{settlement,evaluation}.py
serve/app/repositories/trading/{settlement,evaluation,audit}.py
serve/tests/trading/integration/test_v2_0040_learning_migration.py
```

### Checkpoint C — Label / Evaluation / Replay / G8

```text
serve/app/logics/trading/{settlement,evaluation,replay}.py
serve/app/handlers/trading/{settlement,evaluation}.py
serve/runtimes/trading/{evaluation,replay}.py
serve/app/orchestrator/trading_state_machine.py
serve/tests/trading/integration/test_v2_{label_evaluation,promotion_gate}.py
serve/tests/trading/replay/test_v2_p3_learning_replay.py
```

### Checkpoint D — `b1000041` Read projections

```text
serve/alembic/versions/b1000041_v2_0041_read_projections.py
serve/app/models/trading/projection.py
serve/app/repositories/trading/projection.py
serve/app/logics/trading/projection.py
serve/tests/trading/unit/test_v2_projection_logic.py
serve/tests/trading/integration/test_v2_{0041_projection_migration,read_projections}.py
serve/tests/trading/performance/evaluation_projection_smoke.py
```

同步更新上述模块的显式 exports、`app/models/__init__.py` 及 Alembic head/model-count 合同测试。未修改
V1/Admin，未创建账户、钱包、私有 CLOB 或真实资金路径。

## 2. P_EVALUATION_SPEC_MANIFEST

- production resource: `serve/app/domain/trading/p_evaluation_spec_v1.json`
- frozen test fixture: `serve/tests/trading/fixtures/p3_learning/p_evaluation_spec_v1.json`
- 两文件 raw SHA-256（字节全等）:
  `f3e947d591b8c3186ee9bbd5cffcc6da24c291b9c19627143533c47d557c4e1a`
- spec canonical `content_hash`:
  `82c8548950a411f599895efa6bcf319d449280031dcb25b12bea966e25aa1331`
- `frozen_at`: `2026-08-10T00:00:00Z`，严格早于 2026-08-11 测试 assignment；冻结顺序由
  scoring/inference/真 PostgreSQL组合测试复验。
- code/release: 初交 `34fe444…`，accepted code `8ff2067…`，Alembic `b1000041`；运行时每次读取验证
  `schema_version=p3/evaluation-spec/v1` 与 content hash。
- policy hashes：
  - label: `4ff6f251915007150ff8fb4a1558baeb885d1ed62d2c5a5fde8e31e614567197`
  - target canonicalization: `bdcd2cf91793c8d41eee58df932bc7404dbdd1c5270d278a27fafdfd093c906a`
  - baseline: `ef752818a0fc39b9ca42b6678f28f3f353198bc06098bbb8b60ab0cb227a9839`
  - weight: `4d50b7e19e981d2cb4700b7241d1d868af2d7cbfb4d122e950345f79affcd747`
  - split: `ddd31b216926db79d838e9f3129cf3ac4a3ef5c08038009f7973f443ef60b178`
  - bootstrap: `ba19e6fa54d73cb8f38f12fa750f4ea1871bb9ee8842678692d4fe2a409332fb`
  - metric: `9e4653f4a55ee719634d0398fc0007640451a9950ae48abf484d816d181db7a6`
  - promotion: `1acfc1d21b1b15c49677deede45477ba42dcb66211c38a2e8db6fb80aa0eb0e7`
  - properness: `f5d191eaf1b4a98d28b9d29b169428963f2946bc7a5a4d1fc8047ce2d11f455a`

冻结 baseline 使用 blind commit 时刻的权威 quote-binding midpoint；Bernoulli 为 canonical token midpoint，
multiclass 为完整同步 token midpoint vector 且和精确为 1，mean-only 为 payout token midpoint。缺失、陈旧、
未来或不一致的 baseline 必须持久化为 EXCLUDED，禁止用未来 quote 回填；best ask/bid 仍只属于经济执行价。

## 3. 已接受的工作逻辑

1. **Label audit**：完整读取并验证 resolution evidence CAS body/hash、冻结 contract `h/g`、exact resolution
   source 与全 token cashflow；policy hash 由生产 resource 派生，调用方不能替换。wrong rule/source/mapping/
   cashflow 进入 disputed；final revision 只追加，不能 UPDATE/DELETE。
2. **Cluster / holdout**：cluster 在结果未知时分配 split；membership 只能在 OPEN cluster 追加；结果揭晓后
   assignment/reassignment、policy drift 或跨 split 均 fail closed，合法既有 forward label 不被误判 tamper。
3. **Canonical score**：target 持久化 resolution cluster、horizon、target weight 与 exact memberships；
   target→episode→cluster/time-block 等权聚合，token/component 数不放大样本量。
4. **Observation**：事实行区分 `INCLUDED|EXCLUDED` 并保存 exclusion reason；完整 quote-binding IDs/vector/
   baseline value/hash/checkpoint/staleness 可重算。仅 INCLUDED + final_admissible 进入 proper loss。
5. **Metric**：run 精确绑定 `cohort_id + observation_set_hash + strategy/release/split/label set`，只消费该冻结
   observation set；Prediction/Selection/Edge/Portfolio/Execution 五层分别计算，缺证据明确 not_evaluable，
   不使用常量占位或 0 填充。
6. **Portfolio / execution**：HOLD_TO_RESOLUTION 使用最终 label payout、fill/fee/operating cost 与账本事实重建
   system net、drawdown、CVaR、capital-days；prediction loss 不替代资金评价。
7. **Replay**：只接受唯一 COMPLETED metric artifact；重新加载 exact cohort/observation set，复核 cutoff、label、
   quote vector 与 INCLUDED/EXCLUDED，再重算 score、五层、CI、规模和 artifact hash；既有 output hash 不参与
   自证。固定 `network/search/execution=false`，同 key 异参拒绝，原事实不改写。
8. **Promotion / G8**：任一 hard layer 失败即拒绝；低功效继续 shadow；capital promotion 恒拒；strategy
   approval 仅 future-effective。G8 deferred trigger 精确绑定 COMPLETED metric、release 与 promotion policy。
9. **Projection**：五张 read model 可删重建、重复/乱序 effect=0；cursor 绑定 filter hash 与 snapshot/as_of，
   keyset 无 OFFSET/深页 COUNT；投影永不成为 label、permission、G8、ledger 或 replay 的事实源。
10. **零 AI**：本 WP 的 compiler/scoring/replay/projection 全部确定性，未新增 provider 调用或 key。

## 4. 命令与真实结果（accepted code `8ff2067`）

```bash
cd /code/pollymarket/v2/serve

python3 -m compileall -q app runtimes tests alembic
# exit 0

.venv/bin/pytest -q \
  tests/trading/unit/test_v2_scoring.py \
  tests/trading/unit/test_v2_inference.py \
  tests/trading/unit/test_v2_evaluation_logic.py \
  tests/trading/unit/test_v2_projection_logic.py
# 63 passed in 0.63s

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0040_learning_migration.py \
  tests/trading/integration/test_v2_0041_projection_migration.py \
  tests/trading/integration/test_v2_label_evaluation.py \
  tests/trading/integration/test_v2_promotion_gate.py \
  tests/trading/integration/test_v2_read_projections.py \
  tests/trading/replay/test_v2_p3_learning_replay.py
# 57 passed in 39.42s（0 skip，0 fail）

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
# 1375 passed, 8 warnings in 152.54s（0 skip，0 fail）

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
# 1586 passed, 8 warnings in 154.63s（0 skip，0 fail）

.venv/bin/alembic heads
# b1000041 (head)，唯一 head

.venv/bin/alembic upgrade b1000041 --sql > /tmp/wp04.sql
# 6628 lines；secret-value hits=0

git diff --check
# exit 0
```

临时测试/性能数据库残留：`0`。

## 5. 性能（clean commit、真 PostgreSQL、evaluation pool=3+1）

- artifact: `/tmp/pm_v2_perf_smoke_4.json`
- SHA-256: `ba59231f35c14afdaa388b7048bee93272228e7d773781e116c81556368ff635`
- code identity: `8ff2067f10779921970ef76eef7e9e11c7c0da18`
- `git_worktree_clean=true`，status entries=`0`
- PostgreSQL `18.4`，16 logical CPU，seed=`deterministic/wp-04-read-projection-performance-v1`

| 门 | 结果 | 门槛 |
|---|---:|---:|
| keyset rows / pages / limit | 100,006 / 201 / 500 | source facts≥100,000 |
| keyset p50 / p95 / p99 | 1.783 / 2.446 / 3.254 ms | p95≤500ms、p99≤1s |
| 最大响应 / SQL plan | 193,001 B / Index Scan | ≤200KiB |
| keyset pool wait p95 | 0.058ms | ≤20ms |
| scientific replay p50 / p95 / p99（5 runs） | 10.768 / 21.218 / 21.218 ms | p95≤5s、p99≤15s |
| replay pool wait p95 | 0.156ms | ≤20ms |
| rebuild hash / lost / duplicate | identical / 0 / 0 | exact / 0 / 0 |
| metric workload | 4,991 completions @ 498.493/s（10s） | 固定 workload 报告 |
| metric pool wait p95 / peak | 0.074ms / 4 | ≤20ms / pool 3+1 |
| WAL / max RSS / CPU | 46,733,016 B / 977,892 KiB / 16.009s | 报告 |
| hard assertions | PASS | PASS |

Gate 2 的被测对象是冻结 canonical observation set 的真实 `ReplayLogic`，不是 decision/execution 替身。

## 6. 数据库约束

- 0040 创建 13 张 P3 label/evaluation facts 并在 audit 域增加 `replay_runs`（合计 14）；强化 label
  state/supersede/single-current、cluster lifecycle/membership timing、target exact membership/weight、score
  quote-vector/status/exclusion、metric exact cohort/observation set/lifecycle、promotion/G8 release-policy 约束。
- score observations 保存 baseline binding IDs/value/hash/checkpoint；metric runs 保存 `cohort_id` 与
  `observation_set_hash`；deferred triggers 阻断跨 target、未来 quote、非法 included、weight 放大及完成后改写。
- 0041 创建 5 张只读 projection，每行含 as_of/watermark/version/hash，并提供绑定 filter/snapshot 的 keyset。
- `alembic check`/ORM modeled drift、空库与 Base 库 upgrade、`0041→0040→0031` roundtrip、未知对象 downgrade
  fail-closed 均由上述 57 个真 PostgreSQL/replay 组合测试覆盖。

## 7. 审查整改记录

初交 `34fe444` 的 happy-path 测试通过，但审查发现生产 policy 依赖 tests、label evidence/source/cashflow 可被
弱绑定、holdout tamper 方向错误、baseline 使用经济 ask 且缺 multiclass vector、excluded 未持久化、聚合与
`n_eff` 可被 target/token 数扭曲、metric cohort 不精确、五层/Portfolio 有占位、replay 只验 metadata、
projection cursor 未绑定 filter/snapshot，以及若干 DB guard/downgrade 边界不足。

审查者在 `8ff2067f10779921970ef76eef7e9e11c7c0da18` 直接完成生产 policy resource、CAS/full-cashflow、
midpoint/vector/explicit exclusion、canonical equal weighting、exact observation set、五层与 system-net、
scientific replay、cursor snapshot 及 DB fail-closed 修复；加入相应反例，并重新跑完 unit、真 PostgreSQL、
trading/full 与 clean-commit performance。未创建整改 WP；本轮无剩余 P0/P1。

## 8. P3_COMPLETION_MANIFEST（ARCHITECTURE §10.5）

1. reject-audit 保存 inclusion probability/seed/disposition，Horvitz–Thompson coverage/机会成本可重建；无样本
   只报 unknown。
2. label 五态、完整 CAS/source/payout/mapping/cashflow fail closed；canonical Bernoulli/multiclass/mean-only
   target 与 proper loss 可重算。
3. cluster/split/holdout assignment timing、time-block stratification、fixed seed、cluster-level `n_eff`、stopping/
   multiple testing 冻结；target/token 数不放大独立样本量。
4. top-loss/regret 与随机成功按冻结 seed 入 review；root-cause taxonomy allowlist，ablation bundle 可回放。
5. Portfolio 从 resolution payout、execution/ledger 与全部 operating cost 重建 system net、drawdown、CVaR、
   capital-days；证据不全为 not_evaluable。
6. selected action-set forward system-net/风险是资金主门；full/selected ΔLoss/calibration 是独立认知 guardrail，
   五层互不替代。
7. 本 manifest 记录 P2/P3 spec、cohort/observation、label/metric/replay/projection、code/fixture、测试/perf/SQL
   evidence hashes；所有硬门 0 skip/0 fail，`hard_assertions=PASS`。

结论：P3 七条 DoD 全部满足；WP-04 接受，但仍不授予 canary/live 或非零资本权限。

## 9. Blocker / 非目标

无 P0/P1 blocker。非目标保持：不修改 WP-03 在线 G7B，不做 P4 ensemble/challenger/bias 激活，不做
P-stability/P-execution-readiness/canary/live，不接 Vault/account/private CLOB/User WS/Data API/Polygon/
relayer/redeem，不建设 Admin Controller/API/frontend，不改 V1，不运行新的 AI label auditor。

## 10. 回滚

- 数据库：`alembic downgrade b1000041`；0041 只删可重建 projections，0040 在未知下游对象存在时
  fail closed；append-only label/metric/promotion 先导出 artifact manifest 再回滚。
- 代码：revert 审查修复 `8ff2067`，再按需 revert 初交 `34fe444`；WP-03 forecast/decision/shadow/ledger
  事实完整保留。
- promotion 回滚只追加新 `promotion_decision`，不修改旧记录或历史 assignment。

## 11. Manifest SHA-256

口径：删除本文件中“恰好 64 位小写十六进制”的哈希行后计算。

```text
c22daa477f748354538c484fff5957e237a0f03db39907c2767580e957bf638a
```

```bash
sed -e '/^[0-9a-f]\{64\}$/d' \
  serve/docs/manifests/wp-04-learning-evaluation-read-projections.md | sha256sum
```
