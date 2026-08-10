# COMPLETION MANIFEST — WP-01B · Polymarket 公共市场数据

- Work package: `WP-01B`
- 状态: **ACCEPTED（审查者复验）**
- 日期: 2026-08-10 EDT
- 任务合同: `serve/docs/tasks/wp-01b-public-market-data.md`
- Revision: `b1000010` → `b1000011`；head=`b1000011`
- 实现基线: `be8e2824f45e93ba6b3e51bb8c444e0f4991545a`

## 1. 交付范围

### 1.1 生产文件

任务约定的 23 个生产文件均已落地：

```text
app/models/trading/{market.py,market_stream.py,__init__.py}
app/models/__init__.py
alembic/versions/b1000010_v2_0010_p1a_market_master.py
alembic/versions/b1000011_v2_0011_p1a_evidence_partitions.py
app/schemas/polymarket/{__init__.py,common.py,gamma.py,clob_public.py,market_ws.py}
app/services/polymarket/{__init__.py,base.py,gamma_driver.py,clob_public_driver.py,market_ws_driver.py,service.py}
app/repositories/trading/{__init__.py,market.py,market_stream.py}
app/logics/trading/{universe.py,market_data.py}
runtimes/trading/market_ingest.py
```

另新增包入口 `app/logics/trading/__init__.py`、`runtimes/{__init__.py,trading/__init__.py}`。
测试共享 fixture、既有 migration-head 断言及模型表清单同步到 `b1000011`；没有改变 Base
业务语义。

### 1.2 数据库对象

`b1000010` 固定创建 9 表：

```text
pm_universe_frames, pm_universe_frame_pages, pm_events, pm_markets,
pm_market_versions, pm_tokens, pm_token_versions,
pm_market_lifecycle_events, pm_market_current
```

`b1000011` 固定创建 7 表：

```text
pm_connection_epochs, pm_source_event_batches, pm_source_event_index,
pm_book_checkpoints, pm_book_levels, pm_book_current, pm_quote_bindings
```

4 张高吞吐证据表按 UTC 日 RANGE 分区，无 default partition；当前日及未来 7 日共生成
32 个分区。两个 revision 均为冻结 DDL，不导入 live ORM。

## 2. 已实现合同

### 2.1 官方 wire 与证据

- Gamma 只实现 keyset event/market 与 detail；拒绝 offset，严格解析 cursor、`questionID`、
  JSON-string outcomes/prices/token arrays。
- CLOB 按官方原始 wire：`POST /books` 使用 JSON array，`/time` 是裸整数，
  `/clob-markets` 解析紧凑字段，`/fee-rate` 解析 `base_fee`，token reverse lookup 走 CLOB。
- `/price` 保存请求 side 与 quote role：provider wire `BUY→BEST_BID`、`SELL→BEST_ASK`；
  实际可成交动作仍以完整簿 `max(bids)` / `min(asks)` 为唯一权威，不从 side 名称猜交易成本。
- Market WS 以 `event_type` 判别，支持官方 `price_changes[]`、`new_tick_size`、book、trade、
  new/resolved/best-bid-ask；独立 PING/PONG watchdog，持续行情不能掩盖失联。
- HTTP/WS 成功、失败、retry、parse/schema terminal 都产生有界、脱敏 receipt；request hash 覆盖
  method、规范 path/query 与 body hash，错误保留全部 attempt 证据。

Golden fixtures 是官方 wire 结构的去凭据副本；contract 测试不访问公网。

### 2.2 Universe：完整、可恢复、不会静默丢样

- 每个 frame 由 owner、lease、fencing token 控制；活租约不可抢占，过期后仅新 owner + 更高
  fencing token 可接管。取消/崩溃保留最后已 durable cursor，恢复不重抓已完成页。
- 每页顺序是网络事务外获取 → raw CAS artifact → 短 UoW 写 page、attempt/source index、续租；
  page identity/hash 覆盖 endpoint、cursor、item count 与 raw artifact，而不是只 hash cursor。
- 一个完整 frame 必须包含 `events_open/events_closed/markets_open/markets_closed` 四条连续终止
  cursor 链；数据库核对链与计数后才允许 `COMPLETE`。终态 frame/page 不可更新或删除。
- event 内嵌 market 建立权威 event→market mapping；每条 typed fact 指向自己的实际页 artifact。
  market/token version append-only，current 使用 CAS；closed、inactive、不完整 mapping 与完整 frame
  中缺失项均 fail-closed，不再保持旧 eligible。
- COMPLETE、master/current diff 与 outbox 同一 UoW；失败 frame 不发布 current。

### 2.3 Book：epoch barrier、fencing、checkpoint pin

- epoch 必须绑定已发布 release；状态为
  `CONNECTING→SYNCING→LIVE→STALE→CLOSED`。只有 CONNECTING/SYNCING/LIVE 占 active slot；
  断线立即使该 epoch current 失效并允许新 epoch 重连。
- 每个订阅 token 的 full snapshot/REST baseline 到齐后才原子切 LIVE。旧或 STALE epoch 即使时间戳
  更新也不能覆盖新 current；delta 不跨 epoch 拼接。
- 每个 raw WS frame 先进入 Artifact Store 和 source evidence，再写 checkpoint/levels/current/outbox；
  `(epoch,receive_seq)` 通过非分区 `idempotency_claims` 获得全局幂等。
- current 与 quote binding 精确 pin `(checkpoint_id,checkpoint_received_at,token_id)`；quote 必须双边
  存在、`ask>bid`、`stale_at>as_of`。epoch 非 LIVE、缺边、crossed、非完整或超 TTL 均 hard-stop。
- `tick_size_change` 建新 checkpoint；`new_market/market_resolved` 只发布 refresh 请求，不伪造
  master 或结算标签。

### 2.4 回放与性能

- Universe replay 从 CAS raw pages 经真实 Logic/Repository 重建；Book replay 从 CAS raw WS frame 经
  新 runtime/repository 重建。清库/新 runtime 后 versions、checkpoint/current、source/outbox hash
  一致。
- 性能 harness 使用真 PostgreSQL、`QueuePool(pool_size=4,max_overflow=0)`、100ms microbatch 和真实
  墙钟 pacing；不使用 NullPool 或“尽快写完”冒充持续吞吐。

## 3. 审查整改记录

审查者在同一 WP 内直接关闭了以下阻塞项，没有创建整改链：

1. 对齐官方 WS/CLOB wire、严格数值与 cursor、total timeout、PONG watchdog；
2. 所有 attempt/WS frame 的 raw artifact、receipt、source index 与 outbox 落库；
3. Universe lease/fencing、逐页 durability、四链 COMPLETE 与真实 cursor resume；
4. event→market mapping、逐页 lineage、closed/absent current 失效；
5. epoch LIVE barrier、断线 STALE/CLOSED、新 epoch 与旧 epoch fencing；
6. checkpoint/quote composite FK、source 全局幂等与终态 append-only；
7. `b1000010/b1000011` destructive downgrade 在 DDL 前 fail-closed；
8. 用真实 60s/10s paced workload 替换伪性能烟测。

当前未解决 P0/P1：**0**。

## 4. 可复现验收结果

```bash
cd /code/pollymarket/v2/serve

python3 -m compileall -q app tests alembic runtimes
# exit 0

.venv/bin/pytest -q \
  tests/trading/contract/test_v2_gamma_contract.py \
  tests/trading/contract/test_v2_clob_public_contract.py \
  tests/trading/contract/test_v2_market_ws_contract.py
# 70 passed in 3.75s

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0010_market_master_migration.py \
  tests/trading/integration/test_v2_0011_market_stream_migration.py \
  tests/trading/integration/test_v2_book_resync.py \
  tests/trading/replay/test_v2_public_market_replay.py
# 27 passed in 10.40s

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_universe_ingest.py
# 3 passed in 4.75s

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/python -m tests.trading.performance.ingest_smoke
# PASS；完整结果 /tmp/pm_v2_perf_smoke.json

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/pytest -q tests/trading
# 933 passed, 8 warnings in 52.83s

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
# 1144 passed, 8 warnings in 53.83s

.venv/bin/alembic heads
# b1000011 (head)

.venv/bin/alembic upgrade b1000011 --sql > /tmp/wp01b.sql
# 1602 lines；48 CREATE TABLE trading.pm_*；32 PARTITION OF；secret value hits=0

git diff --check
# exit 0
```

8 个 warning 均为现有 FastAPI/Starlette deprecation；无 skip、失败或 RuntimeWarning。

### 4.1 性能证据

环境：GTR，Linux 7.0.0，Python 3.12.3，PostgreSQL 18.4，16 CPU；deterministic-v1 seed。

| 工作负载 | 实际结果 | 门槛 |
|---|---:|---:|
| 10k markets / 20k tokens frame | 10.457s；行数与 4 pages 精确 | 零丢失/重复 |
| 1k events/s × 60s | 60,000；receipt→durable p99 133.201ms | ≤250ms |
| 5k events/s × 10s | 50,000；receipt→durable p99 167.760ms | ≤250ms |
| 1,000 book writes | current p99 25.034ms | ≤750ms |
| DB pool | peak 1 / budget 4；overflow 0 | 不超预算 |

合计 110,000 source events / 700 batches：零丢失、零重复；21 项硬断言全部通过。

## 5. Blocker、非目标与回滚

- Blocker：无。
- 非目标保持：无钱包/私有 CLOB/Data API/Polygon、无 AI、无概率/edge/仓位/订单、无 Admin UI，
  未读取或迁移 V1 交易代码/数据。
- 回滚：停止 ingest 后执行 `alembic downgrade b1000002`；0011→0010 顺序删除本 WP 对象。
  遇未知 schema 对象会在 destructive DDL 前失败并整次回滚，改走 roll-forward；raw artifact 不删。

## 6. Manifest SHA-256

口径：删除本文件中“恰好为 64 位十六进制”的哈希行后计算。

```text
06b01cdd60ead9657756f01c2b890064211c8e98bfaaae992543792bf9b8c4a2
```

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-01b-public-market-data.md | sha256sum
```
