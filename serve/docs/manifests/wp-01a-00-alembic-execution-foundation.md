# COMPLETION MANIFEST — WP-01A-00 · Alembic 执行基础

- Work package: `WP-01A` 子任务 `WP-01A-00`
- 状态: **DONE（待审）**
- 日期: 2026-08-09
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00d2-r2` 已接受（ACCEPTED）；WP-00 完成。`cdabba1e3903` no-op baseline 未改写、未重签。
- 规范依据: `serve/docs/tasks/wp-01a-00-alembic-execution-foundation.md`；`serve/docs/v2-implementation-contract.md` §3–§4/§11–§13；`serve/docs/performance-cache-database-design.md` §13
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/alembic/env.py` | 修改 | 唯一生产文件：共同 configure（include_schemas/type/default compare、version_table_schema、transaction_per_migration=False）；`include_name`/`include_object` 纯函数 allowlist（trading 全域、public 仅 metadata 表 + alembic_version）；online 复用注入连接/自建 NullPool engine；外层单事务内 SET LOCAL search_path → lock_timeout → `pg_advisory_xact_lock(5786375870084826445)` → run_migrations；dialect 非 PG 以固定 reason code 终止；offline 输出含 search path/transaction/advisory-lock SQL |
| `serve/alembic/README` | 修改 | 增补「V2 执行语义」小节（allowlist、search_path、advisory lock、连接所有权、真 PG 集成 env） |
| `serve/tests/trading/test_v2_alembic_env.py` | 新增 | 14 纯单测：共同 configure、allowlist、连接所有权、SQL 顺序/回滚、fail-closed + secret 计数 0 |
| `serve/tests/trading/integration/__init__.py` | 新增 | integration 测试包标记 |
| `serve/tests/trading/integration/conftest.py` | 新增 | `temp_pg_db` fixture：生成唯一 `pm_v2_test_*` 临时库，finally 断残留连接并删库（前缀+非 template 双重校验 fail-closed）；无 `V2_TEST_ADMIN_DATABASE_URL` 时整模块 skip |
| `serve/tests/trading/integration/test_v2_alembic_env_integration.py` | 新增 | 3 真 PostgreSQL 集成：upgrade→downgrade→upgrade、两进程并发 advisory lock 串行化、故意失败 revision 整体回滚 |
| `serve/docs/manifests/wp-01a-00-alembic-execution-foundation.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` / `serve/docs/tasks/README.md` | 修改 | WP-01A-00 标 DONE（待审） |

范围外未动：`app/config.py`、`app/models/`（无 trading 包）、legacy DB/Redis/Artifact/observability、
`app/main.py`、revision 文件（`cdabba1e3903` 未改写未重签）、`alembic.ini`、`requirements.txt`、
V1。**未遇 `BLOCKED_CONTRACT`**：不创建 revision / trading schema / UoW / Outbox / 业务表。

---

## 2. 实现内容（§5 精确合同）

### 2.1 Online / offline 共同配置（§5.1）

`COMMON_CONFIGURE_KWARGS` 固定并在两模式逐键一致：

```text
target_metadata=Base.metadata
include_schemas=True
compare_type=True
compare_server_default=True
version_table="alembic_version"
version_table_schema="public"
transaction_per_migration=False
```

`include_name`/`include_object` 为纯函数 allowlist：schema 仅 `trading`/`public`；table 在
`trading` 全域放行、`public` 仅放行 `Base.metadata` 已声明表与 `alembic_version`，其他 schema /
未知 public 表忽略；column/index/constraint/type 不再过滤。

### 2.2 Online 事务、锁与连接所有权（§5.2）

- 优先复用 `config.attributes["connection"]` 注入的既有 sync Connection（不 close / 不
  dispose）；未注入才从 typed settings 构造 `NullPool` engine，成功/异常路径 finally dispose
  各一次。
- 硬预置：dialect 必须为 PostgreSQL，否则以固定 reason code
  `v2_migration_requires_postgresql_dialect` 终止；异常不记录 DSN/password/Provider message。
- 外层单事务内顺序：`SET LOCAL search_path TO public, pg_catalog` → `SET LOCAL lock_timeout
  TO '30s'` → `SELECT pg_advisory_xact_lock(5786375870084826445)` → `run_migrations()`。先 begin
  再 configure 使 Alembic `_in_external_transaction=True` 并入外层事务；任何 migration 异常
  保持原类型传播并整体 rollback。密码只用于 engine URL。

### 2.3 Offline（§5.3）

configure(`url`, `literal_binds=True`, `dialect_opts={"paramstyle":"named"}`, **COMMON) 后先做
dialect fail-closed 校验，再在 `begin_transaction()` 内输出 search path / lock_timeout /
advisory-lock SQL 后 `run_migrations()`——生成 SQL 含 transaction boundary 与 advisory-lock
SQL（不实际获锁），password 不写入生成 SQL。

### 2.4 真 PostgreSQL 集成（§6.2）

fixture 在 `V2_TEST_ADMIN_DATABASE_URL` 显式测试管理连接下生成唯一 `pm_v2_test_<hex>` 临时库，
finally 断开残留连接并删库（删除前对名称前缀与 `datistemplate` 双重校验，失败 fail-closed）；
无 env 时整模块 skip。覆盖 upgrade→downgrade→upgrade、两独立进程并发 advisory lock 串行化、
故意失败 revision 整体回滚（临时 script location，不改仓库 revision 文件）。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests alembic
# → exit 0

# 2) 纯单测
.venv/bin/pytest -q tests/trading/test_v2_alembic_env.py
# → 14 passed in 0.22s

# 3) 真 PostgreSQL 集成（显式传 env，0 skip）
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/pytest -q tests/trading/integration/test_v2_alembic_env_integration.py
# → 3 passed in 0.92s（PostgreSQL 18.4；-W error::sqlalchemy.exc.SAWarning 下亦 3 passed）

# 4) tests/trading
.venv/bin/pytest -q tests/trading
# → 683 passed, 3 skipped（无 env 时集成整模块 skip）

# 5) 全量回归（显式传 env，0 skip）
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
# → 897 passed, 9 warnings in 5.57s（9 为既有 pytest-asyncio/FastAPI 弃用告警）

# 6) git diff --check
git diff --check
# → 无输出，exit 0
```

---

## 4. 关键证据

### 4.1 纯单测（14，任务 §6.1 五项全关）

- 共同 configure：online/offline 的 `COMMON_CONFIGURE_KWARGS` 子集逐键相等；online 仅多
  `connection`、offline 仅多 `url`/`literal_binds`/`dialect_opts`。
- allowlist：`trading` 全域、`public` 已知 metadata 表 + `alembic_version` 保留；未知 public
  表、其他 schema（含 `pg_catalog`）排除；column/index 放行；`include_object` 与 `include_name`
  对 table 判定一致。
- 连接所有权：注入 connection 不被 close/dispose、自建 engine 路径不触发；自建 engine 成功与
  异常路径各 dispose 一次。
- SQL 顺序：online 为 begin → search_path → lock_timeout → advisory lock → migration；
  migration 抛异常整体 rollback、原异常传播（不吞测试 assertion）。
- fail-closed：online（SQLite dialect）与 offline（sqlite URL 注入）均以固定 reason code 终止；
  日志、exception、offline SQL 中 secret marker 计数为 0；`_build_sync_url` 对密码做 URL 编码。

### 4.2 真 PostgreSQL 集成（3，任务 §6.2 四项全关）

- `test_upgrade_downgrade_upgrade_roundtrip`：空临时库 `upgrade head` →
  `public.alembic_version` 单行基线、无其他 version table、用户表仅 `alembic_version` →
  `downgrade base` 版本行 0 → `upgrade head` 恢复单行基线。
- `test_concurrent_upgrades_serialized_by_advisory_lock`：两独立进程（文件屏障同时放行）各自
  `upgrade head` → 双进程 0 退出、单行版本、`pg_locks` 中 advisory 锁计数 0（无双执行、无残留锁）。
- `test_failing_revision_rolls_back_and_version_not_advanced`：临时 script location 注入故意
  失败 revision（先 `CREATE TABLE partial_created_table` 后抛 `intentional-failure`）→
  原异常传播、version 仍为基线、`partial_created_table` 不存在（整体回滚）。

### 4.3 回归

880 全量（WP-00 已接受基线）+ 14 单测 + 3 集成 = **897 passed、9 既有弃用告警、0 skip、
`git diff --check` 干净**；`pm_v2_test_*` 临时库零残留。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞（如实记录）：
- 真 PG 集成在无 `V2_TEST_ADMIN_DATABASE_URL` 时整模块 skip（设计如此；验收命令显式传 env 0 skip）。
- 本任务不创建 `trading` schema、revision、UoW/Outbox（非目标，`v2_0001_align_base_metadata`
  在本任务接受后才可创建）。
- `alembic.context` proxy 为进程级共享状态，并发 migration 只能以独立进程呈现（子进程集成已覆盖），
  不支持同进程多线程并发跑 env.py。

---

## 6. 回滚方式

```bash
git checkout -- serve/alembic/env.py serve/alembic/README \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -rf serve/tests/trading/integration
git rm -f serve/tests/trading/test_v2_alembic_env.py \
  serve/docs/manifests/wp-01a-00-alembic-execution-foundation.md
```

回到 WP-00d2-r2 交付状态；未改 revision、模型、config、legacy 迁移系统；无迁移/网络/Provider/
业务数据副作用；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-01a-00-alembic-execution-foundation.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
48b32065940cdc9bba0a2ac3ebc4e520e9fbae2674e7ce2537ce7843eb374af5
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-01a-00-alembic-execution-foundation.md | sha256sum
```
