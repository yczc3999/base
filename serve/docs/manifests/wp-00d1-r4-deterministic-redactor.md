# COMPLETION MANIFEST — WP-00d1-r4 · 确定性有界 Redactor

- Work package: `WP-00` 子任务 `WP-00d1-r4`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00d1-r3`（REMEDIATION_REQUIRED，唯一 P1 = quoted value 正则拼接对相反/转义引号仍泄）；本整改接受前 `WP-00d2` 继续阻塞
- 规范依据: `serve/docs/tasks/wp-00d1-r4-deterministic-redactor.md`；`serve/docs/v2-implementation-contract.md` §3/§12–§15；`serve/docs/polymarket-v2-platform-design.md` §4
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/observability/logging.py` | 修改 | 删除 quoted/unclosed/sentence/single/cookie 正则拼接（`_QUOTED_VALUE_RE`/`_UNCLOSED_QUOTED_RE`/`_KV_*_RE`/`_COOKIE_*_RE` 及 repl helpers），替换为单次确定性 O(n) scanner（`_parse_key`/`_consume_value`/`_scan_assignments`）；保留 PEM/Bearer/userinfo marker 正则；`_normalize_assign_key` 去分隔归一 + 精确词表；最终硬裁剪 ≤ MAX_CLEAN_STRING_LEN+len("[TRUNCATED]")；bytes/bytearray → `<bytes length=N>` |
| `serve/tests/trading/test_v2_log_redaction.py` | 修改 | 91 → 101：新增 R4 三复现、quote 状态表（配对/未闭合/单双引号/相反/escaped/偶奇反斜杠/CRLF/多行）、repeated markers 硬上限、bytes marker、固定 seed 500 fuzz + 200 prompt 样本 |
| `serve/docs/manifests/wp-00d1-r4-deterministic-redactor.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` / `serve/docs/tasks/README.md` | 修改 | 00d1-r4 标 DONE（待审） |

范围外未动：tracing 实现、config、metrics、依赖、main/lifespan、`__init__.py`、数据库/Redis/
Artifact、V1、既有 task/manifest。Span 继续复用 logging 的唯一清洗器（tracing 测试零改动全过）。

---

## 2. 实现内容（§3 精确合同）

### 2.1 单次确定性 scanner（§3.1–3.2、3.6）

- 删除互相叠加的 quoted/unclosed/KV 正则，改用 `_scan_assignments`：单遍从左到右，每个位置
  最多被 `_parse_key` 读一次（成功定位 key+sep，或 `skip` 跳过整个非 assignment token 防重复
  扫描）→ 保证 **O(n)、O(n) 空间，n ≤ 4096，无回溯/递归**。
- `_parse_key`：识别允许字符组成的 key，可带单/双引号、可含内部空格（`private key`）；`skip`
  分支保证 O(n)（不因"读了 key 无 sep"而逐字符回扫）。
- `_normalize_assign_key`：`lower` + 去掉 `-`/`_`/space，精确匹配冻结词表（覆盖
  `access_token`/`access-token`/`AccessToken`/`access token` 等变体），**不用任意子串**（`side=`/
  `obsession=`/`content_type=` 不误伤）。

### 2.2 quote 状态由 scanner 处理（§3.3）

- `_consume_value`：quoted value 只把**与起始 quote 同类且未被奇数反斜杠转义**的 quote 当结束；
  相反 quote 是普通字符（`prompt="don't reveal..."`、`prompt='say "hello"...'` 不再提前失配）；
  `\` 转义 `\` 与 `"` 都按 `k += 2` 跳过（偶/奇反斜杠正确）。
- 找到配对 → 只替换 value（保留引号结构）；未找到 → **从 value 起点清洗到窗口末尾**（跨行，
  引号未闭合即 value 延伸到末尾；CRLF/LF 均覆盖）。
- sentence key（prompt/body/tool/raw payload/payload）unquoted 到行尾；single key 到分隔符；
  Cookie/Set-Cookie 从 header value 到行尾全红。

### 2.3 输出硬上限与 bytes（§3.4–3.5）

- `_clean_string` 先有界截断（≤4096+9），PEM/Bearer/userinfo 正则 → scanner → **最终硬裁剪**到
  `MAX_CLEAN_STRING_LEN + len("[TRUNCATED]")`；重复 1000 个 `token=x` / Cookie 行不扩张。
- `redact` 对 bytes/bytearray 输出固定 `<bytes length=N>`，不输出内容。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests
# → exit 0

# 2) log redaction（91 → 101）
.venv/bin/pytest -q tests/trading/test_v2_log_redaction.py
# → 101 passed in 0.28s

# 3) trace context（80，span 经共用清洗器自动获得修复，无改动）
.venv/bin/pytest -q tests/trading/test_v2_trace_context.py
# → 80 passed in 0.15s

# 4) 观测性合计
.venv/bin/pytest -q tests/trading/test_v2_log_redaction.py \
  tests/trading/test_v2_trace_context.py
# → 181 passed in 0.34s

# 5) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 567 passed in 2.16s

# 6) 全量回归
.venv/bin/pytest -q
# → 778 passed, 1 warning in 4.18s（1 warning = conftest event_loop 弃用告警，同各 WP）

# 7) git diff --check
git diff --check
# → 无输出，exit 0

# 8) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

---

## 4. 关键证据

### 4.1 R4 三复现 + quote 状态表（§4）

- `test_redact_r4_repros[3]`：`prompt="don't reveal TOPSECRET now`、`prompt='say "hello" then
  TOPSECRET now`、`prompt="escaped \" quote then TOPSECRET now` —— 相反/转义引号不再使 scanner
  提前失配，`TOPSECRET` 全不泄。
- `test_redact_quote_state_table`：配对单双引号、escaped quote、偶反斜杠、多行 JSON、未闭合跨行/
  CRLF/单引号/相反引号 全关。
- `test_redact_odd_even_backslash`：奇数反斜杠 `\"` 转义引号不闭合（未闭合到窗口末尾）；偶数
  `\\` 转义反斜杠后引号闭合（只替换 value，引号外独立文本合法保留）；`\\`+`\"` 组合未闭合全红。

### 4.2 有界与 bytes（§4）

- `test_redact_repeated_markers_length_hard_cap` / `test_redact_cookie_repeated_hard_cap`：重复
  1000+ 个 `token=x` / Cookie 行输出 ≤ 4096+10。
- `test_redact_bytes_never_output_content`：`b"TOPSECRET content"` → `<bytes length=17>`，
  bytearray/嵌套 dict 均只输出长度 marker，不输出内容。

### 4.3 确定性 fuzz（§4）

- `test_redact_fixed_seed_fuzz_500`：固定 seed 生成 500 个含 quote/backslash/delimiter 字符串：
  不抛异常、输出有界；嵌入 `prompt=TOPSECRETMARKER` 的样本不保留 marker。
- `test_redact_seeded_prompt_samples`：200 个 `prompt="<随机串>marker`（引号永不闭合）→
  marker 全红（`leak == 0`）。

### 4.4 回归（§4）

- 27 敏感矩阵 + 10 非敏感反例（log 与 span 各一遍）全部继续通过；R2/R3 的 PEM 长度/完整·不完整、
  quoted/sentence/cookie、序列与 provider 生命周期语义全部保留；trading 567、全量 778、双前缀 0、
  `git diff --check` 干净；R4 改动仅落在 logging.py + test_v2_log_redaction.py 两文件（trace 零改动）。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞（如实记录，沿用 WP-00d1 系列）：
- lifespan/metrics endpoint/健康检查/Artifact factory 接入属 `WP-00d2`。
- 真实 OTLP collector conformance 未执行（`OTEL_ENABLED` 默认 false，测试注入 in-memory
  exporter 零网络）。
- scanner 对无引号 sentence 的保守策略宁可整段脱敏（含结构分隔符）；非敏感反例已锁定常规场景。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/observability/logging.py \
  serve/tests/trading/test_v2_log_redaction.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/docs/manifests/wp-00d1-r4-deterministic-redactor.md
```

- 回到 WP-00d1-r3 交付状态；原 00d1/00d1-r1/00d1-r2/00d1-r3 completion manifest 与已冻结
  R1–R3 不受影响；无迁移、网络、Redis、Provider 或业务数据副作用；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00d1-r4-deterministic-redactor.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
4d8cf0200bcaaaa2d98a798adac4ab0a48c2e944505ed1f9932097a7155b0e2d
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00d1-r4-deterministic-redactor.md | sha256sum
```
