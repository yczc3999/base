"""Keyring 引用加载器（后台运行时配置 / WP-05 vault 组合根）。

``PM_V2_VAULT_KEYRING_REF`` 只保存引用，不保存 key 明文。本模块把引用解析成
``VaultService`` 需要的 ``Keyring``（``{(key_id, key_version): key bytes}``）：

- ``env://<VAR>``：从环境变量读值，按 hex（64 字符）或 base64 解码为 32 字节 master key；
- ``file://<path>``：读文件内容，先按 hex/base64 文本解码；文件恰为 32 字节原始
  二进制时直接采用。

单 key 约定：``("master", "v1")`` —— 与 ``envelope.resolve_key`` 的
``(key_id, key_version)`` tuple 解析一致。缺失/非法一律抛 ``VaultKeyError``，
异常 message 绝不携带 key 材料（只带引用形态与变量名/路径）。
"""

from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path

from app.services.vault.envelope import AES_KEY_SIZE, VaultKeyError
from app.services.vault.service import Keyring

_KEY_ID = "master"
_KEY_VERSION = "v1"


def _decode_key_material(raw: bytes, *, source: str) -> bytes:
    """把引用内容解码为 32 字节 master key；hex → base64 → 原始 32 字节。"""
    text = raw.strip()
    try:
        candidate = bytes.fromhex(text.decode("ascii"))
        if len(candidate) == AES_KEY_SIZE:
            return candidate
    except (ValueError, UnicodeDecodeError):
        pass
    try:
        candidate = base64.b64decode(text, validate=True)
        if len(candidate) == AES_KEY_SIZE:
            return candidate
    except (ValueError, binascii.Error):
        pass
    if len(raw) == AES_KEY_SIZE:
        return raw
    raise VaultKeyError(f"vault_keyring_ref_invalid:{source}")


def load_keyring(ref: str) -> Keyring:
    """按引用加载 keyring；未配置/形态未知/内容非法 → ``VaultKeyError``（fail closed）。"""
    if not isinstance(ref, str) or not ref.strip():
        raise VaultKeyError("vault_keyring_ref_missing")
    ref = ref.strip()
    if ref.startswith("env://"):
        var_name = ref[len("env://"):].strip()
        if not var_name:
            raise VaultKeyError("vault_keyring_ref_invalid:env")
        value = os.environ.get(var_name)
        if value is None or not value.strip():
            raise VaultKeyError(f"vault_keyring_env_unset:{var_name}")
        key = _decode_key_material(value.encode("utf-8"), source=f"env:{var_name}")
        return {(_KEY_ID, _KEY_VERSION): key}
    if ref.startswith("file://"):
        path = ref[len("file://"):].strip()
        if not path:
            raise VaultKeyError("vault_keyring_ref_invalid:file")
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            raise VaultKeyError(f"vault_keyring_file_unreadable:{type(exc).__name__}") from exc
        key = _decode_key_material(raw, source="file")
        return {(_KEY_ID, _KEY_VERSION): key}
    raise VaultKeyError("vault_keyring_ref_scheme_unknown")
