# WP-00c2 — S3-compatible Artifact Driver

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00c2-artifact-s3.md`。
> 最后更新：2026-08-08 11:46 EDT。依赖：`WP-00c1-r2` 已审查接受；本任务接受前
> `WP-00d` 继续阻塞。

## 1. 目标、价值与真实状态

Local Artifact Store 的公共合同、大小边界、完整帧校验和 durable no-replace 已冻结，独立复验为
store 35、local 24、contract 18、trading 159、全量 370 tests，manifest SHA 一致。

本任务只增加生产共享对象存储 Driver：保持同一个 content-addressed `ArtifactStore` 接口，在
AWS S3 或通过合规测试的 S3-compatible 服务上做到条件创建、去重、完整性校验、有界读取和精确
Range。它为后续 AI 调用证据、行情原文和回放提供共享持久层，不接入业务数据库或运行时。

## 2. 必读资料与确认决策

1. `/code/pollymarket/v2/AGENTS.md`
2. `serve/docs/v2-implementation-contract.md` §3、§12–§15
3. `serve/docs/performance-cache-database-design.md` §6.2、§11、§14–§15
4. `serve/app/services/artifact_store/contracts.py`
5. `serve/app/services/artifact_store/service.py`
6. `serve/docs/manifests/wp-00c1-r2-artifact-final-boundaries.md`（冻结，只读）
7. AWS 官方 [conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)、
   [PutObject](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html)、
   [GetObject](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/get_object.html)、
   [HeadObject](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/head_object.html) 与
   [Botocore Config](https://docs.aws.amazon.com/botocore/latest/reference/config.html)。

确认决策：

- 使用同步 `boto3`，因为现有 Driver Protocol 同步；client 必须可注入，禁止 import 时建全局连接。
- 当前对象硬上限为 64 MiB，远低于单次 `PutObject` 上限，且 Service 已持有完整 `bytes`；首版只做
  **single conditional PUT**。multipart 在未来引入 streaming 或提高对象上限时另立任务，不提前增加
  失败状态机。
- no-replace 唯一合法实现是 `IfNoneMatch="*"`。禁止先 HEAD 再无条件 PUT，禁止因兼容性问题降级覆盖。
- `ETag` 是不透明 Provider 标识，不是内容 MD5；内容身份仍是未压缩原文 SHA-256。S3 `VersionId`
  不得冒充合同中的 `storage_version=cas/v1`。
- MIME 是引用注解，不属于 locator 身份；同内容/codec 以不同 MIME 再写仍须命中已有对象，不得因
  首写 `ContentType` 不同破坏 Local/S3 一致性。
- 凭据只走 boto3 标准 credential provider chain（IAM role、workload identity 或服务端 secret
  injection）。不新增 access key/secret 字段，不把 credential、签名头或 presigned URL 写入日志。

## 3. 允许修改

```text
serve/app/config.py
serve/.env.example
serve/requirements.txt
serve/app/services/artifact_store/drivers/s3.py
serve/tests/trading/test_v2_config.py
serve/tests/trading/test_v2_artifact_s3.py
serve/docs/v2-implementation-contract.md
serve/docs/manifests/wp-00c2-artifact-s3.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 Artifact `contracts.py/service.py/local.py`、Base 旧 `services/storage/`、数据库/Alembic、
Redis、main/lifespan、V1、旧 task/manifest 和清单外文件。若当前 Protocol 无法实现某项要求，记录
`BLOCKED_CONTRACT`，不得偷偷改公共合同。

## 4. Typed 配置与依赖

新增以下 Settings 与 `.env.example` 键：

```text
ARTIFACT_S3_BUCKET=
ARTIFACT_S3_PREFIX=v2-artifacts
ARTIFACT_S3_REGION=us-east-1
ARTIFACT_S3_ENDPOINT_URL=
ARTIFACT_S3_ADDRESSING_STYLE=auto
ARTIFACT_S3_CONNECT_TIMEOUT_S=2
ARTIFACT_S3_READ_TIMEOUT_S=10
ARTIFACT_S3_MAX_POOL_CONNECTIONS=20
ARTIFACT_S3_MAX_ATTEMPTS=3
ARTIFACT_S3_EXPECTED_BUCKET_OWNER=
ARTIFACT_S3_ALLOW_INSECURE_HTTP=false
```

强制校验：

1. `ARTIFACT_DRIVER=s3` 时 bucket 与 region 为非空；local 模式不要求 S3 配置。
2. addressing style 仅 `auto|virtual|path`；timeout > 0，pool/attempts >= 1。
3. prefix 可空；非空时禁止首尾 `/`、空段、`.`、`..`、反斜杠、NUL/CR/LF，不做静默 strip。
4. endpoint 只能为空或绝对 `http(s)://host[:port]` origin；禁止 userinfo、query、fragment 和非根路径。
   `http://` 还必须显式设置 `ALLOW_INSECURE_HTTP=true`。
5. `.env.example` 只说明凭据由标准 provider chain 注入，不出现 `AWS_ACCESS_KEY_ID`、明文 secret
   或 key-like 示例。

`requirements.txt` 增加 `boto3>=1.43,<2`；不单独锁 botocore，不增加 aioboto、MinIO SDK 或未使用库。

## 5. S3 Driver 精确合同

### 5.1 构造与 key

- `S3ArtifactDriver` 实现现有 `ArtifactDriver` Protocol，`driver_name="s3"`。
- 构造器接收注入 client、bucket、prefix、expected owner、explicit conditional retry limit；同文件提供
  `build_s3_artifact_driver(settings, client=None)`。注入 client 时禁止再创建真实 client。
- builder 使用 SigV4、`Config(connect_timeout, read_timeout, max_pool_connections,
  retries={mode:"standard",total_max_attempts:N}, s3={addressing_style:...})`；endpoint 原样传入。
- 对象 key 精确为 `<prefix>/<ref.locator>`；空 prefix 时就是 locator。不得 `lstrip/rstrip` 修正输入。

### 5.2 条件写与未知结果收敛

每次 `put_if_absent(candidate, data)` 必须先校验 `len(data)==candidate.stored_size`，并计算 stored bytes
SHA-256。`put_object` 固定携带：

```text
Bucket, Key, Body, ContentLength, ContentType
IfNoneMatch="*"
ChecksumAlgorithm="SHA256"
ChecksumSHA256=<base64(SHA256(stored bytes))>
Metadata:
  pm-sha256
  pm-original-size
  pm-compression
  pm-storage-version
  pm-stored-sha256
ExpectedBucketOwner（仅配置非空时）
```

- 2xx 后必须 HEAD 同一 key，验证实际 size/元数据，再返回 `created=True`。
- 412/`PreconditionFailed` 表示已有对象：HEAD 并返回 `created=False` 与实际 `ArtifactHead`；Service
  负责读取、解压和验证是否确为相同原文，Driver 不覆盖。
- 409/`ConditionalRequestConflict` 只能重放**同一条件 PUT**，总次数不超过配置；耗尽即
  `ArtifactStorageError`。
- timeout/connection closed 等发送结果未知时先 HEAD reconcile：若匹配对象存在则返回
  `created=False`；确定不存在或无法定案则抛受控错误，由上层以相同 CAS 请求重试。不得生成新 key，
  不得无条件重发。
- 400/501 等不支持 conditional/checksum 的 Provider fail-closed；实际部署前必须通过同一兼容测试。

### 5.3 元数据、读取和 Range

- `head()` 以 S3 `ContentLength` 为真实 stored size，严格解析五个 `pm-*` metadata；SHA/原始大小/
  compression/layout version 与 ref 不一致、stored SHA 非 64 位小写 hex或元数据缺失，均为
  `ArtifactIntegrityError`。`ContentType` 仅作存储注解，不参与 CAS 相等判断。
- `get()` 使用单个 `get_object(ChecksumMode="ENABLED")`，先检查返回 size/元数据，再对
  `StreamingBody` 最多读取 `ref.stored_size+1`，所有路径关闭 body；长度及 `pm-stored-sha256`
  必须匹配。响应提供 `ChecksumSHA256` 时也必须匹配；不得无界 `.read()`。
- `get_range()` 的 Driver 语义是 stored bytes 半开区间：严格校验
  `0 <= start <= end <= ref.stored_size`。空区间直接返回 `b""`、不发请求；其他范围只发一次
  `Range="bytes=start-(end-1)"` GET，要求 HTTP 206、精确 `ContentRange`、精确长度，并关闭 body。
  Range 不用全对象 checksum 冒充局部 checksum；Service 仍只向 raw artifact 暴露原文 range。
- `exists()` 只把确定的 404/`NoSuchKey|NotFound` 转为 `False`；403、5xx、timeout 和元数据错误不得
  伪装成不存在。
- 404 → `ArtifactNotFound`；416、size/range/metadata/checksum 不一致 → `ArtifactIntegrityError`；
  auth、网络、限流、5xx 和未知 Provider 错误 → 脱敏后的 `ArtifactStorageError`。
- `health()` 只做 `head_bucket`，返回 typed `ArtifactHealth`，不得写 probe 对象或泄露 endpoint
  userinfo/credential/signature；`aclose()` 调 client.close 且幂等。
- 不暴露 delete/list/presign/ACL API。bucket 创建、版本化、默认加密和 lifecycle 由基础设施管理。

## 6. 必测证据

使用 Botocore `Stubber` 或严格 fake client；默认测试不得联网或读取真实凭据：

1. builder 的 SigV4、timeout、pool、retry、addressing-style/endpoint 配置，以及 client 注入不建网。
2. exact key/prefix；PUT 参数确有 `IfNoneMatch="*"`、ContentLength、SHA256 checksum 和五项 metadata。
3. 2xx+HEAD created、412+HEAD dedupe、409 有界同条件重试；测试证明不存在无条件 PUT。
4. timeout 的 HEAD 收敛与未知状态 fail-closed；403/404/409/412/416/429/5xx 分类。
5. metadata 缺失、格式错误、与 ref 冲突、实际 stored size 冲突全部拒绝；ETag 不当内容 hash。
6. GET 只读 `stored_size+1`、body 在成功/异常路径都关闭；stored checksum 篡改拒绝。
7. `[start,end)`→HTTP 闭区间映射、空区间零请求、206/ContentRange/长度异常拒绝。
8. 相同内容/codec 但 MIME 不同仍可 dedupe；跨 zstd level 的不同 stored size 由实际 HEAD 收敛。
9. health 不写对象且脱敏、close 幂等、公共对象无 delete/list/presign。
10. config 默认/覆盖/交叉校验和 `.env.example` 无 credential；Local/R1 全部回归。

真实 AWS/MinIO/R2 key 不是本任务输入。未来选择 Provider 时必须以同一合同补一次隔离 bucket
conformance test；未做真实 Provider 测试不得宣称该 Provider 已获生产批准。

## 7. 验收命令

```bash
cd /code/pollymarket/v2/serve
.venv/bin/pip install -r requirements-dev.txt
python3 -m compileall -q app tests
.venv/bin/pytest -q tests/trading/test_v2_config.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_driver_contract.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_store.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_local.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_s3.py
.venv/bin/pytest -q tests/trading
.venv/bin/pytest -q
git diff --check
```

## 8. 交付、blocker、风险与回滚

创建且只创建 `serve/docs/manifests/wp-00c2-artifact-s3.md`。Manifest 必须记录：修改文件、真实命令
和测试数量、每条条件写/错误收敛/有界读取证据、配置矩阵、Provider conformance 未执行状态、blocker、
回滚及可复现 SHA。更新两个索引为 `DONE（待审）`，保持 00c2 为当前任务，等待用户再次说“完成”。
不得创建 00d、提交或推送。

主要风险是 S3-compatible Provider 对 `If-None-Match` 或 checksum 支持不完整；处理方式是 fail-closed
并在 Provider 选定时做 conformance，不是退化为覆盖写。回滚只删除 S3 Driver/测试，回退本任务配置、
依赖、实施合同及索引；Local Driver 和已冻结 R2 不受影响，无数据库或生产对象删除。

## 9. 非目标

- 不实现 multipart/streaming upload、对象删除、retention/archive、DB artifact 表、HTTP API、UI。
- 不接 main/lifespan，不切换默认 `ARTIFACT_DRIVER=local`，不创建 bucket，不修改 IAM/lifecycle/encryption。
- 不复用或修复 Base 旧 S3 Driver，不加入真实 key，不用 moto 的宽松行为代替 exact wire assertion。
- 不用 TODO、mock 生产实现、skip、吞异常、无条件 PUT 或只改 manifest 文字通过验收。
