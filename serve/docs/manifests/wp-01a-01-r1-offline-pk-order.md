# COMPLETION MANIFEST — WP-01A-01-r1 · Offline composite-PK 顺序等价性

- Work package: `WP-01A` 子任务 `WP-01A-01-r1`（整改）
- 状态: **DONE（待审）**
- 日期: 2026-08-10
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-01A-01` 已交付但审查要求整改（1 个 P1：offline PK 校验丢失复合主键列顺序）
- 规范依据: `serve/docs/tasks/wp-01a-01-r1-offline-pk-order.md`
- 权威输入: 同 WP-01A-01（`backup_20260809_054832_532956.dump`，SHA
  `14e1913edbfed6654e3b0a1073c1e346140e00fe898a063305805954184b0d59`）
- 原 `WP-01A-01` manifest 已冻结，未改写
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/base_schema_contract.py` | 修改 | **唯一生产文件**：`offline_precondition_sql()` 的 PK 双向 `EXCEPT` 由二元组 `(tbl,col)` 升级为三元组 `(tbl,col,ord)`——canonical 侧 `enumerate(primary_key, start=1)` 生成 1-based ordinal，catalog 侧投影 `unnest(i.indkey) WITH ORDINALITY` 的 `k.ord::integer` |
| `serve/tests/trading/test_v2_base_schema_contract.py` | 修改 | 新增 2 单测：SQL 含 ordinal 投影（`AS e(tbl, col, ord)` ×2 / `k.ord::integer` ×2 / 无二元组残留）；canonical 复合 PK ordinal 1、2、单列 PK 1、无「反序列名伪三元组」 |
| `serve/tests/trading/integration/test_v2_0001_base_schema_contract.py` | 修改 | 新增 7 真 PG：三组反序复合主键（`admin_user_roles` / `article_keywords` / `role_menus`）online 与 offline 均 `v2_base_schema_incompatible` + offline canonical 成功路径 |
| `serve/docs/manifests/wp-01a-01-r1-offline-pk-order.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` / `serve/docs/tasks/README.md` | 修改 | WP-01A-01-r1 标 DONE（待审） |

未改（§3 禁止）：revision、fixture、fixture manifest、`alembic/env.py`、`alembic/README`、
implementation contract、Base models。原 WP-01A-01 manifest 未改写。范围外零改动。

---

## 2. 实现内容（§4.1）

根因（审查复现 + 本地复现一致）：`offline_precondition_sql()` 的 PK 校验仅比较
`(table, column)` 集合，`WITH ORDINALITY` 的 `ord` 存在但未投影——`admin_user_roles`
主键从 `(admin_user_id, role_id)` 改为 `(role_id, admin_user_id)` 后，online validator
会拒绝（`extract_signature` 保留 tuple 顺序），offline SQL 却误判兼容并升级到
`b1000001`。

修复让 online 与 offline 对同一 Base schema 给出完全相同的兼容结论：

- **canonical 侧**：`enumerate(sig["primary_key"], start=1)` 生成 `('tbl', 'col', ord)`
  三元组，ordinal 稳定 1-based，与 `extract_signature` 保留的 tuple 顺序一致。
- **catalog 侧**：`unnest(i.indkey) WITH ORDINALITY k(attnum, ord)` 的 `ord` 投影为
  `k.ord::integer`；双向 `EXCEPT` 比较三元组 `(tbl, col, ord)`——列集合相同但顺序不同
  时两侧各有一个方向差一行，`EXISTS` 为真 → `v2_base_schema_incompatible`。
- 不得通过排序列名掩盖顺序：`EXCEPT` 天然无序，三元组包含 ordinal 即保留顺序语义。
- SQL 仍可 offline 渲染、无运行时文件读取、无 Base DDL、无 secret/DSN。
- 未改变 online `CANONICAL_SIGNATURE`、validator 的 EMPTY/COMPATIBLE/partial 语义；
  失败 reason 仍为 `v2_base_schema_incompatible`。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests alembic
# → exit 0

# 2) 纯单测
.venv/bin/pytest -q tests/trading/test_v2_base_schema_contract.py
# → 27 passed in 0.10s（25 既有 + 2 新增 ordinal）

# 3) 真 PostgreSQL 集成（显式传 env，0 skip）
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/pytest -q tests/trading/integration/test_v2_0001_base_schema_contract.py
# → 13 passed in 6.82s（6 既有 + 3 online 反序 + 3 offline 反序 + 1 offline canonical 成功）

# 4) tests/trading（显式传 env）
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
# → 727 passed, 8 warnings

# 5) 全量回归（显式传 env，0 skip）
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
# → 938 passed, 9 warnings in 13.22s（9 为既有弃用告警）

# 6) git diff --check
git diff --check
# → 无输出，exit 0

# 7) 临时库零残留
psql -d postgres -Atc "SELECT datname FROM pg_database WHERE datname LIKE 'pm\_v2\_%'"
# → 空
```

---

## 4. 关键证据

### 4.1 P1 复现 → 修复前后对照

复现（审查者反例，本地复核一致）：`admin_user_roles` PK 反序为
`(role_id, admin_user_id)` 后，修复前应用离线 SQL **rc=0 且 version 前进到 `b1000001`**
（bug 确认）。修复后同一场景应用离线 SQL → **rc=3、stderr 含
`ERROR: v2_base_schema_incompatible`**、整个事务回滚（version 表不存在、18 表原样）。

### 4.2 三组反序复合主键（真 PG，参数化）

`SWAPPED_PK_CASES = [(admin_user_roles, admin_user_id, role_id),
(article_keywords, article_id, keyword_id), (role_menus, role_id, menu_id)]`

- **online**（3）：apply fixture → upgrade head（canonical COMPATIBLE）→ downgrade 基线 →
  反序 PK → `upgrade head` 抛 `BaseSchemaContractError(v2_base_schema_incompatible)`；
  version 停在 `cdabba1e3903`，表仍在（无部分 DDL）。
- **offline**（3）：apply fixture（无 version 表）→ 反序 PK → 生成并**实际应用**
  `upgrade head --sql` → psql 退出非 0、stderr 含 `v2_base_schema_incompatible`；
  整个 offline SQL 事务回滚（`to_regclass('public.alembic_version')` 为 NULL、18 表全在）。
- **canonical 成功路径**（1）：apply fixture → 应用 offline SQL → rc=0、version=`b1000001`、
  19 表（18 Base + alembic_version）——online/offline 对同一 Base schema 结论一致。

### 4.3 单测（2 新增）

- `test_offline_precondition_compares_pk_ordinal`：生成 SQL 含 `AS e(tbl, col, ord)`（×2）
  与 `k.ord::integer`（×2），不再含二元组 `AS e(tbl, col))`。
- `test_offline_precondition_pk_ordinals`：`('admin_user_roles','admin_user_id',1)` /
  `('admin_user_roles','role_id',2)` 等复合 PK ordinal 1、2；单列 PK（`admin_login_logs`、
  `admin_users`）ordinal 1；无 `('admin_user_roles','role_id',1)` 这类反序伪三元组。

### 4.4 不回归

- literal-empty、partial（DROP settings）、incompatible（错类型/错 nullability/缺列/错 PK
  单列顺序）既有路径全部保持；`alembic check` 无 drift。
- 929（WP-01A-01 基线）+ 2 单测 + 7 集成 = **938 passed、9 既有弃用告警、0 skip、
  `git diff --check` 干净、临时库零残留**。
- contract 模块 SHA 更新为 `559ce3e45d3b3d5a87ad34df6ae0e5696b4e9333c5db64dbc249418c1c31b066`
  （因 ordinal 修复而变）；fixture SHA `3fda70ed...` 与 canonical schema SHA
  `17a47a83...` **不变**（未动 fixture/签名结构）。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞（如实记录）：
- 真 PG 集成在无 `V2_TEST_ADMIN_DATABASE_URL` 时整模块 skip（设计如此；验收命令显式传
  env 0 skip）。
- offline 场景从「无 alembic_version 的库」应用（offline SQL 含 CREATE version 表）；与
  online「先 upgrade 再 downgrade 基线」两条路径的 version 状态呈现不同，但等价结论一致
  （都停在 baseline / 未推进到 b1000001）。
- 不扩展 Base 签名范围（default/index/CHECK/FK 仍不在合同内，非目标）。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/base_schema_contract.py \
  serve/tests/trading/test_v2_base_schema_contract.py \
  serve/tests/trading/integration/test_v2_0001_base_schema_contract.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/docs/manifests/wp-01a-01-r1-offline-pk-order.md
```

回到 WP-01A-01 交付状态（含原 offline PK 校验语义）；未改 revision/fixture/env/contract
文档；无 Base/V2 数据副作用。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-01a-01-r1-offline-pk-order.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
a077e3336eb4c5955c3f1ce23fbbfe76a15e972e6eb76c6bf43e46d296ed332a
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-01a-01-r1-offline-pk-order.md | sha256sum
```
