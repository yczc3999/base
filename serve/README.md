# Base Platform — 后端服务

基于 **FastAPI + SQLAlchemy 2 + PostgreSQL + Redis** 的通用后台基础平台。

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

## Polymarket V2 设计文档

- [平台实现设计](docs/polymarket-v2-platform-design.md)：配置、后台页面、业务追踪与发布边界。
- [AI 可观察与回放设计](docs/ai-observability-replay-design.md)：逐次模型调用、工具、成本和实验回放。
- [Polymarket 接入实现设计](docs/polymarket-integration-design.md)：市场发现、行情、鉴权、下单、对账与结算。
- [性能、缓存与数据库设计](docs/performance-cache-database-design.md)：实时路径、缓存层、分区索引、队列、容量和压测。
- [V2 逐文件实施合同](docs/v2-implementation-contract.md)：文件职责、依赖方向、工作包与验收证据。
- [当前任务与交接状态](docs/tasks/README.md)：实现者只执行这里指向的任务；用户回复“完成”后自动审查并生成下一任务。

## Polymarket V2 当前状态（2026-08-15）

本节是面向开发者的状态账本；业务规范仍以
`/code/pollymarket/docs/v2/ARCHITECTURE.md` 为准，当前执行合同与验收状态分别见
[`docs/tasks/README.md`](docs/tasks/README.md) 和
[`docs/manifests/README.md`](docs/manifests/README.md)。

| 口径 | 当前状态 |
|---|---|
| 已接受工程里程碑 | `WP-00`～`WP-07A` |
| Admin 页面 | `WP-07B` 已实现但浏览器硬门仍需整改/复验 |
| 常驻运行时 | `WP-07C` 进行中；outbox 与 Stage 0/1 骨架已接入，Stage 2～4 尚未接通 |
| 运行数据 | 2026-08-15 快照：259 个 universe frame 中 258 `FAILED`、1 `OPEN`；`pm_markets=0` |
| AI/决策/执行事实 | `ai_invocations/forecast_submissions/trade_decisions/executions/metric_runs` 均为 0 |
| 稳定性与上线 | `WP-08`、shadow qualification、canary、live 均未完成 |

当前产品完整度不能按“文件数量”计算：底层领域对象和测试覆盖已经较广，但真实常驻链仍被
`events_open → frame_page_overflow` 阻塞，尚未产生
`COMPLETE frame → market → R0 → opportunity → forecast → decision → shadow execution → evaluation`
的端到端事实。`python -m runtimes.trading --dry-run` 打印出 runtime 名称只证明注册成功；
`cognition/evaluation/execution/reconciliation/replay` 当前仍有 idle runner，不能登记为已运行。

当前恢复顺序固定为：

1. 修复 Gamma universe 分页/终止和失败传播，得到可重放的 `COMPLETE` frame 与非零 market；
2. 接通模型网关和 Stage 2～4，产出第一条完整 shadow 链；
3. 把组合降险候选做成版本化 challenger，在 untouched forward cohort 中评价；
4. 完成真 PostgreSQL、迁移往返、真实浏览器、告警、性能和 soak 证据后再进入资格评估。

## Polymarket V2 产品路线：AI 预测 + 组合降险

V2 的利润路线是用 AI 对规则清晰、具有真实容量、决策窗口为小时至天的市场形成
**市场盲、可回放、可证伪**的 payout belief，再在揭价后相对真实可成交盘口计算
全成本 edge。系统不移植已经被实盘证伪的 BTC 5 分钟 Gaussian 动量策略，也不把
工程测试通过当作经济有效性的证据。

### AI 预测与调整纪律

- AI 先在看不到 market/crowd/odds 的分支中形成局部联合 belief，并不可变提交
  `blind_submission`；揭价后由独立决策分支使用冻结 shrinkage policy 合成
  `Q_decision/U_decision`。
- 新 evidence、规则或 schema 变化、forecast lease 到期、thesis invalidation 才创建
  新 cognition episode。纯 quote、depth、cost 或 position 变化复用仍有效 belief，
  只重新估值和选择动作，禁止让 AI 随盘口反复改口。
- 模型、prompt、权重、阈值或成本政策的任何调整都创建新 strategy、新 cohort 和新
  forward holdout；只允许通过 `train → validation → untouched forward holdout → shadow
  → canary → live` 晋级，旧样本与旧预测不回写。

### 从 Gaussian LIVE 失败中保留的对冲方法

Gaussian LIVE 的第一腿没有覆盖市场价格、点差和费用，但第二腿在真实两腿样本中
证明能够显著减亏。V2 只吸收这项**持仓后增量决策方法**，不复用 Gaussian 信号、
常量、状态机或代码：

1. 以已经认证并入账的实际 fill 数量、现金和费用作为持仓真相，不使用计划成交量；
2. 对同一持仓统一枚举 `HOLD / SELL_TO_REDUCE / SELL_TO_CLOSE / FLIP`，以及在
   capability 允许时买入相反 token 形成 paired inventory；
3. 使用最新可成交 bid/ask、真实深度、费用、滑点、资本占用和部分/失败成交模型，
   比较候选 action set 与 `NO_ACTION` 的完整终值现金流；
4. 同时保存 `robust_EV`、最坏损失、CVaR、capital-days、locked-payout floor 和
   HOLD 反事实，按组合边际效用裁决，而不是机械补第二腿；
5. `REDUCE/CLOSE/FLIP-close` 可按风险改善进入 `RISK_REVIEW`，不被 standalone
   positive-EV 门阻断；买入相反 token 仍是新增资本动作，必须完整通过 G7A/G7B，
   不得以“对冲”为名绕过增仓 Gate；
6. 最终 action set 可以包含多条 leg，但交易所执行不被假定为原子操作；漏腿、拒单、
   partial fill 和 reconciliation 都必须进入账本与评价。

### 资金目标

历史亏损作为不可修改的资金事实保存，但不进入新仓位 sizing，也不形成“必须回本”的
追损目标。V2 从当前资本重新按 `NO_ACTION / WAIT` 基准优化未来风险调整后的
`system_net_profit`；对冲负责改善已有仓位的终值分布，AI 预测负责证明新增风险是否值得。

业务语义以 `/code/pollymarket/docs/v2/ARCHITECTURE.md` 为唯一权威；本节是该规范中
blind forecast、双时钟、action set、G7A/G7B、`RISK_REVIEW` 和分级实盘规则的 README 摘要。

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
| [docs/polymarket-v2-platform-design.md](docs/polymarket-v2-platform-design.md) | Polymarket V2 可配置、可观察、可回溯平台实现设计 |
| [docs/ai-observability-replay-design.md](docs/ai-observability-replay-design.md) | AI 每次调用的输入、工具、输出、成本、校验、追踪与历史回放设计 |
