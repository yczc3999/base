# WP-01B — Polymarket 公共市场数据完整里程碑

> 状态：**ACCEPTED（2026-08-10 审查者复验）**。执行模型：DeepSeek V4 Flash。
> 完成 manifest 固定为 `serve/docs/manifests/wp-01b-public-market-data.md`。
> 最后更新：2026-08-10 EDT。

## 1. 目标与用户价值

建立 V2 的公共市场事实入口：完整发现 Polymarket 市场，保存可回放的 Gamma/CLOB 原始证据，
维护版本化 market/token master 与可靠 order-book current projection。完成后，下游才能在明确
`as-of`、可成交报价和 freshness Gate 上做 contract、预测和决策，避免 V1 的已关闭市场、旧报价、
重复下注事故。

本里程碑连续完成 4 个 checkpoint，中间不等待用户、不拆 task/manifest：

`A typed wire contract/driver → B market master/0010 → C source+book/0011 → D ingest/resync/stale 验收`。

## 2. 已确认决策

1. Gamma 是 event/market catalog 权威；CLOB 是 token、book、tick、fee、server time 权威；
   Data API 不用于 universe discovery。
2. ID 分列保存，禁止混用：`gamma_event_id`、`gamma_market_id`、`condition_id`、`question_id`、
   `yes_token_id`、`no_token_id`。Gamma 的 outcomes/prices/token IDs 字符串数组必须二次解析并按
   index 绑定；已知字段类型错误 fail-closed，未知字段保留于 raw artifact/`raw_extra`。
3. 使用 Gamma keyset：events limit≤500、markets limit≤100；cursor 链完整且 frame 终态为
   COMPLETE 后才允许 current/universe diff，partial/failed frame 不发布。
4. 公共 book 使用 Decimal；`best_bid=max(bids.price)`、`best_ask=min(asks.price)`，绝不读取数组
   `[0]`；price/size/tick/min-size 禁止 float。
5. Market WS 没有 sequence/resume token。每次连接创建 epoch，先 `SYNCING`；断线后重连并获取
   initial full book（必要时 REST `/books`），每 token 原子 cutover 后才 `LIVE`。不得猜接 gap。
6. PostgreSQL 保存事实、artifact locator、epoch/checkpoint/index；进程内 L1 保存热 book；Redis
   只发布可丢弃的 current/version signal。任何 decision-bound checkpoint 永久 pin。
7. 原始大 payload 进入 Artifact Store；DB 仅放窄索引、hash、batch/ordinal 和关键规范字段。
   每次 HTTP/WS attempt（含失败）必须产生无 secret 的 request receipt/source event，不能只写日志。
8. 两个时钟分开：provider/event timestamp 与系统 `received_at`。无 provider sequence 时使用本地
   `(connection_epoch_id, receive_seq)`；它只证明本地接收顺序，不声称上游无 gap。
9. quote freshness fail-closed。TTL、resync timeout、batch size、subscription shard 必须来自被固定
   config/release；本任务测试用显式 policy fixture，不读取 generic settings/latest。
10. 本期只接公共数据，不需要钱包/API key，不得产生交易、预测、edge 或资金动作。
11. Revision 固定：`b1000010` down=`b1000002`；`b1000011` down=`b1000010`；两者均使用
    revision 内固定 DDL，不导入 live ORM。

## 3. 依赖与必读

- `AGENTS.md`
- `docs/polymarket-integration-design.md` §2–§6、§13–§16
- `docs/performance-cache-database-design.md` §5–§8、§13
- `docs/v2-implementation-contract.md` §4–§6、§8、§11–§12
- 已接受 `WP-01A-02` task/manifest；head 必须是 `b1000002`

若官方 fixture 与上述 wire contract 冲突，记录准确响应并标 `BLOCKED`，不得静默默认字段。

## 4. 精确文件范围

### 4.1 生产文件（同一领域，允许 23 个）

```text
serve/app/models/trading/market.py
serve/app/models/trading/market_stream.py
serve/app/models/trading/__init__.py
serve/app/models/__init__.py
serve/alembic/versions/b1000010_v2_0010_p1a_market_master.py
serve/alembic/versions/b1000011_v2_0011_p1a_evidence_partitions.py
serve/app/schemas/polymarket/__init__.py
serve/app/schemas/polymarket/common.py
serve/app/schemas/polymarket/gamma.py
serve/app/schemas/polymarket/clob_public.py
serve/app/schemas/polymarket/market_ws.py
serve/app/services/polymarket/__init__.py
serve/app/services/polymarket/base.py
serve/app/services/polymarket/gamma_driver.py
serve/app/services/polymarket/clob_public_driver.py
serve/app/services/polymarket/market_ws_driver.py
serve/app/services/polymarket/service.py
serve/app/repositories/trading/__init__.py
serve/app/repositories/trading/market.py
serve/app/repositories/trading/market_stream.py
serve/app/logics/trading/universe.py
serve/app/logics/trading/market_data.py
serve/runtimes/trading/market_ingest.py
```

Package `__init__.py` 只显式 export。禁止修改其他生产文件；若确需新增一个直接测试 helper，可放
`tests/trading/fixtures/`，不得借机建立通用框架。

### 4.2 测试与交付

```text
serve/tests/trading/contract/fixtures/polymarket_public/*.json
serve/tests/trading/contract/test_v2_gamma_contract.py
serve/tests/trading/contract/test_v2_clob_public_contract.py
serve/tests/trading/contract/test_v2_market_ws_contract.py
serve/tests/trading/integration/test_v2_0010_market_master_migration.py
serve/tests/trading/integration/test_v2_0011_market_stream_migration.py
serve/tests/trading/integration/test_v2_universe_ingest.py
serve/tests/trading/integration/test_v2_book_resync.py
serve/tests/trading/replay/test_v2_public_market_replay.py
serve/docs/manifests/wp-01b-public-market-data.md
serve/docs/tasks/README.md
serve/docs/manifests/README.md
```

## 5. 实现合同

### 5.1 Checkpoint A — typed schema、Service 与 Driver

- Pydantic schema 只解析/规范化，不发网络；HTTP/WS Driver 只实现 wire、timeout、有限 retry、
  rate-limit 与 typed result，不写 DB/Redis、不做业务判断。
- Gamma：`/events/keyset`、`/markets/keyset`、event/market detail；拒绝 offset；验证 cursor 单调链、
  JSON-string arrays 长度/YES-NO mapping。
- CLOB public：`/book`、`POST /books`（≤500）、`/clob-markets/{condition_id}`、`/time`、tick/fee；
  兼容 price string/number 后统一 Decimal。
- WS：market endpoint、subscribe/unsubscribe、`PING/PONG`、book/price_change/last_trade/tick/new/resolved
  判别联合；不把 timestamp/hash 当 sequence。
- `base.py` 统一 connect/read/total timeout、429/425/5xx 有界 retry+jitter、固定 reason code 和脱敏
  receipt；4xx（除明确 retryable）不重试。Service 只构造短生命周期 Driver 并把每次 attempt receipt
  交给调用方持久化；禁止模块级连接 singleton。
- Golden fixture 必须来自任务文档列明的官方格式或已保存的生产响应，删除凭据但不“修漂亮”。

### 5.2 Checkpoint B — `b1000010` market master

创建且只创建：

```text
pm_universe_frames
pm_universe_frame_pages
pm_events
pm_markets
pm_market_versions
pm_tokens
pm_token_versions
pm_market_lifecycle_events
pm_market_current
```

关键 DB 不变量：

- frame/page append-only；`(frame_id,page_no)`、cursor input/output、artifact/hash 唯一且可追溯；frame
  状态 `OPEN|COMPLETE|FAILED`，只有完整 cursor 终止链可 COMPLETE。
- event/market/token stable identity 与 provider ID/condition/token 唯一；version/lifecycle append-only；
  current 是可重建 projection，CAS 使用 source version/observed time，旧帧/乱序不得覆盖新值。
- market version 保存 question、description、规则/截止、active/closed/acceptingOrders/
  enableOrderBook/negRisk、原始 artifact 与 normalized hash；token version 保存 outcome/index/price hint。
- 每个二元 market 恰有 YES(0)/NO(1) 两 token；condition 与 token reverse mapping 一致；不完整 mapping
  保留事实但标 INVALID，不进入下游 eligible。
- revision DDL 是固定快照，不导入 live ORM；literal-empty/existing-Base 真 PG roundtrip，未知对象
  downgrade fail-closed，public 与 0002 数据保持。

### 5.3 Checkpoint C — `b1000011` source/book evidence

创建且只创建：

```text
pm_connection_epochs
pm_source_event_batches
pm_source_event_index
pm_book_checkpoints
pm_book_levels
pm_book_current
pm_quote_bindings
```

- source event/index 与 book checkpoint 按 `received_at` UTC RANGE 分区，无 default partition；创建当前
  UTC 日及未来 7 日。分区唯一/PK 必须含时间，全局幂等通过 foundation `idempotency_claims`。
- batch 指向 raw artifact；index 保存 source、kind、epoch、local receive seq、provider/event time、
  received_at、payload hash、batch ordinal、parse status/reason。一个 epoch 内 receive seq 唯一。
- epoch 状态机 `CONNECTING→SYNCING→LIVE→STALE|CLOSED`；一次 epoch 只属于一个 shard/config/release。
- checkpoint/levels append-only；checkpoint 保存 token、epoch、source kind、book hash、best bid/ask、
  tick/min-size、provider timestamp、received_at、artifact、completeness。level 以 side/price/base-unit size 唯一。
- `pm_book_current` 仅为可重建 projection，原子替换完整 snapshot 后更新；delta 只应用于当前 LIVE
  epoch。`pm_quote_bindings` append-only pin 精确 checkpoint/price convention/as-of/staleness。
- 决策绑定 checkpoint 不受普通 retention 删除；本任务只建 pin，不实现 decision。

### 5.4 Checkpoint D — ingest、恢复与 freshness

- Universe scheduler：开启 frame→逐页拉取并先保存 raw artifact/receipt→同一 UoW 写 page、versions、
  lifecycle/outbox→cursor 终止后验证 page chain/count/hash→发布 COMPLETE 和 current diff；任意失败标
  FAILED，current 不变。重复页/进程崩溃可从 cursor 恢复，effect=0。
- Market WS：创建 epoch 并订阅 shard；initial dump/REST baseline 到齐前 token 为 SYNCING；以明确
  cutover checkpoint 原子建立 current；之后按 local seq apply delta。断线立即 STALE，重连新 epoch，
  重新 full snapshot；不跨 epoch 拼 delta。
- `tick_size_change` 使配置缓存失效并触发 CLOB refresh；new/resolved 只触发 Gamma detail/frame refresh，
  不直接伪造 master/label。
- `freshness(policy, now, checkpoint)` 是无 DB/网络纯判断：epoch 非 LIVE、snapshot 不完整、book crossed、
  bid/ask 缺失、年龄超 TTL 任一返回固定 hard-stop reason。无有效 book 时绝不制造 0.5/空簿。
- 认知/交易双时钟：quote/depth 更新只更新 book/valuation 输入，不触发 AI/forecast（本任务也无 AI）。
- 所有业务写 + outbox 同一 UoW；网络调用在事务外；进程 crash 后靠 source artifact、cursor、epoch、
  checkpoint 重放出相同 current hash。

## 6. 必测验收

1. Contract：正常、unknown fields、缺字段、错误类型、数组长度/顺序错、price string/number、空簿、
   429/425/5xx/timeout、secret redaction；测试不得访问公网。
2. Migration：两个 revision 的 empty/existing-Base roundtrip、fixed DDL、分区边界/缺分区 fail、唯一/
   FK/CHECK/append-only/current CAS、unknown object rollback、metadata drift。
3. Universe：多页 keyset、最后 cursor、重复页、页缺失、cursor 环、进程中断恢复、failed frame 不更新
   current、完整 frame diff、closed/inactive 保存但不 eligible。
4. Book：无序 levels 求 max bid/min ask；initial snapshot + delta/delete；断线时立即 stale；旧 epoch delta
   拒绝；REST/WS cutover；duplicate/乱序；tick change；crossed/缺边/TTL hard stop。
5. Replay：固定 artifact/event 流两次重放得到相同 versions/current/checkpoint hash；DB/Redis 重启后相同。
6. 性能烟测（非最终 WP-08 压测）：10k markets/20k tokens frame ingest；1k event/s 持续 60s、5k/s
   10s burst；零丢失，receipt→durable p99≤250ms，book current p99≤750ms，DB pool 使用不超过预算。

## 7. 验收命令

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests alembic runtimes
.venv/bin/pytest -q tests/trading/contract/test_v2_gamma_contract.py \
  tests/trading/contract/test_v2_clob_public_contract.py \
  tests/trading/contract/test_v2_market_ws_contract.py
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/pytest -q tests/trading/integration/test_v2_0010_market_master_migration.py \
  tests/trading/integration/test_v2_0011_market_stream_migration.py \
  tests/trading/integration/test_v2_universe_ingest.py \
  tests/trading/integration/test_v2_book_resync.py
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/pytest -q tests/trading/replay/test_v2_public_market_replay.py
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
.venv/bin/alembic heads
.venv/bin/alembic upgrade b1000011 --sql > /tmp/wp01b.sql
git diff --check
```

真 PostgreSQL/Redis 0 skip；HTTP/WS 使用 deterministic wire server/fixture。性能结果记录硬件、数据
seed、commit、进程/连接配置与 p50/p95/p99，不以 mock 或平均值替代。

## 8. 完成、blocker、非目标与回滚

- 完成时只写 `serve/docs/manifests/wp-01b-public-market-data.md`，记录 23 个生产文件、16 表矩阵、
  official fixture provenance、frame/epoch 状态机、断线重放、性能结果、命令真实输出、blocker、
  rollback 和 manifest SHA；状态为 `DONE（待审）`，不提交、不推送。
- Blocker：官方 wire 无法由 fixture唯一表达、真 PG/Redis/Artifact 不可用、cursor completeness 或
  WS cutover 无法证明时标 `BLOCKED`；不得以 partial success、SQLite 或 sleep-only test 代替。
- 非目标：不接私有 CLOB/Data API/钱包/Polygon；不做 contract/payout/component/cohort；不调用 AI；
  不计算概率/edge/仓位；不下单；不做 Admin UI；不改 V1/Base 通用 CRUD。
- 回滚：先停止 market ingest，downgrade `b1000002`（先 0011 后 0010）删除本里程碑 trading 对象；
  public/foundation 不变。若存在下游 pin/未知对象则 fail-closed，改走 roll-forward，不手工删事实。
