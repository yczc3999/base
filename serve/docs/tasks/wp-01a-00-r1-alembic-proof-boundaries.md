# WP-01A-00-r1 — Alembic allowlist、事务所有权与验收证明整改

> 状态：**ACCEPTED**（审查者直接整改）。完成 manifest：
> `serve/docs/manifests/wp-01a-00-r1-alembic-proof-boundaries.md`。
> 最后更新：2026-08-09 07:29 EDT。

## 1. 目标与用户价值

关闭 `WP-01A-00` 交付后复验发现的五个 P1，使“schema allowlist、迁移单事务、
并发锁”从有代码/有测试升级为**真正生效且可被反例证明**的执行合同。

## 2. 整改决策

1. `include_name/include_object` 必须实际传入 online/offline `context.configure`。
   Alembic 传入的 default schema `None` 必须映射为 PostgreSQL `public`。
2. 注入 connection 已有活动事务时，以 `v2_migration_requires_clean_connection`
   fail-closed；禁止 commit/rollback 调用方事务。
3. 临时库 URL 只替换 admin URL 的 database，必须保留 driver/user/password/host/port/query。
4. 并发验收必须运行一个含 `pg_sleep` 的真 revision，两个进程都成功且 DDL 只
   执行一次；禁止用“已在 head 的两次 no-op”冒充串行化。
5. 整 run rollback 验收必须先成功一个 revision，再在后续 revision 失败；前者 DDL 与
   version 推进也必须回滚。

## 3. 范围、证据与回滚

允许修改：`serve/alembic/env.py`、`serve/alembic/README`、
`serve/tests/trading/test_v2_alembic_env.py`、`serve/tests/trading/integration/conftest.py`、
`serve/tests/trading/integration/test_v2_alembic_env_integration.py` 及本 task/manifest/索引。

验收：15 unit、3 个真 PostgreSQL integration、687 trading、898 full，9 个既有弃用告警，
`compileall`/`git diff --check` 通过。两个独立审查结论中的 P1 已全部复现并关闭。

非目标：不创建 revision/trading schema，不改 Base model/config/runtime/UoW/Outbox。
回滚仅撤销上述五个实现/测试文件的 R1 差异；测试库已全部删除，无业务数据副作用。
