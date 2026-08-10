# COMPLETION MANIFEST — WP-01A-02 · Trading foundation

- Work package: `WP-01A-02`
- 状态: **ACCEPTED（审查者已直接整改并复验）**
- 日期: 2026-08-10 EDT
- 实现模型: DeepSeek V4 Flash；最终审查/整改: Codex
- 任务合同: `serve/docs/tasks/wp-01a-02-trading-foundation.md`
- Revision: `b1000002`，down revision `b1000001`

## 1. 交付范围

本里程碑交付了合同规定的 19 个生产文件：

- `app/models/trading/{constants,types,mixins,artifact,control,vault,outbox,__init__}.py`
- `app/models/__init__.py`
- `alembic/versions/b1000002_v2_0002_trading_foundation.py`
- `app/db/{__init__,uow}.py`
- `app/outbox/{__init__,contracts,repository,publisher,consumer,sweeper}.py`
- `app/services/redis_control.py`

并交付对应 model、migration、UoW、Outbox、Redis 单元/真 PostgreSQL/真 Redis 测试。为适配新
Alembic head，最小更新了既有 `0001`/Alembic integration 断言与共享迁移 fixture；未改变
Base public schema、V1、业务路由或运行时。

## 2. 已实现的不变量

### 2.1 ORM 与 20 张 foundation 表

- `trading` 为唯一物理 schema；四个类型工厂、BIGINT identity、UTC 时间、base-unit 数值与
  SQLAlchemy functional optimistic locking 已落地。
- 恰好创建合同列出的 20 张表；Artifact ORM 与现有 `ArtifactRef` 在 `mime`、`none|zstd`、
  `cas/v1`、canonical locator 和 raw-size 语义上无损一致。
- capital mode 固定为 `shadow|canary|live`；Vault algorithm 只允许 `aes-256-gcm`，无明文字段。
- artifact/lineage、vault version/access event、idempotency claim、delivery history、job completion
  由 DB trigger 强制 append-only；vault entry 只允许 `active→disabled`；已发布 control、manifest、
  binding、policy freeze 只能走规定生命周期，内容不可覆盖。

### 2.2 Migration

- `b1000002` 使用 revision 内的**固定 DDL 快照**，不导入 live ORM metadata；未来 model 变更不会
  重写历史 migration。
- upgrade 创建 20 表、当月及后两月 history 分区和 immutability guards；月份按 UTC 计算。
- downgrade 逆序、无 CASCADE；发现未知 trading 对象时整个 run 回滚，20 表与 version 均保持。
- literal-empty 与 existing-Base 均完成 `0001→0002→0001→0002` 真 PostgreSQL roundtrip；
  public Base shape/data/sequence 不变，metadata drift 仅允许动态分区投影。

### 2.3 UoW 与可靠 Outbox

- UoW 单次使用，唯一 commit/rollback/close；失败 hook 不伪装 rollback，取消异常不被吞，失效
  hook 不跨事务泄漏。
- Envelope 严格校验 event hash、aware datetime、priority、release、payload/artifact XOR、JSON、
  float 与 secret；调度时间不污染内容身份。
- enqueue 以 `idempotency_claims` 原子认领；同 key 不同内容拒绝，精确重试 no-op。
- Publisher 在短事务 claim 后才访问 Redis；Consumer 使用稳定 `handler_name`、DB advisory-xact
  lock 与 job completion 唯一键，使 handler 的 DB effect、completion、history、terminal transition
  同一事务提交；Redis lease 仅作削峰，不作完成证据。
- Consumer 以 PostgreSQL status/`available_at` 为权威，拒绝错 stream，未来 backoff/terminal 的旧
  delivery 只 ACK 不执行；污染或 malformed Redis 消息只能隔离/ACK，不能把正常 DB 事实置 DEAD。
- Sweeper 能处理 visibility/deadline/max-attempt；Redis group 只把 BUSYGROUP 视为幂等，其余错误
  fail-closed。

## 3. 审查中直接修复的问题

审查未建立 R1/R2 链，已在本里程碑内直接修复并补回归：

1. ArtifactRef/ORM 字段和 CHECK 不兼容；2. optimistic lock 绑定到字符串而非 mapped column；
3. migration 从 live metadata 动态建表；4. published/append-only 事实缺 DB 边界；5. mode/加密算法
错误；6. UoW 可复用及 hook 生命周期；7. envelope/hash/secret 校验不完整；8. Outbox 幂等仅依赖
进程身份且 handler effect 与 completion 非原子；9. deadline/sweeper/Redis BUSYGROUP 边界；
10. malformed/错路由/旧 Redis delivery 可破坏事实源或绕过 backoff。

## 4. 可复现验收证据

在 `/code/pollymarket/v2/serve` 执行：

```bash
python3 -m compileall -q app tests alembic
# exit 0

.venv/bin/pytest -q tests/trading/test_v2_model_imports.py \
  tests/trading/test_v2_trading_model_primitives.py \
  tests/trading/test_v2_trading_foundation_models.py
# 35 passed

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/pytest -q tests/trading/integration/test_v2_0002_trading_foundation_migration.py
# 7 passed

.venv/bin/pytest -q tests/trading/test_v2_outbox_contracts.py \
  tests/trading/test_v2_outbox_runtime.py tests/trading/test_v2_redis_control.py
# 48 passed

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/pytest -q tests/trading/integration/test_v2_uow.py \
  tests/trading/integration/test_v2_outbox_repository.py \
  tests/trading/integration/test_v2_outbox_recovery.py
# 29 passed

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/pytest -q tests/trading
# 833 passed, 9 warnings in 27.04s

V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
# 1044 passed, 9 warnings in 29.01s

.venv/bin/alembic heads
# b1000002 (head)

.venv/bin/alembic upgrade b1000002 --sql > /tmp/wp01a02.sql
# 899 lines; credential markers=0

psql -d postgres -Atc "SELECT count(*) FROM pg_database WHERE datname LIKE 'pm\\_v2\\_%'"
# 0

git diff --check
# exit 0
```

9 个 warning 均为既有 pytest-asyncio/FastAPI/Starlette 弃用告警，无 RuntimeWarning、skip 或 flaky
重跑豁免。

## 5. Blocker、非目标与回滚

- 未解决 P0/P1: **0**。
- 非目标保持：不实现 KMS/Vault 解密，不接 Polymarket/AI，不建 contract/forecast/decision/
  execution/ledger，不启动 runtime，不改 Admin UI/V1。
- 数据库回滚：在隔离/批准环境执行 `alembic downgrade b1000001`；它只删除本 revision 的
  trading foundation。未知 trading 对象会阻止 downgrade 并回滚，public 无恢复步骤。
- 代码回滚：revert 本里程碑提交；测试数据仅存在已清理的 `pm_v2_test_*` 临时库。

## 6. Manifest SHA-256

口径：删除本文件中“恰好 64 位十六进制”的哈希行后计算。

```text
b5aeeffbd87a16373d91557a24d4501d91453e79572a372578123c3921305aa8
```

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-01a-02-trading-foundation.md | sha256sum
```
