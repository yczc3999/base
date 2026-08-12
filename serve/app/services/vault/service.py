"""Identity-bound envelope-encryption orchestration for the trading vault.

Failure auditing is deliberately separated from the caller's unit of work.  A
``failure_audit`` callback receives a redacted event and is responsible for its
own durable transaction.  The callback is never given the caller's session.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

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
FailureAuditSink = Callable[[Mapping[str, Any]], Awaitable[None]]


class VaultService:
    """Coordinate vault crypto and persistence inside a caller-owned UoW."""

    def __init__(
        self,
        repo: Any,
        keyring: Keyring,
        *,
        env: str = "dev",
        runtime_identity: str,
        failure_audit: FailureAuditSink | None = None,
    ) -> None:
        if not isinstance(runtime_identity, str) or not runtime_identity.strip():
            raise ValueError("vault_runtime_identity_required")
        self._repo = repo
        self._keyring = keyring
        self._env = env
        # Identity is supplied by the execution-plane composition root, never by a
        # per-call DTO.  Calls still carry an identity for explicit audit evidence,
        # but cannot use that caller-controlled string to impersonate another runtime.
        self._runtime_identity = runtime_identity.strip()
        self._failure_audit_sink = failure_audit

    @property
    def durable_failure_audit_configured(self) -> bool:
        """Whether rejected/failed reads have an independent durable audit sink."""
        return self._failure_audit_sink is not None or callable(
            getattr(self._repo, "insert_durable_failure_event", None)
        )

    async def audit_consumer_failure(
        self,
        *,
        entry_id: int,
        version_id: int | None,
        identity: str,
        purpose: str,
        reason: str,
    ) -> None:
        """Durably audit failure after decryption but before a secret is usable.

        Consumers use this for strict credential decoding/identity validation errors.
        The event is deliberately bounded and never accepts exception text or plaintext.
        """
        if not self.durable_failure_audit_configured:
            raise VaultCryptoError("vault_durable_failure_audit_required")
        safe_reason = str(reason)
        if (
            not safe_reason
            or len(safe_reason) > 64
            or any(not (char.isascii() and (char.isalnum() or char in "_-")) for char in safe_reason)
        ):
            raise ValueError("vault_consumer_failure_reason_invalid")
        await self._emit_failure_audit({
            "operation": "read",
            "entry_id": int(entry_id),
            "secret_version_id": version_id,
            "identity": identity,
            "purpose": purpose,
            "key_version": None,
            "result": "READ_FAILED",
            "reason": safe_reason,
        })

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

    def _build_aad(
        self,
        entry: dict[str, Any],
        *,
        account: str | None,
        identity: str,
        purpose: str,
        secret_version: int,
        key_id: str,
        key_version: str,
    ) -> bytes:
        # ``identity`` has already passed _require_identity.  Binding that
        # verified caller value prevents a database value from silently
        # authenticating a forged caller identity.
        return build_aad(
            env=self._env,
            entry=entry["name"],
            secret_kind=entry["secret_kind"],
            account=account,
            runtime_identity=identity,
            purpose=purpose,
            secret_version=secret_version,
            key_id=key_id,
            key_version=key_version,
        )

    def _require_identity(self, entry: Mapping[str, Any], identity: str) -> None:
        expected = entry.get("runtime_identity")
        if (
            not isinstance(identity, str)
            or not hmac.compare_digest(self._runtime_identity, identity)
            or not isinstance(expected, str)
            or not hmac.compare_digest(expected, self._runtime_identity)
        ):
            raise VaultAuthError("vault_runtime_identity_mismatch")

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

    async def _emit_failure_audit(self, event: Mapping[str, Any]) -> None:
        """Emit outside the caller UoW; the injected sink owns durability.

        A repository can explicitly expose ``insert_durable_failure_event`` as
        an independent-transaction hook.  The logging fallback is intentionally
        labelled unconfigured and is not represented as a durable DB audit.
        """
        sink = self._failure_audit_sink
        if sink is not None:
            await sink(dict(event))
            return
        repo_hook = getattr(self._repo, "insert_durable_failure_event", None)
        if callable(repo_hook):
            await repo_hook(dict(event))
            return
        logger.error(
            "vault.failure_audit.unconfigured operation=%s entry_id=%s result=%s reason=%s",
            event["operation"], event["entry_id"], event["result"], event["reason"],
        )

    async def _record_failure(
        self,
        session: Any,
        *,
        operation: str,
        entry_id: int,
        secret_version_id: int | None,
        identity: str,
        purpose: str,
        key_version: str | None,
        exc: Exception,
        entry_exists: bool,
    ) -> None:
        auth_failure = isinstance(exc, (VaultAuthError, VaultKeyError))
        identity_failure = isinstance(exc, VaultAuthError) and str(exc) == (
            "vault_runtime_identity_mismatch"
        )
        result = "AUTH_FAILED" if auth_failure else f"{operation.upper()}_FAILED"
        reason = "identity_mismatch" if identity_failure else type(exc).__name__
        # Preserve the existing in-UoW access trail when its FK can be valid,
        # but never rely on it for failure durability.
        if entry_exists:
            try:
                await self._audit(
                    session,
                    entry_id=entry_id,
                    secret_version_id=secret_version_id,
                    identity=identity,
                    purpose=purpose,
                    key_version=key_version,
                    result=result,
                    reason=reason,
                )
            except Exception as audit_exc:  # keep the original vault failure
                logger.error(
                    "vault.transactional_failure_audit.failed operation=%s entry_id=%s error=%s",
                    operation, entry_id, type(audit_exc).__name__,
                )
        event = {
            "operation": operation,
            "entry_id": entry_id,
            "secret_version_id": secret_version_id,
            "identity": identity,
            "purpose": purpose,
            "key_version": key_version,
            "result": result,
            "reason": reason,
        }
        try:
            await self._emit_failure_audit(event)
        except Exception as audit_exc:  # preserve the security-relevant cause
            logger.error(
                "vault.failure_audit.failed operation=%s entry_id=%s error=%s",
                operation, entry_id, type(audit_exc).__name__,
            )

    async def create_entry(
        self, session: Any, *, name: str, secret_kind: str, runtime_identity: str
    ) -> dict[str, Any]:
        if not hmac.compare_digest(self._runtime_identity, runtime_identity):
            raise VaultAuthError("vault_runtime_identity_mismatch")
        if secret_kind not in (
            "generic", "api_credential", "signer_private_key", "l2_secret", "passphrase"
        ):
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
        entry: dict[str, Any] | None = None
        version_id: int | None = None
        try:
            entry = await self._repo.get_entry(session, entry_id=entry_id)
            if entry is None:
                raise VaultKeyError("vault_entry_missing")
            self._require_identity(entry, identity)
            version_no = await self._repo.next_version_no(session, entry_id=entry_id)
            nonce_bytes = nonce or new_nonce()
            aad = self._build_aad(
                entry, account=account, identity=identity, purpose=purpose,
                secret_version=version_no, key_id=key_id, key_version=key_version,
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
                    account=account, runtime_identity=identity, purpose=purpose,
                    secret_version=version_no, key_id=key_id, key_version=key_version,
                ),
                aad_hash=aad_sha256(aad),
                ciphertext_hash=hashlib.sha256(packed).hexdigest(),
                algorithm="aes-256-gcm",
                status="active",
            )
            version_id = version["id"]
            await self._audit(
                session, entry_id=entry_id, secret_version_id=version_id,
                identity=identity, purpose=purpose, key_version=key_version,
                result="STORED", reason="success",
            )
            logger.info(
                "vault.secret.stored entry_id=%s version_id=%s purpose=%s identity=%s key_version=%s",
                entry_id, version_id, purpose, identity, key_version,
            )
            return version
        except Exception as exc:
            await self._record_failure(
                session, operation="store", entry_id=entry_id,
                secret_version_id=version_id, identity=identity, purpose=purpose,
                key_version=key_version, exc=exc, entry_exists=entry is not None,
            )
            raise

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
        entry: dict[str, Any] | None = None
        version: dict[str, Any] | None = None
        try:
            entry = await self._repo.get_entry(session, entry_id=entry_id)
            if entry is None:
                raise VaultKeyError("vault_entry_missing")
            self._require_identity(entry, identity)
            version = await self._repo.get_version(session, version_id=version_id)
            if version is None or version["entry_id"] != entry_id:
                raise VaultKeyError("vault_version_missing")
            aad = self._build_aad(
                entry, account=account, identity=identity, purpose=purpose,
                secret_version=version["version_no"], key_id=version["key_id"],
                key_version=version["key_version"],
            )
            if aad_sha256(aad) != version["aad_hash"]:
                raise VaultAuthError("vault_aad_context_mismatch")
            key = resolve_key(self._keyring, version["key_id"], version["key_version"])
            plaintext = envelope_decrypt(
                version["ciphertext"], key=key, key_id=version["key_id"],
                key_version=version["key_version"], nonce=bytes.fromhex(version["nonce"]),
                aad=aad,
            )
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
        except Exception as exc:
            await self._record_failure(
                session, operation="read", entry_id=entry_id, secret_version_id=version_id,
                identity=identity, purpose=purpose,
                key_version=version.get("key_version") if version else None,
                exc=exc, entry_exists=entry is not None,
            )
            raise

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
        entry: dict[str, Any] | None = None
        active: dict[str, Any] | None = None
        new_version_id: int | None = None
        try:
            entry = await self._repo.get_entry(session, entry_id=entry_id)
            if entry is None:
                raise VaultKeyError("vault_entry_missing")
            self._require_identity(entry, identity)
            # The production repository supports FOR UPDATE; rotation must hold
            # this lock through insert/retire in the caller's transaction.
            active = await self._repo.get_active_version(
                session, entry_id=entry_id, for_update=True,
            )
            version_no = await self._repo.next_version_no(session, entry_id=entry_id)
            nonce_bytes = nonce or new_nonce()
            aad = self._build_aad(
                entry, account=account, identity=identity, purpose=purpose,
                secret_version=version_no, key_id=key_id, key_version=key_version,
            )
            key = resolve_key(self._keyring, key_id, key_version)
            packed = envelope_encrypt(
                secret, key=key, key_id=key_id, key_version=key_version,
                nonce=nonce_bytes, aad=aad,
            )
            # Verify the newly-produced ciphertext before the first DB mutation.
            verified = envelope_decrypt(
                packed, key=key, key_id=key_id, key_version=key_version,
                nonce=nonce_bytes, aad=aad,
            )
            if not hmac.compare_digest(verified, secret):
                raise VaultAuthError("vault_rotation_verification_failed")

            # Preserve explicit historical-version readability: after the new
            # ciphertext has been verified, authenticate the version that will
            # be retired before mutating lifecycle state.
            if active is not None:
                old_aad = self._build_aad(
                    entry, account=account, identity=identity, purpose=purpose,
                    secret_version=active["version_no"], key_id=active["key_id"],
                    key_version=active["key_version"],
                )
                if aad_sha256(old_aad) != active["aad_hash"]:
                    raise VaultAuthError("vault_aad_context_mismatch")
                old_key = resolve_key(
                    self._keyring, active["key_id"], active["key_version"],
                )
                envelope_decrypt(
                    active["ciphertext"], key=old_key, key_id=active["key_id"],
                    key_version=active["key_version"],
                    nonce=bytes.fromhex(active["nonce"]), aad=old_aad,
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
                    account=account, runtime_identity=identity, purpose=purpose,
                    secret_version=version_no, key_id=key_id, key_version=key_version,
                ),
                aad_hash=aad_sha256(aad),
                ciphertext_hash=hashlib.sha256(packed).hexdigest(),
                algorithm="aes-256-gcm",
                status="active",
                supersedes=supersedes,
            )
            new_version_id = new_version["id"]
            if active is not None:
                retired = await self._repo.mark_version_retired(
                    session, version_id=active["id"],
                )
                if not retired:
                    raise VaultCryptoError("vault_rotate_retire_conflict")
            await self._audit(
                session, entry_id=entry_id, secret_version_id=new_version_id,
                identity=identity, purpose=purpose, key_version=key_version,
                result="ROTATED", reason="success",
            )
            logger.info(
                "vault.secret.rotated entry_id=%s new_version_id=%s old_version_id=%s identity=%s",
                entry_id, new_version_id, supersedes, identity,
            )
            return new_version
        except Exception as exc:
            await self._record_failure(
                session, operation="rotate", entry_id=entry_id,
                secret_version_id=(active or {}).get("id") or new_version_id,
                identity=identity, purpose=purpose, key_version=key_version,
                exc=exc, entry_exists=entry is not None,
            )
            raise

    async def deny(
        self, session: Any, *, entry_id: int, purpose: str, identity: str, reason: str
    ) -> None:
        try:
            await self._audit(
                session, entry_id=entry_id, secret_version_id=None,
                identity=identity, purpose=purpose, key_version=None,
                result="DENIED", reason=reason,
            )
            logger.info(
                "vault.secret.denied entry_id=%s purpose=%s identity=%s reason=%s",
                entry_id, purpose, identity, reason,
            )
        except Exception as exc:
            await self._record_failure(
                session, operation="deny", entry_id=entry_id, secret_version_id=None,
                identity=identity, purpose=purpose, key_version=None,
                exc=exc, entry_exists=False,
            )
            raise
