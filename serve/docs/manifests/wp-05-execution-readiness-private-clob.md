# COMPLETION MANIFEST — WP-05 · P-stability、Execution Readiness、Private CLOB 与确定性对账

- Work package: `WP-05`
- 状态: **ACCEPTED（审查者已直接关闭范围内 P0/P1）**
- 完成日期: 2026-08-11 EDT
- 任务合同: `serve/docs/tasks/wp-05-execution-readiness-private-clob.md`
- Alembic: `b1000041 → b1000050 → b1000051 (head)`
- 初次实现: DeepSeek V4 Flash，commit `39db96bb022615567d219a45a9056aaae3372d95`
- 审查修复: `f53888fd542833f1b0e394fceb12908eb4f1e6ca`；性能证据修复:
  `5588576a2e30cabb0857a55c6be224cb33c57765`
- 前置: WP-03 accepted manifest SHA `996869e25bf818d0fe58b2463a6784a477f43c15b508fa1ec78d0e28621822b5`；WP-04 accepted manifest SHA `c22daa477f748354538c484fff5957e237a0f03db39907c2767580e957bf638a`
- 资本/网络边界: `authorized_capital=0`、`FIXTURE_ONLY`、仅 fake transport，真实网络/资金/链上副作用=0

## 1. 交付范围（修改文件）

初交 `39db96b…` 修改 81 个代码/迁移/测试文件；主体审查修复 `f53888f…` 修改 53 个文件，
其中 47 个是原交付文件、6 个是必要扩展；`5588576…` 再修正同一性能 harness 的连接池 high-water
证据，accepted code union 仍为 87 个文件。可复现清单：

```bash
git diff --name-only 11af8ba7bfd95dc473f4bb75121e8f27cec3052f..5588576a2e30cabb0857a55c6be224cb33c57765 \
  -- 'serve/**' ':(exclude)serve/docs/**'
```

### Checkpoint A — P-stability / execution-readiness fixtures

```text
serve/requirements.txt
serve/tests/trading/fixtures/p5_execution/{p_execution_readiness_spec_v1,sdk_source_manifest_v1,official_heartbeat_drift_v1,stability_event_log_v1,stability_snapshot_v1,private_clob_golden_v1,user_ws_reconcile_v1}.json
serve/tests/trading/fixtures/p5_execution/p5_helpers.py
serve/tests/trading/unit/test_v2_execution_readiness_spec.py
serve/tests/trading/replay/test_v2_p_stability.py
serve/tests/trading/replay/test_v2_execution_recovery.py
```

### Checkpoint B — `b1000050` Vault / Accounts / Funds / Fencing

```text
serve/.env.example
serve/app/config.py
serve/alembic/versions/b1000050_v2_0050_execution_vault_accounts.py
serve/app/services/vault/{__init__,envelope,service}.py
serve/app/models/trading/{vault,execution}.py
serve/app/repositories/trading/{vault,execution}.py
serve/app/logics/trading/{portfolio,execution}.py
serve/tests/trading/unit/test_v2_{vault_crypto,vault,account_funds,execution_fencing}.py
serve/tests/trading/integration/test_v2_{0050_execution_vault_accounts_migration,vault_accounts_funds,execution_reservations_fencing}.py
```

### Checkpoint C — `b1000051` Authorization / Private CLOB / User WS / Reconcile

```text
serve/alembic/versions/b1000051_v2_0051_execution_orders.py
serve/app/{handlers,logics,models,repositories}/trading/*execution*.py
serve/app/logics/trading/reconciliation.py
serve/app/repositories/trading/{audit,decision,ledger,vault}.py
serve/app/schemas/polymarket/{clob_private,user_ws,data_api}.py
serve/app/services/polymarket/{base,clob_trading_driver,user_ws_driver,data_api_driver}.py
serve/app/orchestrator/trading_state_machine.py
serve/runtimes/trading/{execution,reconciliation,__init__}.py
serve/tests/trading/{unit,contract,integration,replay,performance}/test_v2_*.py
serve/tests/trading/performance/execution_readiness_smoke.py
```

所有显式 exports、ORM model-count/head 合同及上游确定性 hash 跟进均在上述 87 文件清单中。未修改
V1/Admin，未创建 canary/live permission、真实账户/secret、真实资金或链上写入。

## P_STABILITY_MANIFEST

对应 ARCHITECTURE §10.6 P-stability。

- **上游锁定**：WP-03/WP-04 accepted SHA 如页首；P2 execution spec、P3 evaluation spec 与
  `b1000041` 前置均由 readiness spec 锁定。
- **冻结证据**：
  - `p_execution_readiness_spec_v1.json` raw SHA `649dc4634dbab198da393bb886d720e02c3f9573692a87bc22b188c77f4ba18f`，canonical content hash `35e49963dfe8f3ea215386658200a4e4ed6d095b1d863188c87ed62266ea6d0a`，`frozen_at=2026-08-10T00:00:00Z`。
  - `stability_event_log_v1.json` raw SHA `dd9af087f7ceff3d928eb90c45397e913fbaca5d0e9e11921089e1fdb6fe2366`；23 事件覆盖 WS 断线+回补、重复/乱序、背压/过期、restart、model timeout/partial failure、rollback 与 fixed seed。
  - `stability_snapshot_v1.json` raw SHA `4a30732eaab32e5f879d38447f389e0494cb4fa19899b1823f89e1b021d92431`。
- **两次全链 replay 与冻结 snapshot 精确一致**：
  - universe `e4fbf2ae0606d34f2061cd889e43c2c7e24c2373d1e1feef6995af534a7d0500`
  - opportunity `bdd562c94355042d1e64378983dd89c6a4884cd7c69ca58965032c8b866a3995`
  - episode identity `c67e88ec2017325d640706050dadd95c81abd3938bc61ea63402b836968a7abd`
  - processing disposition `bdb6029171ee3149d1ca49c3e4e5bc12b4441bee58ddd213b71165c67c796089`
  - blind commit `5b1f9611d582368bd69559e34e9e01738654993f65602f187abded8c994fce2a`
  - economic action intent `b253e214841747e15f8eed7f124ec0641dcb2532718a3aa8abfdb5360a970022`
  - authorization envelope `7a69c0b55b2c4cf529c8b360af167fd88eaf5a4333bab21708982df08c9e1c15`
  - ledger `b25d71f52f90ed33aaac4efabf70ff06abb02bd3fadcaa2816d9e914c293f585`
  - metric artifact `ce37d4946b7933eebeac083e0e93dd5816a4a09c894af41f78229b687e70984f`
- **故障边界**：序列号/rollback 不再渗入 action/envelope hash；worker restart 从 durable state 续跑；
  `SUBMITTED` 恢复只进 `UNKNOWN`；不确定写入、非法因果转移与不可归因随机差异均 fail closed。
- **命令**：`test_v2_p_stability.py + test_v2_execution_recovery.py` → **14 passed**（0 skip/fail）；
  其中 P-stability 8 条、recovery 6 条。未通过项=0。

## P_EXECUTION_READINESS_MANIFEST

对应 ARCHITECTURE §10.6 P-execution-readiness 8 条 DoD。

1. **SDK / Type 3 / exact wire**：`polymarket-client==0.5.0`，tag `polymarket-client-v0.5.0`，commit
   `974d2e22ca92445d8ab7ecd7715a247f1ea7d65a`；官方 wheel
   `polymarket_client-0.5.0-py3-none-any.whl` SHA-256
   `07001aca7462f9638db3d57e2a69445323ff3cb200a3c851167e27758e0e52c0`。maker/funder 为
   Deposit Wallet，signer/SDK private-key signing actor 为 EOA，`signatureType=3`，ERC-7739 签名可恢复
   EOA。Standard/NegRisk 交易所由 DB 冻结 `neg_risk` 选择；chain/exchange identity 与 trusted
   server clock 在 signer 前 fail closed。L2 签名 exact sent body，`POST /order` 每 attempt 只发一次。
   SDK source manifest raw SHA=`8c87743e1a0c1c1e99e11a376d9e3526f8f6054a43a3588d65cd3ea769449b7e`、
   content hash=`8bad26508f2d0b1a8373579c0eebaa78375a9ef755787acadce6f9f8e4d10e74`；private CLOB
   golden raw SHA=`d8b03bcb7b81b074bcf196c65fab0eba23bd40c57c126747504c907e814af98d`、SDK golden
   SHA=`5d3ef1ccb77d2a484164d76deb2aad2478ef67ec3883c7c4337f31ed53ef9a92`。
2. **0050/0051 数据库不变量**：Vault skeleton 就地强化，active version 唯一、lifecycle/append-only、
   identity/AAD 强绑定；account/funds/reservation/lease 与 authorization/order/trade/reconcile 事实只建一套。
   empty/existing Base、`0051→0050→0041`、non-empty vault precondition 和 unknown-downstream downgrade 全部由真
   PostgreSQL 测试。
3. **Vault**：AES-256-GCM（96-bit nonce/128-bit tag）、canonical identity-bound AAD、keyring version、
   encrypt→verify→atomic activate/retire 轮换。`VaultService` 绑定 runtime identity，历史读取必须显式
   secret version；encrypt/decrypt/rotate/deny 只记录无 secret audit event。
4. **Funds / reservation / fencing**：并发预留不超 funds/cap，`HELD/UNKNOWN` 仍计 local reserved；
   partial fill/cancel 按确认数量精确 consume/release，不可重复消费。execution/heartbeat lease 的
   owner+token+expiry 在 signer、submit、heartbeat、cancel、User WS apply 与 reconcile commit 每个私有副作用前复核，stale owner economic effect=0。
5. **Authorization / kill switch**：action intent 排除 mode/permission/authority；envelope 另行绑定
   intent/account/release/spec/permission/authority/idempotency/fence/two preflights。`authorized_capital=0`
   时增仓为 0，风险降低的 REDUCE/CLOSE/CANCEL/reconcile 可继续 fake path。permission twin 的
   decision/economic hashes 与 shadow 路径一致；capability/cost hash 漂移会废弃 qualification lineage 并
   回到 P-execution-spec，不会原地换配置。
6. **UNKNOWN / User WS / REST 收敛**：`SUBMITTED` crash recovery 进 `UNKNOWN` 且零重发；exact order
   lookup + open-order pagination + trade watermark/lookback 对账；仅 `CONFIRMED` trade 产生经济效果。WS
   sequence gap/status 会进 `RECONCILING`，只有 terminal evidence 且 order/trade/reservation/position/cash/
   ledger diff 全 0 才恢复投影。`ACK/PARTIAL/FILLED/CANCELLED/REJECTED/UNKNOWN/RECONCILED` 全状态、
   timeout/5xx/不可判定 200/cancel race/late fill/重复与乱序均有 fixture 和反例。
7. **Heartbeat**：冻结 `POST /v1/heartbeats`，首空 ID 后回传最新 ID，5s monotonic chain 不跳号；
   失败会 durable hard-stop 新单并强制 cancel/reconcile handler。官方 `POST /heartbeats`
   页面漂移只记录，未双发、fallback 或猜测；drift fixture raw SHA=
   `b0c1de3f28b5b7e8a7ca9aaf70a6b0e18b3d9789476048e57839ca2f4eb6d66d`。
8. **Egress / replay / observability**：Private CLOB、User WS、Data API 无注入 transport 都命中
   egress tripwire；canonical reconcile page hash 可重放；自然键化 forecast/decision/execution hash 不受
   sequence/rollback 影响。User WS reconcile fixture raw SHA=
   `65c94e2d861dfabf924de377b354554b4c77fbca13aa76a32036279df51eed67`。fake calls>0，real
   network/money/chain=0，secret value marker=0。

### 审查整改记录

初交 happy-path 证据通过，但审查发现 Vault active-version/身份绑定、资金精确释放、私有网络
fencing、SUBMITTED 恢复、UNKNOWN 定案、trade confirmation、heartbeat 故障 hard-stop、DB-bound
Standard/NegRisk exchange、trusted clock/identity、WS gap 与 Data API egress 存在 P0/P1 边界缺口。

审查者在 `f53888fd542833f1b0e394fceb12908eb4f1e6ca` 直接修复上述范围内问题，增加
`test_v2_execution_recovery.py`、`test_v2_data_api_egress_guard.py` 及所有对应反例。复验后
又发现原性能 artifact 的 `peak_checked_out=0` 是结束时快照、并非真实峰值；`5588576a2e30cabb0857a55c6be224cb33c57765`
改为 SQLAlchemy Pool checkout/checkin 事件记录 high-water，并硬断言 `0 < peak <= 6`。复验后已知 P0/P1=0，
未创建 remediation WP。

## 2. 命令与真实结果（功能修复 `f53888f…`；accepted evidence code `5588576…`）

```bash
cd /code/pollymarket/v2/serve

python3 -m compileall -q app runtimes tests alembic
# exit 0

.venv/bin/python -c 'from importlib.metadata import version; assert version("polymarket-client") == "0.5.0"'
.venv/bin/pip check
# polymarket-client=0.5.0; No broken requirements found

.venv/bin/pytest -q <WP-05 unit + contract + Data API egress list>
# 133 passed in 1.41s（0 skip/fail）

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0050_execution_vault_accounts_migration.py \
  tests/trading/integration/test_v2_0051_execution_orders_migration.py \
  tests/trading/integration/test_v2_vault_accounts_funds.py \
  tests/trading/integration/test_v2_execution_reservations_fencing.py \
  tests/trading/integration/test_v2_private_order_reconciliation.py \
  tests/trading/integration/test_v2_execution_ledger_reconcile.py \
  tests/trading/replay/test_v2_p_stability.py \
  tests/trading/replay/test_v2_execution_recovery.py
# 56 passed in 38.83s（0 skip/fail）

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
# 1566 passed, 8 warnings in 194.79s（0 skip/fail）

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
# 1777 passed, 8 warnings in 191.97s（0 skip/fail）

.venv/bin/alembic heads
# b1000051 (head)，唯一 head

.venv/bin/alembic upgrade b1000051 --sql > /tmp/wp05.sql
# 7692 lines；secret value markers=0

git diff --check
# exit 0
```

官方 wheel 通过 `pip download --no-deps --only-binary` 从本机缓存复验，SHA 见
`P_EXECUTION_READINESS_MANIFEST` 第 1 条。临时测试/性能数据库残留=0。

## 3. 性能（clean commit、真 PostgreSQL、execution/reconciliation pool=5+1）

- artifact: `/tmp/pm_v2_perf_smoke_5.json`
- SHA-256: `b9c9ebd9dc860dc6e99e36e49f8520ebc559c51891750431819c6eab6d09d941`
- code identity: `5588576a2e30cabb0857a55c6be224cb33c57765`
- seed: `deterministic/wp-05-execution-readiness-performance-v1`
- data scale: 1 account，4,024 envelopes/orders，3,024 steady intents，20×1,000-order reconcile
- `hard_assertions=PASS`

| 门 | 结果 | 门槛 |
|---|---:|---:|
| Gate1 DB preflight + reservation tx p95/p99 | 12.407 / 19.213 ms | p99≤50ms |
| Gate2 fake submit→ACK p95/p99 | 18.827 / 20.242 ms | ≤2s / ≤5s |
| Gate3 User WS→PARTIAL p95/p99 | 4.203 / 5.185 ms（500/500） | ≤100ms / ≤300ms |
| Gate4 REST reconcile p95/p99 | 91.043 / 91.043 ms（20×1,000，diff=0） | ≤10s / ≤30s |
| Gate5 steady intents | 3,024 @ **50.395/s**，60.006s | ≥10/s 持续≥60s |
| Gate5 完整 10s 窗口 | [50.5, 49.8, 49.5, 49.7, 51.5, 51.3] | 报告 |
| Gate6 pool wait p95 / tx p99 | 0.013164 / 18.715 ms | ≤20ms / ≤50ms |
| heartbeat max drift / ID chain | 4.453 ms / no-skip | ≤500ms / true |
| heartbeat failure→stop / reconcile | 6.285 / 47.594 ms（stop/cancel/reconcile） | P0 不被阻塞 |
| fake transport / real network | 4,706 / 0 | >0 / =0 |
| max RSS / peak checked-out | 897,936 KiB / 2（execution=2，reconciliation=1；budget≤6） | 报告 / `0<peak≤6` |

artifact 原始 `window_rates_per_second` 末尾的 `0.1` 是 60s 硬门之后的不完整收尾 bucket，
不属于六个完整 10s 窗口；harness 已按完整窗口判定 PASS。

## 4. Blocker / 非目标 / 回滚

- **激活 blocker（保留）**：官方 `POST /heartbeats` 页面与冻结 `POST /v1/heartbeats`
  合同漂移；真实 provider conformance 未关闭。二者关闭前 capital permission 必须继续为 0。
  WP-05 结论只是 `IMPLEMENTED_FAKE_CONFORMANCE`，不是 Canary 激活。
- **范围内剩余 blocker**：已知 P0/P1=0。
- **非目标**：不授予 Canary/Live、不创建 `authorized_capital>0`、不导入真实账户/secret、
  不访问公网；不做 WP-06 Polygon/relayer/settlement finality；不升级 SDK、不自动适配 heartbeat
  新路径；不做做市/short/套利/ACTIVE_REVALUE/P4/Admin UI/V1。
- **回滚**：保持 `authorized_capital=0`/kill，停止 fake submit，保留 cancel/reconcile evidence；
  数据库按 `b1000051 → b1000050 → b1000041` 降级，每个 DROP 前 fail-closed 检查 unknown
  downstream/非测试 vault/未定订单；代码先 revert `5588576…`、再 revert `f53888f…`，最后按需 revert
  `39db96b…`；
  secret 轮换只追加新 encrypted version，订单/账本纠错只追加 event/reversal。

## 5. 交接

- accepted code: `5588576a2e30cabb0857a55c6be224cb33c57765`；accepted code union=87 files。
- reviewer 最小复验：

  ```bash
  cd /code/pollymarket/v2/serve
  .venv/bin/pytest -q \
    tests/trading/unit/test_v2_vault_crypto.py \
    tests/trading/unit/test_v2_clob_trading_driver.py \
    tests/trading/unit/test_v2_private_egress_guard.py \
    tests/trading/unit/test_v2_data_api_egress_guard.py
  V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
    tests/trading/integration/test_v2_0050_execution_vault_accounts_migration.py \
    tests/trading/integration/test_v2_0051_execution_orders_migration.py \
    tests/trading/integration/test_v2_private_order_reconciliation.py \
    tests/trading/integration/test_v2_execution_ledger_reconcile.py \
    tests/trading/replay/test_v2_p_stability.py \
    tests/trading/replay/test_v2_execution_recovery.py
  V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
    .venv/bin/python -m tests.trading.performance.execution_readiness_smoke
  ```

- 未解决 activation blockers：官方 heartbeat 漂移 + 真实 provider conformance（见 §4）。
- rollback 入口：§4。
- WP-05 已 ACCEPTED；WP-06 合同由审查者在独立交接文档中发布。

COMPLETION_MANIFEST_SHA256: 04e365b4b1c18dc529dd2f6aa73c0cccf29c6a6cab5487787776f74a9bdc2fc9
