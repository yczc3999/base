# WP-00d2 — Runtime Lifespan、Health、Metrics 与 Artifact Factory

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00d2-runtime-lifespan-health.md`。
> 最后更新：2026-08-08 16:40 EDT。依赖：`WP-00d1-r5` 已接受。

## 1. 目标与用户价值

把 WP-00 已完成的 typed DB/Redis/Artifact/Observability primitives 接入 FastAPI 进程生命周期，
形成可配置、可观测、可安全关闭的 V2 基础运行时。启动后必须能区分“进程活着”“关键依赖可用”
和“Cache 降级”，并提供受控 metrics 与后台运行状态；不得启动 market ingest、AI 或 execution loop。

## 2. 必读与确认决策

- `serve/docs/v2-implementation-contract.md` §3、§12–§15。
- `serve/docs/performance-cache-database-design.md` 的事实源、连接预算、Redis 故障语义。
- `serve/docs/ai-observability-replay-design.md` 的日志/Trace/指标边界。
- 本任务确认：DB、Control Redis、Artifact Store 是 readiness **required**；Cache Redis 失败只标
  `degraded`，不使 readiness 失败。静态配置/driver 构造错误阻止启动；运行依赖暂时不可达时应用
  可启动但 `/health/ready` 返回 503。

## 3. 允许文件与逐文件合同

生产文件（最多 7 个）：

| 文件 | 唯一职责 |
|---|---|
| `serve/app/config.py` | 新增 `RUNTIME_HEALTH_TIMEOUT_S`（默认 2.0，`gt=0`）；不得加入业务配置 |
| `serve/.env.example` | 只补该静态变量说明，不含真实地址/凭据 |
| `serve/app/services/redis_cache.py` | 补 `ping()` 与 fail-safe `health()`；连接错误返回 `{ok:false,latency_ms:null}`，不得泄 URL/密码 |
| `serve/app/services/artifact_store/factory.py` | 唯一 ArtifactStore factory：local 构造 Local driver；s3 复用现有 `build_s3_artifact_driver`；支持注入 S3 client；import 零网络 |
| `serve/app/services/runtime.py` | `RuntimeResources`、构造/健康快照/关闭；依赖可注入；并发有界探测；只返回安全状态 |
| `serve/app/controllers/admin/trading.py` | 只读、薄 Controller；`GET /trading/runtime`，强制 `admin:monitor:list` 权限，返回安全快照 |
| `serve/app/main.py` | 接入 logging/tracing/runtime lifespan、health/metrics、一个 Trading Admin router；保持 Base 路由兼容 |

直接测试：

```text
serve/tests/trading/test_v2_artifact_factory.py
serve/tests/trading/test_v2_runtime_lifecycle.py
serve/tests/trading/test_v2_router_registration.py
```

交付文件：

```text
serve/docs/manifests/wp-00d2-runtime-lifespan-health.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 observability primitives、DB/control Redis、Artifact drivers/service、legacy Controller/Logic/
Model、Alembic、Admin 前端、V1。禁止新增后台循环、业务表、策略、下单或动态 secret。

## 4. Runtime 精确合同

### 4.1 Factory 与资源所有权

1. `build_artifact_store(cfg, *, s3_client=None)`：`local` → `LocalArtifactDriver(root)`；`s3` →
   `build_s3_artifact_driver(cfg, client=s3_client)`；其他值 fail-fast。统一返回
   `ArtifactStore(driver, cfg)`，不得复制 S3 Config/retry/signature 逻辑。
2. 每次 lifespan 构造新的 `ControlRedisClient`、`CacheRedisClient`、ArtifactStore；不复用已关闭单例。
   DB 使用 `engines` 的 `api` profile。构造函数必须允许测试注入 fake DB probe/Redis/Artifact。
3. import `main.py`、factory 或 runtime 不得发 DB/Redis/S3/OTLP 网络请求；网络只发生在 lifespan
   或显式 health 调用。

### 4.2 Health 与状态

1. 健康检查并发执行 DB `SELECT 1`、Control Redis ping、Cache Redis ping、Artifact `health()`；同步
   Artifact 调用用 `asyncio.to_thread`，每项受 `RUNTIME_HEALTH_TIMEOUT_S` 约束。
2. 快照固定结构：

```json
{
  "status": "ready|unready",
  "components": {
    "database": {"state": "ready|unready"},
    "control_redis": {"state": "ready|unready"},
    "cache_redis": {"state": "ready|degraded"},
    "artifact_store": {"state": "ready|unready", "driver": "local|s3"}
  },
  "degraded": ["cache_redis"],
  "checked_at": "UTC RFC3339"
}
```

   禁止输出异常 message、traceback、DSN、host、port、namespace、文件路径、bucket、endpoint、
   credential、pool 对象或 Artifact `detail`。
3. DB/Control/Artifact 任一失败或超时 → `status=unready`；Cache 失败/超时 → 仍可 ready，但
   `cache_redis.state=degraded` 且进入 `degraded`。未知异常也映射到固定状态，不泄原文。
4. Runtime 初始/closing/closed 均不可报告 ready；并发 health 调用不得修改资源身份或重复构造。

### 4.3 Lifespan 与关闭

1. startup 顺序：`configure_logging` → `configure_tracing` → 构造 runtime → 首次 health snapshot →
   写入 `app.state.trading_runtime`。不得吞静态配置/构造错误；依赖不可达只记录安全 reason code。
2. shutdown 先禁止 ready，然后即使某一 close 失败也继续尝试：Artifact sync close、Cache Redis、
   Control Redis、DB `dispose_engines()`、legacy `close_redis()`、`shutdown_tracing()`；每项至多一次，
   重复 shutdown 幂等。日志只记录 component/reason code，不记录原始异常文本。
3. 不改变 legacy SEO/RBAC/CRUD/CORS/static routes；不启动 ingest、AI、replay、execution 或 worker。

## 5. HTTP 合同

路由必须在 Web SEO catch-all 前注册：

- `GET /health` 与 `GET /health/live`：纯 liveness，进程可服务即 200 `{"status":"ok"}`，不做 I/O。
- `GET /health/ready`：刷新安全快照；required 全过返回 200，任一 required 失败返回同结构 503。
- `GET /metrics`：`PROMETHEUS_ENABLED=true` 时返回 `render_metrics()` 的 bytes 与原 content-type；
  false 时返回 404。不得使用全局默认 Prometheus registry，也不得出现业务 ID/secret。
- `GET /api/admin/trading/runtime`：复用 runtime 最近安全快照，必须通过
  `require_perms("admin:monitor:list")`；不得在 Controller 重做 health 逻辑。

全局异常处理生产模式继续隐藏 traceback；不得把新健康/metrics 错误包装为 HTTP 200 的 Base
业务错误响应。

## 6. 必测证据

1. Artifact factory：local/s3 分支、S3 client 注入、exact config 传递、非法 driver；import/factory
   注入路径零网络；不复制凭据。
2. Runtime：全绿 ready；DB/Control/Artifact 分别 fail/timeout → unready；Cache fail/timeout → ready+
   degraded；响应字段精确且敏感 marker/异常 message/路径/bucket/URL 全不出现。
3. Lifespan：初始化顺序、每类资源构造一次；正常与中途失败均逆序尽力关闭；重复 shutdown 幂等；
   DB engines 最终 `engine_count()==0`；无 loop/task 泄漏。
4. Router：三个 public health 状态码、metrics on/off/content-type、Admin 401/403 与有权限 200；所有
   legacy 关键路由仍注册；Web SEO catch-all 不遮蔽 health/metrics。
5. Observability：startup/shutdown 日志经 redactor；OTEL disabled 不建 exporter；enabled 参数完全来自
   Settings；不得产生第二个 V2 handler/provider 泄漏。
6. 原 WP-00 tests 与全量回归通过；测试不得要求真实 PostgreSQL/Redis/S3/OTLP 服务。

## 7. 验收、非目标与交付

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests
.venv/bin/pytest -q tests/trading/test_v2_artifact_factory.py
.venv/bin/pytest -q tests/trading/test_v2_runtime_lifecycle.py
.venv/bin/pytest -q tests/trading/test_v2_router_registration.py
.venv/bin/pytest -q tests/trading
.venv/bin/pytest -q
git diff --check
```

创建且只创建 `serve/docs/manifests/wp-00d2-runtime-lifespan-health.md`，记录修改文件、真实测试、
依赖故障矩阵、秘密泄漏断言、blocker、回滚和 SHA；更新两个索引为 `DONE（待审）`。不得创建
WP-01A、提交或推送。回滚恢复允许文件并删除 00d2 manifest；无迁移/业务数据副作用。
