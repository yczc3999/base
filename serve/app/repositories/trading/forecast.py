"""Forecast / evidence Repository（WP-02 Checkpoint A）。

只拥有 SQL：prior、evidence coverage policy、evidence revision、bundle、bundle item、
forecast input manifest、submission、payout projection、coherence check、challenge、lease，
以及 forecast_episodes cognition 状态推进。绝不 commit、不调用网络、不做概率判断。
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


def _bind(object_column: str, *params: str):
    return text


class ForecastRepository:
    """forecast/evidence SQL；不持有状态。"""

    # ---------------- prior ----------------

    async def insert_prior(
        self,
        session: AsyncSession,
        *,
        episode_id: int,
        version_no: int,
        reference_class: str | None,
        hazard_ref: str | None,
        applicability: dict,
        sample_rule: dict,
        width: dict,
        failure_conditions: dict,
        market_blind_declaration: bool,
        content: dict,
        content_hash: str,
        status: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.priors "
                "(episode_id, version_no, reference_class, hazard_ref, applicability, sample_rule, "
                " width, failure_conditions, market_blind_declaration, content, content_hash, status) "
                "VALUES (:e, :v, :rc, :hr, :a, :sr, :w, :fc, :m, :c, :ch, :st) RETURNING id"
            ).bindparams(bindparam("a", type_=JSONB()), bindparam("sr", type_=JSONB()),
                         bindparam("w", type_=JSONB()), bindparam("fc", type_=JSONB()),
                         bindparam("c", type_=JSONB())),
            {
                "e": episode_id, "v": version_no, "rc": reference_class, "hr": hazard_ref,
                "a": applicability, "sr": sample_rule, "w": width, "fc": failure_conditions,
                "m": market_blind_declaration, "c": content, "ch": content_hash, "st": status,
            },
        )
        return result.scalar_one()

    async def get_prior(
        self, session: AsyncSession, *, episode_id: int, version_no: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.priors WHERE episode_id=:e AND version_no=:v"
            ),
            {"e": episode_id, "v": version_no},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_active_prior(
        self, session: AsyncSession, episode_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.priors "
                "WHERE episode_id=:e AND status='active' ORDER BY version_no DESC LIMIT 1"
            ),
            {"e": episode_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def latest_prior_version(
        self, session: AsyncSession, episode_id: int
    ) -> int:
        result = await session.execute(
            text(
                "SELECT COALESCE(MAX(version_no),0) FROM trading.priors WHERE episode_id=:e"
            ),
            {"e": episode_id},
        )
        return result.scalar_one()

    # ---------------- evidence coverage policy ----------------

    async def insert_evidence_coverage_policy(
        self,
        session: AsyncSession,
        *,
        cohort_id: int,
        policy_version: int,
        content: dict,
        content_hash: str,
        status: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.evidence_coverage_policies "
                "(cohort_id, policy_version, content, content_hash, status) "
                "VALUES (:c, :v, :content, :ch, :st) RETURNING id"
            ).bindparams(bindparam("content", type_=JSONB())),
            {
                "c": cohort_id, "v": policy_version, "content": content,
                "ch": content_hash, "st": status,
            },
        )
        return result.scalar_one()

    async def get_evidence_coverage_policy(
        self, session: AsyncSession, *, cohort_id: int, policy_version: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.evidence_coverage_policies "
                "WHERE cohort_id=:c AND policy_version=:v"
            ),
            {"c": cohort_id, "v": policy_version},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    # ---------------- evidence revision ----------------

    async def insert_evidence_revision(
        self,
        session: AsyncSession,
        *,
        episode_id: int,
        revision_key: str,
        kind: str,
        event_at: datetime,
        published_at: datetime,
        observed_at: datetime,
        ingested_at: datetime,
        source: str,
        source_type: str,
        branch: str,
        prev_revision_id: int | None,
        raw_artifact_id: int,
        content: dict,
        content_hash: str,
        taint_status: str,
        market_conditioned_discovery: bool,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.evidence_revisions "
                "(episode_id, revision_key, kind, event_at, published_at, observed_at, ingested_at, "
                " source, source_type, branch, prev_revision_id, raw_artifact_id, content, "
                " content_hash, taint_status, market_conditioned_discovery) "
                "VALUES (:e, :rk, :k, :ea, :pa, :oa, :ia, :src, :st, :br, :prev, :art, :c, "
                "        :ch, :ts, :mcd) RETURNING id"
            ).bindparams(bindparam("c", type_=JSONB())),
            {
                "e": episode_id, "rk": revision_key, "k": kind,
                "ea": event_at, "pa": published_at, "oa": observed_at, "ia": ingested_at,
                "src": source, "st": source_type, "br": branch, "prev": prev_revision_id,
                "art": raw_artifact_id, "c": content, "ch": content_hash,
                "ts": taint_status, "mcd": market_conditioned_discovery,
            },
        )
        return result.scalar_one()

    async def get_evidence_revision(
        self, session: AsyncSession, *, episode_id: int, revision_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.evidence_revisions "
                "WHERE episode_id=:e AND revision_key=:rk"
            ),
            {"e": episode_id, "rk": revision_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def evidence_revisions(
        self, session: AsyncSession, episode_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT * FROM trading.evidence_revisions "
                "WHERE episode_id=:e ORDER BY ingested_at, revision_key"
            ),
            {"e": episode_id},
        )
        return _rows(result)

    async def revision_id_by_key(
        self, session: AsyncSession, *, episode_id: int, revision_key: str
    ) -> int | None:
        result = await session.execute(
            text(
                "SELECT id FROM trading.evidence_revisions "
                "WHERE episode_id=:e AND revision_key=:rk"
            ),
            {"e": episode_id, "rk": revision_key},
        )
        return result.scalar_one_or_none()

    # ---------------- evidence bundle ----------------

    async def insert_evidence_bundle(
        self,
        session: AsyncSession,
        *,
        episode_id: int,
        bundle_key: str,
        information_cutoff_at: datetime,
        bundle_hash: str,
        status: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.evidence_bundles "
                "(episode_id, bundle_key, information_cutoff_at, bundle_hash, status) "
                "VALUES (:e, :k, :cut, :h, :st) RETURNING id"
            ),
            {"e": episode_id, "k": bundle_key, "cut": information_cutoff_at, "h": bundle_hash, "st": status},
        )
        return result.scalar_one()

    async def get_evidence_bundle(
        self, session: AsyncSession, episode_id: int, bundle_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.evidence_bundles "
                "WHERE episode_id=:e AND bundle_key=:k"
            ),
            {"e": episode_id, "k": bundle_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_frozen_bundle(
        self, session: AsyncSession, *, episode_id: int
    ) -> dict[str, Any] | None:
        """当前 frozen as-of bundle（每个 episode 一个 frozen bundle）。"""
        result = await session.execute(
            text(
                "SELECT * FROM trading.evidence_bundles "
                "WHERE episode_id=:e AND status='frozen' ORDER BY id DESC LIMIT 1"
            ),
            {"e": episode_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def insert_evidence_bundle_items(
        self,
        session: AsyncSession,
        *,
        bundle_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        if not rows:
            return
        result = await session.execute(
            text(
                "INSERT INTO trading.evidence_bundle_items "
                "(bundle_id, revision_id, item_no, eligible, eligibility_reason) "
                "SELECT :bundle, revision_id, item_no, eligible, eligibility_reason "
                "FROM jsonb_to_recordset(:rows) AS x("
                " revision_id bigint, item_no integer, eligible boolean, eligibility_reason text)"
            ).bindparams(bindparam("rows", type_=JSONB())),
            {"bundle": bundle_id, "rows": rows},
        )
        if result.rowcount not in (-1, len(rows)):
            raise RuntimeError("evidence_bundle_items_partial")

    # ---------------- forecast input manifest ----------------

    async def insert_forecast_input_manifest(
        self,
        session: AsyncSession,
        *,
        episode_id: int,
        manifest_key: str,
        manifest_hash: str,
        evidence_bundle_hash: str,
        contract_spec_set_hash: str,
        world_schema_hash: str,
        prior_hash: str,
        taxonomy_hash: str,
        model_binding_hash: str,
        prompt_hash: str,
        code_hash: str,
        content: dict,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.forecast_input_manifests "
                "(episode_id, manifest_key, manifest_hash, evidence_bundle_hash, "
                " contract_spec_set_hash, world_schema_hash, prior_hash, taxonomy_hash, "
                " model_binding_hash, prompt_hash, code_hash, content) "
                "VALUES (:e, :mk, :mh, :eb, :cs, :ws, :p, :t, :mb, :pr, :c, :content) "
                "RETURNING id"
            ).bindparams(bindparam("content", type_=JSONB())),
            {
                "e": episode_id, "mk": manifest_key, "mh": manifest_hash, "eb": evidence_bundle_hash,
                "cs": contract_spec_set_hash, "ws": world_schema_hash, "p": prior_hash,
                "t": taxonomy_hash, "mb": model_binding_hash, "pr": prompt_hash, "c": code_hash,
                "content": content,
            },
        )
        return result.scalar_one()

    # ---------------- forecast submission ----------------

    async def insert_forecast_submission(
        self,
        session: AsyncSession,
        *,
        episode_id: int,
        submission_key: str,
        Q: dict,
        U: list,
        forecast_input_manifest_id: int,
        contract_schema_prior_evidence_hash: str,
        algorithm_hash: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.forecast_submissions "
                "(episode_id, submission_key, status, Q, U, forecast_input_manifest_id, "
                " contract_schema_prior_evidence_hash, algorithm_hash) "
                "VALUES (:e, :k, 'DRAFT', :q, :u, :m, :csp, :ah) RETURNING id"
            ).bindparams(bindparam("q", type_=JSONB()), bindparam("u", type_=JSONB())),
            {
                "e": episode_id, "k": submission_key, "q": Q, "u": U, "m": forecast_input_manifest_id,
                "csp": contract_schema_prior_evidence_hash, "ah": algorithm_hash,
            },
        )
        return result.scalar_one()

    async def commit_submission(
        self, session: AsyncSession, submission_id: int, *, committed_at: datetime
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.forecast_submissions SET status='BLIND_COMMITTED', committed_at=:c "
                "WHERE id=:s AND status='DRAFT'"
            ),
            {"s": submission_id, "c": committed_at},
        )
        if result.rowcount == 1:
            return True
        existing = await self.get_submission(session, submission_id)
        return bool(
            existing
            and existing["status"] == "BLIND_COMMITTED"
            and existing["committed_at"] is not None
        )

    async def get_submission(
        self, session: AsyncSession, submission_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.forecast_submissions WHERE id=:s"),
            {"s": submission_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def submission_count_committed(
        self, session: AsyncSession, episode_id: int
    ) -> int:
        result = await session.execute(
            text(
                "SELECT count(*) FROM trading.forecast_submissions "
                "WHERE episode_id=:e AND status='BLIND_COMMITTED'"
            ),
            {"e": episode_id},
        )
        return result.scalar_one()

    # ---------------- payout projection ----------------

    async def insert_payout_projections(
        self,
        session: AsyncSession,
        *,
        submission_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        if not rows:
            return
        result = await session.execute(
            text(
                "INSERT INTO trading.payout_projections "
                "(submission_id, contract_spec_id, pm_token_id, mu, v, u_lower, u_upper, "
                " p_blind, algorithm_hash, h_c_hash, g_hash) "
                "SELECT :sub, contract_spec_id, pm_token_id, mu, v, u_lower, u_upper, "
                "       p_blind, algorithm_hash, h_c_hash, g_hash "
                "FROM jsonb_to_recordset(:rows) AS x("
                " contract_spec_id bigint, pm_token_id bigint, mu jsonb, v numeric, "
                " u_lower numeric, u_upper numeric, p_blind numeric, algorithm_hash text, "
                " h_c_hash text, g_hash text)"
            ).bindparams(bindparam("rows", type_=JSONB())),
            {"sub": submission_id, "rows": rows},
        )
        if result.rowcount not in (-1, len(rows)):
            raise RuntimeError("payout_projections_partial")

    async def projections_for_submission(
        self, session: AsyncSession, submission_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT * FROM trading.payout_projections WHERE submission_id=:s "
                "ORDER BY contract_spec_id, pm_token_id"
            ),
            {"s": submission_id},
        )
        return _rows(result)

    # ---------------- coherence check ----------------

    async def insert_coherence_checks(
        self,
        session: AsyncSession,
        *,
        submission_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        if not rows:
            return
        result = await session.execute(
            text(
                "INSERT INTO trading.coherence_checks "
                "(submission_id, check_name, passed, severity, reason_code, details_artifact_hash) "
                "SELECT :sub, check_name, passed, severity, reason_code, details_artifact_hash "
                "FROM jsonb_to_recordset(:rows) AS x("
                " check_name text, passed boolean, severity text, reason_code text, "
                " details_artifact_hash text)"
            ).bindparams(bindparam("rows", type_=JSONB())),
            {"sub": submission_id, "rows": rows},
        )
        if result.rowcount not in (-1, len(rows)):
            raise RuntimeError("coherence_checks_partial")

    # ---------------- challenge / lease ----------------

    async def insert_forecast_challenge(
        self,
        session: AsyncSession,
        *,
        episode_id: int,
        challenge_key: str,
        challenger_role: str,
        status: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.forecast_challenges "
                "(episode_id, challenge_key, challenger_role, status) "
                "VALUES (:e, :k, :r, :st) RETURNING id"
            ),
            {"e": episode_id, "k": challenge_key, "r": challenger_role, "st": status},
        )
        return result.scalar_one()

    async def insert_forecast_lease(
        self,
        session: AsyncSession,
        *,
        submission_id: int,
        valid_until: datetime,
        invalidation_conditions: dict,
        evidence_hash: str,
        schema_hash: str,
        spec_hash: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.forecast_leases "
                "(submission_id, valid_until, invalidation_conditions, evidence_hash, schema_hash, spec_hash) "
                "VALUES (:s, :vu, :ic, :eh, :sh, :sp) RETURNING id"
            ).bindparams(bindparam("ic", type_=JSONB())),
            {
                "s": submission_id, "vu": valid_until, "ic": invalidation_conditions,
                "eh": evidence_hash, "sh": schema_hash, "sp": spec_hash,
            },
        )
        return result.scalar_one()

    # ---------------- episode cognition progression ----------------

    async def mark_episode_prior_ready(
        self, session: AsyncSession, episode_id: int, *, prior_frozen_at: datetime
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.forecast_episodes "
                "SET cognition_status='PRIOR_READY', prior_frozen_at=:t "
                "WHERE id=:e AND cognition_status='PENDING' AND status='ROUTED'"
            ),
            {"e": episode_id, "t": prior_frozen_at},
        )
        if result.rowcount == 1:
            return True
        existing = await self.get_episode(session, episode_id)
        return bool(
            existing
            and existing["cognition_status"] == "PRIOR_READY"
            and existing["prior_frozen_at"] is not None
        )

    async def mark_episode_evidence_ready(
        self, session: AsyncSession, episode_id: int, *, evidence_bundle_at: datetime
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.forecast_episodes "
                "SET cognition_status='EVIDENCE_READY', evidence_bundle_at=:t "
                "WHERE id=:e AND cognition_status='PRIOR_READY' AND status='ROUTED'"
            ),
            {"e": episode_id, "t": evidence_bundle_at},
        )
        if result.rowcount == 1:
            return True
        existing = await self.get_episode(session, episode_id)
        return bool(
            existing
            and existing["cognition_status"] == "EVIDENCE_READY"
            and existing["evidence_bundle_at"] is not None
        )

    async def mark_episode_committed(
        self, session: AsyncSession, episode_id: int, *, committed_at: datetime
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.forecast_episodes "
                "SET cognition_status='COMMITTED', forecast_committed_at=:t, status='BLIND_COMMITTED' "
                "WHERE id=:e AND cognition_status='EVIDENCE_READY' AND status='ROUTED'"
            ),
            {"e": episode_id, "t": committed_at},
        )
        if result.rowcount == 1:
            return True
        existing = await self.get_episode(session, episode_id)
        return bool(
            existing
            and existing["cognition_status"] == "COMMITTED"
            and existing["status"] == "BLIND_COMMITTED"
        )

    async def get_episode(self, session: AsyncSession, episode_id: int) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.forecast_episodes WHERE id=:e"),
            {"e": episode_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def episode_cognition_chain(
        self, session: AsyncSession, episode_id: int
    ) -> dict[str, Any] | None:
        """Episode + opportunity + component + world schema + spec members（G4-G6 冻结材料）。"""
        result = await session.execute(
            text(
                "SELECT fe.id AS episode_id, fe.episode_key, fe.status, fe.cognition_status, "
                "       fe.cutoff_at, fe.horizon, fe.experiment_variant, "
                "       fe.component_version_id, fe.strategy_version_id, fe.objective_contract_id, "
                "       o.cohort_id, cohort.release_manifest_id, "
                "       cv.world_schema_version_id, cv.content_hash AS component_version_hash, "
                "       ws.content_hash AS world_schema_hash, "
                "       ws.world_states, ws.h_c AS schema_hc, "
                "       c.content_hash AS strategy_hash, oc.content_hash AS objective_hash "
                "FROM trading.forecast_episodes fe "
                "JOIN trading.decision_opportunities o ON o.id=fe.decision_opportunity_id "
                "JOIN trading.evaluation_cohorts cohort ON cohort.id=o.cohort_id "
                "JOIN trading.forecast_component_versions cv ON cv.id=fe.component_version_id "
                "JOIN trading.world_schema_versions ws ON ws.id=cv.world_schema_version_id "
                "JOIN trading.strategy_versions c ON c.id=fe.strategy_version_id "
                "JOIN trading.strategy_objective_contracts oc ON oc.id=fe.objective_contract_id "
                "WHERE fe.id=:e"
            ),
            {"e": episode_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def spec_members_for_component(
        self, session: AsyncSession, component_version_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT m.contract_spec_id, m.h_c, s.content_hash AS spec_hash, "
                "       s.kc_resolution_states, s.token_ids, s.token_count "
                "FROM trading.forecast_component_contract_specs m "
                "JOIN trading.contract_specs s ON s.id=m.contract_spec_id AND s.status='pass' "
                "WHERE m.component_version_id=:cv "
                "ORDER BY s.content_hash, m.contract_spec_id"
            ),
            {"cv": component_version_id},
        )
        return _rows(result)

    async def payouts_for_spec(
        self, session: AsyncSession, contract_spec_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT pm_token_id, token_version_id, outcome_index, function_ir, "
                "       algorithm_hash, content_hash "
                "FROM trading.payout_functions WHERE contract_spec_id=:s "
                "ORDER BY outcome_index, pm_token_id"
            ),
            {"s": contract_spec_id},
        )
        return _rows(result)

    async def cohort_coverage_policy(
        self, session: AsyncSession, cohort_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.evidence_coverage_policies "
                "WHERE cohort_id=:c AND status='active' ORDER BY policy_version DESC LIMIT 1"
            ),
            {"c": cohort_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def cohort_release_binding(
        self, session: AsyncSession, cohort_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT c.cohort_key, c.release_manifest_id, c.policy_hashes "
                "FROM trading.evaluation_cohorts c WHERE c.id=:c"
            ),
            {"c": cohort_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None
