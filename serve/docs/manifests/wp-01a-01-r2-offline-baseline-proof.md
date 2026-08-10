# COMPLETION MANIFEST — WP-01A-01-r2 · Offline baseline 失败证明

- 状态：**DONE，独立审查已接受**
- 日期：2026-08-10
- 执行：Codex 审查者直接整改
- 规范：`serve/docs/tasks/wp-01a-01-r2-offline-baseline-proof.md`

## 1. 修改文件与实现

| 文件 | 实现 |
|---|---|
| `serve/tests/trading/integration/test_v2_0001_base_schema_contract.py` | offline SQL helper 支持 revision range；三组 swapped-PK 和 canonical 路径从真实 baseline 应用 `cdabba1e3903:head` |
| `serve/tests/trading/fixtures/base_legacy_schema.sql` | 删除 EOF 多余空行，规范为单个 LF；DDL/schema 语义不变 |
| `serve/tests/trading/fixtures/base_legacy_schema_manifest.json` | 更新规范化后 fixture SHA |

R1 的 `base_schema_contract.py` ordinal 修复不变。原 WP-01A-01 / R1 manifest 均未改写。

## 2. 关键证据

- 三组 `admin_user_roles / article_keywords / role_menus` 反序主键：online 与 offline range
  均以 `v2_base_schema_incompatible` 失败。
- 每个 offline 失败后 `public.alembic_version = cdabba1e3903`，18 张 Base 表与 version 表均在，
  无部分 DDL。
- canonical fixture 从 baseline 应用同一 range 成功到 `b1000001`。
- fixture 当前 SHA 为 `ea9036cea18c7b1f8bbf5aa5b6a3d30b0c1d00492eaae65a25ae4419c600a5ec`；
  canonical schema SHA 仍为 `17a47a83548c2f6e4c5c7fa48ae80ceb32d1c3274392b0d3bb58c8aecbbc008a`。

```text
python3 -m compileall -q app tests alembic                 -> exit 0
pytest -q tests/trading/test_v2_base_schema_contract.py   -> 27 passed
V2_TEST_ADMIN_DATABASE_URL=... pytest -q tests/trading/integration/
  test_v2_0001_base_schema_contract.py                    -> 13 passed
V2_TEST_ADMIN_DATABASE_URL=... pytest -q tests/trading    -> 727 passed, 8 warnings
V2_TEST_ADMIN_DATABASE_URL=... pytest -q                  -> 938 passed, 9 warnings
git diff --check                                           -> exit 0
```

## 3. Blocker 与回滚

Blocker：无。回滚仅恢复 integration test 的原 helper/前置状态；无生产代码、revision 或数据
副作用。

## 4. Manifest SHA-256

口径：删除本文件中“恰好为 64 位小写十六进制”的哈希行后计算。

```text
57991d05853f5c1f752eeabc68aaf0854869e54d0f015b0c3272f781f49fb8bd
```
