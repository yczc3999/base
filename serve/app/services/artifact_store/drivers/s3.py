"""
S3-compatible Artifact Driver — 生产共享对象存储（WP-00c2）。

- 同步 boto3；client 由构造注入，builder 提供默认 client；禁止 import 时建全局连接。
- no-replace 唯一合法实现：`IfNoneMatch="*"` 条件 PUT。禁止先 HEAD 再无条件 PUT，
  禁止因 Provider 兼容性降级为覆盖写。
- ETag 是 Provider 不透明标识，不是内容 MD5；内容身份仍是未压缩原文 SHA-256。
- 凭据只走 boto3 标准 credential provider chain；不新增 access key/secret 字段，
  不把 credential/签名头/presigned URL 写入日志或异常。
- 条件写未知结果收敛：timeout/connection/SSL/proxy → HEAD reconcile，绝不无条件重发；
  其余 BotoCoreError（凭据/签名/参数/region）fail-closed 脱敏，不假定成功。
- 2xx 后 HEAD 严格验证本次创建：ContentLength == 上传 bytes 且 pm-stored-sha256 == 上传 digest；
  412 dedupe / transport reconcile 命中已有对象时允许 stored 与本次候选不同（返回 created=False）。
- get 有界读取（stored_size+1）并校验 stored checksum；get_range 严格半开区间 +
  五项元数据 + ContentLength==requested + HTTP 206 / ContentRange / requested+1 有界读取。
- health 只做 head_bucket、不写 probe；aclose 幂等；不暴露 delete/list/presign/ACL API。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    CredentialRetrievalError,
    EndpointConnectionError,
    HTTPClientError,
    NoCredentialsError,
    NoRegionError,
    ParamValidationError,
    ProxyConnectionError,
    ReadTimeoutError,
    ResponseStreamingError,
    SSLError,
)

from app.config import Settings
from app.services.artifact_store.contracts import (
    LOCATOR_VERSION,
    ArtifactError,
    ArtifactHead,
    ArtifactHealth,
    ArtifactIntegrityError,
    ArtifactNotFound,
    ArtifactRef,
    ArtifactStorageError,
    PutResult,
)

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# 五个 pm-* 元数据键：对象身份 + stored 校验
_METADATA_KEYS = (
    "pm-sha256",
    "pm-original-size",
    "pm-compression",
    "pm-storage-version",
    "pm-stored-sha256",
)

# 发送结果未知的传输级异常（connection/HTTP/SSL/proxy 层；触发 HEAD reconcile，而非无条件重发）。
# HTTPClientError 是 botocore HTTP/connection 未决错误基类，覆盖未显式列出的子类；
# SSLError 不继承 HTTPClientError，需显式列出。其余 BotoCoreError（凭据/签名/参数/region）
# 不得假定成功，一律 fail-closed 脱敏。
_TRANSPORT_ERRORS = (
    HTTPClientError,
    ConnectTimeoutError,
    ReadTimeoutError,
    EndpointConnectionError,
    ConnectionClosedError,
    SSLError,
    ProxyConnectionError,
)


class S3ArtifactDriver:
    """实现 `ArtifactDriver` Protocol（driver_name="s3"）。"""

    driver_name = "s3"

    def __init__(
        self,
        client: object,
        bucket: str,
        prefix: str = "",
        expected_bucket_owner: str = "",
        conditional_retry_limit: int | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("S3ArtifactDriver requires a non-empty bucket")
        if not isinstance(conditional_retry_limit, int) or isinstance(conditional_retry_limit, bool):
            raise ValueError(
                "conditional_retry_limit must be an int >= 1; "
                "builder must pass ARTIFACT_S3_MAX_ATTEMPTS"
            )
        if conditional_retry_limit < 1:
            raise ValueError("conditional_retry_limit must be >= 1")
        self._validate_prefix(prefix)
        self._client = client
        self._bucket = bucket
        self._prefix = prefix
        self._expected_owner = expected_bucket_owner or None
        self._retry_limit = conditional_retry_limit
        self._closed = False

    # ---- key / 请求组装 ----

    @staticmethod
    def _validate_prefix(prefix: str) -> None:
        """prefix 可空；非空时与 Settings 等价结构校验：禁首尾 `/`、空段、`.`、`..`、
        反斜杠、NUL/CR/LF；不 strip、不 normalize。直接构造也不能绕过配置合同。"""
        if not prefix:
            return
        if prefix.startswith("/") or prefix.endswith("/"):
            raise ValueError(f"invalid artifact S3 prefix: {prefix!r}")
        if any(c in prefix for c in ("\x00", "\r", "\n", "\\")):
            raise ValueError(f"invalid artifact S3 prefix: {prefix!r}")
        for seg in prefix.split("/"):
            if not seg or seg in (".", ".."):
                raise ValueError(f"invalid artifact S3 prefix: {prefix!r}")

    def _key(self, locator: str) -> str:
        if self._prefix:
            return f"{self._prefix}/{locator}"
        return locator

    def _owner_args(self) -> dict:
        if self._expected_owner:
            return {"ExpectedBucketOwner": self._expected_owner}
        return {}

    # ---- 错误分类与脱敏 ----

    @staticmethod
    def _status(exc: ClientError) -> int:
        return int(exc.response["ResponseMetadata"]["HTTPStatusCode"])

    @staticmethod
    def _code(exc: ClientError) -> str:
        return exc.response["Error"]["Code"]

    @staticmethod
    def _is_not_found(exc: ClientError) -> bool:
        return S3ArtifactDriver._status(exc) == 404 or S3ArtifactDriver._code(exc) in (
            "NoSuchKey",
            "NotFound",
        )

    def _storage_error(self, exc: BaseException, op: str) -> ArtifactStorageError:
        # 脱敏：只带 HTTP status/code，不含可能含凭据/endpoint/签名/原始消息
        detail = ""
        if isinstance(exc, ClientError):
            detail = f" (HTTP {self._status(exc)} {self._code(exc)})"
        return ArtifactStorageError(f"S3 {op} failed{detail}")

    @staticmethod
    def _error_category(exc: BaseException) -> str:
        """低基数安全分类：只按异常类型映射，绝不携带消息/endpoint/凭据/签名。"""
        if isinstance(exc, (NoCredentialsError, CredentialRetrievalError)):
            return "credentials"
        if isinstance(exc, ParamValidationError):
            return "invalid-request"
        if isinstance(exc, NoRegionError):
            return "no-region"
        if isinstance(exc, _TRANSPORT_ERRORS):
            return "transport"
        return "boto-core"

    # ---- 元数据校验 ----

    def _validate_metadata(self, metadata: dict, ref: ArtifactRef) -> None:
        missing = [k for k in _METADATA_KEYS if k not in metadata]
        if missing:
            raise ArtifactIntegrityError(
                f"S3 object {ref.locator} missing metadata keys: {missing}"
            )
        if metadata["pm-sha256"] != ref.sha256:
            raise ArtifactIntegrityError(
                f"S3 object sha256 metadata mismatch for {ref.locator}"
            )
        try:
            orig = int(metadata["pm-original-size"])
        except (TypeError, ValueError):
            raise ArtifactIntegrityError(
                f"S3 object pm-original-size is not an int for {ref.locator}"
            ) from None
        if orig != ref.original_size:
            raise ArtifactIntegrityError(
                f"S3 object original-size mismatch for {ref.locator}: "
                f"{orig} != {ref.original_size}"
            )
        if metadata["pm-compression"] != ref.compression:
            raise ArtifactIntegrityError(
                f"S3 object compression metadata mismatch for {ref.locator}"
            )
        if metadata["pm-storage-version"] != LOCATOR_VERSION:
            raise ArtifactIntegrityError(
                f"S3 object storage-version metadata mismatch for {ref.locator}"
            )
        stored = metadata["pm-stored-sha256"]
        # 先确认是 str 且精确 64 位小写 hex，再 regex；非字符串绝不进 regex（防 TypeError 泄出）
        if not isinstance(stored, str) or not _SHA256_HEX_RE.match(stored):
            raise ArtifactIntegrityError(
                f"S3 object pm-stored-sha256 is not a 64-lowercase-hex string for {ref.locator}"
            )

    @staticmethod
    def _parse_content_length(value: object, locator: str) -> int:
        """S3 ContentLength 必须是 `type is int`（拒绝 bool/float/numeric string），且非负。
        不得用 int(...) 截断/转换 Provider 响应后再接受。缺失/None/非法 → IntegrityError。"""
        if type(value) is not int:
            raise ArtifactIntegrityError(
                f"S3 response ContentLength is not an int for {locator}: {value!r}"
            )
        if value < 0:
            raise ArtifactIntegrityError(
                f"S3 response ContentLength negative for {locator}: {value}"
            )
        return value

    def _read_bounded(self, body: object, limit: int, label: str) -> bytes:
        """有界读取 body.read(limit)。body 属于 Provider 边界：transport/BotoCoreError
        统一收敛为脱敏 StorageError（from None，不泄原始消息/endpoint/凭据/签名）。
        不捕获 Artifact 内部异常、KeyboardInterrupt/SystemExit 或应用编程错误。"""
        try:
            return body.read(limit)
        except _TRANSPORT_ERRORS as e:
            raise self._storage_error(e, label) from None
        except BotoCoreError as e:
            raise self._storage_error(e, label) from None

    def _head_from_response(self, resp: dict, ref: ArtifactRef) -> ArtifactHead:
        stored_size = self._parse_content_length(resp.get("ContentLength"), ref.locator)
        self._validate_metadata(resp.get("Metadata") or {}, ref)
        return ArtifactHead(
            sha256=ref.sha256,
            original_size=ref.original_size,
            stored_size=stored_size,
            mime=ref.mime,
            compression=ref.compression,
            storage_version=LOCATOR_VERSION,
        )

    # ---- 条件写 ----

    def put_if_absent(self, candidate: ArtifactRef, data: bytes) -> PutResult:
        """原子 no-replace 条件 PUT；已存在返回 created=False 与**实际** head。"""
        if len(data) != candidate.stored_size:
            raise ArtifactIntegrityError(
                f"S3 put data size {len(data)} != candidate.stored_size "
                f"{candidate.stored_size}"
            )
        stored_digest = hashlib.sha256(data)
        kwargs = {
            "Bucket": self._bucket,
            "Key": self._key(candidate.locator),
            "Body": data,
            "ContentLength": len(data),
            "ContentType": candidate.mime,
            "IfNoneMatch": "*",
            "ChecksumAlgorithm": "SHA256",
            "ChecksumSHA256": base64.b64encode(stored_digest.digest()).decode("ascii"),
            "Metadata": {
                "pm-sha256": candidate.sha256,
                "pm-original-size": str(candidate.original_size),
                "pm-compression": candidate.compression,
                "pm-storage-version": candidate.storage_version,
                "pm-stored-sha256": stored_digest.hexdigest(),
            },
        }
        kwargs.update(self._owner_args())

        attempt = 0
        while True:
            try:
                self._client.put_object(**kwargs)
                break  # 2xx
            except ClientError as e:
                status = self._status(e)
                code = self._code(e)
                if status == 412 or code == "PreconditionFailed":
                    # 已存在（去重）：HEAD 返回实际 head，Service 负责读取验证
                    return PutResult(created=False, head=self.head(candidate))
                if status == 409 or code == "ConditionalRequestConflict":
                    attempt += 1
                    if attempt >= self._retry_limit:
                        # 抑制外部 ClientError context，防 traceback 打印含 endpoint/凭据的原始消息
                        raise ArtifactStorageError(
                            f"S3 conditional PUT (IfNoneMatch=*) 409-conflicted "
                            f"{attempt}x; giving up"
                        ) from None
                    continue  # 重放同一条件 PUT
                if status in (400, 501):
                    # Provider 不支持 conditional write / checksum → fail-closed
                    raise ArtifactStorageError(
                        f"S3 provider rejected conditional write or checksum "
                        f"(HTTP {status}); refusing to degrade to unconditional PUT"
                    ) from None
                raise self._storage_error(e, "put_object") from None
            except _TRANSPORT_ERRORS as e:
                # 发送结果未知：HEAD reconcile，绝不无条件重发
                return self._reconcile(candidate, e)
            except BotoCoreError as e:
                # 凭据/签名/参数/region 等：不做假定成功，直接 fail-closed 脱敏；
                # from None 抑制外部 cause，防 traceback 打印含 endpoint/凭据的原始异常
                raise self._storage_error(e, "put_object") from None

        # 2xx → HEAD 同一 key，严格验证本次创建结果：ContentLength == len(data) 且
        # pm-stored-sha256 == sha256(data)，任一不符 fail-closed，不得返回"现在成功、以后读失败"的 ref
        raw = self._head_raw(candidate)
        head = self._head_from_response(raw, candidate)
        if head.stored_size != len(data):
            raise ArtifactIntegrityError(
                f"S3 created object size mismatch for {candidate.locator}: "
                f"HEAD {head.stored_size} != uploaded {len(data)}"
            )
        if (raw.get("Metadata") or {}).get("pm-stored-sha256") != stored_digest.hexdigest():
            raise ArtifactIntegrityError(
                f"S3 created object stored-sha mismatch for {candidate.locator}"
            )
        return PutResult(created=True, head=head)

    def _reconcile(self, candidate: ArtifactRef, cause: BaseException) -> PutResult:
        """发送结果未知 → HEAD reconcile：匹配对象存在则 created=False；确定不存在或
        无法定案则抛受控错误，由上层以相同 CAS 请求重试。"""
        try:
            head = self.head(candidate)
        except ArtifactNotFound:
            raise ArtifactStorageError(
                "S3 put result unknown and HEAD reconcile found no object; "
                "retry the same conditional PUT"
            ) from None  # cause 可能是含 endpoint 的 transport 异常，抑制打印
        except ArtifactError:
            raise
        return PutResult(created=False, head=head)

    # ---- 读 ----

    def _head_raw(self, ref: ArtifactRef) -> dict:
        """HEAD 返回原始响应；任何原生 BotoCoreError 收敛为脱敏 StorageError。"""
        kwargs = {"Bucket": self._bucket, "Key": self._key(ref.locator)}
        kwargs.update(self._owner_args())
        try:
            return self._client.head_object(**kwargs)
        except ClientError as e:
            if self._is_not_found(e):
                raise ArtifactNotFound(f"S3 object not found: {ref.locator}") from None
            raise self._storage_error(e, "head_object") from None
        except _TRANSPORT_ERRORS as e:
            raise self._storage_error(e, "head_object") from None
        except BotoCoreError as e:
            raise self._storage_error(e, "head_object") from None

    def head(self, ref: ArtifactRef) -> ArtifactHead:
        return self._head_from_response(self._head_raw(ref), ref)

    def get(self, ref: ArtifactRef) -> bytes:
        kwargs = {
            "Bucket": self._bucket,
            "Key": self._key(ref.locator),
            "ChecksumMode": "ENABLED",
        }
        kwargs.update(self._owner_args())
        try:
            resp = self._client.get_object(**kwargs)
        except ClientError as e:
            if self._is_not_found(e):
                raise ArtifactNotFound(f"S3 object not found: {ref.locator}") from None
            raise self._storage_error(e, "get_object") from None
        except _TRANSPORT_ERRORS as e:
            raise self._storage_error(e, "get_object") from None
        except BotoCoreError as e:
            raise self._storage_error(e, "get_object") from None
        body = resp.get("Body")
        try:
            if body is None:
                raise ArtifactIntegrityError(f"S3 response missing Body for {ref.locator}")
            self._validate_metadata(resp.get("Metadata") or {}, ref)
            actual = self._parse_content_length(resp.get("ContentLength"), ref.locator)
            if actual != ref.stored_size:
                raise ArtifactIntegrityError(
                    f"S3 stored size mismatch for {ref.locator}: "
                    f"actual {actual} != {ref.stored_size}"
                )
            # 有界读取：body.read 属于 Provider 边界，transport/BotoCore 异常经 helper 收敛
            data = self._read_bounded(body, ref.stored_size + 1, "get_object body")
            if len(data) != ref.stored_size:
                raise ArtifactIntegrityError(
                    f"S3 stored size mismatch on read for {ref.locator}: got {len(data)}"
                )
            stored_sha_hex = hashlib.sha256(data).hexdigest()
            meta = resp.get("Metadata") or {}
            if meta.get("pm-stored-sha256") != stored_sha_hex:
                raise ArtifactIntegrityError(
                    f"S3 stored sha256 mismatch for {ref.locator}"
                )
            checksum = resp.get("ChecksumSHA256")
            if checksum is not None:
                if not isinstance(checksum, (str, bytes)):
                    raise ArtifactIntegrityError(
                        f"S3 response ChecksumSHA256 not str/bytes for {ref.locator}"
                    )
                try:
                    decoded = base64.b64decode(checksum, validate=True)
                except (binascii.Error, ValueError):
                    raise ArtifactIntegrityError(
                        f"S3 response ChecksumSHA256 malformed for {ref.locator}"
                    ) from None
                if len(decoded) != 32:
                    raise ArtifactIntegrityError(
                        f"S3 response ChecksumSHA256 not a SHA-256 digest for {ref.locator}"
                    )
                if decoded != hashlib.sha256(data).digest():
                    raise ArtifactIntegrityError(
                        f"S3 response ChecksumSHA256 mismatch for {ref.locator}"
                    )
            return data
        finally:
            try:
                body.close()
            except Exception:  # noqa: BLE001 - 清理尽力而为
                pass

    def get_range(self, ref: ArtifactRef, start: int, end: int) -> bytes:
        """stored bytes 半开区间 [start, end)；空区间零请求。"""
        if isinstance(start, bool) or not isinstance(start, int):
            raise ValueError(f"range start must be an int, got {start!r}")
        if isinstance(end, bool) or not isinstance(end, int):
            raise ValueError(f"range end must be an int, got {end!r}")
        if start < 0 or end < start or end > ref.stored_size:
            raise ValueError(
                f"invalid range [{start}, {end}) for stored_size {ref.stored_size}"
            )
        if start == end:
            return b""
        last = end - 1
        kwargs = {
            "Bucket": self._bucket,
            "Key": self._key(ref.locator),
            "Range": f"bytes={start}-{last}",
            "ChecksumMode": "ENABLED",
        }
        kwargs.update(self._owner_args())
        try:
            resp = self._client.get_object(**kwargs)
        except ClientError as e:
            status = self._status(e)
            code = self._code(e)
            if self._is_not_found(e):
                raise ArtifactNotFound(f"S3 object not found: {ref.locator}") from None
            if status == 416 or code == "InvalidRange":
                raise ArtifactIntegrityError(
                    f"S3 range [{start}, {end}) invalid for {ref.locator}"
                ) from None
            raise self._storage_error(e, "get_object range") from None
        except _TRANSPORT_ERRORS as e:
            raise self._storage_error(e, "get_object range") from None
        except BotoCoreError as e:
            raise self._storage_error(e, "get_object range") from None
        requested = end - start
        body = resp.get("Body")
        try:
            if body is None:
                raise ArtifactIntegrityError(f"S3 response missing Body for {ref.locator}")
            status_code = resp.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status_code != 206:
                raise ArtifactIntegrityError(
                    f"S3 range expected HTTP 206, got {status_code}"
                )
            content_range = resp.get("ContentRange", "")
            expected = f"bytes {start}-{last}/{ref.stored_size}"
            if content_range != expected:
                raise ArtifactIntegrityError(
                    f"S3 range ContentRange {content_range!r} != expected {expected!r}"
                )
            # 身份与响应长度校验：读取 body 前验证五项 pm-* 元数据 + ContentLength 精确等于请求长度，
            # 防恶意/错误响应把区间读取放大为整对象
            content_length = self._parse_content_length(
                resp.get("ContentLength"), ref.locator
            )
            if content_length != requested:
                raise ArtifactIntegrityError(
                    f"S3 range ContentLength {content_length} != requested "
                    f"{requested} for {ref.locator}"
                )
            self._validate_metadata(resp.get("Metadata") or {}, ref)
            # 有界读取：只多 1 byte 用于发现 Provider 忽略/扩大 Range；不计算全对象 checksum
            data = self._read_bounded(body, requested + 1, "get_object range body")
            if len(data) != requested:
                raise ArtifactIntegrityError(
                    f"S3 range returned {len(data)} bytes, expected {requested}"
                )
            return data
        finally:
            try:
                body.close()
            except Exception:  # noqa: BLE001 - 清理尽力而为
                pass

    def exists(self, ref: ArtifactRef) -> bool:
        """存在且是**有效 CAS 对象**（五项 metadata + ContentLength 校验通过）才 True；
        确定 404 才 False；metadata 缺失/冲突或 size 非法 → IntegrityError；
        403/5xx/BotoCoreError → StorageError，不得伪装成不存在。只发一次 HEAD。"""
        try:
            self.head(ref)
            return True
        except ArtifactNotFound:
            return False

    # ---- health / 关闭 ----

    def health(self) -> ArtifactHealth:
        kwargs = {"Bucket": self._bucket}
        kwargs.update(self._owner_args())
        try:
            self._client.head_bucket(**kwargs)
            return ArtifactHealth(ok=True, driver="s3", detail={"bucket": self._bucket})
        except ClientError as e:
            # 脱敏：不泄露 endpoint userinfo / credential / signature
            return ArtifactHealth(
                ok=False, driver="s3", detail={"error": f"HTTP {self._status(e)}"}
            )
        except _TRANSPORT_ERRORS:
            return ArtifactHealth(ok=False, driver="s3", detail={"error": "transport"})
        except BotoCoreError as e:
            # 任意 BotoCoreError（凭据/签名/参数…）：返回 typed health，不抛异常、不泄消息
            return ArtifactHealth(
                ok=False,
                driver="s3",
                detail={"error": S3ArtifactDriver._error_category(e)},
            )

    def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            self._client.close()


def build_s3_artifact_driver(settings: Settings, client: object | None = None) -> S3ArtifactDriver:
    """从 Settings 构建 S3 Driver；注入 client 时禁止再创建真实 client。"""
    if settings.ARTIFACT_DRIVER != "s3":
        raise ValueError(
            f"ARTIFACT_DRIVER must be 's3' to build an S3 artifact driver, got "
            f"{settings.ARTIFACT_DRIVER!r}"
        )
    if client is None:
        config = Config(
            signature_version="s3v4",                     # 显式 SigV4，不依赖区域/Provider 隐式默认
            connect_timeout=settings.ARTIFACT_S3_CONNECT_TIMEOUT_S,
            read_timeout=settings.ARTIFACT_S3_READ_TIMEOUT_S,
            max_pool_connections=settings.ARTIFACT_S3_MAX_POOL_CONNECTIONS,
            retries={
                "mode": "standard",
                "total_max_attempts": settings.ARTIFACT_S3_MAX_ATTEMPTS,
            },
            s3={"addressing_style": settings.ARTIFACT_S3_ADDRESSING_STYLE},
        )
        client = boto3.client(
            "s3",
            region_name=settings.ARTIFACT_S3_REGION,
            endpoint_url=settings.ARTIFACT_S3_ENDPOINT_URL or None,
            config=config,
        )
    # 注入 client 与自建 client 同一 retry policy：Driver 条件 PUT 上限 = MAX_ATTEMPTS，
    # 与 botocore total attempts 同一来源；无第二套硬编码次数
    return S3ArtifactDriver(
        client=client,
        bucket=settings.ARTIFACT_S3_BUCKET,
        prefix=settings.ARTIFACT_S3_PREFIX,
        expected_bucket_owner=settings.ARTIFACT_S3_EXPECTED_BUCKET_OWNER or None,
        conditional_retry_limit=settings.ARTIFACT_S3_MAX_ATTEMPTS,
    )
