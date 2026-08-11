# WP-02 — Minimal Cognition、AI Observability 与 Blind Forecast

> 状态：**READY**<br>
> 执行模型：DeepSeek V4 Flash<br>
> 前置：`WP-01C` 已 ACCEPTED，Alembic head=`b1000013`<br>
> 唯一交付：`serve/docs/manifests/wp-02-minimal-cognition-ai-observability.md`<br>
> 最后更新：2026-08-11 EDT

## 1. 目标与用户价值

把 WP-01C 的 `R1 ROUTED` episode 推进为一份**市场盲、不可变、可回放**的 component-level
forecast，并让每次 AI 调用都能回答：为什么调用、输入是什么、用了哪个 provider/model/tool、
返回什么、为何通过/失败、花费多少、影响哪个 artifact。

唯一工作链：

```text
R1 ROUTED
→ planner + explicit prior → G4
→ researcher + independent verifier
→ evidence revisions/bundle → G5A integrity → G5B sufficiency
→ joint forecaster
→ deterministic Q/U coherence + h/g payout projection → G6
→ atomic BLIND_COMMITTED + forecast lease
```

本任务完成后仍不揭价、不计算 edge、不产生 action，不进入 shadow/canary/live。

## 2. 已确认产品与技术决策

1. **市场盲是物理边界**：planner、prior、research、verifier、forecaster 的输入禁止 quote、odds、
   crowd forecast、label、future fact 和 market-conditioned discovery。Blind 进程不得导入行情/
   decision repository；仅允许读取冻结的 contract/component/evidence artifact。
2. **显式 prior 必填**：保存 reference class/hazard、适用性、样本/选择规则、宽度、失效条件和
   market-blind 声明。无可靠参照时用显式宽先验，不允许空 prior 或市场价 prior。
3. **证据四时态**：每条 revision 保存 `event_at/published_at/observed_at/ingested_at`、raw Artifact、
   source/type、branch、前一 revision 与污染状态；`published_at/observed_at` 不得晚于 episode cutoff。
4. **AI 只提交结构化候选**：`Q`/`U` 的 coherence、`h_c/g_c,t` push-forward、`μ/V/bounds` 由
   Decimal 确定性代码计算。LLM 输出不能直接成为 projection 或 PASS Gate。
5. **首版 U 语义固定**：`Q` 是 world-state→Decimal-string 的联合分布；`U` 是非空、有限、去重的
   coherent distribution 集，且必须包含 `Q`。G5B widening 只可按冻结 coverage policy 添加预注册
   extreme points；无法构造 coherent U 就 ABSTAIN。
6. **一次 provider request=一次 invocation attempt**：retry、fallback、cache hit、人工重跑各建新
   attempt，绝不覆盖、拼接或静默换模型。Provider 网络调用不在 DB transaction 内。
7. **accepted output 才可消费**：request、raw response、parsed output、normalized output、tool receipts、
   validators 和 lineage 全部存在且 hard validator 通过，才可标 `ACCEPTED`。
8. **Blind commit 原子封账**：G6 PASS、submission、全部 spec×token projection、checks、lease、Gate、
   workflow event/outbox 同一 UoW；任何一项失败，`BLIND_COMMITTED` 数必须为 0。
9. **提交后不可变**：submission 只允许 `DRAFT→BLIND_COMMITTED`；commit 后禁止 update/delete。
   新事实、规则/schema 变化或 lease 到期只能 supersede 并创建新 episode。
10. **模型绑定按已批准方案**：DeepSeek V4 Pro=planner/prior；xAI Grok 4.5=Web/X researcher；
    Gemini 3.6 Flash=独立 verifier；Kimi K3=joint forecaster。Packy 只允许显式的无搜索 fallback
    attempt。Anthropic/Claude 永久排除，OpenAI/GPT 不进首版注册表。
11. **测试不联网、不读 key**：使用官方 wire golden fixtures + fake transport；真实 key smoke 是人工、
    一次性、独立记录，不能成为 CI 前提。
12. **不扩张角色**：本期只有 `planner_prior/researcher/verifier/joint_forecaster` 四个生产角色；
    `forecast_challenges` 仅建 append-only schema 骨架，不进入 champion 路径。

## 3. 内部 Checkpoint 与精确文件

这是一个里程碑、一个 manifest；Checkpoint 连续完成，不另建 `-rN` 任务。

### A — `b1000020` Cognition facts 与确定性 forecast

生产文件：

```text
serve/app/models/trading/forecast.py
serve/app/models/trading/{workflow,__init__}.py
serve/app/models/__init__.py
serve/alembic/versions/b1000020_v2_0020_p1b_cognition.py
serve/app/schemas/trading/{evidence,forecast,__init__}.py
serve/app/domain/trading/{probability,__init__}.py
serve/app/repositories/trading/{forecast,__init__}.py
serve/app/logics/trading/{evidence,forecast,__init__}.py
serve/app/orchestrator/trading_state_machine.py
```

创建且只创建 11 表：

```text
priors
evidence_coverage_policies
evidence_revisions
evidence_bundles
evidence_bundle_items
forecast_input_manifests
forecast_submissions
payout_projections
coherence_checks
forecast_challenges
forecast_leases
```

同时只做必要的既有表强化：`forecast_episodes` 增加 cognition timestamps/status；
`gate_decisions` allowlist 增加 `G4/G5A/G5B/G6`。所有新事实 append-only。

### B — `b1000021` AI invocation 事实与 provider-neutral Gateway

生产文件：

```text
serve/app/models/trading/ai.py
serve/app/models/trading/{artifact,control,__init__}.py
serve/app/models/__init__.py
serve/alembic/versions/b1000021_v2_0021_p1b_ai_observability.py
serve/app/services/model_gateway/{__init__,contracts,registry,service}.py
serve/app/services/model_gateway/drivers/{__init__,deepseek,xai,gemini,kimi,packy}.py
serve/app/ai_runtime/{__init__,runner,validator,cache,redaction}.py
```

创建 3 张 UTC 月 RANGE 分区事实表（无 default partition，预建测试覆盖月份）：

```text
ai_invocations
ai_tool_calls
ai_validation_results
```

扩展 `model_role_bindings` 为 typed、版本化 capability binding；扩展
`artifact_lineage_edges.relation` allowlist 为
`READS|PRODUCES|VALIDATES|SUPERSEDES|PROJECTS_TO|USED_BY`，保留旧数据可逆迁移。

### C — 四角色 Prompt、workflow 与 replay

```text
serve/app/prompts/v2/planner_prior/v1.{md,schema.json}
serve/app/prompts/v2/researcher/v1.{md,schema.json}
serve/app/prompts/v2/verifier/v1.{md,schema.json}
serve/app/prompts/v2/joint_forecaster/v1.{md,schema.json}
serve/app/handlers/trading/cognition.py
serve/app/runtimes/trading/cognition.py
```

若仓库现有 handler/runtime 注册方式要求透明更新，可只修改对应 `__init__.py`/显式 registry；
不得启动常驻进程、修改 Base worker 或读取 `latest` 配置。

## 4. 数据与状态合同

### 4.1 AI invocation

生命周期：

```text
PLANNED → STARTED → TOOL_RUNNING* → RESPONSE_RECEIVED → PARSED → VALIDATED
→ ACCEPTED | REJECTED
异常终态：FAILED | TIMEOUT | CANCELLED | UNKNOWN
```

每个 attempt 必存：episode/stage/role/variant/attempt、retry/fallback/cache lineage、release/strategy/
binding、requested/returned provider-route-model、effort/sampling/seed、network/tool/domain policy、
prompt/schema/input manifest hash、request/response/parsed/normalized Artifact、validator rows、时间、usage、
tool/search 次数、provider request ID、estimated/billed cost/currency/pricing snapshot 和终态 reason。

`episode+stage+role+variant+attempt_no` 使用非分区 `idempotency_claims` 保证全局唯一。terminal row
禁止更新/删除。Blind role 的 tool count 必须为 0；researcher/verifier 每个引用必须有 tool receipt 与
raw source Artifact。

### 4.2 Evidence 与 Gate

- `G4`：prior 完整、hash/版本/适用性/失效条件有效。
- `G5A`：四时态、source、raw/hash、cutoff、taint、market-conditioned discovery 全部合格。
- `G5B`：按冻结 policy 返回 `PASS|WIDEN_REQUIRED|ABSTAIN_EVIDENCE_INSUFFICIENT`；widening 算法、
  输入/输出 hash 可重算。
- bundle item 必须引用 exact eligible revision；bundle/spec/schema/prior/strategy/model/prompt/code
  组成 `forecast_input_manifest`，输入乱序不改变 hash。

### 4.3 Forecast、projection 与 lease

- `Q(ω)≥0`、`ΣQ=1`；`U` 非空、每个分布满足同一 schema/constraints，且 `Q∈U`。
- 对 component 每个 contract spec、每个 token 恰有一条 projection：完整 payout distribution `μ`、
  `V=E[payout]`、U 下/上界、h/g/algorithm hashes；只有 Bernoulli payout 可派生 nullable `p_blind`。
- `forecast_lease` 必含 `valid_until`、结构化 invalidation conditions、evidence/schema/spec hashes；纯
  quote/depth/cost/position 变化不得使它失效或触发新 AI invocation。
- G6 任一 hard check 失败：episode 进入 `PRE_COMMIT_TERMINAL`，不生成 committed submission。

## 5. Provider 与安全边界

1. Gateway 只能按 episode 绑定的 exact `model_role_binding_id` 构造 driver，不接受任意 import/model。
2. Driver 只做 wire mapping；重试/fallback/cache/状态机属于 Runner，业务 Logic 不调用 SDK。
3. request 前递归移除 Authorization/API key/Cookie/secret；疑似 secret echo 进入 quarantine，不能
   `ACCEPTED`。prompt/response 正文只进 Artifact Store，不进普通 log/span/Redis。
4. xAI researcher 允许 Web/X tool；Gemini verifier 允许 Search/URL；DeepSeek planner 与 Kimi
   forecaster 固定 `network=NONE/tools=[]`。Packy 固定无搜索。
5. requested 与 returned model 必须分列；返回未 allowlist 的 alias/model 直接 REJECTED。
6. exact cache key 至少包含 role、所有 input manifest hashes、provider/route/model、prompt/schema/code
   version、network/tool policy、sampling；只缓存 `ACCEPTED + network=NONE`。cache hit 仍生成新 invocation，
   cost=0，并引用 source invocation。

## 6. 测试与验收证据

允许新增/修改：

```text
serve/tests/trading/fixtures/p1b_cognition/*.json
serve/tests/trading/fixtures/ai_wire/{deepseek,xai,gemini,kimi,packy}/*.json
serve/tests/trading/unit/test_v2_{probability,evidence_logic,forecast_logic,ai_runner,model_gateway,cognition_state_machine}.py
serve/tests/trading/contract/test_v2_{deepseek,xai,gemini,kimi,packy}_contract.py
serve/tests/trading/integration/test_v2_{0020_cognition_migration,0021_ai_migration,ai_invocation,blind_forecast_workflow}.py
serve/tests/trading/replay/test_v2_p1b_cognition_replay.py
serve/tests/trading/performance/cognition_smoke.py
```

必须证明：

1. success/429/5xx/timeout/truncated JSON/schema error/model drift/tool receipt/secret echo/crash-after-response；
2. retry/fallback/cache 是独立 attempt；失败 attempt 不被缓存，provider 返回后崩溃为 UNKNOWN；
3. late/缺 raw/缺 hash/nested quote-odds-crowd/market-conditioned evidence 均拒绝；
4. 五类 WP-01C semantic fixture 均产生 deterministic Q/U/projection；非法概率、空 U、`Q∉U`、缺
   spec/token projection 均在 commit 前 fail-closed；
5. commit 中任一点 crash 整体 rollback，重试 effect=0；commit 后 update/delete 全拒绝；
6. frozen Artifact 两次 offline replay 的 prior/bundle/input/Q/U/projection/submission/lease hash 全等；
7. 全部 episode 的 action/qualification/capital eligibility 继续为 false；数据库没有 quote→blind FK。

性能硬门（真 PostgreSQL、有界 pool、真实 UoW/constraint，公网模型时间不计）：

- 100 invocation terminalizations/s 持续 60s，每条含 2 tool + 5 validator rows；
- 20 个 8-state/4-contract blind commits/s 持续 60s；
- lost/duplicate/projection mismatch=0，pool wait p95≤20ms；
- 输出 JSON，记录 p50/p95/p99、连接峰值、WAL、RSS、seed、硬件和 commit。

## 7. 验收命令

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests alembic
.venv/bin/pytest -q tests/trading/unit/test_v2_probability.py \
  tests/trading/unit/test_v2_evidence_logic.py \
  tests/trading/unit/test_v2_forecast_logic.py \
  tests/trading/unit/test_v2_ai_runner.py \
  tests/trading/unit/test_v2_model_gateway.py \
  tests/trading/unit/test_v2_cognition_state_machine.py
.venv/bin/pytest -q tests/trading/contract/test_v2_*_contract.py
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0020_cognition_migration.py \
  tests/trading/integration/test_v2_0021_ai_migration.py \
  tests/trading/integration/test_v2_ai_invocation.py \
  tests/trading/integration/test_v2_blind_forecast_workflow.py \
  tests/trading/replay/test_v2_p1b_cognition_replay.py
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/python -m tests.trading.performance.cognition_smoke
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
.venv/bin/alembic heads
.venv/bin/alembic upgrade b1000021 --sql > /tmp/wp02.sql
git diff --check
```

真 PostgreSQL 测试必须 0 skip；manifest 记录真实命令、输出和性能 JSON，不得写预计结果。

## 8. Blocker、非目标与回滚

### Blocker

- 官方 wire 与 golden fixture 无法对应、provider 关键返回字段未确认；
- migration 不能在 literal-empty/existing Base 真 PG roundtrip；
- blind taint、完整 invocation lineage、coherent projection 或 immutable commit 任一不能证明；
- 需要新产品规则或修改既定模型角色。

出现 blocker 必须在 manifest 如实标 `BLOCKED`，不得缩小校验或读取真实 key 伪造通过。

### 非目标

- 不揭示或绑定市场价格；不做 discrepancy critic；
- 不做 edge、decision belief、action、portfolio、shadow/canary/live、订单、资金或账本；
- 不实现 blind challenger/revision 的生产路径；
- 不做 Admin UI、不改 V1、不接私有 CLOB/User WS/Polygon；
- 不把 provider 质量、预测准确率或盈利能力视为本 WP 已验证结论。

### 风险与回滚

- 风险：provider wire 漂移、relay model alias 漂移、tool receipt 不完整、Artifact 写放大、AI 成本失控。
  全部通过 exact binding、budget、Artifact/hash、validator 和 fail-closed 控制。
- 数据库回滚：`alembic downgrade b1000013`；0021→0020→0013 前检查未知下游对象并整次回滚。
- 代码回滚：revert WP-02 提交；原 WP-01C episode 保持 `ROUTED`，不删除历史事实。

## 9. Manifest 合同

完成后只写：

`serve/docs/manifests/wp-02-minimal-cognition-ai-observability.md`

必须包含：精确 changed files、14 张新表/既有表强化、四角色/五 driver fixture、G4–G6 与 AI
lifecycle 证据、taint/cache/retry/fallback/crash 证据、五类 semantic Q/U/projection hash、replay hash、
性能 JSON、全部命令真实结果、blocker/non-goal/rollback，以及删除哈希行口径的 manifest SHA-256。
