# WP-01A-01 — Base schema 兼容合同与 `v2_0001`

> 状态：**READY**。执行模型：DeepSeek V4 Flash。
> 完成 manifest 固定为 `serve/docs/manifests/wp-01a-01-base-schema-contract.md`。
> 最后更新：2026-08-09 07:38 EDT。

## 1. 目标与用户价值

把 Base 旧库明确当成 V2 的**只读兼容边界**，用一份去数据、可复现的 schema fixture 和
`v2_0001` fail-closed 预置证明目标库可用。这样后续 V2 migration 只拥有 `trading` schema，
不会把当前 92 项 ORM/旧库漂移误生成为 `DROP INDEX/CHECK/default` 等破坏性 DDL。

## 2. 已确认决策

1. Base 旧表继续由 legacy bootstrap/migration 拥有；V2 Alembic 只验证兼容性，不修改它们。
2. `v2_0001` 的正式语义从“自动对齐 Base metadata”收敛为“冻结 Base schema 兼容合同”。禁止把
   `alembic --autogenerate` 的原始 diff 直接变成 DDL。
3. 目标库中 18 张 Base 表**全无**时视为 literal-empty，`upgrade/downgrade` 均 no-op；这不代表
   Base 应用已 bootstrap。存在 1–17 张时固定 fail-closed；18 张全有但关键签名不符也 fail-closed。
4. 兼容签名只约束当前 Base 应用依赖的 table/column/type/nullability/primary-key；额外 public
   表、索引、CHECK、default、trigger 必须原样保留，不由 V2 接管。
5. `public.alembic_version` 仍由 Alembic 管理；autogenerate 对 public 只放行该表，V2 metadata
   仅管理 `trading`。revision 可主动 inspect Base 兼容合同，但不得产生 Base DDL。

## 3. 权威输入与依赖

- 前置：`WP-01A-00-r1` 已接受。
- schema 来源只允许：
  `/code/base/serve/storage/backups/backup_20260809_054832_532956.dump`，SHA-256：
  `14e1913edbfed6654e3b0a1073c1e346140e00fe898a063305805954184b0d59`。
- 只能从该 dump 提取 **schema-only**；禁止读取/导出/提交任何 row data、owner、ACL、凭据或
  原 dump。生成 fixture 前后必须做敏感文本扫描。
- 18 张受检表：`admin_login_logs、admin_operation_logs、admin_user_roles、admin_users、
  article_keywords、articles、db_backups、dict_items、dicts、files、keywords、menus、messages、
  publish_log、role_menus、roles、settings、users`。
- 必读：`serve/docs/v2-implementation-contract.md` §3–§4、§11–§13；
  `serve/alembic/README`；`serve/docs/tasks/wp-01a-00-r1-alembic-proof-boundaries.md`。

## 4. 精确文件范围

```text
serve/app/base_schema_contract.py
serve/alembic/env.py
serve/alembic/README
serve/alembic/versions/b1000001_v2_0001_freeze_base_schema_contract.py
serve/docs/v2-implementation-contract.md
serve/tests/trading/fixtures/base_legacy_schema.sql
serve/tests/trading/fixtures/base_legacy_schema_manifest.json
serve/tests/trading/test_v2_alembic_env.py
serve/tests/trading/test_v2_base_schema_contract.py
serve/tests/trading/integration/test_v2_0001_base_schema_contract.py
serve/docs/manifests/wp-01a-01-base-schema-contract.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改其他文件。生产代码只有 `base_schema_contract.py`、`env.py`、revision 三个文件。

## 5. 实现合同

### 5.1 Canonical fixture 与 manifest

- 在独立 `pm_v2_fixture_*` 临时库恢复输入 dump，再用 `pg_dump/pg_restore` 的 schema-only、
  no-owner、no-privileges 路径只抽取上述 18 表；fixture 必须可在空 PostgreSQL 临时库直接执行。
- SQL 必须 deterministic、UTF-8、LF；不得包含 `COPY/INSERT`、真实 owner、ACL、数据库名、主机、
  用户名、连接串或注释中的私密文本。
- JSON manifest 固定记录：source dump SHA、生成命令（凭据占位）、PostgreSQL client version、
  18 表排序清单、规范化 schema SHA、fixture SHA、生成时间；不得记录连接串。

### 5.2 兼容合同

- `base_schema_contract.py` 保存不可变 canonical 签名，并提供纯读取 validator；不得执行 DDL、
  commit、rollback 或 log 原始数据库异常。
- 结果只有 `EMPTY` 或 `COMPATIBLE`；部分表固定 reason code
  `v2_base_schema_partial`，签名不匹配固定 `v2_base_schema_incompatible`。
- 比较必须 schema-qualified 到 `public`；名称稳定排序；类型归一化后比较；错误只含 reason code
  与安全的 object identifier，不含 DSN/Provider message。

### 5.3 Revision 与 ownership boundary

- revision 固定 `revision="b1000001"`、`down_revision="cdabba1e3903"`。
- online `upgrade()` 调 validator：`EMPTY/COMPATIBLE` 不做 Base DDL；partial/incompatible 在
  version 前进前抛错并整体回滚。`downgrade()` 不改 Base 对象。
- offline SQL 必须可生成且不含密码；在应用 SQL 时执行等价的 PostgreSQL precondition，不能
  因 offline 模式静默跳过兼容检查。
- `env.py` 的 autogenerate ownership 改为：`trading` 全域 +
  `public.alembic_version`；18 张 Base 表仅由 revision validator 主动检查，不能成为
  autogenerate create/drop/alter 候选。
- 更新 implementation contract/README，删除“V2 Alembic 对齐 Base ORM 漂移”的歧义；明确
  Base schema 由 legacy 系统拥有、V2 只验证兼容。

## 6. 必测证据

### 单测

1. fixture/manifest 哈希可重算；SQL 无数据语句与敏感 marker；清单恰好 18 表。
2. validator 覆盖 empty、compatible、1/17 张 partial、缺列、错类型、错 nullability、错 PK；
   固定 reason code，且不 commit/rollback。
3. online/offline autogenerate callback 实际排除 Base public 表，仅保留 version/trading；未知 public
   对象仍排除。
4. revision id/down revision/upgrade/downgrade/offline precondition 固定；异常无 secret。

### 真 PostgreSQL 集成

1. literal-empty：`upgrade head → downgrade cdabba1e3903 → upgrade head`；不创建任何 Base 表。
2. canonical fixture：升级前后 Base schema 规范化 fingerprint 完全一致，只有
   `public.alembic_version` 变化；`alembic check` 无 Base drift。
3. 添加未知 public 表、额外 index/CHECK/default/trigger 后再升级，全部逐项保留。
4. partial 与 incompatible fixture 均在 revision/version 前进前失败，事务回滚且无对象变化。
5. 测试后 `pm_v2_test_* / pm_v2_fixture_*` 临时库零残留；必须复用 01A-00 的安全删库边界。

## 7. 验收与交付

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests alembic
.venv/bin/pytest -q tests/trading/test_v2_alembic_env.py \
  tests/trading/test_v2_base_schema_contract.py
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
  .venv/bin/pytest -q tests/trading/integration/test_v2_0001_base_schema_contract.py
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
git diff --check
```

manifest 必须记录 fixture/contract/revision SHA、真实临时库测试、前后 fingerprint、对象保留断言、
全量回归、blocker、回滚及自身 SHA。完成时只标 `DONE（待审）`，不提交、不推送。

## 8. Blocker、非目标、风险与回滚

- Blocker：源 dump SHA 不符、无法 schema-only 提取、fixture 含数据/身份/ACL、无法运行真 PG，任一
  情况均标 `BLOCKED`，禁止凭 ORM 猜 schema。
- 非目标：不“修复”92 项 legacy drift；不改 18 个 Base model；不创建 `trading` schema/0002；
  不实现 UoW/Outbox；不读取或迁移 Base row data。
- 风险：错误 ownership filter 会隐藏 V2 表。必须用 autogenerate 测试证明 trading 仍被管理、
  Base/未知 public 才被排除。
- 回滚：downgrade 到 `cdabba1e3903` 只回退 version，不碰 Base；代码回滚上述三生产文件与
  fixture/tests/docs。没有业务数据副作用。
