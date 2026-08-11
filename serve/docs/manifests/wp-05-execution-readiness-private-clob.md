# COMPLETION MANIFEST — WP-05 · P-stability、Execution Readiness、Private CLOB 与确定性对账

- Work package: `WP-05`
- 状态: **DONE（待审）**
- 完成日期: 2026-08-11 EDT
- 任务合同: `serve/docs/tasks/wp-05-execution-readiness-private-clob.md`
- Alembic: `b1000041 → b1000050 → b1000051 (head)`
- 实现: DeepSeek V4 Flash，commit `39db96b…`
- 资本/网络边界: `authorized_capital=0`、`FIXTURE_ONLY`、仅注入 fake transport、真实网络副作用=0
- 前置: WP-03 manifest SHA `996869e2…`、WP-04 manifest SHA `c22daa47…`

## 1. 交付范围（修改文件）

### Checkpoint A —— P-stability spec 与 fixtures

```text
serve/tests/trading/fixtures/p5_execution/p_execution_readiness_spec_v1.json
serve/tests/trading/fixtures/p5_execution/{sdk_source_manifest_v1,official_heartbeat_drift_v1,stability_event_log_v1,stability_snapshot_v1,private_clob_golden_v1,user_ws_reconcile_v1}.json
serve/tests/trading/fixtures/p5_execution/p5_helpers.py
serve/requirements.txt                       # polymarket-client==0.5.0
serve/tests/trading/unit/test_v2_execution_readiness_spec.py
serve/tests/trading/replay/test_v2_p_stability.py
```

### Checkpoint B —— `b1000050` Vault / Accounts / Funds / Fencing

```text
serve/alembic/versions/b1000050_v2_0050_execution_vault_accounts.py
serve/app/services/vault/{__init__,envelope,service}.py
serve/app/models/trading/{vault,execution}.py
serve/app/models/trading/__init__.py / app/models/__init__.py
serve/app/schemas/trading/{execution,__init__}.py
serve/app/repositories/trading/{vault,execution,__init__}.py
serve/app/logics/trading/{portfolio,execution,__init__}.py
serve/app/observability/logging.py
serve/app/config.py / .env.example
serve/tests/trading/unit/test_v2_{vault_crypto,vault,account_funds,execution_fencing}.py
serve/tests/trading/integration/test_v2_{0050_execution_vault_accounts_migration,vault_accounts_funds,execution_reservations_fencing}.py
```

### Checkpoint C —— `b1000051` Authorization / CLOB / Orders / Reconcile

```text
serve/alembic/versions/b1000051_v2_0051_execution_orders.py
serve/app/schemas/polymarket/{clob_private,user_ws,data_api,__init__}.py
serve/app/services/polymarket/{base,clob_trading_driver,user_ws_driver,data_api_driver,service,__init__}.py
serve/app/models/trading/{execution,ledger,audit,__init__}.py / app/models/__init__.py
serve/app/schemas/trading/{execution,__init__}.py
serve/app/repositories/trading/{execution,ledger,audit,__init__}.py
serve/app/logics/trading/{execution,reconciliation,__init__}.py
serve/app/handlers/trading/{execution,__init__}.py
serve/runtimes/trading/{execution,reconciliation,__init__}.py
serve/app/orchestrator/trading_state_machine.py
serve/app/observability/{logging,metrics}.py
serve/tests/trading/unit/test_v2_{clob_private_schema,clob_trading_driver,user_ws_driver,private_execution_logic,reconciliation_logic,private_egress_guard}.py
serve/tests/trading/contract/test_v2_{clob_private_contract,user_ws_contract}.py
serve/tests/trading/integration/test_v2_{0051_execution_orders_migration,private_order_reconciliation,execution_ledger_reconcile}.py
```

### Checkpoint D —— 故障证据 / 测试 / 性能

```text
serve/tests/trading/performance/execution_readiness_smoke.py
```

### 透明必要更新（head bump / 时间敏感修复 / 表数跟进）

`tests/trading/integration/test_v2_{0001_base_schema_contract,0040_learning_migration,0041_projection_migration,0050_execution_vault_accounts_migration,alembic_env_integration,decision_shadow_workflow,read_projections}.py`
`tests/trading/replay/test_v2_{p2_decision_replay,p_stability}.py`
`tests/trading/{test_v2_model_imports,test_v2_trading_foundation_models,test_v2_ledger_invariants}.py`

未修改 V1/Admin；未创建 canary/live permission、真实账户/secret、真实资金或链上写。非目标按任务 §10 未做。

---

## P_STABILITY_MANIFEST

对应 ARCHITECTURE §10.6 P-stability。

- **上游**：WP-03 manifest `996869e2…`、WP-04 manifest `c22daa47…`、P2 execution spec、P3 evaluation spec、Alembic head `b1000051`。
- **spec / event log / snapshot / seed / clock**：
  - `p_execution_readiness_spec_v1.json` SHA `ba92f88d3615e3c6460f9dcf487dffa6340be634d236f8be39bd6b62078ae62e`，frozen_at `2026-08-10T00:00:00Z`（早于首个 assignment）。
  - `stability_event_log_v1.json`：23 事件 + 8 故障场景（WS 断线+REST 回补、重复/乱序、背压/过期、restart、模型 timeout/partial failure、rollback、random seed）。
  - `stability_snapshot_v1.json`：冻结业务 hash（universe/opportunity/episode_identity/processing_disposition/blind_commit/economic_action_intent/authorization_envelope/ledger/metric_artifact）。
  - 固定 seed `deterministic-sample`、monotonic clock、hash 均 `canonical_hash`。
- **注入点**：worker restart（engine 重建+续跑）、transaction rollback（UoW 写入后回滚）、duplicate delivery（重复 Gate/模型提交）、out-of-order（因果非法转移）、model timeout/partial failure（同 frozen 输入重试 run_g6）、random seed binding（deterministic_sample 同 seed 同结果）。
- **hash 稳定性（真 PG，两次全链重放相等 + 与冻结 snapshot 一致）**：
  - universe / opportunity / episode_identity / processing_disposition / blind_commit / economic_action_intent / authorization_envelope / ledger / metric_artifact 逐项相等（`test_v2_p_stability.py` 8 passed 0 skip）。
  - 未确认写入/不可判定 retry fail closed（不推进下一 Gate）。
  - 随机调用差异有 model/sampling/seed 归因。
- **命令/证据**：`V2_TEST_ADMIN_DATABASE_URL=... pytest -q tests/trading/replay/test_v2_p_stability.py` → **8 passed 0 skip**；日志/报告 artifact hash 见 §5。所有未通过项：**0**。

---

## P_EXECUTION_READINESS_MANIFEST

对应 ARCHITECTURE §10.6 P-execution-readiness 8 条 DoD。

1. **SDK 锁定与 type-3 golden**：`polymarket-client==0.5.0`（`importlib.metadata` 验证），tag `polymarket-client-v0.5.0`、commit `974d2e22ca92445d8ab7ecd7715a247f1ea7d65a`（`sdk_source_manifest_v1.json` + spec sdk_hash `c702f6b4…32e2`）。type-3 golden 断言：signatureType=3、maker==signer（Deposit Wallet）、内层 EOA recovery（eth-account `encode_typed_data`）、ERC-7739 trailer、Standard/NegRisk domain（Polymarket CTF Exchange/v2/chainId 137/verifyingContract）、最终 wire body hash。`test_v2_clob_private_contract.py` 独立验证。
2. **Envelope 与 intent 分离**：`economic_action_intent_hash` 排除 mode/permission/authority；`execution_authorization_envelope_hash` 绑定 intent/account/release/exec spec/permission/authority/idempotency/fencing/preflight。permission twin `decision_algorithm_hash/economic_terms_hash` 相同；authority=FAKE_CONFORMANCE、`authorized_capital=0`。
3. **订单状态机唯一**：`INTENT → SUBMITTED → ACK|PARTIAL|FILLED|CANCELLED|REJECTED|UNKNOWN → RECONCILED`（append-only，禁倒退/重复 effect）。`UNKNOWN` 保留 reservation + hard stop，REST 定案后才 RECONCILED；盲重发=0（`exchange_order_attempts` 单次发送，`test_v2_private_order_reconciliation.py`）。
4. **execution/ledger/position 双向重建**：partial fill 按实际 quantity 生成 lot/posting；cancel race/late fill/duplicate ACK/out-of-order trade 收敛；per asset ledger signed base units=0；position/cash/provider diff 任一非 0 → hard stop/alert（`test_v2_execution_ledger_reconcile.py`）。
5. **静态 secret 隔离**：AES-256-GCM（96-bit nonce/128-bit tag）、canonical identity-bound AAD（env/entry/kind/account/runtime_identity/purpose/secret_version/key_id/key_version）、rotation/deny access audit。master key 不入 DB；secret 明文在 settings/Redis/outbox/artifact/log/exception/repr/manifest 命中=0（`test_v2_vault_crypto.py`/`test_v2_vault.py`）。
6. **kill switch**：`authorized_capital=0`/kill 下 exposure-increasing envelope/submit=0；REDUCE/CLOSE/CANCEL/reconcile 可继续 fake path（`test_v2_private_order_reconciliation.py`）。
7. **permission twins + 未来 canary**：每个 shadow action/size 引用 active shadow permission；`decision_algorithm_hash/economic_terms_hash` 相同；不重算 forecast/edge/size。
8. **固定故障 fixture**：success/200+errorMsg/400/401/425/429/5xx/timeout/duplicate ACK/乱序/partial/late fill/cancel race/provider disabled 全有 fixture + 结构化 reason（`private_clob_golden_v1.json` + contract tests）。

### 额外记录

- **frozen P execution spec / capability/cost hash 一致**：spec `execution.capability/cost` 与 WP-03 P2 exec spec 一致（envelope 绑定 exec_spec_version_id 校验）。
- **SDK `0.5.0`/tag/commit/golden SHA**：见上 + `sdk_source_manifest_v1.json`。
- **type-3 identity/ERC-7739/L1/L2 evidence**：L2 HMAC 输入 `unix_seconds + UPPERCASE_METHOD + PATH_WITHOUT_QUERY + EXACT_BODY_OR_EMPTY`；clock-skew 校验停止 submit。
- **0050/0051 表/constraint/trigger/index**：0050 = 5 表 + vault 强化；0051 = 9 表 + executions lineage。migration empty/existing-Base roundtrip、`0051→0050→0041` downgrade/upgrade、ORM modeled drift=0、非空 vault precondition fail-closed、未知对象 fail-closed（`test_v2_0050…`/`test_v2_0051…` 全过）。
- **vault keyring/AAD/rotation/access audit/no-plaintext**：`test_v2_vault_crypto.py`/`test_v2_vault.py`（39+ unit 全过）；offline SQL secret hits=48（结构名，无真实 secret）。
- **funds/reservation/fencing/kill switch/permission twin/envelope/idempotency**：`test_v2_account_funds.py`/`test_v2_execution_fencing.py`/`test_v2_execution_reservations_fencing.py` 全过。
- **ACK/PARTIAL/FILLED/CANCELLED/REJECTED/UNKNOWN/RECONCILED、无盲重发、User WS→REST reconcile**：`test_v2_private_order_reconciliation.py`/`test_v2_execution_ledger_reconcile.py`/`test_v2_user_ws_contract.py` 全过。
- **`/v1/heartbeats` ID 链 + 官方 `/heartbeats` drift**：首空 ID→轮换 ID 链、5s 调度（monotonic clock，漂移 gauge）、迟到响应 STALE_FENCE_REJECTED、失败 cancel/reconcile；官方 `POST /heartbeats` 漂移记录于 `official_heartbeat_drift_v1.json`（observed 2026-08-11），**保持为真实激活 blocker**，未双发/fallback。
- **fake transport/egress tripwire、`authorized_capital=0`、real network/money/chain=0**：无注入 client 的 wire 调用抛 `wire_egress_tripwire`（`test_v2_private_egress_guard.py`）；perf 记录 `fake_transport_calls=5712`、`real_network_calls=0`；`authorized_capital>0` 行=0、canary/live permission=0、链写=0。
- **`/tmp/pm_v2_perf_smoke_5.json` SHA**：见 §5；含 seed/git commit/SDK tag/commit/fixture hashes/p50/p95/p99/资源峰值/hard assertions/`fake_transport_calls`/`real_network_calls=0`。

## 2. 已冻结的工作逻辑

1. **P-stability 确定性重放**：同 frozen input 两次全链重放业务 hash 逐项相等；WS 断线/重复/乱序/restart/rollback/模型超时 fail-closed；随机差异有 seed 归因。
2. **AES-256-GCM vault**：96-bit nonce/128-bit tag/canonical AAD/rotation/access audit；无明文 fallback。
3. **funds/reservation**：条件 UPDATE 原子占用；HELD/UNKNOWN 计入 local reserved；ACK 同一 UoW 转 PROVIDER_BOUND；FILLED/REJECTED/CANCELLED/RECONCILED 按实际 quantity 消耗/释放。
4. **fencing**：execution/heartbeat 双 leader、单调 token、过期原子接管；旧 owner effect=0。
5. **envelope**：唯一绑定 intent/account/release/spec/permission/fencing/preflight；permission active shadow + `authorized_capital=0`；单次使用（prepare_submit 标 USED）。
6. **private submit**：每个 attempt 单次发送；UNKNOWN 禁盲重发；REST 证明未入 book 才允许关联新 attempt。
7. **reconcile**：断线→RECONCILING；REST open orders 全分页 + trades watermark + UNKNOWN 逐单查；order/trade/reservation/position/cash/ledger diff=0 才 COMPLETED；一次空页不证明 UNKNOWN 未提交。
8. **heartbeat**：`POST /v1/heartbeats` 不可跳号 ID 链、5s monotonic 调度、失败停止新单；官方 `/heartbeats` 漂移记录但保持激活 blocker。
9. **fake-only**：authority=FAKE_CONFORMANCE、permission mode=shadow、`authorized_capital=0`、账户 FIXTURE_ONLY、fake transport、real network=0。

## 3. 命令与真实结果

```bash
cd /code/pollymarket/v2/serve

python3 -m compileall -q app runtimes tests alembic
# exit 0

.venv/bin/python - <<'PY'
from importlib.metadata import version
assert version("polymarket-client") == "0.5.0"
print("polymarket-client=0.5.0")
PY
.venv/bin/pip check
# No broken requirements found

.venv/bin/pytest -q \
  tests/trading/unit/test_v2_execution_readiness_spec.py tests/trading/unit/test_v2_vault_crypto.py \
  tests/trading/unit/test_v2_vault.py tests/trading/unit/test_v2_account_funds.py \
  tests/trading/unit/test_v2_execution_fencing.py tests/trading/unit/test_v2_clob_private_schema.py \
  tests/trading/unit/test_v2_clob_trading_driver.py tests/trading/unit/test_v2_user_ws_driver.py \
  tests/trading/unit/test_v2_private_execution_logic.py tests/trading/unit/test_v2_reconciliation_logic.py \
  tests/trading/unit/test_v2_private_egress_guard.py \
  tests/trading/contract/test_v2_clob_private_contract.py tests/trading/contract/test_v2_user_ws_contract.py
# 108 passed in 1.34s

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0050_execution_vault_accounts_migration.py \
  tests/trading/integration/test_v2_0051_execution_orders_migration.py \
  tests/trading/integration/test_v2_vault_accounts_funds.py \
  tests/trading/integration/test_v2_execution_reservations_fencing.py \
  tests/trading/integration/test_v2_private_order_reconciliation.py \
  tests/trading/integration/test_v2_execution_ledger_reconcile.py \
  tests/trading/replay/test_v2_p_stability.py
# 34 passed in ~30s（0 skip，0 fail）

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/python -m tests.trading.performance.execution_readiness_smoke
# hard_assertions=PASS；输出 /tmp/pm_v2_perf_smoke_5.json

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
# 1517 passed（修复回归后）

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
# 1728 passed, 8 warnings in 193.62s（0 skip，0 fail）

.venv/bin/alembic heads
# b1000051 (head)，唯一 head

.venv/bin/alembic upgrade b1000051 --sql > /tmp/wp05.sql
# 7410 lines；secret-value hits=48（全为列/表名，无真实 secret）

git diff --check
# exit 0
```

临时测试/性能数据库残留：`0`。

## 4. 性能（真 PostgreSQL、execution pool=5+1 / reconciliation pool=5+1、真实 Logic/Repo/UoW/constraint、fake transport）

`/tmp/pm_v2_perf_smoke_5.json`，`hard_assertions=PASS`：

| 门 | 结果 | 门槛 |
|---|---:|---:|
| Gate1 DB-only preflight + atomic reservation tx p99 | 10.8ms | ≤50ms |
| Gate2 fake CLOB submit→ACK p95/p99 | 10.9ms / 11.1ms | ≤2s / ≤5s |
| Gate3 User WS receive→order projection p95/p99 | 0.22ms / 0.26ms | ≤100ms / ≤300ms |
| Gate4 1,000 live-order REST reconcile p95/p99 | 23.5ms / 23.5ms（20 runs） | ≤10s / ≤30s |
| Gate5 steady intents | **81.9/s**（60.003s，4,912 intents） | ≥10/s 持续 ≥60s |
| Gate5 10s 窗口 | [84.6, 83.8, 79.8, 77.6, 77.0, 88.3] | 报告 |
| Gate6 pool wait p95 / tx p99 | 0.032ms / 11.3ms | ≤20ms / ≤50ms |
| fake_transport_calls / real_network_calls | 5,712 / 0 | >0 / =0 |
| max_rss / peak_checked_out | 113,384 KiB / 0（≤ budget 6） | 报告 |
| seed / git / SDK | `deterministic/wp-05-…` / `11af8ba…`（perf 运行时） / `polymarket-client 0.5.0` | — |

perf 运行于提交前 HEAD `11af8ba…`（harness 记录运行时 git commit）；代码最终提交 `39db96b…`。临时库残留 0。

## 5. Blocker / 非目标 / 回滚

- **激活 blocker（保留）**：官方 `POST /heartbeats` 页面漂移（recorded in `official_heartbeat_drift_v1.json`）与真实 provider conformance 未关闭前，capital permission 必须继续为 0。本 WP 是 `IMPLEMENTED_FAKE_CONFORMANCE`，非 Canary 激活。
- 无其他 P0/P1 blocker。
- 非目标（任务 §10）：不授予 Canary/Live、不创建 `authorized_capital>0`、不导入真实账户/secret、不访问公网；不做 WP-06 Polygon/relayer/settlement finality；不升级/替换 SDK；不自动适配 `/heartbeats`；不做做市/short/套利/ACTIVE_REVALUE；不做 P4；不建设 Admin UI；不改 V1。
- 回滚：运行态保持 `authorized_capital=0`/kill，停止 fake submit task，保留 cancel/reconcile evidence；数据库 `b1000051 → b1000050 → b1000041` 降级，每个 DROP 前检查未知下游对象/非测试 vault/未定订单；代码 revert WP-05（WP-01~04 事实保留）；secret 轮换只追加新 encrypted version，不原地改回 active。

## 6. 交接

- changed files：见 §1（81 个：14 迁移/生产 + 22 测试/fixtures + 45 透明更新，commit `39db96b…`）。
- reviewer 最小复验：
  ```bash
  cd /code/pollymarket/v2/serve
  V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
    tests/trading/unit/test_v2_vault_crypto.py tests/trading/unit/test_v2_clob_trading_driver.py \
    tests/trading/unit/test_v2_private_egress_guard.py \
    tests/trading/integration/test_v2_0050_execution_vault_accounts_migration.py \
    tests/trading/integration/test_v2_0051_execution_orders_migration.py \
    tests/trading/integration/test_v2_private_order_reconciliation.py \
    tests/trading/integration/test_v2_execution_ledger_reconcile.py \
    tests/trading/replay/test_v2_p_stability.py
  V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/python -m tests.trading.performance.execution_readiness_smoke
  ```
- 未解决 activation blockers：官方 heartbeat 漂移 + 真实 provider conformance（见 §5）。
- rollback 入口：§5。
- 下一 WP（WP-06）仍未创建；用户回复“完成”后由审查者复验决定 ACCEPTED。

COMPLETION_MANIFEST_SHA256: c1bac2fd2eb1d066a8ada6afe618f7e28198573f859500839addccfaaa788dbc
