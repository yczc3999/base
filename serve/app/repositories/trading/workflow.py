"""Workflow Repository（WP-01C Checkpoint C）。

只拥有 SQL：opportunity（parent/child）、opportunity market、episode、episode membership、
episode contract-spec、gate decision、information snapshot。绝不 commit、不调用网络。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class WorkflowRepository:
    """workflow SQL；不持有状态。"""

    async def insert_opportunity(
        self,
        session: AsyncSession,
        *,
        opportunity_key: str,
        cohort_id: int,
        parent_id: int | None,
        chain_type: str,
        objective_contract_id: int,
        strategy_version_id: int,
        source_screening_episode_id: int | None,
        triggered_at: datetime,
        audit_tag: str | None = None,
        g0_manifest_hash: str | None = None,
    ) -> int:
        params = {
            "k": opportunity_key,
            "c": cohort_id,
            "p": parent_id,
            "ct": chain_type,
            "obj": objective_contract_id,
            "strat": strategy_version_id,
            "sse": source_screening_episode_id,
            "t": triggered_at,
            "at": audit_tag,
            "g0": g0_manifest_hash,
        }
        result = await session.execute(
            text(
                "INSERT INTO trading.decision_opportunities "
                "(opportunity_key, cohort_id, parent_id, chain_type, objective_contract_id, "
                " strategy_version_id, source_screening_episode_id, triggered_at, audit_tag, "
                " g0_manifest_hash) "
                "VALUES (:k, :c, :p, :ct, :obj, :strat, :sse, :t, :at, :g0) "
                "ON CONFLICT (opportunity_key) DO NOTHING RETURNING id"
            ),
            params,
        )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return inserted
        existing_result = await session.execute(
            text("SELECT * FROM trading.decision_opportunities WHERE opportunity_key=:k"),
            {"k": opportunity_key},
        )
        existing = _rows(existing_result)[0]
        expected = {
            "cohort_id": cohort_id,
            "parent_id": parent_id,
            "chain_type": chain_type,
            "objective_contract_id": objective_contract_id,
            "strategy_version_id": strategy_version_id,
            "source_screening_episode_id": source_screening_episode_id,
            "triggered_at": triggered_at,
            "audit_tag": audit_tag,
            "g0_manifest_hash": g0_manifest_hash,
        }
        for key, value in expected.items():
            if existing[key] != value:
                raise RuntimeError(f"opportunity_idempotency_conflict:{key}")
        return existing["id"]

    async def insert_opportunity_market(
        self, session: AsyncSession, *, opportunity_id: int, market_id: int
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.decision_opportunity_markets (opportunity_id, market_id) "
                "VALUES (:o, :m) ON CONFLICT (opportunity_id, market_id) DO NOTHING"
            ),
            {"o": opportunity_id, "m": market_id},
        )

    async def terminal_opportunity(
        self, session: AsyncSession, opportunity_id: int, *, terminal_reason: str, disposition: str
    ) -> bool:
        """OPEN → PRE_COMMIT_TERMINAL（DB guard 只允许终态 transition）。"""
        result = await session.execute(
            text(
                "UPDATE trading.decision_opportunities "
                "SET status='PRE_COMMIT_TERMINAL', terminal_reason=:r, disposition=:d "
                "WHERE id=:o AND status='OPEN'"
            ),
            {"o": opportunity_id, "r": terminal_reason, "d": disposition},
        )
        if result.rowcount == 1:
            return True
        existing = await self.get_opportunity(session, opportunity_id)
        return bool(
            existing
            and existing["status"] == "PRE_COMMIT_TERMINAL"
            and existing["terminal_reason"] == terminal_reason
            and existing["disposition"] == disposition
        )

    async def route_opportunity(
        self, session: AsyncSession, opportunity_id: int
    ) -> bool:
        """OPEN → ROUTED（G2 PASS 后创建 episode 时）。"""
        result = await session.execute(
            text(
                "UPDATE trading.decision_opportunities SET status='ROUTED', disposition='completed' "
                "WHERE id=:o AND status='OPEN'"
            ),
            {"o": opportunity_id},
        )
        if result.rowcount == 1:
            return True
        existing = await self.get_opportunity(session, opportunity_id)
        return bool(
            existing
            and existing["status"] == "ROUTED"
            and existing["disposition"] == "completed"
        )

    async def get_opportunity(self, session: AsyncSession, opportunity_id: int) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.decision_opportunities WHERE id=:o"),
            {"o": opportunity_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_screening_chain(
        self, session: AsyncSession, screening_episode_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT se.*, c.cohort_key, c.status AS cohort_status, "
                "       c.objective_contract_id AS cohort_objective_contract_id, "
                "       c.strategy_version_id AS cohort_strategy_version_id, "
                "       c.release_manifest_id, c.seed_hash, c.policy_hashes "
                "FROM trading.screening_episodes se "
                "JOIN trading.evaluation_cohorts c ON c.id=se.cohort_id "
                "WHERE se.id=:s"
            ),
            {"s": screening_episode_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_gate_decision(
        self,
        session: AsyncSession,
        *,
        gate: str,
        target_kind: str,
        target_id: int,
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.gate_decisions "
                "WHERE gate=:g AND target_kind=:tk AND target_id=:tid"
            ),
            {"g": gate, "tk": target_kind, "tid": target_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_opportunity_lineage(
        self, session: AsyncSession, opportunity_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT child.*, parent.opportunity_key AS parent_key, "
                "       parent.source_screening_episode_id AS parent_screening_id, "
                "       parent.audit_tag AS parent_audit_tag, parent.chain_type AS parent_chain_type, "
                "       cohort.release_manifest_id AS cohort_release_manifest_id, "
                "       cohort.policy_hashes AS cohort_policy_hashes "
                "FROM trading.decision_opportunities child "
                "LEFT JOIN trading.decision_opportunities parent ON parent.id=child.parent_id "
                "JOIN trading.evaluation_cohorts cohort ON cohort.id=child.cohort_id "
                "WHERE child.id=:o"
            ),
            {"o": opportunity_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def opportunity_market_ids(
        self, session: AsyncSession, opportunity_id: int
    ) -> list[int]:
        result = await session.execute(
            text(
                "SELECT market_id FROM trading.decision_opportunity_markets "
                "WHERE opportunity_id=:o ORDER BY market_id"
            ),
            {"o": opportunity_id},
        )
        return [row[0] for row in result.fetchall()]

    async def market_key(self, session: AsyncSession, market_id: int) -> str | None:
        result = await session.execute(
            text("SELECT gamma_market_id FROM trading.pm_markets WHERE id=:m"),
            {"m": market_id},
        )
        return result.scalar_one_or_none()

    async def episode_binding(
        self,
        session: AsyncSession,
        *,
        opportunity_id: int,
        component_version_id: int,
    ) -> dict[str, Any] | None:
        """从 DB 冻结对象读取 episode key 材料，拒绝调用方自报 semantic hashes。"""

        result = await session.execute(
            text(
                "SELECT o.opportunity_key, o.cohort_id, o.parent_id, o.chain_type, "
                "       o.objective_contract_id, o.strategy_version_id, o.status AS opportunity_status, "
                "       p.audit_tag AS parent_audit_tag, p.chain_type AS parent_chain_type, "
                "       cv.content_hash AS component_version_hash, cv.status AS component_version_status, "
                "       fc.component_key, ws.status AS schema_status, "
                "       sv.content_hash AS strategy_hash, oc.content_hash AS objective_hash "
                "FROM trading.decision_opportunities o "
                "LEFT JOIN trading.decision_opportunities p ON p.id=o.parent_id "
                "JOIN trading.forecast_component_versions cv ON cv.id=:cv "
                "JOIN trading.forecast_components fc ON fc.id=cv.component_id "
                "JOIN trading.world_schema_versions ws ON ws.id=cv.world_schema_version_id "
                "                                   AND ws.component_id=cv.component_id "
                "JOIN trading.strategy_versions sv ON sv.id=o.strategy_version_id "
                "JOIN trading.strategy_objective_contracts oc ON oc.id=o.objective_contract_id "
                "WHERE o.id=:o"
            ),
            {"o": opportunity_id, "cv": component_version_id},
        )
        rows = _rows(result)
        if not rows:
            return None
        binding = rows[0]
        members_result = await session.execute(
            text(
                "SELECT m.contract_spec_id, s.content_hash AS spec_hash "
                "FROM trading.forecast_component_contract_specs m "
                "JOIN trading.contract_specs s ON s.id=m.contract_spec_id AND s.status='pass' "
                "WHERE m.component_version_id=:cv "
                "ORDER BY s.content_hash, m.contract_spec_id"
            ),
            {"cv": component_version_id},
        )
        members = _rows(members_result)
        binding["contract_spec_ids"] = [row["contract_spec_id"] for row in members]
        binding["spec_hashes"] = [row["spec_hash"] for row in members]
        return binding

    async def insert_episode(
        self,
        session: AsyncSession,
        *,
        episode_key: str,
        decision_opportunity_id: int,
        component_version_id: int,
        strategy_version_id: int,
        objective_contract_id: int,
        trigger: str,
        cutoff_at: datetime,
        horizon: str,
        experiment_variant: str,
    ) -> int:
        params = {
            "k": episode_key,
            "opp": decision_opportunity_id,
            "cv": component_version_id,
            "strat": strategy_version_id,
            "obj": objective_contract_id,
            "trg": trigger,
            "cut": cutoff_at,
            "hor": horizon,
            "ev": experiment_variant,
        }
        # BEFORE INSERT validates that the opportunity is OPEN, so an
        # ``INSERT .. ON CONFLICT`` retry after the first transaction routed the
        # opportunity would fail before conflict resolution.  Serialize by the
        # stable key and read the immutable identity before attempting INSERT.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 1103))"),
            {"k": episode_key},
        )
        existing_result = await session.execute(
            text("SELECT * FROM trading.forecast_episodes WHERE episode_key=:k"),
            {"k": episode_key},
        )
        existing_rows = _rows(existing_result)
        if existing_rows:
            existing = existing_rows[0]
            expected = {
                "decision_opportunity_id": decision_opportunity_id,
                "component_version_id": component_version_id,
                "strategy_version_id": strategy_version_id,
                "objective_contract_id": objective_contract_id,
                "trigger": trigger,
                "cutoff_at": cutoff_at,
                "horizon": horizon,
                "experiment_variant": experiment_variant,
            }
            for key, value in expected.items():
                if existing[key] != value:
                    raise RuntimeError(f"episode_idempotency_conflict:{key}")
            return existing["id"]
        result = await session.execute(
            text(
                "INSERT INTO trading.forecast_episodes "
                "(episode_key, decision_opportunity_id, component_version_id, strategy_version_id, "
                " objective_contract_id, trigger, cutoff_at, horizon, experiment_variant) "
                "VALUES (:k, :opp, :cv, :strat, :obj, :trg, :cut, :hor, :ev) "
                "RETURNING id"
            ),
            params,
        )
        return result.scalar_one()

    async def episode_exists(self, session: AsyncSession, episode_key: str) -> bool:
        result = await session.execute(
            text("SELECT 1 FROM trading.forecast_episodes WHERE episode_key=:k"),
            {"k": episode_key},
        )
        return result.first() is not None

    async def get_episode(
        self, session: AsyncSession, episode_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.forecast_episodes WHERE id=:e"),
            {"e": episode_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def mark_episode_routed(self, session: AsyncSession, episode_id: int) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.forecast_episodes SET status='ROUTED' "
                "WHERE id=:e AND status='DRAFT'"
            ),
            {"e": episode_id},
        )
        if result.rowcount == 1:
            return True
        existing = await self.get_episode(session, episode_id)
        return bool(existing and existing["status"] == "ROUTED")

    async def insert_episode_spec(
        self, session: AsyncSession, *, episode_id: int, contract_spec_id: int
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.episode_contract_specs (episode_id, contract_spec_id) "
                "VALUES (:e, :s) ON CONFLICT (episode_id, contract_spec_id) DO NOTHING"
            ),
            {"e": episode_id, "s": contract_spec_id},
        )

    async def insert_episode_membership(
        self,
        session: AsyncSession,
        *,
        episode_id: int,
        route_channel: str,
        first_rejected_gate: str | None,
        reason_code: str | None,
        recheck_at: datetime | None,
        recheck_condition: str | None,
        processing_disposition: str,
        action_eligible: bool,
        qualification_eligible: bool,
        capital_evidence_eligible: bool,
        audit_selected: bool,
    ) -> None:
        params = {
            "e": episode_id,
            "rc": route_channel,
            "frg": first_rejected_gate,
            "rcode": reason_code,
            "ra": recheck_at,
            "rcond": recheck_condition,
            "pd": processing_disposition,
            "ae": action_eligible,
            "qe": qualification_eligible,
            "ce": capital_evidence_eligible,
            "as": audit_selected,
        }
        result = await session.execute(
            text(
                "INSERT INTO trading.episode_memberships "
                "(episode_id, route_channel, first_rejected_gate, reason_code, recheck_at, "
                " recheck_condition, processing_disposition, action_eligible, "
                " qualification_eligible, capital_evidence_eligible, audit_selected) "
                "VALUES (:e, :rc, :frg, :rcode, :ra, :rcond, :pd, :ae, :qe, :ce, :as) "
                "ON CONFLICT (episode_id) DO NOTHING RETURNING id"
            ),
            params,
        )
        if result.scalar_one_or_none() is not None:
            return
        existing_result = await session.execute(
            text("SELECT * FROM trading.episode_memberships WHERE episode_id=:e"),
            {"e": episode_id},
        )
        existing = _rows(existing_result)[0]
        column_map = {
            "route_channel": route_channel,
            "first_rejected_gate": first_rejected_gate,
            "reason_code": reason_code,
            "recheck_at": recheck_at,
            "recheck_condition": recheck_condition,
            "processing_disposition": processing_disposition,
            "action_eligible": action_eligible,
            "qualification_eligible": qualification_eligible,
            "capital_evidence_eligible": capital_evidence_eligible,
            "audit_selected": audit_selected,
        }
        for key, value in column_map.items():
            if existing[key] != value:
                raise RuntimeError(f"episode_membership_idempotency_conflict:{key}")

    async def terminal_episode(
        self, session: AsyncSession, episode_id: int, *, drop_reason: str
    ) -> bool:
        """DRAFT/ROUTED → PRE_COMMIT_TERMINAL（WP-02：G6 失败也允许 ROUTED 终态）。"""
        result = await session.execute(
            text(
                "UPDATE trading.forecast_episodes SET status='PRE_COMMIT_TERMINAL', drop_reason=:r "
                "WHERE id=:e AND status IN ('DRAFT','ROUTED')"
            ),
            {"e": episode_id, "r": drop_reason},
        )
        if result.rowcount == 1:
            return True
        existing = await self.get_episode(session, episode_id)
        return bool(
            existing
            and existing["status"] == "PRE_COMMIT_TERMINAL"
            and existing["drop_reason"] == drop_reason
        )

    async def insert_gate_decision(
        self,
        session: AsyncSession,
        *,
        gate: str,
        target_kind: str,
        target_id: int,
        input_hash: str,
        policy_hash: str,
        version_manifest_id: int | None,
        result: str,
        reason_code: str | None,
        committed_at: datetime,
    ) -> dict[str, Any]:
        expected_target = {
            "G0": "screening",
            "R0": "screening",
            "G1": "opportunity",
            "G2": "opportunity",
            "R1": "episode",
            "G4": "episode",
            "G5A": "episode",
            "G5B": "episode",
            "G6": "episode",
        }.get(gate)
        if expected_target != target_kind:
            raise ValueError(f"gate_target_mismatch:{gate}:{target_kind}")
        result_row = await session.execute(
            text(
                "INSERT INTO trading.gate_decisions "
                "(gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, "
                " result, reason_code, committed_at) "
                "VALUES (:g, :tk, :tid, :ih, :ph, :vm, :res, :rc, :ca) "
                "ON CONFLICT (gate, target_id, target_kind) DO NOTHING RETURNING id"
            ),
            {
                "g": gate,
                "tk": target_kind,
                "tid": target_id,
                "ih": input_hash,
                "ph": policy_hash,
                "vm": version_manifest_id,
                "res": result,
                "rc": reason_code,
                "ca": committed_at,
            },
        )
        inserted = result_row.scalar_one_or_none()
        existing = await self.get_gate_decision(
            session, gate=gate, target_kind=target_kind, target_id=target_id
        )
        if existing is None:  # pragma: no cover
            raise RuntimeError("gate_decision_missing_after_insert")
        expected = {
            "input_hash": input_hash,
            "policy_hash": policy_hash,
            "version_manifest_id": version_manifest_id,
            "result": result,
            "reason_code": reason_code,
        }
        for key, value in expected.items():
            if existing[key] != value:
                raise RuntimeError(f"gate_decision_idempotency_conflict:{gate}:{key}")
        existing["created"] = inserted is not None
        return existing

    async def insert_gate_decisions_bulk(
        self,
        session: AsyncSession,
        *,
        gate: str,
        target_kind: str,
        rows: list[dict[str, Any]],
    ) -> None:
        """Set-based append of one Gate kind, preserving the same target boundary."""

        expected_target = {
            "G0": "screening",
            "R0": "screening",
            "G1": "opportunity",
            "G2": "opportunity",
            "R1": "episode",
            "G4": "episode",
            "G5A": "episode",
            "G5B": "episode",
            "G6": "episode",
        }.get(gate)
        if expected_target != target_kind:
            raise ValueError(f"gate_target_mismatch:{gate}:{target_kind}")
        if not rows:
            return
        result = await session.execute(
            text(
                "WITH payload AS ("
                " SELECT * FROM jsonb_to_recordset(:rows) AS x("
                " target_id bigint,input_hash text,policy_hash text,version_manifest_id bigint,"
                " result text,reason_code text,committed_at timestamptz)"
                ") "
                "INSERT INTO trading.gate_decisions "
                "(gate,target_kind,target_id,input_hash,policy_hash,version_manifest_id,"
                " result,reason_code,committed_at) "
                "SELECT :gate,:kind,target_id,input_hash,policy_hash,version_manifest_id,"
                "       result,reason_code,committed_at FROM payload "
                "ON CONFLICT (gate,target_id,target_kind) DO NOTHING"
            ).bindparams(bindparam("rows", type_=JSONB())),
            {"rows": rows, "gate": gate, "kind": target_kind},
        )
        if result.rowcount not in (-1, len(rows), 0):
            raise RuntimeError(f"gate_decision_bulk_partial:{gate}")

    async def insert_information_snapshot(
        self,
        session: AsyncSession,
        *,
        snapshot_key: str,
        episode_id: int | None,
        opportunity_id: int | None,
        gate: str,
        content: dict,
        content_hash: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.information_snapshots "
                "(snapshot_key, episode_id, opportunity_id, gate, content, content_hash) "
                "VALUES (:k, :e, :o, :g, :c, :ch) RETURNING id"
            ),
            {"k": snapshot_key, "e": episode_id, "o": opportunity_id, "g": gate, "c": content, "ch": content_hash},
        )
        return result.scalar_one()
