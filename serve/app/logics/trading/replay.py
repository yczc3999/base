"""Replay Logic（WP-04 Checkpoint C）。

- ``replay_original``：原样重放（同 manifest+code+seed 重跑 hash 全等）。只读原
  artifact/snapshot/事实；输出新 ``replay_runs`` 行（``output_artifact_hash`` 非空）。
  绝不写回原 episode/submission/decision/execution/label/ledger。
- ``replay_new_code``：新 code/variant 写新 run（``replay_kind='new_code'|'variant'``），
  不覆盖原事实。
- ``ablation``：冻结 bundle ablation，写 ``ablation_runs``。
- ``error_review_selection``：top-loss/top-regret + 随机成功样本按冻结 seed 入
  ``error_reviews``（``deterministic_sample``）；root-cause taxonomy 只允许架构定义集合，
  非法值拒绝。

未来信息隔离：重放输入全部来自历史冻结快照/artifact，无未来 label/quote 污染。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash, deterministic_sample
from app.logics.trading.evaluation import (
    EvaluationLogic,
    _jsonable,
    metric_evidence_manifest,
)
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.evaluation import EvaluationRepository
from app.repositories.trading.settlement import SettlementRepository
from app.schemas.trading.evaluation import MetricRunInput

REPLAY_KINDS = ("original", "new_code", "variant")

# 架构定义 root-cause taxonomy（写死允许集合）。
ROOT_CAUSE_TAXONOMY = (
    "model_miscalibration",
    "selection_error",
    "data_quality",
    "timing",
    "edge_erosion",
    "regime_shift",
    "unexamined_success",
    "other",
)

# review_type → 默认 root_cause
_DEFAULT_ROOT_CAUSE = {
    "top_loss": "model_miscalibration",
    "top_regret": "selection_error",
    "random_success": "unexamined_success",
}


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


@dataclass(frozen=True)
class ReplayResult:
    ok: bool
    replay_run_id: int | None = None
    output_artifact_hash: str | None = None
    replay_kind: str | None = None
    idempotent: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class AblationResult:
    ok: bool
    ablation_id: int | None = None
    result_hash: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ErrorReviewResult:
    ok: bool
    count: int = 0
    reason: str | None = None


class ReplayLogic:
    """科学回放 / 消融 / 错误评审采样；只读原事实，只写新 artifact。"""

    def __init__(
        self,
        audit: AuditRepository | None = None,
        evaluation: EvaluationRepository | None = None,
    ) -> None:
        self._audit = audit or AuditRepository()
        self._evaluation = evaluation or EvaluationRepository()

    async def replay_original(
        self, uow: UnitOfWork, *, run_key: str, manifest_hash: str, seed: int
    ) -> ReplayResult:
        source = await self._source_for_manifest(uow, manifest_hash)
        if source is None:
            return ReplayResult(False, reason="replay_source_missing")
        if seed != int(source["seed"]):
            return ReplayResult(False, reason="replay_seed_mismatch")
        replay = await self._recompute_metric(uow, source)
        if not replay["ok"]:
            return ReplayResult(False, reason=replay["reason"])
        output_artifact_hash = replay["artifact_hash"]
        result = {
            "mode": "original",
            "manifest_hash": manifest_hash,
            "source_metric_run_id": int(source["id"]),
            "source_cutoff": source["completed_at"].isoformat(),
            "observation_set_hash": replay["observation_set_hash"],
            "observation_count": len(replay["observations"]),
            "capabilities": {"network": False, "search": False, "execution": False},
            "artifact": replay["artifact"],
            "exact_match": True,
        }
        try:
            inserted = await self._audit.insert_replay_run(
                uow.session,
                run_key=run_key,
                replay_kind="original",
                manifest_hash=manifest_hash,
                code_hash=source["code_hash"],
                seed=seed,
                input_artifact_hash=source["artifact_hash"],
                output_artifact_hash=output_artifact_hash,
                result=result,
            )
        except RuntimeError as exc:
            return ReplayResult(False, reason=str(exc))
        replay_run_id, created = inserted if isinstance(inserted, tuple) else (inserted, True)
        return ReplayResult(
            True,
            replay_run_id=replay_run_id,
            output_artifact_hash=output_artifact_hash,
            replay_kind="original",
            idempotent=not created,
        )

    async def replay_new_code(
        self,
        uow: UnitOfWork,
        *,
        run_key: str,
        manifest_hash: str,
        code_hash: str,
        seed: int,
        variant: str | None = None,
    ) -> ReplayResult:
        source = await self._source_for_manifest(uow, manifest_hash)
        if source is None:
            return ReplayResult(False, reason="replay_source_missing")
        if seed != int(source["seed"]):
            return ReplayResult(False, reason="replay_seed_mismatch")
        replay = await self._recompute_metric(uow, source)
        if not replay["ok"]:
            return ReplayResult(False, reason=replay["reason"])
        kind = "variant" if variant else "new_code"
        output_artifact_hash = canonical_hash(
            {
                "mode": kind,
                "source_artifact_hash": replay["artifact_hash"],
                "code_hash": code_hash,
                "seed": seed,
                "variant": variant,
                "artifact": replay["artifact"],
            }
        )
        result = {
            "mode": kind,
            "manifest_hash": manifest_hash,
            "source_metric_run_id": int(source["id"]),
            "source_cutoff": source["completed_at"].isoformat(),
            "observation_set_hash": replay["observation_set_hash"],
            "capabilities": {"network": False, "search": False, "execution": False},
            "variant": variant,
            "artifact": replay["artifact"],
        }
        try:
            inserted = await self._audit.insert_replay_run(
                uow.session,
                run_key=run_key,
                replay_kind=kind,
                manifest_hash=manifest_hash,
                code_hash=code_hash,
                seed=seed,
                input_artifact_hash=source["artifact_hash"],
                output_artifact_hash=output_artifact_hash,
                result=result,
            )
        except RuntimeError as exc:
            return ReplayResult(False, reason=str(exc))
        replay_run_id, created = inserted if isinstance(inserted, tuple) else (inserted, True)
        return ReplayResult(
            True,
            replay_run_id=replay_run_id,
            output_artifact_hash=output_artifact_hash,
            replay_kind=kind,
            idempotent=not created,
        )

    async def ablation(
        self,
        uow: UnitOfWork,
        *,
        ablation_key: str,
        metric_run_id: int,
        bundle_hash: str,
        fields: dict,
    ) -> AblationResult:
        run = await self._metric_run_by_id(uow, metric_run_id)
        if run is None or run["status"] != "COMPLETED":
            return AblationResult(False, reason="ablation_metric_run_not_completed")
        result_hash = canonical_hash(
            {"ablation": ablation_key, "bundle": bundle_hash, "fields": fields}
        )
        ablation_id = await self._evaluation.insert_ablation_run(
            uow.session,
            ablation_key=ablation_key,
            metric_run_id=metric_run_id,
            bundle_hash=bundle_hash,
            ablation_fields=fields,
            result_hash=result_hash,
        )
        return AblationResult(True, ablation_id=ablation_id, result_hash=result_hash)

    async def error_review_selection(
        self,
        uow: UnitOfWork,
        *,
        metric_run_id: int,
        seed: int,
        top_n: int = 3,
        explicit_taxonomies: dict[str, str] | None = None,
    ) -> ErrorReviewResult:
        run = await self._metric_run_by_id(uow, metric_run_id)
        if run is None or run["status"] != "COMPLETED":
            return ErrorReviewResult(False, reason="error_review_metric_run_not_completed")
        observations = await self._observations_for_run(uow, run)
        if not observations:
            return ErrorReviewResult(False, reason="error_review_no_observations")

        taxonomy_map = explicit_taxonomies or {}
        for observation_key, taxonomy in taxonomy_map.items():
            if taxonomy not in ROOT_CAUSE_TAXONOMY:
                return ErrorReviewResult(
                    False, reason=f"error_review_taxonomy_unknown:{taxonomy}"
                )

        seed_hash = canonical_hash(str(seed))
        selections: list[tuple[str, str, str]] = []

        # top-loss：score_value 降序 top_n。
        by_loss = sorted(
            observations, key=lambda row: Decimal(str(row["score_value"])), reverse=True
        )
        for row in by_loss[:top_n]:
            selections.append(
                (row["observation_key"], "top_loss", taxonomy_map.get(
                    row["observation_key"], _DEFAULT_ROOT_CAUSE["top_loss"]
                ))
            )

        # top-regret：selected（有 trade_decision_id）降序 top_n。
        selected = [row for row in observations if row.get("trade_decision_id") is not None]
        by_regret = sorted(
            selected, key=lambda row: Decimal(str(row["score_value"])), reverse=True
        )
        for row in by_regret[:top_n]:
            if row["observation_key"] not in {s[0] for s in selections}:
                selections.append(
                    (row["observation_key"], "top_regret", taxonomy_map.get(
                        row["observation_key"], _DEFAULT_ROOT_CAUSE["top_regret"]
                    ))
                )

        # random-success：低 loss 样本按冻结 seed 确定性抽样。
        values = [Decimal(str(row["score_value"])) for row in observations]
        threshold = sorted(values)[max(0, len(values) // 2)]
        success_pool = [
            row for row in observations
            if Decimal(str(row["score_value"])) <= threshold
        ]
        picked_success = 0
        for row in sorted(success_pool, key=lambda r: r["observation_key"]):
            selected_flag, _, _ = deterministic_sample(
                content_hash=canonical_hash(row["observation_key"]),
                seed_hash=seed_hash,
                stratum=f"random_success/{metric_run_id}",
                rate=Decimal("0.5"),
            )
            if selected_flag and row["observation_key"] not in {s[0] for s in selections}:
                selections.append(
                    (row["observation_key"], "random_success", taxonomy_map.get(
                        row["observation_key"], _DEFAULT_ROOT_CAUSE["random_success"]
                    ))
                )
                picked_success += 1
                if picked_success >= top_n:
                    break

        for observation_key, review_type, taxonomy in selections:
            review_key = canonical_hash(
                {"metric_run": metric_run_id, "obs": observation_key, "type": review_type}
            )
            await self._evaluation.insert_error_review(
                uow.session,
                review_key=review_key,
                review_type=review_type,
                metric_run_id=metric_run_id,
                observation_key=observation_key,
                root_cause=taxonomy,
                root_cause_taxonomy=taxonomy,
                seed=seed,
            )
        return ErrorReviewResult(True, count=len(selections))

    # ---------------- helpers ----------------

    async def _source_for_manifest(
        self, uow: UnitOfWork, manifest_hash: str
    ) -> dict | None:
        """Resolve only a frozen COMPLETED metric artifact.

        A previous replay output is deliberately never accepted as source: doing so
        would let a derived hash prove itself without touching the frozen facts.
        """
        return await self._audit.metric_run_by_artifact_hash(
            uow.session, manifest_hash
        )

    async def _recompute_metric(self, uow: UnitOfWork, source: dict) -> dict:
        """Rebuild scores and the five-layer metric artifact at its frozen cutoff."""
        cutoff = source.get("completed_at")
        if cutoff is None:
            return {"ok": False, "reason": "replay_source_cutoff_missing"}
        try:
            observations = await self._observations_for_replay(uow, source, cutoff)
        except RuntimeError as exc:
            return {"ok": False, "reason": str(exc)}
        decision_ids = sorted(
            {
                int(row["trade_decision_id"])
                for row in observations
                if row.get("trade_decision_id") is not None
            }
        )
        if await self._has_future_decision_suffix(uow, decision_ids, cutoff):
            return {"ok": False, "reason": "replay_future_fact_taint"}
        evaluator = EvaluationLogic(self._evaluation, SettlementRepository())
        for row in observations:
            if row.get("status") == "EXCLUDED":
                continue
            target = await evaluator._load_target(uow, int(row["score_target_id"]))
            if target is None:
                return {"ok": False, "reason": "replay_target_missing"}
            _, payouts, h_c = await evaluator._load_contract_material(
                uow, int(target["contract_spec_id"])
            )
            label = {
                "resolution_state": row["resolution_state"],
                "raw_outcome": row.get("raw_outcome"),
                "token_cashflow": row.get("token_cashflow"),
            }
            submission = {"q": row["q"]}
            try:
                score = evaluator._compute_metric(
                    target,
                    submission,
                    label,
                    payouts,
                    h_c,
                    metric_id=row["metric_id"],
                    baseline=(
                        Decimal(str(row["baseline_quote"]))
                        if row.get("baseline_quote") is not None
                        else Decimal("0.5")
                    ),
                )
            except (KeyError, ValueError) as exc:
                return {"ok": False, "reason": f"replay_score_invalid:{exc}"}
            if Decimal(str(row["score_value"])) != score:
                return {
                    "ok": False,
                    "reason": f"replay_score_mismatch:{row['observation_key']}",
                }
            row["score_value"] = score

        metric_input = MetricRunInput(
            run_key=source["run_key"],
            cohort_id=source["cohort_id"],
            observation_ids=source["observation_ids"],
            observation_set_hash=source["observation_set_hash"],
            cohort_query_hash=source["cohort_query_hash"],
            strategy_version_id=source["strategy_version_id"],
            release_manifest_id=source["release_manifest_id"],
            label_versions=source["label_versions"] or {},
            split=source["split"],
            time_blocks=source["time_blocks"] or {},
            code_hash=source["code_hash"],
            config_hash=source["config_hash"],
            seed=source["seed"],
            n_market=source["n_market"],
            n_episode=source["n_episode"],
            n_resolution_cluster=source["n_resolution_cluster"],
            n_eff=source["n_eff"],
            results=source["results"] or {},
            ci=source["ci"] or {},
            artifact_hash=source["artifact_hash"],
        )
        computed = evaluator._compute_five_layers(uow, metric_input, observations)
        if inspect.isawaitable(computed):
            computed = await computed
        results, ci = computed
        sizes = evaluator._run_sizes(uow, metric_input, observations)
        if inspect.isawaitable(sizes):
            sizes = await sizes
        expected_sizes = (
            int(source["n_market"]),
            int(source["n_episode"]),
            int(source["n_resolution_cluster"]),
            Decimal(str(source["n_eff"])),
        )
        actual_sizes = (sizes[0], sizes[1], sizes[2], Decimal(str(sizes[3])))
        if actual_sizes != expected_sizes:
            return {"ok": False, "reason": "replay_metric_size_mismatch"}
        artifact = metric_evidence_manifest(
            metric_input,
            observation_ids=[int(value) for value in source["observation_ids"]],
            observation_set_hash=source["observation_set_hash"],
            n_market=sizes[0],
            n_episode=sizes[1],
            n_resolution_cluster=sizes[2],
            n_eff=Decimal(str(sizes[3])),
            results=results,
            ci=ci,
        )
        artifact_hash = canonical_hash(artifact)
        if canonical_hash(source["results"] or {}) != canonical_hash(artifact["results"]):
            return {"ok": False, "reason": "replay_five_layer_results_mismatch"}
        if canonical_hash(source["ci"] or {}) != canonical_hash(artifact["ci"]):
            return {"ok": False, "reason": "replay_five_layer_ci_mismatch"}
        if artifact_hash != source["artifact_hash"]:
            return {"ok": False, "reason": "replay_source_artifact_hash_mismatch"}
        return {
            "ok": True,
            "artifact": artifact,
            "artifact_hash": artifact_hash,
            "observations": observations,
            "observation_set_hash": source["observation_set_hash"],
        }

    async def _observations_for_replay(
        self, uow: UnitOfWork, source: dict, cutoff: Any
    ) -> list[dict]:
        """Load the exact historical prefix; exact quote bindings prevent refills."""
        label_ids = self._flatten_label_versions(source.get("label_versions") or {})
        observation_ids = [int(value) for value in source.get("observation_ids") or []]
        if not observation_ids or observation_ids != sorted(set(observation_ids)):
            raise RuntimeError("replay_observation_ids_invalid")
        set_material = {
            "cohort_id": int(source["cohort_id"]),
            "split": source["split"],
            "ordered_observation_ids": observation_ids,
            "label_versions": source.get("label_versions") or {},
            "time_blocks": source.get("time_blocks") or {},
            "strategy_version_id": int(source["strategy_version_id"]),
            "release_manifest_id": int(source["release_manifest_id"]),
        }
        if canonical_hash(set_material) != source.get("observation_set_hash"):
            raise RuntimeError("replay_observation_set_hash_mismatch")
        result = await uow.session.execute(
            text(
                "SELECT so.*, s.q, s.committed_at, s.status AS submission_status, "
                "       s.episode_id, rl.state AS label_state, rl.resolution_state, "
                "       rl.raw_outcome, rl.token_cashflow, st.contract_spec_id, "
                "       st.target_type, st.canonical_side, st.members, st.horizon, "
                "       st.resolution_cluster_id AS cluster_id, "
                "       (c.horizon || ':' || c.time_block_start::text || ':' || "
                "        c.time_block_end::text) AS time_block, "
                "       COALESCE(tm.market_ids, ARRAY[]::bigint[]) AS market_ids, "
                "       COALESCE(tm.token_ids, ARRAY[]::bigint[]) AS target_token_ids "
                "FROM trading.score_observations so "
                "JOIN trading.forecast_submissions s ON s.id=so.submission_id "
                "JOIN trading.forecast_episodes fe ON fe.id=s.episode_id "
                "JOIN trading.decision_opportunities dop "
                "  ON dop.id=fe.decision_opportunity_id "
                "JOIN trading.evaluation_cohorts ec ON ec.id=dop.cohort_id "
                "JOIN trading.resolution_labels rl ON rl.id=so.label_version_id "
                "JOIN trading.score_targets st ON st.id=so.score_target_id "
                "JOIN trading.resolution_clusters c ON c.id=st.resolution_cluster_id "
                "LEFT JOIN LATERAL ("
                "  SELECT array_agg(DISTINCT pt.market_id ORDER BY pt.market_id) AS market_ids, "
                "         array_agg(stm.token_id ORDER BY pt.outcome_index, stm.token_id) "
                "           AS token_ids "
                "  FROM trading.score_target_memberships stm "
                "  JOIN trading.pm_tokens pt ON pt.id=stm.token_id "
                "  WHERE stm.score_target_id=so.score_target_id AND pt.market_id IS NOT NULL"
                ") tm ON true "
                "WHERE so.id=ANY(:observation_ids) "
                "  AND so.split=:split AND so.label_version_id = ANY(:labels) "
                "  AND dop.cohort_id=:cohort_id "
                "  AND fe.strategy_version_id=:strategy_version_id "
                "  AND ec.strategy_version_id=:strategy_version_id "
                "  AND ec.release_manifest_id=:release_manifest_id "
                "  AND so.created_at <= :cutoff AND s.committed_at <= :cutoff "
                "  AND rl.created_at <= :cutoff AND st.created_at <= :cutoff "
                "  AND c.created_at <= :cutoff "
                "ORDER BY so.id"
            ),
            {
                "observation_ids": observation_ids,
                "split": source["split"],
                "labels": label_ids or [-1],
                "cohort_id": source["cohort_id"],
                "strategy_version_id": source["strategy_version_id"],
                "release_manifest_id": source["release_manifest_id"],
                "cutoff": cutoff,
            },
        )
        rows = _rows(result)
        if [int(row["id"]) for row in rows] != observation_ids:
            raise RuntimeError("replay_observation_set_incomplete")
        if any(row["label_state"] != "final_admissible" for row in rows):
            return []
        for row in rows:
            # EXCLUDED rows remain in the frozen denominator/coverage set but, by
            # contract, carry no quote binding and never enter proper-loss.
            if row.get("status") == "EXCLUDED":
                continue
            if not await self._quote_binding_matches(uow, row, cutoff):
                raise RuntimeError(
                    f"replay_quote_binding_mismatch:{row['observation_key']}"
                )
        return rows

    async def _quote_binding_matches(
        self, uow: UnitOfWork, row: dict, cutoff: Any
    ) -> bool:
        binding_ids = row.get("baseline_quote_binding_ids")
        if not isinstance(binding_ids, list) or not binding_ids:
            return False
        result = await uow.session.execute(
            text(
                "SELECT qb.id, pt.id AS token_id, qb.best_bid, qb.best_ask, "
                "       qb.checkpoint_received_at "
                "FROM trading.pm_quote_bindings qb "
                "JOIN trading.pm_tokens pt ON pt.token_id=qb.token_id "
                "WHERE qb.id=ANY(:ids) AND qb.created_at<=:cutoff "
                "  AND qb.received_at<=:committed_at AND qb.stale_at>:committed_at "
                "ORDER BY pt.id, qb.id"
            ),
            {
                "ids": [int(value) for value in binding_ids],
                "cutoff": cutoff,
                "committed_at": row["committed_at"],
            },
        )
        bindings = _rows(result)
        if len(bindings) != len(binding_ids):
            return False
        value = {}
        for binding in bindings:
            mid = (
                Decimal(str(binding["best_bid"]))
                + Decimal(str(binding["best_ask"]))
            ) / Decimal("2")
            value[str(binding["token_id"])] = format(mid.normalize(), "f")
        if canonical_hash(value) != row.get("baseline_value_hash"):
            return False
        if canonical_hash(value) != canonical_hash(row.get("baseline_value") or {}):
            return False
        max_checkpoint = max(binding["checkpoint_received_at"] for binding in bindings)
        if max_checkpoint != row.get("baseline_checkpoint_received_at"):
            return False
        if row.get("baseline_quote") is not None:
            if len(value) != 1:
                return False
            if Decimal(next(iter(value.values()))) != Decimal(str(row["baseline_quote"])):
                return False
        return True

    async def _has_future_decision_suffix(
        self, uow: UnitOfWork, decision_ids: list[int], cutoff: Any
    ) -> bool:
        if not decision_ids:
            return False
        result = await uow.session.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM trading.action_candidates "
                "  WHERE trade_decision_id=ANY(:decisions) AND created_at>:cutoff"
                ") OR EXISTS ("
                "  SELECT 1 FROM trading.executions e "
                "  JOIN trading.economic_action_intents i "
                "    ON i.id=e.economic_action_intent_id "
                "  WHERE i.trade_decision_id=ANY(:decisions) AND e.created_at>:cutoff"
                ") OR EXISTS ("
                "  SELECT 1 FROM trading.ledger_transactions "
                "  WHERE trade_decision_id=ANY(:decisions) AND created_at>:cutoff"
                ") OR EXISTS ("
                "  SELECT 1 FROM trading.operating_cost_entries "
                "  WHERE trade_decision_id=ANY(:decisions) AND created_at>:cutoff"
                ")"
            ),
            {"decisions": decision_ids, "cutoff": cutoff},
        )
        return bool(result.scalar_one())

    async def _metric_run_by_id(
        self, uow: UnitOfWork, metric_run_id: int
    ) -> dict | None:
        result = await uow.session.execute(
            text("SELECT * FROM trading.metric_runs WHERE id=:mid"),
            {"mid": metric_run_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def _observations_for_run(
        self, uow: UnitOfWork, run: dict
    ) -> list[dict]:
        label_ids = self._flatten_label_versions(run.get("label_versions") or {})
        observation_ids = [int(value) for value in run.get("observation_ids") or []]
        if not observation_ids or observation_ids != sorted(set(observation_ids)):
            return []
        result = await uow.session.execute(
            text(
                "SELECT observation_key, score_target_id, submission_id, trade_decision_id, "
                "       label_version_id, score_value "
                "FROM trading.score_observations "
                "WHERE id=ANY(:observation_ids) AND status='INCLUDED' "
                "  AND split=:split AND label_version_id = ANY(:lv) "
                "ORDER BY id"
            ),
            {
                "observation_ids": observation_ids,
                "split": run["split"],
                "lv": label_ids or [-1],
            },
        )
        return _rows(result)

    @staticmethod
    def _flatten_label_versions(label_versions: dict) -> list[int]:
        out: list[int] = []
        for value in (label_versions or {}).values():
            if isinstance(value, list):
                out.extend(int(v) for v in value)
            else:
                out.append(int(value))
        return list(dict.fromkeys(out))
