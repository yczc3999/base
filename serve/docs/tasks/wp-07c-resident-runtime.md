# WP-07C — 常驻 Shadow 运行时闭环

> 当前状态：**IN_PROGRESS（P0：universe frame 无法完成）**。
> 本任务只有在全部 checkpoint 和真实验收完成后才生成唯一最终 completion manifest；
> `../manifests/wp-07c-checkpoint-a.md` 是 2026-08-12 Checkpoint A 的历史记录，不能作为
> WP-07C 完成证明。

## 1. 目标与用户价值

把 WP-01B～06 已实现的领域 Logic/Repository/Driver 装配为一条能够长期运行、失败可见、
可回放的 shadow 主链：

```text
Gamma COMPLETE frame
→ confirmed market/membership
→ G0/R0 opportunity
→ contract/component/episode
→ blind AI forecast
→ reveal + G7A/G7B action set
→ shadow execution/ledger
→ label/evaluation/replay/promotion evidence
```

用户价值是让 AI 预测、反复重估和组合降险真正产生可审计事实，而不是停留在类型、表、测试
fixture 或 runtime 注册清单中。

## 2. 已确认决策

1. PostgreSQL 是事实源；Redis、日志、进程存活和 Admin projection 都不替代业务完成证据。
2. `--dry-run` 出现 runtime 名称只证明显式注册；idle runner 不计作已运行。
3. Stage 0 必须先生成完整 REST universe frame；WS hint、部分页或失败 frame 不冒充全集。
4. AI 必须保持 blind commit/reveal 隔离；纯盘口、成本、仓位变化只重算 action，不允许模型改写旧 belief。
5. 运行模式固定为 shadow；WP-07C 不授予 canary/live 权限，不接真实资金 egress。
6. 组合降险复用 `REDUCE/CLOSE/FLIP/RISK_REVIEW` 及 paired-inventory 会计语义；新增风险腿仍完整通过 G7A/G7B。
7. 历史亏损只作为不可变资金事实，不进入追损目标或新仓位 sizing。
8. 任一阶段失败必须进入结构化状态、alert/workflow evidence，并使健康状态反映“业务未推进”；不得返回假 `ok`。

## 3. 真实状态快照

快照时间：`2026-08-15T00:37:50-04:00`；数据库 revision：`b1000075`。

| 项目 | 当前事实 |
|---|---|
| Git | `main` 相对 `origin/main` ahead 60；53 tracked modified + 6 untracked，尚非 release snapshot |
| runtime | 9 个 spec 可 dry-run；outbox 可运行；pipeline 只实现 Stage 0/1；其余五个 spec 仍 idle |
| frame | 259 total：258 `FAILED`、1 `OPEN`、0 `COMPLETE` |
| page | 51,158 页全部为 `events_open`，累计 5,115,750 items |
| market chain | `pm_markets/universe_memberships/screening_episodes=0/0/0` |
| cognition chain | `decision_opportunities/forecast_episodes/ai_invocations/forecast_submissions=0/0/0/0` |
| decision/execution | `trade_decisions/action_sets/economic_action_intents/executions=0/0/0/0` |
| learning | `resolution_labels/metric_runs/promotion_decisions=0/0/0` |
| tests | offline backend 1699 pass/368 skip；runtime target 19 pass/3 skip；真 PG 当前未复验 |

当前直接故障链：`events_open` 位于 endpoint 顺序首位；达到
`max_pages_per_endpoint=200` 后抛 `frame_page_overflow`，流程尚未进入 `markets_open`。
同时 `_sense()` 对失败 frame 仍报告 `ok=True`，使进程存活与业务健康混淆。

## 4. Checkpoints 与精确范围

### A — Outbox 与 supervisor 基础（已交付，历史证据）

- outbox publisher/sweeper/consumer 常驻循环；
- handler dispatch；
- 独立 DB pool profile；
- supervisor 首失败 fence、优雅关闭、显式注册。

### B — Pipeline Stage 0/1 骨架（已实现，未验收）

- `pipeline` spec、shadow seed、frame→membership→R0→opportunity；
- per-market transaction isolation；
- policy hash 单一来源；
- cognition/evaluation/execution/reconciliation/replay 名称已注册，但仍没有实际推进循环。

### C — 当前交付：关闭 Stage 0 P0

允许修改：

```text
serve/app/logics/trading/universe.py
serve/app/repositories/trading/market.py
serve/app/repositories/trading/market_stream.py
serve/app/services/polymarket/gamma_driver.py
serve/app/services/polymarket/base.py
serve/runtimes/trading/market_ingest.py
serve/runtimes/trading/pipeline.py
serve/runtimes/trading/assembly.py
serve/tests/trading/contract/test_v2_gamma_contract.py
serve/tests/trading/unit/test_v2_pipeline_driver.py
serve/tests/trading/unit/test_v2_pipeline_enroll.py
serve/tests/trading/integration/test_v2_universe_ingest.py
serve/tests/trading/integration/test_v2_pipeline_seed.py
serve/tests/trading/replay/test_v2_public_market_replay.py
serve/docs/tasks/README.md
serve/docs/tasks/wp-07c-resident-runtime.md
serve/docs/manifests/README.md
serve/README.md
tofix.md
```

若实际测试文件名不同，只允许使用现有同域 `test_v2_*market*`、`test_v2_*pipeline*` 文件；
新增文件必须在 completion manifest 中解释必要性。不得借此修改 decision/AI 业务语义。

必须关闭：

1. keyset cursor 能终止，或在 provider 数据超出单 frame 预算时使用明确、可恢复且不漏样的扫描策略；
2. open/closed event 与 market 四条物理链均有终止证明；
3. failed/open frame 不更新 current，不进入 cohort；
4. `FrameRunResult.status != COMPLETE` 时 Stage 0 返回失败并产生可观测 reason；
5. 冷启动与断点恢复均能到达 `markets_open/markets_closed`，不重复写 economic facts；
6. 运行时不再无限堆积同原因失败 frame，artifact/page 保留策略明确。

Checkpoint C 验收证据：

- 至少一次完整冷启动 frame 和一次从保存 cursor/lease 恢复后的完整 frame；
- `REST confirmed markets = universe memberships = R0 dispositions`，缺失/重复均为 0；
- 失败 fixture 证明 cursor break、page overflow、timeout 均 fail closed，且健康状态不报成功；
- 固定 artifact 重放产生相同 frame/content/market hashes；
- 真实 PostgreSQL 集成测试、迁移 head、targeted suite、全量 suite 均记录命令与结果。

### D — AI、决策、执行和评价主链

预计允许修改域：

```text
serve/app/config.py
serve/app/services/ai/**
serve/runtimes/trading/assembly.py
serve/runtimes/trading/pipeline.py
serve/runtimes/trading/cognition.py
serve/runtimes/trading/evaluation.py
serve/runtimes/trading/execution.py
serve/runtimes/trading/reconciliation.py
serve/runtimes/trading/replay.py
serve/runtimes/trading/seed.py
serve/tests/trading/{unit,integration,contract,replay}/test_v2_*.py
```

进入本 checkpoint 前先在本文补齐具体 gateway/credential/driver 输入和精确测试文件；不得把
`glob` 当作无限改动许可。完成条件：删除 Stage 2～4 固定 `ai_gated` 实现，所有常驻 spec 有
实际推进/lease/event loop，并由一条真实 shadow chain 证明非零 AI、forecast、decision、action、
execution、ledger、evaluation 和 replay facts。

### E — 整体交付与 WP-08 交接

- 生成且只生成一份最终 `docs/manifests/wp-07c-resident-runtime.md`；
- 记录 release/git/db revision、配置 hash、真实数据计数、失败注入、回放 hash、命令和回滚；
- WP-07B 浏览器整改独立关闭；
- WP-08 承担 partition/archive/perf/alerts/soak，不在 WP-07C 内伪造 qualification。

## 5. 验收命令

```bash
cd v2/serve
.venv/bin/python -m runtimes.trading --dry-run
.venv/bin/python -m compileall -q app runtimes tests
.venv/bin/alembic heads
.venv/bin/alembic current
.venv/bin/pytest -q tests/trading/unit/test_v2_pipeline_driver.py
.venv/bin/pytest -q tests/trading/integration -k 'market_ingest or pipeline or runtime'
.venv/bin/pytest -q

cd ..
git diff --check
```

真 PostgreSQL 验收必须设置有 `CREATE DATABASE` 权限的 `V2_TEST_ADMIN_DATABASE_URL`，并记录
临时库创建/销毁结果。没有该证据时保持 `IN_PROGRESS`。

## 6. 依赖与 blocker

- 当前 P0：Gamma frame 在 `events_open` 达到 200 页后失败。
- Checkpoint D 外部依赖：获批模型 gateway、模型角色绑定和仅服务端可见的 provider credential。
- 当前测试环境 blocker：现有数据库用户没有 `CREATE DATABASE`，真 PG suite 不能复验。
- 当前工作树不是冻结 release；完成前必须划清变更范围并生成可复现 SHA。

## 7. 非目标

- 不发送真实订单、链上交易或资金副作用；
- 不用 mock、人工插表或 Admin projection 冒充常驻闭环；
- 不改变已接受 WP 的 append-only、blind、Gate、ledger 和 permission 语义；
- 不以增加 page cap、吞掉异常或把 FAILED 标 COMPLETE 关闭 Stage 0；
- 不在本 WP 内宣称 shadow qualification、canary 或 live 完成。

## 8. 风险与回滚

- universe 修复必须保留 raw artifact 和失败 frame；回滚只切换 release/config，不删除事实；
- cursor/endpoint 策略变化可能使 cohort 不可比较，必须生成新 frame/policy hash，旧样本不回写；
- AI/gateway 接入失败时关闭新增 episode/action，保留 sensing 与既有 facts；
- execution 不确定状态只允许 reconcile/REDUCE/CLOSE，不允许新增 exposure；
- runtime health/alert 修改可独立回滚，但不得恢复“失败返回 ok”的旧行为。

## 9. 最后更新

`2026-08-15T00:37:50-04:00`
