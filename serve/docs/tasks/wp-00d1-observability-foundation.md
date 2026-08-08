# WP-00d1 — 技术可观测性基础

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00d1-observability-foundation.md`。
> 最后更新：2026-08-08 13:05 EDT。依赖：`WP-00c2-r3` 已接受。

## 1. 目标与用户价值

建立 V2 共用的技术可观测性基础：结构化且脱敏的日志、低基数 Prometheus 指标、可传播的
OpenTelemetry Trace。它用于回答“某次调用在哪里、做了多久、为何失败”，并为 WP-02 每次 AI 调用
的业务证据链提供技术关联；Trace/日志/指标不得替代未来 PostgreSQL append-only 业务事实。

本子任务只提供可测试的 primitives 与 typed config，不接入 FastAPI lifespan、健康端点或业务链。
`WP-00d2` 才负责初始化、关闭、metrics endpoint、依赖健康检查与 Artifact Store factory。

## 2. 必读与冻结决策

1. `serve/docs/v2-implementation-contract.md` §3、§12–§15
2. `serve/docs/polymarket-v2-platform-design.md` §4
3. `serve/docs/performance-cache-database-design.md` §11–§12、§15
4. `serve/docs/manifests/wp-00c2-r3-provider-error-redaction.md`（冻结，只读）

冻结边界：PostgreSQL/Artifact 是业务证据源；Redis/Prometheus/Trace 均不是。Prometheus 只允许低基数
标签；业务 ID 只进入脱敏日志和 Trace。prompt、response body、tool payload、Authorization、Cookie、
API key、passphrase、签名及 secret 永不输出技术日志或 span attribute。

## 3. 允许修改

```text
serve/app/config.py
serve/.env.example
serve/requirements.txt
serve/app/observability/__init__.py
serve/app/observability/logging.py
serve/app/observability/metrics.py
serve/app/observability/tracing.py
serve/tests/trading/test_v2_config.py
serve/tests/trading/test_v2_log_redaction.py
serve/tests/trading/test_v2_metric_cardinality.py
serve/tests/trading/test_v2_trace_context.py
serve/docs/manifests/wp-00d1-observability-foundation.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

最多 7 个生产/基础设施文件。禁止修改 `main.py`、middleware、数据库、Redis、Artifact Store、旧 Base
日志模块、Controller/Logic/Model、V1、既有 task/manifest 或前端。不得启动 exporter 网络请求。

## 4. Typed config 与依赖

### 4.1 运行时依赖

在 `requirements.txt` 只新增并使用：

```text
prometheus-client>=0.21,<1
opentelemetry-api>=1.29,<2
opentelemetry-sdk>=1.29,<2
opentelemetry-exporter-otlp-proto-http>=1.29,<2
```

不得加入 structlog、Sentry、Grafana SDK、自动 instrumentation 或未使用包。

### 4.2 配置字段

在 `Settings` 增加以下静态基础设施字段，并在 `.env.example` 给无 secret 示例：

| 字段 | 默认 | 约束 |
|---|---:|---|
| `OBS_LOG_LEVEL` | `INFO` | `DEBUG/INFO/WARNING/ERROR/CRITICAL` |
| `OBS_LOG_JSON` | `true` | bool；测试/本地可关闭 |
| `OBS_SERVICE_NAME` | `pollymarket-v2` | 1–64，`[a-zA-Z0-9._-]+` |
| `OBS_SERVICE_VERSION` | `dev` | 1–64，不得含控制字符/whitespace |
| `PROMETHEUS_ENABLED` | `true` | bool |
| `OTEL_ENABLED` | `false` | bool；false 时不得创建网络 exporter |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 空 | 启用 OTel 时必填绝对 `http(s)` URL；禁止 userinfo/query/fragment/control/whitespace |
| `OTEL_ALLOW_INSECURE_HTTP` | `false` | `http://` 仅显式 true 才允许 |
| `OTEL_TRACE_SAMPLE_RATIO` | `0.05` | `0.0..1.0` |
| `OTEL_EXPORT_TIMEOUT_S` | `5` | `>0` |

endpoint 允许 `/` 或 `/v1/traces` 路径，原样保留；不得 normalize。配置错误必须在 Settings 构造时
fail-fast，且 ValidationError 不打印任何凭据（endpoint 本身已禁止 userinfo/query）。

## 5. `observability/logging.py`

提供以下唯一职责和公开接口（具体 class 名可合理调整，但 `__init__.py` 必须显式导出）：

```python
configure_logging(*, level: str, json_output: bool, service: str, version: str) -> None
bind_log_context(**fields) -> ContextManager[None]
get_log_context() -> dict[str, str]
redact(value: object) -> object
```

### 5.1 输出合同

- JSON 模式每条日志为单行对象，至少含 UTC RFC3339 `timestamp`、`level`、`logger`、`message`、
  `service`、`service_version`；有值时加入 `event`、`trace_id`、`span_id` 与上下文字段。
- 允许的上下文字段固定为：`chain_id、causation_event_id、attempt_id、idempotency_key、
  release_manifest_id、forecast_episode_id、submission_id、economic_intent_id、execution_id`。
  未知 context key 立即 `ValueError`，不静默扩张。
- 使用 `contextvars`，嵌套 bind 正确恢复；两个 asyncio task 不串 context。
- Trace 已激活时自动读取 32 位小写 `trace_id`、16 位小写 `span_id`；无有效 span 时不伪造。
- `configure_logging()` 可重复调用且不产生重复 handler；不得删除/篡改已有非 V2 handler，实际在
  `main.py` 的接入留给 WP-00d2。

### 5.2 脱敏合同

- 对 dict/list/tuple 做有深度和元素上限的递归副本，不修改调用者对象；超限写固定占位符。
- key 大小写/`-`/`_` 归一后命中以下类别时，值统一为 `[REDACTED]`：password、secret、api key、
  authorization、cookie、passphrase、private key、signature、access/refresh token、credential、
  prompt、request/response body、tool input/output、raw payload。
- 字符串至少清洗：Bearer/Basic Authorization、URL userinfo、`key=value`/JSON 形式常见 secret；
  不把完整原始请求、模型 prompt/response 或 traceback 原样塞入日志。
- exception 文本和 `exc_info` 格式化结果也必须经过同一清洗；保留异常类型和受控错误摘要。
- `repr()`/`str()` 自身抛错的对象必须输出安全占位符，不能让日志调用破坏业务路径。

## 6. `observability/metrics.py`

- 使用显式 `CollectorRegistry`，不在 import 时污染默认全局 registry；提供 registry 注入以便测试。
- 提供受控 Counter/Gauge/Histogram 创建或 catalog API；同名同 schema 幂等返回，同名不同类型、
  label 或 bucket 必须 `ValueError`。
- metric 名必须以 `pm_` 开头且符合 Prometheus 命名规则。
- label key 唯一 allowlist：`stage、gate、result、role、provider、model、version、dependency、
  operation、method、status_class、mode`。任何 `*_id`、market/token/condition/episode/submission/
  intent/execution/chain/user/address/order/trade 等 key 必须拒绝。
- 默认 histogram bucket 是有限、单调、静态 tuple；不得从请求或配置动态造 label/bucket。
- 提供 `render_metrics(registry) -> tuple[bytes, str]`，供 WP-00d2 endpoint 使用；输出不得包含日志
  context、secret 或 trace/business ID。
- 模块只定义技术指标 primitive；本任务不伪造尚未存在的 AI/交易业务指标。

## 7. `observability/tracing.py`

提供以下能力：

```python
configure_tracing(..., exporter=None) -> TracerProvider | None
shutdown_tracing() -> None
start_span(name: str, *, operation: str, attributes: Mapping[str, object] | None = None)
current_trace_ids() -> tuple[str | None, str | None]
inject_trace_context(carrier: MutableMapping[str, str]) -> None
extract_trace_context(carrier: Mapping[str, str]) -> Context
```

- 只用 W3C `traceparent/tracestate`；不传播 baggage。非法 carrier fail-closed 为无父上下文，不抛出
  secret 或整个 header。
- 默认 `ParentBased(TraceIdRatioBased(ratio))`；`execution、ledger、reconciliation` operation 在
  无父 span 时也 100% sample，已有父采样决定必须继承。实现用确定性自定义 Sampler，不靠调用方
  临时改 ratio。
- span name/operation 必须匹配 `[a-z0-9_.-]{1,96}`；operation 是低基数枚举语义，不含业务 ID。
- span attributes 仅允许固定技术键和上述业务链 ID；prompt/body/tool payload、headers、URL
  userinfo、Authorization/Cookie/secret/signature/token 一律拒绝或 `[REDACTED]`，不得交给 exporter。
- `OTEL_ENABLED=false` 返回 `None`/no-op 且零网络；启用时使用 OTLP HTTP exporter，timeout 来自
  config。测试通过注入 in-memory/fake exporter，禁止访问真实 collector。
- 配置和 shutdown 幂等；不得在 import 时替换全局 provider。Trace 只做技术追踪，不落业务状态。

## 8. 必测证据

1. 日志：嵌套 dict、headers、URL userinfo、exception、prompt/body/tool payload 全部脱敏；完整输出无
   注入 secret；context 嵌套恢复、async task 隔离、trace/span 注入、重复 configure 无重复行。
2. Metrics：允许标签可采集；每个禁止 business-ID label 都被拒绝；同名冲突被拒绝；render 类型和
   内容正确；不同 registry 不互相污染。
3. Trace：ratio 0/1、三个 always-sample operation、父采样继承、W3C inject/extract、非法 carrier、
   敏感 attribute、shutdown/reconfigure 与 disabled 零 exporter 调用。
4. Config：默认值、边界 ratio/timeout、service name/version、OTLP endpoint 的合法/非法矩阵。
5. 现有 WP-00 全量测试不得退化。

## 9. 验收命令

```bash
cd /code/pollymarket/v2/serve
.venv/bin/pip install -r requirements-dev.txt
python3 -m compileall -q app tests
.venv/bin/pytest -q tests/trading/test_v2_config.py
.venv/bin/pytest -q tests/trading/test_v2_log_redaction.py
.venv/bin/pytest -q tests/trading/test_v2_metric_cardinality.py
.venv/bin/pytest -q tests/trading/test_v2_trace_context.py
.venv/bin/pytest -q tests/trading
.venv/bin/pytest -q
git diff --check
```

## 10. 交付、风险与回滚

创建且只创建 `serve/docs/manifests/wp-00d1-observability-foundation.md`。Manifest 必须记录依赖版本、
修改文件、脱敏/基数/传播测试、真实命令数量、blocker、回滚及可复现 SHA；更新两个索引为
`DONE（待审）`，保持 00d1 为当前任务，等待用户再次说“完成”。不得创建 00d2、提交或推送。

风险是全局 logger/provider 污染和误泄密；必须以显式初始化、注入 registry/exporter、严格 allowlist
及完整输出断言控制。回滚恢复允许文件并删除新增 observability/tests/manifest；无迁移、Redis、
Provider 或业务数据副作用。

## 11. 非目标

- 不改 `main.py`，不暴露 `/metrics`，不实现 health/lifespan/middleware。
- 不记录 AI prompt/response，不实现 AI invocation 业务表（属于 WP-02）。
- 不接 Grafana/Collector、不写部署文件、不发网络请求。
- 不做自动 instrumentation，不把 Trace/metrics 当审计账本。
- 不用 TODO、skip、mock 成功或只写 manifest 完成本任务。
