"""
WP-00c2 S3-compatible Artifact Driver 验收测试。

使用严格 fake client（精确 wire 断言 / 错误注入 / 传输级异常）与 Botocore Stubber（一组 exact
wire 断言）；无网络、无真实凭据。另以内存"类 S3"实现跑 Service 集成（MIME 去重、跨 zstd level
去重收敛、roundtrip、range）。

覆盖：builder 配置与 client 注入；exact key/prefix；条件 PUT（IfNoneMatch=* + checksum + 5 元数据）；
2xx+HEAD created / 412 dedupe / 409 有界重试 / 传输未知 HEAD reconcile / 400·501 fail-closed；
元数据缺失/格式/冲突拒绝；GET 有界读取 + body 全路径关闭 + stored checksum 篡改拒绝；Range
[ )→闭区间映射、空区间零请求、206/ContentRange/长度异常拒绝；health 只 head_bucket 且脱敏；
aclose 幂等；无 delete/list/presign；Service 集成去重。
"""

import base64
import hashlib
import io

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    HTTPClientError,
    NoCredentialsError,
    ReadTimeoutError,
    ResponseStreamingError,
    SSLError,
)
from botocore.stub import Stubber

from app.config import Settings
from app.services.artifact_store import (
    LOCATOR_VERSION,
    ArtifactHead,
    ArtifactIntegrityError,
    ArtifactNotFound,
    ArtifactRef,
    ArtifactStorageError,
    ArtifactStore,
    build_locator,
)
from app.services.artifact_store.drivers.s3 import (
    S3ArtifactDriver,
    build_s3_artifact_driver,
)

SHA = "a" * 64


def _ref(sha: str = SHA, original_size: int = 3, stored_size: int = 3,
         mime: str = "text/plain", compression: str = "none",
         locator: str | None = None) -> ArtifactRef:
    return ArtifactRef(
        sha256=sha,
        original_size=original_size,
        stored_size=stored_size,
        mime=mime,
        compression=compression,
        storage_driver="s3",
        locator=locator or build_locator(sha, compression),
        storage_version=LOCATOR_VERSION,
    )


def _client_error(code: str, status: int, message: str = "injected") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message},
         "ResponseMetadata": {"HTTPStatusCode": status, "RequestId": "rid"}},
        "op",
    )


def _meta(ref: ArtifactRef, stored_sha_hex: str | None = None) -> dict:
    return {
        "pm-sha256": ref.sha256,
        "pm-original-size": str(ref.original_size),
        "pm-compression": ref.compression,
        "pm-storage-version": "cas/v1",
        "pm-stored-sha256": stored_sha_hex or (ref.sha256 if ref.compression == "none" else "b" * 64),
    }


def _head_response(ref: ArtifactRef, stored_size: int, metadata: dict | None = None) -> dict:
    return {
        "ContentLength": stored_size,
        "Metadata": metadata if metadata is not None else _meta(ref),
        "ContentType": ref.mime,
    }


def _s3_settings(bucket: str = "mybucket", **over) -> Settings:
    base = dict(
        _env_file=None,
        ARTIFACT_DRIVER="s3",
        ARTIFACT_S3_BUCKET=bucket,
        ARTIFACT_S3_PREFIX="v2-artifacts",
        ARTIFACT_S3_REGION="us-east-1",
        ARTIFACT_LOCAL_ROOT="/tmp/x",
        ARTIFACT_INLINE_THRESHOLD_BYTES=1,
        ARTIFACT_COMPRESSION_THRESHOLD_BYTES=1,
        ARTIFACT_MAX_OBJECT_BYTES=67_108_864,
    )
    base.update(over)
    return Settings(**base)


class RecordingClient:
    """严格 fake：按序播放 responses，记录全部调用；默认拒绝未计划的调用。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._plan: dict[str, list] = {}
        self.close_calls = 0

    def add(self, op: str, item) -> None:
        self._plan.setdefault(op, []).append(item)

    def _play(self, op: str, kw: dict):
        self.calls.append((op, kw))
        seq = self._plan.get(op) or []
        if not seq:
            if op == "head_bucket":
                return {}
            raise AssertionError(f"unexpected {op} call with {kw}")
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def put_object(self, **kw):
        return self._play("put_object", kw)

    def get_object(self, **kw):
        return self._play("get_object", kw)

    def head_object(self, **kw):
        return self._play("head_object", kw)

    def head_bucket(self, **kw):
        return self._play("head_bucket", kw)

    def close(self):
        self.close_calls += 1


class _ClosingBytesIO(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.closed_flag = False

    def close(self):
        self.closed_flag = True
        super().close()


class _ReadSpy(io.BytesIO):
    """记录最大 read(n) 大小，验证有界读取。"""

    def __init__(self, data: bytes):
        super().__init__(data)
        self.max_read = 0

    def read(self, n=-1):
        if isinstance(n, int) and n >= 0:
            self.max_read = max(self.max_read, n)
        return super().read(n)


class _FailReadSpy:
    """read(n) 记录 n 后抛指定异常；模拟 StreamingBody 读取失败。"""

    def __init__(self, exc: Exception):
        self.exc = exc
        self.max_read = 0
        self.closed_flag = False

    def read(self, n=-1):
        if isinstance(n, int) and n >= 0:
            self.max_read = max(self.max_read, n)
        raise self.exc

    def close(self):
        self.closed_flag = True


class InMemoryS3:
    """内存"类 S3"：IfNoneMatch=* 条件写、元数据、ContentLength、Range/206、错误码。"""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def put_object(self, **kw):
        self.calls.append(("put_object", kw))
        key = kw["Key"]
        if key in self.objects and kw.get("IfNoneMatch") == "*":
            raise _client_error("PreconditionFailed", 412)
        self.objects[key] = {
            "Body": kw["Body"],
            "ContentLength": kw.get("ContentLength", len(kw["Body"])),
            "Metadata": dict(kw.get("Metadata") or {}),
            "ContentType": kw.get("ContentType", ""),
        }
        return {}

    def head_object(self, **kw):
        self.calls.append(("head_object", kw))
        o = self.objects.get(kw["Key"])
        if o is None:
            raise _client_error("NotFound", 404)
        return {"ContentLength": o["ContentLength"], "Metadata": o["Metadata"],
                "ContentType": o["ContentType"]}

    def get_object(self, **kw):
        self.calls.append(("get_object", kw))
        o = self.objects.get(kw["Key"])
        if o is None:
            raise _client_error("NoSuchKey", 404)
        body = o["Body"]
        n = len(body)
        rng = kw.get("Range")
        if rng:
            _, r = rng.split("=", 1)
            start_s, last_s = r.split("-")
            start, last = int(start_s), int(last_s)
            if start >= n or last >= n:
                raise _client_error("InvalidRange", 416)
            chunk = body[start:last + 1]
            return {"Body": io.BytesIO(chunk), "ContentLength": len(chunk),
                    "ContentRange": f"bytes {start}-{last}/{n}",
                    "Metadata": o["Metadata"], "ContentType": o["ContentType"],
                    "ResponseMetadata": {"HTTPStatusCode": 206}}
        return {"Body": io.BytesIO(body), "ContentLength": n, "Metadata": o["Metadata"],
                "ContentType": o["ContentType"],
                "ChecksumSHA256": base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")}

    def head_bucket(self, **kw):
        self.calls.append(("head_bucket", kw))
        return {}

    def close(self):
        self.close_calls += 1


def _driver(client, bucket: str = "mybucket", prefix: str = "v2-artifacts", **kw) -> S3ArtifactDriver:
    kw.setdefault("conditional_retry_limit", 3)
    return S3ArtifactDriver(client=client, bucket=bucket, prefix=prefix, **kw)


def _service(s3_fake, bucket: str = "mybucket", **over) -> ArtifactStore:
    driver = S3ArtifactDriver(client=s3_fake, bucket=bucket, prefix="v2-artifacts",
                              conditional_retry_limit=3)
    cfg = dict(
        _env_file=None,
        ARTIFACT_DRIVER="s3",
        ARTIFACT_S3_BUCKET=bucket,
        ARTIFACT_LOCAL_ROOT="/tmp/x",
        ARTIFACT_INLINE_THRESHOLD_BYTES=16384,
        ARTIFACT_COMPRESSION_THRESHOLD_BYTES=16384,
        ARTIFACT_ZSTD_LEVEL=6,
        ARTIFACT_MAX_OBJECT_BYTES=67_108_864,
        ARTIFACT_VERIFY_ON_READ=True,
    )
    cfg.update(over)
    return ArtifactStore(driver, Settings(**cfg)), driver, s3_fake


# ---------------- builder ----------------

def test_builder_client_injection_no_network(monkeypatch):
    fake = RecordingClient()
    monkeypatch.setattr(
        boto3, "client",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("boto3.client must not be called")),
    )
    d = build_s3_artifact_driver(_s3_settings(), client=fake)
    assert d._client is fake
    assert d._bucket == "mybucket"
    assert d.driver_name == "s3"


def test_builder_creates_client_with_config(monkeypatch):
    captured = {}
    fake = RecordingClient()

    def fake_factory(service_name, *, region_name=None, endpoint_url=None, config=None, **kw):
        captured.update(kw)
        captured["service_name"] = service_name
        captured["region_name"] = region_name
        captured["endpoint_url"] = endpoint_url
        captured["config"] = config
        return fake

    monkeypatch.setattr(boto3, "client", fake_factory)
    d = build_s3_artifact_driver(
        _s3_settings(ARTIFACT_S3_ENDPOINT_URL="https://minio.example:9000"))
    assert d._client is fake
    assert captured["service_name"] == "s3"
    assert captured["region_name"] == "us-east-1"
    assert captured["endpoint_url"] == "https://minio.example:9000"
    assert "aws_access_key_id" not in captured
    assert "aws_secret_access_key" not in captured
    cfg = captured["config"]
    assert isinstance(cfg, Config)
    assert cfg.signature_version == "s3v4"                       # 显式 SigV4，不依赖隐式默认
    assert d._retry_limit == 3                                    # retry policy = MAX_ATTEMPTS
    assert cfg.connect_timeout == 2.0
    assert cfg.read_timeout == 10.0
    assert cfg.max_pool_connections == 20
    assert cfg.retries == {"mode": "standard", "total_max_attempts": 3}
    assert cfg.s3 == {"addressing_style": "auto"}


def test_builder_requires_s3_driver(monkeypatch):
    monkeypatch.setattr(
        boto3, "client",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("boto3.client must not be called")),
    )
    with pytest.raises(ValueError):
        build_s3_artifact_driver(_s3_settings(ARTIFACT_DRIVER="local"), client=RecordingClient())


# ---------------- 精确 wire：条件 PUT ----------------

def test_put_exact_wire_params():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    data = b"abc"
    stored_sha_hex = hashlib.sha256(data).hexdigest()
    client.add("put_object", {})
    client.add("head_object", _head_response(ref, 3, metadata=_meta(ref, stored_sha_hex)))
    res = d.put_if_absent(ref, data)
    assert res.created is True
    op, kw = client.calls[0]
    assert op == "put_object"
    assert kw["Bucket"] == "mybucket"
    assert kw["Key"] == "v2-artifacts/" + ref.locator
    assert kw["Body"] == data
    assert kw["ContentLength"] == 3
    assert kw["ContentType"] == "text/plain"
    assert kw["IfNoneMatch"] == "*"
    assert kw["ChecksumAlgorithm"] == "SHA256"
    assert kw["ChecksumSHA256"] == base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")
    assert kw["Metadata"] == {
        "pm-sha256": ref.sha256,
        "pm-original-size": "3",
        "pm-compression": "none",
        "pm-storage-version": "cas/v1",
        "pm-stored-sha256": stored_sha_hex,
    }
    # 2xx 后必须 HEAD 同一 key 验证（第二笔调用是 head_object，不是再 put）
    assert client.calls[1][0] == "head_object"
    assert client.calls[1][1]["Key"] == kw["Key"]


def test_put_without_prefix_uses_locator():
    client = RecordingClient()
    d = _driver(client, prefix="")
    ref = _ref()
    client.add("put_object", {})
    client.add("head_object", _head_response(ref, 3, metadata=_meta(ref, hashlib.sha256(b"abc").hexdigest())))
    d.put_if_absent(ref, b"abc")
    assert client.calls[0][1]["Key"] == ref.locator


def test_put_expected_bucket_owner_included():
    client = RecordingClient()
    d = _driver(client, expected_bucket_owner="acct-123")
    ref = _ref()
    client.add("put_object", {})
    client.add("head_object", _head_response(ref, 3, metadata=_meta(ref, hashlib.sha256(b"abc").hexdigest())))
    d.put_if_absent(ref, b"abc")
    assert client.calls[0][1]["ExpectedBucketOwner"] == "acct-123"
    assert client.calls[1][1]["ExpectedBucketOwner"] == "acct-123"


def test_put_data_size_mismatch_rejected_before_wire():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=3, stored_size=3)
    with pytest.raises(ArtifactIntegrityError):
        d.put_if_absent(ref, b"abcd")            # 4 != 3
    assert client.calls == []                    # 不产生任何 S3 调用


# ---------------- 2xx+HEAD created / 412 dedupe / 409 重试 ----------------

def test_put_created_head_returns_actual_stored():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("put_object", {})
    client.add("head_object", _head_response(ref, 3, metadata=_meta(ref, hashlib.sha256(b"abc").hexdigest())))
    res = d.put_if_absent(ref, b"abc")
    assert res.created is True
    assert res.head.stored_size == 3
    assert res.head.original_size == 3


def test_put_412_dedupe_head_only():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("put_object", _client_error("PreconditionFailed", 412))
    client.add("head_object", _head_response(ref, 3))
    res = d.put_if_absent(ref, b"abc")
    assert res.created is False
    assert res.head.stored_size == 3
    puts = [c for c in client.calls if c[0] == "put_object"]
    assert len(puts) == 1                         # 绝不无条件重发
    assert client.calls[1][0] == "head_object"


def test_put_412_head_metadata_conflict_fails_closed():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("put_object", _client_error("PreconditionFailed", 412))
    bad_meta = _meta(ref)
    bad_meta["pm-sha256"] = "b" * 64              # 与候选原文 SHA 冲突
    client.add("head_object", _head_response(ref, 3, metadata=bad_meta))
    with pytest.raises(ArtifactIntegrityError):
        d.put_if_absent(ref, b"abc")


def test_put_409_bounded_retry_same_conditional_put():
    client = RecordingClient()
    d = _driver(client, conditional_retry_limit=3)
    ref = _ref()
    client.add("put_object", _client_error("ConditionalRequestConflict", 409))
    client.add("put_object", _client_error("ConditionalRequestConflict", 409))
    client.add("put_object", {})
    client.add("head_object", _head_response(ref, 3, metadata=_meta(ref, hashlib.sha256(b"abc").hexdigest())))
    res = d.put_if_absent(ref, b"abc")
    assert res.created is True
    puts = [c for c in client.calls if c[0] == "put_object"]
    assert len(puts) == 3
    for op, kw in puts:
        assert kw["IfNoneMatch"] == "*"           # 每次都同一条件 PUT
        assert kw["Key"] == "v2-artifacts/" + ref.locator


def test_put_409_exhaustion_raises_storage_error():
    client = RecordingClient()
    d = _driver(client, conditional_retry_limit=2)
    ref = _ref()
    client.add("put_object", _client_error("ConditionalRequestConflict", 409))
    client.add("put_object", _client_error("ConditionalRequestConflict", 409))
    client.add("put_object", _client_error("ConditionalRequestConflict", 409))
    with pytest.raises(ArtifactStorageError):
        d.put_if_absent(ref, b"abc")
    assert len([c for c in client.calls if c[0] == "put_object"]) == 2   # 有界：不超 limit


@pytest.mark.parametrize("code,status", [("InvalidRequest", 400), ("NotImplemented", 501)])
def test_put_unsupported_provider_fails_closed(code, status):
    client = RecordingClient()
    d = _driver(client)
    client.add("put_object", _client_error(code, status))
    with pytest.raises(ArtifactStorageError):
        d.put_if_absent(_ref(), b"abc")
    assert len([c for c in client.calls if c[0] == "put_object"]) == 1   # 不降级覆盖写


# ---------------- 传输未知：HEAD reconcile ----------------

def test_put_transport_timeout_head_reconcile_dedup():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("put_object", ConnectTimeoutError(endpoint_url="https://x:9000"))
    client.add("head_object", _head_response(ref, 3))
    res = d.put_if_absent(ref, b"abc")
    assert res.created is False                   # 对象匹配 → 去重
    assert len([c for c in client.calls if c[0] == "put_object"]) == 1   # 不无条件重发
    assert client.calls[1][0] == "head_object"


def test_put_transport_timeout_head_absent_raises():
    client = RecordingClient()
    d = _driver(client)
    client.add("put_object", ConnectTimeoutError(endpoint_url="https://x:9000"))
    client.add("head_object", _client_error("NotFound", 404))
    with pytest.raises(ArtifactStorageError):
        d.put_if_absent(_ref(), b"abc")


def test_put_transport_timeout_head_error_raises():
    client = RecordingClient()
    d = _driver(client)
    client.add("put_object", ConnectTimeoutError(endpoint_url="https://x:9000"))
    client.add("head_object", _client_error("ServiceUnavailable", 503))
    with pytest.raises(ArtifactStorageError):
        d.put_if_absent(_ref(), b"abc")


# ---------------- Stubber exact wire（botocore 官方工具） ----------------

def test_stubber_exact_wire_put_and_head():
    client = boto3.client("s3", region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test")
    stubber = Stubber(client)
    ref = _ref()
    data = b"abc"
    stored_sha = hashlib.sha256(data).hexdigest()
    meta = _meta(ref, stored_sha)                    # pm-stored-sha256 = 真实 stored bytes SHA
    stubber.add_response(
        "put_object", {},
        {"Bucket": "mybucket", "Key": "v2-artifacts/" + ref.locator, "Body": data,
         "ContentLength": 3, "ContentType": "text/plain", "IfNoneMatch": "*",
         "ChecksumAlgorithm": "SHA256",
         "ChecksumSHA256": base64.b64encode(hashlib.sha256(data).digest()).decode("ascii"),
         "Metadata": meta})
    stubber.add_response(
        "head_object", _head_response(ref, 3, metadata=meta),
        {"Bucket": "mybucket", "Key": "v2-artifacts/" + ref.locator})
    stubber.activate()
    d = _driver(client)
    res = d.put_if_absent(ref, data)
    stubber.assert_no_pending_responses()
    stubber.deactivate()
    assert res.created is True
    assert res.head.stored_size == 3


# ---------------- head / 元数据校验 ----------------

def test_head_returns_actual_stored_and_meta():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("head_object", _head_response(ref, 7))
    h = d.head(ref)
    assert isinstance(h, ArtifactHead)
    assert h.stored_size == 7
    assert h.original_size == 3
    assert h.storage_version == "cas/v1"


def test_head_404_not_found():
    client = RecordingClient()
    d = _driver(client)
    client.add("head_object", _client_error("NotFound", 404))
    with pytest.raises(ArtifactNotFound):
        d.head(_ref())


@pytest.mark.parametrize("mutate,label", [
    (lambda m: m.pop("pm-sha256"), "missing pm-sha256"),
    (lambda m: m.pop("pm-stored-sha256"), "missing pm-stored-sha256"),
    (lambda m: m.update({"pm-sha256": "b" * 64}), "sha256 conflict"),
    (lambda m: m.update({"pm-original-size": "999"}), "original-size conflict"),
    (lambda m: m.update({"pm-compression": "gzip"}), "compression conflict"),
    (lambda m: m.update({"pm-storage-version": "cas/v2"}), "storage-version conflict"),
    (lambda m: m.update({"pm-stored-sha256": "XYZ"}), "stored-sha not hex"),
    (lambda m: m.update({"pm-original-size": "abc"}), "original-size not int"),
])
def test_head_metadata_anomalies_rejected(mutate, label):
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    meta = _meta(ref)
    mutate(meta)
    client.add("head_object", _head_response(ref, 3, metadata=meta))
    with pytest.raises(ArtifactIntegrityError):
        d.head(ref)


def test_head_etag_is_not_content_hash():
    """ETag 是 Provider 不透明标识；head 仅用 ContentLength + pm-* 元数据，不信任 ETag。"""
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    resp = _head_response(ref, 3)
    resp["ETag"] = '"deadbeef"'                    # 任意 ETag 不影响身份判定
    client.add("head_object", resp)
    h = d.head(ref)
    assert h.sha256 == ref.sha256
    assert h.stored_size == 3


# ---------------- GET：有界读取 + body 关闭 + checksum ----------------

def test_get_reads_and_verifies_checksum():
    client = RecordingClient()
    d = _driver(client)
    data = b"payload"
    ref = _ref(original_size=7, stored_size=7)
    stored_sha = hashlib.sha256(data).hexdigest()
    client.add("get_object", {
        "Body": _ClosingBytesIO(data), "ContentLength": 7, "Metadata": _meta(ref, stored_sha),
        "ChecksumSHA256": base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")})
    got = d.get(ref)
    assert got == data
    op, kw = client.calls[0]
    assert kw["ChecksumMode"] == "ENABLED"


def test_get_bounded_read_only_stored_plus_one():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=3, stored_size=3)
    body = _ReadSpy(b"abc")
    client.add("get_object", {"Body": body, "ContentLength": 3,
                              "Metadata": _meta(ref, hashlib.sha256(b"abc").hexdigest())})
    d.get(ref)
    assert body.max_read == ref.stored_size + 1    # 只读 stored_size+1，禁止无界 .read()


def test_get_body_closed_on_error_path():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=3, stored_size=3)
    body = _ClosingBytesIO(b"abc")
    bad_meta = _meta(ref)
    bad_meta["pm-sha256"] = "b" * 64
    client.add("get_object", {"Body": body, "ContentLength": 3, "Metadata": bad_meta})
    with pytest.raises(ArtifactIntegrityError):
        d.get(ref)
    assert body.closed_flag is True                # 异常路径也关闭 body


def test_get_stored_checksum_tamper_rejected():
    client = RecordingClient()
    d = _driver(client)
    data = b"payload"
    ref = _ref(original_size=7, stored_size=7)
    body = _ClosingBytesIO(data)
    client.add("get_object", {"Body": body, "ContentLength": 7,
                              "Metadata": _meta(ref, "c" * 64)})   # pm-stored-sha256 与实际不符
    with pytest.raises(ArtifactIntegrityError):
        d.get(ref)
    assert body.closed_flag is True


def test_get_response_checksum_mismatch_rejected():
    client = RecordingClient()
    d = _driver(client)
    data = b"payload"
    ref = _ref(original_size=7, stored_size=7)
    stored_sha = hashlib.sha256(data).hexdigest()
    client.add("get_object", {
        "Body": _ClosingBytesIO(data), "ContentLength": 7, "Metadata": _meta(ref, stored_sha),
        "ChecksumSHA256": base64.b64encode(b"wrongsha256wrongsha256wrongsha256wr").decode("ascii")})
    with pytest.raises(ArtifactIntegrityError):
        d.get(ref)


def test_get_size_conflict_rejected():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=3, stored_size=3)
    client.add("get_object", {"Body": _ClosingBytesIO(b"abc"), "ContentLength": 4,
                              "Metadata": _meta(ref)})
    with pytest.raises(ArtifactIntegrityError):
        d.get(ref)


def test_get_not_found():
    client = RecordingClient()
    d = _driver(client)
    client.add("get_object", _client_error("NoSuchKey", 404))
    with pytest.raises(ArtifactNotFound):
        d.get(_ref())


def test_get_403_raises_storage_error():
    client = RecordingClient()
    d = _driver(client)
    client.add("get_object", _client_error("AccessDenied", 403))
    with pytest.raises(ArtifactStorageError):
        d.get(_ref())


# ---------------- Range ----------------

def test_get_range_maps_to_closed_interval():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    client.add("get_object", {
        "Body": _ClosingBytesIO(b"234"), "ContentLength": 3,
        "ContentRange": "bytes 2-4/10", "ResponseMetadata": {"HTTPStatusCode": 206},
        "Metadata": _meta(ref)})
    got = d.get_range(ref, 2, 5)
    assert got == b"234"
    assert client.calls[0][1]["Range"] == "bytes=2-4"
    assert client.calls[0][1]["Key"] == "v2-artifacts/" + ref.locator


def test_get_range_empty_zero_requests():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    assert d.get_range(ref, 5, 5) == b""
    assert client.calls == []                      # 空区间不发请求


def test_get_range_invalid_bounds():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    for s, e in [(-1, 5), (5, 2), (0, 11), (10, 11), (True, 5)]:
        with pytest.raises(ValueError):
            d.get_range(ref, s, e)


def test_get_range_requires_206():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    client.add("get_object", {
        "Body": _ClosingBytesIO(b"234"), "ContentLength": 3,
        "ContentRange": "bytes 2-4/10", "ResponseMetadata": {"HTTPStatusCode": 200},
        "Metadata": _meta(ref)})
    with pytest.raises(ArtifactIntegrityError):
        d.get_range(ref, 2, 5)


def test_get_range_content_range_mismatch():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    client.add("get_object", {
        "Body": _ClosingBytesIO(b"2345"), "ContentLength": 4,
        "ContentRange": "bytes 2-5/10", "ResponseMetadata": {"HTTPStatusCode": 206},
        "Metadata": _meta(ref)})
    with pytest.raises(ArtifactIntegrityError):
        d.get_range(ref, 2, 5)


def test_get_range_length_mismatch():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    client.add("get_object", {
        "Body": _ClosingBytesIO(b"2345"), "ContentLength": 4,
        "ContentRange": "bytes 2-4/10", "ResponseMetadata": {"HTTPStatusCode": 206},
        "Metadata": _meta(ref)})
    with pytest.raises(ArtifactIntegrityError):
        d.get_range(ref, 2, 5)


def test_get_range_416_integrity_error():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    client.add("get_object", _client_error("InvalidRange", 416))
    with pytest.raises(ArtifactIntegrityError):
        d.get_range(ref, 2, 5)


# ---------------- R1：SigV4 / attempts / 异常边界 / created 严格验证 / Range 身份 / prefix ----------------

def test_builder_retry_limit_from_settings_injected_client(monkeypatch):
    fake = RecordingClient()
    monkeypatch.setattr(
        boto3, "client",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("boto3.client must not be called")),
    )
    for attempts in (1, 2, 5):
        d = build_s3_artifact_driver(_s3_settings(ARTIFACT_S3_MAX_ATTEMPTS=attempts), client=fake)
        assert d._retry_limit == attempts             # 注入 client 也取 MAX_ATTEMPTS


def test_builder_retry_limit_from_settings_built_client(monkeypatch):
    fake = RecordingClient()
    monkeypatch.setattr(boto3, "client", lambda *a, **k: fake)
    d = build_s3_artifact_driver(_s3_settings(ARTIFACT_S3_MAX_ATTEMPTS=4))
    assert d._retry_limit == 4                        # 自建 client 同样取 MAX_ATTEMPTS
    assert d._client is fake


def test_put_attempts_1_first_409_fails_immediately():
    client = RecordingClient()
    d = build_s3_artifact_driver(_s3_settings(ARTIFACT_S3_MAX_ATTEMPTS=1), client=client)
    client.add("put_object", _client_error("ConditionalRequestConflict", 409))
    with pytest.raises(ArtifactStorageError):
        d.put_if_absent(_ref(), b"abc")
    assert len([c for c in client.calls if c[0] == "put_object"]) == 1  # 首个 409 即失败


def test_put_attempts_2_retries_once_then_success():
    client = RecordingClient()
    d = build_s3_artifact_driver(_s3_settings(ARTIFACT_S3_MAX_ATTEMPTS=2), client=client)
    ref = _ref()
    client.add("put_object", _client_error("ConditionalRequestConflict", 409))
    client.add("put_object", {})
    client.add("head_object",
               _head_response(ref, 3, metadata=_meta(ref, hashlib.sha256(b"abc").hexdigest())))
    res = d.put_if_absent(ref, b"abc")
    assert res.created is True
    assert len([c for c in client.calls if c[0] == "put_object"]) == 2  # 不超过 MAX_ATTEMPTS


@pytest.mark.parametrize("op", ["put", "head", "get", "range", "exists"])
def test_no_credentials_converged_for_all_ops(op):
    """NoCredentialsError 在全部操作收敛为脱敏 ArtifactStorageError，不原生逃逸。"""
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    no_creds = NoCredentialsError()
    if op == "put":
        client.add("put_object", no_creds)
        invoke = lambda: d.put_if_absent(ref, b"abcdefghij")  # noqa: E731
    elif op == "head":
        client.add("head_object", no_creds)
        invoke = lambda: d.head(ref)  # noqa: E731
    elif op == "get":
        client.add("get_object", no_creds)
        invoke = lambda: d.get(ref)  # noqa: E731
    elif op == "range":
        client.add("get_object", no_creds)
        invoke = lambda: d.get_range(ref, 2, 5)  # noqa: E731
    else:  # exists
        client.add("head_object", no_creds)
        invoke = lambda: d.exists(ref)  # noqa: E731
    with pytest.raises(ArtifactStorageError) as ei:
        invoke()
    assert "Unable to locate" not in str(ei.value)   # 原始消息不泄出


def test_health_no_credentials_returns_false_redacted():
    client = RecordingClient()
    d = _driver(client)
    client.add("head_bucket", NoCredentialsError())
    h = d.health()
    assert h.ok is False and h.driver == "s3"
    assert h.detail == {"error": "credentials"}       # 低基数分类，无原始消息
    assert "Unable to locate" not in str(h.detail)


def test_put_ssl_transport_head_reconcile():
    """SSL 传输异常 → HEAD reconcile（发送结果未知），绝不无条件重发。"""
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("put_object", SSLError(endpoint_url="https://secret-host:9000", error="boom"))
    client.add("head_object", _head_response(ref, 3))
    res = d.put_if_absent(ref, b"abc")
    assert res.created is False
    assert len([c for c in client.calls if c[0] == "put_object"]) == 1


def test_error_output_does_not_leak_endpoint_or_credentials():
    """异常文本不含注入的 endpoint / 原始凭据消息。"""
    client = RecordingClient()
    d = _driver(client)
    client.add("head_object", ConnectTimeoutError(endpoint_url="https://secret-host:9000"))
    with pytest.raises(ArtifactStorageError) as ei:
        d.head(_ref())
    assert "secret-host" not in str(ei.value)
    assert "ConnectTimeout" not in str(ei.value)


def test_put_created_head_size_mismatch_fails_closed():
    """2xx 后 HEAD ContentLength != len(data) → IntegrityError，不返回 created=True。"""
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("put_object", {})
    client.add("head_object", _head_response(ref, 11))   # 上传 3 bytes，HEAD 谎报 11
    with pytest.raises(ArtifactIntegrityError):
        d.put_if_absent(ref, b"abc")


def test_put_created_head_stored_sha_mismatch_fails_closed():
    """2xx 后 HEAD pm-stored-sha256 是另一个合法 64-hex → IntegrityError。"""
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("put_object", {})
    client.add("head_object", _head_response(ref, 3, metadata=_meta(ref, "b" * 64)))
    with pytest.raises(ArtifactIntegrityError):
        d.put_if_absent(ref, b"abc")


def test_put_412_dedupe_allows_different_stored_size_and_digest():
    """412 去重路径允许实际 stored size/digest 与本次候选不同（跨 zstd level），返回 created=False。"""
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=3, stored_size=5, compression="zstd")
    client.add("put_object", _client_error("PreconditionFailed", 412))
    client.add("head_object", _head_response(ref, 3))   # 现存对象 stored=3（不同 level）
    res = d.put_if_absent(ref, b"abcde")
    assert res.created is False
    assert res.head.stored_size == 3


def test_get_range_missing_metadata_rejected():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    client.add("get_object", {
        "Body": _ClosingBytesIO(b"234"), "ContentLength": 3,
        "ContentRange": "bytes 2-4/10", "ResponseMetadata": {"HTTPStatusCode": 206}})
    with pytest.raises(ArtifactIntegrityError):
        d.get_range(ref, 2, 5)


def test_get_range_metadata_conflict_rejected():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    bad_meta = _meta(ref)
    bad_meta["pm-sha256"] = "b" * 64
    client.add("get_object", {
        "Body": _ClosingBytesIO(b"234"), "ContentLength": 3,
        "ContentRange": "bytes 2-4/10", "ResponseMetadata": {"HTTPStatusCode": 206},
        "Metadata": bad_meta})
    with pytest.raises(ArtifactIntegrityError):
        d.get_range(ref, 2, 5)


def test_get_range_content_length_missing_rejected():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    client.add("get_object", {
        "Body": _ClosingBytesIO(b"234"),
        "ContentRange": "bytes 2-4/10", "ResponseMetadata": {"HTTPStatusCode": 206},
        "Metadata": _meta(ref)})
    with pytest.raises(ArtifactIntegrityError):
        d.get_range(ref, 2, 5)


def test_get_range_content_length_non_numeric_rejected():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    client.add("get_object", {
        "Body": _ClosingBytesIO(b"234"), "ContentLength": "three",
        "ContentRange": "bytes 2-4/10", "ResponseMetadata": {"HTTPStatusCode": 206},
        "Metadata": _meta(ref)})
    with pytest.raises(ArtifactIntegrityError):
        d.get_range(ref, 2, 5)


def test_get_range_read_spy_reads_requested_plus_one():
    """[2,5) 的 read spy 精确记录 read(4)（requested+1），不读整对象。"""
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    body = _ReadSpy(b"234")
    client.add("get_object", {
        "Body": body, "ContentLength": 3,
        "ContentRange": "bytes 2-4/10", "ResponseMetadata": {"HTTPStatusCode": 206},
        "Metadata": _meta(ref)})
    got = d.get_range(ref, 2, 5)
    assert got == b"234"
    assert body.max_read == 4                         # requested(3) + 1，不是 stored_size+1


def test_get_range_extra_body_detected_and_closed():
    """Provider 返回超过请求长度的 body → IntegrityError，body 仍关闭。"""
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    body = _ClosingBytesIO(b"2345")                   # 返回 4 bytes，请求 3
    client.add("get_object", {
        "Body": body, "ContentLength": 3,
        "ContentRange": "bytes 2-4/10", "ResponseMetadata": {"HTTPStatusCode": 206},
        "Metadata": _meta(ref)})
    with pytest.raises(ArtifactIntegrityError):
        d.get_range(ref, 2, 5)
    assert body.closed_flag is True


@pytest.mark.parametrize("prefix", [
    "/", "/a", "a/", "a//b", "a/./b", "a/../b", "a/b//c", "a\\b",
    "a\x00b", "a\rb", "a\nb", "./a", "../a", ".", "..",
])
def test_direct_construction_invalid_prefix_rejected(prefix):
    with pytest.raises(ValueError):
        S3ArtifactDriver(client=RecordingClient(), bucket="b", prefix=prefix,
                         conditional_retry_limit=3)


def test_direct_construction_valid_prefixes_accepted():
    for prefix in ("", "a/b", "a/b/c"):
        d = S3ArtifactDriver(client=RecordingClient(), bucket="b", prefix=prefix,
                             conditional_retry_limit=3)
        assert d._prefix == prefix                   # 不 strip、不 normalize
    d = S3ArtifactDriver(client=RecordingClient(), bucket="b", prefix="a/b",
                         conditional_retry_limit=3)
    assert d._key("cas/v1/sha256/ab/cd/x.raw") == "a/b/cas/v1/sha256/ab/cd/x.raw"


# ---------------- R2：StreamingBody 异常 / 响应类型 / cause 抑制 ----------------

def test_get_body_read_transport_error_converged():
    """full GET 的 body.read 抛含 secret endpoint 的 ReadTimeoutError → 脱敏 StorageError，
    body 已 close，read limit 精确。"""
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=3, stored_size=3)
    body = _FailReadSpy(ReadTimeoutError(endpoint_url="https://user:TOPSECRET@host", error="boom"))
    client.add("get_object", {"Body": body, "ContentLength": 3,
                              "Metadata": _meta(ref, hashlib.sha256(b"abc").hexdigest())})
    with pytest.raises(ArtifactStorageError) as ei:
        d.get(ref)
    assert "TOPSECRET" not in str(ei.value)
    assert "secret-host" not in str(ei.value)
    assert body.closed_flag is True
    assert body.max_read == ref.stored_size + 1      # read limit 仍精确（stored_size+1）


def test_get_range_body_read_response_streaming_error_converged():
    """Range 的 body.read 抛 ResponseStreamingError → 脱敏 StorageError，body 已 close，
    read limit 精确（requested+1）。"""
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    body = _FailReadSpy(ResponseStreamingError(error="stream blew up"))
    client.add("get_object", {
        "Body": body, "ContentLength": 3,
        "ContentRange": "bytes 2-4/10", "ResponseMetadata": {"HTTPStatusCode": 206},
        "Metadata": _meta(ref)})
    with pytest.raises(ArtifactStorageError):
        d.get_range(ref, 2, 5)
    assert body.closed_flag is True
    assert body.max_read == 4                        # requested(3)+1，不是 stored_size+1


def test_put_http_client_error_head_reconcile():
    """基础 HTTPClientError（传输未决基类）→ PUT 只做一次 HEAD reconcile，不无条件 PUT。"""
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("put_object", HTTPClientError(endpoint_url="https://user:TOPSECRET@host", error="boom"))
    client.add("head_object", _head_response(ref, 3, metadata=_meta(ref, hashlib.sha256(b"abc").hexdigest())))
    res = d.put_if_absent(ref, b"abc")
    assert res.created is False
    assert len([c for c in client.calls if c[0] == "put_object"]) == 1
    assert client.calls[1][0] == "head_object"


@pytest.mark.parametrize("bad_checksum", [
    123,                                                       # int 非 str/bytes
    object(),                                                  # object
    "!!!invalid-alphabet!!!",                                  # 非法 base64 字符
    "YWJj",                                                    # 无 padding，decode 3 bytes 非 SHA-256 digest
    base64.b64encode(b"short").decode("ascii"),                # 合法 base64 但 5 bytes 非 digest
])
def test_get_checksum_sha256_anomalies_rejected(bad_checksum):
    client = RecordingClient()
    d = _driver(client)
    data = b"payload"
    ref = _ref(original_size=7, stored_size=7)
    client.add("get_object", {
        "Body": _ClosingBytesIO(data), "ContentLength": 7,
        "Metadata": _meta(ref, hashlib.sha256(data).hexdigest()),
        "ChecksumSHA256": bad_checksum})
    with pytest.raises(ArtifactIntegrityError):      # 不泄 TypeError
        d.get(ref)


@pytest.mark.parametrize("bad_len", [3.9, "3", True])
def test_head_content_length_type_rejected(bad_len):
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("head_object", _head_response(ref, bad_len))
    with pytest.raises(ArtifactIntegrityError):
        d.head(ref)


@pytest.mark.parametrize("bad_len", [3.9, "3", True])
def test_get_content_length_type_rejected(bad_len):
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=3, stored_size=3)
    client.add("get_object", {"Body": _ClosingBytesIO(b"abc"), "ContentLength": bad_len,
                              "Metadata": _meta(ref)})
    with pytest.raises(ArtifactIntegrityError):
        d.get(ref)


@pytest.mark.parametrize("bad_len", [3.9, "3", True])
def test_get_range_content_length_type_rejected(bad_len):
    client = RecordingClient()
    d = _driver(client)
    ref = _ref(original_size=10, stored_size=10)
    client.add("get_object", {
        "Body": _ClosingBytesIO(b"234"), "ContentLength": bad_len,
        "ContentRange": "bytes 2-4/10", "ResponseMetadata": {"HTTPStatusCode": 206},
        "Metadata": _meta(ref)})
    with pytest.raises(ArtifactIntegrityError):
        d.get_range(ref, 2, 5)


def test_head_stored_sha_non_string_rejected():
    """pm-stored-sha256 非字符串（int）→ IntegrityError，不泄 TypeError。"""
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    meta = _meta(ref)
    meta["pm-stored-sha256"] = 123
    client.add("head_object", _head_response(ref, 3, metadata=meta))
    with pytest.raises(ArtifactIntegrityError):
        d.head(ref)


def test_external_cause_suppressed_and_no_secret_in_traceback():
    """外部 Provider 异常的 cause 被抑制，完整 traceback 不含注入的 endpoint/secret。"""
    import traceback
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("head_object", ConnectTimeoutError(endpoint_url="https://user:TOPSECRET@host"))
    try:
        d.head(ref)
        raise AssertionError("expected ArtifactStorageError")
    except ArtifactStorageError as e:
        assert e.__cause__ is None                   # 外部 cause 被 from None 抑制
        assert e.__suppress_context__ is True        # traceback 不打印 cause 链
        tb = traceback.format_exc()
        assert "TOPSECRET" not in tb
        assert "user" not in tb
        assert "secret-host" not in tb


# ---------------- R3：PUT ClientError 三分支完整 traceback 脱敏 ----------------

def test_put_generic_client_error_traceback_redacted():
    """一般 ClientError（HTTP 500，message 含 secret endpoint）→ StorageError，
    完整 traceback 脱敏、cause 抑制、尝试次数 1。"""
    import traceback
    client = RecordingClient()
    d = _driver(client)
    client.add("put_object", _client_error(
        "InternalError", 500, message="failed at https://user:TOPSECRET@host"))
    try:
        d.put_if_absent(_ref(), b"abc")
        raise AssertionError("expected ArtifactStorageError")
    except ArtifactStorageError as e:
        assert e.__cause__ is None                   # from None 抑制外部 cause
        assert e.__suppress_context__ is True
        tb = traceback.format_exc()
        assert "TOPSECRET" not in tb
        assert "user" not in tb
        assert "secret-host" not in tb
    assert len([c for c in client.calls if c[0] == "put_object"]) == 1


def test_put_unsupported_client_error_traceback_redacted():
    """400/501 fail-closed ClientError（message 含 secret）→ StorageError，traceback 脱敏。"""
    import traceback
    client = RecordingClient()
    d = _driver(client)
    client.add("put_object", _client_error(
        "InvalidRequest", 400, message="checksum secret at https://user:TOPSECRET@host"))
    try:
        d.put_if_absent(_ref(), b"abc")
        raise AssertionError("expected ArtifactStorageError")
    except ArtifactStorageError as e:
        assert e.__cause__ is None
        assert e.__suppress_context__ is True
        tb = traceback.format_exc()
        assert "TOPSECRET" not in tb
        assert "user" not in tb
    assert len([c for c in client.calls if c[0] == "put_object"]) == 1


def test_put_409_exhaustion_traceback_redacted():
    """409 冲突耗尽（attempts=1，message 含 secret）→ StorageError，尝试次数不变，traceback 脱敏。"""
    import traceback
    client = RecordingClient()
    d = _driver(client, conditional_retry_limit=1)
    client.add("put_object", _client_error(
        "ConditionalRequestConflict", 409, message="conflict at https://user:TOPSECRET@host"))
    try:
        d.put_if_absent(_ref(), b"abc")
        raise AssertionError("expected ArtifactStorageError")
    except ArtifactStorageError as e:
        assert e.__cause__ is None
        assert e.__suppress_context__ is True
        tb = traceback.format_exc()
        assert "TOPSECRET" not in tb
        assert "user" not in tb
    assert len([c for c in client.calls if c[0] == "put_object"]) == 1   # attempts=1，首个 409 即耗尽


# ---------------- exists / health / aclose ----------------

def test_exists_true_and_false():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("head_object", _head_response(ref, 3))   # 有效 CAS 对象（完整元数据 + ContentLength）
    assert d.exists(ref) is True
    client.add("head_object", _client_error("NotFound", 404))
    assert d.exists(ref) is False


def test_exists_403_raises_not_false():
    client = RecordingClient()
    d = _driver(client)
    client.add("head_object", _client_error("AccessDenied", 403))
    with pytest.raises(ArtifactStorageError):
        d.exists(_ref())                           # 403 不得伪装成不存在


def test_exists_valid_404_and_head_once():
    """有效 CAS 对象 → True；404 → False；HEAD 各一次。"""
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("head_object", _head_response(ref, 3))
    assert d.exists(ref) is True
    assert len([c for c in client.calls if c[0] == "head_object"]) == 1
    client.add("head_object", _client_error("NotFound", 404))
    assert d.exists(ref) is False


def test_exists_metadata_missing_raises_integrity():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    client.add("head_object", {"ContentLength": 3, "ContentType": "text/plain"})  # 无五项 pm-*
    with pytest.raises(ArtifactIntegrityError):
        d.exists(ref)
    assert len([c for c in client.calls if c[0] == "head_object"]) == 1


def test_exists_metadata_conflict_raises_integrity():
    client = RecordingClient()
    d = _driver(client)
    ref = _ref()
    bad_meta = _meta(ref)
    bad_meta["pm-sha256"] = "b" * 64
    client.add("head_object", _head_response(ref, 3, metadata=bad_meta))
    with pytest.raises(ArtifactIntegrityError):
        d.exists(ref)
    assert len([c for c in client.calls if c[0] == "head_object"]) == 1


def test_exists_no_credentials_raises_storage_error():
    client = RecordingClient()
    d = _driver(client)
    client.add("head_object", NoCredentialsError())
    with pytest.raises(ArtifactStorageError):
        d.exists(_ref())


def test_health_head_bucket_only_and_redacted():
    client = RecordingClient()
    d = _driver(client)
    client.add("head_bucket", {})
    h = d.health()
    assert h.ok is True and h.driver == "s3"
    assert [c[0] for c in client.calls] == ["head_bucket"]     # 不写 probe 对象
    for bad in ("secret", "password", "AKIA", "@"):
        assert bad not in str(h.detail)


def test_health_failure_redacted():
    client = RecordingClient()
    d = _driver(client)
    client.add("head_bucket", _client_error("ServiceUnavailable", 503))
    h = d.health()
    assert h.ok is False
    assert "503" in str(h.detail)                  # 只带 status，不带凭据/endpoint


def test_aclose_idempotent():
    client = RecordingClient()
    d = _driver(client)
    d.aclose()
    d.aclose()
    assert client.close_calls == 1


def test_no_delete_list_presign_api():
    d = _driver(RecordingClient())
    for name in ("delete", "delete_object", "delete_objects", "list_objects",
                 "list_objects_v2", "generate_presigned_url", "put_object_acl"):
        assert not hasattr(d, name), f"S3 driver must not expose {name}"


# ---------------- Service 集成（内存类 S3） ----------------

def test_service_roundtrip_and_dedup_via_s3():
    service, _, s3 = _service(InMemoryS3())
    r = service.put_bytes(b"payload", "application/octet-stream", compression="none")
    assert r.storage_driver == "s3"
    assert s3.objects["v2-artifacts/" + r.locator]["Body"] == b"payload"
    assert service.get_bytes(r) == b"payload"
    # 去重：同内容再写 → 同一 locator、单对象
    r2 = service.put_bytes(b"payload", "application/octet-stream", compression="none")
    assert r2.locator == r.locator
    assert len(s3.objects) == 1


def test_service_different_mime_same_content_dedupes_via_s3():
    """同内容/codec 不同 MIME 再写仍命中已有对象（MIME 是引用注解，不是身份）。"""
    service, _, s3 = _service(InMemoryS3())
    a = service.put_bytes(b"hello", "text/plain", compression="none")
    b = service.put_bytes(b"hello", "application/x-custom", compression="none")
    assert a.locator == b.locator
    assert len(s3.objects) == 1
    assert service.get_bytes(b) == b"hello"


def test_service_cross_level_dedup_via_s3():
    """同一原文 level 1 → level 22：第二次成功去重，stored_size 由实际 HEAD 收敛为同一值。"""
    data = b"A" * 100000
    s3 = InMemoryS3()
    s1, _, _ = _service(s3, ARTIFACT_ZSTD_LEVEL=1)
    s2, _, _ = _service(s3, ARTIFACT_ZSTD_LEVEL=22)
    r1 = s1.put_bytes(data, "text/plain", compression="zstd")
    r2 = s2.put_bytes(data, "text/plain", compression="zstd")
    assert r1.locator == r2.locator
    assert r1.stored_size == r2.stored_size
    assert len(s3.objects) == 1
    assert s1.get_bytes(r1) == data
    assert s2.get_bytes(r2) == data


def test_service_zstd_roundtrip_and_range_via_s3():
    service, _, s3 = _service(InMemoryS3())
    raw = service.put_bytes(b"0123456789", "text/plain", compression="none")
    assert service.get_range(raw, 2, 5) == b"234"  # Service 只向 raw 暴露原文 range
    z = service.put_bytes(b"y" * 5000, "text/plain", compression="zstd")
    assert service.get_bytes(z) == b"y" * 5000
