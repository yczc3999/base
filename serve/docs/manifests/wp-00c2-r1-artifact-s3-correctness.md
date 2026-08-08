# COMPLETION MANIFEST — WP-00c2-r1 · S3 Driver 正确性整改

- Work package: `WP-00` 子任务 `WP-00c2-r1`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00c2`（REMEDIATION_REQUIRED，四组 P1 + prefix 缺口）；本整改接受前 `WP-00d` 继续阻塞
- 规范依据: `serve/docs/tasks/wp-00c2-r1-artifact-s3-correctness.md`；`serve/docs/v2-implementation-contract.md` §3/§12–§15；`serve/docs/performance-cache-database-design.md` §6.2/§11/§14–§15
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/services/artifact_store/drivers/s3.py` | 修改 | 显式 SigV4；conditional PUT 上限取 `ARTIFACT_S3_MAX_ATTEMPTS`（删硬编码 3）；`BotoCoreError` 两层收敛（transport→reconcile，其余→脱敏 StorageError）；created 分支严格验证 ContentLength==len(data) 且 pm-stored-sha256==digest；get_range 五项元数据 + ContentLength==requested + read(requested+1)；`_validate_prefix` 与 Settings 等价 |
| `serve/tests/trading/test_v2_artifact_s3.py` | 修改 | 54 → 91：新增 37 个（SigV4/attempts 双控、五操作 NoCredentials 收敛、SSL reconcile、脱敏、created 双 fail-closed、412 跨 level 去重、Range 身份/长度/有界读取、直接构造 prefix 矩阵）；既有 created 分支用例补真实 stored-sha 元数据、Range 用例补五项元数据 |
| `serve/docs/manifests/wp-00c2-r1-artifact-s3-correctness.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` | 修改 | 00c2-r1 标 DONE（待审） |
| `serve/docs/tasks/README.md` | 修改 | 当前任务指向 00c2-r1；审查记录 WP-00c2 → REMEDIATION_REQUIRED，追加 00c2-r1 |

范围外未动：config/.env/依赖、Artifact contracts/service/local/`__init__`、实施合同、数据库、Redis、main/lifespan、V1、原 `WP-00c2` task/manifest（已冻结）。**未遇 `BLOCKED_CONTRACT`**：整改只在 Driver 内部，公共 Protocol 与配置字段零改动。

---

## 2. 实现内容

### 2.1 Builder 兑现 SigV4 与 attempts（§5.1）

- `Config` 显式 `signature_version="s3v4"`，不依赖区域/Provider 隐式默认。
- `build_s3_artifact_driver` 在**注入 client 与自建 client 共享的同一 return** 传
  `conditional_retry_limit=settings.ARTIFACT_S3_MAX_ATTEMPTS`；Driver 条件 PUT 上限与 botocore
  `total_max_attempts` 同一来源。删除 `_DEFAULT_CONDITIONAL_RETRY_LIMIT` 模块常量；
  `S3ArtifactDriver.__init__` 的 `conditional_retry_limit` 改为必填（None/非 int/<1 均拒绝），
  杜绝"第二套 3"。
- `MAX_ATTEMPTS=1`：首个 409 立即 `ArtifactStorageError`，PUT 调用总数 1；`=2`：一次 409 后
  成功，PUT 调用总数 2（≤ N）。

### 2.2 全 Botocore 异常收敛为公共合同（§5.2）

- 两层语义：
  1. `_TRANSPORT_ERRORS = (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError,
     ConnectionClosedError, SSLError, ProxyConnectionError)` —— 发送结果未知，PUT 走 HEAD
     reconcile（绝不无条件重发）；读类操作映射脱敏 StorageError；
  2. 其余 `BotoCoreError`（至少 `NoCredentialsError`/`CredentialRetrievalError`、
     `ParamValidationError`、`NoRegionError` 及未分类 BotoCoreError）不做假定成功，直接映射
     脱敏 `ArtifactStorageError`。
- `head/get/get_range/exists` 均捕获 `BotoCoreError` → `_storage_error`；`health()` 对任意
  `BotoCoreError` 返回 `ArtifactHealth(ok=False, driver="s3", detail={"error": <低基数分类>})`
  不抛异常，分类经 `_error_category`（credentials / invalid-request / no-region / transport /
  boto-core）。
- `ClientError` 业务分类（404/409/412/416、400/501 fail-closed）原样保留；`ClientError` 非
  `BotoCoreError` 子类，分层捕获互不吞并；异常文本只带 HTTP status/code，不含 endpoint/
  凭据/签名/原始消息。

### 2.3 2xx 后严格验证本次创建（§5.3）

- created 分支以 `_head_raw` 取原始响应 → `_head_from_response`（五项元数据结构校验）
  后，要求 `head.stored_size == len(data)` 且 `pm-stored-sha256 == sha256(data).hexdigest()`；
  任一不等抛 `ArtifactIntegrityError`，不返回 `created=True`（杜绝"现在成功、以后读失败"）。
- 412 dedupe 与 transport reconcile 命中同一原文另一 zstd level 时仍允许实际 stored
  size/digest 与本次候选不同：返回 `created=False` + 实际 head，由 Service 读回验证原文，
  不套用 created 分支字节级相等断言。ETag 仍只作不透明字段。

### 2.4 Range 身份、响应长度与小范围上限（§5.4）

- 非空 range response 在读取 body 前：校验 HTTP 206、精确 `ContentRange`、
  `ContentLength` 存在且为非负整数并**精确等于 `requested = end-start`**、五项 `pm-*`
  元数据完整且与 ref 匹配；任一不符 `ArtifactIntegrityError`。
- body 只 `read(requested + 1)`（多 1 byte 用于发现 Provider 忽略/扩大 Range），返回长度必须
  恰为 requested；所有成功/失败路径 continue close。空区间仍零请求；Range 不计算/声称全对象
  checksum。

### 2.5 直接构造不能绕过 prefix 合同（§5.5）

- 构造器经 `_validate_prefix` 执行与 Settings 等价校验：可空；非空禁首尾 `/`、空段、`.`、
  `..`、反斜杠、NUL/CR/LF；不 strip、不 normalize。S3 key 仍严格 `<prefix>/<locator>`。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests
# → exit 0

# 2) typed config（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_config.py
# → 45 passed in 0.20s

# 3) artifact driver contract（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_driver_contract.py
# → 18 passed in 0.09s

# 4) artifact store service（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_store.py
# → 35 passed in 0.68s

# 5) artifact local driver（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_local.py
# → 24 passed in 0.30s

# 6) artifact S3 driver（新增 37，54 → 91）
.venv/bin/pytest -q tests/trading/test_v2_artifact_s3.py
# → 91 passed in 0.27s

# 7) 定向五项合计
.venv/bin/pytest -q tests/trading/test_v2_config.py \
  tests/trading/test_v2_artifact_driver_contract.py \
  tests/trading/test_v2_artifact_store.py \
  tests/trading/test_v2_artifact_local.py \
  tests/trading/test_v2_artifact_s3.py
# → 213 passed in 1.54s

# 8) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 279 passed in 1.75s

# 9) 全量回归
.venv/bin/pytest -q
# → 490 passed, 1 warning in 3.79s（1 warning = conftest event_loop 弃用告警，同 R1/R2/00c2）

# 10) git diff --check
git diff --check
# → 无输出，exit 0

# 11) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

---

## 4. 关键证据

### 4.1 SigV4 与 attempts 双控（§5.1 / §6.1）

- `test_builder_creates_client_with_config`：`cfg.signature_version == "s3v4"`、
  `cfg.retries == {"mode":"standard","total_max_attempts":3}`、`d._retry_limit == 3`。
- `test_builder_retry_limit_from_settings_injected_client`（注入，attempts 1/2/5）与
  `test_builder_retry_limit_from_settings_built_client`（自建，attempts=4）均断言
  `d._retry_limit == attempts`。
- `test_put_attempts_1_first_409_fails_immediately`：首个 409 抛 StorageError，PUT 总数 1。
- `test_put_attempts_2_retries_once_then_success`：409→成功，PUT 总数 2（≤ N）。

### 4.2 异常边界收敛（§5.2 / §6.2）

- `test_no_credentials_converged_for_all_ops[put/head/get/range/exists]`：五操作
  `NoCredentialsError` 全部变 `ArtifactStorageError`，且 `"Unable to locate"` 不泄出。
- `test_health_no_credentials_returns_false_redacted`：`h.detail == {"error":"credentials"}`，
  原始消息不泄出；`test_health_failure_redacted` 仅 `HTTP 503`。
- `test_put_ssl_transport_head_reconcile`：`SSLError` → HEAD reconcile → `created=False`，
  PUT 总数 1（不无条件重发）。
- `test_error_output_does_not_leak_endpoint_or_credentials`：`ConnectTimeoutError(endpoint_url=
  "https://secret-host:9000")` 经 `head` 后异常文本无 `secret-host`/`ConnectTimeout`。

### 4.3 created 严格验证（§5.3 / §6.3）

- `test_put_created_head_size_mismatch_fails_closed`：上传 3 bytes、HEAD 谎报 11 →
  `ArtifactIntegrityError`，无 `created=True`。
- `test_put_created_head_stored_sha_mismatch_fails_closed`：HEAD `pm-stored-sha256` 为另一合法
  64-hex → `ArtifactIntegrityError`。
- `test_put_created_head_returns_actual_stored` / `test_put_exact_wire_params` /
  `test_stubber_exact_wire_put_and_head`：正常 created 回归（HEAD 携带真实 stored digest）。
- `test_put_412_dedupe_allows_different_stored_size_and_digest`：候选 stored=5、现存 stored=3
  （跨 zstd level）→ `created=False`、head.stored_size=3；`test_service_cross_level_dedup_via_s3`
  全链回归（level 1→22 单对象、双 ref 可读）。

### 4.4 Range 身份与有界读取（§5.4 / §6.4）

- `test_get_range_missing_metadata_rejected` / `test_get_range_metadata_conflict_rejected`：
  五项元数据缺失/冲突拒绝。
- `test_get_range_content_length_missing_rejected` /
  `test_get_range_content_length_non_numeric_rejected`：缺失/非数值拒绝。
- `test_get_range_length_mismatch`：`ContentLength` 4 ≠ requested 3 拒绝（原用例补元数据后
  命中新 ContentLength 校验）。
- `test_get_range_read_spy_reads_requested_plus_one`：`[2,5)` read spy 精确 `read(4)`，
  非 stored_size+1（11）。
- `test_get_range_extra_body_detected_and_closed`：body 超 requested 被发现，异常路径仍 close。
- 既有 `test_get_range_maps_to_closed_interval`（206/ContentRange/闭区间）与空区间零请求回归。

### 4.5 prefix 直接构造（§5.5 / §6.5）

- `test_direct_construction_invalid_prefix_rejected[15]`：`/`、`/a`、`a/`、`a//b`、`a/./b`、
  `a/../b`、`a/b//c`、`a\b`、NUL/CR/LF、`./a`、`../a`、`.`、`..` 全部 `ValueError`。
- `test_direct_construction_valid_prefixes_accepted`：`""`、`a/b`、`a/b/c` 合法、`_prefix`
  不 strip/normalize；`_key` 仍严格 `<prefix>/<locator>`。

### 4.6 回归

- config 45、contract 18、store 35、local 24、s3 91、trading 279、全量 490、双前缀 0、
  `git diff --check` 干净；R1 改动仅落在 `s3.py` + `test_v2_artifact_s3.py` 两文件。

---

## 5. 未解决 blocker 与 Provider conformance 状态

无阻塞型 blocker。

非阻塞（如实记录，沿用 WP-00c2）：
- **真实 Provider conformance 未执行**：真实 AWS/MinIO/R2 key 不是本任务输入；未来选定
  Provider 时须用同一合同补一次隔离 bucket conformance test；未做真实 Provider 测试不得宣称
  生产批准。默认测试经严格 fake client + Botocore Stubber，无网络、无真实凭据。
- `ARTIFACT_DRIVER` 默认仍为 `local`；S3 接入 main/lifespan 属 WP-00d。
- multipart/streaming 未实现（当前对象上限 64 MiB 远低于单次 PutObject 上限；未来另立任务）。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/services/artifact_store/drivers/s3.py \
  serve/tests/trading/test_v2_artifact_s3.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/docs/manifests/wp-00c2-r1-artifact-s3-correctness.md
```

- 回到 WP-00c2 交付状态；原 WP-00c2 completion manifest 与已冻结 R1/R2 不受影响；无数据库
  迁移、secret 或生产对象副作用；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00c2-r1-artifact-s3-correctness.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
10f4008387fac5696f0a1cce438189dae5392c4e94c5fff3d25a7732bca705c4
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00c2-r1-artifact-s3-correctness.md | sha256sum
```
