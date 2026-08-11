# COMPLETION MANIFEST — WP-02 · Minimal Cognition、AI Observability 与 Blind Forecast

- Work package: `WP-02`
- 状态: **ACCEPTED**
- 完成日期: 2026-08-11 EDT
- 审查修复提交: `e9f4c205dd639736f1c4270a923df10ff77e3f58`
- 任务合同: `serve/docs/tasks/wp-02-minimal-cognition-ai-observability.md`
- Alembic: `b1000020 → b1000021 (head)`
- 实现: DeepSeek V4 Flash

## 1. 交付范围

### 生产（Checkpoint A —— b1000020 cognition）

```text
serve/alembic/versions/b1000020_v2_0020_p1b_cognition.py
serve/app/models/trading/forecast.py
serve/app/models/trading/workflow.py            # forecast_episodes cognition 强化 + gate allowlist
serve/app/schemas/trading/{evidence,forecast,__init__}.py
serve/app/domain/trading/{probability,__init__}.py
serve/app/repositories/trading/{forecast,__init__}.py
serve/app/logics/trading/{evidence,forecast,__init__}.py
serve/app/orchestrator/trading_state_machine.py  # G4/G5A/G5B/G6 + terminal_g6_fail
```

### 生产（Checkpoint B —— b1000021 AI observability + gateway）

```text
serve/alembic/versions/b1000021_v2_0021_p1b_ai_observability.py
serve/app/models/trading/ai.py
serve/app/models/trading/{control,artifact}.py    # model_role_bindings typed/versioned + lineage relation
serve/app/services/model_gateway/{__init__,contracts,registry,service}.py
serve/app/services/model_gateway/drivers/{__init__,base,deepseek,xai,gemini,kimi,packy}.py
serve/app/ai_runtime/{__init__,runner,validator,cache,redaction}.py
```

### 生产（Checkpoint C —— 四角色 prompt + handler/runtime）

```text
serve/app/prompts/v2/{planner_prior,researcher,verifier,joint_forecaster}/v1.{md,schema.json}
serve/app/handlers/trading/{cognition,__init__}.py
serve/runtimes/trading/{cognition,__init__}.py
```

### 透明必要更新

`app/models/__init__.py`、`app/models/trading/__init__.py`、`app/repositories/trading/__init__.py`、
`app/schemas/trading/__init__.py`、`app/logics/trading/__init__.py`、`app/domain/trading/__init__.py`
（显式导出）；`tests/trading/fixtures/migration_helpers.py`（AI 分区白名单）。

### 测试与固定样本

```text
serve/tests/trading/unit/test_v2_{probability,evidence_logic,forecast_logic,ai_runner,model_gateway,cognition_state_machine}.py
serve/tests/trading/contract/test_v2_{deepseek,xai,gemini,kimi,packy}_contract.py
serve/tests/trading/fixtures/ai_wire/{deepseek,xai,gemini,kimi,packy}/*.json
serve/tests/trading/integration/test_v2_{0020_cognition_migration,0021_ai_migration,ai_invocation,blind_forecast_workflow}.py
serve/tests/trading/replay/test_v2_p1b_cognition_replay.py
serve/tests/trading/performance/cognition_smoke.py
```

未修改 V1、Admin、decision/portfolio/execution/ledger、私有 CLOB 或链上代码。

## 2. 已冻结的工作逻辑

1. **G4 Prior**：显式市场盲先验（reference class/hazard、applicability、sample_rule、width、
   failure_conditions、market_blind_declaration=true）；结构/引用缺失或 market-blind 违反 → FAIL。
   通过后 episode `PENDING→PRIOR_READY`。
2. **G5A Integrity**：四时态（event/published/observed/ingested）、source、raw Artifact、cutoff
   （published/observed/ingested ≤ cutoff）、taint、market-conditioned discovery 全 hard veto；
   通过后冻结不可变 bundle（乱序不改变 hash），episode `PRIOR_READY→EVIDENCE_READY`。
3. **G5B Sufficiency**：按冻结 coverage policy 返回 `PASS|WIDEN_REQUIRED|ABSTAIN_EVIDENCE_INSUFFICIENT`；
   widening 输入/输出 hash 可重算。
4. **G6 Blind Forecast**：Q/U coherence（Q 非负 total、U 非空去重含 Q）与全部 μ/V/bounds/p_blind
   由 Decimal 确定性函数重算（domain.probability）；LLM 输出不能直接成为 projection 或 PASS。
   G6 PASS、input manifest、submission、全部 spec×token projection、coherence checks、lease、
   Gate、workflow event/outbox 同一 UoW 原子提交；任何失败 BLIND_COMMITTED 数=0。
5. **Immutable commit**：submission `DRAFT→BLIND_COMMITTED` 唯一合法；commit 后禁 update/delete；
   episode `ROUTED→BLIND_COMMITTED`；forecast_lease 保存 valid_until + 结构化 invalidation +
   evidence/schema/spec hash。
6. **AI invocation**：每次调用先落 PLANNED invocation，再访问 Provider；retry/fallback/cache hit
   各建新 attempt 记录因果；provider 返回后崩溃 → UNKNOWN 不猜结果；失败不缓存；只缓存
   ACCEPTED+network=NONE；requested/returned model 分列，返回未 allowlist 直接 REJECTED。
7. **Blind 物理边界**：planner/joint_forecaster 固定 network=NONE/tools=[]；blind 上下文
   （CONTRACT/PRIOR/EVIDENCE）禁止 quote/odds/crowd/label/future-fact 字段（taint 检测 + 逻辑拒绝）。
8. **模型绑定**：只按冻结 `model_role_binding_id` 构造 Driver；registry allowlist 拒绝
   Anthropic/OpenAI；Anthropic 永久排除，OpenAI 不进首版注册表。
9. **审查后证据闭环**：attempt identity 使用非分区 global claim；episode/binding/
   variant 必须精确匹配。Provider 请求前先持久化 request/prompt/schema Artifact，有响应的
   任意终态都保留 raw/parsed/normalized/tool/validator/lineage。`ACCEPTED` 由 PostgreSQL
   交叉验证 6 份 Artifact、5 个 validator、tool receipt 与 output binding，下游只从
   该 normalized Artifact 取值，不再接受调用者任意注入的 AI 结果。
10. **调用效率与成本**：exact cache key 覆盖 provider/route/model/prompt/schema/code/
    network/tools/domains/sampling/seed/effort/max tokens；cache hit 仍生成完整独立 attempt。
    Provider 网络期间不持有 DB transaction；CAS 为有界 4096-entry LRU + single-flight，
    Artifact/catalog/lineage/validator/tool 批写。成本按冻结 pricing snapshot 估算，缺价时
    显式 `UNPRICED`，不伪造为 0 成本已对账。

## 3. 命令与真实结果

最终审查基于代码提交 `e9f4c205dd639736f1c4270a923df10ff77e3f58`；原 DONE 报告中的
1375/477.9s 与从 `0/0` 起算的 WAL 证据已被本节结果替代。

```bash
cd /code/pollymarket/v2/serve

python3 -m compileall -q app tests alembic
# exit 0

# WP-02 unit + contract + 真 PostgreSQL integration/replay
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/unit/test_v2_probability.py tests/trading/unit/test_v2_evidence_logic.py \
  tests/trading/unit/test_v2_forecast_logic.py tests/trading/unit/test_v2_ai_runner.py \
  tests/trading/unit/test_v2_model_gateway.py tests/trading/unit/test_v2_cognition_state_machine.py \
  tests/trading/contract tests/trading/integration/test_v2_0020_cognition_migration.py \
  tests/trading/integration/test_v2_0021_ai_migration.py tests/trading/integration/test_v2_ai_invocation.py \
  tests/trading/integration/test_v2_blind_forecast_workflow.py \
  tests/trading/replay/test_v2_p1b_cognition_replay.py
# 218 passed in 17.76s（0 skip，0 failure）

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
# 1390 passed, 8 warnings in 92.58s（0 skip，0 failure）

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/python -m tests.trading.performance.cognition_smoke
# hard_assertions=PASS；输出 /tmp/pm_v2_perf_smoke_2.json

.venv/bin/alembic heads
# b1000021 (head)

.venv/bin/alembic upgrade b1000021 --sql > /tmp/wp02-reviewed.sql
# 4142 lines；secret hits=0

git diff --check
# exit 0

psql -Atqc "SELECT datname FROM pg_database WHERE datname LIKE 'v2_test_%' OR datname LIKE 'v2_perf_%'" postgres
# 0 rows
```

## 4. 可复现证据

### 数据库

- 11 张 cognition 表（priors、evidence_coverage_policies、evidence_revisions、evidence_bundles、
  evidence_bundle_items、forecast_input_manifests、forecast_submissions、payout_projections、
  coherence_checks、forecast_challenges、forecast_leases）+ 3 张 AI 月 RANGE 分区表
  （ai_invocations、ai_tool_calls、ai_validation_results，无 default partition，预建覆盖月份）。
- 既有表强化：`forecast_episodes` 增 cognition_status + prior/evidence/commit 时间戳 +
  BLIND_COMMITTED；`gate_decisions`/`information_snapshots` allowlist 增 G4/G5A/G5B/G6；
  `model_role_bindings` typed/versioned capability binding（legacy 反填）；`artifact_lineage_edges`
  relation 增 READS/PRODUCES/VALIDATES/SUPERSEDES/PROJECTS_TO/USED_BY。
- `alembic check` modeled drift=0（动态 AI 分区白名单后）。
- 0020/0021 均支持 literal-empty roundtrip 与 downgrade fail-closed（未知对象 preflight）。

### 约束

- submission `DRAFT→BLIND_COMMITTED` 唯一合法；commit 后 update/delete 全拒绝（guard + 测试）。
- episode 状态机 `G0→R0→G1→G2→R1→G4→G5A→G5B→G6` 由 orchestrator ORDER + DB guard 双重强制。
- payout projection 对每个 member spec×token 恰一条；`Q∉U`/非法概率在 commit 前 fail-closed。

### 性能（真 PostgreSQL、有界 pool=16、overflow=0、真实 UoW/constraint、fake transport）

`/tmp/pm_v2_perf_smoke_2.json`，`hard_assertions=PASS`：

| 项 | 结果 | 门槛 |
|---|---:|---:|
| AI terminalizations | 11,881 @ 197.782/s（60.071s） | ≥100/s 持续≥60s |
| 每条 2 tool + 5 validator | 23,762 / 59,405（=rows×2/×5） | 精确匹配 |
| AI lost/duplicate | 0 / 0 | 0 |
| AI pool wait p95 | 0.115ms | ≤20ms |
| Blind commits | 3,584 @ 59.711/s（60.022s） | ≥20/s 持续≥60s |
| 持续窗口（10s） | [63.0, 60.0, 60.1, 58.8, 57.4, 59.0] | 全部 ≥20/s |
| lost/duplicate/projection mismatch | 0 / 0 / 0 | 0 |
| projection/outbox rows | 7,168 / 3,584 | 精确 2× / 1× commit |
| Commit pool wait p95 | 0.117ms | ≤20ms |
| 本次 workload WAL | 202,161,432 bytes | 记录真实 delta |

环境证据：PostgreSQL 18.4、16 logical CPUs、seed=`deterministic/wp-02-cognition-performance-v1`、
测试提交=`e9f4c205dd639736f1c4270a923df10ff77e3f58`。临时测试/性能数据库残留：`0`。

## 5. Blocker / 非目标

无 P0/P1 blocker。非目标（任务 §8）：不揭示/绑定市场价格、不做 edge/action/portfolio、
不做 blind challenger 生产路径、不做 Admin UI、不改 V1、不接私有 CLOB/Polygon。

## 6. 回滚

- 数据库：`alembic downgrade b1000013`（0021→0020→0013 各 revision 先检查未知下游对象，
  异常整次回滚）；撤销 model_role_bindings/lineage 强化并恢复 b1000013 原 guard。
- 代码：revert WP-02 提交；原 WP-01C episode 保持 `ROUTED`，不删除历史事实。

## 7. Manifest SHA-256

口径：删除本文件中“恰好 64 位十六进制”的哈希行后计算。

```text
5bc49cf3db17b3e42b46f136b1a5bc3569694cb89ee5cee40cfc96707f29d316
```

```bash
sed -e '/^[0-9a-f]\{64\}$/d' \
  serve/docs/manifests/wp-02-minimal-cognition-ai-observability.md | sha256sum
```
