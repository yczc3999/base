"""AES-256-GCM envelope encryption primitives（WP-05 技术决策 10–11）。

- 纯 stdlib + ``pycryptodome``（``Crypto.Cipher.AES`` MODE_GCM），无平台 SDK import。
- 每加密唯一 96-bit nonce（``os.urandom(12)``）；128-bit tag（GCM 内建）。
- 密文布局：``ciphertext || tag``（tag 固定最后 16 字节），nonce 单独由调用方持久化。
- AAD 至少绑定 env/entry/secret_kind/account/runtime_identity/purpose/secret_version/
  key_id/key_version（canonical JSON → bytes），由 ``build_aad`` 生成。
- 错误 identity/AAD/tamper/未知 key version 一律认证失败（固定异常），无 legacy/plaintext
  fallback。secret 明文绝不写入异常 message。
"""

from __future__ import annotations

import json
import os
from hashlib import sha256
from typing import Mapping

from Crypto.Cipher import AES

AES_KEY_SIZE = 32
NONCE_SIZE = 12
TAG_SIZE = 16


class VaultCryptoError(Exception):
    """vault 加密/解密基础错误（不携带 secret 明文）。"""


class VaultKeyError(VaultCryptoError):
    """未知 key id/version 或 key 尺寸错误。"""


class VaultAuthError(VaultCryptoError):
    """AAD/identity/tamper/未知 key 认证失败。"""


def _canonical_json_bytes(fields: Mapping[str, object]) -> bytes:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def build_aad(
    *,
    env: str,
    entry: str,
    secret_kind: str,
    account: str | None,
    runtime_identity: str,
    purpose: str,
    secret_version: int,
    key_id: str,
    key_version: str,
) -> bytes:
    """canonical AAD bytes：字段名固定，序列化确定，用于 GCM 绑定与 aad_hash。"""
    fields = {
        "env": env,
        "entry": entry,
        "secret_kind": secret_kind,
        "account": account,
        "runtime_identity": runtime_identity,
        "purpose": purpose,
        "secret_version": secret_version,
        "key_id": key_id,
        "key_version": key_version,
    }
    return _canonical_json_bytes(fields)


def aad_sha256(aad: bytes) -> str:
    return sha256(aad).hexdigest()


def new_nonce() -> bytes:
    return os.urandom(NONCE_SIZE)


def resolve_key(
    keyring: Mapping[tuple[str, str], bytes], key_id: str, key_version: str
) -> bytes:
    """keyring 查 key；未知 key id/version 立即认证失败（不写 key 内容到异常）。"""
    key = keyring.get((key_id, key_version))
    if key is None:
        raise VaultKeyError(f"vault_unknown_key_version:{key_id}:{key_version}")
    if len(key) != AES_KEY_SIZE:
        raise VaultKeyError("vault_key_size_invalid")
    return key


def encrypt(
    plaintext: bytes,
    *,
    key: bytes,
    key_id: str,
    key_version: str,
    nonce: bytes,
    aad: bytes,
) -> bytes:
    """AES-256-GCM 加密；返回 ``ciphertext || tag``。key_id/key_version 参与 AAD 语义。"""
    if len(key) != AES_KEY_SIZE:
        raise VaultKeyError("vault_key_size_invalid")
    if len(nonce) != NONCE_SIZE:
        raise VaultCryptoError("vault_nonce_size_invalid")
    if not isinstance(plaintext, bytes):
        raise VaultCryptoError("vault_plaintext_not_bytes")
    if not isinstance(aad, bytes):
        raise VaultCryptoError("vault_aad_not_bytes")
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(aad)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return ciphertext + tag


def decrypt(
    ciphertext: bytes,
    *,
    key: bytes,
    key_id: str,
    key_version: str,
    nonce: bytes,
    aad: bytes,
) -> bytes:
    """AES-256-GCM 解密并验证 AAD/tag；认证失败抛 ``VaultAuthError``。"""
    if len(key) != AES_KEY_SIZE:
        raise VaultKeyError("vault_key_size_invalid")
    if len(nonce) != NONCE_SIZE:
        raise VaultCryptoError("vault_nonce_size_invalid")
    if ciphertext is None or len(ciphertext) < TAG_SIZE:
        raise VaultAuthError("vault_ciphertext_truncated")
    body, tag = ciphertext[:-TAG_SIZE], ciphertext[-TAG_SIZE:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(aad)
    try:
        return cipher.decrypt_and_verify(body, tag)
    except ValueError as exc:
        raise VaultAuthError("vault_authentication_failed") from exc
