# COMPLETION MANIFEST — WP-03 · Market-Relative Decision、Minimum Portfolio、Shadow Execution 与双分录账本

- Work package: `WP-03`
- 状态: **DONE（待审）**
- 完成日期: 2026-08-11 EDT
- 任务合同: `serve/docs/tasks/wp-03-market-relative-decision-shadow-ledger.md`
- Alembic: `b1000030 → b1000031 (head)`
- 实现: DeepSeek V4 Flash

## 1. 交付范围

### 生产（Checkpoint A —— P2 execution spec 冻结）

```text
serve/tests/trading/fixtures/p2_decision/p_execution_spec_v1.json
serve/tests/trading/fixtures/p2_decision/{bernoulli,time_nested,mutually_exclusive,conditional,void_partial,shadow_book_depth,v1_gold_reentry}.json
serve/tests/trading/fixtures/p2_decision/p2_helpers.py
```

### 生产（Checkpoint B —— b1000030 Decision/G7A/Action Set）

```text
serve/alembic/versions/b1000030_v2_0030_p2_decision_shadow.py
serve/app/models/trading/decision.py
serve/app/models/trading/{workflow,market_stream}.py        # G7A/G7B gates + episode REVEALED/DECIDED + quote binding
serve/app/schemas/trading/decision.py
serve/app/domain/trading/{valuation,rounding,__init__}.py
serve/app/repositories/trading/{decision,__init__}.py
serve/app/logics/trading/{decision,__init__}.py
serve/app/orchestrator/trading_state_machine.py             # G0..G6,G7A,G7B
```

### 生产（Checkpoint C —— b1000031 Portfolio/Shadow/Ledger）

```text
serve/alembic/versions/b1000031_v2_0031_p2_shadow_ledger.py
serve/app/models/trading/execution.py
serve/app/models/trading/ledger.py
serve/app/schemas/trading/execution.py
serve/app/domain/trading/{portfolio,ledger,__init__}.py
serve/app/repositories/trading/{execution,ledger,__init__}.py
serve/app/logics/trading/{portfolio,execution,__init__}.py
```

### 生产（Checkpoint D —— Handler/Runtime）

```text
serve/app/handlers/trading/{decision,execution,__init__}.py
serve/runtimes/trading/{execution,__init__}.py
```

### 透明必要更新

`app/models/__init__.py`、`app/models/trading/__init__.py`、`app/repositories/trading/__init__.py`、
`app/schemas/trading/__init__.py`、`app/logics/trading/__init__.py`、`app/domain/trading/__init__.py`。

### 测试

```text
serve/tests/trading/unit/test_v2_{valuation,portfolio,decision_logic,shadow_execution,trading_state_machine}.py
serve/tests/trading/integration/test_v2_{0030_decision_migration,0031_shadow_ledger_migration,decision_shadow_workflow,ledger_invariants}.py
serve/tests/trading/replay/test_v2_p2_decision_replay.py
serve/tests/trading/performance/decision_shadow_smoke.py
```

未修改 V1、Admin、decision 之外无越界；未创建 pm_accounts/authorization envelope/exchange order/
真实下单路径（属 WP-05）。

## 2. P_EXECUTION_SPEC_MANIFEST

- fixture: `serve/tests/trading/fixtures/p2_decision/p_execution_spec_v1.json`
- fixture SHA-256: `b1f26b53332d4a0185f8977c680d3486b63a3a05e50dd3a5193dda606e943ac1`
- spec frozen_at: `2026-08-11T00:00:00Z`
- 首个 P2 decision trigger: `2026-08-11`（测试运行期）→ **frozen_at 严格早于首个 trigger** ✓
- 通过测试 fixture 创建的真实 DB 行（workflow 测试 seed）：
  - `execution_spec_versions` spec_key=`p2-exec-spec-v1` version=1 status=active
  - `capital_permission_manifests` name=`p2-shadow-capital-v1` mode=shadow authorized_capital=0
  - `release_manifests` release_name=`p2-shadow-release-v1` status=active
  - release 精确引用 exec_spec + capital permission + strategy/objective
- 冻结语义：HOLD_TO_RESOLUTION、allowed actions、short=false、shadow-only、authorized capital=0、
  executable depth walk、fee/slippage 0、4%/6%/30% caps、BLIND_ONLY baseline、
  optional linear-shrinkage challenger（无默认学习 w）。

## 3. 已冻结的工作逻辑

1. **create_decision**：仅当 episode `BLIND_COMMITTED` + 有效 lease + release 引用的 active
   shadow/authorized=0 execution spec + capital permission 才创建 `CREATED` decision；否则
   `decision_episode_not_blind_committed/decision_lease_invalid/decision_freeze_invalid`。
2. **reveal**：按 exact quote checkpoint 写 `pm_quote_bindings`（trade_decision_id 绑定）；
   stale/crossed/missing fail-closed；`decision_ref` 不再写。
3. **market-relative**：BLIND_ONLY 默认（Q_decision=Q_blind、U_decision=U_blind 精确复用，不覆盖
   blind submission）；LINEAR_SHRINKAGE challenger 仅当完整互斥 token ask set 和=1 能构造
   coherent Q_market；不可识别 → `ABSTAIN_MARKET_REFERENCE_UNIDENTIFIED`，不阻塞 BLIND_ONLY。
4. **G7A**：depth walk（buy 按 ask、sell 按 bid；无 midpoint fill、不造 book 中不存在的数量）+
   全成本 ΔW（settlement - entry - fee，每项只扣一次，residual=0）+ robust EV（min over U）；
   写 action_candidates + resolution_cashflows + G7A gate。
5. **G7B**：4%/6%/30%（或更低 permission cap）精确执行 + marginal utility；cap 越限 FAIL。
6. **terminalize**：ACTION/WAIT/ABSTAIN（HOLD 零 leg、RISK_REVIEW 只 reduce/close、FLIP close/open
   成对）；ACTION 至少 1 leg，WAIT 带 wake，ABSTAIN 带 reason；OPEN/ADD/HOLD 绑定 underwriting；
   exposure-changing ACTION → immutable intent（mode-independent hash）。
7. **shadow fill**：execution PENDING→PARTIAL|FILLED|REJECTED|FAILED；position/lot 与 ledger 同一
   UoW；BUY 至少 4 postings（cash+token 双对手各自归零）；position 不为负；posted transaction/
   posting 禁改/禁删（reversal 精确相反）。
8. **quote-only 零 AI**：reveal/edge/cost/fill/ledger 全代码；测试断言 quote-only refresh 前后
   `ai_invocations=0`、`forecast_episodes=1`。
9. **账本**：deferred trigger 强制每 asset 归零且 ≥2 postings；operating cost 类别白名单
   DATA|LLM|SEARCH|INFRASTRUCTURE|HUMAN|OPERATIONAL_LOSS；系统收益三层
   trading_pnl/operating_cost/system_net_profit。

## 4. 命令与真实结果

```bash
cd /code/pollymarket/v2/serve

python3 -m compileall -q app runtimes tests alembic
# exit 0

.venv/bin/pytest -q \
  tests/trading/unit/test_v2_valuation.py tests/trading/unit/test_v2_portfolio.py \
  tests/trading/unit/test_v2_decision_logic.py tests/trading/unit/test_v2_shadow_execution.py \
  tests/trading/unit/test_v2_trading_state_machine.py
# 85 passed in 0.51s

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0030_decision_migration.py \
  tests/trading/integration/test_v2_0031_shadow_ledger_migration.py \
  tests/trading/integration/test_v2_decision_shadow_workflow.py \
  tests/trading/integration/test_v2_ledger_invariants.py \
  tests/trading/replay/test_v2_p2_decision_replay.py
# 17 passed in 10.26s（0 skip）

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/python -m tests.trading.performance.decision_shadow_smoke
# hard_assertions=PASS；输出 /tmp/pm_v2_perf_smoke_3.json

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
# 1271 passed in 106.87s

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
# 1482 passed, 8 warnings in 109.42s（0 skip，0 failure）

.venv/bin/alembic heads
# b1000031 (head)

.venv/bin/alembic upgrade b1000031 --sql > /tmp/wp03.sql
# 4936 lines；secret hits=0

git diff --check
# exit 0
```

## 5. 性能（真 PostgreSQL、有界 pool=16/overflow=0、真实 domain/repository/UoW/constraint）

`/tmp/pm_v2_perf_smoke_3.json`，`hard_assertions=PASS`：

| 门 | 结果 | 门槛 |
|---|---:|---:|
| deterministic decision valuation | 16,251 @ **270.6/s**（60.05s） | ≥100/s 持续≥60s |
| 10s 窗口 | [279.2, 282.8, 279.5, …] | 全部 ≥100/s |
| valuation tx p99 | 20.6ms | ≤50ms |
| valuation pool wait p95 | 0.063ms | ≤20ms |
| atomic shadow terminalization | 4,720 @ **78.7/s**（60.01s） | ≥10/s 持续≥60s |
| 10s 窗口 | [76.0–82.4] | 全部 ≥10/s |
| terminalization tx p99 | 18.5ms | ≤50ms |
| ledger transactions / postings | 4,720 / 18,880 | 4 postings/event |
| lost/duplicate/unbalanced/negative-position | 0 / 0 / 0 / 0 | 全 0 |
| env | git `49d6546`、PostgreSQL 18.4、16 CPU、RSS 109,656 KiB、WAL delta 345MB | — |

临时测试/性能数据库残留：`0`。

## 6. 数据库约束

- 0030：9 张 decision 表 + pm_quote_bindings 强化（trade_decision_id + partial unique）+
  gate G7A/G7B（绑 trade_decision）+ episode REVEALED/DECIDED + trade_decision lifecycle guard +
  action_set leg 一致性（deferred）+ intent lifecycle guard。
- 0031：3 execution + 3 ledger 表 + execution lifecycle guard + ledger_transactions lifecycle guard
  （POSTED 禁改/禁删）+ ledger deferred balance（每 asset 归零 ≥2 postings，≥2 asset groups）。
- `alembic check` modeled drift=0；0030/0031 均支持 literal-empty roundtrip 与 downgrade
  fail-closed（未知对象 preflight）。

## 7. 五类 semantic + V1 Gold fixture

- bernoulli / time_nested / mutually_exclusive / conditional / void_partial：BLIND_ONLY 决策、
  coherent fixed-shrinkage（完整互斥集）、不可识别 market reference challenger abstain、
  VOID/PARTIAL 退款路径。
- shadow_book_depth：确定性深度 walk partial fill。
- v1_gold_reentry：end 后/terminal、13h+ stale、重复旧 signal、UNREVIEWED 四路径均 0 增仓。

## 8. Blocker / 非目标

无 P0/P1 blocker。非目标（任务 §8）：不扩展 BTC/做市/套利/返利驱动/跟单；不实现 ACTIVE_REVALUE/
学习型 shrinkage/portfolio optimizer；不接私有 CLOB/User WS/Data API/Polygon/relayer/Vault/真实账户；
不创建 canary/live execution envelope，不真实下单；不做 WP-04/Admin UI/V1 兼容。

## 9. 回滚

- 数据库：`alembic downgrade b1000021`；0031→0030→0021 各 revision 在 destructive DDL 前检查
  未知下游对象并整次回滚（含 gate guard/ledger guard 恢复）。
- 代码：revert WP-03 提交；WP-02 blind submissions 保留。
- Shadow 账本更正只追加 reversal；不改旧 transaction/posting。

## 10. Manifest SHA-256

口径：删除本文件中“恰好 64 位十六进制”的哈希行后计算。

```text
875a1716ca4cb9463f61c2b1ca6bbedb95b2123ee911a9d4e3243c7a4ed02e1b
```

```bash
sed -e '/^[0-9a-f]\{64\}$/d' \
  serve/docs/manifests/wp-03-market-relative-decision-shadow-ledger.md | sha256sum
```
