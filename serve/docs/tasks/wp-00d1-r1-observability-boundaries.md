# WP-00d1-r1 — 可观测性脱敏与 Provider 生命周期整改

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00d1-r1-observability-boundaries.md`。
> 最后更新：2026-08-08 15:07 EDT。依赖：`WP-00d1` 已交付但审查未接受；本任务接受前
> `WP-00d2` 继续阻塞。

## 1. 审查结论与复现

00d1 的 config、metrics、传播与基本日志实现有效；独立复验为 93 config、15 log、33 metric、
16 trace、417 trading、628 full tests，manifest SHA
`cd10e0cf26b60077fc0d673a860b1576899684ac46ba5dadc0b44f559c53e427` 一致。仍有三个同域 P1：

1. **Span attribute 值泄密**：`tracing.py:119-135` 只识别少量关键词。允许键
   `model="https://alice:hunter2@host/path"` 被 InMemorySpanExporter 原样收到；Basic/Cookie/
   passphrase/private-key/signature/token/credential 等合同形态也未完整覆盖。
2. **日志格式化异常绕过脱敏**：JSON/Text formatter 在 redaction 前调用 `record.getMessage()`。
   当 `%s` 参数的 `__str__()` 抛 `RuntimeError("TOPSECRET ...")` 时，stdlib 输出 raw “Logging error”
   traceback、异常消息和对象参数，既丢失结构化日志又泄密。
3. **Trace provider 重配置会泄漏后台资源**：连续两次 `configure_tracing(enabled=True)` 不 shutdown
   旧 provider/exporter；`shutdown` 后再 disabled configure 还可能二次 shutdown。当前“幂等”测试在
   重配置前手动 shutdown，未覆盖真实重配置路径。

## 2. 目标与用户价值

确保进入日志或 exporter 的任何允许字段都经过同一敏感文本边界，并让 Trace provider 在启用、
重配置、禁用、关闭和初始化失败时具有确定、无泄漏的生命周期。本次只关闭上述边界，不接 lifespan。

## 3. 允许修改

```text
serve/app/observability/logging.py
serve/app/observability/tracing.py
serve/tests/trading/test_v2_log_redaction.py
serve/tests/trading/test_v2_trace_context.py
serve/docs/manifests/wp-00d1-r1-observability-boundaries.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 config、metrics、依赖、`.env.example`、`__init__.py`、main/lifespan、数据库、Redis、
Artifact、V1、既有 task/manifest 或前端。不得扩大 allowlist、增加 exporter 或自动 instrumentation。

## 4. 精确整改合同

### 4.1 日志消息必须先安全格式化再脱敏

- JSON/Text formatter 共用一个安全 helper；正常 `record.getMessage()` 行为不变。
- `record.getMessage()` 或 exception 格式化抛普通 `Exception` 时，输出固定、无参数内容的占位符，
  只允许包含异常**类型名**，不得包含异常 message、原始 `record.args`、对象 repr 或 traceback。
- 不捕获 `KeyboardInterrupt/SystemExit`。格式化失败不得触发 stdlib “Logging error”，也不得中断业务。
- JSON 仍是单行合法对象；Text 仍是受控单行。既有 message/exception redaction 全部保留。
- 测试至少覆盖 `__str__` 和 exception `__str__` 抛错，注入 `TOPSECRET` 后对完整 stderr/output 断言
  不出现 secret、对象 repr 或 “Logging error”。

### 4.2 Span attribute 复用完整敏感文本边界

- 对允许 key 的 string value 使用与日志一致的清洗能力；不得在 tracing 中维护更窄的第二套规则。
- 至少覆盖 Bearer、Basic、Authorization、Cookie、URL userinfo、password/secret/API key、passphrase、
  private key、signature、access/refresh token、credential、prompt/body/tool/raw payload 形态。
- 可以保留非敏感前后文，但敏感值必须变成 `[REDACTED]`；若无法安全部分清洗，整个 attribute 值置
  `[REDACTED]`。原输入对象不得修改。
- 不允许 arbitrary object/bytes 通过 SDK 隐式 `str/repr`；仅接受 OTel 合法 scalar 和有限同类型
  scalar sequence，其他类型 `ValueError`。序列每个字符串元素同样脱敏。
- 补表驱动 exporter 断言：导出的所有 attribute 字符串均不含注入 secret/userinfo/token。

### 4.3 Trace provider 生命周期必须确定

- `configure_tracing(enabled=True)` 成功构造新 provider 后再原子替换模块引用，并对旧 provider
  **恰好 shutdown 一次**；新 provider/exporter 构造失败时保留旧 provider 可用，且不误 shutdown。
- `configure_tracing(enabled=False)` 对现存 provider shutdown 一次并清空引用；重复 disabled 不重复。
- `shutdown_tracing()` shutdown 当前 provider 一次并清空引用；重复调用为 no-op。关闭后
  `start_span()` 使用 no-op tracer，不再向已关闭 exporter 写入。
- 先验证 ratio/timeout/service/endpoint 所需参数，再改变旧 provider 状态；不因坏配置破坏当前链。
- 不调用全局 `set_tracer_provider`，不改变 sampling/propagation/attribute allowlist 语义。

## 5. 必测证据

1. JSON 与 Text 各覆盖坏 `__str__`；exception formatter 坏 `__str__`；输出可解析/受控且无
   `TOPSECRET`、raw args、对象地址和 “Logging error”。
2. Span value 表驱动覆盖 §4.2 全部形态，含 URL userinfo、Basic、Cookie、signature/token；导出值
   脱敏，原输入不变；非法 object/bytes/混合或超限序列拒绝。
3. provider A→B：A shutdown=1、B 可导出；B→disabled：B shutdown=1；重复 disabled/shutdown 不增计数。
4. 新 exporter/provider 构造失败时 A shutdown=0 且 A 继续导出；坏 ratio/timeout 同样不改变 A。
5. 原 15 log、16 trace 以及全部 config/metric/WP-00 回归继续通过。

## 6. 验收命令

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests
.venv/bin/pytest -q tests/trading/test_v2_log_redaction.py
.venv/bin/pytest -q tests/trading/test_v2_trace_context.py
.venv/bin/pytest -q tests/trading/test_v2_config.py
.venv/bin/pytest -q tests/trading/test_v2_metric_cardinality.py
.venv/bin/pytest -q tests/trading
.venv/bin/pytest -q
git diff --check
```

## 7. 交付、回滚与非目标

创建且只创建 `serve/docs/manifests/wp-00d1-r1-observability-boundaries.md`；记录三类复现与关闭证据、
真实命令/数量、blocker、回滚及可复现 SHA。更新两个索引为 `DONE（待审）`，保持 R1 为当前任务，
等待用户再次说“完成”。不得创建 00d2、提交或推送。

回滚恢复允许代码/测试/索引并删除 R1 manifest；无迁移、网络、Redis、Provider 或业务数据副作用。
本任务不接 main/lifespan/metrics endpoint，不修改业务审计链，不新增依赖，不处理 Base 外部 handler。
