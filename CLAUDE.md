# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Base Platform

通用基础平台 — 任何业务系统的起点。它同时是 gui-tu 等下游项目的上游：base 先实现并验证，再同步到下游。

## 项目结构

```
serve/          Python 后端（FastAPI + SQLAlchemy 2 async + PostgreSQL + Redis）
admin/          Vue 前端（Vue 3 + TypeScript + Element Plus + Vite）
php_project/    后端的 PHP 镜像（controller/logic/model/service/middleware/task/queue/scheduler）
serve/docs/     架构事实源（api-convention / queue-task-design / service-design / rbac / file-system）
serve/databases/  init.sql（11 核心表+种子）+ 域名 SQL（article/tag/seo）+ migrations/（001-015）
```

**重要**：`serve/docs/` 是架构设计的唯一权威来源，动手前先读对应文档（api-convention、queue-task-design、service-design、rbac-design/implementation、file-system-design、keyword-refactor-design）。文档与代码冲突时以代码为准，并同步修文档。

## 快速启动

```bash
cd serve && cp .env.example .env   # 填 DATABASE_PASSWORD + REDIS_PASSWORD
./start.sh                          # 一键启动（见下方「进程模型」）
# 默认管理员: admin / admin123
```

`start.sh` 一次性拉起 3 个进程（固定端口 9000/9200，被占自动 kill 占用进程再复用，绑 0.0.0.0，输出局域网 IP，Ctrl+C 一起收）：
1. `uvicorn app.main:app --reload` — API
2. `python -m app.worker` — 队列消费 + 定时任务（**独立进程**）
3. `npm run dev` — Vite 前端

单测：项目目前**没有自动化测试**（serve 与 admin 均无 test 目录）。常规验证靠 `npm run build`（`vue-tsc -b && vite build`，同时做类型检查）和真实启动验证。

## 架构分层

```
Controller（crud_router）  →  Logic（BaseLogic）  →  Model  →  DB
                                    ↕
                              Service（BaseService + 驱动）
                                    ↕
                                  Redis（Token 会话 + 缓存 + 队列）
```

## 核心约定

### 新增业务模块（后端）

1. `app/models/order.py` — 建 Model
2. `app/logics/order.py` — 继承 BaseLogic，配置字段白名单
3. Controller 里一行注册：`crud_router("order", order_logic, perms_prefix="admin:order")`
4. 自动获得：getList / getDetail / doEdit / doDelete / doExport

### 新增业务页面（前端）

1. `src/views/xxx/order/index.vue` — CrudTable 配置 columns + formFields
2. 数据库 menus 表加一条记录（template_path = "xxx/order/index"）→ 路由自动生成

### 路由分组（main.py 挂载）

| 前缀 | 控制器 | 用途 |
|------|--------|------|
| `/api/admin` | `controllers/admin/` | 后台 CRUD + 权限校验 |
| `/api/client` | `controllers/client/` | 前台用户（无 RBAC） |
| 根路径 | `controllers/web/` | SEO 站点文件：`/sitemap.xml`、`/sitemap-{n}.xml`、`/robots.txt`、`/{name}` IndexNow key 验证文件 |

### Worker：定时任务 vs 队列任务

新增任务 = 新建一个类文件，worker 启动时自动扫描注册（零外部依赖，纯 asyncio + Redis，无 Celery）。

| 类型 | 目录 | 基类 | 触发方式 |
|------|------|------|----------|
| 定时任务 | `app/tasks/` | `BaseTask`（声明 `interval` 秒） | worker 按 interval 推入队列 |
| 队列任务 | `app/jobs/` | `BaseJob`（`name` + `handle`） | 业务代码 `await queue.push('job-name', data)` |

Redis 队列键：`{APP_NAME}:queue:{default,export,notify,task}`。当前定时任务含 SEO 管线 4 件套（`seo_pipeline` / `seo_publisher` / `seo_scheduler_task` / `seo_phase_detector`）+ `cleanup_expired` + `system_monitor`。`worker.py` 用 `MAX_CONCURRENT=10` 限制并发防 OOM。

### Service 层

配置驱动 + 驱动可插拔：服务商凭证存 DB `settings` 表（category+name），运行时按 `default` 切换，不改代码。每种服务一个目录：`sms/`、`storage/`、`notify/`，各自含 `interface.py` + `drivers/` 下每厂商一个 Driver（纯 HTTP + 手写签名，零 SDK）。

**错误不抛异常，存实例状态**：
```python
await sms_service.send_code(db, phone, code)
if sms_service.failed:
    logger.error(sms_service.error)
```

### Token 认证

Opaque Bearer token（Authorization 头），**会话状态存 Redis，不是签名 JWT**。`app/utils/token.py` 生成 token，`app/deps.py` 提供 `require_auth` / `require_admin` / `require_client` 三个依赖，通过 Depends 做路由级鉴权。`api-convention.md` 中 "JWT" 字样是宽松说法，实际实现以 Redis 为准。

### RBAC

菜单与权限合一：menus 表 `type` 0=目录 / 1=菜单页面 / 2=按钮权限点（挂在页面下）。`require_perms("admin:order:edit")` 一行校验。**RBAC 仅作用于 admin 端**，client 端无角色权限。超级管理员（`is_super_admin`）不受限。设计见 `serve/docs/rbac-design.md`。

### BaseLogic 子类可覆写

| 方法 | 作用 |
|------|------|
| `allowed_filters()` | 允许过滤的字段白名单 |
| `allowed_sorts()` | 允许排序的字段白名单 |
| `keyword_fields()` | keyword 搜索目标字段 |
| `format_save_data()` | 入库前格式化 |
| `before_create()` / `before_edit()` / `before_delete()` | 生命周期钩子 |
| `format_data()` | 输出格式化 |
| `export_header_map()` | 导出表头（返回非空即启用导出） |
| `format_export_row()` | 导出行格式化 |

## 数据库（重要）

- `init.sql` = 11 张核心表（admin_users / users / settings / menus / roles / role_menus / admin_user_roles / admin_operation_logs / admin_login_logs / messages / files）+ 种子。
- `migrations/`（001-015）记录演进历史，**当前 schema 以 init.sql + 最新迁移 + article.sql 为准**。
- **遗留陷阱**：`tag.sql` / `seo.sql` 里的 `tags` / `search_keywords` / `article_tags` / `publish_schedule` 表**已被迁移 015 删除**，这两个 SQL 文件**与当前 schema 不同步，勿以它们为准**（`controllers/admin/seo.py` 有注释掉的 `publish_schedule` 引用作证）。
- 关键词模块已完成重构：三表合并为单表 `keywords`（`stage` candidate/approved/archived + `review_status` + `source_code` + `metrics_json` + `ai_review_json`）+ `article_keywords` 关联表。设计见 `serve/docs/keyword-refactor-design.md`，表结构与菜单重命名（content-tag → content-keyword）全在 `015_keyword_unify.sql`（含空表强断言，生产保护）。

## PHP 镜像

`php_project/` 是后端的 PHP 全量镜像（controller/logic/model/service/middleware/task/queue/scheduler/command 等）。**改后端业务逻辑时先确认是否要同步到 PHP 端**；php 侧大量目录（如 `market_test`、`product`）是历史遗留，不要以 php_project 推断当前产品结构。

## 设计规范（前端）

- 圆角上限 4px，扁平无阴影，边框区分层级
- Primary #2563EB，字体 Geist，所有按钮带 Lucide 图标
- Element Plus 全覆写（见 `src/styles/element-override.scss`）
- 后端 CRUD 页统一走声明式 `CrudTable` + `CrudForm`；`createCrudApi`（`src/api/crud.ts`）生成 API 工厂；导出用 `hooks/useExport.ts`
- 常用业务组件：`SettingForm`（多服务商配置）、`FileManager` / `FileUpload` / `ImageUpload`、`RichEditor`（wangeditor）、`ArticleEditor`、`PermButton`（按钮权限）、`DictTag`、`JsonEditor`
- 页面分布：`views/content/`（article、keyword）、`views/seo/`（dashboard、log、sitemap）、`views/settings/`（ai、notify、payment、seo、site、sms、storage）、`views/system/`（log、menu、role、setting、user）+ dashboard/message/profile/login

## 关键文件索引

| 文件 | 说明 |
|------|------|
| `serve/app/logics/base.py` | BaseLogic — CRUD + 缓存 + 查询 + 校验 + 导出 |
| `serve/app/controllers/base.py` | crud_router — 一行生成 5 个接口 |
| `serve/app/utils/query.py` | QueryHelper — 35 操作符 |
| `serve/app/utils/validator.py` | Validator — 16 规则 |
| `serve/app/utils/export_helper.py` | ExportHelper — XLSX 生成 + 分块 + 进度 |
| `serve/app/services/base.py` | BaseService + 驱动工厂 |
| `serve/app/deps.py` | 鉴权依赖（require_admin / require_perms） |
| `serve/app/queue.py` | Queue.push() — 入队客户端 |
| `serve/app/worker.py` | Worker — 队列消费 + 定时任务（独立进程） |
| `serve/app/config.py` | Settings（.env 基础设施配置，区别于 DB settings 表） |
| `serve/app/tasks/` + `serve/app/jobs/` | 定时任务 / 队列任务 |
| `serve/docs/` | 架构事实源（先读这里） |
| `serve/ASSETS.md` / `admin/ASSETS.md` | 后端 / 前端完整资产清单 |
| `admin/src/components/CrudTable/` | 声明式 CRUD 组件 |
| `admin/src/components/SettingForm/` | 多服务商配置组件 |
| `admin/src/api/crud.ts` | createCrudApi — CRUD API 工厂 |
| `admin/src/hooks/useExport.ts` | 导出 Hook |
