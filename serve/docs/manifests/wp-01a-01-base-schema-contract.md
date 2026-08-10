# COMPLETION MANIFEST — WP-01A-01 · Base schema 兼容合同与 `v2_0001`

- Work package: `WP-01A` 子任务 `WP-01A-01`
- 状态: **DONE（待审）**
- 日期: 2026-08-09
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-01A-00-r1` 已接受（ACCEPTED）
- 规范依据: `serve/docs/tasks/wp-01a-01-base-schema-contract.md`；`serve/docs/v2-implementation-contract.md` §3–§4/§11–§13；`serve/alembic/README`
- 权威输入: `/code/base/serve/storage/backups/backup_20260809_054832_532956.dump`，
  SHA-256 `14e1913edbfed6654e3b0a1073c1e346140e00fe898a063305805954184b0d59`（核验一致）
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/base_schema_contract.py` | **新增** | 生产代码：不可变 canonical 签名（18 表）+ 纯读 validator（EMPTY/COMPATIBLE，partial→`v2_base_schema_partial`、签名不符→`v2_base_schema_incompatible`）+ offline 等价 PostgreSQL precondition（DO 块）单源生成 |
| `serve/alembic/env.py` | 修改 | 生产代码：`PUBLIC_ALLOWED_TABLES = {alembic_version}`（18 张 Base 表交给 revision validator）；`version_table_schema` 改 `None`（default schema 表示，修复 `alembic check` 误报 DROP version 表） |
| `serve/alembic/versions/b1000001_v2_0001_freeze_base_schema_contract.py` | **新增** | 生产代码：revision `b1000001` / down `cdabba1e3903`；online 调 validator，offline 发 DO 块 precondition，downgrade 不改 Base |
| `serve/alembic/README` | 修改 | 增补「Base 兼容合同（v2_0001）」小节与 version 表表示说明，删除「对齐漂移」歧义 |
| `serve/docs/v2-implementation-contract.md` | 修改 | §4 `v2_0001_align_base_metadata` → `v2_0001_freeze_base_schema_contract`（只读合同，不建 V2 表、不对齐漂移）；§3 env.py 行更新 |
| `serve/tests/trading/fixtures/base_legacy_schema.sql` | **新增** | canonical fixture：权威 dump schema-only 提取 18 表（含 18 PK / 7 FK / CHECK / 索引 / 默认值 / 注释），无数据、无 owner/ACL、无连接串、确定性 |
| `serve/tests/trading/fixtures/base_legacy_schema_manifest.json` | **新增** | fixture manifest：source dump SHA、生成命令（凭据占位）、PG client version、18 表排序清单、规范化 schema SHA、fixture SHA、生成时间 |
| `serve/tests/trading/test_v2_alembic_env.py` | 修改 | 更新 allowlist 断言：public 仅放行 `alembic_version`、Base metadata 表被排除；`version_table_schema=None` |
| `serve/tests/trading/test_v2_base_schema_contract.py` | **新增** | 25 纯单测（fixture/manifest 哈希、敏感扫描、validator 全矩阵、offline precondition、revision 行为） |
| `serve/tests/trading/integration/test_v2_0001_base_schema_contract.py` | **新增** | 6 真 PG 集成（literal-empty、fingerprint 不变 + `alembic check`、额外对象保留、partial/incompatible 失败回滚、零残留） |
| `serve/tests/trading/integration/test_v2_alembic_env_integration.py` | 修改 | **必要测试更新**：WP-01A-01 新增 revision 使 head 变为 `b1000001`，原硬编码「head==基线」断言过期；3 个探针 revision 的 `down_revision` 改为挂到 `b1000001`，避免多 head |
| `serve/docs/manifests/wp-01a-01-base-schema-contract.md` | **新增** | 本 manifest |

范围外未动：`app/config.py`、`app/models/`（18 个 Base model 不改）、`app/main.py`、legacy
DB/Redis/Artifact/observability、revision `cdabba1e3903`（未改写未重签）、`alembic.ini`、
V1。**未创建** `trading` schema / `v2_0002` / UoW / Outbox（非目标）。

> 透明说明：`test_v2_alembic_env_integration.py` 属任务 §4 允许清单之外，但新 revision 使
> 其断言必然失效（任务 §7 要求全量 suite 通过），故做最小必要更新——仅改 head 断言与
> 探针 `down_revision`，不改变其测试语义。无任何生产文件在 §4 清单之外被改。

---

## 2. 实现内容（§5 精确合同）

### 2.1 Canonical fixture 与 manifest（§5.1）

- 在 `pm_v2_fixture_*` 临时库 `pg_restore --schema-only --no-owner --no-privileges`
  全量恢复权威 dump（`-t` 定向恢复只建表不建约束，会丢 primary-key，故全量恢复），再
  `pg_dump --schema-only --no-owner --no-privileges -t <18 表>` 提取 fixture。
- 归一化：剥离 pg_dump 18.4 每次随机注入的 `\restrict`/`\unrestrict` 行（破坏确定性、
  无 DDL 语义）；CRLF→LF；UTF-8。**确定性已验证**（两次独立生成 SHA 一致）。
- fixture 无 `COPY/INSERT`、无 `OWNER TO/GRANT/ACL`、无连接串/主机/用户名、无 `base_user`；
  `password` 仅作为列名（`admin_users.password` / `users.password`）出现，无凭据值（扫描
  判定 + 逐行白名单断言）。
- JSON manifest 记录 source dump SHA、生成命令（`<dbname>` 占位，本地 socket peer 认证
  不含主机/用户名/密码）、PostgreSQL client 18.4、18 表排序清单、规范化 schema SHA
  `17a47a83...`、fixture SHA `3fda70ed...`、生成时间；不含连接串。

### 2.2 兼容合同（§5.2）

- `CANONICAL_SIGNATURE` 不可变（`MappingProxyType` + tuple 深冻结，单元测试断言
  mutation 抛 TypeError），由权威 dump schema-only 提取生成，18 表全列
  table/column/type（`format_type` 归一化）/nullability/primary-key。
- `validate_base_schema(connection)` 纯读（只发 SELECT）：0 表→`EMPTY`；18 表签名一致
  →`COMPATIBLE`；1–17 表→`BaseSchemaContractError`（`v2_base_schema_partial`）；签名不符
  →`BaseSchemaContractError`（`v2_base_schema_incompatible`，detail 只含安全 object
  identifier 如表/列名）。不 DDL / commit / rollback / 不 log 原始 DB 异常；错误不含
  DSN / password / Provider message。

### 2.3 Revision 与 ownership 边界（§5.3）

- revision `b1000001` / down `cdabba1e3903`。online `upgrade()` 调 validator：
  EMPTY/COMPATIBLE 不做 Base DDL；partial/incompatible 在 version 前进前抛错、外层事务
  整体回滚。`downgrade()` 不改 Base 对象。
- offline 不静默跳过：生成等价 PostgreSQL precondition（`DO $v2_base_precondition$`
  块，基于 `CANONICAL_SIGNATURE` 单源生成），应用 SQL 时执行同样的
  EMPTY/partial/incompatible 检查；不含密码。
- env.py ownership：`trading` 全域 + `public.alembic_version`；18 张 Base 表仅由
  revision validator 主动检查，不进入 autogenerate create/drop/alter 候选、不得反射。
- **`version_table_schema` 由 `"public"` 改为 `None`**（WP-01A-00 字面量的必要修正）：
  `include_schemas=True` 时 Alembic autogenerate 用 `None` 表示 default schema（public），
  tables.py 仅当 `schema_name == version_table_schema` 时排除版本表；字面量 `"public"`
  会使 `alembic check` / `revision --autogenerate` 把 `alembic_version` 误判为待删除表。
  物理位置不变（search_path=public, pg_catalog 下版本表仍落 public）。
- contract/README 已更新：删除「V2 Alembic 对齐 Base ORM 漂移」歧义；明确 Base schema
  由 legacy 系统拥有、V2 只验证兼容。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests alembic
# → exit 0

# 2) 纯单测（env + contract）
.venv/bin/pytest -q tests/trading/test_v2_alembic_env.py \
  tests/trading/test_v2_base_schema_contract.py
# → 40 passed in 0.24s（15 env + 25 contract）

# 3) 真 PostgreSQL 集成（显式传 env，0 skip）
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/pytest -q tests/trading/integration/test_v2_0001_base_schema_contract.py
# → 6 passed in 3.00s（PostgreSQL 18.4）

# 4) tests/trading（显式传 env）
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
# → 718 passed, 8 warnings

# 5) 全量回归（显式传 env，0 skip）
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
# → 929 passed, 9 warnings in 9.05s（9 为既有 pytest-asyncio/FastAPI 弃用告警）

# 6) git diff --check
git diff --check
# → 无输出，exit 0

# 7) 临时库零残留
psql -d postgres -Atc "SELECT datname FROM pg_database WHERE datname LIKE 'pm\_v2\_%'"
# → 空（pm_v2_test_* / pm_v2_fixture_* / 冒烟遗留全部清理）
```

---

## 4. 关键证据

### 4.1 fixture / contract / revision SHA

- source dump SHA：`14e1913edbfed6654e3b0a1073c1e346140e00fe898a063305805954184b0d59`
- fixture SHA-256：`3fda70eda514f1a6d368a0d1e17b916197cb6683fb479bdc3a4f8ac9c483dc49`
  （`sha256sum` 复算一致；两次独立生成一致 = 确定性）
- canonical schema SHA-256：`17a47a83548c2f6e4c5c7fa48ae80ceb32d1c3274392b0d3bb58c8aecbbc008a`
  （`canonical_signature_sha256()` 复算一致）
- contract 模块文件 SHA-256：`082b472112bb570e4bf661cbf919636157804aaebf294ad7090fcc029efbfe5b`
- revision：`b1000001` / down `cdabba1e3903`；revision 文件 SHA-256
  `3e57ada88d667c1b12faea8f3f494eb731b91bdc1ffc4183e7d87835b88612ac`；
  `alembic heads` 单 head `b1000001`。

### 4.2 真 PostgreSQL 集成（6，任务 §6.2 五项全关）

- `test_literal_empty_upgrade_downgrade_upgrade`：空临时库 upgrade head → version
  `b1000001`、用户表仅 `alembic_version`；downgrade `cdabba1e3903` → version 回到基线；
  再 upgrade head 恢复。**不创建任何 Base 表**。
- `test_fixture_upgrade_keeps_base_fingerprint`：fixture 应用后 `extract_signature`
  before/after 完全相等（只有 `alembic_version` 变化）；`alembic check` 无 Base drift
  （autogenerate 不反射 18 张 Base 表）。
- `test_extra_public_objects_preserved`：未知 public 表 + 额外 index/CHECK/default/trigger
  升级后**全部逐项保留**（未知表、`idx_admin_users_email_extra`、
  `chk_admin_users_status_extra`、nickname default、`trg_admin_users_extra`）。
- `test_partial_fails_before_version_advances`：DROP `settings` 后 upgrade → reason
  `v2_base_schema_partial`，version 停在基线，失败 run 无对象变化。
- `test_incompatible_fails_before_version_advances`：`ALTER username TYPE text` 后 upgrade
  → reason `v2_base_schema_incompatible`，version 停在基线，列仍为 text。
- `test_no_temporary_database_residue`：`pm_v2_test_* / pm_v2_fixture_*` 零残留。

### 4.3 纯单测（25 contract + 15 env）

- fixture/manifest 哈希可重算；SQL 无数据语句与敏感 marker；清单恰好 18 表（排序一致）；
  `password` 仅列名。
- validator 全矩阵：empty / compatible / 1 张 / 17 张 partial / 缺列 / 错类型 / 错
  nullability / 错 PK，reason code 固定，`commit/rollback/begin` 计数全 0。
- canonical 签名不可变（MappingProxyType 阻止写入）。
- offline precondition：含 reason codes 与全部表/列，无密码。
- revision：id/down 固定；online 调 validator（compatible 无 DDL、partial 抛错）；
  offline 发 DO 块；downgrade no-op。
- env allowlist：public 仅 `alembic_version`，Base metadata 表与未知 public 表排除，
  trading 全域；`version_table_schema=None`。

### 4.4 offline SQL 语义（真 PG）

`alembic upgrade head --sql` 输出含 `CREATE TABLE alembic_version`、
`DO $v2_base_precondition$`（含 partial/incompatible reason）、版本更新与 transaction
boundary；无密码。应用验证：compatible 库 → 通过且 version=b1000001；partial 库 →
`ERROR: v2_base_schema_partial` 整体回滚。不因 offline 静默跳过。

### 4.5 回归

898 全量（WP-01A-00-r1 已接受基线）+ 25 contract 单测 + 6 集成 = **929 passed、
9 既有弃用告警、0 skip（显式 env）、`git diff --check` 干净、临时库零残留**。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞（如实记录）：
- `version_table_schema` 由 WP-01A-00 的字面量 `"public"` 修正为 `None`（default schema
  表示）——WP-01A-01 §6.2.2 `alembic check` 必须通过，原字面量会导致把 `alembic_version`
  误报为待删除表；物理位置仍为 public。已在 env.py 注释与 README 显式说明。
- `test_v2_alembic_env_integration.py` 属 §4 清单外，因新 revision 链使旧断言失效而做
  最小必要更新（见 §1 透明说明）；未改变测试语义。
- 真 PG 集成在无 `V2_TEST_ADMIN_DATABASE_URL` 时整模块 skip（设计如此；验收命令显式传
  env 0 skip）。
- 本任务不创建 `trading` schema / `v2_0002` / UoW / Outbox（非目标）。
- 18 张 Base 表与 `Base.metadata` 的历史漂移（92 项）**未消解**——由本任务的兼容合同
  隔离在 autogenerate 之外，不生成破坏性 DDL（非目标）。

---

## 6. 回滚方式

```bash
git checkout -- serve/alembic/env.py serve/alembic/README \
  serve/docs/v2-implementation-contract.md serve/tests/trading/test_v2_alembic_env.py \
  serve/tests/trading/integration/test_v2_alembic_env_integration.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/app/base_schema_contract.py \
  serve/alembic/versions/b1000001_v2_0001_freeze_base_schema_contract.py \
  serve/tests/trading/test_v2_base_schema_contract.py \
  serve/tests/trading/integration/test_v2_0001_base_schema_contract.py \
  serve/docs/manifests/wp-01a-01-base-schema-contract.md
git rm -rf serve/tests/trading/fixtures
```

回到 WP-01A-00-r1 交付状态（含其测试原样）；未改 `cdabba1e3903` revision / Base model /
config / legacy 迁移系统。`v2_0001` 的 downgrade 只回退 version、不触碰 Base 表，无业务
数据副作用；回滚代码后重新 `upgrade head` 仍回到 `cdabba1e3903`。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-01a-01-base-schema-contract.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
82c14364a9ae9885e015668b108a9313c923b97b655deea836c26fce44076001
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-01a-01-base-schema-contract.md | sha256sum
```
