# COMPLETION MANIFEST — WP-01A-00-r1 · Alembic 证明边界整改

- 状态：**DONE，独立审查已接受**
- 日期：2026-08-09
- 执行：Codex 审查者直接整改
- 规范：`serve/docs/tasks/wp-01a-00-r1-alembic-proof-boundaries.md`

## 1. 修改文件与实现

| 文件 | 实现 |
|---|---|
| `serve/alembic/env.py` | 将 allowlist hooks 真正传入 configure；`None` default schema 按 public；活动外部事务 fail-closed，不夺取 commit/rollback 所有权 |
| `serve/alembic/README` | 同步 default-schema、callback 生效与 clean injected connection 合同 |
| `serve/tests/trading/test_v2_alembic_env.py` | 新增 hook 实际注入、`None` schema 及外部事务不被提交/回滚证据 |
| `serve/tests/trading/integration/conftest.py` | 从 admin URL 仅替换 database 派生目标 URL；fixture 改为每测试独立库；terminate query 参数化 |
| `serve/tests/trading/integration/test_v2_alembic_env_integration.py` | 并发改为含稳定重叠窗口的真 revision；rollback 改为“先成功 revision，后失败 revision”；校验 URL origin 保留 |

## 2. 命令与真实结果

```text
python3 -m compileall -q app tests alembic                 -> exit 0
pytest -q tests/trading/test_v2_alembic_env.py             -> 15 passed
V2_TEST_ADMIN_DATABASE_URL=postgresql+psycopg:///postgres
  pytest -q tests/trading/integration/test_v2_alembic_env_integration.py
                                                            -> 3 passed in 2.00s, 0 skip
V2_TEST_ADMIN_DATABASE_URL=... pytest -q tests/trading     -> 687 passed, 8 warnings
V2_TEST_ADMIN_DATABASE_URL=... pytest -q                   -> 898 passed, 9 warnings
git diff --check                                           -> exit 0
```

## 3. 关键验收证据

- `context.configure` 的 online/offline kwargs 均包含各自模块的 `include_name` 和
  `include_object`；`schema=None` 与 `parent.schema_name=None` 正确按 public 过滤。
- 注入 connection 已有 transaction 时，固定 reason code 抛错；外部 transaction 仍 active，
  无 commit/rollback/SQL 副作用。
- 每个真 PG 测试使用独立 `pm_v2_test_*` 库；target URL 的 driver/user/password/
  host/port/query 与 admin URL 一致，仅 database 变化；测试后临时库零残留。
- 并发两进程对 baseline 之后的非 no-op revision 同时 upgrade；两者均成功，
  version 只前进一次，probe 表只有一行，同 advisory key 随后可重新获取。
- 同一 upgrade run 的第一 revision 成功建表，第二 revision 建表后抛错；最终两表
  均不存在、version 仍为 baseline，证明整 run 回滚。

## 4. Blocker、回滚与副作用

Blocker：无。回滚：只回滚§1五个实现/测试文件的 R1 差异。无 revision、业务表、
Provider 或业务数据副作用。

## 5. Manifest SHA-256

口径：删除本文件中“恰好为 64 位小写十六进制”的哈希行后计算。

```text
88091118c83be3b3305be84cc26d771e25f07067f80d17592683ac436c01a3bb
```
