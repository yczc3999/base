"""
ArtifactStore Service — content-addressed 不可变大对象底座。

- SHA-256 永远计算**未压缩原文**；locator = cas/v1/sha256/ab/cd/<sha>.raw|.zst。
- compression=auto|none|zstd：auto 仅在达阈值且 zstd 后确实更小时使用。
- 相同内容/codec 并发 put 去重（Driver put_if_absent no-replace）；跨压缩配置去重时
  返回包含**实际** stored size 的 canonical ref，绝不以候选压缩长度要求现存对象。
- get_bytes/verify 先 `driver.head` 做 fail-closed 预检（head.stored_size 不一致立即拒绝），
  再按配置校验 original/SHA，任何篡改 fail-closed。
- zstd 解压**有界**：frame 声明 content size 且大于上限时在分配输出前拒绝；content size
  未知时以 streaming reader 最多读取 max+1 bytes；超限 ArtifactTooLarge，损坏/截断/
  尾随非法数据 ArtifactIntegrityError。
- get_range 仅 compression=none：严格 `0 <= start <= end <= original_size`，必须走
  driver.get_range，返回长度必须恰为 end-start。
- 硬上限（original/stored size > max）在任何 I/O 前直接拒绝，无法用 verify=False 绕过。

Service 不选 retention、不判断业务有效性、不访问 DB/Redis/env/global settings；
Driver 与配置经构造参数注入。
"""

from __future__ import annotations

import hashlib

import zstandard as zstd

from app.config import Settings, settings as default_settings
from app.services.artifact_store.contracts import (
    LOCATOR_VERSION,
    ArtifactDriver,
    ArtifactHead,
    ArtifactHealth,
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

# zstd 级别安全范围（库支持 1..22，任务 §8 配置层已保证 ge=1；此处再夹紧）
_ZSTD_MIN_LEVEL = 1
_ZSTD_MAX_LEVEL = 22


def sha256_hex(data: bytes) -> str:
    """未压缩原文的 SHA-256（64 位小写 hex）。"""
    return hashlib.sha256(data).hexdigest()


class ArtifactStore:
    """公开接口：put_bytes / get_bytes / get_range / verify / health / aclose。"""

    def __init__(self, driver: ArtifactDriver, cfg: Settings | None = None) -> None:
        self._driver = driver
        self._cfg = cfg or default_settings

    # ---- 内部 ----

    def _locator(self, sha256: str, compression: str) -> str:
        return build_locator(sha256, compression)

    def _compressor(self) -> zstd.ZstdCompressor:
        level = self._cfg.ARTIFACT_ZSTD_LEVEL
        if not (_ZSTD_MIN_LEVEL <= level <= _ZSTD_MAX_LEVEL):
            raise ValueError(
                f"ARTIFACT_ZSTD_LEVEL out of range {_ZSTD_MIN_LEVEL}..{_ZSTD_MAX_LEVEL}: {level}"
            )
        return zstd.ZstdCompressor(level=level)

    def _decompressor(self) -> zstd.ZstdDecompressor:
        return zstd.ZstdDecompressor()

    def _encode(self, data: bytes, mode: str) -> tuple[bytes, str]:
        """返回 (stored_bytes, compression)。mode=auto 仅在压缩后确实更小时选 zstd。"""
        if mode == "none":
            return data, "none"
        if mode == "zstd":
            return self._compressor().compress(data), "zstd"
        # auto
        if len(data) < self._cfg.ARTIFACT_COMPRESSION_THRESHOLD_BYTES:
            return data, "none"
        compressed = self._compressor().compress(data)
        if len(compressed) < len(data):
            return compressed, "zstd"
        return data, "none"

    def _declared_content_size(self, data: bytes) -> int:
        """frame 声明的 content size；未知/非法返回 -1。"""
        try:
            return zstd.frame_content_size(data)
        except Exception:  # pragma: no cover - 非 zstd frame 防御
            return -1

    def _decode(self, data: bytes, compression: str) -> bytes:
        """**有界**解压 + **完整 frame EOF 证明**；绝不静默接受截断/损坏 frame。

        - 声明 content size 大于 max：分配输出前直接 `ArtifactTooLarge`。
        - bounded pass：`stream_reader` 分块读取，最多 max+1 bytes，证明输出不超过 max。
        - EOF pass：`decompressobj().eof` 证明完整 frame 结束（对无 content-size 及
          带 checksum 的截断 frame 均可靠返回 eof=False）；`unused_data/unconsumed_tail`
          非空 = 尾随非法数据。本 pass 的内存有界：bounded pass 已证明输出 <= max。
        - 超限 `ArtifactTooLarge`；截断/损坏/未到 EOF/尾随 `ArtifactIntegrityError`；
          异常分类不依赖易变错误字符串。
        """
        if compression == "none":
            return data
        if compression != "zstd":
            raise ArtifactIntegrityError(f"unknown compression {compression!r}")
        max_out = self._cfg.ARTIFACT_MAX_OBJECT_BYTES
        declared = self._declared_content_size(data)
        if declared >= 0 and declared > max_out:
            # frame 声明超过上限：分配输出前直接拒绝（防 OOM）
            raise ArtifactTooLarge(
                f"zstd frame declares content size {declared}, exceeds max {max_out}"
            )
        # bounded pass：证明输出不超过 max（不整段物化）
        out = bytearray()
        reader = self._decompressor().stream_reader(data)
        try:
            while True:
                chunk = reader.read(max_out + 1 - len(out))
                if not chunk:
                    break
                out += chunk
                if len(out) > max_out:
                    raise ArtifactTooLarge(f"decompressed object exceeds max {max_out}")
        except zstd.ZstdError as e:
            # 损坏 / 尾随非法数据（stream_reader 解析后续 frame 报错）
            raise ArtifactIntegrityError(f"zstd decompression failed: {e}") from e
        finally:
            reader.close()
        # EOF pass：证明完整 frame 结束（bounded pass 已证明输出 <= max，二次解码有界）
        try:
            dobj = self._decompressor().decompressobj()
            dobj.decompress(data)
            if not dobj.eof:
                raise ArtifactIntegrityError(
                    "zstd frame not complete (truncated or missing end marker)"
                )
            if dobj.unused_data or dobj.unconsumed_tail:
                raise ArtifactIntegrityError("zstd frame has trailing data")
        except zstd.ZstdError as e:
            raise ArtifactIntegrityError(f"zstd decompression failed: {e}") from e
        return bytes(out)

    def _ref_from_put(self, sha256: str, original_size: int, stored_size: int,
                      mime: str, compression: str) -> ArtifactRef:
        return ArtifactRef(
            sha256=sha256,
            original_size=original_size,
            stored_size=stored_size,
            mime=mime,
            compression=compression,
            storage_driver=self._driver.driver_name,
            locator=self._locator(sha256, compression),
            storage_version=LOCATOR_VERSION,
        )

    def _validate_ref(self, ref: ArtifactRef) -> None:
        """读取前校验 ref 与当前 driver/版本/规范路径匹配，禁止跨 driver 静默流转。"""
        if ref.storage_driver != self._driver.driver_name:
            raise ArtifactStorageError(
                f"ref storage_driver {ref.storage_driver!r} does not match "
                f"driver {self._driver.driver_name!r}"
            )
        if ref.storage_version != LOCATOR_VERSION:
            raise ArtifactStorageError(
                f"ref storage_version {ref.storage_version!r} != {LOCATOR_VERSION!r}"
            )
        canonical = self._locator(ref.sha256, ref.compression)
        if ref.locator != canonical:
            raise ArtifactPathError(
                f"ref locator is not canonical: {ref.locator!r} != {canonical!r}"
            )

    def _check_ceiling(self, ref: ArtifactRef) -> None:
        """硬上限：original/stored 超过 max 在任何 I/O 前直接拒绝（verify=False 不可绕过）。"""
        max_out = self._cfg.ARTIFACT_MAX_OBJECT_BYTES
        if ref.original_size > max_out or ref.stored_size > max_out:
            raise ArtifactTooLarge(
                f"ref sizes exceed max {max_out}: "
                f"original={ref.original_size} stored={ref.stored_size}"
            )
        if ref.compression == "none" and ref.original_size != ref.stored_size:
            raise ArtifactIntegrityError(
                f"raw object must satisfy original_size == stored_size, "
                f"got {ref.original_size} != {ref.stored_size}"
            )

    def _head_precheck(self, ref: ArtifactRef, head: ArtifactHead) -> None:
        """fail-closed：实际 stored size 与 ref 声明不一致立即拒绝。"""
        if head.stored_size != ref.stored_size:
            raise ArtifactIntegrityError(
                f"stored size mismatch for {ref.locator}: "
                f"ref {ref.stored_size}, actual {head.stored_size}"
            )

    # ---- 公开接口 ----

    def put_bytes(self, data: bytes, mime: str, compression: str = "auto") -> ArtifactRef:
        """写入对象，返回不可变 ArtifactRef；已存在则去重返回（不覆盖）。"""
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError(f"put_bytes expects bytes, got {type(data).__name__}")
        data = bytes(data)
        if len(data) > self._cfg.ARTIFACT_MAX_OBJECT_BYTES:
            raise ArtifactTooLarge(
                f"object {len(data)} bytes exceeds ARTIFACT_MAX_OBJECT_BYTES "
                f"{self._cfg.ARTIFACT_MAX_OBJECT_BYTES}"
            )
        if compression not in ("auto", "none", "zstd"):
            raise ValueError(f"compression must be auto|none|zstd, got {compression!r}")
        if not isinstance(mime, str) or not mime:
            raise ValueError("mime must be a non-empty string")
        if any(c in mime for c in "\r\n\x00"):
            raise ValueError(f"mime contains control characters: {mime!r}")

        sha = sha256_hex(data)
        stored, codec = self._encode(data, compression)
        # 写前双尺寸硬上限：压缩后 stored 也可能超限（原文合法、zstd 膨胀），必须在
        # 任何 Driver 调用前拒绝，避免产生"写得进、读不出"的对象
        if len(stored) > self._cfg.ARTIFACT_MAX_OBJECT_BYTES:
            raise ArtifactTooLarge(
                f"stored object {len(stored)} bytes exceeds ARTIFACT_MAX_OBJECT_BYTES "
                f"{self._cfg.ARTIFACT_MAX_OBJECT_BYTES}"
            )
        candidate = self._ref_from_put(sha, len(data), len(stored), mime, codec)
        result: PutResult = self._driver.put_if_absent(candidate, stored)
        if not result.created:
            # 命中已有对象：以**实际** stored size 有界读取、解压并校验原文 SHA/original size，
            # 绝不静默覆盖冲突内容，也绝不要求现存压缩长度等于本次候选压缩长度。
            actual_stored = result.head.stored_size
            if actual_stored > self._cfg.ARTIFACT_MAX_OBJECT_BYTES:
                raise ArtifactTooLarge(
                    f"existing object stored size {actual_stored} exceeds max "
                    f"{self._cfg.ARTIFACT_MAX_OBJECT_BYTES}"
                )
            existing = self._ref_from_put(sha, len(data), actual_stored, mime, codec)
            content = self._driver.get(existing)
            decoded = self._decode(content, codec)
            if len(decoded) != len(data) or sha256_hex(decoded) != sha:
                raise ArtifactStorageError(
                    f"existing object {existing.locator} content does not match "
                    f"sha256 {sha}"
                )
            return existing
        return self._ref_from_put(sha, len(data), result.head.stored_size, mime, codec)

    def get_bytes(self, ref: ArtifactRef, verify: bool | None = None) -> bytes:
        """读取对象并（按配置）校验 SHA/size；硬上限与 head 预检始终生效。"""
        should_verify = self._cfg.ARTIFACT_VERIFY_ON_READ if verify is None else verify
        self._validate_ref(ref)
        self._check_ceiling(ref)
        head = self._driver.head(ref)
        self._head_precheck(ref, head)
        stored = self._driver.get(ref)
        data = self._decode(stored, ref.compression)
        if should_verify:
            if len(data) != ref.original_size:
                raise ArtifactIntegrityError(
                    f"original size mismatch for {ref.locator}: "
                    f"expected {ref.original_size}, got {len(data)}"
                )
            if sha256_hex(data) != ref.sha256:
                raise ArtifactIntegrityError(
                    f"sha256 mismatch for {ref.locator}: {ref.sha256} != {sha256_hex(data)}"
                )
        return data

    def get_range(self, ref: ArtifactRef, start: int, end: int) -> bytes:
        """读取 stored bytes 的半开区间 [start, end)；仅 compression=none，严格有界。"""
        self._validate_ref(ref)
        if ref.compression != "none":
            raise ArtifactRangeUnsupported(
                f"range read on compressed object ({ref.compression}) unsupported"
            )
        self._check_ceiling(ref)
        if not isinstance(start, int) or isinstance(start, bool):
            raise ValueError(f"range start must be an int, got {start!r}")
        if not isinstance(end, int) or isinstance(end, bool):
            raise ValueError(f"range end must be an int, got {end!r}")
        if start < 0 or end < start or end > ref.original_size:
            raise ValueError(
                f"invalid range [{start}, {end}) for size {ref.original_size}"
            )
        head = self._driver.head(ref)
        self._head_precheck(ref, head)
        data = self._driver.get_range(ref, start, end)
        if len(data) != end - start:
            raise ArtifactIntegrityError(
                f"range [{start}, {end}) returned {len(data)} bytes, "
                f"expected {end - start}"
            )
        return data

    def verify(self, ref: ArtifactRef) -> ArtifactHead:
        """校验对象存在且内容与 ref 一致，返回 ArtifactHead（stored_size 取实际对象）。"""
        self._validate_ref(ref)
        self._check_ceiling(ref)
        head = self._driver.head(ref)
        self._head_precheck(ref, head)
        stored = self._driver.get(ref)
        data = self._decode(stored, ref.compression)
        if len(data) != ref.original_size:
            raise ArtifactIntegrityError(
                f"original size mismatch for {ref.locator}: "
                f"expected {ref.original_size}, got {len(data)}"
            )
        if sha256_hex(data) != ref.sha256:
            raise ArtifactIntegrityError(
                f"sha256 mismatch for {ref.locator}: {ref.sha256} != {sha256_hex(data)}"
            )
        return ArtifactHead(
            sha256=ref.sha256,
            original_size=len(data),
            stored_size=head.stored_size,
            mime=ref.mime,
            compression=ref.compression,
            storage_version=LOCATOR_VERSION,
        )

    def health(self) -> ArtifactHealth:
        return self._driver.health()

    def aclose(self) -> None:
        self._driver.aclose()
