# WP-06 — Polygon/Relayer、CTF 结算与兑换闭环 — Completion Manifest

> 状态：**ACCEPTED（审查通过）**
> 初交 commits：`de79edc`（实现）、`d03082b`（初版 manifest/索引）、`c3bc35d`（配置键）
> 审查修复 commit：`53b4744bbe47a74063db2c99b11f344a93d3c541`
> Alembic head：`b1000052`（唯一）
> 接受日期：2026-08-12 EDT

## 1. 审查结论与整改记录

初交报告中的测试与性能数字不能证明任务合同：审查复跑发现其 registry/RPC bytes 为伪造样本、
ABI 编码及 collateral/target 选择错误，且真实 evaluation/reconciliation runtime 没有接线；DB 的
registry 发布、operation preflight/fence、finality、单次经济 effect 与 lineage 约束也不完整。
UNKNOWN recovery、Vault credential composition、wrong-chain fail-closed、并发重复提交、取消窗口以及
finalized 后矛盾的 CRITICAL audit/outbox 均缺少端到端证据。这些均按 P0/P1 处理；旧报告的
`103/1669/1880 passed` 与 `211.3 ops/s` 不再作为接受证据。

`53b4744` 已关闭上述问题：

- 使用三个 RPC origin 在 finalized block `91842167` 捕获的完整 runtime/proxy/implementation/beacon
  bytes 重建 registry golden；满长 Keccak 与 source artifact 一致，wrong-chain/code drift 在签名前拒绝。
- 改为 canonical `eth_abi` 编码；split/merge/redeem 的 adapter、pUSD/CTF collateral、partition 与
  calldata 都与冻结 golden 全等。
- `EvaluationRuntime.submit_redeem` 落实 TX1 commit → 无事务 provider 调用 → TX2 commit；
  `recover_chain_operation` 与 reconciliation startup 使用已冻结 provider/registry 事实只读恢复，
  timeout/5xx/cancellation 均不盲重发。
- 0052 补齐 registry 原子发布、authoritative preflight、account/lease fence、operation natural-key
  advisory lock、状态 CAS、finality deferred guard、settlement ledger lineage、单次 economic effect 与
  observation exact-set 约束；并发同 key 只有一个 provider POST。
- signer/Builder credential 经 WP-05 Vault ref/version/runtime identity 解密并独立审计；明文、签名、
  signed body 与 auth header 不进入 DB/log/artifact。geoblock denied/stale/malformed 在 nonce/sign/send 前终止。
- canonical receipt 且 `finalized.number > receipt.blockNumber` 才写 balanced ledger、position、outbox/audit；
  finalized 后证据矛盾只追加 `SETTLEMENT_CONFLICT` 与 CRITICAL alert，不产生第二次 effect。
- P-stability 仍在每次执行验证当前 quote/balance freshness；稳定 identity hash 只排除 wall-clock 字段，
  保留自然身份、book/depth/request hashes，并由更新后的冻结 snapshot 重放验证。

审查者在 clean commit `53b4744` 上复跑全部合同、真 PostgreSQL 集成/回放、全仓与 60 秒性能门，
结果均满足 §8～§10，因此 WP-06 **ACCEPTED**。

## 2. 审查修复 changed files（精确，50）

以下列表与 `git show --format='' --name-only 53b4744bbe47a74063db2c99b11f344a93d3c541`
全等。

**迁移与生产代码（23）**

```text
serve/alembic/versions/b1000052_v2_0052_chain_settlement.py
serve/app/domain/trading/payout.py
serve/app/handlers/trading/settlement.py
serve/app/logics/trading/execution.py
serve/app/logics/trading/projection.py
serve/app/logics/trading/settlement.py
serve/app/models/trading/ledger.py
serve/app/models/trading/settlement.py
serve/app/orchestrator/trading_state_machine.py
serve/app/repositories/trading/settlement.py
serve/app/schemas/polymarket/__init__.py
serve/app/schemas/polymarket/chain.py
serve/app/schemas/trading/__init__.py
serve/app/schemas/trading/settlement.py
serve/app/services/polymarket/__init__.py
serve/app/services/polymarket/geoblock_driver.py
serve/app/services/polymarket/polygon_driver.py
serve/app/services/polymarket/relayer_driver.py
serve/app/services/polymarket/service.py
serve/app/services/vault/service.py
serve/requirements.txt
serve/runtimes/trading/evaluation.py
serve/runtimes/trading/reconciliation.py
```

**测试与冻结 fixture（27）**

```text
serve/tests/trading/contract/test_v2_polygon_rpc_contract.py
serve/tests/trading/contract/test_v2_relayer_contract.py
serve/tests/trading/fixtures/p5_execution/stability_snapshot_v1.json
serve/tests/trading/fixtures/p6_settlement/capture_polygon_registry.py
serve/tests/trading/fixtures/p6_settlement/chain_settlement_spec_v1.json
serve/tests/trading/fixtures/p6_settlement/contract_registry_polygon_v1.json
serve/tests/trading/fixtures/p6_settlement/p6_helpers.py
serve/tests/trading/fixtures/p6_settlement/polygon_rpc_golden_v1.json
serve/tests/trading/fixtures/p6_settlement/provider_source_v1.json
serve/tests/trading/fixtures/p6_settlement/relayer_deposit_wallet_golden_v1.json
serve/tests/trading/integration/test_v2_0052_chain_settlement_migration.py
serve/tests/trading/integration/test_v2_chain_ledger_reconcile.py
serve/tests/trading/integration/test_v2_chain_operation_finality.py
serve/tests/trading/integration/test_v2_chain_runtime_workflow.py
serve/tests/trading/integration/test_v2_chain_secret_boundary.py
serve/tests/trading/integration/test_v2_contract_registry.py
serve/tests/trading/integration/test_v2_settlement_conflict_redeem.py
serve/tests/trading/integration/wp06_runtime_fixture.py
serve/tests/trading/performance/chain_settlement_smoke.py
serve/tests/trading/replay/test_v2_chain_operation_recovery.py
serve/tests/trading/replay/test_v2_p_stability.py
serve/tests/trading/unit/test_v2_chain_egress_guard.py
serve/tests/trading/unit/test_v2_chain_settlement_spec.py
serve/tests/trading/unit/test_v2_polygon_driver.py
serve/tests/trading/unit/test_v2_relayer_driver.py
serve/tests/trading/unit/test_v2_relayer_vault_composition.py
serve/tests/trading/unit/test_v2_settlement_logic.py
```

初交的 57 文件与后续 manifest/配置变更可分别由 `git show --name-only de79edc`、
`git show --name-only d03082b`、`git show --name-only c3bc35d` 复现；本节不把初交文件重复冒充审查修改。

## 3. 数据层、runtime 与证据链

- 0052 建立 `contract_registry`、`chain_operations`、`chain_operation_state_history`、
  `settlement_observations`，并给 `executions` / `ledger_transactions` 增加 chain-operation lineage。
- registry append-only、每 chain+kind 单 active、完整 bundle 原子发布；重试必须 exact match，active
  rollover 不改变已提交 operation 的 frozen recovery context。
- operation 在 DB 内绑定 account/release/permission/fence/reconciliation/kill、registry、market、
  five-source settlement evidence、pre/post balance、nonce/deadline/calls/body/envelope hashes；绑定字段不可变。
- TX1 提交后才允许 injected fake Polygon/Relayer transport；provider/Vault/签名均不位于 DB transaction。
  TX2 只采纳与冻结请求全等的 response/state facts。
- receipt、同高 canonical block、`finalized` block 与 pre/post balance 组成 finality evidence；
  `MINED_PROVISIONAL` effect=0，FINALIZED 的 position/ledger/outbox/audit 在一个 UoW 内恰好一次。
- UNKNOWN recovery 查询 transaction/nonce/receipt/canonical/finalized/balance，使用原 registry/provider
  context；kill、reconciliation conflict 与 registry rollover 阻止新操作，但不阻止既有 UNKNOWN 的只读恢复。
- evaluation/reconciliation runtime、typed settlement handler 与 latest-chain projection 已接线；不存在旧
  manifest 所述“只由测试直接调用、runtime 未接线”的剩余项。

## 4. Source、registry 与 wire 冻结

### 4.1 Source provenance

`provider_source_v1.json`：file SHA-256
`5fe8ad19bb4f7e70af338a2139d420e97d418c4b43ab4bfc7fcce1833b4b3823`，内部 content hash
`8d08604e07241c59baea5500172f812e26d9e353030d958a72aebbbb812e3366`。

| role | URL / commit | retrieved_at | content SHA-256 |
|---|---|---|---|
| contracts | `https://docs.polymarket.com/resources/contracts` | 2026-08-11T00:00:00Z | `24e5f13f654555aa2ddae01f8d62b1fbf6632903d2dc5ec22414f3fba342748d` |
| wallets/auth | `https://docs.polymarket.com/trading/wallets-auth` | 2026-08-11T00:00:00Z | `545ea3da91e6e8e39be044eea2fb51b0fdb18f9158d97c8731cdeb38034c4062` |
| manage positions | `https://docs.polymarket.com/trading/positions/manage` | 2026-08-11T00:00:00Z | `8d547467508acef30d96def7f632cab457f9d6de9f563412bf4177c348f27165` |
| Polygon finality | `https://docs.polygon.technology/pos/concepts/finality/finality` | 2026-08-11T00:00:00Z | `62f46aa927a15b4bcbbbb29771e6026bc738d9c863f9d869656c7f9693ce0da2` |
| receipt spec | `https://ethereum.github.io/execution-apis/api/methods/eth_getTransactionReceipt/` | 2026-08-11T00:00:00Z | `d3b3f1030c01b72a65b97cae4ecc89bab6e9a123fda98ea65fc78e7acc79c55a` |
| py-sdk source | `https://github.com/Polymarket/py-sdk` @ `39b90750c0ff4034be32f2db623d4ed4fa74a729` | 2026-08-11T00:00:00Z | `a959fd795e9fc9e14d62d23e9435adb5d629a2076fc8b8d67595602fa062dcd5` |

### 4.2 Registry snapshot

Fixture `contract_registry_polygon_v1.json`：file SHA-256
`9cd24e84c93656fcc92e942f9bb8106b1eff3f2494c61a6408e0327168a087ca`，内部 content hash
`5c0731e770060502febd0b1963da9de2084ff169efb00b92e0a4bef24cc826d8`。
chainId=`137`，finalized block=`91842167`，block hash=
`0x16d35ed4cc72f20c141efcc38d8c0362d4ba95482f3aa96071e85fd06857a47f`，三个 origin response
逐项全等。

| kind/address | proxy | runtime Keccak | resolved implementation | resolved code Keccak |
|---|---|---|---|---|
| pUSD `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` | EIP-1967 | `0xaaa52c8cc8a0e3fd27ce756cc6b4e70c51423e9b597b11f32d3e49f8b1fc890d` | `0x6bbcef9f7ef3b6c592c99e0f206a0de94ad0925f` | `0x932c9369433b333d6d97d99b7731885751862aa3502122786d24174a9fd8e58e` |
| CTF `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` | direct | `0xbe524e094025c2a1122ccfbe3264e29fe662d7e0ae518b6926135c814405eceb` | — | same as runtime |
| Deposit Wallet `0x00000000000Fb5C9ADea0298D729A0CB3823Cc07` | EIP-1967 factory | `0xaaa52c8cc8a0e3fd27ce756cc6b4e70c51423e9b597b11f32d3e49f8b1fc890d` | `0x528cc05efac2b0d255e423272187efd41248abd7` | `0xe6424f1008e46b4b657efacf9500ea7747cbbf3055d9d76459253ac2884793d2` |
| Standard adapter `0xAdA100Db00Ca00073811820692005400218FcE1f` | direct | `0x93b965351d01c1a128821ac79fc98a18105daefb46bda0d1e5b52306d713aa4f` | — | same as runtime |
| NegRisk adapter `0xadA2005600Dec949baf300f4C6120000bDB6eAab` | direct | `0x3b892c7c2f80e7af69f28faf72a51c2d793f6b79b96011bdf0a1996319fcbe5b` | — | same as runtime |

Deposit Wallet 的已部署 wallet sample/beacon 路径另核对：beacon
`0x7a18edfe055488a3128f01f563e5b479d92ffc3a`，beacon runtime Keccak
`0xf87b06a1302051471df08ff79a938757509569e16b7a7efa55a3ea7b29b0b9d1`，implementation
`0xf7f27c29e60fe6325bef8da7f93250353d2e3294`，implementation code Keccak
`0xf5c1072460e64902af84d35f5bb1d0a15d80a88c5827b831a977fbc5a0684b96`。

### 4.3 Golden artifacts

| artifact | file SHA-256 | internal content hash |
|---|---|---|
| chain settlement spec | `85fc64ba9e7439b7704b2cee9fc8833318b116e04c467cfdbe93b57aba97b36e` | `9361542bb9b7dc6ba5bf56aaef0e77fbeb149f204dc796ae6fd686a9882bcb7d` |
| Polygon RPC golden | `43b5f053ca89a759775f7bc05a7bd5924770107483463cf50b38206075390e6f` | `6d0bb5d8604447d21027c8819d8c6f5dcfacc1927133bfabe93067ba50aa14d5` |
| Relayer golden | `64ebd97d8de10610b43a83fa74a402ba43a008c7626afd56c3513d743c6630c6` | `731650675c2be444d816682e3d143e44f3c60df34c2481de9fbfeb5a3364aea9` |
| settlement exact-set | `b4dd61c6e2fb01147ba66193a0428ec9a3b3ef5652540cb2cbf057ebf1e89189` | `a0e91e79772b0d3c9e51599723194bf67f8f6e1102546cd3fdedca871d6681f8` |
| recovery matrix | `60ea7c1fb734c670d4936d59104ad606ff90cec8cd728a1c15eb4a542327ca0f` | `9feda93cd4902a521052653360c7c306ea9005838fd14525b2c3de36c6fb10dd` |

Relayer exact JSON bytes、`transactionID`、raw `STATE_*`、EIP-712、nonce/deadline、HMAC input/signature
与 golden 全等；旧 `/transaction?id=...` route 和 Safe/Proxy capability 保持关闭。

## 5. 最终 clean 验收证据

全部命令在 clean commit `53b4744bbe47a74063db2c99b11f344a93d3c541` 上运行：

| 验收范围 | 结果 |
|---|---:|
| WP-06 unit/contract + Vault composition | **108 passed**，0 skip/fail，0.77s |
| 真 PostgreSQL integration/replay/runtime | **43 passed**，0 skip/fail，76.45s |
| `tests/trading` 全回归 | **1717 passed**，0 skip/fail，8 warnings，277.07s |
| 全仓 | **1928 passed**，0 skip/fail，8 warnings，276.24s |

```text
python3 -m compileall -q app runtimes tests alembic   OK
.venv/bin/pip check                                   No broken requirements
.venv/bin/alembic heads                               b1000052 (head)，唯一
git diff --check                                      exit 0
.venv/bin/alembic upgrade b1000052 --sql              8,771 lines；secret sentinel hits=0
临时测试/性能数据库残留                               0
```

真 PostgreSQL 测试通过真实 Logic/Repository/UoW/constraints；provider 边界使用 deterministic injected
transport，真实 Polygon/Relayer/network/chain/money 调用为 0。P-stability 与 WP-03～05 回归包含在上述
trading/full 结果内。

## 6. 最终 clean 性能证据

Artifact：`/tmp/pm_v2_perf_smoke_6.json`
SHA-256：`bb9185af0317a5f93d57408c0f4b20a86d542c957a587fddb58aa259b8427119`
Git：commit=`53b4744bbe47a74063db2c99b11f344a93d3c541`，dirty=`false`
`hard_assertions=PASS`

| Gate | 真实结果 | 门槛 |
|---|---:|---:|
| logical chain operations，60.005s | **660 / 10.999 ops/s** | ≥10/s |
| 1,000 UNKNOWN recovery pass 1 / pass 2 | **9.350s / 7.329s**；hash 全等；blind resend=0 | identical；blind=0 |
| pool wait p95 | **0.020ms**；high-water=6/6 | ≤20ms；≤6 |
| correctness | lost=0，duplicate=0，unbalanced=0，conflict=0 | 全 0 |
| provider boundary | fake=215,560，real=0，transaction probe failure=0 | fake>0；其余=0 |
| durable counters | operations=1,660，finalized=660，outbox=660，nonzero finalized positions=0 | exact |

| 阶段 | p50 | p95 | p99 |
|---|---:|---:|---:|
| registry preflight | 3.111ms | 12.192ms | 19.848ms |
| TX1 | 13.209ms | 34.665ms | 46.878ms |
| fake submit | 0.057ms | 0.114ms | 0.138ms |
| TX2 | 3.945ms | 14.768ms | 26.569ms |
| receipt → finalized apply | 4.087ms | 10.475ms | 12.504ms |
| logical recovery/finality | 40.810ms | 51.812ms | 60.297ms |
| UNKNOWN recovery（1,000） | 49.749ms | 64.286ms | 70.750ms |
| event-loop lag | 0.545ms | 6.885ms | 13.529ms |

资源：WAL=`52,632,544` bytes，CPU=`67.406s`，RSS start/peak=`869,656/869,656 KiB`；
测试数据库与 artifact root 均在结束时删除。该测试只声明 DB/逻辑/正确性容量，不冒充真实 provider SLO。

## 7. 安全边界与剩余 blocker

- `FIXTURE_ONLY`、`authorized_capital=0`、injected transport；fake call >0，真实 outbound/chain/money=0。
- Vault 明文、Builder/RPC credential、auth header、signature、raw signed body 在通用 DB/log/artifact/offline SQL
  中 sentinel hits=0；source 只保存密文引用/版本、安全 hash 与独立 access audit。
- wrong-chain、空 code、proxy-only hash、registry drift、stale/malformed geoblock、kill/fence/reconciliation
  conflict 都在签名/nonce/submit 前 fail closed。

范围内剩余 P0/P1：**无**。激活 blocker 保留：真实 RPC/Relayer endpoints 与 secret、nonzero capital、
canary/release approval、live signer/Builder capability 尚未提供；WP-06 接受不等于允许真实兑换。

## 8. 非目标与回滚

非目标：不启用 live/canary/nonzero capital；不发真实交易、不 approve/split/merge/redeem；不支持 Other
conversion、Safe/Proxy raw flow、其他链或 CLOB V1；不改 forecast/decision/risk 产品规则；不建设
Admin/UI；不改 V1。

回滚：先 kill 新 operation worker；UNKNOWN/active operation 必须完成只读 reconcile。downgrade 到
`b1000051` 前由迁移 preflight 拒绝未知对象、非 fixture registry、active/UNKNOWN operation 与 chain facts；
先导出 append-only registry/operation/history/observation/ledger artifact manifest，账本纠错只追加 exact
reversal。代码按 `53b4744`、`c3bc35d`、`d03082b`、`de79edc` 逆序 revert；WP-01～05 facts 保留。

COMPLETION_MANIFEST_SHA256: a2280e003d02a9799e263efbef5f1de504f79e2a5e0f94564b6c9a133263f868
