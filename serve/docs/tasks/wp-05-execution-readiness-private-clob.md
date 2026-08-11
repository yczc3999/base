# WP-05 — P-stability、Execution Readiness、Private CLOB 与确定性对账

> 状态：**ACCEPTED（审查通过）**
> 前置：`WP-04` 已 ACCEPTED；Alembic head=`b1000041`
> 执行模型：DeepSeek V4 Flash
> 唯一完成交付：`serve/docs/manifests/wp-05-execution-readiness-private-clob.md`
> 资本/网络边界：`authorized_capital=0`；仅注入 fake transport；真实网络副作用=0
> 最后更新：2026-08-11 EDT

## 0. 唯一执行路线

1. 本任务是一个 WP、四个连续 checkpoint，严格按 `A → B → C → D` 完成；不得拆成 `-rN`，
   不得创建 checkpoint manifest，也不得等待用户逐段确认。
2. 全里程碑只有一个 completion manifest。其内部证据小节顺序固定为：
   `P_STABILITY_MANIFEST` **在前**，`P_EXECUTION_READINESS_MANIFEST` **在后**；不得另建同名文件。
3. 本 WP 只证明稳定性和执行实现就绪，不授予资本权限。所有 permission fixture 必须为
   `mode=shadow`、`authorized_capital=0`；所有账户必须为 `FIXTURE_ONLY`；所有 HTTP、WS、SDK、
   heartbeat、User WS、订单和对账调用必须走可注入 fake transport。验收期间 DNS/socket/公网 HTTP、
   公网 WS、真实签名提交、真实撤单、真实余额变更、链上写入和资金副作用总数均为 0。
4. 固定纵向链只有一条：

```text
frozen P2 execution spec + accepted P3 facts
→ deterministic stability replay
→ AES-GCM vault + fixture account + atomic funds/reservation + fenced leaders
→ authorization envelope
→ L1/L2 auth + type-3/ERC-7739 signed wire body
→ fake CLOB submit/cancel + User WS
→ REST orders/trades reconcile
→ order/trade/position/cash/ledger convergence
```

5. Checkpoint A 的全部稳定性断言通过后才进入 B；B 的 `b1000050` 迁移、vault、账户、资金和租约通过后
   才进入 C；C 的 `b1000051` 与完整故障状态机通过后才进入 D。
6. 发现范围内会造成 secret 泄露、重复经济效果、盲重发、资金超额预留、fencing 失效、状态倒退、
   对账差异、回放漂移或 migration 破坏的 P0/P1，直接在本任务允许文件内修复并复验。
7. 文件存在、mock 返回 200、SDK 能 import、单元测试通过或实现者写 `DONE` 都不是完成证据。

## 1. 目标与用户价值

把 WP-01～04 已冻结的 sensing、blind forecast、decision、shadow ledger 和 evaluation 链变成一条在故障下
仍可确定性恢复、且具备私有 CLOB 写协议实现的执行链，同时保持真实资本和真实外部副作用为零。必须证明：

- 固定 event log/snapshot 在断线、乱序、重复、背压、worker 重启、模型部分失败和事务回滚后，业务 hash 不漂移；
- signer、L2 token 和 passphrase 只在 execution plane 的授权解密窗口中出现，其他持久层/缓存/日志/API
  明文为 0；
- 并发 intent 使用 PostgreSQL 原子预留，旧 leader 的 fencing token 永远不能继续 heartbeat 或提交；
- type-3 Deposit Wallet 身份、SDK 版本、签名向量、L1/L2 header 和 exact body bytes 可复现；
- 提交结果不确定时只进入 `UNKNOWN` 并对账，绝不换 salt/timestamp 盲重发；
- User WS 无 replay 的断线通过 REST open orders/trades 回补，最终使订单、成交、仓位、现金和账本差异为 0；
- kill switch/permission 为 0 时增仓数始终为 0，但获批的风险降低/撤单/对账路径不被阻断。

本 WP 的“就绪”是 `IMPLEMENTED_FAKE_CONFORMANCE`，不是 Canary 激活。官方 heartbeat 页面漂移和真实 provider
conformance 必须显式保留为后续资本激活 blocker，不能用本 WP 的 fake 证据冒充实盘验证。

## 2. 已确认且不可改写的技术决策

1. **SDK 完全锁定**：运行依赖固定为 `polymarket-client==0.5.0`；官方仓库
   `https://github.com/Polymarket/py-sdk`，tag=`polymarket-client-v0.5.0`，tag commit=
   `974d2e22ca92445d8ab7ecd7715a247f1ea7d65a`。`requirements.txt` 必须精确 `==`，禁止范围版本、
   未锁 VCS head、vendoring 或另一个 CLOB SDK。fixture/manifest 同时记录 package、tag、commit 和 golden hash。
2. **Type 3 身份分离**：签名主体/SDK signer 是 EOA；`funder` 和 maker 是 Deposit Wallet；
   `signatureType=3`（`POLY_1271`），签名使用 ERC-7739 包装（任务输入中的 `ERP7739` 即此协议）。
   传给 SDK 的 private-key signer 必须是 EOA，wallet/funder 必须是 Deposit Wallet。锁定 SDK 的 type-3 wire
   golden 还必须断言：外层 order 的 `maker`/`funder` 为 Deposit Wallet，`signer` 与 SDK
   private-key signing actor 为 EOA，ERC-7739 签名可恢复到该 EOA；不得把 EOA 当
   funder/maker，也不得手工拼接或改写 SDK 生成的 wrapper。
3. **CLOB V2**：chainId=137；订单 EIP-712 domain 固定
   `Polymarket CTF Exchange/version=2`，Standard/NegRisk exchange 由冻结 market config 选择。最终发送 body
   bytes、order hash、SDK identity 和签名向量 hash 必须在发送前持久化其 hash；数据库、日志和 manifest
   不保存 private key、L2 secret/passphrase 或原始 signature。
4. **L1/L2**：L1 只用于 fixture 中创建/派生 API credential 的签名向量；L2 HMAC 输入严格为
   `unix_seconds + UPPERCASE_METHOD + PATH_WITHOUT_QUERY + EXACT_BODY_OR_EMPTY`，必须对最终发送的同一 body
   bytes 签名。时钟偏差超过冻结阈值直接停止 submit。
5. **Heartbeat 合同固定而不自动漂移**：本实现固定 `POST /v1/heartbeats`。首次 body 为
   `{"heartbeat_id":""}`，之后每次回传上次响应的最新 `heartbeat_id`，形成不可跳号的 ID 链；调度间隔
   5s，使用 monotonic clock，漂移≤500ms。失败立即停止新单、触发 cancel/reconcile。
6. **必须记录官方页面漂移**：截至 2026-08-11，官方页面
   `https://docs.polymarket.com/api-reference/trade/send-heartbeat` 展示的是 `POST /heartbeats` 和
   `{"status":"ok"}`，未展示 `heartbeat_id` 链；它与本仓库冻结的 `/v1/heartbeats` 合同不一致。
   `official_heartbeat_drift_v1.json` 必须保存 observed_at、URL、frozen/observed contract、差异 reason 和
   fixture hash。不得静默切到页面新路径、双发两个路径或通过 fallback 猜测；真实 provider 激活保持阻塞。
7. **私有 submit 不继承公共 REST 自动重试**：`POST /order` 每个 attempt 只发送一次。socket write、
   response header/body timeout、断连、5xx 或不可判定 200 body 都进入 `UNKNOWN`；禁止换 salt、timestamp、
   signature 或 body 盲重发。只有 REST/order hash/open orders/recent trades 明确证明未入 book，且冻结 reason
   允许时，才创建关联的新 attempt；旧 attempt 永久保留。
8. **订单状态机唯一**：append-only event 顺序为
   `INTENT → SUBMITTED → ACK|PARTIAL|FILLED|CANCELLED|REJECTED|UNKNOWN → RECONCILED`。
   ACK 后可继续 PARTIAL/FILLED/CANCELLED；重复/乱序事件只补 provenance，不能倒退状态或重复 ledger effect。
   `UNKNOWN` 期间 reservation 保持、增仓 hard stop，直到 REST 定案；不能把“没查到一次”当未提交。
9. **User WS 不是事实终局**：断线即 `execution=RECONCILING`，停止增仓；重连后仍先分页拉全部 open orders，
   按冻结 watermark/lookback 拉 trades，并逐单查询 UNKNOWN。只有订单、trade、reservation、position、cash、
   ledger 差异全为 0 且 reconciliation manifest 已持久化，才恢复 fake `LIVE` 投影。
10. **Vault**：专用 envelope encryption 使用 AES-256-GCM、每版本唯一 96-bit nonce、128-bit tag、
    environment/KMS keyring、canonical identity-bound AAD。AAD 至少绑定 env、entry、secret kind、account、
    runtime identity、purpose、secret version、key id/version。错误 identity/AAD、tamper 或未知 key version
    必须认证失败，无 legacy/plaintext fallback。
11. **Vault 轮换与审计**：轮换顺序为 encrypt new → decrypt/verify → 原子 activate new/retire old；历史调用
    必须显式 secret version。每次 encrypt/decrypt/rotate/deny 都追加 access event，保存 identity、purpose、
    entry/version、key version、result/reason，不保存 secret。master key 不入 DB；通用 settings、Redis、
    outbox、artifact、log/trace、Admin/API、exception/repr、manifest 的 secret 明文命中数必须为 0。
12. **资金与 reservation**：金额/份额只用 base-unit integer/Decimal。preflight 用条件 UPDATE 或
    `SELECT ... FOR UPDATE` 原子占用；禁止先查后写。`HELD/UNKNOWN` 继续计入 local reserved；ACK 后只有在
    同一 UoW 已把等额 provider open-order reserve 纳入 current funds 时才转 `PROVIDER_BOUND`，不得出现漏计或
    双计窗口；FILLED/REJECTED/CANCELLED/RECONCILED 按实际 quantity 消耗/释放。
13. **Leader fencing**：每账户分别只有一个 execution leader 和 heartbeat leader；获取/接管租约必须使
    fencing token 单调增加。每次签名、submit、heartbeat、cancel、User WS apply 和 reconcile commit 都校验
    token；旧 owner 的迟到 ack/heartbeat 只能追加 stale evidence，不能改变 current 状态。
14. **Envelope 与 intent 分离**：`economic_action_intent_hash` 仍排除 mode/permission id/authority；driver
    不重算 forecast、edge 或 size。`execution_authorization_envelope_hash` 另行绑定 intent、account、release、
    execution spec、permission、authority、idempotency key、fencing token 和两次 preflight hash。
    本 WP authority 仅 `FAKE_CONFORMANCE`，permission `authorized_capital=0`。
15. **Kill switch**：阻止所有 exposure-increasing envelope/submit；已证明降低风险的 REDUCE/CLOSE、CANCEL、
    heartbeat shutdown 和 reconcile 可继续。任何 capability/cost/hash 与 `P_EXECUTION_SPEC_MANIFEST` 不同，
    必须废弃旧 qualification lineage 并回到 P-execution-spec，不能就地替换配置。

## 3. 依赖与必读

执行前按顺序核验：

1. `/code/pollymarket/docs/v2/ARCHITECTURE.md` §3.4、§4.5、§10.4、§10.6；
2. `serve/docs/v2-implementation-contract.md` §3–6、§8、§11–15；
3. `serve/docs/polymarket-integration-design.md` §6–10、§13–17；
4. `serve/docs/performance-cache-database-design.md` §2–3、§5.3、§7–8、§10–12、§15；
5. `serve/docs/tasks/wp-03-market-relative-decision-shadow-ledger.md`、accepted WP-03 manifest；
6. `serve/docs/tasks/wp-04-learning-evaluation-read-projections.md`、accepted WP-04 manifest；
7. 当前 `b1000041` migration、ORM metadata、UoW、Outbox、ledger/replay 和 redaction 实现。

上游 manifest/hash、Alembic head 或 frozen execution spec 不一致即停止受影响 checkpoint，记录
`UPSTREAM_MANIFEST_MISMATCH`，不得自行重新解释产品规则。

## 4. Checkpoint A — P-stability 与 execution-readiness spec

### 4.1 精确文件

新增：

```text
serve/tests/trading/fixtures/p5_execution/p_execution_readiness_spec_v1.json
serve/tests/trading/fixtures/p5_execution/sdk_source_manifest_v1.json
serve/tests/trading/fixtures/p5_execution/official_heartbeat_drift_v1.json
serve/tests/trading/fixtures/p5_execution/stability_event_log_v1.json
serve/tests/trading/fixtures/p5_execution/stability_snapshot_v1.json
serve/tests/trading/fixtures/p5_execution/private_clob_golden_v1.json
serve/tests/trading/fixtures/p5_execution/user_ws_reconcile_v1.json
serve/tests/trading/fixtures/p5_execution/p5_helpers.py
serve/tests/trading/unit/test_v2_execution_readiness_spec.py
serve/tests/trading/replay/test_v2_p_stability.py
```

修改：

```text
serve/requirements.txt
```

只有当新 stability test 先证明现有实现失败时，才可做最小修复：

```text
serve/app/ai_runtime/runner.py
serve/app/outbox/consumer.py
serve/app/outbox/repository.py
serve/app/outbox/sweeper.py
serve/app/logics/trading/market_data.py
serve/app/logics/trading/forecast.py
serve/app/logics/trading/decision.py
serve/app/logics/trading/execution.py
serve/app/logics/trading/replay.py
serve/app/orchestrator/trading_state_machine.py
```

### 4.2 冻结内容与 Gate

`p_execution_readiness_spec_v1.json` 必须是 canonical JSON，至少冻结：上游 manifest hashes、SDK
package/tag/commit、type-3 identity mapping、CLOB V2 domains、L1/L2 bytes、`/v1/heartbeats` ID 链、
User WS/REST reconcile watermark、capability/cost hashes、order transition table、UNKNOWN retry matrix、
reservation/fencing/vault AAD schema、kill-switch 降级矩阵、fake-only/`authorized_capital=0` 和 first-assignment
时间。spec 的 `frozen_at` 必须早于首个 account、reservation、authorization envelope 和 order attempt。

固定 event log 必须覆盖 WS 断线+REST 回补、重复/乱序、队列背压/过期、worker restart、模型 timeout/
部分失败、事务 rollback 和随机调用 seed。相同 frozen input 的以下 hash 必须逐项相等：universe、opportunity、
episode identity、processing disposition、blind commit、economic action intent、authorization envelope、ledger、
metric artifact。未确认写入/不可判定 retry 不得推进下一 Gate。A 不创建 manifest；结果留待 D 写入唯一 manifest。

## 5. Checkpoint B — `b1000050` Vault、Accounts、Funds、Reservations、Fencing

### 5.1 精确生产文件

```text
serve/.env.example
serve/app/config.py
serve/app/services/vault/__init__.py
serve/app/services/vault/envelope.py
serve/app/services/vault/service.py
serve/app/models/trading/vault.py
serve/app/models/trading/execution.py
serve/app/models/trading/__init__.py
serve/app/models/__init__.py
serve/app/schemas/trading/execution.py
serve/app/schemas/trading/__init__.py
serve/app/repositories/trading/vault.py
serve/app/repositories/trading/execution.py
serve/app/repositories/trading/__init__.py
serve/app/logics/trading/portfolio.py
serve/app/logics/trading/execution.py
serve/app/logics/trading/__init__.py
serve/app/observability/logging.py
serve/alembic/versions/b1000050_v2_0050_execution_vault_accounts.py
```

`config.py/.env.example` 只增加 keyring/secret **reference**、provider endpoint/timeout 和 execution egress
mode 的 typed 基础设施配置；示例不得出现真实或可用 key。strategy、capital permission、账户 token 明文仍不进 env。

### 5.2 `b1000050` 唯一数据库对象

0050 强化 0002 已存在的三张 vault skeleton 表，不得重建平行 vault：

```text
secret_vault_entries
secret_vault_versions
secret_access_events
```

必须增加/约束：secret kind、允许的 execution-plane identity；`(entry_id,version_no)` 唯一；
`(key_id,key_version,nonce)` 唯一；canonical `aad_context` + `aad_hash` + ciphertext hash；每 entry 最多一个
ACTIVE version；version/access event append-only；entry 只允许 active→disabled。现有 skeleton 任一非空行若
无法无歧义补齐 identity/AAD/version，migration 必须在 version 前进前整体回滚并报
`VAULT_BACKFILL_DECISION_REQUIRED`，禁止猜 secret kind 或解密旧值。

0050 新建且只新建：

```text
pm_accounts
pm_balance_allowance_snapshots
account_funds_current
capital_reservations
execution_leases
```

- `pm_accounts`：稳定 account key、provider/chain、fixture/prod identity、Deposit Wallet funder/maker、EOA
  signing identity、wallet type、signature type=3、signer/L2 secret refs+versions、release/permission、状态与
  `network_mode`；本 WP 插入值只能 `FIXTURE_ONLY`。
- `pm_balance_allowance_snapshots`：append-only provider/fixture observation；account/asset/spender、balance、
  allowance、provider reserved、observed time、request/hash、fencing token 和 completeness。
- `account_funds_current`：可从 snapshot+reservation 重建的 CAS projection；confirmed/provider-reserved/
  local-reserved/available 恒等式非负，source snapshot 和 reconciliation watermark 必填。
- `capital_reservations`：intent/account/asset/idempotency 唯一；金额>0；状态只允许
  `HELD|UNKNOWN|PROVIDER_BOUND|CONSUMED|RELEASED`；UNKNOWN 不释放；状态转移和 funds current 在同一 UoW。
- `execution_leases`：`(account_id,lease_role)` 唯一，role=`EXECUTION|HEARTBEAT`；owner、lease_until、
  单调 fencing token、最新 heartbeat ID/hash、optimistic version；过期接管原子递增 token。

所有金额使用 base-unit `NUMERIC(38,0)`/整数，时间使用 UTC `TIMESTAMPTZ`，地址/外部 ID 使用
`TEXT COLLATE "C"`。migration 为 literal frozen DDL，不 import live ORM；空库、existing Base、
`upgrade→downgrade→upgrade`、非空 vault precondition rollback 和未知下游对象 fail-closed 均必须测试。

## 6. Checkpoint C — `b1000051` Authorization、Private CLOB、User WS、Orders、Trades、Reconcile

### 6.1 精确生产文件

```text
serve/app/schemas/polymarket/clob_private.py
serve/app/schemas/polymarket/user_ws.py
serve/app/schemas/polymarket/data_api.py
serve/app/schemas/polymarket/__init__.py
serve/app/services/polymarket/base.py
serve/app/services/polymarket/clob_trading_driver.py
serve/app/services/polymarket/user_ws_driver.py
serve/app/services/polymarket/data_api_driver.py
serve/app/services/polymarket/service.py
serve/app/services/polymarket/__init__.py
serve/app/models/trading/execution.py
serve/app/models/trading/ledger.py
serve/app/models/trading/audit.py
serve/app/models/trading/__init__.py
serve/app/models/__init__.py
serve/app/schemas/trading/execution.py
serve/app/schemas/trading/__init__.py
serve/app/repositories/trading/execution.py
serve/app/repositories/trading/ledger.py
serve/app/repositories/trading/audit.py
serve/app/repositories/trading/__init__.py
serve/app/logics/trading/execution.py
serve/app/logics/trading/reconciliation.py
serve/app/logics/trading/__init__.py
serve/app/handlers/trading/execution.py
serve/app/handlers/trading/__init__.py
serve/runtimes/trading/execution.py
serve/runtimes/trading/reconciliation.py
serve/runtimes/trading/__init__.py
serve/app/orchestrator/trading_state_machine.py
serve/app/observability/logging.py
serve/app/observability/metrics.py
serve/alembic/versions/b1000051_v2_0051_execution_orders.py
```

### 6.2 Driver 与 fake transport 合同

- 外部 schema 只解析/规范化；未知字段进 `raw_extra`，已知字段类型错误拒绝，money/price/share 禁 float。
- Driver 只做 wire/auth/sign/timeout/response normalization；Logic 决定 Gate、retry、reservation 和状态转换。
- `ClobTradingDriver` 封装锁定 SDK；SDK object/secret/signature 不离开 Driver。order golden 必须独立验证
  EOA recovery、Deposit Wallet maker/funder、type 3、ERC-7739 trailer、Standard/NegRisk domain 和最终 wire hash。
- 私有 submit 的 transport policy 强制单次发送；公共 Driver 的 425/429/5xx 自动 retry 不得复用于 submit。
- User WS 初始 auth 订阅账户全量 order/trade，10s 文本 PING/PONG；raw private frame 只以脱敏 artifact hash/
  typed event 落库。WS receive/heartbeat 是 execution runtime 的独立 monotonic task，不进入普通 Job。
- 所有构造器必须可注入 fake HTTP/WS/clock；测试安装 egress tripwire，任何未注入 transport、真实 hostname
  connect 或 socket 调用立即失败。最终性能 JSON 记录 `fake_transport_calls>0`、`real_network_calls=0`。

### 6.3 `b1000051` 唯一数据库对象

新建：

```text
execution_authorization_envelopes
exchange_order_attempts
exchange_orders
order_state_events
exchange_trades
account_reconciliations
workflow_events
external_call_attempts
alert_events
```

并只对既有 `executions、positions、position_lots、ledger_transactions` 增加必要 account/envelope/order/trade
lineage；不得创建第二套 intent、position、cash ledger、reservation 或 order 表。

硬约束：

1. authorization envelope 唯一绑定 intent/account/release/execution spec/permission/fencing/preflight；稳定
   idempotency key 与 envelope hash 全局唯一。permission 必须是 release 引用的 active shadow permission，
   `authorized_capital=0`，authority 只能 `FAKE_CONFORMANCE`。
2. permission twin 的 `decision_algorithm_hash/economic_terms_hash` 必须相同；envelope 引用既有唯一
   `economic_action_intent_hash`，driver 不新建 intent、不重算 forecast/edge/size。
3. attempt 在 fake send 前已提交 exact body hash、expected order hash、SDK manifest hash、salt/timestamp、
   fencing token 与 state event。signed body/signature 原文不入 DB/artifact/log。
4. `(account,external_order_id)`、`(account,external_trade_id)`、attempt number、order event idempotency key 唯一；
   append-only event/trade/external-call/workflow/alert 禁 UPDATE/DELETE。
5. current order projection 用 CAS 从 event 重建；非法 transition、倒退、重复经济 effect 和跨账户引用由 DB/
   Logic 双重拒绝。`UNKNOWN` 必须保留 reservation/hard stop；对账 terminal 后才 `RECONCILED`。
6. partial fill 只按实际 quantity 生成 lot/posting；cancel race、late fill、duplicate ACK、out-of-order trade 均可收敛。
   每 asset ledger signed base units 为 0；position/cash/provider diff 任一非 0 触发 hard stop/alert。
7. reconciliation 保存触发原因、WS watermark、REST page cursor/hash、逐单查询、input/output manifest、差异和
   fencing token；只有完整 pages + diff=0 可完成。一次空页不证明 UNKNOWN 未提交。
8. heartbeat ID 更新、order/trade apply、reservation transfer 和 outbox/workflow event 必须同一 UoW；旧 fencing
   response 只记录 `STALE_FENCE_REJECTED`，不能覆盖 latest heartbeat/order/current funds。
9. kill switch 或任何 upstream/spec/provider drift 阻止增仓，但不能阻止已批准 REDUCE/CLOSE/CANCEL/reconcile。
10. `external_call_attempts` 只存 endpoint/method/request-response hash/status/latency/rate-limit/error/fence；认证
    header、body、signature、secret/passphrase 明文为 0。

## 7. Checkpoint D — 故障证据、测试、性能与唯一 Manifest

### 7.1 精确测试文件

```text
serve/tests/trading/test_v2_config.py
serve/tests/trading/test_v2_log_redaction.py
serve/tests/trading/test_v2_metric_cardinality.py
serve/tests/trading/test_v2_model_imports.py
serve/tests/trading/test_v2_runtime_lifecycle.py
serve/tests/trading/fixtures/migration_helpers.py
serve/tests/trading/unit/test_v2_vault_crypto.py
serve/tests/trading/unit/test_v2_vault.py
serve/tests/trading/unit/test_v2_account_funds.py
serve/tests/trading/unit/test_v2_execution_fencing.py
serve/tests/trading/unit/test_v2_clob_private_schema.py
serve/tests/trading/unit/test_v2_clob_trading_driver.py
serve/tests/trading/unit/test_v2_user_ws_driver.py
serve/tests/trading/unit/test_v2_private_execution_logic.py
serve/tests/trading/unit/test_v2_reconciliation_logic.py
serve/tests/trading/unit/test_v2_private_egress_guard.py
serve/tests/trading/unit/test_v2_trading_state_machine.py
serve/tests/trading/contract/test_v2_clob_private_contract.py
serve/tests/trading/contract/test_v2_user_ws_contract.py
serve/tests/trading/integration/test_v2_0050_execution_vault_accounts_migration.py
serve/tests/trading/integration/test_v2_0051_execution_orders_migration.py
serve/tests/trading/integration/test_v2_vault_accounts_funds.py
serve/tests/trading/integration/test_v2_execution_reservations_fencing.py
serve/tests/trading/integration/test_v2_private_order_reconciliation.py
serve/tests/trading/integration/test_v2_execution_ledger_reconcile.py
serve/tests/trading/replay/test_v2_execution_recovery.py
serve/tests/trading/performance/execution_readiness_smoke.py
```

### 7.2 必须证明的事实

1. SDK version/tag/commit fixture 和官方 type-3 golden 全等；maker/funder Deposit Wallet、signer/SDK
   signing actor EOA、signatureType 3、ERC-7739 wrapper、L1/L2 exact bytes 均精确。
2. `/v1/heartbeats` 首空 ID→轮换 ID 链、5s 调度、fence takeover、迟到响应、失败 cancel/reconcile 全覆盖；
   官方 `/heartbeats` 页面漂移被记录但从未触发双发/fallback。
3. AES-GCM roundtrip、nonce uniqueness、AAD/identity/account/purpose mismatch、ciphertext/tag tamper、未知 key、
   rotation/old version、concurrent activation、deny audit 全覆盖；扫描 synthetic canary 后 settings/Redis/outbox/
   logs/traces/errors/artifacts/manifest plaintext hit=0。
4. 两个并发 reservation 不能越过 funds/cap；crash rollback 无半条 reservation；UNKNOWN 保留；ACK 的 local→
   provider reserved 原子转移无漏计/双计；release/consume quantity 精确。
5. execution/heartbeat 双 leader、lease expiry/takeover 和每个 side effect fencing；旧 owner economic effect=0。
6. submit 在 write/response/body 各断点只产生一个 logical order；UNKNOWN 盲重发=0；只有完整 REST 证明未入 book
   才允许关联新 attempt。
7. success、200+errorMsg、400、401、425、429、5xx、timeout、duplicate ACK、乱序/partial/late fill、cancel race、
   provider cancel-only/post-only/disabled 全有 fixture 和结构化 reason。
8. User WS 断线后 open orders 全分页 + trades watermark + UNKNOWN 单查；重连 WS 本身不解除 RECONCILING；
   manifest 完整且 order/trade/reservation/position/cash/ledger diff=0 才恢复。
9. kill/`authorized_capital=0` 下 exposure-increasing provider submit=0；REDUCE/CLOSE/CANCEL/reconcile 仍可走 fake path。
10. P-stability 同一 frozen log 两次 hash 全等；随机差异有 model/sampling/seed 归因；所有未确认写入 fail closed。
11. migration 支持 empty/existing Base、`0051→0050→0041` downgrade/upgrade roundtrip、ORM modeled drift=0、
    非空旧 vault/未知下游对象在任何 DROP/version advance 前整次回滚。
12. 全测试期间 `real_network_calls=0`、真实 secret=0、canary/live permission=0、authorized capital>0 行=0、链写=0。

### 7.3 性能硬门

使用真 PostgreSQL、execution pool=`5+1`、reconciliation pool=`5+1`，真实 Repository/UoW/constraint 和
fake provider transport；不得直接批量 INSERT 冒充业务路径：

- DB-only final preflight + atomic reservation p99≤50ms；完整本地 preflight p95≤150ms、p99≤500ms；
- fake CLOB submit→ACK p95≤2s、p99≤5s；User WS receive→order projection p95≤100ms、p99≤300ms；
- 1,000 个 live-order fixture 的断线 REST reconcile p95≤10s、p99≤30s，最终 diff=0；
- ≥10 intents/s 持续 60s；duplicate economic effect、unbalanced ledger、negative funds、stale-fence apply 均为 0；
- P2/P3 队列饱和时 heartbeat 调度漂移≤500ms、age<10s，P0 cancel/reconcile 不被 AI/报表阻塞；
- DB pool wait p95≤20ms，transaction p99≤50ms；连接/CPU/RSS/WAL 与 10s 窗口必须记录；
- 输出 `/tmp/pm_v2_perf_smoke_5.json`，含 seed、实际 git commit、SDK tag/commit、fixture hashes、数据规模、
  p50/p95/p99、资源峰值、hard assertions、`fake_transport_calls` 和 `real_network_calls=0`；临时 DB 清理为 0。

## 8. 验收命令

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app runtimes tests alembic

.venv/bin/python - <<'PY'
from importlib.metadata import version
assert version("polymarket-client") == "0.5.0"
print("polymarket-client=0.5.0")
PY
.venv/bin/pip check

.venv/bin/pytest -q \
  tests/trading/unit/test_v2_execution_readiness_spec.py \
  tests/trading/unit/test_v2_vault_crypto.py \
  tests/trading/unit/test_v2_vault.py \
  tests/trading/unit/test_v2_account_funds.py \
  tests/trading/unit/test_v2_execution_fencing.py \
  tests/trading/unit/test_v2_clob_private_schema.py \
  tests/trading/unit/test_v2_clob_trading_driver.py \
  tests/trading/unit/test_v2_user_ws_driver.py \
  tests/trading/unit/test_v2_private_execution_logic.py \
  tests/trading/unit/test_v2_reconciliation_logic.py \
  tests/trading/unit/test_v2_private_egress_guard.py \
  tests/trading/contract/test_v2_clob_private_contract.py \
  tests/trading/contract/test_v2_user_ws_contract.py

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0050_execution_vault_accounts_migration.py \
  tests/trading/integration/test_v2_0051_execution_orders_migration.py \
  tests/trading/integration/test_v2_vault_accounts_funds.py \
  tests/trading/integration/test_v2_execution_reservations_fencing.py \
  tests/trading/integration/test_v2_private_order_reconciliation.py \
  tests/trading/integration/test_v2_execution_ledger_reconcile.py \
  tests/trading/replay/test_v2_p_stability.py \
  tests/trading/replay/test_v2_execution_recovery.py

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/python -m tests.trading.performance.execution_readiness_smoke

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
.venv/bin/alembic heads
.venv/bin/alembic upgrade b1000051 --sql > /tmp/wp05.sql
git diff --check
```

真 PostgreSQL 测试必须 0 skip；任何命令未运行/失败必须按原样记入 manifest，不能写预期结果。

## 9. 唯一 Completion Manifest 合同

完成后只创建：

```text
serve/docs/manifests/wp-05-execution-readiness-private-clob.md
```

禁止创建 `P_STABILITY_MANIFEST.md`、`P_EXECUTION_READINESS_MANIFEST.md` 或 checkpoint README。唯一 manifest
必须依次包含以下两个固定二级标题。

### 9.1 `P_STABILITY_MANIFEST`（必须先出现）

逐项对应 ARCHITECTURE §10.6 P-stability，记录：accepted WP-03/WP-04 与 P2/P3 hashes；spec/event log/snapshot/
seed/clock hashes；WS 回补、重复/乱序、背压、restart、模型 timeout/partial failure、rollback 的注入点；
universe/opportunity/episode/disposition/blind commit/economic intent/authorization envelope/ledger/metric 前后 hash；
未确认写入 fail-closed 证据；命令、日志/报告 artifact hash 和所有未通过项。

### 9.2 `P_EXECUTION_READINESS_MANIFEST`（必须后出现）

逐项对应 ARCHITECTURE §10.6 的 8 条 DoD，并额外记录：

- frozen P execution spec 与 capability/cost/hash 一致性；
- SDK `0.5.0`、tag、commit、golden SHA；type-3 identity/ERC-7739/L1/L2 evidence；
- 0050/0051 表、constraint/trigger/index、迁移 roundtrip 与 ORM drift；
- vault keyring/AAD/rotation/access audit/no-plaintext 扫描结果；
- funds/reservation/fencing/kill switch/permission twin/envelope/idempotency；
- ACK/PARTIAL/FILLED/CANCELLED/REJECTED/UNKNOWN/RECONCILED、无盲重发、User WS→REST reconcile；
- `/v1/heartbeats` ID 链和官方 `/heartbeats` drift artifact；
- fake transport/egress tripwire、`authorized_capital=0`、real network/real money/chain side effect=0；
- `/tmp/pm_v2_perf_smoke_5.json` SHA、全部命令真实结果、blocker/non-goal/rollback。

manifest 最后一段必须有 handoff：changed files、reviewer 最小复验命令、未解决 activation blockers、rollback
入口、下一 WP 仍未创建。最后一行格式固定：

```text
COMPLETION_MANIFEST_SHA256: <64 lowercase hex>
```

SHA-256 计算规则：UTF-8/LF 文件中删除且只删除上述整行（连同行尾 LF）后计算；填回 64 位小写十六进制，
再用同一算法复算相等。不得使用“删除所有 64 位 hex 行”这类会误删 artifact hash 的模糊规则。

## 10. Blocker、非目标与回滚

### Blocker

- WP-04 未 ACCEPTED、Alembic head/hash 不等于任务前置，或 P execution spec/provider capability/cost hash 漂移；
- 无法精确安装/证明 `polymarket-client==0.5.0` 与指定 tag commit，或官方 golden 不匹配；
- 0002 vault 已有数据且 secret kind/identity/AAD/version 无法无歧义迁移；
- AES-GCM/AAD/rotation/access audit 或 no-plaintext 无法证明，任一 secret canary 泄露；
- 原子 funds/reservation、单调 fencing、UNKNOWN 保留或重复 economic effect=0 无法由真 PostgreSQL 证明；
- User WS 断线后的 REST 全量/增量边界无法冻结，订单/成交/仓位/现金/账本无法收敛为 0 差异；
- 实现要求真实 credential、真实私有 endpoint、真实资金、canary/live permission 或链上写才能通过；
- 需要改变 action ontology、HOLD_TO_RESOLUTION、4%/6%/30%、objective 或既有经济成本合同。

官方 heartbeat 页面漂移本身必须作为 **真实激活 blocker** 保留，但不阻止在冻结 `/v1/heartbeats` fake contract
下完成 `IMPLEMENTED_FAKE_CONFORMANCE`；它绝不能被写成 provider conformance 已通过。

### 非目标

- 不授予 Canary/Live，不创建 `authorized_capital>0`，不导入真实账户/secret，不访问公网；
- 不做 WP-06 Polygon、relayer、contract registry、split/merge/redeem/settlement finality；
- 不升级/替换 SDK，不自动适配 `/heartbeats`，不双发 heartbeat 路径；
- 不扩展做市、BTC/crypto 短窗、套利、short/sell-to-open、返利驱动、跟单或 ACTIVE_REVALUE；
- 不做 P4 ensemble/challenger/bias，不修改预测/edge/size 算法；
- 不建设 Admin Controller/API/frontend，不改 V1，不新增第二套 intent/order/position/ledger/vault 表；
- 不用 Redis/log/WS/Data API 取代 PostgreSQL 订单、资金、permission 或账本事实。

### 回滚

- 运行态先保持 `authorized_capital=0`/kill，停止 fake submit task，保留 cancel/reconcile evidence；UNKNOWN 不删除、
  不释放，直到可证明定案。
- 数据库按 `b1000051 → b1000050 → b1000041` 降级；每个 DROP 前检查未知下游对象/非测试 encrypted vault/
  未定订单，存在即整次回滚。不得手工删表或改 Alembic version。
- 代码 revert WP-05；WP-01～04 的 forecast/decision/shadow ledger/evaluation facts 完整保留。
- secret 轮换回滚只创建新 encrypted version/显式 keyring 版本，不把旧 ciphertext 原地改回 active；所有 access
  evidence 保留。订单/账本纠错只追加 state event/reversal，不覆盖历史。

## 11. 交付与交接

实现者完成时：

1. 只生成第 9 节唯一 completion manifest，并同步 `serve/docs/manifests/README.md` 的 WP-05 行与
   `serve/docs/tasks/README.md` 当前任务状态为 `DONE（待审）`；不得创建 WP-06 task。
2. 最终回复依次列出：changed files；commands/results；P-stability evidence；P-execution-readiness evidence；
   SDK/heartbeat drift/no-network evidence；unresolved blockers；rollback；manifest path/hash。
3. 实现者不得自行写 `ACCEPTED`。用户回复“完成”后，审查者读取任务、唯一 manifest、Git 状态和真实 diff，
   复跑定向 migration/vault/UNKNOWN/reconcile/no-egress/performance 证据；范围内 P0/P1 直接修复并复验。
4. 只有审查接受后才决定是否创建 WP-06；官方 heartbeat 漂移和真实 provider conformance 未关闭前，
   capital permission 必须继续为 0。

除第 4–7 节精确文件、唯一 manifest 和两个索引 README 外，任何文件均不在 WP-05 实现范围内。
