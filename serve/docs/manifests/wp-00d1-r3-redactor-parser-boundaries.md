# COMPLETION MANIFEST — WP-00d1-r3 · Redactor 解析边界最终整改

- Work package: `WP-00` 子任务 `WP-00d1-r3`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00d1-r2`（REMEDIATION_REQUIRED，唯一解析 P1 = 长 PEM / quoted value 遇空格停 / Cookie 只到第一个分号）；本整改接受前 `WP-00d2` 继续阻塞
- 规范依据: `serve/docs/tasks/wp-00d1-r3-redactor-parser-boundaries.md`；`serve/docs/v2-implementation-contract.md` §3/§12–§15；`serve/docs/polymarket-v2-platform-design.md` §4
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/observability/logging.py` | 修改 | `_PEM_COMPLETE_RE` + `_PEM_INCOMPLETE_RE`（完整块 + BEGIN 到窗口末尾，不要求 END）；`_QUOTED_VALUE_RE`（配对引号值含 whitespace/转义/标点/换行，DOTALL 跨多行 JSON）+ `_UNCLOSED_QUOTED_RE`（未闭合到窗口末尾）；词表拆 `_KV_SENTENCE_KEYWORDS`（body/prompt/tool/raw payload 到行尾）+ `_KV_SINGLE_KEYWORDS`（单值到分隔符）；`_KV_SENTENCE_RE` value 首字符非空白/引号/换行（防 `\s*` 回溯绕过引号结构）；`_COOKIE_HEADER_RE` 到行尾（`[^\n]*`，LF/CRLF） |
| `serve/tests/trading/test_v2_log_redaction.py` | 修改 | 61 → 91：新增 PEM 长度矩阵（4095/4096/4097/10000 + 完整/无 END）、多类型无 END PEM、quoted 空格/逗号/分号/escaped/多行 JSON、未闭合 quote、无引号 sentence 到行尾、Cookie 多 opaque/属性/LF·CRLF、输出长度上限；更新 R2 cookie 断言为新语义（整行红） |
| `serve/docs/manifests/wp-00d1-r3-redactor-parser-boundaries.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` / `serve/docs/tasks/README.md` | 修改 | 00d1-r3 标 DONE（待审） |

范围外未动：tracing 实现、config、metrics、依赖、main/lifespan、`__init__.py`、既有 task/manifest、
数据库/Redis/Artifact/V1。Span 经现有共用清洗器自动获得修复，不建第二套规则。

---

## 2. 实现内容（§3 精确合同）

### 2.1 有界 + 完整/不完整 PEM（§3.1）

- `_clean_string` 先做有界副本（`MAX_CLEAN_STRING_LEN=4096`，截断标记），所有正则只在有界副本
  运行（防 ReDoS/大内存）。
- `_PEM_COMPLETE_RE`：BEGIN..END 完整块（DOTALL）→ `[REDACTED]`。
- `_PEM_INCOMPLETE_RE`：保留窗口内出现合法 `-----BEGIN ... PRIVATE KEY-----` 即从 BEGIN 到窗口
  末尾全部 `[REDACTED]`——不要求 END 存在；长 PEM 被截断（END 在窗口外）或完全无 END 均不泄露
  BEGIN 后的 marker/主体。先完整后不完整，顺序无关泄露。

### 2.2 quoted value 完整覆盖（§3.2）

- `_QUOTED_VALUE_RE`：双/单引号值覆盖 whitespace、转义字符（`\\.`）、常见标点与换行（多行
  JSON），直到配对引号（DOTALL 使值跨行）。
- `_UNCLOSED_QUOTED_RE`：未闭合 quote 从起始引号清洗到保留窗口末尾（跨行）。
- `_KV_SENTENCE_RE`：无引号 body/prompt/tool/raw payload 保守清洗到行尾；value 首字符
  `[^\s"'\n]` 非空白/引号/换行，防 `\s*` 回溯把分隔空格塞进 value 而绕过已脱敏 quoted 的引号
  结构（`{"prompt": "[REDACTED]"}` 不再被二次匹配吞引号）。
- `_KV_SINGLE_RE`：password/secret/token 等单值到空白/结构分隔符（`[^\s&,;\"]+`），保留配对引号。

### 2.3 Cookie/Set-Cookie 整行（§3.3）

- `_COOKIE_HEADER_RE`：从 header 名后 `[^\n]*` 到行尾（不是第一个分号）；LF 与 CRLF 均覆盖；
  换行后其他 header（如 `X-Custom: keep`）保留。

### 2.4 输出上限（§3.4）

- 输出固定 ≤ `MAX_CLEAN_STRING_LEN + len("[TRUNCATED]")` 数量级；有界副本保证正则不处理无限输入。

### 2.5 保持既有语义（§3.5）

- 非敏感反例、URL/Bearer/Basic、KV/JSON、PEM 完整块、序列与 provider 生命周期语义全部保留。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests
# → exit 0

# 2) log redaction（61 → 91）
.venv/bin/pytest -q tests/trading/test_v2_log_redaction.py
# → 91 passed in 5.21s

# 3) trace context（80，span 经共用清洗器自动获得修复，无新增）
.venv/bin/pytest -q tests/trading/test_v2_trace_context.py
# → 80 passed in 0.15s

# 4) 观测性合计
.venv/bin/pytest -q tests/trading/test_v2_log_redaction.py \
  tests/trading/test_v2_trace_context.py
# → 171 passed in 6.58s

# 5) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 557 passed in 8.29s

# 6) 全量回归
.venv/bin/pytest -q
# → 768 passed, 1 warning in 10.52s（1 warning = conftest event_loop 弃用告警，同各 WP）

# 7) git diff --check
git diff --check
# → 无输出，exit 0

# 8) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

---

## 4. 关键证据

### 4.1 PEM 长度与完整/不完整（§4）

- `test_redact_pem_lengths[10]`：body 4095/4096/4097/10000/4000/200 × 完整/无 END，`BODY_` marker
  与主体均不泄露、含 `[REDACTED]`。
- `test_redact_pem_kinds_without_end[5]`：RSA/EC/OPENSSH/DSA/ENCRYPTED 无 END 也整段红。
- `test_redact_pem_end_outside_4096_window`：END 在 4096 截断窗口之外 → BEGIN 到窗口末尾全红。
- `test_redact_pem_multiline_fully_hidden`（R2 保留）：ENCRYPTED PRIVATE KEY 多行（含 DEK-Info）无残留。

### 4.2 quoted value（§4）

- `test_redact_quoted_value_full[6]`：`prompt="Tell me TOPSECRET now"`、逗号/分号/escaped quote、
  单引号、多行 JSON `{"prompt": "line1\nline2 TOPSECRET"}`、`request_body={"a":1,"b":"TOPSECRET"}` ——
  `TOPSECRET` 全不泄、含 `[REDACTED]`；配对引号结构保留（`{"prompt": "[REDACTED]"}`）。
- `test_redact_unclosed_quoted_to_window_end`：`prompt="unclosed\nstill TOPSECRET` → 从起始引号
  到窗口末尾全红。
- `test_redact_unquoted_sentence_to_line_end`：`prompt=Tell me TOPSECRET now` /
  `tool_output=...` / `raw_payload=...` 整段到行尾红。

### 4.3 Cookie/Set-Cookie（§4）

- `test_redact_cookie_multiple_opaque_values`：`Cookie: a=SECRET1; opaque=SECRET2; theme=dark` →
  `Cookie: [REDACTED]`（整行，非第一个分号）。
- `test_redact_set_cookie_attributes`：`Set-Cookie: opaque=...; Path=/; HttpOnly` → 整行红。
- `test_redact_cookie_line_and_crlf[2]`：LF 与 CRLF 均覆盖；`X-Custom: keep-me` 保留。
- `test_redact_cookie_and_session_forms`（更新）：新语义整行红；`sid=`/`jwt=` 独立键值仍按
  key=value 脱敏；`side=`/`obsession=` 不误伤。

### 4.4 有界与回归（§4）

- `test_redact_output_length_cap`：10000 字符 `prompt=` 输入输出 ≤ 4096+标记+余量。
- `test_redact_non_sensitive_still_untouched_r3`：10 个非敏感反例原样。
- 原 61 log、80 trace 全部继续通过（仅 R2 cookie 断言更新为新语义）；trading 557、全量 768、
  双前缀 0、`git diff --check` 干净；R3 改动仅落在 logging.py + test_v2_log_redaction.py 两文件
  （trace 经共用清洗器自动获得修复，未改 tracing 实现）。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞（如实记录，沿用 WP-00d1 系列）：
- lifespan/metrics endpoint/健康检查/Artifact factory 接入属 `WP-00d2`。
- 真实 OTLP collector conformance 未执行（`OTEL_ENABLED` 默认 false，测试注入 in-memory
  exporter 零网络）。
- 无引号 sentence 保守策略宁可整段脱敏（含结构分隔符）；非敏感反例已锁定常规场景。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/observability/logging.py \
  serve/tests/trading/test_v2_log_redaction.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/docs/manifests/wp-00d1-r3-redactor-parser-boundaries.md
```

- 回到 WP-00d1-r2 交付状态；原 00d1/00d1-r1/00d1-r2 completion manifest 与已冻结 R1/R2 不受影响；
  无迁移、网络、Redis、Provider 或业务数据副作用；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00d1-r3-redactor-parser-boundaries.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
67b71a901da3942354509e8a7212d6607fe52bbe82c7574b4a68d2d466131015
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00d1-r3-redactor-parser-boundaries.md | sha256sum
```
