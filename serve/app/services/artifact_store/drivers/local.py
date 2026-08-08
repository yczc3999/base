"""
Local Artifact Driver — 开发/测试/有界 spool 的本地对象存储。

- root 由构造参数注入并在启动时 resolve；只接受 contract 生成的规范相对 locator。
- 拒绝绝对路径、`..`、NUL、root 外路径以及任一已有 symlink 路径分量（防御纵深，
  规范 locator 已由 ArtifactRef 强制）。
- 临时文件与目标同文件系统：写入 → flush → file fsync → no-replace 原子发布
  （os.link）→ directory fsync；EEXIST 返回 created=False，其他错误清理 temp 后抛
  ArtifactStorageError。directory fsync 失败不得返回 created=True（保留已完整发布的
  不可变 target，重试经验证收敛）。existing 快路径与 EEXIST loser 分支返回 created=False
  前也必须 directory fsync 已有 target 的目录项，失败抛 ArtifactStorageError。
- 对象不可变，不暴露 delete 业务 API；get_range 只读取请求区间并校验范围在文件内。
- get() 按 ref.stored_size + 1 有界读取，禁止对未知大小文件无界分配。
- health() 用唯一临时 probe，不覆盖/删除固定名称文件；aclose() 幂等。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.services.artifact_store.contracts import (
    LOCATOR_VERSION,
    ArtifactHead,
    ArtifactHealth,
    ArtifactIntegrityError,
    ArtifactNotFound,
    ArtifactPathError,
    ArtifactRef,
    ArtifactStorageError,
    PutResult,
)

_FORBIDDEN_PATH_PARTS = ("..",)
_NUL = "\x00"


class LocalArtifactDriver:
    driver_name = "local"

    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as e:  # pragma: no cover - env-dependent
            raise ArtifactStorageError(f"cannot create artifact root {self._root}: {e}") from e

    # ---- 路径安全 ----

    def _resolve_locator(self, locator: str) -> Path:
        """校验相对 locator 并解析到 root 内的绝对路径。"""
        if not isinstance(locator, str) or not locator:
            raise ArtifactPathError("locator must be a non-empty string")
        if locator.startswith("/") or _NUL in locator:
            raise ArtifactPathError("locator must be relative and NUL-free")
        parts = locator.split("/")
        if any(part in _FORBIDDEN_PATH_PARTS or part == "" for part in parts):
            raise ArtifactPathError(f"locator contains unsafe path part: {locator!r}")
        path = self._root.joinpath(*parts)
        # 必须仍在 root 内（防御纵深）
        if not str(path.resolve()).startswith(str(self._root) + os.sep):
            raise ArtifactPathError(f"locator escapes root: {locator!r}")
        # 任一已有路径分量不得是 symlink（防符号链接逃逸）
        probe = self._root
        for part in parts:
            probe = probe / part
            if probe.exists() and probe.is_symlink():
                raise ArtifactPathError(f"symlink path component rejected: {probe}")
        return path

    def _parent_dir(self, path: Path) -> Path:
        d = path.parent
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ArtifactStorageError(f"cannot create parent dir {d}: {e}") from e
        return d

    def _fsync_dir(self, parent: Path) -> None:
        """directory fsync；OSError 一律转 ArtifactStorageError，不得静默成功。"""
        try:
            dfd = os.open(parent, os.O_RDONLY)
        except OSError as e:
            raise ArtifactStorageError(f"cannot open dir for fsync {parent}: {e}") from e
        try:
            try:
                os.fsync(dfd)
            except OSError as e:
                raise ArtifactStorageError(f"directory fsync failed for {parent}: {e}") from e
        finally:
            os.close(dfd)

    def _head(self, target: Path, candidate: ArtifactRef) -> ArtifactHead:
        """head.stored_size 必须来自底层实际对象；original_size 取 candidate 元数据。"""
        try:
            actual = target.stat().st_size
        except OSError as e:
            raise ArtifactStorageError(f"stat failed for {target}: {e}") from e
        return ArtifactHead(
            sha256=candidate.sha256,
            original_size=candidate.original_size,
            stored_size=actual,
            mime=candidate.mime,
            compression=candidate.compression,
            storage_version=LOCATOR_VERSION,
        )

    # ---- 写 ----

    def put_if_absent(self, candidate: ArtifactRef, data: bytes) -> PutResult:
        """原子 no-replace 发布；目标已存在返回 created=False 与**实际** head（不要求
        现存压缩长度等于本次候选长度，读取并验证由 Service 负责）。

        existing 快路径与 EEXIST 并发 loser 分支在返回 created=False 前都必须对 target 的
        parent 执行 directory fsync，保证已有对象的目录项耐久化；fsync 失败抛
        `ArtifactStorageError`，不得报告成功。"""
        target = self._resolve_locator(candidate.locator)
        if target.exists():
            self._fsync_dir(target.parent)
            return PutResult(created=False, head=self._head(target, candidate))
        parent = self._parent_dir(target)
        fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=".tmp-", suffix=".part")
        tmp = Path(tmp_name)
        try:
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
            except OSError as e:
                raise ArtifactStorageError(
                    f"write/fsync failed for {candidate.locator}: {e}"
                ) from e
            try:
                os.link(tmp, target)
            except FileExistsError:
                # 并发胜者已发布；created=False，不覆盖。同样耐久化已有 target 的目录项。
                self._fsync_dir(parent)
                return PutResult(created=False, head=self._head(target, candidate))
            except OSError as e:
                raise ArtifactStorageError(
                    f"atomic publish failed for {candidate.locator}: {e}"
                ) from e
            try:
                actual = target.stat().st_size
            except OSError as e:
                raise ArtifactStorageError(f"stat after publish failed for {target}: {e}") from e
            if actual != len(data):
                raise ArtifactStorageError(
                    f"published size mismatch for {candidate.locator}: "
                    f"expected {len(data)}, got {actual}"
                )
            self._fsync_dir(parent)
            return PutResult(created=True, head=self._head(target, candidate))
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:  # pragma: no cover - 清理尽力而为
                pass

    # ---- 读 ----

    def get(self, ref: ArtifactRef) -> bytes:
        """按 ref.stored_size + 1 有界读取并验证恰好等于声明长度；禁止 read_bytes 无界分配。"""
        path = self._resolve_locator(ref.locator)
        if not path.exists():
            raise ArtifactNotFound(f"artifact not found: {ref.locator}")
        try:
            actual = path.stat().st_size
        except OSError as e:
            raise ArtifactStorageError(f"stat failed for {path}: {e}") from e
        limit = ref.stored_size + 1
        try:
            with open(path, "rb") as f:
                data = f.read(limit)
        except OSError as e:
            raise ArtifactStorageError(f"read failed for {path}: {e}") from e
        if len(data) != ref.stored_size:
            raise ArtifactIntegrityError(
                f"stored size mismatch for {ref.locator}: "
                f"expected {ref.stored_size}, got {len(data)}"
            )
        return data

    def get_range(self, ref: ArtifactRef, start: int, end: int) -> bytes:
        """只读取请求区间；范围与实际文件 size 不符即拒绝（禁止静默截断）。"""
        if not isinstance(start, int) or isinstance(start, bool):
            raise ValueError(f"range start must be an int, got {start!r}")
        if not isinstance(end, int) or isinstance(end, bool):
            raise ValueError(f"range end must be an int, got {end!r}")
        if start < 0 or end < start:
            raise ValueError(f"invalid range [{start}, {end})")
        path = self._resolve_locator(ref.locator)
        if not path.exists():
            raise ArtifactNotFound(f"artifact not found: {ref.locator}")
        try:
            actual = path.stat().st_size
        except OSError as e:
            raise ArtifactStorageError(f"stat failed for {path}: {e}") from e
        if end > actual:
            raise ArtifactIntegrityError(
                f"range end {end} exceeds actual size {actual} for {ref.locator}"
            )
        try:
            with open(path, "rb") as f:
                f.seek(start)
                return f.read(end - start)
        except OSError as e:
            raise ArtifactStorageError(f"read failed for {path}: {e}") from e

    def head(self, ref: ArtifactRef) -> ArtifactHead:
        path = self._resolve_locator(ref.locator)
        if not path.exists():
            raise ArtifactNotFound(f"artifact not found: {ref.locator}")
        try:
            st = path.stat()
        except OSError as e:
            raise ArtifactStorageError(f"stat failed for {path}: {e}") from e
        return ArtifactHead(
            sha256=ref.sha256,
            original_size=ref.original_size,
            stored_size=st.st_size,
            mime=ref.mime,
            compression=ref.compression,
            storage_version=LOCATOR_VERSION,
        )

    def exists(self, ref: ArtifactRef) -> bool:
        try:
            path = self._resolve_locator(ref.locator)
        except ArtifactPathError:
            return False
        return path.exists()

    def health(self) -> ArtifactHealth:
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=self._root, prefix=".health-", suffix=".probe")
            try:
                os.close(fd)
            finally:
                os.unlink(name)
            return ArtifactHealth(ok=True, driver="local", detail={"root": str(self._root)})
        except OSError as e:
            return ArtifactHealth(ok=False, driver="local", detail={"error": str(e)})

    def aclose(self) -> None:
        """本地 driver 无长连接；幂等。"""
        return None
