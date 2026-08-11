"""VaultService：envelope encryption 业务编排（WP-05 技术决策 10–11）。

- 每次 encrypt/decrypt/rotate/deny 追加 access event（identity/purpose/entry/version/
  key version/result/reason），绝不保存 secret。
- master key 不入 DB：调用方提供 keyring bytes（config 只给 keyring 引用）。
- AAD 由 DB 中 entry 的 name/secret_kind/runtime_identity 与调用方 identity/account/purpose
  构建；错误 identity/AAD/tamper/未知 key version 一律认证失败。
- 轮换顺序：encrypt new → decrypt/verify old → 原子 activate new/retire old（同一 UoW）。
- secret 明文绝不写 log / exception / manifest。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Mapping

from app.services.vault.envelope import (
    VaultAuthError,
    VaultCryptoError,
    VaultKeyError,
    aad_sha256,
    build_aad,
    decrypt as envelope_decrypt,
    encrypt as envelope_encrypt,
    new_nonce,
    resolve_key,
)

logger = logging.getLogger(__name__)

Keyring = Mapping[tuple[str, str], bytes]


class VaultService:
    """依赖注入 repository + keyring；所有写操作在同一外层 UoW 内由调用方提交。"""

    def __init__(self, repo: Any, keyring: Keyring, *, env: str = "dev") -> None:
        self._repo = repo
        self._keyring = keyring
        self._env = env

    # ---- helpers ----

    @staticmethod
    def _aad_context(
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
    ) -> dict[str, Any]:
        return {
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

    def _build_aad(self, entry: dict[str, Any], *, account: str | None, purpose: str,
                   secret_version: int, key_id: str, key_version: str) -> bytes:
        return build_aad(
            env=self._env,
            entry=entry["name"],
            secret_kind=entry["secret_kind"],
            account=account,
            runtime_identity=entry["runtime_identity"],
            purpose=purpose,
            secret_version=secret_version,
            key_id=key_id,
            key_version=key_version,
        )

    async def _audit(
        self,
        session: Any,
        *,
        entry_id: int,
        secret_version_id: int | None,
        identity: str,
        purpose: str,
        key_version: str | None,
        result: str,
        reason: str,
    ) -> None:
        await self._repo.insert_access_event(
            session,
            entry_id=entry_id,
            secret_version_id=secret_version_id,
            subject=identity,
            identity=identity,
            purpose=purpose,
            key_version=key_version,
            result=result,
            result_reason=reason,
        )

    # ---- public API ----

    async def create_entry(
        self, session: Any, *, name: str, secret_kind: str, runtime_identity: str
    ) -> dict[str, Any]:
        """创建 vault entry（稳定 identity，无 secret 内容）。"""
        if secret_kind not in ("generic", "api_credential", "signer_private_key", "l2_secret", "passphrase"):
            raise VaultCryptoError(f"vault_secret_kind_unknown:{secret_kind}")
        return await self._repo.insert_entry(
            session, name=name, secret_kind=secret_kind, runtime_identity=runtime_identity,
        )

    async def store_secret(
        self,
        session: Any,
        *,
        entry_id: int,
        secret: bytes,
        purpose: str,
        identity: str,
        account: str | None = None,
        key_id: str,
        key_version: str,
        nonce: bytes | None = None,
    ) -> dict[str, Any]:
        """encrypt + insert version + access event（调用方 UoW 内原子）。"""
        entry = await self._repo.get_entry(session, entry_id=entry_id)
        if entry is None:
            raise VaultKeyError("vault_entry_missing")
        version_no = await self._repo.next_version_no(session, entry_id=entry_id)
        nonce_bytes = nonce or new_nonce()
        aad = self._build_aad(
            entry, account=account, purpose=purpose, secret_version=version_no,
            key_id=key_id, key_version=key_version,
        )
        key = resolve_key(self._keyring, key_id, key_version)
        packed = envelope_encrypt(
            secret, key=key, key_id=key_id, key_version=key_version,
            nonce=nonce_bytes, aad=aad,
        )
        version = await self._repo.insert_version(
            session,
            entry_id=entry_id,
            version_no=version_no,
            key_id=key_id,
            key_version=key_version,
            nonce=nonce_bytes.hex(),
            ciphertext=packed,
            aad_context=self._aad_context(
                env=self._env, entry=entry["name"], secret_kind=entry["secret_kind"],
                account=account, runtime_identity=entry["runtime_identity"],
                purpose=purpose, secret_version=version_no,
                key_id=key_id, key_version=key_version,
            ),
            aad_hash=aad_sha256(aad),
            ciphertext_hash=hashlib.sha256(packed).hexdigest(),
            algorithm="aes-256-gcm",
            status="active",
        )
        await self._audit(
            session, entry_id=entry_id, secret_version_id=version["id"],
            identity=identity, purpose=purpose, key_version=key_version,
            result="STORED", reason="success",
        )
        logger.info(
            "vault.secret.stored entry_id=%s version_id=%s purpose=%s identity=%s key_version=%s",
            entry_id, version["id"], purpose, identity, key_version,
        )
        return version

    async def read_secret(
        self,
        session: Any,
        *,
        entry_id: int,
        version_id: int,
        purpose: str,
        identity: str,
        account: str | None = None,
    ) -> bytes:
        """decrypt + verify AAD + access event；任何认证失败抛固定异常。"""
        entry = await self._repo.get_entry(session, entry_id=entry_id)
        if entry is None:
            raise VaultKeyError("vault_entry_missing")
        version = await self._repo.get_version(session, version_id=version_id)
        if version is None or version["entry_id"] != entry_id:
            raise VaultKeyError("vault_version_missing")
        aad = self._build_aad(
            entry, account=account, purpose=purpose,
            secret_version=version["version_no"],
            key_id=version["key_id"], key_version=version["key_version"],
        )
        try:
            if aad_sha256(aad) != version["aad_hash"]:
                raise VaultAuthError("vault_aad_context_mismatch")
            key = resolve_key(self._keyring, version["key_id"], version["key_version"])
            plaintext = envelope_decrypt(
                version["ciphertext"], key=key, key_id=version["key_id"],
                key_version=version["key_version"],
                nonce=bytes.fromhex(version["nonce"]), aad=aad,
            )
        except (VaultAuthError, VaultKeyError) as exc:
            await self._audit(
                session, entry_id=entry_id, secret_version_id=version_id,
                identity=identity, purpose=purpose, key_version=version["key_version"],
                result="AUTH_FAILED", reason="decrypt",
            )
            raise
        await self._audit(
            session, entry_id=entry_id, secret_version_id=version_id,
            identity=identity, purpose=purpose, key_version=version["key_version"],
            result="READ", reason="success",
        )
        logger.info(
            "vault.secret.read entry_id=%s version_id=%s purpose=%s identity=%s",
            entry_id, version_id, purpose, identity,
        )
        return plaintext

    async def rotate_secret(
        self,
        session: Any,
        *,
        entry_id: int,
        secret: bytes,
        purpose: str,
        identity: str,
        account: str | None = None,
        key_id: str,
        key_version: str,
        nonce: bytes | None = None,
    ) -> dict[str, Any]:
        """encrypt new → decrypt/verify old → 原子 activate new/retire old（同一 UoW）。"""
        entry = await self._repo.get_entry(session, entry_id=entry_id)
        if entry is None:
            raise VaultKeyError("vault_entry_missing")
        active = await self._repo.get_active_version(session, entry_id=entry_id)
        if active is not None:
            old_aad = self._build_aad(
                entry, account=account, purpose=purpose,
                secret_version=active["version_no"],
                key_id=active["key_id"], key_version=active["key_version"],
            )
            try:
                if aad_sha256(old_aad) != active["aad_hash"]:
                    raise VaultAuthError("vault_aad_context_mismatch")
                old_key = resolve_key(self._keyring, active["key_id"], active["key_version"])
                envelope_decrypt(
                    active["ciphertext"], key=old_key, key_id=active["key_id"],
                    key_version=active["key_version"],
                    nonce=bytes.fromhex(active["nonce"]), aad=old_aad,
                )
            except (VaultAuthError, VaultKeyError) as exc:
                await self._audit(
                    session, entry_id=entry_id, secret_version_id=active["id"],
                    identity=identity, purpose=purpose, key_version=active["key_version"],
                    result="ROTATE_VERIFY_FAILED", reason="decrypt",
                )
                raise
        version_no = await self._repo.next_version_no(session, entry_id=entry_id)
        nonce_bytes = nonce or new_nonce()
        aad = self._build_aad(
            entry, account=account, purpose=purpose, secret_version=version_no,
            key_id=key_id, key_version=key_version,
        )
        key = resolve_key(self._keyring, key_id, key_version)
        packed = envelope_encrypt(
            secret, key=key, key_id=key_id, key_version=key_version,
            nonce=nonce_bytes, aad=aad,
        )
        supersedes = active["id"] if active is not None else None
        new_version = await self._repo.insert_version(
            session,
            entry_id=entry_id,
            version_no=version_no,
            key_id=key_id,
            key_version=key_version,
            nonce=nonce_bytes.hex(),
            ciphertext=packed,
            aad_context=self._aad_context(
                env=self._env, entry=entry["name"], secret_kind=entry["secret_kind"],
                account=account, runtime_identity=entry["runtime_identity"],
                purpose=purpose, secret_version=version_no,
                key_id=key_id, key_version=key_version,
            ),
            aad_hash=aad_sha256(aad),
            ciphertext_hash=hashlib.sha256(packed).hexdigest(),
            algorithm="aes-256-gcm",
            status="active",
            supersedes=supersedes,
        )
        if active is not None:
            retired = await self._repo.mark_version_retired(session, version_id=active["id"])
            if not retired:
                raise VaultCryptoError("vault_rotate_retire_conflict")
        await self._audit(
            session, entry_id=entry_id, secret_version_id=new_version["id"],
            identity=identity, purpose=purpose, key_version=key_version,
            result="ROTATED", reason="success",
        )
        logger.info(
            "vault.secret.rotated entry_id=%s new_version_id=%s old_version_id=%s identity=%s",
            entry_id, new_version["id"], supersedes, identity,
        )
        return new_version

    async def deny(
        self, session: Any, *, entry_id: int, purpose: str, identity: str, reason: str
    ) -> None:
        """deny 审计：只记录结果与原因，不接触 secret。"""
        await self._audit(
            session, entry_id=entry_id, secret_version_id=None,
            identity=identity, purpose=purpose, key_version=None,
            result="DENIED", reason=reason,
        )
        logger.info(
            "vault.secret.denied entry_id=%s purpose=%s identity=%s reason=%s",
            entry_id, purpose, identity, reason,
        )
