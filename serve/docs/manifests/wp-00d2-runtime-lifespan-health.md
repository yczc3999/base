# COMPLETION MANIFEST — WP-00d2 · Runtime Lifespan、Health、Metrics 与 Artifact Factory

- Work package: `WP-00` 子任务 `WP-00d2`
- 状态: **DONE（待审）**
- 日期: 2026-08-09
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00d1-r5`（ACCEPTED，134 log + 88 trace + 93 config + 33 metric、608 trading、819 full）
- 规范依据: `serve/docs/tasks/wp-00d2-runtime-lifespan-health.md`；`serve/docs/v2-implementation-contract.md` §3/§8/§12–§15；`serve/docs/performance-cache-database-design.md` §11–§12；`serve/docs/ai-observability-replay-design.md`
- 验收命令: 见 §3

---

## 1. 修改文件（生产/基础设施 7，恰为上限）

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/config.py` | 修改 | 新增 `RUNTIME_HEALTH_TIMEOUT_S`（默认 2.0，`gt=0`）+ `_validate_observability` 校验 |
| `serve/.env.example` | 修改 | 补 `RUNTIME_HEALTH_TIMEOUT_S=2.0` 说明（无真实地址/凭据） |
| `serve/app/services/redis_cache.py` | 修改 | 新增 `ping()` + fail-safe `health()`（成功 `{ok,latency_ms}`；连接错误 `{ok:false,latency_ms:null}`，不泄 URL/密码/namespace） |
| `serve/app/services/artifact_store/factory.py` | **新增** | `build_artifact_store(cfg, *, s3_client=None)`：local→`LocalArtifactDriver(root)`、s3→`build_s3_artifact_driver(cfg, client=s3_client)`、非法 fail-fast；统一 `ArtifactStore(driver, cfg)`；import 零网络 |
| `serve/app/services/runtime.py` | **新增** | `RuntimeResources`（db_probe/control/cache/artifact/db_engines 可注入）+ `build_runtime_resources`（缺省构造真实 client）；`health_snapshot` 并发有界、固定结构、仅安全状态；`close` 逆序尽力幂等；初始/closing/closed 不可 ready |
| `serve/app/controllers/admin/trading.py` | **新增** | 薄只读 `GET /trading/runtime`，`require_perms("admin:monitor:list")`，复用 runtime 最近安全快照，不重做 health |
| `serve/app/main.py` | 修改 | lifespan 接入 logging→tracing→runtime→首次快照→`app.state.trading_runtime`；shutdown runtime→legacy close_redis→shutdown_tracing；`/health/ready` 改经 runtime snapshot（required 全过 200 / 同结构 503）；新增 `/metrics`（PROMETHEUS_ENABLED 控制 200/404，render_metrics bytes+content-type）；include trading router（SEO 前） |

测试：`test_v2_artifact_factory.py`（新 7）、`test_v2_runtime_lifecycle.py`（新 15）、
`test_v2_router_registration.py`（新 11）。交付：本 manifest + 两个索引。

范围外未动：observability primitives、DB/control Redis、Artifact drivers/service、legacy
Controller/Logic/Model、Alembic、Admin 前端、V1。**未遇 `BLOCKED_CONTRACT`**：不新增后台循环/
业务表/策略/下单/动态 secret。

---

## 2. 实现内容

### 2.1 Factory 与资源所有权（§4.1）

- `build_artifact_store`：`local` → `LocalArtifactDriver(root)`；`s3` → 复用
  `build_s3_artifact_driver(cfg, client=s3_client)`（不复制 S3 Config/retry/signature 逻辑）；
  其他值 fail-fast。统一返回 `ArtifactStore(driver, cfg)`。
- `build_runtime_resources`：缺省构造新的 ControlRedisClient/CacheRedisClient/ArtifactStore；
  DB probe 用 `db_engines.engine("api")` 跑 `SELECT 1`。构造器可注入 fake DB probe/Redis/Artifact/
  db_engines。
- import `main.py`/factory/runtime 零网络（S3 client 由 builder 调用时创建；DB engine 惰性；
  legacy redis 惰性）。

### 2.2 Health 与状态（§4.2）

- 并发探测 DB `SELECT 1`、Control Redis `health()`、Cache Redis `health()`、Artifact `health()`
  （sync 经 `asyncio.to_thread`）；每项受 `RUNTIME_HEALTH_TIMEOUT_S` 约束（`asyncio.wait_for`，
  超时/未知异常映射到固定状态）。
- 快照固定结构：`{status, components{database, control_redis, cache_redis,
  artifact_store{state, driver}}, degraded, checked_at}`；DB/Control/Artifact 任一失败或超时 →
  `unready`；Cache 失败/超时 → 仍 `ready` 但 `cache_redis=degraded` 且进入 `degraded`。
- 禁止输出异常 message/traceback/DSN/host/port/namespace/文件路径/bucket/endpoint/credential/
  pool/Artifact detail。
- `mark_started` 后才可 ready；`close` 置 `_closed` 后不可 ready；并发 health 只读，不修改资源
  身份、不重复构造。

### 2.3 Lifespan 与关闭（§4.3）

- startup：`configure_logging` → `configure_tracing`（OTEL disabled 零 exporter）→
  `build_runtime_resources` → 首次 `health_snapshot` → `mark_started` →
  `app.state.trading_runtime`。静态配置/构造错误不吞（阻止启动）；依赖不可达只记录安全状态。
- shutdown：先经 runtime.close() 禁 ready，逆序尽力关闭 Artifact sync → Cache → Control → DB
  dispose；随后 legacy `close_redis()`、`shutdown_tracing()`；每项至多一次、重复幂等；日志只记
  component/原因，不记原始异常文本。

### 2.4 HTTP 合同（§5）

- `/health`、`/health/live`：纯 liveness `{"status":"ok"}`，不做 I/O。
- `/health/ready`：刷新 runtime 安全快照；required 全过 200，任一 required 失败同结构 503；
  runtime 未设时 503 unready。
- `/metrics`：`PROMETHEUS_ENABLED=true` 返回 `render_metrics()` bytes + 原 content-type；
  false 返回 404。用显式 MetricCatalog registry（非全局默认 Prometheus registry），不含业务
  ID/secret。
- `/api/admin/trading/runtime`：复用 `app.state.trading_runtime` 最近安全快照；强制
  `admin:monitor:list`；Controller 不重做 health 逻辑。
- 路由均在 Web SEO catch-all 前注册；legacy SEO/RBAC/CRUD/CORS/static routes 未改变。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests
# → exit 0

# 2) artifact factory（新增 7）
.venv/bin/pytest -q tests/trading/test_v2_artifact_factory.py
# → 7 passed in 0.10s

# 3) runtime lifecycle（新增 15）
.venv/bin/pytest -q tests/trading/test_v2_runtime_lifecycle.py
# → 15 passed in 0.06s

# 4) router registration（新增 11）
.venv/bin/pytest -q tests/trading/test_v2_router_registration.py
# → 11 passed in 0.35s

# 5) 定向三项合计
.venv/bin/pytest -q tests/trading/test_v2_artifact_factory.py \
  tests/trading/test_v2_runtime_lifecycle.py tests/trading/test_v2_router_registration.py
# → 33 passed in 0.55s

# 6) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 641 passed in 2.68s

# 7) 全量回归
.venv/bin/pytest -q
# → 852 passed, 7 warnings in 4.72s（2 legacy test_session_cache 经延迟导入 app.main + sys.modules
#   弹出后恢复通过；warnings 为 pytest-asyncio/FastAPI 弃用告警，非本次引入）

# 8) git diff --check
git diff --check
# → 无输出，exit 0

# 9) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

---

## 4. 关键证据

### 4.1 Artifact factory（§6.1）

- `test_factory_local_branch` / `test_factory_local_creates_root`：local 分支返回 ArtifactStore、
  driver=local、root 目录创建。
- `test_factory_s3_branch_with_injected_client`：s3 分支 driver=s3、不调 `boto3.client`
  （monkeypatch 抛错）、`_client is fake`。
- `test_factory_s3_exact_config_passed`：bucket/prefix/expected_owner/retry_limit 精确传递
  （MAX_ATTEMPTS=4 → `_retry_limit==4`）。
- `test_factory_invalid_driver_rejected` / `test_factory_s3_requires_bucket_and_region`：
  非法 driver / 缺 bucket·region fail-fast。
- `test_factory_import_zero_network`：factory 重新 import 不触发任何 client 创建。

### 4.2 Runtime 健康矩阵（§6.2）

- `test_all_green_ready`：全绿 → `ready`，components/degraded/checked_at 精确。
- `test_not_started_unready` / `test_after_close_unready`：未 start/关闭后不可 ready。
- `test_required_component_fail_unready[3]`：DB/Control/Artifact fail → unready + 对应 state。
- `test_db_timeout_marks_unready` / `test_control_timeout_marks_unready`：超时 → unready。
- `test_cache_fail_ready_degraded` / `test_cache_timeout_ready_degraded`：Cache fail/timeout →
  `ready` + `degraded:["cache_redis"]`。
- `test_no_sensitive_leak_in_snapshot`：异常 message 含 `TOPSECRET`/`/var/secret`/`user`/`pw=` 的
  control → snapshot 全不含、`control_redis=unready`。
- `test_concurrent_snapshots_do_not_mutate_identity`：5 并发快照各健康调用 5 次、资源身份不变。

### 4.3 Lifespan 与关闭（§6.3）

- `test_close_idempotent_and_db_dispose_once`：close 两次 → artifact/cache/control/db dispose 各 1 次。
- `test_close_continues_on_failure`：control close 抛错 → cache/artifact/db 仍关闭。
- `test_build_runtime_resources_constructs_real_clients`：缺省构造真实 client（local artifact +
  注入 DatabaseEngines 惰性 engine，零网络）。

### 4.4 Router（§6.4）

- `test_health_and_live_200` / `test_health_ready_503_without_runtime`：三个 public health 状态码；
  runtime 未设 → 503 同结构。
- `test_metrics_200_and_content_type` / `test_metrics_404_when_disabled`：metrics on（200 +
  `text/plain` content-type）/ off（404）。
- `test_trading_runtime_401_unauthenticated` / `test_trading_runtime_403_insufficient_perms` /
  `test_trading_runtime_200_super_admin`：Admin 401/403/200（BizError code 语义）。
- `test_trading_runtime_reuses_recent_snapshot`：复用 runtime 最近快照、不重做 health。
- `test_legacy_public_dict_route_still_registered`：legacy `/api/dict/items` 仍注册可达。
- `test_seo_catch_all_does_not_shadow_health_or_metrics` / `test_web_seo_and_trading_routers_registered`：
  Web SEO `/{name}` 不遮蔽 health/metrics；trading router 在第一个 include 之前。

### 4.5 测试隔离与回归

- 全量中 2 个 legacy `test_session_cache` 测试因 `app.main` 顶层导入固化 `get_redis` 绑定而失败；
  修复为模块级 fixture 延迟 import app.main + 模块结束时弹出 `app.controllers.admin.session/cache`
  后恢复（`tests/trading + test_session_cache` 648、全量 852 全绿）。
- 环境说明：Base `default_response_class=ORJSONResponse` 依赖 `orjson`，测试 venv 原先缺失；
  已装进 venv（仅环境，未改 requirements.txt）供 router 测试渲染响应。属既有 Base 依赖缺口，
  非 00d2 引入。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞（如实记录）：
- Base `ORJSONResponse` 依赖 `orjson` 未声明在 requirements.txt；测试 venv 需预装或 Base 补声明
  （非本任务范围，未改依赖）。
- 真实 DB/Redis/S3/OTLP conformance 未执行（测试全用 fake/惰性对象，零网络）；`/health/ready`
  在无真实服务时返回 503 unready 属预期。
- `RuntimeResources` 未暴露 status/events 到业务链；WP-01A 起各域接入 lifecycle。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/config.py serve/.env.example serve/app/main.py \
  serve/app/services/redis_cache.py serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/app/services/artifact_store/factory.py serve/app/services/runtime.py \
  serve/app/controllers/admin/trading.py \
  serve/tests/trading/test_v2_artifact_factory.py \
  serve/tests/trading/test_v2_runtime_lifecycle.py \
  serve/tests/trading/test_v2_router_registration.py \
  serve/docs/manifests/wp-00d2-runtime-lifespan-health.md
```

- 回到 WP-00d1-r5 交付状态；observability primitives / DB / Redis / Artifact drivers 未改；
  无迁移、网络、Provider 或业务数据副作用；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00d2-runtime-lifespan-health.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
5b9d88e4414ab49f7df9e68999b082b80a143c07faa26d0e44595cd949466135
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00d2-runtime-lifespan-health.md | sha256sum
```
