"""
WP-00c1-r1 Local Artifact Driver 验收测试（真实文件系统 + tmp_path）。

覆盖：路径安全（防御纵深 _resolve_locator + contract 规范 locator）、原子 no-replace 发布、
三类故障注入（file fsync / os.link / directory fsync）无脏对象无临时文件、dir fsync 失败后
target 保留且重试收敛、并发 put 收集线程异常、有界 get（声明长度不符拒绝）、head 语义
（stored 取实际、original 取元数据）、range 范围校验、health 唯一 probe、aclose 幂等、
无 delete。
"""

import os
import stat
import threading
import zstandard as zstd
from pathlib import Path

import pytest

from app.services.artifact_store import (
    ArtifactHead,
    ArtifactIntegrityError,
    ArtifactNotFound,
    ArtifactPathError,
    ArtifactRef,
    ArtifactStorageError,
)
from app.services.artifact_store.drivers.local import LocalArtifactDriver


def _sha() -> str:
    return "b" * 64


def _loc(sha: str, compression: str = "none") -> str:
    suffix = "zst" if compression == "zstd" else "raw"
    return f"cas/v1/sha256/{sha[:2]}/{sha[2:4]}/{sha}.{suffix}"


def _candidate(sha: str, data: bytes, compression: str = "none", mime: str = "text/plain",
               original_size: int | None = None) -> ArtifactRef:
    return ArtifactRef(
        sha256=sha,
        original_size=len(data) if original_size is None else original_size,
        stored_size=len(data),
        mime=mime,
        compression=compression,
        storage_driver="local",
        locator=_loc(sha, compression),
        storage_version="cas/v1",
    )


def _ref(sha: str | None = None, data: bytes = b"abc", compression: str = "none", **over) -> ArtifactRef:
    sha = sha or _sha()
    base = dict(
        sha256=sha,
        original_size=len(data),
        stored_size=len(data),
        mime="application/octet-stream",
        compression=compression,
        storage_driver="local",
        locator=_loc(sha, compression),
        storage_version="cas/v1",
    )
    base.update(over)
    return ArtifactRef(**base)


def _no_temp(root) -> bool:
    return not any(p.is_file() and ".tmp-" in p.name for p in Path(root).rglob("*"))


# ---------------- 路径安全（防御纵深） ----------------

def test_rejects_absolute_path(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    with pytest.raises(ArtifactPathError):
        d._resolve_locator("/etc/passwd")
    with pytest.raises(ArtifactPathError):
        d._resolve_locator("cas/v1/sha256/aa/aa/" + _sha() + ".raw/../../escape")


def test_rejects_dotdot(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    with pytest.raises(ArtifactPathError):
        d._resolve_locator("../escape")
    with pytest.raises(ArtifactPathError):
        d._resolve_locator("a/../outside")


def test_rejects_nul(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    with pytest.raises(ArtifactPathError):
        d._resolve_locator("a\x00b")


def test_rejects_symlink_escape(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    outside = tmp_path.parent / "outside-dir"
    outside.mkdir(exist_ok=True)
    link_dir = tmp_path / "linked"
    os.symlink(outside, link_dir)
    with pytest.raises(ArtifactPathError):
        d._resolve_locator("linked/evil")


def test_contract_rejects_noncanonical_locator():
    sha = _sha()
    with pytest.raises(ArtifactPathError):
        ArtifactRef(sha256=sha, original_size=3, stored_size=3, mime="x",
                    compression="none", storage_driver="local",
                    locator="cas/v1/sha256/aa/aa/" + sha + ".raw", storage_version="cas/v1")


def test_valid_locator_published(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    sha = _sha()
    res = d.put_if_absent(_candidate(sha, b"abc"), b"abc")
    assert res.created is True
    assert (tmp_path / _loc(sha)).read_bytes() == b"abc"


# ---------------- 原子 no-replace / 并发去重 ----------------

def test_put_absent_then_existing_no_overwrite(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    sha = _sha()
    assert d.put_if_absent(_candidate(sha, b"abc"), b"abc").created is True
    res = d.put_if_absent(_candidate(sha, b"abc"), b"abc")
    assert res.created is False          # 已存在，不覆盖
    assert (tmp_path / _loc(sha)).read_bytes() == b"abc"


def test_no_temp_files_left(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    sha = _sha()
    d.put_if_absent(_candidate(sha, b"abc"), b"abc")
    assert _no_temp(tmp_path)


def test_concurrent_put_collects_thread_exceptions(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    sha = _sha()
    data = b"concurrent-" * 100
    errors: list[BaseException] = []
    created_flags: list[bool] = []
    lock = threading.Lock()

    def _put():
        try:
            res = d.put_if_absent(_candidate(sha, data), data)
            with lock:
                created_flags.append(res.created)
        except BaseException as e:      # 收集线程异常，不能只检查成功结果
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=_put) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], f"thread exceptions leaked: {errors}"
    assert sum(created_flags) == 1      # 只有一个 created=True（并发胜者）
    assert (tmp_path / _loc(sha)).read_bytes() == data
    assert _no_temp(tmp_path)


# ---------------- 有界 get / head 语义 ----------------

def test_get_not_found(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    with pytest.raises(ArtifactNotFound):
        d.get(_ref(data=b"missing"))


def test_get_bounded_read_rejects_declared_size_mismatch(tmp_path):
    """有界读取：声明 stored_size 与实际不符 → IntegrityError，不做无界分配。"""
    d = LocalArtifactDriver(str(tmp_path))
    sha = _sha()
    data = b"A" * 5000
    compressed = zstd.ZstdCompressor(level=6).compress(data)
    d.put_if_absent(_candidate(sha, compressed, compression="zstd", original_size=len(data)), compressed)
    ref = _ref(sha=sha, data=compressed, compression="zstd",
               original_size=len(data), stored_size=len(compressed) + 1)
    with pytest.raises(ArtifactIntegrityError):
        d.get(ref)


def test_head_reports_actual_stored_and_original_meta(tmp_path):
    """head.stored_size 来自底层实际对象；original_size 是元数据，不是压缩体大小。"""
    d = LocalArtifactDriver(str(tmp_path))
    sha = _sha()
    data = b"B" * 5000
    compressed = zstd.ZstdCompressor(level=6).compress(data)
    d.put_if_absent(_candidate(sha, compressed, compression="zstd", original_size=len(data)), compressed)
    ref = _ref(sha=sha, data=compressed, compression="zstd",
               original_size=len(data), stored_size=len(compressed))
    h = d.head(ref)
    assert h.original_size == len(data)
    assert h.stored_size == (tmp_path / ref.locator).stat().st_size
    assert h.stored_size == len(compressed)
    assert h.compression == "zstd"


def test_head_and_exists(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    sha = _sha()
    d.put_if_absent(_candidate(sha, b"abc"), b"abc")
    ref = _ref(sha=sha)
    assert d.exists(ref) is True
    h = d.head(ref)
    assert isinstance(h, ArtifactHead)
    assert h.stored_size == 3
    # 不同 sha → 不同 locator → 不存在
    assert d.exists(_ref(sha="c" * 64, data=b"not-there")) is False


# ---------------- range ----------------

def test_get_range_driver(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    sha = _sha()
    data = b"0123456789"
    d.put_if_absent(_candidate(sha, data), data)
    ref = _ref(sha=sha, data=data)
    assert d.get_range(ref, 2, 5) == b"234"
    assert d.get_range(ref, 0, 0) == b""
    assert d.get_range(ref, 0, 10) == b"0123456789"
    with pytest.raises(ValueError):
        d.get_range(ref, 5, 2)
    with pytest.raises(ValueError):
        d.get_range(ref, True, 3)
    # 范围超过实际文件 size → IntegrityError（不静默截断）
    with pytest.raises(ArtifactIntegrityError):
        d.get_range(ref, 0, 999)


# ---------------- 持久化失败注入 ----------------

def test_file_fsync_failure_no_target_no_temp(tmp_path, monkeypatch):
    d = LocalArtifactDriver(str(tmp_path))
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("injected fsync")))
    sha = _sha()
    with pytest.raises(ArtifactStorageError):
        d.put_if_absent(_candidate(sha, b"abc"), b"abc")
    assert not (tmp_path / _loc(sha)).exists()
    assert _no_temp(tmp_path)


def test_link_failure_no_target_no_temp(tmp_path, monkeypatch):
    d = LocalArtifactDriver(str(tmp_path))
    monkeypatch.setattr(os, "link", lambda src, dst: (_ for _ in ()).throw(OSError("injected link")))
    sha = _sha()
    with pytest.raises(ArtifactStorageError):
        d.put_if_absent(_candidate(sha, b"abc"), b"abc")
    assert not (tmp_path / _loc(sha)).exists()
    assert _no_temp(tmp_path)


def test_dir_fsync_failure_keeps_published_target_and_converges(tmp_path, monkeypatch):
    d = LocalArtifactDriver(str(tmp_path))
    real_fsync = os.fsync

    def dir_only_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("injected dir fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", dir_only_fsync)
    sha = _sha()
    data = b"abc"
    with pytest.raises(ArtifactStorageError):
        d.put_if_absent(_candidate(sha, data), data)
    # target 已完整发布（不可变），temp 已清理；不得把完整 target 删成不确定状态
    assert (tmp_path / _loc(sha)).read_bytes() == data
    assert _no_temp(tmp_path)
    # 移除故障，重试：existing 分支必须实际调用 directory fsync 并成功 → created=False
    calls = {"dir": 0}

    def counting_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            calls["dir"] += 1
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting_fsync)
    res = d.put_if_absent(_candidate(sha, data), data)
    assert res.created is False
    assert calls["dir"] >= 1                  # 重试真正耐久化（fsync 计数增加），不能只凭 target 存在
    assert (tmp_path / _loc(sha)).read_bytes() == data


def test_existing_branch_calls_dir_fsync(tmp_path, monkeypatch):
    """普通 existing 分支返回 created=False 前必须实际调用 directory fsync。"""
    d = LocalArtifactDriver(str(tmp_path))
    sha = _sha()
    data = b"abc"
    d.put_if_absent(_candidate(sha, data), data)
    calls = {"dir": 0}
    real_fsync = os.fsync

    def counting_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            calls["dir"] += 1
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting_fsync)
    res = d.put_if_absent(_candidate(sha, data), data)
    assert res.created is False
    assert calls["dir"] >= 1
    assert (tmp_path / _loc(sha)).read_bytes() == data


def test_existing_branch_dir_fsync_failure_no_success(tmp_path, monkeypatch):
    """existing 分支的 directory fsync 失败必须抛 ArtifactStorageError，不得返回成功。"""
    d = LocalArtifactDriver(str(tmp_path))
    sha = _sha()
    data = b"abc"
    d.put_if_absent(_candidate(sha, data), data)
    real_fsync = os.fsync

    def fail_dir_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("injected dir fsync on existing")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_dir_fsync)
    with pytest.raises(ArtifactStorageError):
        d.put_if_absent(_candidate(sha, data), data)
    assert (tmp_path / _loc(sha)).read_bytes() == data   # 目标未被改动


def test_eexist_branch_calls_dir_fsync(tmp_path, monkeypatch):
    """并发 EEXIST loser 分支返回 created=False 前必须实际调用 directory fsync。"""
    d = LocalArtifactDriver(str(tmp_path))
    sha = _sha()
    data = b"abc"
    real_link = os.link

    def eexist_after_create(src, dst):
        real_link(src, dst)          # 模拟并发胜者已发布 target
        raise FileExistsError()      # 本进程随后收到 EEXIST

    monkeypatch.setattr(os, "link", eexist_after_create)
    calls = {"dir": 0}
    real_fsync = os.fsync

    def counting_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            calls["dir"] += 1
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting_fsync)
    res = d.put_if_absent(_candidate(sha, data), data)
    assert res.created is False
    assert calls["dir"] >= 1
    assert (tmp_path / _loc(sha)).read_bytes() == data
    assert _no_temp(tmp_path)


def test_eexist_branch_dir_fsync_failure_no_success(tmp_path, monkeypatch):
    """EEXIST loser 分支的 directory fsync 失败必须抛 ArtifactStorageError。"""
    d = LocalArtifactDriver(str(tmp_path))
    sha = _sha()
    data = b"abc"
    real_link = os.link

    def eexist_after_create(src, dst):
        real_link(src, dst)
        raise FileExistsError()

    monkeypatch.setattr(os, "link", eexist_after_create)
    real_fsync = os.fsync

    def fail_dir_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("injected dir fsync on eexist")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_dir_fsync)
    with pytest.raises(ArtifactStorageError):
        d.put_if_absent(_candidate(sha, data), data)
    assert (tmp_path / _loc(sha)).read_bytes() == data
    assert _no_temp(tmp_path)


# ---------------- health / aclose / immutable ----------------

def test_health_ok_unique_probe(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    fixed = tmp_path / ".health-write"
    fixed.write_text("keep-me")
    h = d.health()
    assert h.ok is True
    assert h.driver == "local"
    assert "password" not in str(h.detail)
    assert fixed.read_text() == "keep-me"                 # 不覆盖/删除固定名称既有文件
    leftovers = [p for p in tmp_path.rglob("*") if p.is_file() and p.name.endswith(".probe")]
    assert leftovers == []                                 # 唯一临时 probe 已清理


def test_aclose_idempotent(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    d.aclose()
    d.aclose()          # 幂等


def test_no_delete_api(tmp_path):
    d = LocalArtifactDriver(str(tmp_path))
    for name in ("delete", "remove", "delete_bytes"):
        assert not hasattr(d, name), f"Driver must not expose {name}"
