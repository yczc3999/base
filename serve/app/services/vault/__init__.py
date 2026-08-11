"""Vault 服务包（WP-05 Checkpoint B）。

- ``envelope``：AES-256-GCM 封装原语（纯 stdlib + pycryptodome）。
- ``service``：VaultService 业务编排（access audit / rotation / deny）。
纯服务层，禁止平台 SDK import；master key 不入 DB。
"""

from app.services.vault.envelope import (
    VaultAuthError,
    VaultCryptoError,
    VaultKeyError,
    aad_sha256,
    build_aad,
    decrypt,
    encrypt,
    new_nonce,
    resolve_key,
)
from app.services.vault.service import Keyring, VaultService

__all__ = [
    "VaultService",
    "Keyring",
    "VaultCryptoError",
    "VaultKeyError",
    "VaultAuthError",
    "build_aad",
    "aad_sha256",
    "new_nonce",
    "encrypt",
    "decrypt",
    "resolve_key",
]
