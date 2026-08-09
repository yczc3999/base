# COMPLETION MANIFEST — WP-00d2-r1 · Runtime 失败边界与可复现依赖整改

- Work package: `WP-00` 子任务 `WP-00d2-r1`
- 状态: **DONE（待审）**
- 日期: 2026-08-09
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00d2`（REMEDIATION_REQUIRED，四类 P1：异常退出不 cleanup、basicConfig/raw exception 日志旁路、首次/关闭状态与固定 schema、health/metrics 编排异常落 Base 200、orjson 不可复现）；本整改接受前 `WP-01A` 继续阻塞
- 规范依据: `serve/docs/tasks/wp-00d2-r1-runtime-failure-boundaries.md`；`serve/docs/v2-implementation-contract.md` §3/§8/§12–§15
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/services/runtime.py` | 修改 | 新增 `safe_unready_snapshot`（唯一 safe/unavailable 快照 builder，四组件固定 schema、driver 取配置）；`health_snapshot` closed/not-started 短路固定 unready 不访问依赖；artifact driver 恒取 `ARTIFACT_DRIVER`（不输出 unknown）；`close()` 开始即覆盖 `last_snapshot=unready` 并返回固定失败 component 集合；`build_runtime_resources` 改 `async` 异常安全（构造失败逆序关闭自建 Artifact/Cache/Control，注入对象不误关） |
| `serve/app/main.py` | 修改 | 删 `logging.basicConfig`（V2 只经 `configure_logging`）；prewarm 失败日志去 `%s, e` 用固定 reason code；lifespan 从 tracing 初始化起 `try/finally` 覆盖 runtime 构造/首次 health/prewarm/yield 与取消/异常退出，清理顺序 Runtime→`close_redis()`→`shutdown_tracing()`，一项失败不阻后续；builder 改 `await`；构造失败清 `app.state.trading_runtime` 防旧指针；started 后首次 health（全绿首快照 ready）；`/health/ready` 捕获编排异常→固定 schema 503；`/metrics` 渲染异常→固定纯文本 503 |
| `serve/app/controllers/admin/trading.py` | 修改 | runtime absent → 复用 `safe_unready_snapshot`（四组件 unready schema，driver 取配置），不复制空结构 |
| `serve/requirements.txt` | 修改 | 新增 `orjson>=3.10,<4`（与 `ORJSONResponse` 一致，可复现依赖） |
| `serve/tests/trading/test_v2_runtime_lifecycle.py` | 修改 | 15 → 28：新增真 `async with lifespan`（正常/body 抛异常/cancellation/factory 抛错，逐项断言清理次数）、builder 失败注入矩阵（Artifact/Cache/Control 各构造点逆序关闭+注入不误关+原异常传播）、close 覆盖 unready+不再访问依赖、not-started 零依赖访问、driver 配置值、safe snapshot 四组件、close 返回失败集合 |
| `serve/tests/trading/test_v2_router_registration.py` | 修改 | 11 → 19：新增 runtime absent 固定四组件 503、health 编排异常 503（marker=0）、metrics 渲染异常 503 纯文本、Admin absent 固定 schema、close 后 unready、main 无 basicConfig、prewarm 失败日志 marker=0、orjson 从 requirements 可复现 |
| `serve/docs/manifests/wp-00d2-r1-runtime-failure-boundaries.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` / `serve/docs/tasks/README.md` | 修改 | 00d2-r1 标 DONE（待审） |

范围外未动：config、Redis clients、Artifact factory/drivers/service、observability primitives、
legacy Controller/Logic/Model、Alembic、Admin 前端、V1、既有 task/manifest。**未遇
`BLOCKED_CONTRACT`**：不新增业务 loop/表/策略/下单/secret。

---

## 2. 实现内容（§3 精确合同）

### 2.1 Lifespan 必须清理（§3.1）

- 删除 `main.py` 的 `logging.basicConfig`；V2 初始化只调用 `configure_logging`；startup/shutdown
  failure 日志只含固定 component + reason code（`"startup prewarm db failed"`），无 `%s, exc`、
  `exc_info`、traceback 或原始异常 message。
- 从 tracing 初始化起 `try/finally` 覆盖 runtime 构造、首次 health、legacy prewarm、`yield` 及
  取消/异常退出：无论异常发生在 yield 前、yield 内或正常退出，都按 Runtime（若已构造）→
  `close_redis()` → `shutdown_tracing()` 尽力执行；一项失败不阻止后续项。
- `runtime.close()` 返回固定失败 component 集合（`list[str]`），main 记录安全 reason code，不
  静默丢关闭失败；重复 close 幂等返回同集合。
- `build_runtime_resources` 改可等待异常安全 builder（main `await`）：任何构造步骤失败时逆序
  关闭**本次 builder 自己创建**的 Artifact/Cache/Control 后重新抛出；不关闭调用方注入对象
  （不再遗留"先建两个 Redis client、Artifact 构造失败后遗留连接池"）。lifespan 同时释放已启用
  tracing/legacy 资源并阻止 startup；finally 清 `app.state.trading_runtime` 防旧指针。依赖
  health 不可达不抛 startup，只形成 unready 快照。

### 2.2 状态与固定 schema（§3.2）

- 成功构造后先 `mark_started()`，再执行首次 health；只有该快照完成后写入
  `app.state.trading_runtime`；全绿首次快照为 ready，Admin 首次读取不依赖先访问公开 health。
- `close()` 开始立即把最近快照替换为固定 unready；closed/not-started 的 `health_snapshot()` 不再
  访问已关闭依赖，直接返回固定 unready。
- `safe_unready_snapshot(driver)` 为唯一 safe/unavailable builder，main 与 Admin Controller 均复用；
  四组件 database/control/artifact=unready、cache=degraded、`degraded=["cache_redis"]`、
  `checked_at` UTC RFC3339。
- Artifact 成功或失败都用冻结配置的 `ARTIFACT_DRIVER`（`local|s3`），不输出 unknown；probe result
  形状异常映射为固定 unready，不泄对象 repr/message。
- `GET /health/ready` 捕获 health 编排异常返回固定 schema 503；`GET /metrics` 渲染异常返回固定
  纯文本 503；二者不落入 Base 全局 HTTP-200 异常包装，不返回异常原文。

### 2.3 可复现依赖（§3.3）

- `requirements.txt` 显式声明 `orjson>=3.10,<4`；验收含 `python -c 'import orjson'` + 测试断言
  requirements 文本含该行；不依赖手工改 venv。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests
# → exit 0

# 2) artifact factory（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_factory.py
# → 7 passed in 0.10s

# 3) runtime lifecycle（15 → 28）
.venv/bin/pytest -q tests/trading/test_v2_runtime_lifecycle.py
# → 28 passed in 0.12s

# 4) router registration（11 → 19）
.venv/bin/pytest -q tests/trading/test_v2_router_registration.py
# → 19 passed in 0.40s

# 5) 定向三项合计
.venv/bin/pytest -q tests/trading/test_v2_artifact_factory.py \
  tests/trading/test_v2_runtime_lifecycle.py tests/trading/test_v2_router_registration.py
# → 54 passed in 0.65s

# 6) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 662 passed in 2.90s

# 7) 全量回归
.venv/bin/pytest -q
# → 873 passed, 11 warnings in 4.64s（warnings 为 pytest-asyncio/FastAPI 弃用告警；R1 新增
#   lifespan 测试的 RuntimeWarning 已通过同步 noop monkeypatch 消除）

# 8) orjson 可复现
.venv/bin/python -c 'import orjson'
# → orjson 3.11.9（requirements.txt 已声明 orjson>=3.10,<4）

# 9) git diff --check
git diff --check
# → 无输出，exit 0

# 10) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

---

## 4. 关键证据

### 4.1 Lifespan finally 清理（§4 前两条）

- `test_lifespan_normal_exit_cleans_all` / `test_lifespan_body_raises_still_cleans` /
  `test_lifespan_body_cancellation_still_cleans`：真 `async with lifespan(fake_app)`——正常退出、
  body 抛异常、CancelledError 三种路径 Runtime.close、`close_redis`、`shutdown_tracing` 均恰好
  调用一次，异常正确传播（`pytest.raises`），`app.state.trading_runtime` 清空。
- `test_lifespan_build_failure_propagates_and_shuts_tracing`：factory 抛异常 → 异常传播（阻止
  startup）、close_redis/shutdown_tracing 仍执行、runtime 未构造（close=0）、无旧指针残留。
- `test_lifespan_cleanup_continues_on_close_failure`：runtime.close 抛 `TOPSECRET` → close_redis/
  shutdown_tracing 仍执行。
- `test_builder_artifact_failure_closes_created` / `test_builder_cache_failure_closes_created` /
  `test_builder_control_failure_no_created_to_close`：Artifact/Cache/Control 各构造点失败——自建
  资源逆序关闭（`["cache","control"]`/`["control"]`/无）、注入对象不被误关、原异常传播。

### 4.2 状态与固定 schema（§4 后三条）

- `test_close_replaces_last_snapshot_unready`：close 前 ready → close 后 `last_snapshot=unready`；
  随后 health 不再访问依赖（计数不变）、driver=配置值。
- `test_health_snapshot_not_started_no_dependency_access`：not-started 零依赖访问、返回固定
  unready。
- `test_artifact_fail_driver_is_config_value`：artifact 失败 driver 仍为配置 `local`（非 unknown）。
- `test_safe_unready_snapshot_four_component_schema`：四组件 schema 精确、driver 传参、非法 driver
  回落 local。
- `test_close_returns_failed_components`：close 返回失败 component 集合、重复幂等。
- router：`test_health_ready_runtime_absent_fixed_four_component_schema`（503 四组件）、
  `test_health_ready_health_snapshot_raises_503`（编排异常 503、marker=0）、
  `test_metrics_render_raises_503`（纯文本 503、marker=0）、`test_trading_runtime_absent_fixed_schema`、
  `test_trading_runtime_closed_last_snapshot_unready`。

### 4.3 日志与可复现（§4）

- `test_main_never_calls_basic_config`：main.py 无可执行的 `basicConfig(` 调用（仅注释提及）。
- `test_prewarm_failure_logs_no_raw_exception`：db/redis prewarm 抛含 `TOPSECRET` 异常 → 捕获
  stderr 中 `TOPSECRET`=0、只有固定 reason code；V2 handler 由本模块安装。
- `test_orjson_importable_from_requirements`：`import orjson` 成功 + requirements 文本含
  `orjson>=3.10,<4`（不 pip install）。
- `test_builder_artifact_failure_closes_created`：构造异常原样传播（构造失败阻止 startup 属预期），
  marker 泄漏禁止点（日志/HTTP）由 4.2/4.3 各测试覆盖。

### 4.4 回归

- 原 33 定向、641 trading、852 full 全部继续通过；R1 后定向 54、trading 662、全量 873、双前缀 0、
  `git diff --check` 干净；R1 改动仅落在 main.py、runtime.py、trading.py、requirements.txt、
  test_v2_runtime_lifecycle.py、test_v2_router_registration.py 六文件。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞（如实记录）：
- 真实 DB/Redis/S3/OTLP conformance 未执行（测试全 fake/惰性，零网络）；`/health/ready` 无真实
  服务时 503 unready 属预期。
- 构造失败异常（编程/配置错误）原样传播阻止 startup；运行依赖不可达则应用启动但 snapshot=unready
  （两语义分离）。
- `RuntimeResources` 未暴露 status/events 到业务链；WP-01A 起各域接入 lifecycle。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/main.py serve/app/services/runtime.py \
  serve/app/controllers/admin/trading.py serve/requirements.txt \
  serve/tests/trading/test_v2_runtime_lifecycle.py \
  serve/tests/trading/test_v2_router_registration.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/docs/manifests/wp-00d2-r1-runtime-failure-boundaries.md
```

- 回到 WP-00d2 交付状态；observability primitives / DB / Redis / Artifact drivers 未改；无迁移、
  网络、Provider 或业务数据副作用；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00d2-r1-runtime-failure-boundaries.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
7a18da989ea994a774a494319fd95e0e3c37ccb496fc16226db612ec67acbc3d
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00d2-r1-runtime-failure-boundaries.md | sha256sum
```
