# 后端资产清单

> Python 3.12 + FastAPI + SQLAlchemy 2 + PostgreSQL + Redis

---

## 数据库（10 张表 + 14 个迁移文件）

| 表 | 说明 |
|----|------|
| admin_users | 管理员用户 |
| users | 前端用户 |
| settings | 系统配置（category + name） |
| admin_operation_logs | 操作日志 |
| admin_login_logs | 登录日志 |
| menus | 菜单/权限（两级 + 按钮） |
| roles | 角色 |
| role_menus | 角色菜单关联 |
| admin_user_roles | 管理员角色关联 |
| messages | 系统消息 |
| files | 文件记录 |

## 核心架构

| 层 | 说明 |
|----|------|
| **Model** | 每表一个文件，Status 枚举 |
| **Logic（BaseLogic）** | CRUD + 缓存 + QueryHelper(35 操作符) + Validator(16 规则) + 钩子 + 断言 + 事务 + 软删除 + bindUserColumn + 导出 |
| **Controller（crud_router）** | 一行代码生成 CRUD 五接口（含 doExport）+ Depends 鉴权 + perms_prefix 权限 |
| **Service（BaseService）** | 配置驱动工厂 + ok/failed/error 状态模式 + 14 个驱动 |
| **Token** | Redis Session（access + refresh + 多点登录控制 + BRPOPLPUSH） |
| **RBAC** | 菜单即权限 + 角色 + 超管绕过 + Redis 缓存权限列表 |
| **Queue** | Redis BRPOPLPUSH + Semaphore 限流 + 延迟队列 + ACK 模式 |
| **Task** | 自动扫描注册 + asyncio 循环 + 防重复锁 + 优雅关闭 |

## Service 驱动（14 个，零 SDK 依赖）

| 服务 | 驱动 | 签名 |
|------|------|------|
| **SMS** | 阿里云 / 阿里云国际 / 腾讯云 / 华为云 | ACS3-HMAC-SHA256 / TC3 / WSSE |
| **Storage** | Local / 阿里云 OSS / 腾讯云 COS / 七牛 / S3 | HMAC-SHA1 / TC3 / QBox / AWS4 |
| **Notify** | Telegram / 钉钉 / 飞书 / 企微 / Email | Bearer / HMAC-SHA256 / SMTP |

## 文件系统

- path + url + platform 三字段（URL 直存，不运行时拼）
- 隐私文件强制 Local + 代理访问
- 删除时 DB + 存储双删（用 record.platform 定位）

## 队列 & 定时任务

| 组件 | 说明 |
|------|------|
| `app/queue.py` | Queue.push(job, data, delay) — 业务代码一行入队 |
| `app/worker.py` | python -m app.worker — 独立进程 |
| `app/tasks/*.py` | 定时任务（自动扫描，声明 interval） |
| `app/jobs/*.py` | 队列任务（自动扫描，声明 name） |

## 接口清单

### Admin 端

| 路径 | 说明 |
|------|------|
| POST /user/login | 登录（支持用户名/邮箱 + rate limit） |
| POST /user/refreshToken | 续期 |
| GET /user/info | 用户信息 |
| GET /user/menus | 菜单树 + 权限列表 |
| POST /user/assignRoles | 分配角色 |
| POST /user/updateProfile | 修改资料 |
| POST /user/changePassword | 改密（踢所有 token） |
| POST /user/logout | 登出 |
| CRUD /user/* | 用户管理 |
| CRUD /role/* | 角色管理 |
| GET /role/menuIds | 角色菜单 |
| POST /role/assignMenus | 分配权限 |
| CRUD /menu/* | 菜单管理 |
| GET /menu/tree | 菜单树 |
| GET /setting/get | 读取配置 |
| POST /setting/set | 写入配置 |
| CRUD /message/* | 系统消息 |
| GET /message/unreadCount | 未读数 |
| POST /message/markRead | 标记已读 |
| POST /file/upload | 上传文件 |
| POST /file/uploadImage | 上传图片 |
| POST /file/batchDelete | 批量删除（DB+存储双删） |
| CRUD /file/* | 文件管理 |
| CRUD /operationLog/* | 操作日志 |
| CRUD /loginLog/* | 登录日志 |
| GET /dashboard/stats | 统计数据 |
| GET /dashboard/system | 系统状态 |
| GET /dashboard/recent | 最近操作 |
| GET /export/progress | 导出进度查询 |
| GET /export/download | 导出文件下载（FileResponse） |
| POST /*/doExport | CRUD 导出入口（crud_router 自动生成） |

### Client 端

| 路径 | 说明 |
|------|------|
| POST /user/login | 登录 |
| POST /user/register | 注册 |
| POST /user/refreshToken | 续期 |
| GET /user/info | 用户信息 |
| POST /user/logout | 登出 |

### 公共

| 路径 | 说明 |
|------|------|
| GET /health | 健康检查 |
| GET /api/file/{id} | 隐私文件代理 |

## 文档清单

| 文件 | 说明 |
|------|------|
| docs/api-convention.md | 接口约定 + Filters DSL（35 操作符） |
| docs/rbac-design.md | RBAC 设计 |
| docs/rbac-implementation.md | RBAC 实现规划 |
| docs/service-design.md | Service 层设计（14 驱动零 SDK） |
| docs/file-system-design.md | 文件系统设计（三字段 + 双删） |
| docs/queue-task-design.md | 队列 & 定时任务设计（BRPOPLPUSH + ACK） |
