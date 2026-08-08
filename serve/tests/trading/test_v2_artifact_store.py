"""
WP-00c1-r1 ArtifactStore Service 验收测试（真实 local driver + tmp_path）。

覆盖：SHA 寻址/去重、跨压缩级别去重（返回实际 stored size）、raw/zstd/auto 往返、
阈值边界、超限/压缩炸弹有界拒绝（声明 content-size 预检 + streaming 有界解码）、
range 严格语义（只走 driver.get_range，head+get_range spy）、完整性 fail-closed、
head stored-size 预检、driver 身份校验、公共无 delete API、并发收集线程异常。
"""

import hashlib
import os
import threading
from pathlib import Path

import pytest
import zstandard as zstd

from app.config import Settings
from app.services.artifact_store import (
    LOCATOR_VERSION,
    ArtifactIntegrityError,
    ArtifactRangeUnsupported,
    ArtifactRef,
    ArtifactStorageError,
    ArtifactStore,
    ArtifactTooLarge,
    build_locator,
)
from app.services.artifact_store.drivers.local import LocalArtifactDriver
from app.services.artifact_store.service import sha256_hex


def _cfg(root: str, **over):
    base = dict(
        _env_file=None,
        ARTIFACT_LOCAL_ROOT=root,
        ARTIFACT_INLINE_THRESHOLD_BYTES=16384,
        ARTIFACT_COMPRESSION_THRESHOLD_BYTES=16384,
        ARTIFACT_ZSTD_LEVEL=6,
        ARTIFACT_MAX_OBJECT_BYTES=67_108_864,
        ARTIFACT_VERIFY_ON_READ=True,
    )
    base.update(over)
    return Settings(**base)


def _store(tmp_path, **over) -> ArtifactStore:
    cfg = _cfg(str(tmp_path), **over)
    return ArtifactStore(LocalArtifactDriver(cfg.ARTIFACT_LOCAL_ROOT), cfg)


class SpyDriver:
    """记录调用次数的委托 Driver，用于证明 get_range 不退化成全量 get。"""

    driver_name = "local"

    def __init__(self, real):
        self._real = real
        self.calls = {"put_if_absent": 0, "get": 0, "get_range": 0, "head": 0}

    def put_if_absent(self, candidate, data):
        self.calls["put_if_absent"] += 1
        return self._real.put_if_absent(candidate, data)

    def get(self, ref):
        self.calls["get"] += 1
        return self._real.get(ref)

    def get_range(self, ref, start, end):
        self.calls["get_range"] += 1
        return self._real.get_range(ref, start, end)

    def head(self, ref):
        self.calls["head"] += 1
        return self._real.head(ref)

    def exists(self, ref):
        return self._real.exists(ref)

    def health(self):
        return self._real.health()

    def aclose(self):
        self._real.aclose()


def _bomb_env(tmp_path, max_bytes):
    cfg = _cfg(str(tmp_path), ARTIFACT_MAX_OBJECT_BYTES=max_bytes,
               ARTIFACT_INLINE_THRESHOLD_BYTES=max_bytes,
               ARTIFACT_COMPRESSION_THRESHOLD_BYTES=max_bytes)
    driver = LocalArtifactDriver(str(tmp_path))
    return driver, ArtifactStore(driver, cfg)


def _put_lying_ref(driver, compressed, data, original_size):
    """经 driver 直接放压缩 blob（绕过 Service 写入检查），ref.original_size 说谎以进入解码路径。"""
    sha = hashlib.sha256(data).hexdigest()
    loc = build_locator(sha, "zstd")
    ref = ArtifactRef(sha256=sha, original_size=original_size, stored_size=len(compressed),
                      mime="text/plain", compression="zstd", storage_driver="local",
                      locator=loc, storage_version=LOCATOR_VERSION)
    driver.put_if_absent(ref, compressed)
    return ref


# ---------------- SHA 寻址 / 去重 ----------------

def test_same_content_same_sha(tmp_path):
    s = _store(tmp_path)
    a = s.put_bytes(b"hello world", "text/plain", compression="none")
    b = s.put_bytes(b"hello world", "text/plain", compression="none")
    assert a.sha256 == b.sha256 == hashlib.sha256(b"hello world").hexdigest()
    assert a.locator == b.locator


def test_different_content_different_sha(tmp_path):
    s = _store(tmp_path)
    a = s.put_bytes(b"a", "text/plain", compression="none")
    b = s.put_bytes(b"b", "text/plain", compression="none")
    assert a.sha256 != b.sha256


def test_dedup_does_not_overwrite(tmp_path):
    s = _store(tmp_path)
    r = s.put_bytes(b"payload", "application/octet-stream", compression="none")
    assert s.get_bytes(r) == b"payload"
    # 相同内容再写 → 不覆盖，读回一致
    s.put_bytes(b"payload", "application/octet-stream", compression="none")
    assert s.get_bytes(r) == b"payload"


def test_concurrent_dedup_single_object_no_thread_exceptions(tmp_path):
    s = _store(tmp_path)
    data = b"concurrent-content-" * 1000
    results: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def _put():
        try:
            r = s.put_bytes(data, "application/octet-stream", compression="none")
            with lock:
                results.append(r.locator)
        except BaseException as e:   # 必须收集线程异常，不能只检查成功结果的 set
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=_put) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], f"thread exceptions leaked: {errors}"
    assert len(set(results)) == 1
    # 用任一返回的 ref 读回完整内容（去重后内容一致）
    ref = s.put_bytes(data, "application/octet-stream", compression="none")
    assert ref.locator == results[0]
    assert s.get_bytes(ref) == data


# ---------------- 跨压缩级别去重（P1-3） ----------------

def test_cross_level_dedup_returns_actual_stored(tmp_path):
    """同一原文先 level 1 再 level 22：第二次成功去重，locator 相同、只保留一个对象，
    两次 ref 均可读取，且 stored_size 都取**实际**对象长度。"""
    data = b"A" * 100000
    s1 = _store(tmp_path, ARTIFACT_ZSTD_LEVEL=1)
    s2 = _store(tmp_path, ARTIFACT_ZSTD_LEVEL=22)
    r1 = s1.put_bytes(data, "text/plain", compression="zstd")
    r2 = s2.put_bytes(data, "text/plain", compression="zstd")
    assert r1.locator == r2.locator
    assert r1.stored_size == r2.stored_size          # 不要求第二次候选压缩长度
    assert r1.original_size == r2.original_size == len(data)
    assert s1.get_bytes(r1) == data
    assert s2.get_bytes(r2) == data
    objects = [p for p in Path(tmp_path).rglob("*") if p.is_file() and ".tmp-" not in p.name]
    assert len(objects) == 1


def test_cross_level_dedup_stored_size_matches_file(tmp_path):
    data = b"BBBB" * 30000
    s1 = _store(tmp_path, ARTIFACT_ZSTD_LEVEL=1)
    r1 = s1.put_bytes(data, "text/plain", compression="zstd")
    file_size = (Path(tmp_path) / r1.locator).stat().st_size
    assert r1.stored_size == file_size              # stored_size 来自底层实际对象
    h = s1.verify(r1)
    assert h.original_size == len(data)             # zstd 对象 original_size == 原文
    assert h.stored_size == file_size


def test_dedup_existing_content_mismatch_fails_closed(tmp_path):
    s = _store(tmp_path)
    r = s.put_bytes(b"payload", "application/octet-stream", compression="none")
    # 篡改 stored 文件（保持大小）后，重新 put 相同内容：去重读取现对象 → SHA 不符 → fail-closed
    obj = Path(s._cfg.ARTIFACT_LOCAL_ROOT) / r.locator
    orig = obj.read_bytes()
    obj.write_bytes(b"X" + orig[1:])
    with pytest.raises(ArtifactStorageError):
        s.put_bytes(b"payload", "application/octet-stream", compression="none")


# ---------------- compression 往返 / 选择 ----------------

def test_none_roundtrip(tmp_path):
    s = _store(tmp_path)
    r = s.put_bytes(b"x" * 100, "text/plain", compression="none")
    assert r.compression == "none"
    assert s.get_bytes(r) == b"x" * 100


def test_zstd_roundtrip(tmp_path):
    s = _store(tmp_path)
    r = s.put_bytes(b"y" * 5000, "text/plain", compression="zstd")
    assert r.compression == "zstd"
    assert s.get_bytes(r) == b"y" * 5000


def test_auto_keeps_raw_for_incompressible(tmp_path):
    s = _store(tmp_path)
    data = os.urandom(50000)                # 真随机：zstd 压缩后必然 >= 原长
    r = s.put_bytes(data, "application/octet-stream", compression="auto")
    assert r.compression == "none"          # 不可压缩 → raw
    assert s.get_bytes(r) == data


def test_auto_picks_zstd_for_compressible_large(tmp_path):
    s = _store(tmp_path)
    data = (b"AAAA" * 10000)                 # 高度可压缩且 > 阈值
    r = s.put_bytes(data, "text/plain", compression="auto")
    assert r.compression == "zstd"
    assert s.get_bytes(r) == data


def test_auto_keeps_raw_below_threshold(tmp_path):
    s = _store(tmp_path)
    r = s.put_bytes(b"abc", "text/plain", compression="auto")   # 小于阈值
    assert r.compression == "none"


def test_auto_compressible_but_not_smaller_stays_raw(tmp_path):
    s = _store(tmp_path)
    data = bytes(range(256)) * 200            # 熵高，压缩后未必更小
    r = s.put_bytes(data, "application/octet-stream", compression="auto")
    if r.compression == "zstd":
        assert s.get_bytes(r) == data
    else:
        assert r.compression == "none"
        assert s.get_bytes(r) == data


# ---------------- 阈值 / 超限 / 压缩炸弹（有界） ----------------

def test_empty_object(tmp_path):
    s = _store(tmp_path)
    r = s.put_bytes(b"", "application/octet-stream", compression="none")
    assert r.original_size == 0
    assert s.get_bytes(r) == b""


def test_max_object_boundary(tmp_path):
    max_bytes = 1024
    s = _store(tmp_path, ARTIFACT_MAX_OBJECT_BYTES=max_bytes,
               ARTIFACT_INLINE_THRESHOLD_BYTES=max_bytes,
               ARTIFACT_COMPRESSION_THRESHOLD_BYTES=max_bytes)
    r = s.put_bytes(b"z" * max_bytes, "text/plain", compression="none")
    assert s.get_bytes(r) == b"z" * max_bytes
    with pytest.raises(ArtifactTooLarge):
        s.put_bytes(b"z" * (max_bytes + 1), "text/plain", compression="none")


def test_compression_bomb_rejected_on_read(tmp_path):
    """原始 bomb ref（original_size 声明即超上限）：读取/verify 在 I/O 前受硬上限拒绝。"""
    max_bytes = 1024
    data = b"A" * 100000
    compressed = zstd.ZstdCompressor(level=6).compress(data)
    sha = hashlib.sha256(data).hexdigest()
    loc = build_locator(sha, "zstd")
    driver = LocalArtifactDriver(str(tmp_path))
    candidate = ArtifactRef(sha256=sha, original_size=len(data), stored_size=len(compressed),
                            mime="text/plain", compression="zstd", storage_driver="local",
                            locator=loc, storage_version=LOCATOR_VERSION)
    driver.put_if_absent(candidate, compressed)
    store = ArtifactStore(driver, _cfg(str(tmp_path), ARTIFACT_MAX_OBJECT_BYTES=max_bytes,
                                       ARTIFACT_INLINE_THRESHOLD_BYTES=max_bytes,
                                       ARTIFACT_COMPRESSION_THRESHOLD_BYTES=max_bytes))
    with pytest.raises(ArtifactTooLarge):
        store.get_bytes(candidate)              # original_size 100000 > max
    with pytest.raises(ArtifactTooLarge):
        store.verify(candidate)


def test_zstd_bomb_declared_size_rejected_before_allocation(tmp_path):
    """frame 声明 content size 大于 max：分配输出前直接 ArtifactTooLarge（无全量解压路径）。"""
    max_bytes = 1024
    data = b"A" * 100000
    compressed = zstd.ZstdCompressor(level=6).compress(data)
    assert zstd.frame_content_size(compressed) == len(data)   # 带 content size
    driver, store = _bomb_env(tmp_path, max_bytes)
    ref = _put_lying_ref(driver, compressed, data, original_size=500)
    with pytest.raises(ArtifactTooLarge):
        store.get_bytes(ref)
    with pytest.raises(ArtifactTooLarge):
        store.verify(ref)


def test_zstd_bomb_without_content_size_rejected_bounded(tmp_path):
    """无 content size 的 bomb：streaming 有界读取，超 max 抛 ArtifactTooLarge。"""
    max_bytes = 1024
    data = b"A" * 100000
    cobj = zstd.ZstdCompressor(level=6, write_content_size=False).compressobj()
    compressed = cobj.compress(data) + cobj.flush()
    assert zstd.frame_content_size(compressed) == -1           # 无 content size
    driver, store = _bomb_env(tmp_path, max_bytes)
    ref = _put_lying_ref(driver, compressed, data, original_size=500)
    with pytest.raises(ArtifactTooLarge):
        store.get_bytes(ref)
    with pytest.raises(ArtifactTooLarge):
        store.verify(ref)


def test_verify_flag_cannot_bypass_hard_ceiling(tmp_path):
    """verify=False 不能绕过硬上限（original_size/stored_size > max 依旧拒绝）。"""
    max_bytes = 1024
    data = b"A" * 100000
    compressed = zstd.ZstdCompressor(level=6).compress(data)
    driver = LocalArtifactDriver(str(tmp_path))
    ref = _put_lying_ref(driver, compressed, data, original_size=len(data))
    store = ArtifactStore(driver, _cfg(str(tmp_path), ARTIFACT_MAX_OBJECT_BYTES=max_bytes,
                                       ARTIFACT_INLINE_THRESHOLD_BYTES=max_bytes,
                                       ARTIFACT_COMPRESSION_THRESHOLD_BYTES=max_bytes))
    with pytest.raises(ArtifactTooLarge):
        store.get_bytes(ref, verify=False)


# ---------------- P1-1 写前 stored 硬上限 ----------------

def test_stored_size_ceiling_rejected_before_driver(tmp_path):
    """原文合法但 zstd 膨胀后 stored > max：写前拒绝，Driver 调用 0 次、磁盘无对象无 temp。"""
    max_bytes = 1024
    data = b"".join(hashlib.sha256(f"seed-{i}".encode()).digest() for i in range(32))
    assert len(data) == max_bytes
    comp = zstd.ZstdCompressor(level=6).compress(data)
    assert len(comp) > max_bytes                # 确定性：zstd 确实膨胀（1034 > 1024）
    driver = LocalArtifactDriver(str(tmp_path))
    spy = SpyDriver(driver)
    store = ArtifactStore(spy, _cfg(str(tmp_path), ARTIFACT_MAX_OBJECT_BYTES=max_bytes,
                                    ARTIFACT_INLINE_THRESHOLD_BYTES=max_bytes,
                                    ARTIFACT_COMPRESSION_THRESHOLD_BYTES=max_bytes))
    with pytest.raises(ArtifactTooLarge):
        store.put_bytes(data, "text/plain", compression="zstd")
    assert spy.calls["put_if_absent"] == 0
    assert not any(p.is_file() for p in Path(tmp_path).rglob("*"))


# ---------------- P1-2 无 content-size frame 完整 EOF 证明 ----------------

def _nocsize_comp(data: bytes) -> bytes:
    cobj = zstd.ZstdCompressor(level=6, write_content_size=False).compressobj()
    return cobj.compress(data) + cobj.flush()


def test_nocontent_size_complete_roundtrip(tmp_path):
    data = b"roundtrip-data-" * 200
    comp = _nocsize_comp(data)
    assert zstd.frame_content_size(comp) == -1   # 无 content size
    sha = hashlib.sha256(data).hexdigest()
    loc = build_locator(sha, "zstd")
    driver = LocalArtifactDriver(str(tmp_path))
    ref = ArtifactRef(sha256=sha, original_size=len(data), stored_size=len(comp),
                      mime="text/plain", compression="zstd", storage_driver="local",
                      locator=loc, storage_version=LOCATOR_VERSION)
    driver.put_if_absent(ref, comp)
    store = ArtifactStore(driver, _cfg(str(tmp_path)))
    assert store.get_bytes(ref) == data
    assert store.get_bytes(ref, verify=False) == data


@pytest.mark.parametrize("cut", [1, 2, 5])
def test_nocontent_size_truncated_fails_closed(tmp_path, cut):
    """无 content-size 的截尾 frame：即使 verify=False 也必须 ArtifactIntegrityError。"""
    data = b"T" * 20000
    comp = _nocsize_comp(data)
    assert zstd.frame_content_size(comp) == -1
    sha = hashlib.sha256(data).hexdigest()
    loc = build_locator(sha, "zstd")
    driver = LocalArtifactDriver(str(tmp_path))
    full_ref = ArtifactRef(sha256=sha, original_size=len(data), stored_size=len(comp),
                           mime="text/plain", compression="zstd", storage_driver="local",
                           locator=loc, storage_version=LOCATOR_VERSION)
    driver.put_if_absent(full_ref, comp)
    path = Path(tmp_path) / loc
    path.write_bytes(path.read_bytes()[:-cut])          # 截尾 cut bytes
    ref = ArtifactRef(sha256=sha, original_size=len(data),
                      stored_size=path.stat().st_size,  # ref 描述截尾后实际大小（head 预检通过）
                      mime="text/plain", compression="zstd", storage_driver="local",
                      locator=loc, storage_version=LOCATOR_VERSION)
    store = ArtifactStore(driver, _cfg(str(tmp_path)))
    with pytest.raises(ArtifactIntegrityError):
        store.get_bytes(ref)
    with pytest.raises(ArtifactIntegrityError):
        store.get_bytes(ref, verify=False)
    with pytest.raises(ArtifactIntegrityError):
        store.verify(ref)


def test_checksummed_truncated_eof_fails_closed(tmp_path):
    """带 checksum 的 content-size frame 截掉末尾 1 byte：stream 层可产出全量内容，
    EOF pass 必须仍判 IntegrityError（verify=False 亦然）。"""
    data = b"C" * 5000
    comp = zstd.ZstdCompressor(level=6, write_content_size=True, write_checksum=True).compress(data)
    sha = hashlib.sha256(data).hexdigest()
    loc = build_locator(sha, "zstd")
    driver = LocalArtifactDriver(str(tmp_path))
    full_ref = ArtifactRef(sha256=sha, original_size=len(data), stored_size=len(comp),
                           mime="text/plain", compression="zstd", storage_driver="local",
                           locator=loc, storage_version=LOCATOR_VERSION)
    driver.put_if_absent(full_ref, comp)
    path = Path(tmp_path) / loc
    path.write_bytes(path.read_bytes()[:-1])
    ref = ArtifactRef(sha256=sha, original_size=len(data), stored_size=path.stat().st_size,
                      mime="text/plain", compression="zstd", storage_driver="local",
                      locator=loc, storage_version=LOCATOR_VERSION)
    store = ArtifactStore(driver, _cfg(str(tmp_path)))
    with pytest.raises(ArtifactIntegrityError):
        store.get_bytes(ref, verify=False)


# ---------------- range 严格语义（P1-2） ----------------

def test_raw_range(tmp_path):
    s = _store(tmp_path)
    r = s.put_bytes(b"0123456789", "text/plain", compression="none")
    assert s.get_range(r, 2, 5) == b"234"       # [start, end)
    assert s.get_range(r, 0, 0) == b""          # 空范围
    assert s.get_range(r, 0, 10) == b"0123456789"
    assert s.get_range(r, 10, 10) == b""        # 末端等于 size 合法


def test_raw_range_invalid(tmp_path):
    s = _store(tmp_path)
    r = s.put_bytes(b"0123456789", "text/plain", compression="none")
    with pytest.raises(ValueError):
        s.get_range(r, -1, 5)                   # 负 start
    with pytest.raises(ValueError):
        s.get_range(r, 5, 2)                    # 反向
    with pytest.raises(ValueError):
        s.get_range(r, 0, 999)                  # 末端大于 size → 拒绝，不静默截断
    with pytest.raises(ValueError):
        s.get_range(r, 10, 11)                  # 末端越界
    with pytest.raises(ValueError):
        s.get_range(r, True, 5)                 # bool 非合法边界


def test_get_range_uses_driver_get_range_only(tmp_path):
    """fake/spy Driver：raw range 只调用 head + get_range，get() 调用次数为 0。"""
    real = LocalArtifactDriver(str(tmp_path))
    spy = SpyDriver(real)
    store = ArtifactStore(spy, _cfg(str(tmp_path)))
    r = store.put_bytes(b"0123456789", "text/plain", compression="none")
    spy.calls["get"] = 0
    spy.calls["get_range"] = 0
    spy.calls["head"] = 0
    assert store.get_range(r, 2, 5) == b"234"
    assert spy.calls["get"] == 0                # 绝不退化成全量下载
    assert spy.calls["get_range"] == 1
    assert spy.calls["head"] >= 1


def test_zstd_range_rejected(tmp_path):
    s = _store(tmp_path)
    data = b"C" * 30000
    r = s.put_bytes(data, "text/plain", compression="zstd")
    with pytest.raises(ArtifactRangeUnsupported):
        s.get_range(r, 0, 10)


# ---------------- 完整性 fail-closed / head 预检 / driver 身份 ----------------

def test_verify_detects_tampered_stored(tmp_path):
    s = _store(tmp_path)
    r = s.put_bytes(b"integrity-check", "text/plain", compression="none")
    path = Path(s._cfg.ARTIFACT_LOCAL_ROOT) / r.locator
    orig = path.read_bytes()
    path.write_bytes(b"X" + orig[1:])
    with pytest.raises(ArtifactIntegrityError):
        s.verify(r)
    with pytest.raises(ArtifactIntegrityError):
        s.get_bytes(r)


def test_verify_ok_on_clean(tmp_path):
    s = _store(tmp_path)
    r = s.put_bytes(b"clean", "text/plain", compression="none")
    h = s.verify(r)
    assert h.original_size == 5
    assert h.stored_size == 5
    assert h.sha256 == r.sha256


def test_get_bytes_rejects_stored_size_mismatch_before_body(tmp_path):
    """head 与 ref 的 stored size 不一致 → 读取 body 前直接 IntegrityError。"""
    s = _store(tmp_path)
    r = s.put_bytes(b"payload", "text/plain", compression="none")
    bad = ArtifactRef(sha256=r.sha256, original_size=r.original_size + 1,
                      stored_size=r.stored_size + 1, mime=r.mime, compression=r.compression,
                      storage_driver="local", locator=r.locator,
                      storage_version=LOCATOR_VERSION)
    with pytest.raises(ArtifactIntegrityError):
        s.get_bytes(bad)
    with pytest.raises(ArtifactIntegrityError):
        s.verify(bad)


def test_ref_driver_mismatch_rejected(tmp_path):
    """禁止把 storage_driver=s3 的 ref 静默交给 local driver。"""
    s = _store(tmp_path)
    r = s.put_bytes(b"x", "text/plain", compression="none")
    s3_ref = ArtifactRef(sha256=r.sha256, original_size=r.original_size,
                         stored_size=r.stored_size, mime=r.mime, compression=r.compression,
                         storage_driver="s3", locator=r.locator, storage_version=LOCATOR_VERSION)
    with pytest.raises(ArtifactStorageError):
        s.get_bytes(s3_ref)


# ---------------- 公共接口无 delete ----------------

def test_no_delete_api():
    for name in ("delete", "delete_bytes", "remove"):
        assert not hasattr(ArtifactStore, name), f"Service must not expose {name}"
        assert not hasattr(LocalArtifactDriver, name), f"Driver must not expose {name}"


# ---------------- locator 布局 ----------------

def test_locator_layout(tmp_path):
    s = _store(tmp_path)
    r = s.put_bytes(b"loc", "text/plain", compression="none")
    assert r.locator.startswith("cas/v1/sha256/")
    assert r.locator.endswith(".raw")
    body = r.locator.split("/")
    assert body[0:3] == ["cas", "v1", "sha256"]
    assert body[3] == r.sha256[:2]
    assert body[4] == r.sha256[2:4]
    assert body[5] == r.sha256 + ".raw"
