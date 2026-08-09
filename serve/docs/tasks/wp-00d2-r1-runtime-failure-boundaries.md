# WP-00d2-r1 — Runtime 失败边界与可复现依赖整改

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00d2-r1-runtime-failure-boundaries.md`。
> 最后更新：2026-08-09 02:26 EDT。依赖：`WP-00d2` 已交付但审查未接受；本任务接受前
> `WP-01A` 继续阻塞。

## 1. 审查结论

00d2 的 factory、并发 probe、权限与正常 HTTP 路径主体成立；独立复验 7 factory、15 runtime、
11 router、641 trading、852 full 与 manifest SHA 均通过。但测试没有进入真实 lifespan 上下文，
留下以下同属运行时正确性的 P1：

1. `lifespan` 的 `yield` 不在 `try/finally` 中。已独立复现：应用 body 抛异常后，Runtime、legacy
   Redis 和 tracing 的关闭调用全部为 0；runtime 构造中途失败时，先前启用的 tracing 也不会释放。
2. `main.py` 仍在 import 时调用 `logging.basicConfig`，同时 prewarm 以 `%s, e` 记录原始异常。
   这条非 V2 handler 绕过统一 redactor，可能直接输出连接串、路径或 Provider message。
3. 首次 health 在 `mark_started()` 前执行，因此全绿依赖也写入 `last_snapshot.status=unready`；
   Admin 在第一次 `/health/ready` 之前看到假故障。`close()` 又不覆盖最近 ready 快照，closed runtime
   仍可经 Admin 返回 ready。
4. health 固定 schema 未闭合：Artifact 失败返回 `driver=unknown`，runtime 缺失返回空
   `components`；若 health/metrics 编排自身抛异常，会落入 Base 全局处理并以 HTTP 200 返回。
5. `ORJSONResponse` 是运行时默认响应类，但 `orjson` 未在 `requirements.txt` 声明；manifest 通过
   手工安装到 venv 才完成 router 测试，验收不可由仓库依赖复现。

## 2. 允许范围

```text
serve/app/main.py
serve/app/services/runtime.py
serve/app/controllers/admin/trading.py
serve/requirements.txt
serve/tests/trading/test_v2_runtime_lifecycle.py
serve/tests/trading/test_v2_router_registration.py
serve/docs/manifests/wp-00d2-r1-runtime-failure-boundaries.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 config、Redis clients、Artifact factory/drivers/service、observability primitives、legacy
Controller/Logic/Model、Alembic、Admin 前端、V1 或既有 task/manifest。不得新增业务 loop、表、策略、
下单或 secret。

## 3. 精确整改合同

### 3.1 Lifespan 必须清理

1. 删除 `main.py` 的 `logging.basicConfig`；V2 初始化只调用 `configure_logging`。所有 startup/
   shutdown failure 日志只能包含固定 component + reason code，禁止 `%s, exc`、`exc_info`、traceback
   或原始异常 message。
2. 从 tracing 初始化开始使用 `try/finally` 覆盖 runtime 构造、首次 health、legacy prewarm、
   `yield` 及取消/异常退出。无论异常发生在 yield 前、yield 内或正常退出，都按以下顺序尽力执行：
   Runtime（若已构造）→ legacy `close_redis()` → `shutdown_tracing()`；一项失败不阻止后续项。
3. runtime 内部仍按 Artifact → Cache → Control → DB dispose 关闭。每项最多一次；`close()` 返回或
   记录固定失败 component 集合，使 main 可记录安全 reason code；不得静默丢掉关闭失败。
4. 将 `build_runtime_resources` 改为可等待的异常安全 builder（main 必须 `await`）：任何构造步骤
   失败时，按逆序关闭**本次 builder 自己创建**的 Artifact/Cache/Control 资源后重新抛出；不得关闭
   调用方注入对象。当前“先创建两个 Redis client、Artifact 构造失败后遗留连接池”的路径必须有
   失败注入测试。lifespan 同时释放已启用 tracing/legacy 资源并阻止 startup，不得留下旧
   `app.state.trading_runtime` 指针。依赖 health 不可达则不抛 startup，只形成 unready 快照。

### 3.2 状态与固定 schema

1. 成功构造后先将 runtime 置 started，再执行首次 health；只有该快照完成后写入
   `app.state.trading_runtime`。全绿首次快照必须为 ready，Admin 首次读取不得依赖先访问公开 health。
2. `RuntimeResources.close()` 开始时立即把最近快照替换为固定 unready；closed/not-started 的
   `health_snapshot()` 不再访问已关闭依赖，直接返回固定 unready。
3. 在 `runtime.py` 提供唯一 safe/unavailable snapshot builder，main 与 Admin Controller 均复用，
   不复制空结构。无可用 runtime 时固定输出四组件：database/control/artifact=`unready`，
   cache=`degraded`，`degraded=["cache_redis"]`，`checked_at` 为 UTC RFC3339。
4. Artifact 成功或失败都使用冻结配置的 `ARTIFACT_DRIVER`（`local|s3`），不得输出 `unknown`；
   任何 probe result 形状异常也映射为固定 unready，不泄对象 repr/message。
5. `GET /health/ready` 捕获 health 编排异常并返回固定 schema HTTP 503；`GET /metrics` 的渲染异常
   返回固定纯文本 HTTP 503。二者禁止落入 Base 全局 HTTP-200 异常包装，且不得返回异常原文。

### 3.3 可复现依赖

在 `requirements.txt` 显式声明 `orjson>=3.10,<4`，与现有 `ORJSONResponse` 一致。不得依赖手工
修改 venv；dependency smoke 必须从 requirements 可安装/导入。

## 4. 必测证据

- 真正进入 `async with lifespan(fake_app)`：正常退出、body 抛异常、body cancellation、runtime
  factory 抛异常；逐项断言 Runtime/legacy Redis/tracing 的调用次数与顺序，且异常正确传播。
- builder 在 Artifact/Cache/Control 各构造点失败：本次已创建资源全部逆序关闭、注入对象不被误关、
  原异常继续传播。
- startup 全绿时首个 `last_snapshot=ready`；首个 Admin 读取即 ready。依赖失败时应用仍启动但
  snapshot=unready。
- close 前 ready → close 后 `last_snapshot=unready`；随后 health 不再调用 DB/Redis/Artifact。
- closing 某项抛带敏感 marker 的异常：其余资源照常关闭，日志/exporter/HTTP 中 marker=0。
- 删除/禁止 `logging.basicConfig`；DB/legacy Redis prewarm 异常含敏感 marker 时所有捕获日志中
  marker=0，且只有 V2 handler 由本模块安装。
- runtime absent、artifact fail、malformed probe result 都返回字段完全一致的四组件 schema；
  artifact driver 始终为配置值。
- fake `health_snapshot()` / `render_metrics()` 抛异常：分别 HTTP 503，不是 200，body 无 marker。
- requirements 文本与独立 Python import 均证明 `orjson` 可复现；不得在测试中 pip install。
- 原 33 定向、全部 WP-00 与全量回归继续通过。

## 5. 验收与交付

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests
.venv/bin/pytest -q tests/trading/test_v2_runtime_lifecycle.py
.venv/bin/pytest -q tests/trading/test_v2_router_registration.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_factory.py
.venv/bin/pytest -q tests/trading
.venv/bin/pytest -q
.venv/bin/python -c 'import orjson'
git diff --check
```

创建且只创建 `serve/docs/manifests/wp-00d2-r1-runtime-failure-boundaries.md`，记录失败注入矩阵、
真实测试计数、日志/HTTP marker 断言、依赖证据、blocker、回滚与可复现 SHA；更新两个索引为
`DONE（待审）`。不得创建 WP-01A、提交或推送。无迁移或业务数据副作用。
