# WP-01A-00 — Alembic 执行基础

> 状态：**READY**。执行模型：DeepSeek V4 Flash。
> 完成 manifest 固定为 `serve/docs/manifests/wp-01a-00-alembic-execution-foundation.md`。
> 最后更新：2026-08-09 05:32 EDT。

## 1. 目标与用户价值

在任何 V2 DDL 之前，先把 Alembic 变成可串行、可回滚、schema-aware、不泄密的迁移执行器。
这一步不建表；它防止之后 `0001/0002` 在错误 schema、并发 migration 或伪 SQLite
测试下施工。

## 2. 已确认决策

1. Alembic 是 V2 DDL 的唯一入口；legacy `python -m app.migrate` 仍只负责 Base 旧基线和
   菜单/种子，本任务不调用它。
2. 现有 `cdabba1e3903` no-op baseline 不改写、不重签。`alembic_version` 继续固定在
   `public`。
3. V2 模型只管理 `trading` schema；Base metadata 只管理已声明的 `public` 表。未知
   public 表不得被 autogenerate 判为应删除。
4. 显式 `search_path=public,pg_catalog`；V2 表必须 schema-qualified，禁止依赖隐式搜索顺序。
5. 迁移锁使用 PostgreSQL transaction advisory lock，固定 key
   `5786375870084826445`；一次 Alembic run 使用一个外层事务，失败整体回滚。
6. “字面空库”对后续 revision 的工程语义已固定：`0001` 在 Base 旧表不存在时按预置条件
   no-op，`0002` 仍可创建 V2 `trading` 基础；这不代表 Base 应用已完成新库引导。
   新环境启动 Base 仍需先跑 legacy bootstrap。

## 3. 依赖与必读

- 依赖：`WP-00d2-r2` 已接受，WP-00 完成。
- 必读：`serve/docs/v2-implementation-contract.md` §3–§4、§11–§13；
  `serve/docs/performance-cache-database-design.md` §13；`serve/alembic/README`。
- 真 PostgreSQL 验收使用显式测试管理连接：
  `V2_TEST_ADMIN_DATABASE_URL=postgresql+psycopg:///postgres`。fixture 只能创建/删除
  `pm_v2_test_*` 临时库，禁止在管理库或业务库直接运行 downgrade。

## 4. 精确文件范围

```text
serve/alembic/env.py
serve/alembic/README
serve/tests/trading/test_v2_alembic_env.py
serve/tests/trading/integration/__init__.py
serve/tests/trading/integration/conftest.py
serve/tests/trading/integration/test_v2_alembic_env_integration.py
serve/docs/manifests/wp-01a-00-alembic-execution-foundation.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改其他文件。本次生产文件只有 `alembic/env.py`；README/测试/交付文档不计入
DeepSeek 的 8 文件上限。

## 5. 实现合同

### 5.1 Online / offline 共同配置

`context.configure` 必须在两种模式同时启用：

```text
target_metadata=Base.metadata
include_schemas=True
compare_type=True
compare_server_default=True
version_table="alembic_version"
version_table_schema="public"
transaction_per_migration=False
```

实现稳定 `include_name/include_object` allowlist：允许 `trading` schema 全域；`public` 仅允许
`Base.metadata` 已声明表与 `alembic_version`；忽略其他 schema/未知 public 表。过滤逻辑是纯函数，
可直接单测。

### 5.2 Online 事务、锁与连接所有权

- 优先复用 `config.attributes["connection"]` 注入的既有 sync Connection，不关闭/不 dispose
  调用方连接；未注入时才从 typed settings 构造 `NullPool` engine 并在 finally dispose。
- 硬预置：dialect 必须为 PostgreSQL，否则用固定 reason code 终止；异常不记录
  DSN/password/Provider message。
- 在外层 transaction 内依次执行 `SET LOCAL search_path TO public, pg_catalog`、
  `SET LOCAL lock_timeout TO '30s'`、
  `SELECT pg_advisory_xact_lock(5786375870084826445)`，然后才 `run_migrations()`。
- 任何 migration 异常保持原类型传播并整体 rollback；不吞测试 assertion。密码只用于
  engine URL，禁止写 log/exception/manifest。

### 5.3 Offline

Offline SQL 不连网、不实际获锁，但输出必须包含显式 search path、transaction boundary 和
advisory-lock SQL，保持与 online 语义一致。不得把含密码的 URL 写入生成 SQL。

## 6. 必测证据

### 纯单测

1. online/offline 的共同 `context.configure` 参数完全一致。
2. schema/table allowlist：未知 public 表、非 `public|trading` schema 均被排除；metadata
   public 表、trading 表和 version table 保留。
3. 注入 connection 不被 close/dispose；自建 engine 在成功/异常路径各 dispose 一次。
4. SQL 顺序为 begin → search path/timeout → advisory lock → migration。migration 失败后 rollback，
   原异常传播。
5. SQLite/错误 dialect fail-closed；日志、exception、offline SQL 中 secret marker 计数为 0。

### 真 PostgreSQL 集成

1. fixture 生成唯一 `pm_v2_test_*` 库，finally 断开残留连接并删库。无 env 时普通全量
   suite 可 skip；**manifest 验收命令必须显式传 env 且 0 skip**。
2. 临时库执行 `upgrade head → downgrade base → upgrade head`；每步 revision 正确，
   `public.alembic_version` 且无其他 version table。
3. 两个并发 migration 在同一临时库中被 advisory lock 串行化，无双执行/无残留锁。
4. 故意 revision 异常 fixture 整体回滚且 version 不前进；fixture 不得改仓库 revision
   文件，使用临时 script location 或 connection-level 注入完成。

## 7. 验收命令与交付

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests
.venv/bin/pytest -q tests/trading/test_v2_alembic_env.py
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/pytest -q tests/trading/integration/test_v2_alembic_env_integration.py
.venv/bin/pytest -q tests/trading
.venv/bin/pytest -q
git diff --check
```

manifest 必须记录：修改文件、online/offline 配置证据、真 PostgreSQL 临时库名（不记连接串）、
迁移/并发/回滚真实结果、全量回归、blocker、回滚和 SHA。完成时两个索引标
`DONE（待审）`；不提交、不推送。

## 8. Blocker、非目标、风险/回滚

- 当前 blocker：无。若本机缺少可创库的 PostgreSQL 账号，交付状态必须 `BLOCKED`，
  不得用 SQLite/mock/skip 冒充集成验收。
- 非目标：不创建 revision；不改 Base model/legacy SQL runner/config/runtime；不建 `trading`
  schema；不实现 `0001/0002`、UoW、Outbox、业务表或种子。
- 风险：测试管理连接具有 `CREATE DATABASE`权限。fixture 必须对名称前缀做二次校验，
  删除前确认不是 template/current/admin 库；任何检查失败时 fail-closed。
- 回滚：回退 `alembic/env.py` 与本任务的测试/README 差异。本任务不改 revision 与业务数据。
