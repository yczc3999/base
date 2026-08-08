# COMPLETION MANIFEST — WP-00d1-r2 · 敏感文本覆盖最终整改

- Work package: `WP-00` 子任务 `WP-00d1-r2`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00d1-r1`（REMEDIATION_REQUIRED，唯一生产 P1 = 敏感文本覆盖仍窄：private_key/body/tool/raw_payload/token/Set-Cookie 6 项仍可原样进 exporter）；本整改接受前 `WP-00d2` 继续阻塞
- 规范依据: `serve/docs/tasks/wp-00d1-r2-sensitive-text-coverage.md`；`serve/docs/v2-implementation-contract.md` §3/§12–§15；`serve/docs/polymarket-v2-platform-design.md` §4
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/observability/logging.py` | 修改 | `_KV_SECRET_KEYWORDS` 扩展为完整词表（词间 `[ _-]*`：password/passwd/secret/apikey/api key/authorization/cookie/set-cookie/passphrase/private key/signature/access·refresh·id token/credential/prompt/request·response body/tool input·output/raw payload/payload/token）；`_PEM_RE`（RSA/EC/OPENSSH/DSA/ENCRYPTED PRIVATE KEY 整段脱敏）；`_COOKIE_HEADER_RE` 匹配 `Cookie`/`Set-Cookie`（整段 header value 脱敏、保留 header 名、换行后其他 header 保留）；`_clean_string` 加 `MAX_CLEAN_STRING_LEN=4096` 有界截断后再清洗 |
| `serve/app/observability/tracing.py` | 修改 | `_sanitize_attributes` 序列：上限 64 项 + 同质 `type(v) is first`（`[1, True]` 拒，bool 不得作为 int 混入）；`configure_tracing` 把 ratio/timeout 校验移到最前（enabled/disabled 两路径都先校验，`enabled=False, ratio=-1`/`timeout=0` 保留旧 provider 不 shutdown） |
| `serve/tests/trading/test_v2_log_redaction.py` | 修改 | 20 → 61：新增共享 `SENSITIVE_MATRIX`（27 形态，含 6 复现 + PEM + Set-Cookie）、`NON_SENSITIVE_EXAMPLES`（10 反例）、大小写/hyphen 变体、Cookie/Set-Cookie 多 header、PEM 多行、超长截断 |
| `serve/tests/trading/test_v2_trace_context.py` | 修改 | 38 → 80：import 同一 `SENSITIVE_MATRIX`/`NON_SENSITIVE_EXAMPLES` 经 span exporter 断言、64/65 序列、`[1, True]` 拒、超长 span 值截断、disabled+坏 ratio/timeout 保留旧 provider |
| `serve/docs/manifests/wp-00d1-r2-sensitive-text-coverage.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` / `serve/docs/tasks/README.md` | 修改 | 00d1-r2 标 DONE（待审） |

范围外未动：config、metrics、依赖、`__init__.py`、main/lifespan、数据库、Redis、Artifact、V1、
既有 task/manifest。**未遇 `BLOCKED_CONTRACT`**：不建立 tracing 专用第二套敏感规则；不增加依赖
或 allowlist。

---

## 2. 实现内容

### 2.1 共用敏感字符串清洗器完整矩阵（§3.1–3.5）

- `_KV_SECRET_KEYWORDS`：覆盖大小写及 `-`/`_`/空格 分隔变体的赋值、JSON、header 形态——
  password/passwd/secret/api key/authorization/cookie/set-cookie/passphrase/private key/signature/
  access·refresh·id token/credential/prompt/request·response body/tool input·output/raw payload/
  payload/token。词间分隔 `[ _-]*` 使 `private_key=`、`private-key=`、`PRIVATE KEY=`、`private key=`
  全部命中。6 个独立复现（`private_key=`、`response_body=`、`tool_output=`、`raw_payload=`、
  `token=`、`Set-Cookie:`）全部关闭。
- `_PEM_RE`：`-----BEGIN (RSA|EC|OPENSSH|DSA|ENCRYPTED )?PRIVATE KEY-----...-----END ...-----`
  （DOTALL）整段 `[REDACTED]`，多行 PEM 不残留任何 base64/DEK-Info。
- `_COOKIE_HEADER_RE`：`Cookie`/`Set-Cookie` 的 header value 整体脱敏、保留 header 名；以
  `[^;\n]+` 为界，换行后其他 header（如 `X-Custom: keep`）保留。
- `MAX_CLEAN_STRING_LEN=4096`：超限输入先有界截断（`[TRUNCATED]`）再清洗，日志/span 不复制
  无限字符串。
- 非敏感不误伤（表驱动 10 例）：`side=YES`、`content_type=json`、`model=grok-3`、`attempt_id=a1`、
  `chain_id=c1`、`status=ok`、`result=pass`、`hash=abc123def`、`episode_id=e1`、`execution_id=ex1`。

### 2.2 Span scalar sequence 有界 + disabled 先校验（§3.6–3.7）

- 序列上限 64 项（65 项 ValueError）；同质判断改 `type(v) is first`（`[1, True]`/`(True, 1)`
  拒绝，bool 不得作为 int 子类混入）；字符串元素逐一复用日志清洗。
- `configure_tracing` 的 ratio/timeout 校验移到最前：enabled 与 disabled 两条路径都先校验，
  `enabled=False, ratio=-1` 或 `timeout=0` 抛 ValueError 且保留旧 provider（不 shutdown、继续导出）。

### 2.3 不输出原始 secret（§3.8）

- 测试统一用固定 marker（`SENSITIVE_MATRIX` 内的禁止子串）只断言"不存在"；manifest/异常/日志不
  含真实密钥。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests
# → exit 0

# 2) log redaction（20 → 61）
.venv/bin/pytest -q tests/trading/test_v2_log_redaction.py
# → 61 passed in 0.12s

# 3) trace context（38 → 80）
.venv/bin/pytest -q tests/trading/test_v2_trace_context.py
# → 80 passed in 0.16s

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
# → 267 passed in 0.48s

# 7) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 527 passed in 1.97s

# 8) 全量回归
.venv/bin/pytest -q
# → 738 passed, 1 warning in 4.22s（1 warning = conftest event_loop 弃用告警，同各 WP）

# 9) git diff --check
git diff --check
# → 无输出，exit 0

# 10) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

---

## 4. 关键证据

### 4.1 6 个独立复现关闭 + PEM + Set-Cookie（§4 前三条）

- `test_redact_full_sensitive_matrix[27]` 与 `test_span_value_full_sensitive_matrix_redacted[27]`：
  同一 `SENSITIVE_MATRIX` 表驱动——`private_key=ZXCV1234`、`PRIVATE KEY=ZXCV9999`、
  `private-key=ZXCV-7`、`response_body=MODEL_RESPONSE_789`、`request-body=REQ_BODY_111`、
  `tool_output=TOOL_RESULT_456`、`tool_input=TOOL_INPUT_222`、`raw_payload=RAW_DATA_321`、
  `payload=PLD_444`、`token=TOKEN_VALUE_999`、`access_token=AT_555`、`refresh-token=RT_666`、
  `id_token=IT_777`、`Set-Cookie: opaque=COOKIE_VALUE_123`、`set_cookie=SC_888`、
  `Authorization: Bearer authval999`、`Basic base64cred`、`https://alice:hunter2@host/path`、
  `password=pass99`、`secret_key=sec88`、`passphrase=pp77`、`signature=sig66`、
  `credential=cred55`、`prompt=prompt44`、RSA/EC/OPENSSH PEM——经日志 redact 与 span exporter
  导出均不含禁止子串且含 `[REDACTED]`。
- `test_redact_cookie_set_cookie_multi_header_keeps_others`：
  `Cookie: sid=abc\nSet-Cookie: theme=dark\nX-Custom: keep-me` → 两 cookie value 脱敏、
  `X-Custom: keep-me` 保留。
- `test_redact_pem_multiline_fully_hidden`：多行 ENCRYPTED PRIVATE KEY（含 DEK-Info）无残留。
- `test_redact_case_and_separator_variants`：`PRIVATE_KEY=`/`Private-Key=`/`API_KEY=`/
  `AccessToken=`/`SET-COOKIE:` 大小写与分隔变体全命中。

### 4.2 非敏感反例（§4）

- `test_redact_non_sensitive_not_overredacted[10]` / `test_span_value_non_sensitive_not_overredacted[10]`：
  日志与 span 均原样保留（`side=YES`、`content_type=json`、`model=grok-3`、业务 ID 等）。

### 4.3 序列与超长有界（§4）

- `test_span_sequence_64_items_ok_65_rejected`：64 项合法、65 项 ValueError。
- `test_span_sequence_bool_int_mix_rejected`：`[1, True]`/`(True, 1)` 拒绝（`type(v) is first`）。
- `test_redact_long_string_truncated_before_clean` / `test_span_long_string_truncated`：超长
  （10000 字符）输入截断到 <4200、不复制无限内容、含 `[REDACTED]`/`[TRUNCATED]`。

### 4.4 disabled + 坏配置保留旧 provider（§4）

- `test_configure_disabled_bad_ratio_keeps_old_provider` / `test_configure_disabled_bad_timeout_keeps_old_provider`：
  active provider 下 `enabled=False, ratio=-1`/`timeout=0` → ValueError、模块 `T._provider` 引用不变、
  旧 provider 继续导出。

### 4.5 回归

- 原 20 log、38 trace、93 config、33 metric 全部继续通过；trading 527、全量 738、双前缀 0、
  `git diff --check` 干净；R2 改动仅落在 logging.py、tracing.py、test_v2_log_redaction.py、
  test_v2_trace_context.py 四文件。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞（如实记录，沿用 WP-00d1 系列）：
- lifespan/metrics endpoint/健康检查/Artifact factory 接入属 `WP-00d2`。
- 真实 OTLP collector conformance 未执行（`OTEL_ENABLED` 默认 false，测试注入 in-memory
  exporter 零网络）。
- 共用清洗器对 `secretary=`/`content_token=` 等罕见含敏感子串的词可能过度脱敏（宁可过度不
  泄露）；表驱动非敏感反例已锁定 10 个常规无敏感场景。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/observability/logging.py serve/app/observability/tracing.py \
  serve/tests/trading/test_v2_log_redaction.py serve/tests/trading/test_v2_trace_context.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/docs/manifests/wp-00d1-r2-sensitive-text-coverage.md
```

- 回到 WP-00d1-r1 交付状态；原 00d1/00d1-r1 completion manifest 与已冻结 R1 不受影响；无迁移、
  网络、Redis、Provider 或业务数据副作用；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00d1-r2-sensitive-text-coverage.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
e2d08336a9dff253e53782220c7e17b5a614b510eb934d4e7e8f4eaaff5edf8b
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00d1-r2-sensitive-text-coverage.md | sha256sum
```
