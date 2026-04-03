# Base Platform

通用基础平台 — 任何业务系统的起点。

## 项目结构

```
serve/          Python 后端（FastAPI + SQLAlchemy 2 + PostgreSQL + Redis）
admin/          Vue 前端（Vue 3 + TypeScript + Element Plus + Vite）
```

## 快速启动

```bash
# 后端
cd serve
cp .env.example .env          # 填 DATABASE_PASSWORD + REDIS_PASSWORD
psql -d base -f databases/init.sql   # 初始化数据库（11 表 + 菜单 + 管理员）
pip install -r requirements.txt
uvicorn app.main:app --port 3000     # API 服务
python -m app.worker                 # 队列 + 定时任务

# 前端
cd admin
npm install && npm run dev

# 默认管理员: admin / admin123
```

## 架构分层

```
Controller（crud_router）  →  Logic（BaseLogic）  →  Model  →  DB
                                    ↕
                              Service（BaseService + 14 驱动）
                                    ↕
                                  Redis（Token + 缓存 + 队列）
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

### Service 层

错误不抛异常，存实例状态：
```python
await sms_service.send_code(db, phone, code)
if sms_service.failed:
    logger.error(sms_service.error)
```

每个 Driver 完全独立，纯 HTTP + 手写签名，零 SDK。

### Token

Redis Session（不是 JWT）。access_token + refresh_token，用户信息存 Redis。

### RBAC

菜单表 type：0=目录 / 1=菜单 / 2=按钮（权限点）。`require_perms("admin:order:edit")` 一行校验。

## 设计规范（前端）

- 圆角上限 4px，扁平无阴影，边框区分层级
- Primary #2563EB，字体 Geist
- 所有按钮带 Lucide 图标
- Element Plus 全覆写（见 `src/styles/element-override.scss`）

## 关键文件索引

| 文件 | 说明 |
|------|------|
| `serve/app/logics/base.py` | BaseLogic — CRUD + 缓存 + 查询 + 校验 + 导出 |
| `serve/app/controllers/base.py` | crud_router — 一行生成 5 个接口 |
| `serve/app/utils/query.py` | QueryHelper — 35 操作符 |
| `serve/app/utils/validator.py` | Validator — 16 规则 |
| `serve/app/utils/export_helper.py` | ExportHelper — XLSX 生成 + 分块 + 进度 |
| `serve/app/services/base.py` | BaseService + BaseDriver |
| `serve/app/deps.py` | 鉴权依赖（require_admin / require_perms） |
| `serve/app/queue.py` | Queue.push() — 入队客户端 |
| `serve/app/worker.py` | Worker — 队列消费 + 定时任务 |
| `admin/src/components/CrudTable/` | 声明式 CRUD 组件 |
| `admin/src/components/SettingForm/` | 多服务商配置组件 |
| `admin/src/api/crud.ts` | createCrudApi — CRUD API 工厂 |
| `admin/src/hooks/useExport.ts` | 导出 Hook |
| `serve/ASSETS.md` | 后端完整资产清单 |
| `admin/ASSETS.md` | 前端完整资产清单 |
| `serve/databases/init.sql` | 数据库初始化（一键建表 + 种子数据） |
