"""
WP-00c1-r1 Artifact Driver 合同验收测试（纯合同，无存储）。

覆盖：ArtifactRef frozen + 全字段运行时校验（sha/size/mime/compression/driver/version/
规范 locator/raw 尺寸相等）、ArtifactHead/PutResult frozen 与语义、ArtifactDriver
Protocol 结构（put_if_absent(candidate, data) 单签名）、受控异常层次、build_locator。
"""

import inspect

import pytest

from app.services.artifact_store.contracts import (
    LOCATOR_VERSION,
    ArtifactDriver,
    ArtifactError,
    ArtifactHead,
    ArtifactIntegrityError,
    ArtifactNotFound,
    ArtifactPathError,
    ArtifactRangeUnsupported,
    ArtifactRef,
    ArtifactStorageError,
    ArtifactTooLarge,
    PutResult,
    build_locator,
)

SHA = "a" * 64
SHORT_SHA = "abcd" + "a" * 60  # 前缀 ab/cd 的合法小写 hex


def _ref(**overrides):
    sha = overrides.get("sha256", SHA)
    compression = overrides.get("compression", "none")
    base = dict(
        sha256=sha,
        original_size=3,
        stored_size=3,
        mime="application/octet-stream",
        compression=compression,
        storage_driver="local",
        locator=build_locator(sha, compression),
        storage_version=LOCATOR_VERSION,
    )
    base.update(overrides)
    return ArtifactRef(**base)


# ---------------- frozen + 基础字段校验 ----------------

def test_ref_is_frozen():
    r = _ref()
    with pytest.raises(Exception):
        r.sha256 = "b" * 64   # frozen：不可变


def test_ref_validates_sha():
    with pytest.raises(ValueError):
        _ref(sha256="ABC")                      # 非 hex
    with pytest.raises(ValueError):
        _ref(sha256="B" * 64)                   # 大写非小写 hex
    with pytest.raises(ValueError):
        _ref(sha256="a" * 63)                   # 长度不对
    _ref(sha256="b" * 64)                       # 小写 hex 合法（locator 自动规范）


def test_ref_validates_sizes_are_non_bool_ints():
    with pytest.raises(ValueError):
        _ref(original_size=-1)
    with pytest.raises(ValueError):
        _ref(stored_size=-5)
    with pytest.raises(ValueError):
        _ref(original_size=True)                # bool 不是合法 size
    with pytest.raises(ValueError):
        _ref(stored_size="3")                   # str 不是合法 size


def test_ref_validates_mime():
    with pytest.raises(ValueError):
        _ref(mime="")                           # 空
    with pytest.raises(ValueError):
        _ref(mime="a\rb")                       # CR
    with pytest.raises(ValueError):
        _ref(mime="a\nb")                       # LF
    with pytest.raises(ValueError):
        _ref(mime="a\x00b")                     # NUL
    _ref(mime="text/plain; charset=utf-8")      # 合法


def test_ref_validates_compression():
    with pytest.raises(ValueError):
        _ref(compression="gzip")
    _ref(compression="zstd")


def test_ref_validates_storage_driver():
    with pytest.raises(ValueError):
        _ref(storage_driver="gcs")              # 白名单外
    with pytest.raises(ValueError):
        _ref(storage_driver="")
    _ref(storage_driver="s3")                   # s3 允许（driver 未实现属于 00c2）


def test_ref_validates_storage_version():
    with pytest.raises(ValueError):
        _ref(storage_version="cas/v2")
    with pytest.raises(ValueError):
        _ref(storage_version="v2")


def test_ref_validates_raw_size_equal():
    # raw 对象必须 original_size == stored_size
    with pytest.raises(ValueError):
        _ref(compression="none", original_size=4, stored_size=3)
    # zstd 允许两者不等（压缩体小于原文）
    r = _ref(compression="zstd", original_size=5000, stored_size=100)
    assert r.original_size == 5000 and r.stored_size == 100


# ---------------- 规范 locator ----------------

def test_ref_valid_locator_accepted():
    sha = SHORT_SHA
    r = _ref(sha256=sha, locator=build_locator(sha, "none"))
    assert r.locator == f"cas/v1/sha256/ab/cd/{sha}.raw"
    rz = _ref(sha256=sha, compression="zstd", original_size=5, stored_size=3,
              locator=build_locator(sha, "zstd"))
    assert rz.locator == f"cas/v1/sha256/ab/cd/{sha}.zst"


def test_ref_rejects_noncanonical_locator():
    # 所有非规范形式都必须拒绝（ArtifactPathError）；sha 前缀为 ab/cd
    sha = SHORT_SHA
    canonical = build_locator(sha, "none")
    cases = [
        "/abs/path",                       # 绝对
        "a/../b",                          # ..
        "a\x00b",                          # NUL
        "a:b/c",                           # 冒号
        "a b/c",                           # 空格
        "a\\b",                            # 反斜杠
        "./" + canonical,                  # ./ 前缀别名
        canonical + "/",                   # 尾部斜杠
        "cas/v1/sha256/aa/aa/" + sha + ".raw",   # 目录段与 sha 前缀（ab/cd）不符
        canonical.replace(".raw", ".zst"),       # 错误 suffix（raw 对象）
        "cas/v1/sha256/ab/cd/" + sha + ".raw.gz",  # 错误后缀扩展
    ]
    for loc in cases:
        with pytest.raises(ArtifactPathError):
            _ref(sha256=sha, locator=loc)
    # zstd 对象错误 suffix（.raw）同样拒绝
    with pytest.raises(ArtifactPathError):
        _ref(sha256=sha, compression="zstd", original_size=5, stored_size=3,
             locator=build_locator(sha, "none"))


def test_build_locator_canonical():
    assert build_locator(SHORT_SHA, "none") == f"cas/v1/sha256/ab/cd/{SHORT_SHA}.raw"
    assert build_locator(SHORT_SHA, "zstd") == f"cas/v1/sha256/ab/cd/{SHORT_SHA}.zst"
    with pytest.raises(ValueError):
        build_locator("XYZ", "none")
    with pytest.raises(ValueError):
        build_locator(SHORT_SHA, "gzip")


# ---------------- ArtifactHead / PutResult ----------------

def test_head_frozen_and_validates():
    def _h(**over):
        base = dict(sha256=SHA, original_size=3, stored_size=3, mime="x",
                    compression="none", storage_version=LOCATOR_VERSION)
        base.update(over)
        return ArtifactHead(**base)

    h = _h()
    with pytest.raises(Exception):
        h.sha256 = "b" * 64
    with pytest.raises(ValueError):
        _h(original_size=-1)
    with pytest.raises(ValueError):
        _h(stored_size=True)
    with pytest.raises(ValueError):
        _h(mime="a\nb")
    with pytest.raises(ValueError):
        _h(storage_version="cas/v2")
    # zstd 语义：original_size 是对象元数据，可以大于 stored_size
    hz = _h(compression="zstd", original_size=5000, stored_size=100)
    assert hz.original_size == 5000 and hz.stored_size == 100


def test_put_result_frozen():
    h = ArtifactHead(sha256=SHA, original_size=3, stored_size=3, mime="x",
                     compression="none", storage_version=LOCATOR_VERSION)
    p = PutResult(created=True, head=h)
    with pytest.raises(Exception):
        p.created = False


# ---------------- Protocol 结构 ----------------

def test_artifact_driver_protocol_signature():
    methods = ["put_if_absent", "get", "get_range", "head", "exists", "health", "aclose"]
    for m in methods:
        assert hasattr(ArtifactDriver, m)
    # put_if_absent 必须是 (candidate, data) 单签名，禁止两套接口
    sig = inspect.signature(ArtifactDriver.put_if_absent)
    assert list(sig.parameters) == ["self", "candidate", "data"]


def test_artifact_driver_runtime_checkable():
    class Good:
        driver_name = "local"
        def put_if_absent(self, candidate, data): ...
        def get(self, ref): ...
        def get_range(self, ref, start, end): ...
        def head(self, ref): ...
        def exists(self, ref): ...
        def health(self): ...
        def aclose(self): ...

    # runtime_checkable 校验方法存在性；签名一致性由 test_artifact_driver_protocol_signature 锁定
    assert isinstance(Good(), ArtifactDriver)


def test_artifact_driver_protocol_driver_name():
    assert "driver_name" in ArtifactDriver.__annotations__


# ---------------- 受控异常层次 ----------------

def test_exception_hierarchy():
    for exc in (ArtifactNotFound, ArtifactTooLarge, ArtifactIntegrityError,
                ArtifactRangeUnsupported, ArtifactPathError, ArtifactStorageError):
        assert issubclass(exc, ArtifactError)
    assert issubclass(ArtifactError, Exception)


def test_exceptions_are_distinct():
    kinds = {ArtifactNotFound, ArtifactTooLarge, ArtifactIntegrityError,
             ArtifactRangeUnsupported, ArtifactPathError, ArtifactStorageError}
    assert len(kinds) == 6
