"""Replay / audit Repository（WP-04 Checkpoint B）。

只拥有 SQL：replay_runs 的辅助读写。绝不 commit、不调用网络、不做业务判断。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class AuditRepository:
    """replay/audit SQL；不持有状态。"""

    async def insert_replay_run(
        self,
        session: AsyncSession,
        *,
        run_key: str,
        replay_kind: str,
        manifest_hash: str,
        code_hash: str,
        seed: int,
        input_artifact_hash: str,
        output_artifact_hash: str,
        result: dict | None,
    ) -> int:
        res = await session.execute(
            text(
                "INSERT INTO trading.replay_runs "
                "(run_key, replay_kind, manifest_hash, code_hash, seed, "
                " input_artifact_hash, output_artifact_hash, result) VALUES "
                "(:k, :rk, :mh, :ch, :seed, :ia, :oa, :res) RETURNING id"
            ),
            {
                "k": run_key, "rk": replay_kind, "mh": manifest_hash, "ch": code_hash,
                "seed": seed, "ia": input_artifact_hash, "oa": output_artifact_hash, "res": result,
            },
        )
        return res.scalar_one()

    async def get_replay_run(self, session: AsyncSession, run_key: str) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.replay_runs WHERE run_key=:k"),
            {"k": run_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def list_replay_runs(
        self, session: AsyncSession, manifest_hash: str
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT * FROM trading.replay_runs WHERE manifest_hash=:mh "
                "ORDER BY id"
            ),
            {"mh": manifest_hash},
        )
        return _rows(result)
