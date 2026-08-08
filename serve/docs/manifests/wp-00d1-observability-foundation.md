# COMPLETION MANIFEST — WP-00d1 · 技术可观测性基础

- Work package: `WP-00` 子任务 `WP-00d1`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00c2-r3`（ACCEPTED，117 S3 / 315 trading / 526 full）
- 规范依据: `serve/docs/tasks/wp-00d1-observability-foundation.md`；`serve/docs/v2-implementation-contract.md` §3/§12–§15；`serve/docs/polymarket-v2-platform-design.md` §4；`serve/docs/performance-cache-database-design.md` §11–§12、§15
- 验收命令: 见 §3

---

## 1. 修改文件

生产/基础设施（7，恰为任务上限）：`config.py`、`.env.example`、`requirements.txt`、
`observability/__init__.py`、`observability/logging.py`、`observability/metrics.py`、
`observability/tracing.py`。
测试：`test_v2_config.py`（+观测性段）、`test_v2_log_redaction.py`（新）、
`test_v2_metric_cardinality.py`（新）、`test_v2_trace_context.py`（新）。
索引：`manifests/README.md`、`tasks/README.md`；本 manifest。

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/config.py` | 修改 | 11 个 `OBS_*`/`PROMETHEUS_*`/`OTEL_*` typed 字段 + `_validate_otel_endpoint`（严格 origin，允许 `/`、`/v1/traces` 路径）+ `_validate_observability` 交叉校验 |
| `serve/.env.example` | 修改 | 观测性配置段（无 secret 示例，http 需 opt-in） |
| `serve/requirements.txt` | 修改 | 新增 prometheus-client>=0.21,<1、opentelemetry-api/sdk/exporter-otlp-proto-http>=1.29,<2 |
| `serve/app/observability/__init__.py` | **新增** | 显式导出全部 primitives |
| `serve/app/observability/logging.py` | **新增** | JSON/redaction 日志：configure_logging（幂等、不删非 V2 handler）、bind_log_context（contextvars 嵌套/async 隔离）、get_log_context、redact（深度/元素上限 + 敏感类别 + 字符串清洗 + repr 抛错占位）、trace/span 自动注入 |
| `serve/app/observability/metrics.py` | **新增** | 显式 CollectorRegistry + catalog（同名同 schema 幂等/不同 ValueError）、pm_ 前缀、label allowlist 12 键、静态 bucket、render_metrics |
| `serve/app/observability/tracing.py` | **新增** | configure_tracing（disabled 零网络、幂等）、_PolicySampler（父采样继承 + execution/ledger/reconciliation 无父 100% + 确定性 ratio）、start_span（name/operation 正则 + attribute allowlist）、current_trace_ids/inject/extract（W3C traceparent/tracestate，不传播 baggage）、shutdown 幂等 |
| `serve/tests/trading/test_v2_config.py` | 修改 | +14 观测性测试（默认值/override/level/name/version/ratio/timeout/endpoint 矩阵/.env 契约）→ 93 |
| `serve/tests/trading/test_v2_log_redaction.py` | **新增** | 15 tests |
| `serve/tests/trading/test_v2_metric_cardinality.py` | **新增** | 33 tests |
| `serve/tests/trading/test_v2_trace_context.py` | **新增** | 16 tests |
| `serve/docs/manifests/wp-00d1-observability-foundation.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` / `serve/docs/tasks/README.md` | 修改 | 00d1 标 DONE（待审） |

范围外未动：`main.py`、middleware、数据库、Redis、Artifact Store、旧 Base 日志模块、
Controller/Logic/Model、V1、既有 task/manifest、前端。**未遇 `BLOCKED_CONTRACT`**：公共 Protocol
零改动；不启动 exporter 网络请求。

---

## 2. 实现内容

### 2.1 Typed 配置与依赖（§4）

- 11 字段：`OBS_LOG_LEVEL`（枚举）、`OBS_LOG_JSON`、`OBS_SERVICE_NAME`（1–64 `[a-zA-Z0-9._-]+`）、
  `OBS_SERVICE_VERSION`（1–64 无 control/whitespace）、`PROMETHEUS_ENABLED`、
  `OTEL_ENABLED`（false 时不建网络 exporter）、`OTEL_EXPORTER_OTLP_ENDPOINT`（严格 http(s)
  origin：禁 userinfo/query/fragment/control/whitespace；path 仅空/`/`/`/v1/traces`；port 校验）、
  `OTEL_ALLOW_INSECURE_HTTP`、`OTEL_TRACE_SAMPLE_RATIO`（0..1）、`OTEL_EXPORT_TIMEOUT_S`（>0）。
- `_validate_otel_endpoint` 返回 scheme；启用时 endpoint 必填、http 需 opt-in；未启用时填了
  endpoint 也做格式校验（fail-fast）。配置错误在 Settings 构造时抛，ValidationError 不打印凭据。
- `requirements.txt` 只加 4 个依赖（prometheus-client + OTel api/sdk/exporter），无 structlog/
  Sentry/Grafana SDK/自动 instrumentation。

### 2.2 logging.py（§5）

- `configure_logging(level, json_output, service, version)` 幂等：仅移除自己先前安装的 V2
  handler（`_pm_v2_observability_handler` 标记），不删除/篡改非 V2 handler；level 校验。
- JSON 单行对象：`timestamp`(UTC RFC3339)、`level`、`logger`、`message`、`service`、
  `service_version`；有值时 `event`、`trace_id`(32 hex)、`span_id`(16 hex) 与上下文字段。
- `bind_log_context(**fields)`：context key 固定 allowlist（9 个业务链 ID），未知键 ValueError；
  contextvars 嵌套用 token 恢复；两个 asyncio task 不串 context。
- `redact`：dict/list/tuple 深度（≤6）与元素（≤64）上限递归副本，不修改调用者对象；key 归一
  （lower、`-`→`_`）后命中敏感类别值统一 `[REDACTED]`（password/secret/api_key/authorization/
  cookie/passphrase/private_key/signature/access·refresh_token/credential/prompt/request·response
  body/tool input·output/raw/payload/token 等）；字符串清洗 Bearer/Basic、URL userinfo、
  key=value/JSON secret；`repr()`/`str()` 抛错对象输出 `<unrepr-presentable>`；基本标量
  （int/float/bool/None）原样保留；exception 文本与 exc_info 走同一清洗。
- Trace 激活时自动读 trace_id/span_id；无有效 span 不伪造（不依赖 tracing provider 状态）。

### 2.3 metrics.py（§6）

- 显式 `CollectorRegistry` + `MetricCatalog`（注入 registry）；import 不污染默认全局 registry。
- `make_counter/gauge/histogram`：metric 名必须 `^pm_[a-zA-Z_:][a-zA-Z0-9_:]*$`；label 唯一
  allowlist（stage/gate/result/role/provider/model/version/dependency/operation/method/
  status_class/mode）；任何 `*_id` 或业务域子串（market/token/condition/episode/submission/
  intent/execution/chain/user/address/order/trade）拒绝。
- 同名同 schema（kind/labels/buckets）幂等返回；不同 → ValueError。histogram 默认 bucket 是
  有限单调静态 tuple（不动态造）。
- `render_metrics(registry) -> (bytes, "text/plain; version=0.0.4; charset=utf-8")`；输出不含
  日志 context/secret/trace/business ID。模块只定义技术指标 primitive，不伪造 AI/交易业务指标。

### 2.4 tracing.py（§7）

- `configure_tracing(enabled, endpoint, allow_insecure_http, ratio, timeout_s, service, version,
  exporter=None)`：disabled → None 且零 exporter（不 import/不构造 OTLP）；enabled → 显式
  registry/exporter 注入（测试用 in-memory）；exporter 缺省按 endpoint/timeout 建 OTLP HTTP。
  幂等（每次以新 provider 替换，configure 前 shutdown 旧 provider 的 span processors）。
- `_PolicySampler`（确定性）：SDK 用 `sampling_result.attributes` 作为 span 属性，故 sampler 把
  传入 attributes 原样带回；父采样决定必须继承（`get_current_span(parent_context)`，SDK 传
  `context=None` 时回退运行时 context = 嵌套父 span）；无父且 operation ∈ {execution, ledger,
  reconciliation} → 100% 采样；其余按 `TraceIdRatioBased` 确定性 ratio。
- `start_span(name, operation, attributes)`：name/operation 匹配 `[a-z0-9_.-]{1,96}` 且 operation
  不以 `_id` 结尾；attribute 键仅 allowlist（技术键 + 业务链 ID），敏感 str 值置 `[REDACTED]`；
  disabled 时用全局 no-op tracer（零记录零网络）。
- `current_trace_ids() -> (trace_id|None, span_id|None)`；`inject/extract_trace_context` 只用
  W3C traceparent/tracestate（不传播 baggage），非法 carrier fail-closed 为无父上下文。
- 配置和 shutdown 幂等；import 时不替换全局 provider；Trace 只做技术追踪，不落业务状态。

---

## 3. 命令与真实结果

```bash
# 1) 依赖
.venv/bin/pip install -q -r requirements-dev.txt
# → exit 0；prometheus-client 0.26.0、opentelemetry-api/sdk/exporter-otlp-proto-http 1.44.0

# 2) compileall
python3 -m compileall -q app tests
# → exit 0

# 3) typed config（含观测性段）
.venv/bin/pytest -q tests/trading/test_v2_config.py
# → 93 passed in 0.20s

# 4) log redaction
.venv/bin/pytest -q tests/trading/test_v2_log_redaction.py
# → 15 passed in 0.08s

# 5) metric cardinality
.venv/bin/pytest -q tests/trading/test_v2_metric_cardinality.py
# → 33 passed in 0.09s

# 6) trace context
.venv/bin/pytest -q tests/trading/test_v2_trace_context.py
# → 16 passed in 0.10s

# 7) 观测性四项合计
.venv/bin/pytest -q tests/trading/test_v2_config.py \
  tests/trading/test_v2_log_redaction.py \
  tests/trading/test_v2_metric_cardinality.py \
  tests/trading/test_v2_trace_context.py
# → 157 passed in 0.34s

# 8) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 417 passed in 1.88s

# 9) 全量回归
.venv/bin/pytest -q
# → 628 passed, 1 warning in 3.96s（1 warning = conftest event_loop 弃用告警，同各 WP）

# 10) git diff --check
git diff --check
# → 无输出，exit 0

# 11) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

---

## 4. 关键证据

### 4.1 日志脱敏（§8.1）

- `test_redact_nested_dict_headers_and_userinfo`：Authorization/Cookie/password/api_key/token
  全 `[REDACTED]`，name/ok 保留，不修改调用者对象。
- `test_redact_string_authorization_userinfo_and_kv`：`Bearer x`→`Bearer [REDACTED]`、
  `https://u:p@host`→`https://[REDACTED]@host`、`password=hunter2`→`password=[REDACTED]`、
  JSON `"password":"x"`→`"password":"[REDACTED]"`。
- `test_redact_prompt_body_tool_payload`：prompt/request_body/tool_input/tool_output/response
  全 `[REDACTED]`。
- `test_redact_depth_and_item_limits`：深度 >6 与元素 >64 写 `[TRUNCATED]`。
- `test_redact_unrepr_presentable`：`repr()` 抛错对象 → `<unrepr-presentable>`。
- `test_json_log_line_fields_and_event`：单行 JSON 含 timestamp/level/logger/message/service/
  service_version；无有效 span 不伪造 trace_id。
- `test_context_nested_restore` / `test_context_async_tasks_isolated`：嵌套恢复、asyncio task
  各自 context。
- `test_configure_repeat_no_duplicate_lines`：三次 configure 只产一行。
- `test_configure_keeps_foreign_handler`：非 V2 handler 不被删除。
- `test_unknown_context_key_valueerror`：未知 key / prompt 键 ValueError。
- `test_trace_and_span_ids_in_json`：span 内日志含 32 hex trace_id / 16 hex span_id，与
  InMemorySpanExporter 导出一致。

### 4.2 指标基数（§8.2）

- `test_allowed_labels_collectable`：12 个 allowlist 标签可采集，render 含三类 metric。
- `test_business_id_labels_rejected[18]` / `test_unknown_labels_rejected[5]`：业务 ID/未知标签
  全 ValueError。
- `test_name_must_be_pm_prefixed`：非 `pm_` 前缀/非法字符/空拒绝。
- `test_same_name_same_schema_idempotent`：同名同 schema 返回同一对象。
- `test_same_name_different_kind/labels/buckets_rejected`：冲突 ValueError。
- `test_histogram_bucket_static_and_monotonic`：非单调/重复/空 bucket 拒绝；默认静态单调。
- `test_render_is_bytes_and_ctype`：bytes + `text/plain`。
- `test_registries_isolated` / `test_new_registry_independent_of_default`：不同 registry/默认
  catalog 互不污染。

### 4.3 Trace 传播与采样（§8.3）

- `test_disabled_returns_none_and_zero_exporter`：disabled 不 import/不构造 OTLPSpanExporter。
- `test_ratio_1_samples_root_span` / `test_ratio_0_drops_root_span`：ratio 边界。
- `test_always_sample_operations_sample_at_ratio_0[3]`：execution/ledger/reconciliation 无父时
  100% 采样，且 span attribute operation 保留。
- `test_parent_sampled_inherited_by_child` / `test_parent_dropped_inherited_drop`：父采样决定
  继承（嵌套场景，`get_current_span(None)` 回退运行时 context）。
- `test_current_trace_ids_inside_span`：span 内 32/16 位 id。
- `test_w3c_inject_extract_roundtrip`：traceparent 注入/提取 trace id 一致；不传播 baggage。
- `test_invalid_carrier_fails_closed`：非法 traceparent/空 carrier 无父不抛。
- `test_sensitive_span_attribute_rejected[4]`：password/prompt/Authorization/request_body 键
  拒绝；`test_sensitive_value_in_allowed_key_redacted`：model="Bearer abc123" → `[REDACTED]`。
- `test_invalid_span_name_and_operation_rejected`：非法 name / `has space` / 超长 / `*_id` 拒绝。
- `test_reconfigure_and_shutdown_idempotent`：重复 configure + 幂等 shutdown。

### 4.4 配置（§8.4）

- `test_obs_defaults` / `test_obs_env_override` / `test_obs_level_validation` /
  `test_obs_service_name_invalid[4]` / `test_obs_service_version_invalid[5]` /
  `test_obs_ratio_bounds` / `test_obs_timeout_bounds`。
- `test_obs_otel_endpoint_valid[4]`（DNS/DNS+port/`/`/`/v1/traces` 原样保留）与
  `test_obs_otel_endpoint_invalid[12]`（ftp/userinfo/query/fragment/坏 path/坏 port/空 port/
  whitespace/http 无 allow/control/`//host`）。
- `test_obs_otel_enabled_requires_endpoint` / `test_obs_otel_http_requires_allow_insecure` /
  `test_obs_endpoint_validated_when_disabled_too` / `test_env_example_contains_all_obs_keys`。

### 4.5 回归

- WP-00 全量无退化：config 93、log 15、metric 33、trace 16、contract 18、store 35、local 24、
  s3 117、trading 417、全量 628、双前缀 0、`git diff --check` 干净。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞（如实记录）：
- `main.py`/lifespan/metrics endpoint/健康检查/Artifact factory 的接入属 `WP-00d2`（本任务只
  提供 primitives + typed config，不接 lifespan）。
- 真实 OTLP collector conformance 未执行（无真实 collector 地址；`OTEL_ENABLED` 默认 false，
  测试注入 in-memory exporter，零网络）。
- 不记录 AI prompt/response；AI invocation 业务证据表属 WP-02。Trace/metrics 只做技术关联，
  不作为审计账本（业务事实在 PostgreSQL/Artifact）。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/config.py serve/.env.example serve/requirements.txt \
  serve/tests/trading/test_v2_config.py serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -rf serve/app/observability serve/tests/trading/test_v2_log_redaction.py \
  serve/tests/trading/test_v2_metric_cardinality.py \
  serve/tests/trading/test_v2_trace_context.py \
  serve/docs/manifests/wp-00d1-observability-foundation.md
```

- 回到 WP-00c2-r3 交付状态；依赖可保留（新增 4 包不进生产路径，`OTEL_ENABLED=false`）；
  无迁移、Redis、Provider 或业务数据副作用；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00d1-observability-foundation.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
cd10e0cf26b60077fc0d673a860b1576899684ac46ba5dadc0b44f559c53e427
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00d1-observability-foundation.md | sha256sum
```
