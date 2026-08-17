# Base Platform — 后端服务

基于 **FastAPI + SQLAlchemy 2 + PostgreSQL + Redis** 的通用后台基础平台。

> 本目录只维护通用基础能力。任何具体项目必须先 FORK/CLONE 本仓库，再在自己的仓库中开发；禁止把具体产品业务直接写入 Base。

## 技术栈

- Python 3.12 + FastAPI + uvicorn（异步高性能）
- SQLAlchemy 2（async，Mapped 类型注解）
- PostgreSQL（asyncpg 驱动）
- Redis（缓存 + Session Token + 分布式锁 + 队列）
- bcrypt 密码加密

## 架构分层

```
Request → AuthMiddleware → OperationLogMiddleware → Router → Logic → Model → DB
                                                              ↕
                                                     Redis（缓存 + Token）
                                                              ↕
                                                     Service（SMS / Storage / Notify）
```

| 层 | 职责 | 对应目录 |
|----|------|----------|
| Controller | 接收请求，参数校验，返回响应 | `controllers/` |
| Logic（BaseLogic） | 业务 CRUD + 缓存 + 查询 + 校验 + 钩子 | `logics/` |
| Model | 数据结构，字段约束，Status 枚举 | `models/` |
| Service（BaseService） | 配置驱动工厂 + 14 驱动（零 SDK） | `services/` |
| Deps | 鉴权（require_admin / require_client / require_perms） | `deps.py` |
| Middleware | JWT 注入 + 操作日志自动记录 | `middleware/` |

## 核心设计

### BaseLogic — 零代码 CRUD

```python
class OrderLogic(BaseLogic):
    model = Order
    cache_prefix = "order"
    except_keys = ["internal_note"]

    def allowed_filters(self):
        return ["id", "status", "created_at"]

    def keyword_fields(self):
        return ["order_no", "customer_name"]
```

自动获得：get_list（分页 + QueryHelper 35 操作符 + keyword + 排序）、get_detail（主键缓存）、save（创建/编辑）、do_delete（软删除感知）、Validator（16 规则）、生命周期钩子、事务包装、bindUserColumn。

### crud_router — 一行 CRUD

```python
router.include_router(crud_router("order", OrderLogic, perms_prefix="admin:order"))
```

自动生成 getList / getDetail / doEdit / doDelete 四个接口 + 权限校验。

### Service 层 — 14 驱动零 SDK

```python
result = await sms_service.send_code(db, phone, code)
if sms_service.failed:
    logger.error(sms_service.error)
```

| 服务 | 驱动 |
|------|------|
| SMS | 阿里云 / 阿里云国际 / 腾讯云 / 华为云 |
| Storage | Local / 阿里云 OSS / 腾讯云 COS / 七牛 / S3 |
| Notify | Telegram / 钉钉 / 飞书 / 企微 / Email |

所有驱动纯 HTTP + 手写签名，零 SDK 依赖。错误状态存实例（ok/failed），调用方决定处理方式。

### Token — Redis Session

access_token（15 分钟）+ refresh_token（7 天），用户信息存 Redis，支持多点登录控制、踢人、续期时恢复完整 user_info。不用 JWT。

### RBAC — 菜单即权限

菜单表 type：0=目录 / 1=菜单 / 2=按钮（权限点）。角色关联菜单，权限列表 Redis 缓存。`require_perms("admin:user:edit")` 一行校验。超管自动绕过。

### 队列 & 定时任务

```python
# 入队
await Queue.push("send_sms", {"phone": "13800138000", "code": "1234"})

# 延迟入队
await Queue.push("send_email", data, delay=60)
```

Worker 独立进程（`python -m app.worker`），含：
- **QueueConsumer**：Redis BRPOPLPUSH + ACK 模式（防丢失）+ Semaphore(10) 限流
- **TaskScheduler**：自动扫描 `app/tasks/*.py`，声明 interval 即运行，SET NX 防重复锁
- 优雅关闭：SIGINT/SIGTERM → 等待运行中任务完成

### 数据导出

```python
class OrderLogic(BaseLogic):
    model = Order

    def export_header_map(self):
        return {"id": "ID", "order_no": "订单号", "status": "状态", "created_at": "创建时间"}

    def format_export_row(self, row, context=None):
        row["status"] = "已付款" if row.get("status") == 1 else "待付款"
        return row
```

Logic 覆写 `export_header_map()` 即启用导出。crud_router 自动生成 `POST /doExport` 接口。

流程：前端触发 → 推队列 → Worker 异步生成 XLSX（openpyxl write_only 常量内存）→ Redis 进度 → 前端 Blob 下载。超过 20 万行自动拆分多文件打包 ZIP。

### 文件系统

- path + url + platform 三字段存储（URL 直存，不运行时拼接）
- 隐私文件强制 Local + `/api/file/{id}` 代理访问
- 删除时 DB + 存储双删（用 record.platform 定位驱动）
- _safe_path() 防路径穿越

## 项目结构

```
serve/
├── app/
│   ├── main.py                         # FastAPI 入口
│   ├── config.py                       # Pydantic Settings
│   ├── deps.py                         # 鉴权依赖（AuthInfo + require_*）
│   ├── queue.py                        # Queue.push() — 入队客户端
│   ├── worker.py                       # Worker 独立进程
│   ├── models/                         # 数据模型（11 张表）
│   ├── logics/                         # 业务逻辑层
│   │   ├── base.py                     #   BaseLogic
│   │   └── ...                         #   各业务 Logic
│   ├── controllers/                    # 路由层
│   │   ├── base.py                     #   crud_router() 工厂
│   │   ├── admin/                      #   Admin 端路由
│   │   └── client/                     #   Client 端路由
│   ├── services/                       # 服务层
│   │   ├── base.py                     #   BaseService + BaseDriver
│   │   ├── database.py                 #   SQLAlchemy async engine
│   │   ├── redis.py                    #   Redis 连接 + 缓存工具
│   │   ├── sms/                        #   短信服务（4 驱动）
│   │   ├── storage/                    #   存储服务（5 驱动）
│   │   └── notify/                     #   通知服务（5 驱动）
│   ├── tasks/                          # 定时任务（自动扫描）
│   │   ├── base.py                     #   BaseTask
│   │   ├── cleanup_expired.py          #   清理过期数据（每小时）
│   │   └── system_monitor.py           #   系统监控（每分钟）
│   ├── jobs/                           # 队列任务（自动扫描）
│   │   ├── base.py                     #   BaseJob
│   │   └── send_sms.py                 #   异步发送短信
│   ├── middleware/                      # 中间件
│   │   ├── auth.py                     #   Token 鉴权
│   │   └── operation_log.py            #   操作日志自动记录
│   └── utils/                          # 工具
│       ├── query.py                    #   QueryHelper（35 操作符）
│       ├── validator.py                #   Validator（16 规则）
│       ├── response.py                 #   ok() / fail()
│       ├── token.py                    #   Redis Session Token
│       └── password.py                 #   bcrypt
├── databases/migrations/               # SQL 迁移文件
├── docs/                               # 设计文档
├── .env.example
├── requirements.txt
└── README.md
```

## 快速开始

```bash
# 1. 安装依赖
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置
cp .env.example .env
# 编辑 .env：数据库 + Redis + APP_NAME

# 3. 建表
# 执行 databases/migrations/ 下的 SQL

# 4. 启动 API
uvicorn app.main:app --host 0.0.0.0 --port 3000

# 5. 启动 Worker（队列 + 定时任务）
python -m app.worker

# API 文档：http://localhost:3000/docs（APP_DEBUG=true）
# 健康检查：http://localhost:3000/health
```

## 文档

| 文件 | 说明 |
|------|------|
| [docs/api-convention.md](docs/api-convention.md) | 接口约定 + Filters DSL（35 操作符） |
| [docs/rbac-design.md](docs/rbac-design.md) | RBAC 设计 |
| [docs/rbac-implementation.md](docs/rbac-implementation.md) | RBAC 实现规划 |
| [docs/service-design.md](docs/service-design.md) | Service 层设计 |
| [docs/file-system-design.md](docs/file-system-design.md) | 文件系统设计 |
| [docs/queue-task-design.md](docs/queue-task-design.md) | 队列 & 定时任务设计 |
