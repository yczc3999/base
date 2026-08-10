# WP-01A-01-r1 — Offline composite-PK 顺序等价性整改

> 状态：**READY**。执行模型：DeepSeek V4 Flash。
> 完成 manifest 固定为 `serve/docs/manifests/wp-01a-01-r1-offline-pk-order.md`。
> 最后更新：2026-08-10 EDT。

## 1. 目标与用户价值

关闭 `WP-01A-01` 审查中复现的一个 P1：online validator 会拒绝复合主键列顺序错误，
但 offline precondition 只比较 `(table, column)` 集合，错误顺序仍能升级到 `b1000001`。
整改后，online migration 与 `alembic upgrade --sql` 应用路径对同一 Base schema 必须给出
完全相同的兼容结论。

## 2. 已确认事实与修复决策

1. 复现：将 `public.admin_user_roles` 主键从
   `(admin_user_id, role_id)` 改为 `(role_id, admin_user_id)` 后，生成并应用 offline SQL，
   当前实现返回成功且版本前进到 `b1000001`。
2. 根因：`offline_precondition_sql()` 的 PK 双向 `EXCEPT` 仅比较 `(tbl,col)`，丢失
   `WITH ORDINALITY` 的 `ord`；online `extract_signature()` 则保留 tuple 顺序。
3. 修复必须让 canonical PK rows 与 catalog PK rows都包含 ordinal，并比较
   `(table, column, ordinal)`；不得通过排序列名掩盖顺序。
4. 固定失败 reason 仍为 `v2_base_schema_incompatible`；失败时 version 停在 baseline，
   整个 offline SQL transaction 回滚。

## 3. 精确文件范围

```text
serve/app/base_schema_contract.py
serve/tests/trading/test_v2_base_schema_contract.py
serve/tests/trading/integration/test_v2_0001_base_schema_contract.py
serve/docs/manifests/wp-01a-01-r1-offline-pk-order.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 revision、fixture、fixture manifest、Alembic env/README、implementation contract、
Base models 或其他文件。原 `WP-01A-01` manifest 已冻结，不得改写。

## 4. 实现与必测合同

### 4.1 实现

- `offline_precondition_sql()` 为每张表的 canonical PK 列生成从 1 开始的稳定 ordinal。
- catalog 侧使用 `unnest(i.indkey) WITH ORDINALITY` 的 `ord`，双向 `EXCEPT` 比较三元组。
- 保持 SQL 可 offline 渲染、无运行时文件读取、无 Base DDL、无 secret/DSN。
- 不改变 online `CANONICAL_SIGNATURE`、validator 的 EMPTY/COMPATIBLE/partial 语义。

### 4.2 单测

1. 生成 SQL 明确比较 PK ordinal，不只是列集合。
2. canonical 复合 PK 的 ordinal 为 1、2；单列 PK 为 1。
3. 原 fixture/contract/revision SHA 规则仍可复算；仅 contract 文件 SHA 因本修复更新并记录。

### 4.3 真 PostgreSQL

1. 至少参数化覆盖 `admin_user_roles`、`article_keywords`、`role_menus` 三个复合主键：
   交换列顺序后，online upgrade 与实际应用 offline SQL 都以
   `v2_base_schema_incompatible` 失败。
2. 两种路径均保持 Alembic version 在 `cdabba1e3903`，不得留下部分 DDL。
3. canonical fixture 的 online/offline upgrade 仍成功到 `b1000001`。
4. literal-empty、partial、wrong-type 既有路径不得回归；临时库零残留。

## 5. 验收命令与交付

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests alembic
.venv/bin/pytest -q tests/trading/test_v2_base_schema_contract.py
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/pytest -q tests/trading/integration/test_v2_0001_base_schema_contract.py
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
git diff --check
```

manifest 必须记录三组反序 PK 的 online/offline 真实结果、version/rollback、canonical 成功路径、
全量回归、临时库零残留、修改后 contract SHA 和自身 SHA。完成时只标 `DONE（待审）`，
不提交、不推送。

## 6. Blocker、非目标与回滚

- Blocker：无法运行真 PostgreSQL 或无法实际执行生成的 offline SQL 时标 `BLOCKED`；不得用
  字符串断言代替数据库验收。
- 非目标：不扩展 Base 签名范围，不检查 default/index/CHECK/FK，不创建 `trading/0002`，
  不重构 1220 行 canonical 常量，不处理新功能。
- 回滚：仅回退 `offline_precondition_sql()` 的 ordinal 差异及新增测试/交付文档；不涉及
  Base/V2 数据恢复。
