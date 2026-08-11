"""Evaluation Logic（WP-04 Checkpoint C）。

- ``score_observation``：只接受 ``final_admissible`` label（``proper_loss_guard`` 放行）；
  从 DB 读取 exact blind submission、权威 baseline quote checkpoint、canonical target、
  split、algorithm hash；用 ``scoring.py`` 计算 metric 值，``ΔLoss = delta_loss(candidate,
  baseline)``；写 ``score_observations``。baseline 缺失/陈旧 → 显式 excluded（禁止未来
  quote 回填）。
- ``score_observation_guardrails``：full forecast-set 与 selected action-set 两组结果分开，
  prediction loss 与 system net 不互相替代。
- ``run_metric``：固定 cohort/strategy/release/label versions/split/time blocks/code/config/
  seed；五层结果 + 95% CI（``cluster_bootstrap``）+ artifact hash；写 ``metric_runs``
  （RUNNING→COMPLETED，terminal 禁改由 DB guard 强约束）。Portfolio 层不完整时
  ``not_evaluable``，不 0 填充。
- ``promote``：capital promotion 恒 fail closed；strategy approval 只创建未来生效的 shadow
  assignment，不写历史 cohort/assignment、不回写 metric/forecast/decision 事实；任一 hard
  guardrail 失败即 REJECTED，低功效继续 shadow（DEFERRED）。
- ``champion_challenger_pair``：champion/challenger immutable input manifest 除唯一变化字段外
  全等，否则实验 INVALIDATED。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.domain.trading.evaluation_policy import evaluation_policy, evaluation_policy_hash
from app.domain.trading.scoring import (
    BERNOULLI_EPSILON,
    bernoulli_brier,
    bernoulli_log_loss,
    cluster_bootstrap,
    delta_loss,
    mean_squared_payout_loss,
    multiclass_brier,
    multiclass_log_loss,
    proper_loss_guard,
    round_score,
    tail_loss,
)
from app.domain.trading.inference import (
    edge_bucket_monotonicity,
    execution_metrics,
    horvitz_thompson_weight,
    ht_estimate,
    no_action_regret,
    portfolio_summary,
)
from app.domain.trading.payout import apply_payout_lookup
from app.repositories.trading.evaluation import EvaluationRepository
from app.repositories.trading.settlement import SettlementRepository
from app.schemas.trading.evaluation import (
    MetricRunInput,
    PromotionDecisionInput,
    ScoreObservationInput,
)

def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


def _decimal(value: Any, path: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{path}_bool_or_float_forbidden")
    return Decimal(str(value))


def _p3_spec() -> dict:
    # Compatibility helper used by focused tests/callers; source is deployment-owned.
    from app.domain.trading.evaluation_policy import evaluation_spec

    return evaluation_spec()


def _jsonable(value: Any) -> Any:
    """递归把 Decimal 转规范化字符串，供 JSONB 存储（与 canonical_hash 口径一致）。"""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def metric_evidence_manifest(
    input_: MetricRunInput,
    *,
    observation_ids: list[int],
    observation_set_hash: str,
    n_market: int,
    n_episode: int,
    n_resolution_cluster: int,
    n_eff: Decimal,
    results: dict,
    ci: dict,
) -> dict[str, Any]:
    """Canonical immutable evidence covered by ``metric_runs.artifact_hash``."""
    return _jsonable({
        "run_key": input_.run_key,
        "cohort_id": input_.cohort_id,
        "ordered_observation_ids": observation_ids,
        "observation_set_hash": observation_set_hash,
        "cohort_query_hash": input_.cohort_query_hash,
        "strategy_version_id": input_.strategy_version_id,
        "release_manifest_id": input_.release_manifest_id,
        "label_versions": input_.label_versions,
        "split": input_.split,
        "time_blocks": input_.time_blocks,
        "code_hash": input_.code_hash,
        "config_hash": input_.config_hash,
        "seed": input_.seed,
        "counts": {
            "n_market": n_market,
            "n_episode": n_episode,
            "n_resolution_cluster": n_resolution_cluster,
            "n_eff": n_eff,
        },
        "results": results,
        "ci": ci,
    })


def _promotion_policy_hash() -> str:
    return evaluation_policy_hash("promotion_policy")


def _metric_epsilon() -> Decimal:
    return Decimal(evaluation_policy("metric_policy")["bernoulli_epsilon"])


@dataclass(frozen=True)
class ScoreResult:
    ok: bool
    observation_id: int | None = None
    reason: str | None = None
    state: str | None = None  # "excluded" 表示显式 excluded，未写 observation


@dataclass(frozen=True)
class MetricRunResult:
    ok: bool
    metric_run_id: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PromotionResult:
    ok: bool
    promotion_id: int | None = None
    status: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ExperimentResult:
    ok: bool
    experiment_id: int | None = None
    status: str | None = None
    reason: str | None = None


class EvaluationLogic:
    """五层评价 / promotion / experiment 业务规则。"""

    def __init__(
        self,
        evaluation: EvaluationRepository | None = None,
        settlement: SettlementRepository | None = None,
    ) -> None:
        self._evaluation = evaluation or EvaluationRepository()
        self._settlement = settlement or SettlementRepository()

    # ---------------- score observation ----------------

    async def score_observation(
        self, uow: UnitOfWork, *, input_: ScoreObservationInput
    ) -> ScoreResult:
        label = await self._settlement.get_label_by_version(
            uow.session, input_.label_version_id
        )
        if label is None:
            return ScoreResult(False, reason="score_label_missing")
        if not proper_loss_guard(label["state"]):
            return ScoreResult(False, reason="score_label_not_admissible")

        submission = await self._load_submission(uow, input_.submission_id)
        if submission is None or submission["status"] != "BLIND_COMMITTED":
            return ScoreResult(False, reason="score_submission_missing")

        target = await self._load_target(uow, input_.score_target_id)
        if target is None:
            return ScoreResult(False, reason="score_target_missing")
        if label["contract_spec_id"] != target["contract_spec_id"]:
            return ScoreResult(False, reason="score_label_target_contract_mismatch")
        if input_.algorithm_hash != submission["algorithm_hash"]:
            return ScoreResult(False, reason="score_algorithm_hash_mismatch")
        if input_.baseline_policy_hash != evaluation_policy_hash("baseline_convention"):
            return ScoreResult(False, reason="score_baseline_policy_mismatch")
        if input_.trade_decision_id is not None and not await self._decision_matches_submission(
            uow, input_.trade_decision_id, input_.submission_id
        ):
            return ScoreResult(False, reason="score_decision_submission_mismatch")

        R_c, payouts, h_c = await self._load_contract_material(
            uow, target["contract_spec_id"]
        )
        if R_c is None:
            return ScoreResult(False, reason="score_contract_spec_missing")

        baseline_fact = await self._authoritative_baseline(
            uow, target, submission, input_.baseline_quote_binding_ids or []
        )
        if baseline_fact is None:
            # 冻结 baseline_policy：缺失/陈旧 → 显式 excluded，禁止未来 quote 回填。
            observation_id = await self._insert_excluded_observation(
                uow, input_, reason="score_baseline_missing"
            )
            return ScoreResult(
                False, observation_id=observation_id,
                reason="score_baseline_missing", state="excluded"
            )
        baseline = baseline_fact["scalar"]
        if (
            (baseline is None) != (input_.baseline_quote is None)
            or (
                baseline is not None
                and input_.baseline_quote is not None
                and _decimal(input_.baseline_quote, "score_baseline_quote") != baseline
            )
            or input_.baseline_value is None
            or {str(k): _decimal(v, "score_baseline_value") for k, v in input_.baseline_value.items()}
            != baseline_fact["value"]
            or input_.baseline_value_hash != baseline_fact["value_hash"]
            or input_.baseline_checkpoint_received_at is None
            or input_.baseline_checkpoint_received_at
            != baseline_fact["checkpoint_received_at"]
        ):
            return ScoreResult(
                False, reason="score_baseline_authority_mismatch", state="excluded"
            )

        try:
            computed = self._compute_metric(
                target, submission, label, payouts, h_c,
                metric_id=input_.metric_id, baseline=baseline,
            )
        except ValueError as exc:
            return ScoreResult(False, reason=str(exc))
        if input_.score_value is None or _decimal(input_.score_value, "score_value") != computed:
            return ScoreResult(False, reason="score_value_authority_mismatch")

        observation_id = await self._evaluation.insert_score_observation(
            uow.session,
            observation_key=input_.observation_key,
            score_target_id=input_.score_target_id,
            submission_id=input_.submission_id,
            trade_decision_id=input_.trade_decision_id,
            label_version_id=input_.label_version_id,
            status="INCLUDED",
            exclusion_reason=None,
            baseline_quote=input_.baseline_quote,
            baseline_quote_binding_ids=input_.baseline_quote_binding_ids,
            baseline_value=_jsonable(input_.baseline_value),
            baseline_value_hash=input_.baseline_value_hash,
            baseline_checkpoint_received_at=input_.baseline_checkpoint_received_at,
            baseline_policy_hash=input_.baseline_policy_hash,
            split=input_.split,
            algorithm_hash=input_.algorithm_hash,
            metric_id=input_.metric_id,
            score_value=computed,
        )
        return ScoreResult(True, observation_id=observation_id)

    async def _insert_excluded_observation(
        self, uow: UnitOfWork, input_: ScoreObservationInput, *, reason: str
    ) -> int:
        return await self._evaluation.insert_score_observation(
            uow.session,
            observation_key=input_.observation_key,
            score_target_id=input_.score_target_id,
            submission_id=input_.submission_id,
            trade_decision_id=input_.trade_decision_id,
            label_version_id=input_.label_version_id,
            status="EXCLUDED",
            exclusion_reason=reason,
            baseline_quote=None,
            baseline_quote_binding_ids=None,
            baseline_value=None,
            baseline_value_hash=None,
            baseline_checkpoint_received_at=None,
            baseline_policy_hash=input_.baseline_policy_hash,
            split=input_.split,
            algorithm_hash=input_.algorithm_hash,
            metric_id=input_.metric_id,
            score_value=None,
        )

    # ---------------- guardrails ----------------

    async def score_observation_guardrails(
        self, uow: UnitOfWork, *, metric_id: str
    ) -> dict:
        """full forecast-set 与 selected action-set 两组都要产出，prediction loss 与 system
        net 不能互相替代。"""
        rows = await self._load_observations_by_metric(uow, metric_id)
        full = [row for row in rows if row.get("status", "INCLUDED") == "INCLUDED"]
        selected = [row for row in full if row.get("trade_decision_id") is not None]
        return {
            "full_forecast_set": self._aggregate_scores(full),
            "selected_action_set": self._aggregate_scores(selected),
        }

    # ---------------- metric run ----------------

    async def run_metric(
        self, uow: UnitOfWork, *, input_: MetricRunInput
    ) -> MetricRunResult:
        obs_rows = await self._load_run_observations(uow, input_)
        observation_ids = [int(row["id"]) for row in obs_rows]
        authoritative_labels = sorted({int(row["label_version_id"]) for row in obs_rows})
        requested_labels = sorted(self._flatten_label_versions(input_.label_versions))
        if requested_labels != authoritative_labels:
            return MetricRunResult(False, reason="metric_label_versions_mismatch")
        observation_material = {
            "cohort_id": input_.cohort_id,
            "split": input_.split,
            "ordered_observation_ids": observation_ids,
            "label_versions": input_.label_versions,
            "time_blocks": input_.time_blocks,
            "strategy_version_id": input_.strategy_version_id,
            "release_manifest_id": input_.release_manifest_id,
        }
        observation_set_hash = canonical_hash(observation_material)
        if observation_ids != input_.observation_ids:
            return MetricRunResult(False, reason="metric_observation_ids_mismatch")
        if observation_set_hash != input_.observation_set_hash:
            return MetricRunResult(False, reason="metric_observation_set_hash_mismatch")
        results, ci = await self._compute_five_layers(uow, input_, obs_rows)
        n_market, n_episode, n_cluster, n_eff = self._run_sizes(uow, input_, obs_rows)
        evidence_manifest = metric_evidence_manifest(
            input_,
            observation_ids=observation_ids,
            observation_set_hash=observation_set_hash,
            n_market=n_market,
            n_episode=n_episode,
            n_resolution_cluster=n_cluster,
            n_eff=n_eff,
            results=results,
            ci=ci,
        )
        artifact_hash = canonical_hash(evidence_manifest)
        run_id = await self._evaluation.insert_metric_run(
            uow.session,
            run_key=input_.run_key,
            cohort_id=input_.cohort_id,
            observation_ids=observation_ids,
            observation_set_hash=observation_set_hash,
            cohort_query_hash=input_.cohort_query_hash,
            strategy_version_id=input_.strategy_version_id,
            release_manifest_id=input_.release_manifest_id,
            label_versions=json.dumps(input_.label_versions),
            split=input_.split,
            time_blocks=json.dumps(input_.time_blocks),
            code_hash=input_.code_hash,
            config_hash=input_.config_hash,
            seed=input_.seed,
            n_market=n_market,
            n_episode=n_episode,
            n_resolution_cluster=n_cluster,
            n_eff=n_eff,
            results=json.dumps(_jsonable(results)),
            ci=json.dumps(_jsonable(ci)),
            artifact_hash=artifact_hash,
        )
        if not await self._evaluation.advance_metric_run_status(
            uow.session, input_.run_key,
            to_status="COMPLETED", completed_at=datetime.now(timezone.utc),
        ):
            return MetricRunResult(False, reason="metric_run_status_conflict")
        return MetricRunResult(True, metric_run_id=run_id)

    # ---------------- promotion ----------------

    async def promote(
        self, uow: UnitOfWork, *, input_: PromotionDecisionInput
    ) -> PromotionResult:
        if input_.promotion_type == "capital":
            # capital promotion 恒 fail closed（DB CHECK 已强约束 + Logic 前置拒绝）。
            promotion_id = await self._evaluation.insert_promotion_decision(
                uow.session,
                promotion_key=input_.promotion_key,
                metric_run_id=input_.metric_run_id,
                promotion_type="capital",
                from_ref=input_.from_ref,
                to_ref=input_.to_ref,
                evidence_manifest_hash=input_.evidence_manifest_hash,
                status="REJECTED",
                reason_code="capital_promotion_fail_closed",
                future_effective_at=None,
                capital_amount=Decimal("0"),
            )
            return PromotionResult(
                True, promotion_id=promotion_id, status="REJECTED",
                reason="capital_promotion_fail_closed",
            )

        metric_run = await self._metric_run_by_id(uow, input_.metric_run_id)
        reason = await self._promotion_guardrail(uow, metric_run, input_)
        if reason == "promotion_low_power":
            status = "DEFERRED"
        elif reason is not None:
            status = "REJECTED"
        elif input_.status == "APPROVED":
            status = "APPROVED"
        elif input_.status == "DEFERRED":
            status = "DEFERRED"
        else:
            status = "REJECTED"
            reason = "promotion_status_unapproved"

        future_effective_at = input_.future_effective_at
        if status == "APPROVED":
            if future_effective_at is None or future_effective_at <= datetime.now(timezone.utc):
                status = "REJECTED"
                reason = "promotion_future_effective_required"
            else:
                reason = None
        else:
            future_effective_at = None

        promotion_id = await self._evaluation.insert_promotion_decision(
            uow.session,
            promotion_key=input_.promotion_key,
            metric_run_id=input_.metric_run_id,
            promotion_type="strategy",
            from_ref=input_.from_ref,
            to_ref=input_.to_ref,
            evidence_manifest_hash=input_.evidence_manifest_hash,
            status=status,
            reason_code=reason,
            future_effective_at=future_effective_at,
            capital_amount=Decimal("0"),
        )
        return PromotionResult(
            True, promotion_id=promotion_id, status=status, reason=reason
        )

    # ---------------- champion / challenger ----------------

    async def champion_challenger_pair(
        self, uow: UnitOfWork, *, experiment_key: str
    ) -> ExperimentResult:
        experiment = await self._experiment_by_key(uow, experiment_key)
        if experiment is None:
            return ExperimentResult(False, reason="experiment_missing")
        if experiment["status"] == "INVALIDATED":
            return ExperimentResult(
                True, experiment_id=experiment["id"], status="INVALIDATED",
                reason="experiment_already_invalidated",
            )
        if experiment["status"] == "PLANNED":
            if not await self._evaluation.advance_experiment_status(
                uow.session, experiment["id"],
                from_status="PLANNED", to_status="RUNNING",
            ):
                return ExperimentResult(False, reason="experiment_status_conflict")
            experiment["status"] = "RUNNING"
        elif experiment["status"] not in ("RUNNING", "COMPLETED"):
            return ExperimentResult(False, reason="experiment_status_invalid")
        champion = await self._variant(uow, experiment["id"], "champion")
        challenger = await self._variant(uow, experiment["id"], "challenger")
        if champion is None or challenger is None:
            return ExperimentResult(
                False, reason="experiment_variant_missing", experiment_id=experiment["id"]
            )
        challenger_cfg = await self._challenger_cfg(
            uow, experiment["id"], challenger["variant_key"]
        )
        if challenger_cfg is None:
            return ExperimentResult(
                False, reason="challenger_config_missing", experiment_id=experiment["id"]
            )
        # 除唯一变化字段外 manifest 必须全等：challenger 声明的 changed_fields 必须恰好等于
        # 实验注册的唯一变化字段；否则实验 INVALIDATED。
        changed = set((challenger_cfg["changed_fields"] or {}).keys())
        unique = {experiment["unique_change_field"]}
        if changed != unique:
            if experiment["status"] == "RUNNING":
                await self._evaluation.advance_experiment_status(
                    uow.session, experiment["id"],
                    from_status="RUNNING", to_status="INVALIDATED",
                )
            return ExperimentResult(
                True, experiment_id=experiment["id"], status="INVALIDATED",
                reason="experiment_multiple_factors_changed",
            )
        if champion["input_manifest_hash"] == challenger["input_manifest_hash"]:
            if experiment["status"] == "RUNNING":
                await self._evaluation.advance_experiment_status(
                    uow.session, experiment["id"],
                    from_status="RUNNING", to_status="INVALIDATED",
                )
            return ExperimentResult(
                True, experiment_id=experiment["id"], status="INVALIDATED",
                reason="experiment_manifests_not_distinct",
            )
        if experiment["status"] == "RUNNING" and not await self._evaluation.advance_experiment_status(
            uow.session, experiment["id"],
            from_status="RUNNING", to_status="COMPLETED",
        ):
            return ExperimentResult(False, reason="experiment_status_conflict")
        return ExperimentResult(
            True, experiment_id=experiment["id"], status="COMPLETED"
        )

    # ---------------- metric computation ----------------

    def _compute_metric(
        self,
        target: dict,
        submission: dict,
        label: dict,
        payouts: dict[int, dict],
        h_c: dict,
        *,
        metric_id: str,
        baseline: Decimal | None,
    ) -> Decimal:
        target_type = target["target_type"]
        resolution_state = label["resolution_state"]
        q_dist = submission["q"] or {}
        epsilon = _metric_epsilon()
        if target_type == "bernoulli":
            side = target["canonical_side"]
            p = sum(
                _decimal(q_dist[ws], "score_q") for ws, res in h_c.items()
                if res == side
            )
            outcome = 1 if resolution_state == side else 0
            if metric_id == "bernoulli_brier":
                return bernoulli_brier(p, outcome)
            if metric_id == "bernoulli_log_loss":
                return bernoulli_log_loss(p, outcome, epsilon)
            if metric_id == "bernoulli_brier_delta":
                if baseline is None:
                    raise ValueError("score_scalar_baseline_missing")
                return delta_loss(
                    bernoulli_brier(p, outcome), bernoulli_brier(baseline, outcome)
                )
            if metric_id == "bernoulli_log_loss_delta":
                if baseline is None:
                    raise ValueError("score_scalar_baseline_missing")
                return delta_loss(
                    bernoulli_log_loss(p, outcome, epsilon),
                    bernoulli_log_loss(baseline, outcome, epsilon),
                )
        elif target_type == "multiclass":
            members = list(target["members"] or [])
            probs = [
                sum(
                    _decimal(q_dist[ws], "score_q") for ws, res in h_c.items()
                    if res == member
                )
                for member in members
            ]
            one_hot = [1 if resolution_state == member else 0 for member in members]
            if metric_id == "multiclass_brier":
                return multiclass_brier(probs, one_hot)
            if metric_id == "multiclass_log_loss":
                return multiclass_log_loss(probs, one_hot, epsilon)
        elif target_type == "mean_only":
            predicted = self._mean_only_predicted(target, q_dist, payouts, h_c)
            actual = self._mean_only_actual(label)
            if metric_id == "mean_squared_payout_loss":
                return mean_squared_payout_loss(predicted, actual)
        raise ValueError(f"score_metric_unknown:{target_type}:{metric_id}")

    def _mean_only_predicted(
        self, target: dict, q_dist: dict, payouts: dict[int, dict], h_c: dict
    ) -> Decimal:
        token_id = target.get("membership_token_id")
        if token_id is None or token_id not in payouts:
            raise ValueError("score_mean_only_token_missing")
        total = Decimal("0")
        for ws, res in h_c.items():
            payout = apply_payout_lookup(payouts[token_id], res)
            total += _decimal(q_dist.get(ws, "0"), "score_q") * payout
        return total

    @staticmethod
    def _mean_only_actual(label: dict) -> Decimal:
        raw = label.get("raw_outcome")
        if isinstance(raw, dict) and raw.get("actual_mean") is not None:
            return _decimal(raw["actual_mean"], "score_actual_mean")
        cashflow = label.get("token_cashflow")
        if isinstance(cashflow, dict) and cashflow.get("mean") is not None:
            return _decimal(cashflow["mean"], "score_actual_mean")
        raise ValueError("score_mean_only_actual_missing")

    # ---------------- five layers ----------------

    async def _compute_five_layers(
        self, uow: UnitOfWork, input_: MetricRunInput, obs_rows: list[dict]
    ) -> tuple[dict, dict]:
        full = [
            row for row in obs_rows
            if row.get("status", "INCLUDED") == "INCLUDED"
            and row.get("score_value") is not None
        ]
        selected_all = [
            row for row in obs_rows if row.get("trade_decision_id") is not None
        ]
        selected = [row for row in full if row.get("trade_decision_id") is not None]

        primary_full = [row for row in full if self._is_primary_metric(row)]
        primary_selected = [row for row in selected if self._is_primary_metric(row)]
        tail_full = [row for row in full if self._is_tail_metric(row)]
        prediction_full = self._aggregate_scores(primary_full)
        prediction_selected = self._aggregate_scores(primary_selected)
        paired_deltas = [self._paired_delta(row) for row in primary_full]
        paired_complete = bool(primary_full) and all(
            value is not None for value in paired_deltas
        )
        paired_values = [value for value in paired_deltas if value is not None]
        paired_mean = (
            round_score(sum(paired_values) / Decimal(len(paired_values)))
            if paired_values else None
        )
        paired_tail = tail_loss(paired_values) if paired_values else None
        tail_deltas = [self._paired_delta(row) for row in tail_full]
        tail_complete = all(value is not None for value in tail_deltas)
        tail_values = [value for value in tail_deltas if value is not None]
        log_tail_delta = tail_loss(tail_values) if tail_values else None
        bernoulli_primary_count = sum(
            1 for row in primary_full if row.get("target_type") == "bernoulli"
        )
        prediction_full["paired_delta_count"] = len(paired_values)
        prediction_full["paired_delta_mean"] = paired_mean
        prediction_full["paired_delta_tail"] = paired_tail
        prediction_full["log_loss_delta_count"] = len(tail_values)
        prediction_full["log_loss_delta_tail"] = log_tail_delta
        # Raw proper loss and paired delta are different facts.  Promotion is
        # gated only by the frozen paired comparison (candidate - baseline),
        # never by the sign of a raw non-negative proper loss.
        prediction_pass = (
            paired_complete
            and paired_mean is not None and paired_mean < 0
            and (
                bernoulli_primary_count == 0
                or (
                    tail_complete
                    and len(tail_values) >= bernoulli_primary_count
                    and log_tail_delta is not None and log_tail_delta <= 0
                )
            )
        )
        prediction = {
            "full_forecast_set": prediction_full,
            "selected_action_set": prediction_selected,
            "evaluable": bool(full),
            "hard_guardrail_pass": prediction_pass,
        }
        if full and not prediction_pass:
            prediction["reason"] = "paired_delta_not_improved_or_tail_unsafe"

        # Proper-loss eligibility never controls economic truth.  EXCLUDED
        # rows remain in selection/edge/execution/ledger reporting.
        portfolio, economic = await self._portfolio_layer(uow, selected_all)
        realized_net = economic.get("system_net") if economic else None
        realized_by_decision = economic.get("decision_system_net", {}) if economic else {}
        regret = (
            no_action_regret(realized_net, Decimal("0"))
            if realized_net is not None else None
        )
        coverage = self._coverage(obs_rows, selected_all)
        audit_rows = await self._load_reject_audit(
            uow, input_.cohort_id, [int(row["id"]) for row in obs_rows]
        )
        audit_regret: Decimal | None = None
        audit_complete = bool(audit_rows)
        audit_values: list[tuple[Decimal, Decimal]] = []
        for row in audit_rows:
            pi = _decimal(row["inclusion_probability"], "selection_audit_pi")
            decision_id = row.get("trade_decision_id")
            if pi <= 0 or row.get("observation_id") is None:
                audit_complete = False
                break
            if decision_id is None:
                realized_missed = Decimal("0")
            elif int(decision_id) in realized_by_decision:
                realized_missed = max(
                    realized_by_decision[int(decision_id)], Decimal("0")
                )
            else:
                audit_complete = False
                break
            audit_values.append((realized_missed, horvitz_thompson_weight(pi)))
        if audit_complete and audit_values:
            audit_regret = ht_estimate(audit_values)
        selection_pass = (
            coverage is not None and coverage > 0
            and regret is not None and regret > 0
            and audit_regret is not None and audit_regret <= 0
        )
        selection = {
            "opportunity_coverage": coverage,
            "no_action_regret": regret,
            # Reject-audit AUC remains unknown when the frozen audit sample has
            # no realized target; it is not fabricated from selected rows.
            "selection_triage_auc": None,
            "reject_audit_ht_missed_opportunity": audit_regret,
            "reject_audit_sample_count": len(audit_rows),
            "evaluable": regret is not None and coverage is not None,
            "hard_guardrail_pass": selection_pass,
        }
        if not selection_pass:
            selection["reason"] = (
                "reject_audit_missing"
                if audit_regret is None else (
                    "realized_no_action_regret_missing"
                    if regret is None else "realized_no_action_regret_nonpositive"
                )
            )

        edge_rows = await self._load_edge_rows(uow, selected_all)
        edge_erosions = [
            _decimal(row["edge_delay_erosion"], "edge_delay_erosion")
            for row in edge_rows
            if row.get("edge_delay_erosion") is not None
        ]
        bucket_values: dict[Decimal, list[Decimal]] = {}
        for row in edge_rows:
            decision_id = int(row["trade_decision_id"])
            if row.get("net_edge") is None or decision_id not in realized_by_decision:
                continue
            declared_edge = _decimal(row["net_edge"], "edge_net")
            bucket_values.setdefault(declared_edge, []).append(
                realized_by_decision[decision_id]
            )
        buckets = [
            {
                "edge": declared_edge,
                "realized_excess_return": round_score(
                    sum(values) / Decimal(len(values))
                ),
            }
            for declared_edge, values in sorted(bucket_values.items())
        ]
        monotonic = edge_bucket_monotonicity(buckets) if buckets else None
        edge_direction_ok = bool(buckets) and all(
            bucket["edge"] > 0 and bucket["realized_excess_return"] > 0
            for bucket in buckets
        )
        edge_pass = len(buckets) >= 2 and monotonic is True and edge_direction_ok
        edge = {
            "edge_bucket_monotonicity": monotonic,
            "blind_to_decision_delay_erosion": (
                round_score(sum(edge_erosions) / Decimal(len(edge_erosions)))
                if edge_erosions else None
            ),
            "realized_bucket_count": len(buckets),
            "evaluable": bool(buckets),
            "hard_guardrail_pass": edge_pass,
        }
        if not edge_pass:
            edge["reason"] = (
                "realized_edge_buckets_missing" if not buckets
                else "realized_edge_direction_or_monotonicity_failed"
            )
        execution_rows = await self._load_execution_rows(uow, selected_all)
        execution = execution_metrics(execution_rows) if execution_rows else {
            "fill_count": None,
            "partial_count": None,
            "reject_count": None,
            "fee_total": None,
            "slippage_n": None,
            "slippage_vwap": None,
        }
        execution_complete = bool(execution_rows) and all(
            row.get("terminal_complete") is True
            and row.get("ledger_consistent") is True
            for row in execution_rows
        )
        execution["evaluable"] = bool(execution_rows)
        execution["hard_guardrail_pass"] = execution_complete
        if not execution_complete:
            execution["reason"] = (
                "terminal_execution_facts_missing" if not execution_rows
                else "terminal_execution_or_ledger_incomplete"
            )
        results = {
            "prediction": prediction,
            "selection": selection,
            "edge": edge,
            "portfolio": portfolio,
            "execution": execution,
        }
        cluster_losses, block_labels = self._cluster_losses(obs_rows)
        bootstrap: dict = {}
        if cluster_losses:
            bootstrap = cluster_bootstrap(
                cluster_losses,
                seed=input_.seed,
                time_blocks=len(set(block_labels)),
                cluster_time_blocks=block_labels,
            )
        ci = {"prediction": {"full_forecast_set": bootstrap}}
        return results, ci

    def _run_sizes(
        self, uow: UnitOfWork, input_: MetricRunInput, obs_rows: list[dict]
    ) -> tuple[int, int, int, Decimal]:
        # Use the same authoritative target-membership -> token -> market
        # projection as the database completion guard.  A target may contain
        # several outcome tokens, so count the union rather than trusting a
        # caller-supplied/synthetic market id.
        markets: set[int] = set()
        for row in obs_rows:
            market_ids = row.get("market_ids")
            if market_ids:
                markets.update(int(market_id) for market_id in market_ids)
            elif row.get("market_id"):
                # Retain compatibility with repository test doubles.
                markets.add(int(row["market_id"]))
        episodes = {row["episode_id"] for row in obs_rows if row.get("episode_id")}
        clusters = {row["cluster_id"] for row in obs_rows if row.get("cluster_id")}
        n_eff: Decimal = Decimal("0")
        cluster_losses, _ = self._cluster_losses(obs_rows)
        if cluster_losses:
            from app.domain.trading.scoring import n_eff as _n_eff

            n_eff = _n_eff([1] * len(cluster_losses), len(cluster_losses))
        return len(markets), len(episodes), len(clusters), n_eff

    def _cluster_losses(
        self, obs_rows: list[dict]
    ) -> tuple[list[list[Decimal]], list[str]]:
        # First equal-weight canonical targets inside episode×cluster.  The resulting
        # episode values are the within-cluster observations used by the frozen Kish
        # n_eff formula; token cardinality therefore cannot inflate the estimate.
        by_episode_cluster: dict[tuple[int, int], dict[int, list[Decimal]]] = {}
        cluster_blocks: dict[int, str] = {}
        for row in obs_rows:
            cluster_id = row.get("cluster_id")
            episode_id = row.get("episode_id")
            target_id = row.get("score_target_id")
            if (
                cluster_id is None or episode_id is None or target_id is None
                or row.get("status", "INCLUDED") != "INCLUDED"
                or row.get("score_value") is None
                or not self._is_primary_metric(row)
            ):
                continue
            paired_delta = self._paired_delta(row)
            if paired_delta is None:
                continue
            key = (int(episode_id), int(cluster_id))
            by_episode_cluster.setdefault(key, {}).setdefault(int(target_id), []).append(
                paired_delta
            )
            cluster_blocks[int(cluster_id)] = str(row.get("time_block") or "0")
        by_cluster: dict[int, list[Decimal]] = {}
        for (_episode_id, cluster_id), target_values in by_episode_cluster.items():
            canonical_values = [
                sum(values) / Decimal(len(values))
                for _, values in sorted(target_values.items())
            ]
            by_cluster.setdefault(cluster_id, []).append(
                sum(canonical_values) / Decimal(len(canonical_values))
            )
        ordered = [(cid, values) for cid, values in sorted(by_cluster.items()) if values]
        return [values for _, values in ordered], [cluster_blocks[cid] for cid, _ in ordered]

    async def _portfolio_layer(
        self, uow: UnitOfWork, selected: list[dict]
    ) -> tuple[dict, dict]:
        decision_ids = sorted({int(row["trade_decision_id"]) for row in selected})
        if not decision_ids:
            return portfolio_summary([]), {}
        result = await uow.session.execute(
            text(
                "SELECT t.id AS transaction_id, t.trade_decision_id, "
                "       t.portfolio_namespace, t.kind, t.posted_at, "
                "       p.asset_type, p.asset_key, sum(p.amount) AS amount "
                "FROM trading.ledger_transactions t "
                "JOIN trading.ledger_postings p ON p.transaction_id=t.id "
                "WHERE t.status='POSTED' AND t.trade_decision_id=ANY(:decisions) "
                "  AND p.counterparty=t.portfolio_namespace "
                "GROUP BY t.id, t.trade_decision_id, t.portfolio_namespace, "
                "         t.kind, t.posted_at, p.asset_type, p.asset_key "
                "ORDER BY t.posted_at, t.id, p.asset_type, p.asset_key"
            ),
            {"decisions": decision_ids},
        )
        ledger_rows = _rows(result)
        costs_result = await uow.session.execute(
            text(
                "SELECT id, trade_decision_id, amount "
                "FROM trading.operating_cost_entries "
                "WHERE trade_decision_id = ANY(:decisions) ORDER BY id"
            ),
            {"decisions": decision_ids},
        )
        cost_rows = _rows(costs_result)
        capital_result = await uow.session.execute(
            text(
                "SELECT ac.trade_decision_id, ac.capital_days "
                "FROM trading.action_candidates ac "
                "JOIN trading.trade_decisions d ON d.id=ac.trade_decision_id "
                "WHERE ac.trade_decision_id=ANY(:decisions) "
                "  AND ac.action_type=d.selected_action_type "
                "  AND ac.capital_days IS NOT NULL ORDER BY ac.id"
            ),
            {"decisions": decision_ids},
        )
        capital_rows = _rows(capital_result)
        if not ledger_rows or not cost_rows or not capital_rows:
            return portfolio_summary([]), {}
        labels = {
            int(row["contract_spec_id"]): row.get("token_cashflow")
            for row in selected
            if isinstance(row.get("token_cashflow"), dict)
        }
        if not labels:
            return portfolio_summary([]), {}

        total_cost = sum(
            _decimal(row["amount"], "portfolio_operating_cost") for row in cost_rows
        )
        cost_by_decision: dict[int, Decimal] = {}
        for row in cost_rows:
            if row.get("trade_decision_id") is None:
                return portfolio_summary([]), {}
            decision_id = int(row["trade_decision_id"])
            cost_by_decision[decision_id] = cost_by_decision.get(
                decision_id, Decimal("0")
            ) + _decimal(row["amount"], "portfolio_operating_cost")
        capital_values = [
            _decimal(row["capital_days"], "portfolio_capital_days")
            for row in capital_rows
        ]
        rows: list[dict] = []
        running = Decimal("0")
        token_quantities: dict[tuple[int, int], Decimal] = {}
        decision_cash: dict[int, Decimal] = {}
        decision_tokens: dict[tuple[int, int, int], Decimal] = {}
        transactions: dict[int, Decimal] = {}
        for posting in ledger_rows:
            amount = _decimal(posting["amount"], "portfolio_ledger_amount")
            decision_id = int(posting["trade_decision_id"])
            if posting["asset_type"] == "CASH":
                transactions[int(posting["transaction_id"])] = (
                    transactions.get(int(posting["transaction_id"]), Decimal("0")) + amount
                )
                decision_cash[decision_id] = decision_cash.get(
                    decision_id, Decimal("0")
                ) + amount
                continue
            if posting["asset_type"] != "TOKEN":
                return portfolio_summary([]), {}
            parts = str(posting["asset_key"]).split(":")
            if len(parts) != 3 or parts[0] != "tok":
                return portfolio_summary([]), {}
            try:
                spec_id, token_id = int(parts[1]), int(parts[2])
            except ValueError:
                return portfolio_summary([]), {}
            key = (spec_id, token_id)
            token_quantities[key] = token_quantities.get(key, Decimal("0")) + amount
            decision_key = (decision_id, spec_id, token_id)
            decision_tokens[decision_key] = decision_tokens.get(
                decision_key, Decimal("0")
            ) + amount
        for transaction_id in sorted(transactions):
            pnl = transactions[transaction_id]
            running += pnl
            rows.append({
                "pnl": pnl,
                "operating_cost": Decimal("0"),
                "equity": running,
                "capital": Decimal("0"),
                "horizon_days": Decimal("1"),
            })
        settlement_cash = Decimal("0")
        for (spec_id, token_id), quantity in token_quantities.items():
            if quantity == 0:
                continue
            if spec_id not in labels:
                return portfolio_summary([]), {}
            cashflow = labels[spec_id]
            if str(token_id) not in cashflow:
                return portfolio_summary([]), {}
            settlement_cash += quantity * _decimal(
                cashflow[str(token_id)], "portfolio_resolution_cashflow"
            )
        running += settlement_cash - total_cost
        rows.append({
            "pnl": settlement_cash,
            "operating_cost": total_cost,
            "equity": running,
            "capital": sum(capital_values),
            "horizon_days": Decimal("1"),
        })
        summary = portfolio_summary(rows)
        summary["capital_days"] = round_score(sum(capital_values))
        summary["hard_guardrail_pass"] = (
            not summary["not_evaluable"] and summary["system_net"] > 0
        )
        decision_system_net = dict(decision_cash)
        for (decision_id, spec_id, token_id), quantity in decision_tokens.items():
            if quantity == 0:
                continue
            cashflow = labels.get(spec_id)
            if not isinstance(cashflow, dict) or str(token_id) not in cashflow:
                return portfolio_summary([]), {}
            decision_system_net[decision_id] = decision_system_net.get(
                decision_id, Decimal("0")
            ) + quantity * _decimal(
                cashflow[str(token_id)], "portfolio_resolution_cashflow"
            )
        for decision_id, cost in cost_by_decision.items():
            decision_system_net[decision_id] = decision_system_net.get(
                decision_id, Decimal("0")
            ) - cost
        return summary, {
            "system_net": summary["system_net"],
            "decision_system_net": decision_system_net,
        }

    async def _load_edge_rows(
        self, uow: UnitOfWork, selected: list[dict]
    ) -> list[dict]:
        decisions = sorted({int(row["trade_decision_id"]) for row in selected})
        if not decisions:
            return []
        result = await uow.session.execute(
            text(
                "SELECT ac.trade_decision_id, ac.contract_spec_id, ac.token_id, "
                "       ac.net_edge, ac.edge_delay_erosion "
                "FROM trading.action_candidates ac "
                "JOIN trading.trade_decisions d ON d.id=ac.trade_decision_id "
                "WHERE ac.trade_decision_id=ANY(:decisions) "
                "  AND ac.action_type=d.selected_action_type ORDER BY ac.id"
            ),
            {"decisions": decisions},
        )
        return _rows(result)

    async def _load_execution_rows(
        self, uow: UnitOfWork, selected: list[dict]
    ) -> list[dict]:
        decisions = sorted({int(row["trade_decision_id"]) for row in selected})
        if not decisions:
            return []
        result = await uow.session.execute(
            text(
                "WITH expected AS ("
                " SELECT count(*) AS n FROM trading.economic_action_intents i "
                " JOIN trading.action_set_legs l ON l.action_set_id=i.action_set_id "
                " WHERE i.trade_decision_id=ANY(:decisions) AND i.status='COMMITTED'"
                "), terminal AS ("
                " SELECT count(*) AS n FROM trading.executions ex "
                " JOIN trading.economic_action_intents ix "
                "   ON ix.id=ex.economic_action_intent_id "
                " WHERE ix.trade_decision_id=ANY(:decisions) "
                "   AND ex.status IN ('FILLED','PARTIAL','REJECTED','FAILED')"
                ") "
                "SELECT CASE e.status WHEN 'FILLED' THEN 'fill' "
                "                         WHEN 'PARTIAL' THEN 'partial' "
                "                         ELSE 'reject' END AS status, "
                "       e.filled_quantity AS quantity, e.vwap AS fill_price, "
                "       l.entry_vwap AS reference_price, e.fee, "
                "       ((SELECT n FROM expected)>0 AND "
                "        (SELECT n FROM expected)=(SELECT n FROM terminal)) "
                "          AS terminal_complete, "
                "       CASE WHEN e.status IN ('FILLED','PARTIAL') THEN "
                "         EXISTS (SELECT 1 FROM trading.ledger_transactions lt "
                "           WHERE lt.execution_id=e.id AND lt.status='POSTED' "
                "             AND lt.kind='FILL') "
                "       ELSE NOT EXISTS (SELECT 1 FROM trading.ledger_transactions lt "
                "           WHERE lt.execution_id=e.id AND lt.status='POSTED') END "
                "          AS ledger_consistent "
                "FROM trading.executions e "
                "JOIN trading.economic_action_intents i "
                "  ON i.id=e.economic_action_intent_id "
                "JOIN trading.action_set_legs l ON l.id=e.action_set_leg_id "
                "WHERE i.trade_decision_id = ANY(:decisions) "
                "  AND e.status IN ('FILLED','PARTIAL','REJECTED','FAILED') "
                "ORDER BY e.id"
            ),
            {"decisions": decisions},
        )
        return _rows(result)

    async def _load_reject_audit(
        self, uow: UnitOfWork, cohort_id: int, observation_ids: list[int]
    ) -> list[dict]:
        if not observation_ids:
            return []
        result = await uow.session.execute(
            text(
                "SELECT a.content_hash, a.inclusion_probability, "
                "       so.id AS observation_id, so.trade_decision_id "
                "FROM trading.audit_samples a "
                "LEFT JOIN trading.screening_episodes se "
                "  ON se.cohort_id=a.cohort_id AND se.input_hash=a.content_hash "
                "LEFT JOIN trading.decision_opportunities dop "
                "  ON dop.source_screening_episode_id=se.id "
                "LEFT JOIN trading.forecast_episodes fe "
                "  ON fe.decision_opportunity_id=dop.id "
                "LEFT JOIN trading.forecast_submissions fs ON fs.episode_id=fe.id "
                "LEFT JOIN trading.score_observations so "
                "  ON so.submission_id=fs.id AND so.id=ANY(:observations) "
                " AND so.metric_id IN ('bernoulli_brier','bernoulli_brier_delta',"
                "                      'multiclass_brier','multiclass_brier_delta',"
                "                      'mean_squared_payout_loss',"
                "                      'mean_squared_payout_loss_delta') "
                "WHERE a.cohort_id=:cohort AND a.selected "
                "ORDER BY a.target, a.content_hash, so.id"
            ),
            {"cohort": cohort_id, "observations": observation_ids},
        )
        rows = _rows(result)
        # A pre-registered audit sample must resolve to exactly one primary
        # outcome in this frozen metric set; duplicates are ambiguous.
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(str(row["content_hash"]), []).append(row)
        if any(len(values) != 1 for values in grouped.values()):
            return []
        return [values[0] for _, values in sorted(grouped.items())]

    @staticmethod
    def _coverage(full: list[dict], selected: list[dict]) -> Decimal | None:
        if not full:
            return None
        return round_score(Decimal(len(selected)) / Decimal(len(full)))

    @staticmethod
    def _aggregate_scores(rows: list[dict]) -> dict:
        if not rows:
            return {"count": 0, "mean_loss": None, "tail_loss": None}
        values = [_decimal(row["score_value"], "score_value") for row in rows]
        return {
            "count": len(values),
            "mean_loss": round_score(sum(values) / Decimal(len(values))),
            "tail_loss": tail_loss(values),
        }

    @staticmethod
    def _is_primary_metric(row: dict) -> bool:
        return str(row.get("metric_id") or "").removesuffix("_delta") in {
            "bernoulli_brier",
            "multiclass_brier",
            "mean_squared_payout_loss",
        }

    @staticmethod
    def _is_tail_metric(row: dict) -> bool:
        return (
            row.get("target_type") == "bernoulli"
            and str(row.get("metric_id") or "").removesuffix("_delta")
            == "bernoulli_log_loss"
        )

    @staticmethod
    def _paired_delta(row: dict) -> Decimal | None:
        """Recompute baseline proper loss from the frozen row evidence.

        ``score_value`` normally stores candidate raw proper loss.  Legacy
        explicit ``*_delta`` observations already store the paired value and
        are accepted without reinterpreting their sign as a raw loss.
        """
        score_value = row.get("score_value")
        metric_id = str(row.get("metric_id") or "")
        if score_value is None:
            return None
        candidate = _decimal(score_value, "prediction_candidate_loss")
        if metric_id.endswith("_delta"):
            return candidate
        target_type = row.get("target_type")
        resolution_state = row.get("resolution_state")
        baseline_value = row.get("baseline_value")
        if isinstance(baseline_value, str):
            try:
                baseline_value = json.loads(baseline_value)
            except json.JSONDecodeError:
                return None
        if not isinstance(baseline_value, dict):
            return None
        epsilon = _metric_epsilon()
        try:
            if target_type == "bernoulli":
                baseline = _decimal(row.get("baseline_quote"), "prediction_baseline")
                outcome = 1 if resolution_state == row.get("canonical_side") else 0
                if metric_id == "bernoulli_brier":
                    baseline_loss = bernoulli_brier(baseline, outcome)
                elif metric_id == "bernoulli_log_loss":
                    baseline_loss = bernoulli_log_loss(baseline, outcome, epsilon)
                else:
                    return None
            elif target_type == "multiclass":
                members = list(row.get("members") or [])
                token_ids = [str(value) for value in (row.get("target_token_ids") or [])]
                if not token_ids:
                    token_ids = sorted(baseline_value, key=lambda value: int(value))
                if len(token_ids) != len(members):
                    return None
                probs = [
                    _decimal(baseline_value[token_id], "prediction_baseline_vector")
                    for token_id in token_ids
                ]
                one_hot = [1 if resolution_state == member else 0 for member in members]
                if metric_id == "multiclass_brier":
                    baseline_loss = multiclass_brier(probs, one_hot)
                elif metric_id == "multiclass_log_loss":
                    baseline_loss = multiclass_log_loss(probs, one_hot, epsilon)
                else:
                    return None
            elif target_type == "mean_only":
                baseline = _decimal(row.get("baseline_quote"), "prediction_baseline")
                raw = row.get("raw_outcome")
                cashflow = row.get("token_cashflow")
                if isinstance(raw, dict) and raw.get("actual_mean") is not None:
                    actual = _decimal(raw["actual_mean"], "prediction_actual_mean")
                elif isinstance(cashflow, dict) and cashflow.get("mean") is not None:
                    actual = _decimal(cashflow["mean"], "prediction_actual_mean")
                else:
                    return None
                if metric_id != "mean_squared_payout_loss":
                    return None
                baseline_loss = mean_squared_payout_loss(baseline, actual)
            else:
                return None
        except (KeyError, TypeError, ValueError):
            return None
        return delta_loss(candidate, baseline_loss)

    # ---------------- DB reads ----------------

    async def _load_submission(self, uow: UnitOfWork, submission_id: int) -> dict | None:
        result = await uow.session.execute(
            text(
                "SELECT id, q, u, status, committed_at, algorithm_hash, episode_id "
                "FROM trading.forecast_submissions WHERE id=:sid"
            ),
            {"sid": submission_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def _load_target(self, uow: UnitOfWork, score_target_id: int) -> dict | None:
        result = await uow.session.execute(
            text("SELECT * FROM trading.score_targets WHERE id=:tid"),
            {"tid": score_target_id},
        )
        rows = _rows(result)
        if not rows:
            return None
        target = rows[0]
        members = await uow.session.execute(
            text(
                "SELECT token_id, member_weight FROM trading.score_target_memberships "
                "WHERE score_target_id=:tid ORDER BY token_id"
            ),
            {"tid": score_target_id},
        )
        member_rows = _rows(members)
        target["membership_token_ids"] = [row["token_id"] for row in member_rows]
        target["membership_token_id"] = (
            member_rows[0]["token_id"] if member_rows else None
        )
        return target

    async def _load_contract_material(
        self, uow: UnitOfWork, contract_spec_id: int
    ) -> tuple[list[str] | None, dict[int, dict], dict]:
        spec_result = await uow.session.execute(
            text(
                "SELECT kc_resolution_states FROM trading.contract_specs WHERE id=:cs"
            ),
            {"cs": contract_spec_id},
        )
        spec_row = spec_result.first()
        if spec_row is None:
            return None, {}, {}
        R_c = list(spec_row[0])
        payout_result = await uow.session.execute(
            text(
                "SELECT pm_token_id, function_ir FROM trading.payout_functions "
                "WHERE contract_spec_id=:cs ORDER BY outcome_index"
            ),
            {"cs": contract_spec_id},
        )
        payouts = {int(row[0]): row[1] for row in payout_result.fetchall()}
        hc_result = await uow.session.execute(
            text(
                "SELECT h_c FROM trading.forecast_component_contract_specs "
                "WHERE contract_spec_id=:cs LIMIT 1"
            ),
            {"cs": contract_spec_id},
        )
        hc_row = hc_result.first()
        return R_c, payouts, (hc_row[0] if hc_row else {})

    async def _authoritative_baseline(
        self, uow: UnitOfWork, target: dict, submission: dict, binding_ids: list[int]
    ) -> dict[str, Any] | None:
        """Validate the immutable quote binding pinned at blind-commit time."""
        committed_at = submission.get("committed_at")
        if committed_at is None:
            return None
        token_ids = target.get("membership_token_ids") or []
        if not token_ids:
            return None
        if len(binding_ids) != len(set(binding_ids)):
            return None
        result = await uow.session.execute(
            text(
                "SELECT b.id, t.id AS internal_token_id, "
                "       (b.best_bid + b.best_ask) / 2 AS mid, "
                "       b.checkpoint_received_at, b.as_of, b.received_at, "
                "       b.stale_at, cp.validity "
                "FROM trading.pm_quote_bindings b "
                "JOIN trading.pm_tokens t ON t.token_id=b.token_id "
                "JOIN trading.pm_book_checkpoints cp "
                "  ON cp.id=b.checkpoint_id "
                " AND cp.received_at=b.checkpoint_received_at "
                " AND cp.token_id=b.token_id "
                "WHERE b.id = ANY(:bids) ORDER BY t.id"
            ),
            {"bids": binding_ids},
        )
        rows = result.fetchall()
        if len(rows) != len(binding_ids):
            return None
        value: dict[str, Decimal] = {}
        checkpoint_times: list[datetime] = []
        for row in rows:
            internal_token_id = int(row[1])
            if internal_token_id not in token_ids or row[7] != "VALID":
                return None
            checkpoint_received_at, as_of, received_at, stale_at = row[3:7]
            if (
                checkpoint_received_at > committed_at
                or as_of > committed_at
                or received_at > committed_at
                or stale_at <= committed_at
            ):
                return None
            value[str(internal_token_id)] = _decimal(row[2], "score_baseline_mid")
            checkpoint_times.append(checkpoint_received_at)
        if set(map(int, value)) != set(map(int, token_ids)):
            return None
        if len(set(checkpoint_times)) != 1:
            # Frozen P3 baseline policy uses zero-second vector sync tolerance.
            return None
        if target["target_type"] == "multiclass" and sum(value.values()) != Decimal("1"):
            return None
        scalar = None if target["target_type"] == "multiclass" else value[str(token_ids[0])]
        return {
            "scalar": scalar,
            "value": value,
            "value_hash": canonical_hash(_jsonable(value)),
            "checkpoint_received_at": max(checkpoint_times),
        }

    async def _decision_matches_submission(
        self, uow: UnitOfWork, decision_id: int, submission_id: int
    ) -> bool:
        result = await uow.session.execute(
            text(
                "SELECT 1 FROM trading.trade_decisions "
                "WHERE id=:decision AND forecast_submission_id=:submission"
            ),
            {"decision": decision_id, "submission": submission_id},
        )
        return result.first() is not None

    async def _external_token_id(self, uow: UnitOfWork, token_id: int) -> str | None:
        result = await uow.session.execute(
            text("SELECT token_id FROM trading.pm_tokens WHERE id=:tid"),
            {"tid": token_id},
        )
        row = result.first()
        return row[0] if row is not None else None

    async def _load_observations_by_metric(
        self, uow: UnitOfWork, metric_id: str
    ) -> list[dict]:
        result = await uow.session.execute(
            text(
                "SELECT * FROM trading.score_observations WHERE metric_id=:mi "
                "ORDER BY id"
            ),
            {"mi": metric_id},
        )
        return _rows(result)

    async def _load_run_observations(
        self, uow: UnitOfWork, input_: MetricRunInput
    ) -> list[dict]:
        """run 的观察集由冻结 cohort 定义（``cohort_query_hash``）决定；split + label
        versions 是其确定性投影。strategy/release 记录在 metric_runs 上作为 run 绑定，
        不在每行观察上重复（观察不直接携带 strategy）。"""
        result = await uow.session.execute(
            text(
                "SELECT so.*, s.q, s.committed_at, s.status AS submission_status, "
                "       s.episode_id, rl.state AS label_state, rl.resolution_state, "
                "       rl.raw_outcome, rl.token_cashflow, "
                "       st.target_type, st.canonical_side, st.members, st.contract_spec_id, "
                "       st.resolution_cluster_id AS cluster_id, st.horizon, "
                "       (c.horizon || ':' || c.time_block_start::text || ':' || "
                "        c.time_block_end::text) AS time_block, "
                "       COALESCE(tm.market_ids, ARRAY[]::bigint[]) AS market_ids, "
                "       COALESCE(tm.token_ids, ARRAY[]::bigint[]) AS target_token_ids "
                "FROM trading.score_observations so "
                "JOIN trading.forecast_submissions s ON s.id=so.submission_id "
                "JOIN trading.forecast_episodes e ON e.id=s.episode_id "
                "JOIN trading.decision_opportunities dop "
                "  ON dop.id=e.decision_opportunity_id "
                "JOIN trading.resolution_labels rl ON rl.id=so.label_version_id "
                "JOIN trading.score_targets st ON st.id=so.score_target_id "
                "JOIN trading.resolution_clusters c ON c.id=st.resolution_cluster_id "
                "LEFT JOIN LATERAL ("
                "  SELECT array_agg(DISTINCT pt.market_id ORDER BY pt.market_id) AS market_ids, "
                "         array_agg(stm.token_id ORDER BY pt.outcome_index, stm.token_id) "
                "           AS token_ids "
                "    FROM trading.score_target_memberships stm "
                "    JOIN trading.pm_tokens pt ON pt.id=stm.token_id "
                "   WHERE stm.score_target_id=st.id"
                ") tm ON TRUE "
                "LEFT JOIN trading.trade_decisions d ON d.id=so.trade_decision_id "
                "WHERE so.split=:split "
                "  AND dop.cohort_id=:cohort AND e.strategy_version_id=:strategy "
                "  AND (d.id IS NULL OR d.release_manifest_id=:release) "
                "  AND c.horizon = ANY(:horizons) "
                "  AND (CAST(:time_blocks AS jsonb)->>c.horizon) ~ '^\\d{4}-\\d{2}-\\d{2}$' "
                "  AND (CAST(:time_blocks AS jsonb)->>c.horizon)::date "
                "      BETWEEN c.time_block_start::date AND c.time_block_end::date "
                "ORDER BY so.id"
            ),
            {
                "split": input_.split,
                "cohort": input_.cohort_id,
                "strategy": input_.strategy_version_id,
                "release": input_.release_manifest_id,
                "horizons": sorted(str(key) for key in input_.time_blocks),
                "time_blocks": json.dumps(input_.time_blocks),
            },
        )
        return _rows(result)

    async def _attach_clusters(
        self, uow: UnitOfWork, rows: list[dict]
    ) -> list[dict]:
        spec_ids = {row["contract_spec_id"] for row in rows if row.get("contract_spec_id")}
        cluster_map: dict[int, int] = {}
        if spec_ids:
            result = await uow.session.execute(
                text(
                    "SELECT contract_spec_id, resolution_cluster_id "
                    "FROM trading.resolution_cluster_memberships "
                    "WHERE contract_spec_id = ANY(:specs)"
                ),
                {"specs": list(spec_ids)},
            )
            for row in result.fetchall():
                cluster_map[int(row[0])] = int(row[1])
        for row in rows:
            row["cluster_id"] = cluster_map.get(int(row.get("contract_spec_id") or -1))
        return rows

    @staticmethod
    def _flatten_label_versions(label_versions: dict) -> list[int]:
        out: list[int] = []
        for value in (label_versions or {}).values():
            if isinstance(value, list):
                out.extend(int(v) for v in value)
            else:
                out.append(int(value))
        return list(dict.fromkeys(out))

    # ---------------- promotion guardrails ----------------

    async def _promotion_guardrail(
        self, uow: UnitOfWork, metric_run: dict | None, input_: PromotionDecisionInput
    ) -> str | None:
        if metric_run is None:
            return "promotion_metric_run_missing"
        if metric_run["status"] != "COMPLETED":
            return "promotion_metric_run_not_completed"
        # Evidence is the immutable completed metric artifact.  Promotion policy
        # identity is independently bound by the G8 decision's policy_hash.
        if input_.evidence_manifest_hash != metric_run.get("artifact_hash"):
            return "promotion_evidence_manifest_mismatch"
        if input_.from_ref == input_.to_ref:
            return "promotion_from_to_required"
        if metric_run["split"] in ("train", "validation"):
            return "promotion_train_validation_only_result"
        if metric_run["split"] != "forward_holdout":
            return "promotion_forward_holdout_required"
        if await self._holdout_tampered(uow, metric_run):
            return "promotion_holdout_tampered"
        if await self._run_has_inadmissible_label(uow, metric_run):
            return "promotion_inadmissible_label"
        results = metric_run.get("results") or {}
        if isinstance(results, str):
            try:
                results = json.loads(results)
            except json.JSONDecodeError:
                return "promotion_metric_results_invalid"
        for layer in ("prediction", "selection", "edge", "portfolio", "execution"):
            evidence = results.get(layer) if isinstance(results, dict) else None
            if not isinstance(evidence, dict) or evidence.get("hard_guardrail_pass") is not True:
                return f"promotion_hard_guardrail_failed:{layer}"
        portfolio = results["portfolio"]
        if portfolio.get("not_evaluable") is True or portfolio.get("system_net") is None:
            return "promotion_portfolio_not_evaluable"
        if _decimal(portfolio["system_net"], "promotion_system_net") <= 0:
            return "promotion_system_net_nonpositive"
        if _decimal(metric_run.get("n_eff", 0), "promotion_n_eff") <= 1:
            return "promotion_low_power"
        if not await self._promotion_experiment_matches(uow, metric_run, input_):
            return "promotion_experiment_manifest_mismatch"
        if not await self._release_is_shadow_zero_capital(uow, metric_run):
            return "promotion_capital_permission_mismatch"
        return None

    async def _promotion_experiment_matches(
        self, uow: UnitOfWork, metric_run: dict, input_: PromotionDecisionInput
    ) -> bool:
        result = await uow.session.execute(
            text(
                "SELECT 1 FROM trading.experiments e "
                "JOIN trading.experiment_variants champion "
                "  ON champion.experiment_id=e.id AND champion.variant_type='champion' "
                "JOIN trading.experiment_variants challenger "
                "  ON challenger.experiment_id=e.id AND challenger.variant_type='challenger' "
                "JOIN trading.challenger_variants cv "
                "  ON cv.experiment_id=e.id AND cv.variant_key=challenger.variant_key "
                "WHERE e.status='COMPLETED' "
                "  AND e.champion_input_manifest_hash=:from_ref "
                "  AND e.challenger_input_manifest_hash=:to_ref "
                "  AND champion.input_manifest_hash=:from_ref "
                "  AND challenger.input_manifest_hash=:to_ref "
                "  AND challenger.strategy_version_id=:strategy "
                "  AND challenger.release_manifest_id=:release "
                "  AND cv.status='ACTIVE' "
                "  AND (SELECT count(*) FROM jsonb_object_keys(cv.changed_fields))=1 "
                "  AND cv.changed_fields ? e.unique_change_field "
                "  AND e.seed=:seed "
                "  AND NOT EXISTS ("
                "    SELECT 1 FROM jsonb_each_text(CAST(:time_blocks AS jsonb)) tb "
                "    WHERE tb.value !~ '^\\d{4}-\\d{2}-\\d{2}$' "
                "       OR tb.value < e.time_block_start::date::text "
                "       OR tb.value > e.time_block_end::date::text"
                "  ) "
                "  AND (NOT (e.sample_policy ? 'split') "
                "       OR e.sample_policy->>'split'=:split) "
                "  AND (NOT (e.sample_policy ? 'min_n_eff') OR ("
                "       (e.sample_policy->>'min_n_eff') ~ '^[0-9]+(?:\\.[0-9]+)?$' "
                "       AND :n_eff >= (e.sample_policy->>'min_n_eff')::numeric)) "
                "  AND (NOT (e.stopping_rule ? 'min_n_eff') OR ("
                "       (e.stopping_rule->>'min_n_eff') ~ '^[0-9]+(?:\\.[0-9]+)?$' "
                "       AND :n_eff >= (e.stopping_rule->>'min_n_eff')::numeric)) LIMIT 1"
            ),
            {
                "from_ref": input_.from_ref,
                "to_ref": input_.to_ref,
                "strategy": metric_run["strategy_version_id"],
                "release": metric_run["release_manifest_id"],
                "seed": metric_run["seed"],
                "time_blocks": json.dumps(metric_run.get("time_blocks") or {}),
                "split": metric_run["split"],
                "n_eff": metric_run["n_eff"],
            },
        )
        return result.first() is not None

    async def _holdout_tampered(self, uow: UnitOfWork, metric_run: dict) -> bool:
        label_ids = self._flatten_label_versions(metric_run.get("label_versions") or {})
        if not label_ids:
            return False
        result = await uow.session.execute(
            text(
                "SELECT 1 FROM trading.resolution_labels rl "
                "JOIN trading.resolution_cluster_memberships m "
                "  ON m.contract_spec_id=rl.contract_spec_id "
                "JOIN trading.resolution_clusters c "
                "  ON c.id=m.resolution_cluster_id "
                # Labels created after a frozen pre-outcome assignment are normal.
                # Contamination is scoped to this run and requires an outcome-known
                # fact that predates (or equals) the assignment itself.
                "WHERE rl.id=ANY(:labels) AND c.split=:split "
                "  AND rl.state IN ('provisional','disputed','final_admissible','final_excluded') "
                "  AND rl.created_at <= m.added_at LIMIT 1"
            ),
            {"labels": label_ids, "split": metric_run["split"]},
        )
        return result.first() is not None

    async def _release_is_shadow_zero_capital(
        self, uow: UnitOfWork, metric_run: dict
    ) -> bool:
        result = await uow.session.execute(
            text(
                "SELECT 1 FROM trading.release_manifests r "
                "JOIN trading.capital_permission_manifests p "
                "  ON p.id=r.capital_permission_manifest_id "
                "WHERE r.id=:release AND p.mode='shadow' "
                "  AND p.authorized_capital=0 AND p.status='active'"
            ),
            {"release": metric_run["release_manifest_id"]},
        )
        return result.first() is not None

    async def _run_has_inadmissible_label(
        self, uow: UnitOfWork, metric_run: dict
    ) -> bool:
        label_ids = self._flatten_label_versions(metric_run.get("label_versions") or {})
        if not label_ids:
            return False
        result = await uow.session.execute(
            text(
                "SELECT 1 FROM trading.resolution_labels "
                "WHERE id = ANY(:lv) AND state <> 'final_admissible' LIMIT 1"
            ),
            {"lv": label_ids},
        )
        return result.first() is not None

    async def _metric_run_by_id(
        self, uow: UnitOfWork, metric_run_id: int
    ) -> dict | None:
        result = await uow.session.execute(
            text("SELECT * FROM trading.metric_runs WHERE id=:mid"),
            {"mid": metric_run_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def _experiment_by_key(
        self, uow: UnitOfWork, experiment_key: str
    ) -> dict | None:
        result = await uow.session.execute(
            text("SELECT * FROM trading.experiments WHERE experiment_key=:k"),
            {"k": experiment_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def _variant(
        self, uow: UnitOfWork, experiment_id: int, variant_type: str
    ) -> dict | None:
        result = await uow.session.execute(
            text(
                "SELECT * FROM trading.experiment_variants "
                "WHERE experiment_id=:e AND variant_type=:vt"
            ),
            {"e": experiment_id, "vt": variant_type},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def _challenger_cfg(
        self, uow: UnitOfWork, experiment_id: int, variant_key: str
    ) -> dict | None:
        result = await uow.session.execute(
            text(
                "SELECT * FROM trading.challenger_variants "
                "WHERE experiment_id=:e AND variant_key=:vk"
            ),
            {"e": experiment_id, "vk": variant_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None
