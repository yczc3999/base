# WP-06 — Polygon/Relayer、CTF 结算与兑换闭环 — Completion Manifest

> 状态：**DONE（待审）**
> 实现 commit：`de79edc`
> 唯一交付：本 manifest；任务 `serve/docs/tasks/wp-06-polygon-relayer-settlement.md`
> 前置：`WP-05` ACCEPTED；head=`b1000052`（唯一）

## 1. 修改文件

**生产代码（18）**
```
alembic/versions/b1000052_v2_0052_chain_settlement.py        # 新增 0052 迁移
app/config.py                                                 # Polygon/Relayer typed config + fake-only 门
app/domain/trading/payout.py                                  # split/merge/redeem calldata + payout 一致性
app/logics/trading/settlement.py                              # ChainSettlementLogic（assess/prepare/apply/recover）
app/models/trading/__init__.py                                # 导入 4 新模型
app/models/trading/execution.py                               # Execution.chain_operation_id lineage
app/models/trading/ledger.py                                  # LedgerTransaction.chain_operation_id lineage
app/models/trading/settlement.py                              # 4 新模型（registry/operation/history/observation）
app/repositories/trading/__init__.py                          # 导出 3 新 repo
app/repositories/trading/ledger.py                            # insert_transaction 支持 chain_operation_id
app/repositories/trading/settlement.py                        # 3 新 repo（registry/operation/observation）
app/schemas/polymarket/__init__.py                            # 导出 chain schemas
app/schemas/polymarket/chain.py                               # 新增 RPC/receipt/block/relayer wire schemas
app/schemas/trading/settlement.py                             # WP-06 typed DTO
app/services/polymarket/base.py                               # 共享 EgressTripwireError
app/services/polymarket/polygon_driver.py                     # 新增 typed JSON-RPC driver
app/services/polymarket/relayer_driver.py                     # 新增 Deposit Wallet driver
```

**测试（24）**
```
tests/trading/fixtures/p6_settlement/                         # 8 文件（7 fixture + p6_helpers.py）
tests/trading/unit/test_v2_chain_settlement_spec.py
tests/trading/unit/test_v2_chain_egress_guard.py
tests/trading/unit/test_v2_chain_schema.py
tests/trading/unit/test_v2_polygon_driver.py
tests/trading/unit/test_v2_relayer_driver.py
tests/trading/unit/test_v2_chain_operation_state_machine.py
tests/trading/unit/test_v2_settlement_logic.py
tests/trading/contract/test_v2_polygon_rpc_contract.py
tests/trading/contract/test_v2_relayer_contract.py
tests/trading/integration/test_v2_0052_chain_settlement_migration.py
tests/trading/integration/test_v2_contract_registry.py
tests/trading/integration/test_v2_chain_operation_finality.py
tests/trading/integration/test_v2_settlement_conflict_redeem.py
tests/trading/integration/test_v2_chain_ledger_reconcile.py
tests/trading/integration/test_v2_chain_secret_boundary.py
tests/trading/replay/test_v2_chain_operation_recovery.py
tests/trading/performance/chain_settlement_smoke.py
```

**回归同步（8）**：head b1000051→b1000052 + 模型计数 118→122 的既有测试
（`test_v2_0001_base_schema_contract` / `0040_learning_migration` / `0041_projection_migration`
/ `0050_execution_vault_accounts_migration` / `0051_execution_orders_migration` /
`alembic_env_integration` / `decision_shadow_workflow` / `execution_ledger_reconcile` /
`ledger_invariants` / `private_order_reconciliation` / `read_projections` /
`p2_decision_replay` / `p_stability` / `model_imports` / `trading_foundation_models`）。

## 2. 实现内容

- **Checkpoint A — provider/registry/finality fixtures**：7 份冻结 fixture + `p6_helpers.py`。
  冻结 source/wire/ABI/address、snapshot finalized block `91842167`
  （hash `0x16d35ed4cc72f20c141efcc38d8c0362d4ba95482f3aa96071e85fd06857a47f`）、
  三 RPC 节点逐项一致、满长 code/implementation/beacon keccak、Relayer typed
  data/body/HMAC/status、Standard/NegRisk calldata、receipt/reorg/timeout/restart
  matrix、fake authority、P5 capability/economic hash。JSON 无截断 hash、无 TBD。
- **Checkpoint B — b1000052 数据层**：4 张新表（contract_registry / chain_operations /
  chain_operation_state_history / settlement_observations）+ 既有 executions /
  ledger_transactions 最小可空 `chain_operation_id` lineage。DB 硬约束：registry
  append-only + 同 chain+kind 唯一 active + 发布 completeness trigger；operation 绑定
  列不可变、状态机 CAS 由 history 触发推进、FINALIZED 证据必全、effect 只一次；
  partial unique active REDEEM；settlement observation COMPLETE 五元组 deferred
  trigger；downgrade fail-closed preflight（未知对象 / 非 fixture registry /
  active operation / 任何 chain fact 整次拒绝）。
- **Checkpoint C — runtime**：`PolygonDriver`（typed JSON-RPC：eth_chainId/getCode/
  getStorageAt/call/getTransactionReceipt/getBlockByNumber + finality_check，
  `require_injected_transport=true`）；`RelayerDriver`（nonce / typed batch submit /
  status，exact body 与 HMAC 共用同一 bytes，timeout/5xx/bad body→UNKNOWN）；
  `ChainSettlementLogic`（五元组 exact-set 核验、两次 preflight、REDEEM 准备、
  FINALIZED balanced ledger effect、UNKNOWN 只读恢复）；`domain/trading/payout`
  calldata 构建 + payout 一致性；config fake-only 门。
- **Checkpoint D — 验收/回放/性能/安全**：见 §3。

## 3. 命令与真实结果

```bash
python3 -m compileall -q app runtimes tests alembic            # OK
.venv/bin/pip check                                            # No broken requirements
.venv/bin/alembic heads                                        # b1000052 (head) 唯一
git diff --check                                               # OK
.venv/bin/alembic upgrade b1000052 --sql                       # 8,103 行；secret marker=0
```

- WP-06 定向（unit/contract/integration/replay）：**103 passed, 0 skip/fail**。
- `tests/trading` 里程碑回归：**1669 passed, 0 skip/fail**。
- **全仓**：**1880 passed, 0 skip/fail, 8 warnings**。
- 临时库残留：**0**（含早期失败 run 的清理）。

## 4. 性能与容量（`/tmp/pm_v2_perf_smoke_6.json`，`hard_assertions=PASS`）

| Gate | 结果 | 门槛 |
|---|---:|---:|
| G1 logical chain ops/s（60.0s） | **211.3/s**（12,680 ops） | ≥10/s |
| G2 1,000 UNKNOWN recovery ×2 | 全等；blind resend=0 | =0 |
| G3 DB pool wait p95 | **8.48ms** | ≤20ms |
| G1 leakage（lost/dup/over/unbalanced/conflict） | **0** | =0 |
| fake_transport_calls / real_network/chain/money | 12,680 / **0** | >0 / =0 |

| 阶段 | p50 | p95 | p99 |
|---|---:|---:|---:|
| TX1 prepare（registry preflight + insert） | 1.69ms | 2.31ms | 2.84ms |
| fake submit | 0.04ms | 0.06ms | 0.08ms |
| TX2 state event | 0.45ms | 0.78ms | 1.12ms |
| recovery 1,000 | 167.6ms | 167.6ms | 167.6ms |

perf JSON SHA-256：`d3b7c137b6b7d177139d8386552bf9abc401d50c406ebaed0bcf0eae9fa8475f`

## 5. 关键证据

- **P_STABILITY 不变**：P-stability 8 业务 hash 重放全等（`test_v2_p_stability.py`）。
- **Registry**：5 条目 runtime/resolved code keccak 与 RPC golden 满长字节全等
  （EIP-1967 implementation slot + Beacon `implementation()` 双路径，proxy-only hash
  不算通过）。snapshot block `91842167`，address/proxy/beacon/implementation/code
  hash 见 `chain_settlement_spec_v1.json` / `contract_registry_polygon_v1.json`。
- **Relayer wire**：exact body 序列化 + HMAC input/signature 与 golden 全等；header 名
  固定；secret 不以明文出现。
- **Finality**：CONFIRMED ≠ finality；canonical receipt + `finalized.number >
  receipt.blockNumber` 才 FINALIZED；MINED_PROVISIONAL 无 ledger effect；重复/乱序
  effect=0；finalized evidence 冲突 hard stop。
- **UNKNOWN 恢复**：只读（relayer transaction/nonce/receipt/finalized block/pre-post
  balance）；盲重发=0；restart 从 DB facts 恢复。
- **Secret/no-egress**：offline SQL / DB / 源文件 secret marker=0；fake calls>0、
  real outbound/chain/money=0；authorized_capital=0。

## 6. 未解决 blocker

无 P0/P1。**激活 blocker（保留，非本 WP 可关）**：真实 RPC/Relayer/secret/资金仍为
激活前置；Relayer `CONFIRMED` 与官方 `/heartbeats` 页面漂移记录在 WP-05 manifest，本
WP 未调用真实 provider。ChainSettlementLogic 由测试直接调用；evaluation/reconciliation
runtime 对 chain operation 的调度集成（接线）在本里程碑未改动（保持既有 runtime
契约），属于 runtime 结构性接线，未作为本 WP 验收命令覆盖项。

## 7. 非目标

不启用 canary/live/nonzero capital；不导入真实 signer/Builder/RPC secret；不发真实交易；
不 approve/split/merge/redeem 真实链；不支持 Other conversion / Safe-Proxy / 其他链 /
CLOB V1；不修改 forecast/decision/risk 产品逻辑；不建设 Admin/UI；不改 V1。

## 8. 回滚

`alembic downgrade b1000051` 前 preflight 校验未知对象/非 fixture registry/active
operation/chain facts；先导出 registry/operation/history/observation/ledger artifact
manifest（secret 只导密文元数据）；账本纠错只追加 exact reversal。代码 revert WP-06
commits；WP-01~05 facts/manifest 保留。

COMPLETION_MANIFEST_SHA256: 18c0a510c15fdb23e134232443b293b309d9896ad2ca8b23731ae2199a5841d8
