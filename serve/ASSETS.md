# 项目资产清单

> 随开发进度持续更新。每新增/修改文件时同步更新本文档。

---

## 技术栈

| 项 | 选型 | 版本 |
|----|------|------|
| 语言 | Python | 3.12 |
| Web 框架 | FastAPI + uvicorn | 0.115+ |
| ORM | SQLAlchemy 2 (async) | 2.0+ |
| 数据库 | PostgreSQL (asyncpg) | 16 |
| 缓存 | Redis (redis-py async) | 5.0+ |
| 密码加密 | bcrypt | 4.0+ |
| 配置管理 | pydantic-settings | 2.0+ |

---

## 目录结构

```
serve/
├── app/                            # 应用代码
│   ├── main.py                     # FastAPI 入口 + 全局异常处理
│   ├── config.py                   # 配置（Pydantic Settings）
│   ├── deps.py                     # 全局依赖注入（鉴权）
│   ├── models/                     # 数据模型
│   ├── logics/                     # 业务逻辑
│   ├── controllers/                # 路由
│   ├── middleware/                  # 中间件
│   ├── services/                   # 基础服务
│   └── utils/                      # 工具函数
├── databases/migrations/           # SQL 迁移文件
├── docs/                           # 文档
├── .env / .env.example             # 环境变量
├── requirements.txt                # Python 依赖
├── README.md                       # 项目说明
└── ASSETS.md                       # 本文件
```

---

## 文件清单

### 入口 & 配置

| 文件 | 说明 | 状态 |
|------|------|------|
| `app/main.py` | FastAPI 应用入口，中间件注册，全局异常处理，路由挂载 | ✅ |
| `app/config.py` | 环境变量配置类（APP_NAME/URL/KEY, DB, Redis, Token） | ✅ |
| `app/deps.py` | 鉴权依赖注入（require_auth / require_admin / require_client） | ✅ |
| `.env` | 环境变量（不入库） | ✅ |
| `.env.example` | 环境变量模板 | ✅ |
| `requirements.txt` | Python 依赖清单 | ✅ |

### Models — 数据模型（每表一个文件）

| 文件 | 表名 | 说明 | 状态 |
|------|------|------|------|
| `app/models/base.py` | — | SQLAlchemy DeclarativeBase | ✅ |
| `app/models/admin_user.py` | admin_users | 管理员用户（含 Status 枚举） | ✅ |
| `app/models/user.py` | users | 前端用户（含 Status 枚举） | ✅ |
| `app/models/setting.py` | settings | 系统配置（category+name 联合唯一） | ✅ |
| `app/models/admin_operation_log.py` | admin_operation_logs | 操作日志 | ✅ |
| `app/models/admin_login_log.py` | admin_login_logs | 登录日志（含 Status 枚举） | ✅ |
| `app/models/__init__.py` | — | 统一导出所有 Model | ✅ |

### Logics — 业务逻辑

| 文件 | 说明 | 状态 |
|------|------|------|
| `app/logics/base.py` | BaseLogic 基类：CRUD + 缓存 + QueryHelper + 钩子 + 断言 + 事务 + 软删除 | ✅ |
| `app/logics/admin_user.py` | 管理员逻辑：登录验证、密码管理、白名单配置 | ✅ |
| `app/logics/user.py` | 前端用户逻辑：登录验证、密码管理 | ✅ |
| `app/logics/setting.py` | 配置逻辑：get / set / get_all / set_many（全量缓存） | ✅ |
| `app/logics/admin_operation_log.py` | 操作日志：异步写入，密码脱敏 | ✅ |
| `app/logics/admin_login_log.py` | 登录日志：异步写入 | ✅ |

### Controllers — 路由

| 文件 | 路径前缀 | 说明 | 状态 |
|------|----------|------|------|
| `app/controllers/base.py` | — | crud_router()：自动生成 CRUD 四接口 + 鉴权控制 | ✅ |
| `app/controllers/admin/user.py` | /api/admin | 登录 / 续期 / 用户信息 / 改密 / 登出 + CRUD | ✅ |
| `app/controllers/admin/setting.py` | /api/admin | 配置读写（get 公开，set 需管理员） | ✅ |

### Middleware — 中间件

| 文件 | 说明 | 状态 |
|------|------|------|
| `app/middleware/operation_log.py` | 操作日志自动记录（admin POST 请求，含耗时） | ✅ |
| `app/middleware/cors.py` | CORS 跨域配置（支持 CORS_ORIGINS 环境变量） | ✅ |

### Services — 基础服务

| 文件 | 说明 | 状态 |
|------|------|------|
| `app/services/database.py` | SQLAlchemy 异步引擎 + Session 工厂 | ✅ |
| `app/services/redis.py` | Redis 连接 + cache_get/set/del/del_pattern/scan | ✅ |

### Utils — 工具函数

| 文件 | 说明 | 状态 |
|------|------|------|
| `app/utils/query.py` | QueryHelper：19 操作符 + $or/$and 嵌套 + keyword | ✅ |
| `app/utils/token.py` | Redis Session Token：create/verify/refresh/revoke | ✅ |
| `app/utils/response.py` | 统一响应格式：ok() / fail() | ✅ |
| `app/utils/password.py` | bcrypt 密码加密/校验 | ✅ |
| `app/utils/helpers.py` | 通用工具：get_client_ip / to_tree / has_chinese | ✅ |

### 数据库迁移

| 文件 | 说明 | 状态 |
|------|------|------|
| `databases/migrations/001_create_admin_users.sql` | 管理员用户表 | ✅ 已执行 |
| `databases/migrations/002_create_users.sql` | 前端用户表 | ✅ 已执行 |
| `databases/migrations/003_create_settings.sql` | 系统配置表 | ✅ 已执行 |
| `databases/migrations/004_create_admin_operation_logs.sql` | 操作日志表 | ✅ 已执行 |
| `databases/migrations/005_create_admin_login_logs.sql` | 登录日志表 | ✅ 已执行 |
| `databases/migrations/006_seed_admin_user.sql` | 种子数据：admin / admin123 | ✅ 已执行 |

### 文档

| 文件 | 说明 | 状态 |
|------|------|------|
| `README.md` | 项目说明、结构、快速开始 | ✅ |
| `ASSETS.md` | 资产清单（本文件） | ✅ |
| `docs/api-convention.md` | 前后端接口约定 + Filters DSL 完整文档 | ✅ |
| `docs/architecture.md` | 架构说明 | ⚠️ 待更新（Node.js 版本残留） |

---

## 接口清单

### Admin 端 — `/api/admin`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | /user/login | 公开 | 登录 |
| POST | /user/refreshToken | 公开 | 续期 access_token |
| GET | /user/info | require_admin | 当前用户信息 |
| POST | /user/changePassword | require_admin | 修改密码（改后踢所有 token） |
| POST | /user/logout | require_admin | 登出 |
| GET | /user/getList | require_admin | 用户列表（CRUD 自动） |
| GET | /user/getDetail | require_admin | 用户详情（CRUD 自动） |
| POST | /user/doEdit | require_admin | 创建/编辑用户（CRUD 自动） |
| POST | /user/doDelete | require_admin | 删除用户（CRUD 自动） |
| GET | /setting/get | 公开 | 读取全部配置 |
| POST | /setting/set | require_admin | 写入配置 |

### 公共

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /health | 健康检查 |
| GET | /docs | Swagger 文档（仅 APP_DEBUG=true） |

---

## 环境变量清单

| 变量 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| APP_NAME | str | base | 应用名称（Redis key 前缀） |
| APP_URL | str | http://localhost:3000 | 应用地址 |
| APP_KEY | str | — | 应用密钥（通用加密用） |
| APP_DEBUG | bool | false | 调试模式（开启 SQL 日志 + Swagger + 详细错误） |
| PORT | int | 3000 | 监听端口 |
| WORKERS | int | 0 | 工作进程数（0=自动） |
| DATABASE_HOST | str | localhost | PostgreSQL 地址 |
| DATABASE_PORT | int | 5432 | PostgreSQL 端口 |
| DATABASE_NAME | str | base | 数据库名 |
| DATABASE_USER | str | base_user | 数据库用户 |
| DATABASE_PASSWORD | str | — | 数据库密码 |
| DATABASE_SCHEMA | str | public | Schema |
| DATABASE_POOL_SIZE | int | 20 | 连接池大小 |
| DATABASE_MAX_OVERFLOW | int | 10 | 连接池溢出上限 |
| REDIS_HOST | str | localhost | Redis 地址 |
| REDIS_PORT | int | 6379 | Redis 端口 |
| REDIS_PASSWORD | str | — | Redis 密码 |
| REDIS_DB | int | 0 | Redis 数据库编号 |
| TOKEN_EXPIRES_IN | int | 7200 | access_token 有效期（秒） |
| REFRESH_TOKEN_EXPIRES_IN | int | 604800 | refresh_token 有效期（秒） |

---

## Redis Key 规则

| 模式 | 说明 | TTL |
|------|------|-----|
| `{APP_NAME}:token:{access_token}` | 用户会话 | TOKEN_EXPIRES_IN |
| `{APP_NAME}:refresh:{refresh_token}` | 续期凭证 | REFRESH_TOKEN_EXPIRES_IN |
| `{APP_NAME}:user_tokens:{scope}:{user_id}` | 用户活跃 token 集合 | REFRESH_TOKEN_EXPIRES_IN |
| `{cache_prefix}:{field}:{value}` | 业务数据缓存 | cache_ttl（默认 3600） |
| `settings:all` | 全量配置缓存 | 永不过期（写入时清除） |

---

## BaseLogic 能力清单

| 能力 | 方法 | 说明 |
|------|------|------|
| 分页列表 | `get_list()` | 含 QueryHelper + keyword + 排序 + 分页 |
| 详情 | `get_detail()` | 主键缓存 |
| 按字段查询 | `get_by_field()` | 多字段缓存（含敏感字段，内部用） |
| 创建 | `create()` | format_save_data → before_create → 入库 → 写缓存 |
| 编辑 | `modify()` | format_save_data → before_edit → 清旧缓存 → 更新 |
| 统一保存 | `save()` | 有主键=编辑，无主键=创建 |
| 删除 | `do_delete()` | 自动识别软删除 / 物理删除 |
| 业务断言 | `assert_true()` | 条件不满足抛 BizError |
| 事务 | `transaction()` | 事务包装 |
| 白名单 | `allowed_filters()` / `allowed_sorts()` | 查询安全边界 |
| 关键词搜索 | `keyword_fields()` | 多字段 OR 搜索 |
| 入库格式化 | `format_save_data()` | 空字符串过滤等 |
| 输出格式化 | `format_data()` | 敏感字段过滤 + 日期转换 |
| 缓存管理 | `_set_cache()` / `_clear_cache()` / `clear_all_cache()` | 多字段自动缓存 |

## QueryHelper 操作符清单（19 个）

| 分类 | 操作符 |
|------|--------|
| 比较 | eq, neq, gt, gte, lt, lte |
| 集合 | in, not_in |
| 范围 | between, not_between, date_between |
| 模糊 | like, not_like, prefix, suffix |
| 空值 | is_null, not_null, is_empty, not_empty |
| 逻辑 | $or, $and（任意嵌套） |
