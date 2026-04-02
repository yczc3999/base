# Base Platform — 后端服务

基于 **FastAPI + SQLAlchemy 2 + PostgreSQL + Redis** 的通用后台基础平台。

## 技术栈

- Python 3.12 + FastAPI + uvicorn（异步高性能）
- SQLAlchemy 2（async，Mapped 类型注解）
- PostgreSQL（asyncpg 驱动）
- Redis（异步，缓存 + token_version 校验）
- JWT 认证（python-jose）
- bcrypt 密码加密

## 项目结构

```
serve/
├── app/
│   ├── main.py                         # FastAPI 入口 + 全局异常处理
│   ├── config.py                       # 配置（Pydantic Settings，读 .env）
│   ├── models/                         # 数据模型（每表一个文件）
│   │   ├── base.py                     #   DeclarativeBase
│   │   ├── admin_user.py               #   管理员用户
│   │   ├── user.py                     #   前端用户
│   │   ├── setting.py                  #   系统配置
│   │   ├── admin_operation_log.py      #   操作日志
│   │   └── admin_login_log.py          #   登录日志
│   ├── logics/                         # 业务逻辑层
│   │   ├── base.py                     #   BaseLogic（CRUD + 缓存 + 钩子 + 断言 + 事务）
│   │   ├── admin_user.py              #   管理员逻辑（登录/改密/token 管理）
│   │   ├── user.py                    #   前端用户逻辑
│   │   ├── setting.py                 #   配置逻辑（get/set/get_all/set_many）
│   │   ├── admin_operation_log.py     #   操作日志（异步写入）
│   │   └── admin_login_log.py         #   登录日志（异步写入）
│   ├── controllers/                    # 路由层
│   │   ├── base.py                    #   crud_router() — 自动生成 CRUD 四接口
│   │   └── admin/                     #   admin 端路由
│   │       ├── user.py                #     登录/改密/登出 + CRUD
│   │       └── setting.py             #     配置读写
│   ├── middleware/                      # 中间件
│   │   ├── auth.py                    #   JWT 鉴权（Redis 缓存 token_version）
│   │   └── operation_log.py           #   操作日志自动记录
│   ├── services/                       # 基础服务
│   │   ├── database.py                #   SQLAlchemy async engine + session
│   │   └── redis.py                   #   Redis 连接 + 缓存工具函数
│   └── utils/                          # 工具
│       ├── query.py                   #   QueryHelper — 查询 DSL（19 操作符 + $or/$and 嵌套）
│       ├── response.py                #   ok() / fail()
│       ├── token.py                   #   JWT 签发/验证
│       └── password.py                #   bcrypt hash/verify
├── databases/
│   └── migrations/                     # SQL 迁移文件
├── docs/
│   ├── api-convention.md              # 前后端接口约定（含完整 Filters DSL 文档）
│   └── architecture.md                # 架构说明
├── .env.example                        # 环境变量模板
├── requirements.txt                    # Python 依赖
└── README.md                           # 本文件
```

## 架构分层

```
Request → AuthMiddleware → OperationLogMiddleware → Router → Logic → Model → DB
                                                              ↕
                                                            Redis（缓存）
```

| 层 | 职责 | 对应目录 |
|----|------|----------|
| Router | 接收请求，参数校验，返回响应 | `controllers/` |
| Logic | 业务逻辑，CRUD，缓存，钩子 | `logics/` |
| Model | 数据结构定义，字段约束 | `models/` |
| Service | 数据库/Redis 连接管理 | `services/` |
| Middleware | 鉴权，操作日志 | `middleware/` |

## 核心设计

### BaseLogic — 零代码 CRUD

继承 `BaseLogic` 并配置类属性，即可获得完整的 CRUD + 缓存 + 查询能力：

```python
class AdminUserLogic(BaseLogic):
    model = AdminUser
    cache_prefix = "admin_user"
    cache_fields = ["username"]
    except_keys = ["password"]

    def allowed_filters(self):
        return ["id", "username", "status", "created_at"]

    def allowed_sorts(self):
        return ["id", "created_at", "updated_at"]

    def keyword_fields(self):
        return ["username", "nickname", "email"]
```

自动获得：
- `get_list()` — 分页列表（Filters DSL + keyword + 排序）
- `get_detail()` — 详情查询（主键缓存）
- `create()` / `modify()` / `save()` — 创建/编辑
- `do_delete()` — 删除（自动识别软删除）
- 多字段 Redis 缓存（主键 + 自定义字段）
- 生命周期钩子（before_create / before_edit / before_delete / after_delete）
- 业务断言 `assert_true()`
- 事务包装 `transaction()`

### QueryHelper — 查询 DSL

19 个操作符 + `$or`/`$and` 任意嵌套 + keyword 多字段搜索。

详见 [docs/api-convention.md](docs/api-convention.md) 第四章。

### Model 状态枚举

每个 Model 通过内部 `IntEnum` 定义状态，代码中使用枚举而非魔法数字：

```python
class AdminUser(Base):
    class Status(IntEnum):
        DISABLED = 0   # 禁用
        ACTIVE = 1     # 正常

# 使用
if user.status != AdminUser.Status.ACTIVE:
    ...
```

## 快速开始

```bash
# 1. 安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入数据库、Redis、JWT 配置

# 3. 创建数据库表
# 执行 databases/migrations/ 下的 SQL 文件

# 4. 启动
uvicorn app.main:app --host 0.0.0.0 --port 3000

# 5. 访问
# API 文档：http://localhost:3000/docs（仅 APP_DEBUG=true 时可用）
# 健康检查：http://localhost:3000/health
```

## 文档

- [前后端接口约定](docs/api-convention.md) — CRUD 接口规范 + Filters DSL 完整文档
- [架构说明](docs/architecture.md) — 分层设计 + 缓存策略
