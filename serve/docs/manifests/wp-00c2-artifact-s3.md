# COMPLETION MANIFEST — WP-00c2 · S3-compatible Artifact Driver

- Work package: `WP-00` 子任务 `WP-00c2`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00c1-r2`（ACCEPTED，store 35 / local 24 / contract 18 / trading 159 / full 370）
- 规范依据: `serve/docs/tasks/wp-00c2-artifact-s3.md`；`serve/docs/v2-implementation-contract.md` §3/§12–§15；`serve/docs/performance-cache-database-design.md` §6.2/§11/§14–§15
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/config.py` | 修改 | 新增 11 个 `ARTIFACT_S3_*` typed 字段 + `_validate_s3_config` 交叉校验（driver=s3 需 bucket/region、addressing style、timeout>0、pool/attempts≥1、prefix 格式、endpoint origin + http 需 ALLOW_INSECURE_HTTP） |
| `serve/.env.example` | 修改 | 新增 S3 Driver 配置段（11 键），说明凭据走标准 provider chain，无云厂商 access key / key-like 示例 |
| `serve/requirements.txt` | 修改 | 新增 `boto3>=1.43,<2`（有上界：锁定主版本；不单独锁 botocore） |
| `serve/app/services/artifact_store/drivers/s3.py` | **新增** | `S3ArtifactDriver`（实现 ArtifactDriver Protocol，driver_name=s3）+ `build_s3_artifact_driver` |
| `serve/tests/trading/test_v2_config.py` | 修改 | 新增 S3 配置默认/覆盖/交叉校验/.env.example 契约测试 |
| `serve/tests/trading/test_v2_artifact_s3.py` | **新增** | 54 个 Driver + Service 集成测试（严格 fake client + Botocore Stubber + 内存"类 S3"） |
| `serve/docs/v2-implementation-contract.md` | 修改 | `drivers/s3.py` 行更新为实际实现的合同（条件写 + checksum + 元数据 + 有界读取 + reconcile） |
| `serve/docs/manifests/wp-00c2-artifact-s3.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` | 修改 | 00c2 标 DONE（待审） |
| `serve/docs/tasks/README.md` | 修改 | 审查记录追加 00c2（当前任务保持） |

范围外未动：Artifact `contracts.py/service.py/local.py/__init__.py`、Base 旧 `services/storage/`、数据库/Alembic、Redis、main/lifespan、V1、旧 task/manifest（冻结）。**未遇 `BLOCKED_CONTRACT`**：现有 ArtifactDriver Protocol 完全覆盖 S3 Driver 所需（put_if_absent/get/get_range/head/exists/health/aclose），无需改动公共合同。

---

## 2. 实现内容

### 2.1 Typed 配置与依赖（§4）

- 11 个 `ARTIFACT_S3_*` 字段；`ARTIFACT_DRIVER=s3` 时 bucket 与 region 必填，local 模式不要求。
- addressing style ∈ {auto,virtual,path}；connect/read timeout > 0；max_pool_connections ≥ 1；
  max_attempts ≥ 1。
- prefix 可空；非空时禁止首尾 `/`、空段、`.`、`..`、反斜杠、NUL/CR/LF（不做静默 strip）。
- endpoint 为空或绝对 `http(s)://host[:port]` origin；禁止 userinfo/query/fragment/非根路径；
  `http://` 必须显式 `ARTIFACT_S3_ALLOW_INSECURE_HTTP=true`。
- `.env.example` 只说明凭据由标准 provider chain 注入，不出现云厂商 access key / 明文 secret。
- `requirements.txt` 增加 `boto3>=1.43,<2`；不单独锁 botocore，不加 aioboto/MinIO SDK。

### 2.2 构造与 key（§5.1）

- `S3ArtifactDriver` 构造注入 client/bucket/prefix/expected owner/显式 conditional retry limit；
  `build_s3_artifact_driver(settings, client=None)`；注入 client 时禁止再建真实 client。
- builder 用 SigV4（boto3 s3 默认签名，未覆盖 signature_version/凭据）、`Config(connect_timeout,
  read_timeout, max_pool_connections, retries={mode:"standard",total_max_attempts:N},
  s3={addressing_style})`；endpoint 原样传入。
- 对象 key = `<prefix>/<ref.locator>`；空 prefix 时即 locator；不 lstrip/rstrip 修正输入。

### 2.3 条件写与未知结果收敛（§5.2）

- `put_if_absent` 先校验 `len(data)==candidate.stored_size`，计算 stored bytes SHA-256；
  `put_object` 固定携带 `Bucket/Key/Body/ContentLength/ContentType/IfNoneMatch="*"/
  ChecksumAlgorithm="SHA256"/ChecksumSHA256(⩴base64(SHA256(stored)))/五项 pm-* Metadata/
  ExpectedBucketOwner(仅配置非空)`。
- 2xx → HEAD 同一 key 验证实际 size/元数据 → `created=True`。
- 412/`PreconditionFailed` → HEAD 返回 `created=False` 与实际 head；Service 负责读取解压验证，不覆盖。
- 409/`ConditionalRequestConflict` → 重放**同一条件 PUT**（`IfNoneMatch="*"` 恒在），总次数
  ≤ 显式 limit，耗尽 `ArtifactStorageError`。
- timeout/connection closed 等发送结果未知 → **HEAD reconcile**：匹配对象存在则 `created=False`；
  确定不存在（HEAD 404）或无法定案 → 受控错误由上层同 CAS 重试；不生成新 key、不无条件重发。
- 400/501 等不支持 conditional/checksum 的 Provider fail-closed，不降级覆盖写。

### 2.4 元数据、读取与 Range（§5.3）

- `head()` 以 S3 `ContentLength` 为真实 stored size；严格解析五项 `pm-*` 元数据：`pm-sha256`/
  `pm-original-size`/`pm-compression`/`pm-storage-version`(==cas/v1)/`pm-stored-sha256`(64 小写 hex)，
  与 ref 冲突或缺失 → `ArtifactIntegrityError`；`ContentType` 仅作注解不参与身份。
- `get()` 单个 `get_object(ChecksumMode="ENABLED")`；校验返回 size/元数据；`StreamingBody` 最多读
  `ref.stored_size+1`（有界），长度与 `pm-stored-sha256` 必须匹配；响应 `ChecksumSHA256` 存在则必须
  匹配；body 在成功/异常路径都 close。
- `get_range()` stored 半开区间：严格 `0≤start≤end≤ref.stored_size`；空区间零请求返回 `b""`；
  否则一次 `Range="bytes=start-(end-1)"` GET，要求 HTTP 206、精确 `ContentRange`、精确长度，close body。
- `exists()` 只把确定 404/`NoSuchKey|NotFound` 转 False；403/5xx/timeout 不得伪装成不存在。
- 404→`ArtifactNotFound`；416/size/range/metadata/checksum 不一致→`ArtifactIntegrityError`；
  auth/网络/限流/5xx/未知→脱敏 `ArtifactStorageError`（只带 HTTP status/code）。
- `health()` 只 `head_bucket` 返回 typed `ArtifactHealth`，不写 probe、不泄露 endpoint
  userinfo/credential/signature；`aclose()` 幂等；不暴露 delete/list/presign/ACL API。

### 2.5 错误收敛与身份

- 传输级异常集 `_TRANSPORT_ERRORS = {ConnectTimeoutError, ReadTimeoutError,
  EndpointConnectionError, ConnectionClosedError}` 触发 reconcile。
- MIME 是引用注解：同内容/codec 以不同 MIME 再写仍命中已有对象（CAS key 相同 + IfNoneMatch），
  不因首写 ContentType 不同破坏 Local/S3 一致性。
- 跨 zstd level 不同 stored size：Driver 以实际 HEAD 的 ContentLength 收敛，Service 读回验证
  原文 SHA/original size 后返回含**实际** stored size 的 canonical ref。

---

## 3. 命令与真实结果

```bash
# 1) 依赖
.venv/bin/pip install -q -r requirements-dev.txt        # → exit 0（boto3 1.43.67 装入）

# 2) compileall
python3 -m compileall -q app tests                      # → exit 0

# 3) typed config（含 S3 配置矩阵）
.venv/bin/pytest -q tests/trading/test_v2_config.py
# → 45 passed in 0.20s

# 4) artifact driver contract（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_driver_contract.py
# → 18 passed in 0.07s

# 5) artifact store service（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_store.py
# → 35 passed in 0.68s

# 6) artifact local driver（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_local.py
# → 24 passed in 0.30s

# 7) artifact S3 driver（新增）
.venv/bin/pytest -q tests/trading/test_v2_artifact_s3.py
# → 54 passed in 0.20s

# 8) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 242 passed in 1.66s

# 9) 全量回归
.venv/bin/pytest -q
# → 453 passed, 1 warning in 3.97s（1 warning = conftest event_loop 弃用告警，同 R1/R2）

# 10) git diff --check
git diff --check                                        # → 无输出，exit 0

# 11) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l            # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l        # → 0
```

---

## 4. 关键证据

### 4.1 条件写 exact wire（§6.1–6.3）

- `test_put_exact_wire_params` / `test_stubber_exact_wire_put_and_head`（Botocore Stubber 精确断言）：
  `IfNoneMatch="*"`、`ContentLength`、`ContentType`、`ChecksumAlgorithm="SHA256"`、
  `ChecksumSHA256=base64(SHA256(stored))`、五项 `pm-*` Metadata、`Bucket/Key=<prefix>/<locator>`
  全部逐字段吻合；2xx 后必须 HEAD 同一 key。
- `test_put_412_dedupe_head_only`：412 只 HEAD 一次，put 调用数恒为 1（不存在无条件 PUT）。
- `test_put_409_bounded_retry_same_conditional_put`：2×409 后成功，3 次 put 每次 `IfNoneMatch="*"`、
  同 key；`test_put_409_exhaustion_raises_storage_error`：limit=2 时第 3 次 409 抛 StorageError，
  put 调用数=2（有界）。
- `test_put_unsupported_provider_fails_closed[400/501]`：拒绝且不降级覆盖写。
- `test_put_transport_timeout_*`：timeout 后 HEAD reconcile（匹配→created=False 去重 / 404→
  StorageError / 503→StorageError），put 调用数=1（不无条件重发）。
- `test_put_data_size_mismatch_rejected_before_wire`：`len(data)!=candidate.stored_size` 在 wire 前拒绝，S3 调用 0 次。

### 4.2 元数据与身份（§6.5）

- `test_head_metadata_anomalies_rejected[8 cases]`：缺失 pm-sha256/pm-stored-sha256、sha256 冲突、
  original-size 冲突/非 int、compression 冲突、storage-version 冲突、stored-sha 非 64 hex 全部
  `ArtifactIntegrityError`。
- `test_head_etag_is_not_content_hash`：任意 ETag 不影响身份判定（只信 ContentLength + pm-*）。
- `test_put_412_head_metadata_conflict_fails_closed`：去重 HEAD 元数据与候选冲突 → IntegrityError。

### 4.3 有界读取 + body 关闭 + checksum（§6.6）

- `test_get_bounded_read_only_stored_plus_one`：`_ReadSpy.max_read == stored_size+1`，禁止无界 `.read()`。
- `test_get_body_closed_on_error_path` / `test_get_stored_checksum_tamper_rejected`：
  异常路径 body 仍 close；`pm-stored-sha256` 与实际不符 → IntegrityError。
- `test_get_response_checksum_mismatch_rejected`：响应 `ChecksumSHA256` 不匹配 → IntegrityError。
- `test_get_size_conflict_rejected` / `test_get_403_raises_storage_error` / `test_get_not_found`。

### 4.4 Range（§6.7）

- `test_get_range_maps_to_closed_interval`：`[2,5)` → `Range="bytes=2-4"`、206、`ContentRange="bytes 2-4/10"`。
- `test_get_range_empty_zero_requests`：空区间返回 `b""`，S3 调用 0 次。
- `test_get_range_invalid_bounds`：负数/反向/末端越界/bool → ValueError。
- `test_get_range_requires_206` / `test_get_range_content_range_mismatch` /
  `test_get_range_length_mismatch` / `test_get_range_416_integrity_error`：206/ContentRange/长度异常 → IntegrityError。

### 4.5 exists / health / aclose / 无管理 API（§6.9）

- `test_exists_true_and_false`（404→False）；`test_exists_403_raises_not_false`（403 不得伪装不存在）。
- `test_health_head_bucket_only_and_redacted`：只 head_bucket、detail 无 secret/password/AKIA；
  `test_health_failure_redacted`：只带 HTTP status。
- `test_aclose_idempotent`：close 只调用 1 次。
- `test_no_delete_list_presign_api`：无 delete/delete_objects/list/presign/ACL 公共 API。

### 4.6 Service 集成（§6.8）

- `test_service_roundtrip_and_dedup_via_s3`：put/get 往返、去重单对象。
- `test_service_different_mime_same_content_dedupes_via_s3`：同内容不同 MIME 命中已有对象，单对象。
- `test_service_cross_level_dedup_via_s3`：level 1 → level 22 第二次成功去重，stored_size 由实际
  HEAD 收敛为同一值、单对象、两次 ref 均可读。
- `test_service_zstd_roundtrip_and_range_via_s3`：zstd 往返 + Service 只向 raw 暴露原文 range。

### 4.7 配置矩阵（§6.10）

- `test_s3_defaults` / `test_s3_env_override` / `test_s3_driver_requires_bucket_and_region` /
  `test_s3_addressing_style_validation` / `test_s3_numeric_bounds[5]` /
  `test_s3_prefix_invalid_rejected[8]` / `test_s3_endpoint_invalid_rejected[8]` /
  `test_s3_endpoint_https_valid` / `test_s3_endpoint_http_requires_allow_insecure` /
  `test_env_example_contains_all_s3_keys` / `test_env_example_s3_no_credentials`。
- `.env.example` 无云厂商 access key / key-like 示例（既有 `test_env_example_uses_secret_ref_only`
  及新增无凭据断言）。

### 4.8 回归

- Local/R1 全部回归：config 45、contract 18、store 35、local 24、s3 54、trading 242、全量 453、
  双前缀 0、`git diff --check` 干净。

---

## 5. 未解决 blocker 与 Provider conformance 状态

无阻塞型 blocker。

非阻塞（如实记录）：
- **真实 Provider conformance 未执行**：真实 AWS/MinIO/R2 key 不是本任务输入；按任务 §6/§8，
  未来选定 Provider 时必须用同一合同补一次隔离 bucket conformance test；未做真实 Provider
  测试不得宣称该 Provider 已获生产批准。默认测试经 Botocore Stubber + 严格 fake client，
  无网络、无真实凭据。
- `ARTIFACT_DRIVER` 默认仍为 `local`；S3 接入 main/lifespan 属 WP-00d。
- multipart/streaming 未实现（当前对象上限 64 MiB 远低于单次 PutObject 上限，Service 已持完整
  bytes；未来引入 streaming 或提高上限时另立任务）。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/config.py serve/.env.example serve/requirements.txt \
  serve/docs/v2-implementation-contract.md serve/tests/trading/test_v2_config.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/app/services/artifact_store/drivers/s3.py \
  serve/tests/trading/test_v2_artifact_s3.py \
  serve/docs/manifests/wp-00c2-artifact-s3.md
```

- 回到 WP-00c1-r2 交付状态；Local Driver 与已冻结 R2 不受影响；无数据库迁移、secret 或生产
  对象删除；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00c2-artifact-s3.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
be34bf58dd9778a5670561ece6990bc508edb0386b0265cadcb1cc487a801f06
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00c2-artifact-s3.md | sha256sum
```
