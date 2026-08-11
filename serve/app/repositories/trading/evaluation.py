"""Evaluation Repository（WP-04 Checkpoint B）。

只拥有 SQL：score observation、experiment、metric run、promotion decision 的辅助读写。
绝不 commit、不调用网络、不做业务判断。
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


class EvaluationRepository:
    """evaluation SQL；不持有状态。"""

    # ---------------- score observations ----------------

    async def insert_score_observation(
        self,
        session: AsyncSession,
        *,
        observation_key: str,
        score_target_id: int,
        submission_id: int,
        trade_decision_id: int | None,
        label_version_id: int,
        status: str,
        exclusion_reason: str | None,
        baseline_quote: Any,
        baseline_quote_binding_ids: list[int] | None,
        baseline_value: dict | None,
        baseline_value_hash: str | None,
        baseline_checkpoint_received_at: datetime | None,
        baseline_policy_hash: str,
        split: str,
        algorithm_hash: str,
        metric_id: str,
        score_value: Any,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.score_observations "
                "(observation_key, score_target_id, submission_id, trade_decision_id, "
                " label_version_id, status, exclusion_reason, baseline_quote, "
                " baseline_quote_binding_ids, baseline_value, "
                " baseline_value_hash, baseline_checkpoint_received_at, baseline_policy_hash, split, "
                " algorithm_hash, metric_id, score_value) VALUES "
                "(:k, :t, :s, :d, :lv, :status, :reason, :bq, :bqbs, :bv, :bvh, "
                " :bcrt, :bph, :sp, :ah, :mi, :sv) "
                "RETURNING id"
            ).bindparams(
                # EXCLUDED observations intentionally persist SQL NULL evidence;
                # PostgreSQL JSON ``null`` is a value and would violate the
                # disposition shape/check constraints.
                bindparam("bqbs", type_=JSONB(none_as_null=True)),
                bindparam("bv", type_=JSONB(none_as_null=True)),
            ),
            {
                "k": observation_key, "t": score_target_id, "s": submission_id, "d": trade_decision_id,
                "lv": label_version_id, "status": status, "reason": exclusion_reason,
                "bq": baseline_quote,
                "bqbs": baseline_quote_binding_ids,
                "bv": baseline_value,
                "bvh": baseline_value_hash,
                "bcrt": baseline_checkpoint_received_at,
                "bph": baseline_policy_hash,
                "sp": split, "ah": algorithm_hash, "mi": metric_id, "sv": score_value,
            },
        )
        return result.scalar_one()

    # ---------------- experiments ----------------

    async def insert_experiment(
        self,
        session: AsyncSession,
        *,
        experiment_key: str,
        hypothesis: str,
        hypothesis_hash: str,
        primary_metric: str,
        guardrails: dict,
        unique_change_field: str,
        champion_input_manifest_hash: str,
        challenger_input_manifest_hash: str,
        sample_policy: dict,
        stopping_rule: dict,
        seed: int,
        time_block_start: datetime,
        time_block_end: datetime,
        status: str = "PLANNED",
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.experiments "
                "(experiment_key, hypothesis, hypothesis_hash, primary_metric, guardrails, "
                " unique_change_field, champion_input_manifest_hash, "
                " challenger_input_manifest_hash, sample_policy, stopping_rule, seed, "
                " time_block_start, time_block_end, status) VALUES "
                "(:k, :hyp, :hh, :pm, :gr, :ucf, :ch, :cl, :sp, :sr, :seed, :tbs, :tbe, :st) "
                "RETURNING id"
            ),
            {
                "k": experiment_key, "hyp": hypothesis, "hh": hypothesis_hash, "pm": primary_metric,
                "gr": guardrails, "ucf": unique_change_field, "ch": champion_input_manifest_hash,
                "cl": challenger_input_manifest_hash, "sp": sample_policy, "sr": stopping_rule,
                "seed": seed, "tbs": time_block_start, "tbe": time_block_end, "st": status,
            },
        )
        return result.scalar_one()

    async def insert_experiment_variant(
        self,
        session: AsyncSession,
        *,
        experiment_id: int,
        variant_key: str,
        variant_type: str,
        input_manifest_hash: str,
        strategy_version_id: int,
        release_manifest_id: int,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.experiment_variants "
                "(experiment_id, variant_key, variant_type, input_manifest_hash, "
                " strategy_version_id, release_manifest_id) VALUES "
                "(:e, :vk, :vt, :ih, :sv, :rm) RETURNING id"
            ),
            {"e": experiment_id, "vk": variant_key, "vt": variant_type, "ih": input_manifest_hash,
             "sv": strategy_version_id, "rm": release_manifest_id},
        )
        return result.scalar_one()

    async def insert_challenger_variant(
        self,
        session: AsyncSession,
        *,
        experiment_id: int,
        variant_key: str,
        challenger_type: str,
        changed_fields: dict,
        policy_hash: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.challenger_variants "
                "(experiment_id, variant_key, challenger_type, changed_fields, policy_hash) "
                "VALUES (:e, :vk, :ct, :cf, :ph) RETURNING id"
            ),
            {"e": experiment_id, "vk": variant_key, "ct": challenger_type,
             "cf": changed_fields, "ph": policy_hash},
        )
        return result.scalar_one()

    async def advance_experiment_status(
        self,
        session: AsyncSession,
        experiment_id: int,
        *,
        from_status: str,
        to_status: str,
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.experiments SET status=:to "
                "WHERE id=:id AND status=:from_status"
            ),
            {"id": experiment_id, "from_status": from_status, "to": to_status},
        )
        return result.rowcount == 1

    async def supersede_challenger_variant(
        self, session: AsyncSession, *, experiment_id: int, variant_key: str
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.challenger_variants SET status='SUPERSEDED' "
                "WHERE experiment_id=:experiment AND variant_key=:variant "
                "  AND status='ACTIVE'"
            ),
            {"experiment": experiment_id, "variant": variant_key},
        )
        return result.rowcount == 1

    # ---------------- metric runs ----------------

    async def insert_metric_run(
        self,
        session: AsyncSession,
        *,
        run_key: str,
        cohort_id: int,
        observation_ids: list[int],
        observation_set_hash: str,
        cohort_query_hash: str,
        strategy_version_id: int,
        release_manifest_id: int,
        label_versions: dict,
        split: str,
        time_blocks: dict,
        code_hash: str,
        config_hash: str,
        seed: int,
        n_market: int,
        n_episode: int,
        n_resolution_cluster: int,
        n_eff: Any,
        results: dict,
        ci: dict,
        artifact_hash: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.metric_runs "
                "(run_key, cohort_id, observation_ids, observation_set_hash, cohort_query_hash, "
                " strategy_version_id, release_manifest_id, "
                " label_versions, split, time_blocks, code_hash, config_hash, seed, "
                " n_market, n_episode, n_resolution_cluster, n_eff, results, ci, artifact_hash) "
                "VALUES "
                "(:k, :cohort, :oids, :osh, :cqh, :sv, :rm, :lv, :sp, :tb, :ch, :cgh, :seed, "
                " :nm, :ne, :nrc, :neff, :res, :ci, :ah) RETURNING id"
            ).bindparams(bindparam("oids", type_=JSONB())),
            {
                "k": run_key, "cohort": cohort_id, "oids": observation_ids,
                "osh": observation_set_hash, "cqh": cohort_query_hash, "sv": strategy_version_id,
                "rm": release_manifest_id, "lv": label_versions, "sp": split, "tb": time_blocks,
                "ch": code_hash, "cgh": config_hash, "seed": seed,
                "nm": n_market, "ne": n_episode, "nrc": n_resolution_cluster, "neff": n_eff,
                "res": results, "ci": ci, "ah": artifact_hash,
            },
        )
        return result.scalar_one()

    async def get_metric_run(self, session: AsyncSession, run_key: str) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.metric_runs WHERE run_key=:k"),
            {"k": run_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def advance_metric_run_status(
        self,
        session: AsyncSession,
        run_key: str,
        *,
        to_status: str,
        completed_at: datetime | None,
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.metric_runs SET status=:to, completed_at=:ca "
                "WHERE run_key=:k AND status='RUNNING'"
            ),
            {"k": run_key, "to": to_status, "ca": completed_at},
        )
        return result.rowcount == 1

    async def insert_error_review(
        self,
        session: AsyncSession,
        *,
        review_key: str,
        review_type: str,
        metric_run_id: int,
        observation_key: str,
        root_cause: str,
        root_cause_taxonomy: str,
        seed: int,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.error_reviews "
                "(review_key, review_type, metric_run_id, observation_key, root_cause, "
                " root_cause_taxonomy, seed) VALUES "
                "(:k, :rt, :m, :ok, :rc, :rct, :seed) RETURNING id"
            ),
            {"k": review_key, "rt": review_type, "m": metric_run_id, "ok": observation_key,
             "rc": root_cause, "rct": root_cause_taxonomy, "seed": seed},
        )
        return result.scalar_one()

    async def insert_ablation_run(
        self,
        session: AsyncSession,
        *,
        ablation_key: str,
        metric_run_id: int,
        bundle_hash: str,
        ablation_fields: dict,
        result_hash: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.ablation_runs "
                "(ablation_key, metric_run_id, bundle_hash, ablation_fields, result_hash) "
                "VALUES (:k, :m, :bh, :af, :rh) RETURNING id"
            ),
            {"k": ablation_key, "m": metric_run_id, "bh": bundle_hash,
             "af": ablation_fields, "rh": result_hash},
        )
        return result.scalar_one()

    # ---------------- promotion decisions ----------------

    async def insert_promotion_decision(
        self,
        session: AsyncSession,
        *,
        promotion_key: str,
        metric_run_id: int,
        promotion_type: str,
        from_ref: str,
        to_ref: str,
        evidence_manifest_hash: str,
        status: str,
        reason_code: str | None,
        future_effective_at: datetime | None,
        capital_amount: Any,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.promotion_decisions "
                "(promotion_key, metric_run_id, promotion_type, from_ref, to_ref, "
                " evidence_manifest_hash, status, reason_code, future_effective_at, "
                " capital_amount) VALUES "
                "(:k, :m, :pt, :fr, :tr, :eh, :st, :rc, :fea, :ca) RETURNING id"
            ),
            {
                "k": promotion_key, "m": metric_run_id, "pt": promotion_type,
                "fr": from_ref, "tr": to_ref, "eh": evidence_manifest_hash, "st": status,
                "rc": reason_code, "fea": future_effective_at, "ca": capital_amount,
            },
        )
        return result.scalar_one()
