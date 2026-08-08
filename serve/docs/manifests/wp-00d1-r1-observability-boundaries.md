# COMPLETION MANIFEST — WP-00d1-r1 · 可观测性脱敏与 Provider 生命周期整改

- Work package: `WP-00` 子任务 `WP-00d1-r1`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00d1`（REMEDIATION_REQUIRED，三个同域 P1）；本整改接受前 `WP-00d2` 继续阻塞
- 规范依据: `serve/docs/tasks/wp-00d1-r1-observability-boundaries.md`；`serve/docs/v2-implementation-contract.md` §3/§12–§15；`serve/docs/polymarket-v2-platform-design.md` §4；`serve/docs/performance-cache-database-design.md` §12
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/observability/logging.py` | 修改 | 新增 `_safe_record_message`（`getMessage()` 抛普通 Exception → 仅含异常类型名的占位符，不捕获 KeyboardInterrupt/SystemExit、不触发 stdlib "Logging error"）与 `_safe_exc_text`（exception 文本格式化抛错 → 受控占位符）；JSON/Text formatter 均改用安全 helper；`_COOKIE_HEADER_RE`（`Cookie:`/`Set-Cookie:` 整段值脱敏）与 `_COOKIE_SECRET_RE`（sid/session/jwt/id_token/csrf/auth 词边界 key=value 脱敏） |
| `serve/app/observability/tracing.py` | 修改 | `_sanitize_attributes` 复用 logging 的 `_clean_string`（同一边界，不再维护第二套窄规则）；仅接受 OTel 合法 scalar + 同类型 scalar sequence，bytes/object/混合/空序列 ValueError，序列字符串元素逐一脱敏；`configure_tracing` 生命周期重构（先验证参数→构造新 provider→原子替换→旧 provider 恰 shutdown 一次；构造失败保留旧可用不误关；disabled 对现存 shutdown 一次清引用）；`shutdown_tracing` 幂等清引用 |
| `serve/tests/trading/test_v2_log_redaction.py` | 修改 | 15 → 19：新增坏 `__str__` 消息 JSON/Text/exception、坏 `__str__` 不破坏业务、cookie/session 脱敏与词边界；坏 `__str__` 测试用 `_emit_isolated` 隔离 pytest 注入的 root handler + monkeypatch stderr 只验证本模块边界 |
| `serve/tests/trading/test_v2_trace_context.py` | 修改 | 16 → 39：新增 span value 表驱动 10 形态脱敏、scalar sequence 逐元素脱敏、非法值拒绝矩阵、provider A→B 单次 shutdown、disabled 单次、构造失败保留旧、坏 ratio 保留旧、关闭后 start_span no-op |
| `serve/docs/manifests/wp-00d1-r1-observability-boundaries.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` / `serve/docs/tasks/README.md` | 修改 | 00d1-r1 标 DONE（待审） |

范围外未动：config、metrics、依赖、`.env.example`、`__init__.py`、main/lifespan、数据库、Redis、
Artifact、V1、既有 task/manifest。**未遇 `BLOCKED_CONTRACT`**：不扩大 allowlist、不增加 exporter/
自动 instrumentation。

---

## 2. 实现内容

### 2.1 日志消息先安全格式化再脱敏（§4.1）

- `_safe_record_message(record)`：`record.getMessage()` 正常路径行为不变；当 `%s` 参数 `__str__()`
  抛普通 Exception 时输出固定占位符 `<log-message-format-failed: <异常类型名>>`——只含异常类型名，
  不含异常 message、record.args、对象 repr 或 traceback；不捕获 KeyboardInterrupt/SystemExit。
- `_safe_exc_text(record)`：`traceback.format_exception` 抛普通 Exception 时输出
  `<exception-format-failed: <异常类型名>>`；stdlib 对坏 `__str__` 异常本身优雅降级（不泄原始消息）。
- JSON/Text formatter 均在 redaction 前改用 `_safe_record_message`/`_safe_exc_text`；JSON 仍是单行
  合法对象、Text 仍是受控单行；不触发 stdlib "Logging error"，不中断业务。
- 新增 `_COOKIE_HEADER_RE`（`Cookie:`/`Set-Cookie:` 整段值 `[REDACTED]`，保留前缀）与
  `_COOKIE_SECRET_RE`（`\bsid|session|jwt|id_token|csrf|auth\b` 词边界 key=value 脱敏，`side=`/
  `obsession=` 不误伤）。该边界同时服务日志与 span 值。

### 2.2 Span attribute 复用完整敏感文本边界（§4.2）

- `_sanitize_attributes` 对允许键的 string value 调用 logging 的 `_clean_string`（唯一敏感文本
  边界，不维护第二套规则）；原输入对象不修改（新 dict / 新 tuple）。
- 覆盖 Bearer、Basic、Authorization、Cookie、URL userinfo、password/secret/API key、passphrase、
  private key、signature、access/refresh token、credential、prompt/body/tool/raw payload、sid/
  session/jwt 形态；可保留非敏感前后文，敏感值 `[REDACTED]`。
- 仅接受 OTel 合法 scalar（str/bool/int/float）与同类型非空 scalar sequence；bytes/arbitrary
  object/混合/空序列 ValueError，绝不交给 SDK 隐式 str/repr；序列每个字符串元素同样脱敏。

### 2.3 Trace provider 生命周期确定（§4.3）

- 模块不变量：`_provider` 要么 None、要么**未 shutdown** 的活跃 provider；所有关闭/替换路径对其
  shutdown 恰一次。
- `configure_tracing(enabled=True)`：先验证 ratio/timeout（坏配置不改变旧状态）→ 构造新 provider
  （含 exporter；构造抛错保留旧 provider 可用、不误关）→ **原子替换** `_provider` → 对旧 provider
  `shutdown()` 恰一次。
- `configure_tracing(enabled=False)`：对现存 provider shutdown 一次并清引用；重复 disabled no-op。
- `shutdown_tracing()`：shutdown 当前 provider 一次并清引用；重复调用 no-op。关闭后 `start_span`
  用全局 no-op tracer，不向已关闭 exporter 写入。
- 不调用全局 `set_tracer_provider`；sampling/propagation/attribute allowlist 语义不变。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests
# → exit 0

# 2) log redaction（新增 4，15 → 19）
.venv/bin/pytest -q tests/trading/test_v2_log_redaction.py
# → 19 passed in 0.09s

# 3) trace context（新增 23，16 → 39）
.venv/bin/pytest -q tests/trading/test_v2_trace_context.py
# → 39 passed in 0.12s

# 4) typed config（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_config.py
# → 93 passed in 0.20s

# 5) metric cardinality（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_metric_cardinality.py
# → 33 passed in 0.09s

# 6) 观测性四项合计
.venv/bin/pytest -q tests/trading/test_v2_config.py \
  tests/trading/test_v2_log_redaction.py \
  tests/trading/test_v2_metric_cardinality.py \
  tests/trading/test_v2_trace_context.py
# → 184 passed in 0.36s

# 7) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 444 passed in 1.89s

# 8) 全量回归
.venv/bin/pytest -q
# → 655 passed, 1 warning in 4.15s（1 warning = conftest event_loop 弃用告警，同各 WP）

# 9) git diff --check
git diff --check
# → 无输出，exit 0

# 10) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

---

## 4. 关键证据

### 4.1 日志安全格式化（§5.1）

- `test_bad_message_str_json_safe` / `test_bad_message_str_text_safe`：`%s` 参数 `__str__` 抛
  `RuntimeError("TOPSECRET ...")` → JSON 输出 `<log-message-format-failed: RuntimeError>`（Text
  同样含占位符）；完整输出无 `TOPSECRET`、`0xDEADBEEF`（对象 repr）、`value=`（raw args）、
  "Logging error"。
- `test_bad_exc_str_json_safe`：exception 文本格式化坏 `__str__` → JSON 单行、`level=ERROR`、
  `exception` 键为受控摘要，无 `TOPSECRET`/"Logging error"。
- `test_bad_str_does_not_break_business`：坏 `__str__` 后正常日志照常输出（两行，第二条 message
  不变）。
- `test_redact_cookie_and_session_forms`：`Cookie: sid=abc`→`Cookie: [REDACTED]`、
  `Set-Cookie: session=xyz`→脱敏、`sid=`/`jwt=`→`[REDACTED]`；词边界 `side=`/`obsession=` 不误伤。

### 4.2 Span value 完整边界（§5.2）

- `test_span_value_sensitive_forms_redacted[10]`：URL userinfo（`alice:hunter2` 不泄）、Basic、
  Bearer、Cookie、password、api_key、signature、access_token、credential、passphrase 全部脱敏，
  导出的 attribute 不含注入 secret/userinfo/token。
- `test_span_value_scalar_sequence_sanitized`：`("alice","https://u:p@host","Bearer abc")` →
  `("alice","https://[REDACTED]@host","Bearer [REDACTED]")`，原输入 list 不变。
- `test_span_attribute_invalid_value_rejected[6]`：bytes/object/混合序列/空序列/非 scalar 元素/
  None 全部 ValueError（不交 SDK 隐式 str/repr）。
- 既有 `test_sensitive_value_in_allowed_key_redacted` 更新断言为复用日志边界的
  `"Bearer [REDACTED]"`。

### 4.3 Provider 生命周期（§5.3/5.4）

- `test_reconfigure_shuts_down_old_exactly_once`：A→B，A shutdown 恰一次、B 可导出；shutdown 后
  重复 no-op（计数不变）。
- `test_configure_disabled_shuts_down_once`：enabled→disabled，现 provider shutdown 一次清引用；
  重复 disabled no-op。
- `test_configure_failure_keeps_old_provider`：新 provider 构造抛错 → 旧 provider 保留（`_provider`
  仍为 A、A shutdown 计数 0）、A 继续导出。
- `test_bad_ratio_keeps_old_provider`：坏 ratio 在改变旧状态前验证 → A 仍导出、不 shutdown。
- `test_shutdown_then_start_span_no_op`：关闭后 `start_span` 无有效 span（`current_trace_ids()==
  (None, None)`）、不向已关闭 exporter 写入、不抛错。

### 4.4 回归

- 原 15 log、16 trace 全部继续通过；config 93、metric 33、trading 444、全量 655、双前缀 0、
  `git diff --check` 干净；R1 改动仅落在 logging.py、tracing.py、test_v2_log_redaction.py、
  test_v2_trace_context.py 四文件。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞（如实记录，沿用 WP-00d1）：
- lifespan/metrics endpoint/健康检查/Artifact factory 接入属 `WP-00d2`。
- 真实 OTLP collector conformance 未执行（`OTEL_ENABLED` 默认 false，测试注入 in-memory
  exporter 零网络）。
- 坏 `__str__` 测试需隔离 pytest 注入的 root log handler（pytest 用 base Formatter 格式化会
  冒泡）；生产环境 root 只有本模块 handler，`_safe_record_message` 即保证不触发 "Logging error"。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/observability/logging.py serve/app/observability/tracing.py \
  serve/tests/trading/test_v2_log_redaction.py serve/tests/trading/test_v2_trace_context.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/docs/manifests/wp-00d1-r1-observability-boundaries.md
```

- 回到 WP-00d1 交付状态；原 00d1 completion manifest 与已冻结 R1 不受影响；无迁移、网络、Redis、
  Provider 或业务数据副作用；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00d1-r1-observability-boundaries.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
a8e6ce006f8859c3c1af820428ea2d174820a7b3387eaeb454b80453ff86cffd
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00d1-r1-observability-boundaries.md | sha256sum
```
