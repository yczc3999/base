"""Vault Repository（WP-05 Checkpoint B）。

只拥有 SQL：vault entry/version/access-event 的裸 SQL 与 CAS。绝不 commit、不调用网络、
不做业务判断。secret 明文永远不经过本层（只有 ciphertext bytes + key metadata）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class VaultRepository:
    """vault SQL；不持有状态。"""

    async def insert_entry(
        self,
        session: AsyncSession,
        *,
        name: str,
        secret_kind: str,
        runtime_identity: str,
        status: str = "active",
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "INSERT INTO trading.secret_vault_entries "
                "(name, secret_kind, runtime_identity, status) "
                "VALUES (:name, :kind, :rid, :status) RETURNING *"
            ),
            {"name": name, "kind": secret_kind, "rid": runtime_identity, "status": status},
        )
        rows = _rows(result)
        if not rows:
            raise RuntimeError("vault_entry_insert_lost")
        return rows[0]

    async def get_entry(self, session: AsyncSession, *, entry_id: int) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, name, secret_kind, runtime_identity, status, created_at "
                "FROM trading.secret_vault_entries WHERE id=:e"
            ),
            {"e": entry_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_entry_by_name(self, session: AsyncSession, *, name: str) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, name, secret_kind, runtime_identity, status, created_at "
                "FROM trading.secret_vault_entries WHERE name=:name"
            ),
            {"name": name},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def next_version_no(self, session: AsyncSession, *, entry_id: int) -> int:
        result = await session.execute(
            text(
                "SELECT COALESCE(MAX(version_no), 0) + 1 FROM trading.secret_vault_versions "
                "WHERE entry_id=:e"
            ),
            {"e": entry_id},
        )
        return int(result.scalar_one())

    async def insert_version(
        self,
        session: AsyncSession,
        *,
        entry_id: int,
        version_no: int,
        key_id: str,
        key_version: str,
        nonce: str,
        ciphertext: bytes,
        aad_context: dict[str, Any],
        aad_hash: str,
        ciphertext_hash: str,
        algorithm: str,
        status: str,
        supersedes: int | None = None,
    ) -> dict[str, Any]:
        import json as _json

        result = await session.execute(
            text(
                "INSERT INTO trading.secret_vault_versions "
                "(entry_id, version_no, key_id, key_version, nonce, ciphertext, "
                " aad_context, aad_hash, ciphertext_hash, algorithm, status, supersedes) "
                "VALUES (:e, :vn, :kid, :kv, :nonce, :ct, CAST(:aad AS jsonb), "
                " :ah, :ch, :alg, :status, :sup) RETURNING *"
            ),
            {
                "e": entry_id, "vn": version_no, "kid": key_id, "kv": key_version,
                "nonce": nonce, "ct": ciphertext, "aad": _json.dumps(aad_context),
                "ah": aad_hash,
                "ch": ciphertext_hash, "alg": algorithm, "status": status, "sup": supersedes,
            },
        )
        rows = _rows(result)
        if not rows:
            raise RuntimeError("vault_version_insert_lost")
        return rows[0]

    async def get_version(self, session: AsyncSession, *, version_id: int) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, entry_id, version_no, key_id, key_version, nonce, ciphertext, "
                "aad_context, aad_hash, ciphertext_hash, algorithm, status, supersedes, created_at "
                "FROM trading.secret_vault_versions WHERE id=:v"
            ),
            {"v": version_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_active_version(
        self, session: AsyncSession, *, entry_id: int, for_update: bool = False
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT id, entry_id, version_no, key_id, key_version, nonce, ciphertext, "
            "aad_context, aad_hash, ciphertext_hash, algorithm, status, supersedes, created_at "
            "FROM trading.secret_vault_versions "
            "WHERE entry_id=:e AND status='active' ORDER BY version_no DESC LIMIT 1"
        )
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(text(sql), {"e": entry_id})
        rows = _rows(result)
        return rows[0] if rows else None

    async def list_versions(
        self, session: AsyncSession, *, entry_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT id, entry_id, version_no, key_id, key_version, status, supersedes, "
                "created_at FROM trading.secret_vault_versions "
                "WHERE entry_id=:e ORDER BY version_no"
            ),
            {"e": entry_id},
        )
        return _rows(result)

    async def mark_version_retired(self, session: AsyncSession, *, version_id: int) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.secret_vault_versions SET status='retired' "
                "WHERE id=:v AND status='active'"
            ),
            {"v": version_id},
        )
        return result.rowcount == 1

    async def activate_version(self, session: AsyncSession, *, version_id: int) -> bool:
        """Historical ciphertext is never reactivated; rotation always appends a new version."""
        del session, version_id
        raise RuntimeError("vault_version_reactivation_forbidden")

    async def insert_access_event(
        self,
        session: AsyncSession,
        *,
        entry_id: int,
        secret_version_id: int | None,
        subject: str,
        identity: str,
        purpose: str,
        key_version: str | None,
        result: str,
        result_reason: str,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.secret_access_events "
                "(entry_id, secret_version_id, subject, identity, purpose, key_version, "
                " result, result_reason) "
                "VALUES (:e, :sv, :subject, :identity, :purpose, :kv, :result, :reason)"
            ),
            {
                "e": entry_id, "sv": secret_version_id, "subject": subject,
                "identity": identity, "purpose": purpose, "kv": key_version,
                "result": result, "reason": result_reason,
            },
        )
