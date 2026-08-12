# WP-06 — Polygon/Relayer、CTF 结算与兑换闭环

> 状态：**ACCEPTED（审查通过）**
> 前置：`WP-05` 已 ACCEPTED；Alembic head=`b1000052`（唯一）
> 执行模型：DeepSeek V4 Flash
> 唯一完成交付：`serve/docs/manifests/wp-06-polygon-relayer-settlement.md`
> 初交 commits：`de79edc` / `d03082b` / `c3bc35d`
> 审查修复 commit：`53b4744bbe47a74063db2c99b11f344a93d3c541`
> 最后更新：2026-08-12 EDT

## 审查接受记录

- 初交复验发现并阻塞：伪造 registry/RPC bytes、错误 ABI/collateral、runtime 未接线、DB
  preflight/finality/fence/effect 不完整，以及 UNKNOWN recovery、Vault composition、wrong-chain 与
  post-final conflict audit 缺口。
- `53b4744` 已关闭全部范围内 P0/P1；最终 clean 证据为 unit/contract+Vault `108 passed`，真 PostgreSQL
  integration/replay/runtime `43 passed`，`tests/trading 1717 passed`，全仓 `1928 passed`，0 skip/fail。
- clean perf `/tmp/pm_v2_perf_smoke_6.json` SHA-256
  `bb9185af0317a5f93d57408c0f4b20a86d542c957a587fddb58aa259b8427119`，`hard_assertions=PASS`；
  660 ops / 60.005s = 10.999 ops/s，1,000 UNKNOWN 两轮全等、blind resend=0，real call=0。
- 完整证据、精确 changed files、blocker 与回滚见 completion manifest；审查结论：**ACCEPTED**。

## 0. 快车道执行规则

1. 本里程碑连续完成四个 Checkpoint：A provider/registry/finality freeze → B `0052` 数据层 →
   C Polygon/Relayer/settlement runtime → D recovery/performance/security/manifest。只生成一份 completion manifest。
2. 继续使用 `FIXTURE_ONLY`、`authorized_capital=0`、注入式 fake RPC/Relayer transport。验收期间真实
   Polygon/Relayer 请求、真实签名、真实资金和链上副作用必须为 0；WP-06 完成不等于允许自动兑换。
3. 外部网络、Vault 解密、签名、RPC 和 Relayer 调用不得位于数据库事务内：

   ```text
   TX1: 冻结 registry/account/permission/operation/calls/body hashes → COMMIT
   RPC/RELAYER: 只发送与已提交 hash 全等的 bytes；无 DB transaction
   TX2: 追加 response/state/receipt/balance/artifact/outbox → COMMIT
   RECOVERY: relayer status + nonce + receipt + finalized block + balance 对账
   ```

4. 范围内 P0/P1 直接修复并复验，不拆 `-rN`。不把 REST mock、直接 INSERT 或 Relayer
   `CONFIRMED` 冒充链上 finality。
5. provider source/wire、合约地址或代码发生漂移时，旧 capability 立即关闭；创建新 registry/spec
   版本并重新验收，禁止运行时静默适配。
6. A/B/C 可按文件所有权并行；完整 trading/full/performance/security 仅在最终代码上各跑一次。

## 1. 目标与用户价值

把 WP-05 已冻结的账户、权限、执行证据和账本接到可回放的 Polygon/CTF 结算边界，使系统能够在
不动用真实资金的情况下证明：

1. 合约地址、proxy/beacon implementation 与 runtime bytecode 都来自版本化 registry 和 finalized block；
2. Gamma/CLOB、CTF payout、Data API redeemable、CLOB winner/50-50 与 label audit 一致后，结算才可采纳；
3. Standard 与 NegRisk 选择正确 adapter，split/merge/redeem calldata 可由 golden fixture 独立复核；
4. Relayer timeout/5xx/重启只进入 UNKNOWN 并查询 transaction/nonce/receipt/balance，绝不盲重发；
5. Relayer `CONFIRMED` 之后仍需 canonical receipt 与 Polygon finalized block，经济账才产生一次效果；
6. settlement、position、cash/fee/gas ledger、label/evaluation 可从 append-only evidence 重建，冲突时
   `SETTLEMENT_CONFLICT`，不评分、不学习、不自动兑换。

## 2. 已确认决策

### 2.1 产品与权限

1. 新闻类、`HOLD_TO_RESOLUTION`、现有 action ontology 与风险上限不变；不引入做市、短窗或新 alpha。
2. WP-06 是 `IMPLEMENTED_FAKE_CONFORMANCE`。真实 RPC/Relayer/secret/资金仍是激活 blocker；所有新增
   operation 的 authority 固定为 fake，真实 chain-write capability=false。
3. 首版只支持 Polygon PoS `chainId=137`、CLOB V2、pUSD、Standard/NegRisk 的 split/merge/redeem；
   不支持 `NO → 其他 YES` conversion、其他链、CLOB V1。
4. kill/geoblock/reconciliation conflict 阻止新增 split/merge/redeem；已 UNKNOWN 的操作仍必须只读恢复。

### 2.2 Relayer Deposit Wallet wire

当前冻结 source 为 Polymarket Wallets/Auth、Manage Positions、Contracts 与官方
`Polymarket/py-sdk` source commit `39b90750c0ff4034be32f2db623d4ed4fa74a729`。该 commit 是
provider-wire provenance，不是新的隐式 runtime dependency；source archive/hash 必须进入 fixture。只为
Deposit Wallet/WALLET 冻结 raw contract；Safe/Proxy capability=false。

- Base URL：`https://relayer-v2.polymarket.com`。
- nonce：`GET /v1/account/transactions/params?address=<EOA>&type=WALLET`；响应
  `{address,nonce}`，nonce 必须为非空十进制字符串。
- EIP-712 domain：`name=DepositWallet, version=1, chainId=137,
  verifyingContract=<deposit_wallet>`；types 为
  `Call(target address,value uint256,data bytes)` 与
  `Batch(wallet address,nonce uint256,deadline uint256,calls Call[])`；deadline=`trusted_now+600s`。
- submit：`POST /submit`，exact body：

  ```json
  {
    "type": "WALLET",
    "from": "EOA_SIGNER",
    "to": "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07",
    "nonce": "NONCE",
    "signature": "SIGNATURE",
    "metadata": "METADATA",
    "depositWalletParams": {
      "depositWallet": "DEPOSIT_WALLET",
      "deadline": "DEADLINE",
      "calls": [{"target": "TARGET", "value": "0", "data": "CALLDATA"}]
    }
  }
  ```

- status：`GET /v1/account/transactions/<transaction_id>`；响应字段
  `transaction_id,transaction_hash,state,error_msg`。
- states：`NEW|EXECUTED|MINED|CONFIRMED|INVALID|FAILED`；`CONFIRMED` 只是 Relayer 成功终态，
  `INVALID|FAILED` 是失败终态。
- Builder HMAC headers：`POLY_BUILDER_API_KEY/TIMESTAMP/PASSPHRASE/SIGNATURE`；签名材料为
  `timestamp + UPPERCASE_METHOD + path + exact serialized body`，secret base64-decode 后 HMAC-SHA256，
  URL-safe Base64 且保留 padding。
- nonce endpoint 同时按官方 golden 验证 `RELAYER_API_KEY/RELAYER_API_KEY_ADDRESS` 要求。两类 header
  均只存在于瞬时 request，不得写入普通 DB/log/trace/artifact。
- 官方旧页面的 `GET /transaction?id=...` 记录为 drift，生产合同只用
  `/v1/account/transactions/{id}`，禁止双发或 fallback。

### 2.3 Polygon finality

1. Relayer `CONFIRMED` 不等于链上 finality。另取 `eth_getTransactionReceipt`，要求 transaction hash
   全等、`status=0x1`、`blockNumber/blockHash` 非空；同高度 `eth_getBlockByNumber` 的 hash 必须一致。
2. 读取 `eth_getBlockByNumber("finalized", false)`；只有
   `finalized.number > receipt.blockNumber` 才能将 operation 置 `FINALIZED` 并记账。
3. 未 finalized 只记 `MINED_PROVISIONAL`，经济 effect=0。receipt 消失、block hash 改变、canonical
   不符或 removed log 进入 `REORGED/UNKNOWN`，重新查询 relayer/nonce/receipt/balance，禁止重发。
4. RPC 不支持 `finalized`、超时、chainId 错误、多个证据源不一致时 hard stop settlement。
5. 已被 finalized 的证据随后出现矛盾时视为 provider/registry 故障：追加
   `SETTLEMENT_CONFLICT` 与 CRITICAL alert，停止自动结算；不原地改历史 ledger。

### 2.4 CTF calls

- Standard target：`CtfCollateralAdapter=0xAdA100Db00Ca00073811820692005400218FcE1f`。
- NegRisk target：`NegRiskCtfCollateralAdapter=0xadA2005600Dec949baf300f4C6120000bDB6eAab`。
- pUSD：`0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`；CTF：
  `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`。
- `parentCollectionId=bytes32(0)`、`partition/indexSets=[1,2]`；amount 是 pUSD 6-decimal base units。
- ABI：`splitPosition(address,bytes32,bytes32,uint256[],uint256)`、
  `mergePositions(address,bytes32,bytes32,uint256[],uint256)`、
  `redeemPositions(address,bytes32,bytes32,uint256[])`。
- split 前需 pUSD allowance；merge/redeem 前需 CTF operator approval。WP-06 只构造并验证 fake batch，
  不执行真实 approve。redeem 无 amount，消费该 wallet/condition 的全部 outcome balance。
- adapter 只从 DB 冻结的 `pm_markets.neg_risk` 选择；NULL/漂移/调用方覆盖全部拒绝。

### 2.5 Registry 与 code hash

1. 官方 Contracts 地址是地址 source of truth；runtime code hash 是 finalized-chain snapshot，不伪称官方常量。
2. 每个条目必须保存 `registry_version/source_url/retrieved_at/chain_id/snapshot_block_number/hash/address/
   proxy_kind/runtime_keccak/resolved_implementation_or_beacon/resolved_code_keccak`。
3. EIP-1967 proxy 必须解析 implementation slot；Beacon proxy 还要读取 beacon 的 `implementation()` 并核对
   beacon 与 implementation code。只核 proxy 自身 hash 不算通过。
4. fixture 生成时至少由三个已配置 Polygon RPC 在同一 finalized block 对地址/code/slot/implementation
   达成一致，保存 raw response artifacts。无法复现即 A BLOCKED。
5. 启动与每次 chain operation 前复核 exact registry version/hash；漂移时 capability off、hard stop、人工批准
   新版本，禁止信任 SDK 内置旧 implementation 常量。
6. v1 candidate snapshot 固定 block `91842167`、block hash
   `0x16d35ed4cc72f20c141efcc38d8c0362d4ba95482f3aa96071e85fd06857a47f`；Checkpoint A 必须从三个
   RPC 重新取得完整 runtime/proxy/beacon/implementation bytes 并计算全长 Keccak。不得改用 `latest`、
   不得把本任务中的缩写或人工抄录值写进 registry fixture。

## 3. 依赖与必读

- `/code/pollymarket/docs/v2/ARCHITECTURE.md` §6–§10；
- `serve/docs/v2-implementation-contract.md` §3–§12；
- `serve/docs/polymarket-integration-design.md` §10–§11；
- `serve/docs/performance-cache-database-design.md` 的 execution/reconciliation/DB SLO；
- WP-05 accepted manifest SHA
  `04e365b4b1c18dc529dd2f6aa73c0cccf29c6a6cab5487787776f74a9bdc2fc9`；
- 官方 Contracts：`https://docs.polymarket.com/resources/contracts`；
- 官方 Wallets/Auth：`https://docs.polymarket.com/trading/wallets-auth`；
- 官方 Manage Positions：`https://docs.polymarket.com/trading/positions/manage`；
- Polygon Finality：`https://docs.polygon.technology/pos/concepts/finality/finality`；
- Ethereum receipt spec：`https://ethereum.github.io/execution-apis/api/methods/eth_getTransactionReceipt/`。

所有 source fixture 必须含 `url,retrieved_at,content_hash`。页面/API/SDK/source 不一致时在 fixture 明确
记录裁决与 fail-closed 行为，不从聊天记录猜 wire。

## 4. Checkpoint A — 协议、registry 与 recovery fixture

精确文件：

```text
serve/tests/trading/fixtures/p6_settlement/
  chain_settlement_spec_v1.json
  contract_registry_polygon_v1.json
  provider_source_v1.json
  polygon_rpc_golden_v1.json
  relayer_deposit_wallet_golden_v1.json
  settlement_sources_v1.json
  chain_recovery_v1.json
  p6_helpers.py
serve/tests/trading/contract/test_v2_polygon_rpc_contract.py
serve/tests/trading/contract/test_v2_relayer_contract.py
serve/tests/trading/unit/test_v2_chain_settlement_spec.py
serve/tests/trading/unit/test_v2_chain_egress_guard.py
```

fixture 必须冻结：source/wire/ABI/address、snapshot finalized block、完整 code/implementation/beacon hashes、
registry version、Relayer typed data/body/HMAC/status、RPC request/response、Standard/NegRisk calldata、receipt/
reorg/timeout/restart matrix、fake authority、P5 capability/economic hash。JSON 禁止截断 hash 或 `TBD`。

## 5. Checkpoint B — `b1000052_v2_0052_chain_settlement`

精确文件：

```text
serve/alembic/versions/b1000052_v2_0052_chain_settlement.py
serve/app/models/trading/{settlement,__init__}.py
serve/app/models/trading/execution.py
serve/app/models/__init__.py
serve/app/schemas/trading/{settlement,execution,__init__}.py
serve/app/repositories/trading/{settlement,execution,__init__}.py
serve/tests/trading/integration/test_v2_0052_chain_settlement_migration.py
serve/tests/trading/integration/test_v2_contract_registry.py
```

0052 只新建：

```text
contract_registry
chain_operations
chain_operation_state_history
settlement_observations
```

允许为 exact builder/vault/ledger lineage 向既有 `pm_accounts/executions/ledger_transactions` 增加最小
可空 FK/版本列；禁止平行账户、Vault、账本、label 或 current-projection 表。

### 5.1 数据库硬约束

- Registry 发布版本 append-only；同 chain/kind 只允许一个 active；地址/code/implementation/snapshot/source
  全冻结，发布前 exact completeness trigger；未知 kind/proxy type 拒绝。
- Chain operation 有稳定 `operation_key/idempotency_key/economic_hash`；account/wallet/condition/market/
  registry version/permission/release/fence/call-set/body hash exact binding。相同 key 异参硬冲突。
- `chain_operation_state_history` append-only，aggregate sequence 唯一；current 只由受控 CAS/trigger 推进。
- 状态机覆盖 `PREPARED→SUBMITTING→UNKNOWN|RELAYER_NEW|EXECUTED|MINED|RELAYER_CONFIRMED→
  MINED_PROVISIONAL→FINALIZED`，以及 `INVALID|FAILED|REORGED|SETTLEMENT_CONFLICT|REVERSED`；非法倒退、
  terminal mutation 与跳过 finality 拒绝。
- Partial unique：同 `account/wallet+condition_id` 同时最多一个 active `REDEEM`；并发 claim 使用非分区
  `idempotency_claims`，不能靠先查后写。
- Settlement observation append-only，绑定 source kind、condition/token set、payout vector、winner/50-50、
  redeemable、label audit version、as_of/received_at、raw artifact/hash。complete set 由 deferred trigger 验证。
- `FINALIZED` 必须存在 relayer CONFIRMED、canonical receipt、finalized block、pre/post balance、registry hash、
  zero-conflict evidence；ledger transaction/position effect 一一对应且只能产生一次。
- 所有金额为 NUMERIC base units、所有时间 TIMESTAMPTZ UTC、hash/ID 使用 C collation；事实表 UPDATE/DELETE
  拒绝，修正只写新 state/reversal/supersede；FK 默认 RESTRICT。
- downgrade 在 DROP 前检查未知 relation/index/trigger/FK/dependency、非 fixture registry、UNKNOWN/active
  operation；任一存在整次拒绝。空库/Base、0051↔0052 roundtrip、offline SQL 与 ORM drift 都必须证明。

## 6. Checkpoint C — Polygon/Relayer/settlement runtime

精确生产文件：

```text
serve/app/config.py
serve/.env.example
serve/app/schemas/polymarket/{chain,data_api,clob_public,__init__}.py
serve/app/services/polymarket/{base,polygon_driver,relayer_driver,service,__init__}.py
serve/app/domain/trading/{payout,rounding}.py
serve/app/repositories/trading/{settlement,execution,ledger,audit}.py
serve/app/logics/trading/{settlement,reconciliation,execution,projection}.py
serve/app/handlers/trading/settlement.py
serve/app/handlers/trading/__init__.py
serve/runtimes/trading/{evaluation,reconciliation,__init__}.py
serve/app/orchestrator/trading_state_machine.py
serve/app/observability/metrics.py
```

不新建 `runtimes/trading/settlement.py`；使用已约定 evaluation/reconciliation runtime。只有确实缺少且能
精确锁版本/来源/哈希时才改 `requirements.txt`，不得引入 unpinned/transitive SDK。

### 6.1 Driver/Service

- `PolygonDriver` 只实现 typed JSON-RPC：`eth_chainId,eth_getCode,eth_getStorageAt,eth_call,
  eth_getTransactionReceipt,eth_getBlockByNumber` 与余额/allowance必要 `eth_call`；response shape/hex/quantity
  严格校验，保存安全 receipt，不做 DB 写。
- `RelayerDriver` 只实现 §2.2 exact nonce/typed batch/submit/status；exact serialized bytes 与 HMAC bytes
  共用同一对象。timeout/connection reset/5xx 只返回 OUTCOME_UNKNOWN，不生成新 nonce/deadline/signature。
- 两个 Driver 默认 `require_injected_transport=true`；缺 transport 在 socket/client 构造前抛
  `wire_egress_tripwire`。endpoint URL 中 auth/query secret 禁止进入日志/receipt。
- signer、Builder credential 只按 WP-05 Vault ref+version+runtime identity 解密；成功、拒绝、异常均有独立
  secret access audit，明文/signature/raw signed body 不持久化。

### 6.2 Settlement Logic

自动标记 `final_admissible/redeemable` 前 exact 核验：

1. Gamma/CLOB `closed=true && acceptingOrders=false`；
2. CTF payout numerators/denominator；
3. Data API position `redeemable=true`；
4. CLOB winner/`is_50_50_outcome` 与 payout 一致；
5. contract rules/clarification snapshot 已完成 label audit。

token set、condition、outcome index、payout vector 与 source cutoff 必须完整一致；任一缺失/冲突进入
`SETTLEMENT_CONFLICT`，G8/score/learning/redeem/ledger effect=0。

### 6.3 Operation 与恢复

1. TX1 前两次 preflight：account/release/permission/fence/reconciliation/kill、registry chain/code、market
   negRisk、payout/observation、balance/allowance、operation uniqueness；第二次结果必须与 envelope hash 全等。
2. 发送前持久化 exact nonce/deadline/calls/calldata/body hash/expected operation hash，提交后才允许 fake
   transport。外部调用期间无 DB transaction。
3. timeout/断连/5xx → UNKNOWN；恢复查询 relayer transaction、nonce、receipt、canonical/finalized block 与
   pre/post balances。没有权威失败/不存在证据时真实 resend=0。
4. duplicate/out-of-order state/receipt effect=0；`FAILED/INVALID` 无经济账；`MINED_PROVISIONAL` 无经济账；
   `FINALIZED` 在同一 UoW 写 operation state、execution/position、balanced ledger、outbox/audit。
5. restart 在 TX1/网络/TX2 任一点都从 DB facts 恢复；Relayer `CONFIRMED` 无 tx hash、receipt gap、nonce
   mismatch 或 balance mismatch 保持 UNKNOWN/CONFLICT，不能猜成功。
6. `latest_chain_summary` 只更新既有 0041 projection source，不建新表；Gate/资金/账本永远查事实表。

## 7. Checkpoint D — 验收、回放、性能与唯一 manifest

精确测试/交付文件：

```text
serve/tests/trading/unit/test_v2_chain_schema.py
serve/tests/trading/unit/test_v2_polygon_driver.py
serve/tests/trading/unit/test_v2_relayer_driver.py
serve/tests/trading/unit/test_v2_chain_operation_state_machine.py
serve/tests/trading/unit/test_v2_settlement_logic.py
serve/tests/trading/contract/test_v2_polygon_rpc_contract.py
serve/tests/trading/contract/test_v2_relayer_contract.py
serve/tests/trading/integration/test_v2_0052_chain_settlement_migration.py
serve/tests/trading/integration/test_v2_contract_registry.py
serve/tests/trading/integration/test_v2_chain_operation_finality.py
serve/tests/trading/integration/test_v2_settlement_conflict_redeem.py
serve/tests/trading/integration/test_v2_chain_ledger_reconcile.py
serve/tests/trading/replay/test_v2_chain_operation_recovery.py
serve/tests/trading/performance/chain_settlement_smoke.py
serve/tests/trading/integration/test_v2_chain_secret_boundary.py
serve/docs/manifests/wp-06-polygon-relayer-settlement.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

允许同步 head/model-count/import/config/projection 测试；不得修改 V1、Admin API/frontend。

## 8. 必测事实

1. wrong chain、空 code、proxy-only hash、implementation/beacon/code drift、非 active registry 均在签名前拒绝；
2. Standard/NegRisk exact adapter/calldata、pUSD/CTF/partition/amount 与 golden 全等，caller 不可覆盖；
3. Relayer nonce/EIP-712/deadline/body/HMAC/path/header/status 与 fixture 全等，旧 status route 不 fallback；
4. 五类 settlement source exact set 一致才 admissible；缺一项或 payout/winner/token/cutoff 冲突 effect=0；
5. N 个并发同 wallet+condition redeem 只有一个 active logical operation；不同参数同 key 拒绝；
6. timeout/5xx/bad body/restart 均 UNKNOWN，logical operation=1、real/fake economic submit effect不重复；
7. Relayer CONFIRMED、receipt mined 但未 finalized 时 ledger/position=0；failed receipt=0；
8. canonical receipt + `finalized.number>receipt.blockNumber` + balance delta 后 ledger/position/settlement
   effect 恰一次，重复/乱序=0，账本每 asset 平衡；
9. pre-final reorg/removed/missing receipt 回到 UNKNOWN/REORGED，无半条账；finalized evidence 冲突 hard stop；
10. startup/restart 从 transaction/nonce/receipt/block/balance 恢复，不调用 AI、不重算 decision、不盲重发；
11. registry/operation/history/observation/external-call/outbox/artifact/ledger 全链可按 ID 展开并回放；
12. secret/signature/raw signed body/auth header/RPC credential marker 在 DB/Redis/log/trace/artifact/API/git 为 0；
13. outbound socket tripwire、fake calls>0、real network/chain/money calls=0、authorized capital=0；
14. 0052 空库/Base、roundtrip、downgrade preflight、ORM drift、offline SQL 全过，真 PG 0 skip；
15. WP-03～05 ledger/evaluation/replay 与 P-stability 回归不变。

## 9. 性能与容量

真 PostgreSQL、真实 Logic/Repository/UoW/constraints、execution/reconciliation 有界 pool；RPC/Relayer 仅
deterministic fake transport：

- 10 logical chain operations/s 持续 60s；lost/duplicate/over-effect/unbalanced/conflict leakage=0；
- 1,000 UNKNOWN operations 两次 recovery 最终状态/hash 全等、blind resend=0；
- DB pool wait p95≤20ms，连接峰值用 checkout/checkin high-water 实测且不超过配置总量；
- 报告 registry preflight、TX1、fake submit、TX2、receipt→finalized apply、1k recovery 的 p50/p95/p99；
- 不为 fake provider latency声明真实 Polymarket/Polygon SLO；只对 DB/正确性 hard assert；
- 报告 WAL/RSS/CPU/event-loop lag/连接峰值/fake-real call counters；临时库/文件残留=0；
- 输出 `/tmp/pm_v2_perf_smoke_6.json`，包含 clean code commit、seed、fixture/source/registry hashes、规模、
  plan 摘要与 `hard_assertions=PASS`。

## 10. 验收命令

```bash
cd /code/pollymarket/v2/serve

python3 -m compileall -q app runtimes tests alembic
.venv/bin/pip check

.venv/bin/pytest -q \
  tests/trading/unit/test_v2_chain_settlement_spec.py \
  tests/trading/unit/test_v2_chain_schema.py \
  tests/trading/unit/test_v2_polygon_driver.py \
  tests/trading/unit/test_v2_relayer_driver.py \
  tests/trading/unit/test_v2_chain_operation_state_machine.py \
  tests/trading/unit/test_v2_settlement_logic.py \
  tests/trading/unit/test_v2_chain_egress_guard.py \
  tests/trading/contract/test_v2_polygon_rpc_contract.py \
  tests/trading/contract/test_v2_relayer_contract.py

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0052_chain_settlement_migration.py \
  tests/trading/integration/test_v2_contract_registry.py \
  tests/trading/integration/test_v2_chain_operation_finality.py \
  tests/trading/integration/test_v2_settlement_conflict_redeem.py \
  tests/trading/integration/test_v2_chain_ledger_reconcile.py \
  tests/trading/replay/test_v2_chain_operation_recovery.py \
  tests/trading/integration/test_v2_chain_secret_boundary.py

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/python -m tests.trading.performance.chain_settlement_smoke

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
.venv/bin/alembic heads
.venv/bin/alembic upgrade b1000052 --sql > /tmp/wp06.sql
git diff --check
```

必须 0 skip/0 fail，唯一 head=`b1000052`；offline SQL/日志/artifact secret-value marker=0；真实 outbound/
chain/money counters=0。manifest 只写最终 clean commit 的真实数字。

## 11. Completion manifest 合同

只创建：

```text
serve/docs/manifests/wp-06-polygon-relayer-settlement.md
```

至少记录：状态与实现 commit、精确 changed files、source URLs/hashes/retrieved_at、registry finalized snapshot
block/address/proxy/beacon/implementation/code hashes、Relayer wire/golden hashes、0052 objects/roundtrip/drift、
settlement source exact-set/conflict、operation UNKNOWN/recovery/finality/ledger、secret/no-egress scan、测试与性能
JSON/SHA、blocker/non-goal/rollback。完成后按本 task 冻结的口径计算唯一 self SHA：删除且只删除最后一行

```text
COMPLETION_MANIFEST_SHA256: <64 lowercase hex>
```

（含行尾 LF）后 SHA-256；将同一值同步两个 README，再独立复验。

## 12. Blocker、非目标与回滚

### Blocker

- WP-05 accepted manifest/head/hash不一致；
- 三 RPC 无法在同一 finalized block 对 registry/code/proxy/beacon/implementation 达成一致；
- Relayer source/wire/golden 无法复现，或 Safe/Proxy 被隐式启用；
- RPC 不支持 `finalized`、finality/receipt/balance 无法 fail closed；
- active redeem、UNKNOWN、idempotency、ledger/finality 任一无法由 DB/回放证明；
- 通过测试必须使用真实 secret、真实签名、真实 RPC/Relayer 写或真实资金。

遇到 blocker 如实写唯一 manifest 并停止，不降低 gate、不把 Relayer CONFIRMED 当 finality。

### 非目标

- 不启用 canary/live/nonzero capital，不导入真实 signer/Builder/RPC secret，不发真实交易；
- 不部署 wallet、不 approve、不 split/merge/redeem，不操作真实 Polygon/Relayer；
- 不支持 Other conversion、Safe/Proxy raw flow、其他链、CLOB V1；
- 不修改 forecast/decision/risk/4%/6%/30% 产品逻辑，不做 P4 optimizer；
- 不建设 Admin/UI，不做 WP-07/08，不改 V1；
- 不建平行 vault/account/ledger/label/projection/runtime。

### 回滚

- 先 kill 并停止 fake operation worker；UNKNOWN/active operation 必须先完成只读 reconcile，不能伪造失败；
- `alembic downgrade b1000051` 前必须无未定/非 fixture chain facts及未知依赖，否则整次拒绝；
- append-only registry/operation/history/observation/ledger 先导出 artifact manifest；secret 只导出密文元数据；
- 账本纠错只追加 exact reversal，label 只 supersede；不 UPDATE/DELETE 历史事实；
- 代码 revert WP-06 commits，WP-01～05 facts/manifest 保留。

## 13. 交接

1. 全部 Checkpoint 完成后，创建唯一 manifest 并把两个 README 的 WP-06 标为 `DONE（待审）`。
2. 不自行写 ACCEPTED，不创建 WP-07A，不启用真实网络/chain/capital。
3. 用户回复“完成”后，审查者读取 task/manifest/Git，复跑 registry/finality/UNKNOWN/ledger/no-egress/perf；
   范围内 P0/P1 直接修复，无阻塞后接受并发布 WP-07A。
