"""Settlement / label Repository（WP-04 Checkpoint B）。

只拥有 SQL：resolution label revision、resolution cluster、score target 的辅助读写。
绝不 commit、不调用网络、不做业务判断。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class SettlementRepository:
    """settlement SQL；不持有状态。"""

    # ---------------- resolution labels ----------------

    async def get_label_current(
        self, session: AsyncSession, contract_spec_id: int, label_key: str
    ) -> dict[str, Any] | None:
        """返回当前（未被 supersede 的）label revision。"""
        result = await session.execute(
            text(
                "SELECT * FROM trading.resolution_labels r "
                "WHERE r.contract_spec_id=:cs AND r.label_key=:k "
                "  AND NOT EXISTS ("
                "    SELECT 1 FROM trading.resolution_labels s "
                "     WHERE s.contract_spec_id=r.contract_spec_id "
                "       AND s.label_key=r.label_key AND s.supersedes_id=r.id)"
                "ORDER BY r.version_no DESC LIMIT 1"
            ),
            {"cs": contract_spec_id, "k": label_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_label_by_version(
        self, session: AsyncSession, label_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.resolution_labels WHERE id=:id"),
            {"id": label_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def insert_label_revision(
        self,
        session: AsyncSession,
        *,
        contract_spec_id: int,
        label_key: str,
        version_no: int,
        state: str,
        resolution_state: str | None,
        resolution_source: str | None,
        evidence_artifact_id: int | None,
        raw_outcome: dict | None,
        token_cashflow: dict | None,
        policy_code_hash: str,
        supersedes_id: int | None,
        auditor_identity: str | None,
        exclusion_reason: str | None,
        conflict_set: list | None,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.resolution_labels "
                "(contract_spec_id, label_key, version_no, state, resolution_state, "
                " resolution_source, evidence_artifact_id, raw_outcome, token_cashflow, "
                " policy_code_hash, supersedes_id, auditor_identity, exclusion_reason, "
                " conflict_set) VALUES "
                "(:cs, :k, :v, :st, :rs, :rsc, :ea, :ro, :tc, :ph, :sup, :au, :er, :cf) "
                "RETURNING id"
            ),
            {
                "cs": contract_spec_id, "k": label_key, "v": version_no, "st": state,
                "rs": resolution_state, "rsc": resolution_source, "ea": evidence_artifact_id,
                "ro": raw_outcome, "tc": token_cashflow, "ph": policy_code_hash,
                "sup": supersedes_id, "au": auditor_identity, "er": exclusion_reason,
                "cf": conflict_set,
            },
        )
        return result.scalar_one()

    # ---------------- resolution clusters ----------------

    async def get_cluster(
        self, session: AsyncSession, *, cluster_key: str, cluster_version: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.resolution_clusters "
                "WHERE cluster_key=:k AND cluster_version=:v"
            ),
            {"k": cluster_key, "v": cluster_version},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def insert_cluster(
        self,
        session: AsyncSession,
        *,
        cluster_key: str,
        cluster_version: int,
        split: str,
        time_block_start: datetime,
        time_block_end: datetime,
        horizon: str,
        status: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.resolution_clusters "
                "(cluster_key, cluster_version, split, time_block_start, time_block_end, "
                " horizon, status) VALUES "
                "(:k, :v, :sp, :tbs, :tbe, :h, :st) "
                "ON CONFLICT (cluster_key, cluster_version) DO NOTHING RETURNING id"
            ),
            {"k": cluster_key, "v": cluster_version, "sp": split, "tbs": time_block_start,
             "tbe": time_block_end, "h": horizon, "st": status},
        )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return inserted
        existing = await self.get_cluster(session, cluster_key=cluster_key, cluster_version=cluster_version)
        if existing is None:
            raise RuntimeError("resolution_cluster_missing_after_insert")
        return existing["id"]

    async def insert_cluster_membership(
        self,
        session: AsyncSession,
        *,
        resolution_cluster_id: int,
        contract_spec_id: int,
        token_id: int,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.resolution_cluster_memberships "
                "(resolution_cluster_id, contract_spec_id, token_id) "
                "VALUES (:c, :cs, :t)"
            ),
            {"c": resolution_cluster_id, "cs": contract_spec_id, "t": token_id},
        )

    # ---------------- score targets ----------------

    async def insert_score_target(
        self,
        session: AsyncSession,
        *,
        target_key: str,
        target_type: str,
        contract_spec_id: int,
        resolution_cluster_id: int,
        horizon: str,
        target_weight: Any,
        payout_function_id: int | None,
        canonical_side: str | None,
        members: list | None,
        payout_type: str | None,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.score_targets "
                "(target_key, target_type, contract_spec_id, resolution_cluster_id, "
                " horizon, target_weight, payout_function_id, canonical_side, members, "
                " payout_type) VALUES "
                "(:k, :tt, :cs, :rc, :h, :tw, :pf, :csd, :m, :pt) "
                "ON CONFLICT (target_key) DO NOTHING RETURNING id"
            ).bindparams(bindparam("m", type_=JSONB())),
            {"k": target_key, "tt": target_type, "cs": contract_spec_id,
             "rc": resolution_cluster_id, "h": horizon, "tw": target_weight,
             "pf": payout_function_id,
             "csd": canonical_side, "m": members, "pt": payout_type},
        )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return inserted
        existing = await session.execute(
            text("SELECT id FROM trading.score_targets WHERE target_key=:k"),
            {"k": target_key},
        )
        return existing.scalar_one()

    async def insert_score_target_membership(
        self,
        session: AsyncSession,
        *,
        score_target_id: int,
        token_id: int,
        member_weight: Any,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.score_target_memberships "
                "(score_target_id, token_id, member_weight) VALUES (:t, :tk, :w)"
            ),
            {"t": score_target_id, "tk": token_id, "w": member_weight},
        )
