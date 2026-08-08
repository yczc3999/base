# COMPLETION MANIFEST — WP-00c2-r2 · S3 StreamingBody 与 Endpoint 最终整改

- Work package: `WP-00` 子任务 `WP-00c2-r2`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00c2-r1`（REMEDIATION_REQUIRED，三个合同边界 + 三个响应/异常缺口）；本整改接受前 `WP-00d` 继续阻塞
- 规范依据: `serve/docs/tasks/wp-00c2-r2-artifact-stream-endpoint.md`；`serve/docs/v2-implementation-contract.md` §3/§12–§15；`serve/docs/performance-cache-database-design.md` §6.2/§11/§14–§15
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/services/artifact_store/drivers/s3.py` | 修改 | `_read_bounded` 有界读取 helper（full GET/Range 共用）；`_TRANSPORT_ERRORS` 加 `HTTPClientError` 基类；`_parse_content_length` 严格类型；stored-sha 先 isinstance str；ChecksumSHA256 严格 base64+32-byte；exists 用 `head()` 验证 CAS 身份；全部外部 Provider 异常转换 `from None` 抑制 cause |
| `serve/app/config.py` | 修改 | `_validate_s3_endpoint` 重写为严格 origin：字符级禁 whitespace/control/`?`/`#`/`@`；访问并验证 parsed port（非数字/空/0/>65535 立即拒绝）；IPv6 bracketed 合法 |
| `serve/tests/trading/test_v2_artifact_s3.py` | 修改 | 91 → 114：新增 23 个（body read 注入 ReadTimeoutError/ResponseStreamingError、HTTPClientError reconcile、ChecksumSHA256 5 异常、ContentLength 类型 3×3、stored-sha 非字符串、cause 抑制 + traceback 无 secret、exists 矩阵）；旧 `exists` 用例改完整 CAS 元数据 |
| `serve/tests/trading/test_v2_config.py` | 修改 | endpoint 非法矩阵扩 9 例（畸形 port/空 port/port 0/whitespace/空 userinfo/空分隔符/control）；新增严格 origin 合法覆盖（DNS、DNS+port、IPv6 有/无 port） |
| `serve/docs/manifests/wp-00c2-r2-artifact-stream-endpoint.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` | 修改 | 00c2-r2 标 DONE（待审） |
| `serve/docs/tasks/README.md` | 修改 | 当前任务指向 00c2-r2；审查记录 WP-00c2-r1 → REMEDIATION_REQUIRED，追加 00c2-r2 |

范围外未动：`.env.example`、依赖、Artifact contracts/service/local/`__init__`、实施合同、数据库、Redis、main/lifespan、V1、原 `WP-00c2`/`WP-00c2-r1` task/manifest（已冻结）。**未遇 `BLOCKED_CONTRACT`**：公共 Protocol 与配置字段零改动。

---

## 2. 实现内容

### 2.1 StreamingBody 异常属于 Provider 边界（§5.1）

- 提取 `_read_bounded(body, limit, label)` helper，full GET 与 Range 共用；`body.read(limit)`
  抛 `_TRANSPORT_ERRORS` 或 `BotoCoreError` 时统一转为脱敏 `ArtifactStorageError`，不包含原始
  异常消息、endpoint、userinfo、credential 或签名；finally 仍在成功/失败路径 close body。
- `_TRANSPORT_ERRORS` 加入基础 `HTTPClientError`（botocore HTTP/connection 未决错误基类，
  覆盖未显式列出的子类；`SSLError` 不继承它，单独保留）。`ResponseStreamingError` 是
  `BotoCoreError` 子类，body read 抛它走 `except BotoCoreError` 收敛为 StorageError。
- 不捕获 `ArtifactIntegrityError`、`KeyboardInterrupt/SystemExit` 或应用编程错误。
- full GET 继续读 `stored_size+1`，Range 继续读 `requested+1`；不为修异常改回无界读取。
- **cause 抑制**：所有从外部 Provider 异常（ClientError/BotoCoreError/transport）转换的公共
  异常一律 `raise ... from None`（`ArtifactNotFound`、`ArtifactStorageError`、416 IntegrityError、
  reconcile 的 StorageError 全部含盖）；内部 Artifact 异常链（`except ArtifactError: raise`、
  新构造的 IntegrityError）不受影响。格式化完整 traceback 不出现注入的 endpoint/secret。

### 2.2 Provider 响应类型 fail-closed（§5.2）

- `pm-stored-sha256` 先确认 `isinstance(stored, str)` 且精确 64 位小写 hex 再 regex；
  非字符串（int/object）绝不进 regex，防 `TypeError` 泄出公共合同。
- 新增 `_parse_content_length`：HEAD/full GET/Range 三路径的 `ContentLength` 必须
  `type(value) is int`（拒绝 bool/float/numeric string）且非负；Range 另要求精确等于 requested。
  缺失/None/非法 → `ArtifactIntegrityError`。不再用 `int(...)` 截断 Provider 响应。
- `ChecksumSHA256` 若存在：先确认 `isinstance(checksum, (str, bytes))`；`base64.b64decode(
  checksum, validate=True)` 严格校验（非法 alphabet/padding → binascii.Error/ValueError →
  IntegrityError）；decode 后必须恰 32 bytes（SHA-256 digest）；再与本地 digest 比较。缺失时
  仍由 `pm-stored-sha256` 校验 stored bytes 的既有语义不变。

### 2.3 Endpoint 必须是可解析的严格 origin（§5.3）

- 输入为 str 且非空；任何 whitespace / ASCII control（<0x20 或 0x7F）→ 拒绝（含首尾/内部）。
- 原始串含 `?`、`#`、`@` → 直接拒绝（不依赖 `urlsplit` 后空字符串的 truthiness）。
- `urlsplit`；scheme 精确 `http|https`、hostname 非空、path 仅空或 `/`。
- 显式访问并验证 `p.port`：`urlsplit` 抛 ValueError（非数字/超范围）→ 拒绝；`port<=0`
  （含 0）或 `>65535` → 拒绝；空 port（`netloc` 以 `:` 结尾，如 `host:` / `[::1]:`）→ 拒绝。
- 不 normalize、不静默删字符；原 endpoint 原样交给 boto3。合法覆盖：DNS 无端口、DNS+port、
  `http://127.0.0.1:9000`（仍要求 `ALLOW_INSECURE_HTTP=true`）、IPv6 bracketed（有/无 port）。

### 2.4 exists() 必须验证 CAS 身份（§5.4）

- `exists` 复用 `head(ref)`：一次 HEAD + 五项 `pm-*` metadata + ContentLength（严格类型）校验
  通过才返回 `True`；确定 404（`ArtifactNotFound`）才返回 `False`；metadata 缺失/冲突或 size
  非法 → `ArtifactIntegrityError`；403/5xx/BotoCoreError → 脱敏 `ArtifactStorageError`，不得伪装
  成不存在。只发一次 HEAD，不增加 GET 或第二次请求。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests
# → exit 0

# 2) typed config（endpoint 矩阵扩展）
.venv/bin/pytest -q tests/trading/test_v2_config.py
# → 55 passed in 0.20s

# 3) artifact driver contract（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_driver_contract.py
# → 18 passed in 0.09s

# 4) artifact store service（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_store.py
# → 35 passed in 0.68s

# 5) artifact local driver（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_local.py
# → 24 passed in 0.30s

# 6) artifact S3 driver（新增 23，91 → 114）
.venv/bin/pytest -q tests/trading/test_v2_artifact_s3.py
# → 114 passed in 0.30s

# 7) 定向五项合计
.venv/bin/pytest -q tests/trading/test_v2_config.py \
  tests/trading/test_v2_artifact_driver_contract.py \
  tests/trading/test_v2_artifact_store.py \
  tests/trading/test_v2_artifact_local.py \
  tests/trading/test_v2_artifact_s3.py
# → 246 passed in 1.31s

# 8) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 312 passed in 1.75s

# 9) 全量回归
.venv/bin/pytest -q
# → 523 passed, 1 warning in 3.82s（1 warning = conftest event_loop 弃用告警，同各 WP）

# 10) git diff --check
git diff --check
# → 无输出，exit 0

# 11) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

---

## 4. 关键证据

### 4.1 StreamingBody 异常收敛（§5.1 / §6.1）

- `test_get_body_read_transport_error_converged`：full GET body.read 抛
  `ReadTimeoutError(endpoint_url="https://user:TOPSECRET@host")` → 脱敏 StorageError，
  `TOPSECRET`/`secret-host` 不泄出，body 已 close，`max_read == stored_size+1`。
- `test_get_range_body_read_response_streaming_error_converged`：Range body.read 抛
  `ResponseStreamingError` → StorageError，body close，`max_read == requested+1`（4，非 11）。
- `test_put_http_client_error_head_reconcile`：基础 `HTTPClientError` → PUT 只做一次 HEAD
  reconcile，PUT 总数 1，不无条件重发。
- `test_external_cause_suppressed_and_no_secret_in_traceback`：`__cause__ is None`、
  `__suppress_context__ is True`，`traceback.format_exc()` 无 `TOPSECRET`/`user`/`secret-host`。

### 4.2 响应类型 fail-closed（§5.2 / §6.2）

- `test_get_checksum_sha256_anomalies_rejected[5]`：int/object/非法 base64/无 padding/非
  32-byte digest 全部 `ArtifactIntegrityError`，不泄 TypeError。
- `test_head/get/get_range_content_length_type_rejected[3×3]`：float `3.9`、string `"3"`、bool
  `True` 全路径拒绝（`int(3.9)` 不再截断接受）。
- `test_head_stored_sha_non_string_rejected`：`pm-stored-sha256=123` → IntegrityError。

### 4.3 Endpoint 严格 origin（§5.3 / §6.3）

- `test_s3_endpoint_invalid_rejected` 扩 9 例：`https://host:abc`、`:99999`、`host:`（空 port）、
  `host:0`、`exa mple.com`（whitespace）、`@host`、`host?`、`host#`、`host\x01.com`（control）
  全部 `ValidationError`；原有 7 例（ftp/userinfo/query/fragment/path/http 无 allow/`//host`）
  继续通过。
- `test_s3_endpoint_strict_origin_valid`：`https://s3.example.com`、`https://s3.example.com:9000`、
  `https://[::1]:9000`、`https://[::1]` 全通过且原样保留。
- `test_s3_endpoint_http_requires_allow_insecure` 回归（http 需 opt-in）。

### 4.4 exists CAS 身份（§5.4 / §6.4）

- `test_exists_valid_404_and_head_once`：有效→True、404→False，HEAD 各一次。
- `test_exists_metadata_missing_raises_integrity` / `test_exists_metadata_conflict_raises_integrity`：
  缺失/冲突 → IntegrityError，HEAD 一次。
- `test_exists_403_raises_not_false` / `test_exists_no_credentials_raises_storage_error`：
  403/NoCredentials → StorageError，不得伪装 False。

### 4.5 回归

- config 55、contract 18、store 35、local 24、s3 114、trading 312、全量 523、双前缀 0、
  `git diff --check` 干净；R2 改动仅落在 `s3.py`、`config.py`、`test_v2_artifact_s3.py`、
  `test_v2_config.py` 四文件。

---

## 5. 未解决 blocker 与 Provider conformance 状态

无阻塞型 blocker。

非阻塞（如实记录，沿用 WP-00c2/R1）：
- **真实 Provider conformance 未执行**：真实 AWS/MinIO/R2 key 不是本任务输入；未来选定
  Provider 时须用同一合同补一次隔离 bucket conformance test；未做真实 Provider 测试不得宣称
  生产批准。默认测试经严格 fake client + Botocore Stubber，无网络、无真实凭据。
- `ARTIFACT_DRIVER` 默认仍为 `local`；S3 接入 main/lifespan 属 WP-00d。
- multipart/streaming upload 未实现（当前对象上限 64 MiB 远低于单次 PutObject 上限；未来另立任务）。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/services/artifact_store/drivers/s3.py serve/app/config.py \
  serve/tests/trading/test_v2_artifact_s3.py serve/tests/trading/test_v2_config.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/docs/manifests/wp-00c2-r2-artifact-stream-endpoint.md
```

- 回到 WP-00c2-r1 交付状态；原 00c2/00c2-r1 completion manifest 与已冻结 R1/R2 不受影响；无
  数据库迁移、secret 或生产对象副作用；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00c2-r2-artifact-stream-endpoint.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
081126a9ddc31322c2d45bfd75bd1622461c436bce7eeb7ee5a58fb8b0774d7a
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00c2-r2-artifact-stream-endpoint.md | sha256sum
```
