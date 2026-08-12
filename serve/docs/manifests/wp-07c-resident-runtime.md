# WP-07C — 常驻运行时进程装配（P-shadow qualification 运行时，Checkpoint A）

> 状态：**DONE（待审）**——本 checkpoint 完整交付 **outbox 常驻传输层 + 进程装配骨架**；
> 认知/执行/评价/对账/回放 runtime 的 supervisor 注册为**后续 checkpoint**（这些 runtime 类
> 已在 WP-01B~WP-06 建成，本 WP 只负责把它们装配成常驻进程）。
> 前置：WP-07A ACCEPTED；head=`b1000071`。

## 背景与缺口

V2 业务逻辑（16 Logic + 5 handler + 状态机）与运行时编排类（`runtimes/trading/`
下 UniverseIngestor/BookWsIngestor/CognitionRuntime/ShadowExecutionRuntime/
PrivateExecutionRuntime/UserWsExecutionRuntime/EvaluationRuntime/ReconciliationRuntime/
ReplayRuntime）已建成，但**没有进程把它们装配成常驻运行的系统**。本 WP 建装配层。

## 范围（本 checkpoint A）

1. **DB pool profile**：`app/config.py` 增加 `reconciliation`、`outbox` 两档
   （各 pool_size=2/overflow=1/stmt_timeout=30s），全局连接预算 60→66（上限 80）。
   同步 `.env.example`。
2. **handler↔outbox 适配层** `runtimes/trading/_dispatch.py`：`TradingEventDispatch` 把
   `OutboxEnvelope` 按 topic 路由到 5 个 trading handler（cognition/decision/evaluation/
   execution/settlement），重建域事件，`HandlerResult.ok` 映射为 consumer 成败；未知
   topic / handler 不 ok → fail closed。订阅 7 个 topic（blind_commit /
   chain.settlement.finalized / shadow.execution.terminalized / universe.frame /
   universe.refresh / market.book / market.config.refresh）。
3. **outbox 常驻进程** `runtimes/trading/outbox.py`：包装 `app/outbox` 的
   Publisher/Sweeper/Consumer 为常驻循环（publish 0.5s / sweep 5s / consumer block 1s），
   各自持 outbox pool + control Redis，尊重共享 stop_event。
4. **进程 supervisor + 显式注册** `runtimes/trading/supervisor.py` +
   `runtimes/trading/assembly.py` + `runtimes/trading/__main__.py`：`RuntimeSpec`
   显式注册（**不走 Base worker.py 非递归扫描**）；每 runtime 独立 pool；任一 runtime
   首失败 → 全组 fence；SIGINT/SIGTERM 逆序优雅关闭。`python -m runtimes.trading
   --dry-run` 打印注册清单（不联网）。

## §3.1 八 worker → §8 七文件映射（本 WP 固定，消除文档空白）

| §3.1 worker | runtime 文件 | 现有类 |
|---|---|---|
| REST universe scheduler | `market_ingest.py` | `UniverseIngestor.run_once`（调用方循环） |
| Market WS consumer | `market_ingest.py` | `BookWsIngestor.run_epoch` |
| Cohort/R0 + Contract/Opportunity coordinator + Research + Blind forecast + Reveal/decision | `cognition.py` | `CognitionRuntime.run_cognition_chain`（一条链 G0→G7B） |
| Label/evaluation worker | `evaluation.py` | `EvaluationRuntime` |
| 执行/心跳（P-exec-readiness） | `execution.py` | Shadow/Private/UserWs runtimes |
| 对账/链恢复 | `reconciliation.py` | `ReconciliationRuntime` |
| 回放（P3） | `replay.py` | `ReplayRuntime` |
| 传输 | `outbox.py`（本 WP 新建） | `app/outbox` Publisher/Sweeper/Consumer |

## 修改文件

```
serve/app/config.py                                   # +reconciliation/outbox pool profile
serve/.env.example                                    # +DB_RECONCILIATION_*/DB_OUTBOX_*
serve/runtimes/trading/_dispatch.py                   # 新建：envelope→handler 适配
serve/runtimes/trading/outbox.py                      # 新建：publisher/sweeper/consumer 常驻
serve/runtimes/trading/supervisor.py                  # 新建：RuntimeSpec/Supervisor
serve/runtimes/trading/assembly.py                    # 新建：spec 注册 + build_dispatch
serve/runtimes/trading/__main__.py                    # 新建：进程入口（--dry-run / 常驻）
serve/tests/trading/unit/test_v2_runtime_dispatch.py  # 新建：10 单测（无 DB）
serve/tests/trading/unit/test_v2_runtime_supervisor.py# 新建：4 单测（无 DB）
serve/tests/trading/integration/test_v2_runtime_supervisor.py # 新建：2 集成（真 PG，skip w/o V2_TEST_ADMIN_DATABASE_URL）
serve/tests/trading/test_v2_config.py                 # 预算断言 60→66 / 6→8 profile
serve/tests/trading/test_v2_database_profiles.py      # PROFILE_NAMES +2、预算断言
```

## 命令与真实结果

```bash
.venv/bin/python -m runtimes.trading --dry-run        # outbox-publisher/sweeper/consumer（pool=outbox），不联网
.venv/bin/python -m pytest -q tests/trading/unit/test_v2_runtime_dispatch.py       # 10 passed
.venv/bin/python -m pytest -q tests/trading/unit/test_v2_runtime_supervisor.py     # 4 passed
.venv/bin/python -m pytest -q tests/trading/test_v2_config.py tests/trading/test_v2_database_profiles.py  # 108 passed / 1 skipped
.venv/bin/python -m pytest -q                         # 全仓：1666 passed / 364 skipped / 0 failed（14.09s）
.venv/bin/python -m compileall -q runtimes/ …        # OK
.venv/bin/python -c "from app.config import Settings; s=Settings(); print(s.pool_profile_names, s.connection_budget().total)"  # 8 profile / budget 66
```

- 集成测试（真 PG + Redis）：本机无 `V2_TEST_ADMIN_DATABASE_URL`，**2 skipped**；
  逻辑在验收环境复跑（`test_outbox_consumer_runtime_routes_envelope` /
  `test_supervisor_registers_and_stops_outbox_specs`）。
- 临时库残留：0。

## 关键证据

- **pool 隔离**：outbox runtime 独占 `outbox` profile；reconciliation profile 已备；
  全局预算 66 ≤ 80，connection_budget 交叉校验通过。
- **fail closed**：dispatch 未知 topic / handler not-ok → 返回 False / raise，
  由 consumer 记 retry/dead，不静默丢消息。
- **显式注册**：`default_specs()` 是 supervisor 唯一注册入口；duplicate name 拒绝。
- **首失败 fence**：任一 runtime 异常 → 共享 stop_event 置位 → 全组逆序关闭。

## 未完成（后续 checkpoint，诚实标注）

- **认知/执行/评价/对账/回放 runtime 未在 supervisor 注册**：这些类已在 WP-01B~06
  建成，但本 checkpoint 只完整装配了 outbox 传输层；它们的 build 工厂（需 provider
  凭证、模型网关、execution vault）在后续 checkpoint 接入 `default_specs()`。
- 集成测试真 PG 复跑（验收环境）。

## 非目标

- 不做真实 provider 下单（`PM_V2_EXECUTION_EGRESS_MODE=shadow` 阻塞）
- 不改任何 Logic/handler 业务语义（只装配 + 适配）
- 不做 WP-08（分区/归档/perf/alerts/soak）；不做 canary/live 权限升级

## 回滚

`config.py` 移除两档 profile（同时回退 4 处测试断言）；删除 `runtimes/trading/`
新增 5 文件与 3 测试文件；不影响已交付 WP-00~07B（outbox 类、handler、Logic 均未改）。

COMPLETION_MANIFEST_SHA256: a776d1c2c227be31d60f7af8a827e7931719ef14da5197dc109a00ffd5e5be11
