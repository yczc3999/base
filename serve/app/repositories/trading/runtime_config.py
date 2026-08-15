"""Runtime config Repository（后台运行时配置：flag get/upsert + append-only 审计）。

只拥有 SQL：``trading.runtime_flags`` 单行 upsert 与 ``runtime_flag_events``
追加。绝不 commit、不做业务判断；本层不含任何 secret（模型 key 走 vault 表，
由 ``VaultRepository`` 承载）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class RuntimeConfigRepository:
    """runtime flag SQL；不持有状态。"""

    async def get_flag(
        self, session: AsyncSession, *, flag_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT flag_key, flag_value, updated_by, updated_at "
                "FROM trading.runtime_flags WHERE flag_key=:k"
            ),
            {"k": flag_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def upsert_flag(
        self,
        session: AsyncSession,
        *,
        flag_key: str,
        flag_value: str,
        actor: str,
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "INSERT INTO trading.runtime_flags (flag_key, flag_value, updated_by) "
                "VALUES (:k, :v, :a) "
                "ON CONFLICT (flag_key) DO UPDATE "
                "SET flag_value=EXCLUDED.flag_value, updated_by=EXCLUDED.updated_by, "
                "    updated_at=now() "
                "RETURNING flag_key, flag_value, updated_by, updated_at"
            ),
            {"k": flag_key, "v": flag_value, "a": actor},
        )
        rows = _rows(result)
        if not rows:
            raise RuntimeError("runtime_flag_upsert_lost")
        return rows[0]

    async def insert_flag_event(
        self,
        session: AsyncSession,
        *,
        flag_key: str,
        old_value: str | None,
        new_value: str | None,
        actor: str,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.runtime_flag_events "
                "(flag_key, old_value, new_value, actor) "
                "VALUES (:k, :old, :new, :a)"
            ),
            {"k": flag_key, "old": old_value, "new": new_value, "a": actor},
        )
