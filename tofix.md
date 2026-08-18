# tofix.md — 待修 / 已修问题台账

> 只记录 Base 平台自身的通用问题。下游项目必须在各自 fork/clone 中维护自己的问题台账。
> 约定: 🔴 严重 / 🟡 中等 / 🟢 已修(留档)。

---

## 🟢 [已修 2026-08-07 · base] vue-router 根路径/未知路径 未登录时渲染 404 而非跳登录

**来源**: 下游 fork/clone 的复现反馈; 2026-08-07 在 Base 中复现并应用通用修法。

### 症状
- 未登录访问后台根路径 `/` → 显示"404 页面不存在", 而不是跳到登录页。
- 登录后跳转 `/` → 再次 404, 不能直达仪表盘。

### 根因
vue-router 的 `redirect` 在**解析阶段**就完成重定向, **跳过目标路由的 `beforeEnter`**:
1. 未登录访问 `/` → `rootRoute.redirect: '/dashboard'`(动态路由, 未登录时未注册)
2. 找不到 `/dashboard` → 落到 catch-all `/:pathMatch(.*)*`
3. catch-all 的 `redirect: '/404'` → 同样跳过守卫
4. `/404` 在 `WHITE_LIST` 中, `beforeEach` 直接放行 → 渲染 404

未登录的鉴权守卫从未被触发。

### 修复(正确做法)
**catch-all 不能用 `redirect`, 要在守卫内跳转**:

```ts
// router/static.ts
{
  path: '/:pathMatch(.*)*',
  name: 'CatchAll',
  component: () => import('@/views/error/NotFound.vue'),
  meta: { title: '404', layout: 'blank' },
  beforeEnter: (to, _from, next) => {
    // redirect 会在解析阶段跳过 beforeEnter, 导致未登录绕过守卫直接渲染 404。
    // 在守卫内跳转: 未登录 → 登录页; 已登录 → 渲染 404。
    if (!isLoggedIn()) return next(`/login?redirect=${to.fullPath}`)
    return next()
  },
}
```

注意: 不要试图给 `rootRoute`(path: `/`) 或 `redirect` 路由加 beforeEnter, 它们不生效。

### base 落地
- 文件: `admin/src/router/static.ts`
- 提交: 待提交
- 验证: `npm run build` PASS（1.19s）

### 验证方法(headless)
用 puppeteer-core + 系统 Chrome:
- 未登录访问 `/` → 应 `#/login?redirect=/dashboard`
- 注入 token 后访问 `/` → 应自动跳 `#/dashboard`

### 参考
- vue-router 4: redirect 与 beforeEnter 的时序关系。
- 修复提交: 下游 fork/clone 的对应修复记录。

---

## 🔴 [已修 2026-08-07] messages 表 schema 漂移 → dashboard 数据全空

**症状**: dashboard 四个统计卡 + 最近操作全显示 `—`。接口返回 HTTP 200 但前端静默全空。

### 根因
`messages` 表被某次**非受控手改**改成了多接收者事件化设计（`recipient_id` + `recipient_type` + event/biz/payload/idempotency 等扩展列），但:
- schema_migrations 无任何迁移改过它（init.sql 与迁移 013 均为 `user_id`）
- 整个 serve 后端 **0 处代码**使用 recipient 列
- 模型 `app/models/message.py` 用 `user_id`
- 表内 **0 行数据**

`dashboard_stats` 查 `Message.user_id` → SQL 生成 `messages.user_id` → 列不存在 → `UndefinedColumn` → `main.py` 全局 `exception_handler(Exception)` 转成 **HTTP 200 + {code:500}** → 前端 `showError:false` 静默吞掉 → 显示空。

### 修复
- 迁移 `030_messages_rebuild.sql`: DROP 漂移表 + 按 init.sql/迁移 013 重建 `user_id` 结构
- 原 recipient 结构备份: `databases/backups/messages_recipient_schema_20260707.sql`
- 验证: dashboard_stats 核心查询全通过（admin=2, month_operations=2507）

### 教训
**任何改表都必须走迁移 + 同步模型 + 同步代码**。手工改表是漂移根源。若未来确需多接收者消息（admin/customer/parttime），走正式迁移，勿再手改。

---

<!-- 后续问题追加在下面 -->

---

## 🔴 [待设计·未实施 2026-08-17] Base 与下游产品缺少代码级扩展边界

### 目标与用户价值

将 Base 从“依靠文档约束的可复制模板”改进为“具有稳定扩展协议、可持续升级的基础平台”，使下游产品功能与 Base 核心能够在目录、注册、数据迁移和依赖层面明确分离，减少长期同步时的冲突、误覆盖和归属不明。

### 已确认决定

- 这是 Base 自身的通用架构不足，应记入 Base 问题台账，不归入任何具体产品。
- 当前只记录问题与验收合同，不把“插件化”“拆包”或其他候选方案默认为已决定的产品规则。
- 用户确认尚有另一个不同的主要设计不足；本条不代表或替代该问题。

### 现状与可复现证据

1. `CLAUDE.md` 要求具体产品 Fork/Clone Base，并直接在下游仓库开发。
2. `admin/README.md` 的 CRUD 扩展流程要求在现有 `admin/src/views/` 内复制页面并接入现有路由/菜单体系；后端扩展同样进入现有 `serve/app/` 分层目录。
3. `UPSTREAM.md` 和 `scripts/sync-base-release.sh` 通过 `git merge --no-ff refs/tags/base/vX.Y.Z` 把新 Base 版本合并回同一个下游工作树。
4. 上述流程没有代码级的 Base/Product 模块边界，因此下游与 Base 修改可以同时落入相同源文件、迁移序列、路由、菜单、设置 Schema 和依赖锁文件。

### 影响范围与精确文件

- 当前合同：`AGENTS.md`、`CLAUDE.md`、`UPSTREAM.md`、`CHANGELOG.md`
- 同步实现：`scripts/sync-base-release.sh`
- 后端扩展表面：`serve/app/controllers/`、`serve/app/logics/`、`serve/app/models/`、`serve/app/services/`、`serve/databases/migrations/`
- 前端扩展表面：`admin/src/router/`、`admin/src/views/`、`admin/src/components/`、`admin/package.json`、`admin/package-lock.json`
- 本条台账：`tofix.md`
- 实施目标文件待扩展协议设计确认后补齐；在决策前不虚构文件名或目录结构。

### 依赖与阻塞项

- 需先确认 Base 的分发形式：模板、可安装核心包、模块注册机制，或者组合方案。
- 需定义后端路由/Logic/Model/Service Driver、前端路由/菜单/页面以及数据库迁移的稳定扩展协议。
- 需定义现有下游项目从“同目录直接修改”迁移到新边界的兼容路径。
- **当前阻塞**：扩展模型属于尚未确认的平台决策，本条不进入实施。

### 验收证据（实施后必须可复现）

- 建立一个最小下游 fixture，包含产品独有的后端模块、前端页面、菜单/路由、设置与数据库迁移。
- 连续同步两个 Base 测试版本，下游产品模块不需要覆盖 Base 核心文件，不发生迁移版本、路由/菜单标识或依赖归属冲突。
- 运行后端全量测试、迁移到 head、前端 lint/build、`scripts/check-base-release.py` 和 `git diff --check`，全部通过。
- 从新的扩展合同能唯一判定每个模块、迁移、配置和静态资源的 Base/Product 归属。
- `UPSTREAM.md` 提供完整的升级、冲突热点、兼容性与回滚证据，并与实际自动化脚本一致。

### 非目标

- 不在 Base 仓库中引入任何具体产品或客户业务。
- 不在方案未确认前大规模搬迁目录、改写迁移序列或更换发布模型。
- 不把“合并时手动解决冲突”作为代码级隔离的验收标准。
- 不将用户尚未说明的另一个设计不足并入本条。

### 风险与回滚

- 边界改造可能改变导入路径、启动顺序、迁移拓扑、前端打包和下游同步方式，属于架构级风险。
- 实施时必须提供旧扩展方式的兼容期或机械化迁移工具，不得静默覆盖下游代码。
- 回滚单位必须是新的 Base SemVer 发布；已发布 Tag 不移动、不覆盖。

### 最后更新

2026-08-17T06:03:09-04:00

---

## ✅ [已完成 2026-08-18] Controller 内分散定义路由，缺少统一 Route Registry

### 目标与用户价值

建立类 Laravel `group / middleware / get / post / match / any` 的集中式路由层，使 URL、Method、鉴权、权限、Handler 和注册优先级可从唯一入口检索、校验和生成目录，避免规模增长后逐 Controller 检查。

### 已确认决定

- 只有 `serve/app/routes/` 定义路由；Controller 不再创建 `APIRouter`、使用路由装饰器或 `include_router()`。
- 根入口为 `app.routes.register_routes(app)`；可按 scope/domain 分片 Manifest，但必须显式聚合并生成全局 Catalog。
- 第一次重构只改注册机制，不改现有 159 个 HTTP/OpenAPI 契约。
- 具体方案、逐文件/方法介入、验收和回滚以 `serve/docs/route-registry-design.md` 为执行合同。

### 当前状态与证据

- `serve/app/main.py` 已只保留 `app.routes.register_routes(app)` 一个路由入口；Base Controller AST 边界测试确认零 Router 构造、零装饰器、零 `include_router()`。
- `serve/app/routes/` 已实现 Registry、Laravel 风格 Group/Verb DSL、CRUD 资源声明、Manifest、Catalog、CLI、编译期校验及稳定下游扩展入口。
- `python -m app.routes check`：`159 http routes, 1 mounts`；JSON Catalog 共 160 entries，ACCESS 完整、operationId 无空值、来源全部指向 `app/routes/` Manifest，两次输出字节一致。
- `serve/tests/fixtures/route-catalog-v1.json` 与当前 App：159 operations / 159 paths 零差异；GET 70、POST 89。
- 实施检查发现并关闭 I-01～I-08（来源帧、Group middleware 继承、无类型透传、安全策略 identity/access、Catalog 字段、安装顺序文档、legacy CRUD 混合访问边界、测试依赖隔离），详见方案文档第 17 节。

### 精确文件、依赖与验收

- 方案：`serve/docs/route-registry-design.md`
- 当前入口：`serve/app/main.py`
- 当前工厂：`serve/app/routes/resources.py::register_legacy_crud()`；`serve/app/controllers/base.py::crud_router()` 仅为兼容 re-export。
- 当前策略：`serve/app/deps.py`
- 当前权威来源：`serve/app/routes/system.py`、`admin.py`、`client.py`、`public.py`、`web.py`、`extensions.py`；Controller 只保留 Handler。
- 目标文件、方法、分阶段顺序、契约 fixture、全量验证和完成定义均见方案文档第 4–15 节。

### 验收证据

- 路由专项：`77 passed in 1.11s`。
- 后端全量：`289 passed in 2.98s`。
- 前端：`npm run lint` 0 errors（2 个既有 `v-html` warning）；`npm run build` PASS。
- 发布元数据：`scripts/check-base-release.py` PASS（v2.0.0）；`git diff --check` PASS。
- v2.0.0 验收时曾确认旧运行配置指向 `base_user@base`；v3.0.0 已将该临时共享身份彻底替换为 `base_platform_app@base_platform`，见本账本“Base 数据库唯一身份”条目。
- Alembic：只读检查发现旧 `base` 库记录的是其他项目 revision `b1000077`，而 Base 仓库唯一 head 为 `cdabba1e3903`；未对旧 `base` 执行 `stamp`、迁移、DDL 或数据写入。
- Alembic 离线静态生成验证：`cd serve && .venv/bin/alembic upgrade head --sql` 成功生成唯一 head `cdabba1e3903` 的 baseline SQL（仅建 alembic_version + 插入版本号），与共享 `base` 库的 `b1000077` 无关，证明 Base 迁移链可从空库独立落到 head。
- v2.0.0 的临时 `base_verify` 只用于一次性 Alembic 验收；v3.0.0 专属库通过后已删除，不再作为运行、测试或迁移目标。
- 发布：本完成提交绑定 immutable tag `base/v2.0.0`。

### 阻塞项、非目标与风险/回滚

- **当前阻塞**：无。
- 当前所有 Base 数据库验证只使用 `base_platform_app@base_platform`；下游项目必须使用自己的分项 `DATABASE_*`，不得覆盖回 Base 专属身份。
- 非目标：不改 REST/RPC 风格、不改业务 Handler、不同时重构前端 Router、不添加产品路由。
- 主要风险是漏路由、鉴权/权限漂移、fallback 遮蔽和 OpenAPI 变更；必须以 159 条 snapshot、policy catalog 和切换前提交/Tag 控制。
- 回滚必须整体撤销 Registry 入口切换，不保留半切换状态；无 DB migration。

### 最后更新

2026-08-18T02:36:27-04:00

---

## ✅ [已完成 2026-08-18] Base 数据库唯一身份、ACL 与下游隔离

### 目标与用户价值

消除 Base 运行时与其他项目共享 database/role 的歧义：明确一个唯一数据库，
让运行、迁移、验收和账本指向同一身份，并从 PostgreSQL 权限层阻止普通项目角色
访问，避免误迁移、误清理或跨项目数据污染。

### 已确认决定

- Base 唯一身份固定为 `base_platform_app@base_platform`，不提供名称覆盖入口。
- 密码只保存在 Git 忽略、`0600` 的 `serve/.env`，不进入源码、示例、日志或通用 settings。
- `PUBLIC` 无 database CONNECT/schema 权限；专属角色无管理特权且不得授予其他普通角色。
- 下游 Fork/Clone 必须使用项目专属 database/role，严禁连接、迁移、备份、清理或测试 `base_platform`。
- 旧 `base` 库不属于本次变更范围；只读识别后未执行任何写操作。临时 `base_verify` 在专属库验收完成后删除。

### 精确文件与依赖

- 默认身份：`serve/app/config.py::{BASE_DATABASE_NAME,BASE_DATABASE_USER,Settings}`
- 示例配置：`serve/.env.example`
- 建库/ACL/初始化：`scripts/provision-base-database.sh`
- 静态门禁：`scripts/check-database-boundary.py`，并由 `scripts/check-base-release.py` 调用
- 数据库合同：`serve/docs/database-boundary.md`
- 新装迁移兼容：`serve/databases/migrations/028_1_normalize_legacy_menu_seeds.sql`
- 测试：`serve/tests/test_database_boundary.py`
- 账本：`AGENTS.md`、`CLAUDE.md`、`README.md`、`serve/README.md`、
  `CHANGELOG.md`、`UPSTREAM.md`、`tofix.md`

### 当前状态与验收证据

- PostgreSQL：database owner=`base_platform_app`；ACL=`base_platform_app=CTc/...`，无 PUBLIC 项。
- Role：`NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOREPLICATION/NOBYPASSRLS`；无成员关系。
- 普通登录角色 CONNECT 矩阵：只有 `base_platform_app=true`，其余全部 false。
- 完整 schema：20 张 public table（18 张模型表 + `schema_migrations` + `alembic_version`）。
- SQL 迁移账本：29/29；Alembic：`cdabba1e3903 (head)`。
- 本地运行时 `.env` 已切换且权限 `0600`；`/health/ready` 返回 `{"status":"ready"}`。
- 建库脚本在同一库连续执行两次：第二次 SQL migration applied=0，schema/ACL
  仍为 29/29，证明幂等且不会重新初始化数据。
- 工程验证：后端 `291 passed`；路由检查 `159 http routes, 1 mounts`；前端
  lint 0 errors（2 个既有 warning）/build PASS；release/boundary check 与
  `git diff --check` PASS。
- 发布目标：`base/v3.0.0`。

### 阻塞项、非目标与风险/回滚

- 当前阻塞：无。
- 非目标：不读取、不迁移、不删除旧 `base` 或任何下游数据库；不把 Base 专属凭据交给下游。
- PostgreSQL superuser 始终保留管理能力；它不是应用/项目角色，普通角色隔离由 ACL 强制。
- 回滚代码不回滚数据库隔离；数据恢复只使用 `base_platform` 的专属备份。

### 最后更新

2026-08-18T03:03:00-04:00

---

## ✅ [已完成 2026-08-18] 下游 Fork/Clone 一键开箱

### 目标与用户价值

让新项目在完成 Fork/Clone 和 `upstream` remote 后，通过一条命令获得可开发、
可测试、数据库隔离的完整运行环境，消除手工建库、配 env、安装依赖和逐项验收。

### 已确认决定

- 标准命令：`scripts/bootstrap-project.sh PROJECT_SLUG "Project Name"`。
- database=`PROJECT_SLUG`，role=`PROJECT_SLUG_app`；Base 标识全部保留并拒绝。
- Base 与下游共用一个通用 PostgreSQL 初始化内核，不复制迁移/ACL 逻辑。
- 缺少 `upstream`、缺少项目 remote，或两个 remote URL 相同时停止，防止在 Base
  原仓库直接生成产品项目。
- 密码随机生成，只进入 ignored `serve/.env`；`PROJECT.md` 只记录非敏感同步信息。
- 默认完成依赖安装、SQL/Alembic、route check、pytest、lint 和 build，不留下半验收状态。

### 精确文件

- 下游入口：`scripts/bootstrap-project.sh`
- 共享内核：`scripts/lib/provision-postgres-database.sh`
- Base 固定入口：`scripts/provision-base-database.sh`
- 合同：`serve/docs/project-bootstrap.md`
- 门禁：`scripts/check-database-boundary.py`
- 测试：`serve/tests/test_project_bootstrap.py`
- 使用说明：`AGENTS.md`、`CLAUDE.md`、`README.md`、`serve/README.md`、
  `admin/README.md`、`UPSTREAM.md`、`serve/docs/database-boundary.md`

### 验收证据

- 隔离临时 Fork 使用 `fork_fixture` 实际执行 bootstrap 成功：完整 schema、
  SQL migration 29/29、Alembic head 到达 `cdabba1e3903`。
- 生成 `fork_fixture_app@fork_fixture`；项目 role 对项目库 CONNECT=true、对
  `base_platform` CONNECT=false；Base role 对 fixture CONNECT=false。
- 自动生成的前后端 `.env` 权限 `0600` 且密码未输出；`PROJECT.md` 不含密码。
- fixture database/role 与临时工作树已在验收后删除，Base 数据库保持 ready。
- 首轮完整 Fork 测试发现备份锁/任务锁测试写死 `base:` 前缀；已改为
  `settings.APP_NAME`，复跑临时 Fork：291 passed、路由检查通过、前端 lint
  0 errors/build PASS。
- 当前 Base 完整工程验证：295 passed、路由 `159 http routes, 1 mounts`、
  前端 lint 0 errors/build PASS；发布证据记录在 `base/v3.1.0`。

### 阻塞、非目标与回滚

- 当前阻塞：无。
- 前置依赖为本机 Python/Node/OpenSSL/PostgreSQL 与 postgres 管理权限；脚本不代替
  操作系统包管理器。
- 非目标：不在 Base 仓库生成具体项目，不替已有下游重建数据库。
- 回滚只删除新项目自己的 database/role/env/`PROJECT.md`，不得操作 Base 或其他项目。

### 最后更新

2026-08-18T03:15:00-04:00

---

## ✅ [已完成 2026-08-18] Base 发布节点与下游版本/更新历史账本

### 目标与用户价值

让 Base 每次发布明确回答“更新了哪些节点、哪些文件、是否迁移、怎么验证和回滚”；
让所有 Fork/基座项目明确回答“当前用了哪个 Base 版本、历次更新了什么、下次如何
计划和执行更新”，避免凭记忆合并或代码版本与账本脱节。

### 已确认决定

- 每个 Base 版本必须有 `releases/base-vX.Y.Z.json`，更新 node 使用稳定 ID。
- `CHANGELOG.md` 是人读发布账本；Manifest 是机器同步权威，两者版本/日期必须一致。
- 下游提交 `PROJECT.md` 保存当前 Base version/tag/commit 和下一次计划/更新命令。
- 下游提交 append-only `BASE_UPDATES.md`，逐次记录跨版本节点、精确 Base 文件 diff、
  迁移、动作、冲突点、验证、回滚、时间和 commit。
- sync 在合并前打印计划，合并后执行完整工程验证，并将 Base 代码与含 PASS 证据的
  两个账本原子提交；冲突/验证失败发生在账本写入前，commit hook 失败后可从
  merge 前 HEAD 重建账本继续，不重复历史。

### 精确文件

- 发布 Manifest：`releases/base-v*.json`
- 计划/记录 CLI：`scripts/base-update-ledger.py`
- 原子同步：`scripts/sync-base-release.sh`
- 新项目接入：`scripts/bootstrap-project.sh`
- 发布门禁：`scripts/check-base-release.py`
- 合同：`serve/docs/base-update-ledger.md`、`UPSTREAM.md`
- 测试：`serve/tests/test_base_update_ledger.py`

### 验收证据

- 回填 v1.0.0、v2.0.0、v3.0.0、v3.1.0，并新增 v3.2.0 Manifest；5/5 schema 校验通过。
- v3.0.0→v3.2.0 计划正确聚合 v3.1.0/v3.2.0 的全部 update nodes。
- 临时下游账本完成 initialize→record，`PROJECT.md` 从 v3.1.0 更新到 v3.2.0，
  `BASE_UPDATES.md` 保留初次采用和升级两段历史，版本不匹配时明确阻断。
- 账本/同步/bootstrap/database boundary 专项 13 passed；`--continue` 固定从
  merge 前 HEAD 读取源版本并重建派生账本，覆盖重复执行边界。
- 新项目 bootstrap 同时生成 PROJECT/BASE_UPDATES；标准 sync 使用 no-commit merge
  后记录账本并形成单一 merge commit。
- 隔离 Git upstream/downstream 模拟 v3.2.0→v3.3.0：更新前准确列出 synthetic node
  与 tag 间文件 diff；合并后 302 tests、lint 0 errors、build PASS；最终 merge commit
  有 2 个 parent，目标 tag 为祖先，PROJECT 更新到 v3.3.0，BASE_UPDATES 追加 PASS 证据。
- 另以一次性 commit hook 故障中断同步，再用 `--continue` 完整复验：最终仅有 1 条
  v3.2.0→v3.3.0 历史、2-parent merge、目标 tag 祖先关系成立、工作树完全干净。
- 当前 Base：302 passed；路由 `159 http routes, 1 mounts`；前端 lint 0 errors
  （2 个既有 warning）/build PASS；release/Manifest/boundary/diff checks PASS。
- 完整工程验证与模拟下游 tag 更新结果记录在 `base/v3.2.0`。

### 阻塞、非目标与回滚

- 当前阻塞：无。
- 非目标：不把下游产品变更写入 Base Manifest，不在账本保存 secret。
- v3.1.0 下游首次合并 v3.2.0 需按合同执行一次 record + amend；后续全自动。
- 同步未提交执行 `git merge --abort`；已提交 revert 整个 merge，并追加回滚历史。

### 最后更新

2026-08-18T03:41:04-04:00
