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
- 数据库账户已确认并修复：应用实际使用 `base_user@base`；本地 `.env` 已同步有效凭据，异步 SQLAlchemy 连接成功，重载后的 `/health/ready` 返回 `{"status":"ready"}`。
- Alembic：密码问题解决后发现 `base` 库记录的是下游产品 revision `b1000077`，而 Base 仓库唯一 head 为 `cdabba1e3903`；该库含 133 张表及大量产品表，不得直接 `stamp` 覆盖版本。
- Alembic 离线静态生成验证：`cd serve && .venv/bin/alembic upgrade head --sql` 成功生成唯一 head `cdabba1e3903` 的 baseline SQL（仅建 alembic_version + 插入版本号），与共享 `base` 库的 `b1000077` 无关，证明 Base 迁移链可从空库独立落到 head。
- Alembic 独立库：以 PostgreSQL 管理权限创建 `base_verify`（owner=`base_user`），由 `base_user` 执行 `databases/init.sql` 后运行 `DATABASE_NAME=base_verify .venv/bin/alembic upgrade head`；`alembic current` 输出 `cdabba1e3903 (head)`，12 张表/序列对象均无非 `base_user` owner。
- 发布：本完成提交绑定 immutable tag `base/v2.0.0`。

### 阻塞项、非目标与风险/回滚

- **当前阻塞**：无。
- 独立验证使用 `DATABASE_NAME=base_verify`，因为 `alembic/env.py` 从 `app.config` 的分项 `DATABASE_*` 设置构建连接串；不使用未定义的 `DATABASE_URL` 覆盖。
- 非目标：不改 REST/RPC 风格、不改业务 Handler、不同时重构前端 Router、不添加产品路由。
- 主要风险是漏路由、鉴权/权限漂移、fallback 遮蔽和 OpenAPI 变更；必须以 159 条 snapshot、policy catalog 和切换前提交/Tag 控制。
- 回滚必须整体撤销 Registry 入口切换，不保留半切换状态；无 DB migration。

### 最后更新

2026-08-18T02:36:27-04:00
