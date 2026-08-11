# COMPLETION MANIFEST — WP-01C · Contract、Component、Cohort 与 Screening

- Work package: `WP-01C`
- 状态: **ACCEPTED**
- 完成/审查日期: 2026-08-11 EDT
- 任务合同: `serve/docs/tasks/wp-01c-contract-component-cohort-screening.md`
- Alembic: `b1000012 → b1000013 (head)`
- 实现: DeepSeek V4 Flash；范围内 P0/P1 由审查者直接修复并复验

## 1. 交付范围

### 生产

```text
serve/alembic/versions/b1000012_v2_0012_p1a_semantics.py
serve/alembic/versions/b1000013_v2_0013_p1a_cohort_episode.py
serve/app/domain/trading/{gates,hashing,payout}.py
serve/app/schemas/trading/{semantics,workflow}.py
serve/app/models/trading/{cohort,semantics,workflow}.py
serve/app/repositories/trading/{cohort,semantics,workflow}.py
serve/app/logics/trading/{contract,component,screening}.py
serve/app/orchestrator/trading_state_machine.py
```

以及对应 `__init__.py` 显式导出、`control.py` policy freeze 强化和既有 head/table-count
测试适配。未修改 V1、AI provider、decision/portfolio/execution、Admin 或真实下单代码。

### 测试与固定样本

```text
serve/tests/trading/fixtures/p1a_fixtures.py
serve/tests/trading/fixtures/p1a_semantics/*.json
serve/tests/trading/unit/test_v2_{payout,contract_logic,component_logic,screening_logic,trading_state_machine}.py
serve/tests/trading/integration/test_v2_{0012_semantics_migration,0013_cohort_episode_migration}.py
serve/tests/trading/integration/test_v2_{cohort_screening,semantic_workflow}.py
serve/tests/trading/replay/test_v2_p1a_semantics_replay.py
serve/tests/trading/performance/cohort_screening_smoke.py
```

## 2. 已冻结的工作逻辑

1. **G0 / Cohort**：cohort 只能在 objective、strategy、release 与全部必需 policy freeze
   精确绑定后开启；全量 market 必须有 prospective membership。
2. **R0**：只做廉价 `SELECT/DEFER/REJECT`；拒绝审计使用冻结 seed/salt/probability 的
   确定性抽样，重试和输入乱序不改变结果。
3. **G1 / Contract**：冻结 exact market/token versions、规则来源、`K_c/R_c` 和每个 token 的
   total payout truth table。缺规则、歧义、token 冲突、缺关键 clarification 或 payout 不完整均
   fail-closed；PASS spec 的 payout 数量、token 身份、`R_c` key 集和数值范围由 deferred DB
   trigger 在 commit 时验证。
4. **G2 / Component**：局部 component 使用有限 world-state assignment 和安全 IR；每个
   `h_c` 必须对所有 world state total，值属于对应 contract spec 的 `R_c`。component/schema/
   membership 身份、依赖边 canonical 顺序和发布完整性由 DB 约束。
5. **Episode / R1**：只有 G1、G2 的持久化 PASS 证据可创建 component episode；episode spec
   集必须与 component membership 双向全等。R1 只产生 route/disposition；本期所有 episode 的
   action/qualification/capital eligibility 固定为 false。
6. **状态与证据**：`G0→R0→G1→G2→R1` 是唯一顺序；Gate 决策必须绑定该 cohort 冻结的
   **对应 policy type** 与 release，不能用另一 policy 的合法 hash 冒充。事实表 append-only，
   terminal/retry/crash 路径可重放。

## 3. 审查中直接关闭的问题

- payout 为零、token-version 偷换、PASS spec 无 payout 等 DB 绕过；
- component/schema 身份漂移、空 active component、`h_c` 非 total 或越出 spec `R_c`；
- DRAFT→OPEN 括号绕过、缺 10 项 freeze、OPEN frame 被当 confirmed membership；
- gate target/kind/order、episode 空 spec-set、R1 eligibility 及终态可改写；
- world state 从含糊字符串改为 exact `{world_state_id, assignment}`，并校验唯一、完备映射；
- screening 幂等、Decimal JSON、首写 CTE 可见性、真实状态机前序证据；
- 原性能脚本未持续 60 秒、未跑真实 pipeline，替换为 paced 真 PG/UoW/constraint harness。

最终审查结论：当前任务范围内未发现剩余 P0/P1。

## 4. 可复现证据

```bash
cd /code/pollymarket/v2/serve

python3 -m compileall -q app tests alembic

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/unit \
  tests/trading/integration/test_v2_0012_semantics_migration.py \
  tests/trading/integration/test_v2_0013_cohort_episode_migration.py \
  tests/trading/integration/test_v2_cohort_screening.py \
  tests/trading/integration/test_v2_semantic_workflow.py \
  tests/trading/replay/test_v2_p1a_semantics_replay.py
# 98 passed in 16.70s

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
# 1242 passed, 8 warnings in 73.71s

.venv/bin/alembic heads
# b1000013 (head)

.venv/bin/alembic upgrade b1000013 --sql > /tmp/wp01c.sql
# 2957 lines；secret hits=0

git diff --check
# exit 0
```

真 PostgreSQL 性能报告：`/tmp/pm_v2_perf_smoke_1c.json`，`hard_assertions=PASS`。

| 项 | 结果 | 门槛 |
|---|---:|---:|
| 50,000 enrollment + R0 | 8.714s；50,000 exact | ≤60s；零缺失/重复 |
| G1→G2→episode→R1 | 9,721 commits / 67.046s = 144.99/s | ≥100/s 且持续≥60s |
| 最低 10s 窗口 | 153.0/s | ≥100/s |
| Pool | size=16，overflow=0，wait p95=0.091ms | p95≤20ms |
| 完整性 | loss/duplicate/spec mismatch/eligibility true 均 0 | 全部为 0 |

临时测试/性能数据库残留：`0`。

## 5. 回滚与非目标

- 回滚：`alembic downgrade b1000011`；0013→0012→0011 均在 destructive DDL 前检查未知对象，
  异常时整次 rollback。代码回滚为 revert 本里程碑提交。
- 非目标：AI invocation、联网研究、prior/evidence bundle、Q/U/μ/V、blind commit/reveal、edge、
  portfolio、执行、账本、实盘和 Admin UI；这些由后续 WP 承接。

## 6. Manifest SHA-256

口径：删除本文件中“恰好 64 位十六进制”的哈希行后计算。

```text
4a17a08acffa3380f0fa37ac6b7ba592c48dbe80df91e07e30c24ef0c5e7c9c4
```

```bash
sed -e '/^[0-9a-f]\{64\}$/d' \
  serve/docs/manifests/wp-01c-contract-component-cohort-screening.md | sha256sum
```
