"""
Artifact Store 公共合同 — immutable typed contract + 受控异常。

只定义类型与异常，不含存储实现；Driver 依赖本模块，Service 依赖 Driver Protocol。

R1 修正（WP-00c1-r1）：
- `ArtifactRef` 对所有字段做运行时校验：size 非 bool 整数且非负；mime 非空且无
  CR/LF/NUL；storage_driver 仅 local|s3；storage_version 固定 cas/v1；locator 必须
  与 sha256+compression 精确匹配规范路径（不接受 ./、别名或错误 suffix）；
  raw 对象必须 original_size == stored_size。
- `ArtifactHead.stored_size` 语义 = 底层实际对象大小；`original_size` = 对象元数据。
- `ArtifactDriver.put_if_absent(candidate, data)` 接收完整 candidate ref。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# 布局版本：唯一合法值
LOCATOR_VERSION = "cas/v1"

# sha256 目录段
_DIGEST_PATH = "sha256"

# 64 位小写 hex SHA-256
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# compression 只允许 none|zstd
_COMPRESSION_VALUES = frozenset({"none", "zstd"})

# storage_driver 白名单
_VALID_DRIVERS = frozenset({"local", "s3"})

_MIME_FORBIDDEN = ("\r", "\n", "\x00")


def build_locator(sha256: str, compression: str) -> str:
    """从 sha256 + compression 生成规范 locator：cas/v1/sha256/ab/cd/<sha>.raw|.zst。"""
    if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
        raise ValueError(f"sha256 must be 64 lowercase hex, got {sha256!r}")
    if compression not in _COMPRESSION_VALUES:
        raise ValueError(
            f"compression must be one of {sorted(_COMPRESSION_VALUES)}, got {compression!r}"
        )
    suffix = "zst" if compression == "zstd" else "raw"
    return (
        f"{LOCATOR_VERSION}/{_DIGEST_PATH}/{sha256[:2]}/{sha256[2:4]}/{sha256}.{suffix}"
    )


def _validate_mime(mime: str) -> None:
    if not isinstance(mime, str) or not mime:
        raise ValueError("mime must be a non-empty string")
    if any(c in mime for c in _MIME_FORBIDDEN):
        raise ValueError(f"mime contains control characters: {mime!r}")


def _validate_size(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int (non-bool), got {value!r}")
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}")


# ---------------- 受控异常 ----------------

class ArtifactError(Exception):
    """所有 artifact 错误的基类。"""


class ArtifactNotFound(ArtifactError):
    """对象不存在。"""


class ArtifactTooLarge(ArtifactError):
    """对象超过配置上限（写入或解压读取时，防压缩炸弹）。"""


class ArtifactIntegrityError(ArtifactError):
    """stored 内容与声明不一致（size 或 SHA 不匹配）。"""


class ArtifactRangeUnsupported(ArtifactError):
    """该对象/压缩类型不支持 range 读取（如 zstd 原文 range）。"""


class ArtifactPathError(ArtifactError):
    """locator 非法（非规范路径、绝对路径、..、NUL、symlink 逃逸等）。"""


class ArtifactStorageError(ArtifactError):
    """底层存储失败（写/fsync/publish/stat/read 失败等）。"""


# ---------------- immutable typed contract ----------------

@dataclass(frozen=True)
class ArtifactRef:
    """指向一个不可变对象的完整引用；构造时全字段校验。"""

    sha256: str
    original_size: int
    stored_size: int
    mime: str
    compression: str            # "none" | "zstd"
    storage_driver: str         # "local" | "s3"
    locator: str                # 规范相对对象名
    storage_version: str = LOCATOR_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.sha256, str) or not _SHA256_RE.match(self.sha256):
            raise ValueError(f"sha256 must be 64 lowercase hex, got {self.sha256!r}")
        _validate_size("original_size", self.original_size)
        _validate_size("stored_size", self.stored_size)
        if self.compression not in _COMPRESSION_VALUES:
            raise ValueError(
                f"compression must be one of {sorted(_COMPRESSION_VALUES)}, "
                f"got {self.compression!r}"
            )
        _validate_mime(self.mime)
        if self.storage_driver not in _VALID_DRIVERS:
            raise ValueError(
                f"storage_driver must be one of {sorted(_VALID_DRIVERS)}, "
                f"got {self.storage_driver!r}"
            )
        if self.storage_version != LOCATOR_VERSION:
            raise ValueError(
                f"storage_version must be {LOCATOR_VERSION!r}, got {self.storage_version!r}"
            )
        if self.compression == "none" and self.original_size != self.stored_size:
            raise ValueError(
                "raw object must satisfy original_size == stored_size, "
                f"got {self.original_size} != {self.stored_size}"
            )
        canonical = build_locator(self.sha256, self.compression)
        if self.locator != canonical:
            raise ArtifactPathError(
                f"locator is not canonical: {self.locator!r} != {canonical!r}"
            )


@dataclass(frozen=True)
class ArtifactHead:
    """对象元数据头（用于 verify）。

    `stored_size` 必须来自底层实际对象；`original_size` 是对象元数据（未压缩原文大小），
    不是压缩体大小。
    """

    sha256: str
    original_size: int
    stored_size: int
    mime: str
    compression: str
    storage_version: str = LOCATOR_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.sha256, str) or not _SHA256_RE.match(self.sha256):
            raise ValueError(f"sha256 must be 64 lowercase hex, got {self.sha256!r}")
        _validate_size("original_size", self.original_size)
        _validate_size("stored_size", self.stored_size)
        if self.compression not in _COMPRESSION_VALUES:
            raise ValueError(
                f"compression must be one of {sorted(_COMPRESSION_VALUES)}, "
                f"got {self.compression!r}"
            )
        _validate_mime(self.mime)
        if self.storage_version != LOCATOR_VERSION:
            raise ValueError(
                f"storage_version must be {LOCATOR_VERSION!r}, got {self.storage_version!r}"
            )


@dataclass(frozen=True)
class PutResult:
    """put 的结果；created=False 表示命中已有去重对象（不覆盖）。"""

    created: bool
    head: ArtifactHead


@dataclass(frozen=True)
class ArtifactHealth:
    """Driver health 状态（不泄漏 secret）。"""

    ok: bool
    driver: str
    detail: dict = field(default_factory=dict)


@runtime_checkable
class ArtifactDriver(Protocol):
    """Driver 必须实现的接口（结构类型）。"""

    driver_name: str

    def put_if_absent(self, candidate: ArtifactRef, data: bytes) -> PutResult: ...

    def get(self, ref: ArtifactRef) -> bytes: ...

    def get_range(self, ref: ArtifactRef, start: int, end: int) -> bytes: ...

    def head(self, ref: ArtifactRef) -> ArtifactHead: ...

    def exists(self, ref: ArtifactRef) -> bool: ...

    def health(self) -> ArtifactHealth: ...

    def aclose(self) -> None: ...
