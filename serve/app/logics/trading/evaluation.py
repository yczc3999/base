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
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
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

_SPEC_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests" / "trading" / "fixtures" / "p3_learning" / "p_evaluation_spec_v1.json"
)


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


def _decimal(value: Any, path: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{path}_bool_or_float_forbidden")
    return Decimal(str(value))


def _p3_spec() -> dict:
    with open(_SPEC_PATH, encoding="utf-8") as f:
        return json.load(f)


def _jsonable(value: Any) -> Any:
    """递归把 Decimal 转规范化字符串，供 JSONB 存储（与 canonical_hash 口径一致）。"""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _promotion_policy_hash() -> str:
    return canonical_hash(_p3_spec()["promotion_policy"])


def _metric_epsilon() -> Decimal:
    return Decimal(_p3_spec()["metric_policy"]["bernoulli_epsilon"])


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

        R_c, payouts, h_c = await self._load_contract_material(
            uow, target["contract_spec_id"]
        )
        if R_c is None:
            return ScoreResult(False, reason="score_contract_spec_missing")

        baseline = await self._authoritative_baseline(uow, target, submission)
        if baseline is None:
            # 冻结 baseline_policy：缺失/陈旧 → 显式 excluded，禁止未来 quote 回填。
            return ScoreResult(False, reason="score_baseline_missing", state="excluded")
        if _decimal(input_.baseline_quote, "score_baseline_quote") != baseline:
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
        if _decimal(input_.score_value, "score_value") != computed:
            return ScoreResult(False, reason="score_value_authority_mismatch")

        observation_id = await self._evaluation.insert_score_observation(
            uow.session,
            observation_key=input_.observation_key,
            score_target_id=input_.score_target_id,
            submission_id=input_.submission_id,
            trade_decision_id=input_.trade_decision_id,
            label_version_id=input_.label_version_id,
            baseline_quote=input_.baseline_quote,
            baseline_policy_hash=input_.baseline_policy_hash,
            split=input_.split,
            algorithm_hash=input_.algorithm_hash,
            metric_id=input_.metric_id,
            score_value=computed,
        )
        return ScoreResult(True, observation_id=observation_id)

    # ---------------- guardrails ----------------

    async def score_observation_guardrails(
        self, uow: UnitOfWork, *, metric_id: str
    ) -> dict:
        """full forecast-set 与 selected action-set 两组都要产出，prediction loss 与 system
        net 不能互相替代。"""
        rows = await self._load_observations_by_metric(uow, metric_id)
        full = rows
        selected = [row for row in rows if row.get("trade_decision_id") is not None]
        return {
            "full_forecast_set": self._aggregate_scores(full),
            "selected_action_set": self._aggregate_scores(selected),
        }

    # ---------------- metric run ----------------

    async def run_metric(
        self, uow: UnitOfWork, *, input_: MetricRunInput
    ) -> MetricRunResult:
        obs_rows = await self._load_run_observations(uow, input_)
        results, ci = self._compute_five_layers(uow, input_, obs_rows)
        n_market, n_episode, n_cluster, n_eff = self._run_sizes(uow, input_, obs_rows)
        artifact_hash = canonical_hash(
            {"run_key": input_.run_key, "results": results, "ci": ci}
        )
        run_id = await self._evaluation.insert_metric_run(
            uow.session,
            run_key=input_.run_key,
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
        if reason is not None:
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
            return ExperimentResult(
                True, experiment_id=experiment["id"], status="INVALIDATED",
                reason="experiment_multiple_factors_changed",
            )
        if champion["input_manifest_hash"] == challenger["input_manifest_hash"]:
            return ExperimentResult(
                True, experiment_id=experiment["id"], status="INVALIDATED",
                reason="experiment_manifests_not_distinct",
            )
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
        baseline: Decimal,
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
                return delta_loss(
                    bernoulli_brier(p, outcome), bernoulli_brier(baseline, outcome)
                )
            if metric_id == "bernoulli_log_loss_delta":
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

    def _compute_five_layers(
        self, uow: UnitOfWork, input_: MetricRunInput, obs_rows: list[dict]
    ) -> tuple[dict, dict]:
        full = obs_rows
        selected = [row for row in obs_rows if row.get("trade_decision_id") is not None]

        prediction_full = self._aggregate_scores(full)
        prediction_selected = self._aggregate_scores(selected)
        prediction = {
            "full_forecast_set": prediction_full,
            "selected_action_set": prediction_selected,
        }
        selection = {
            "opportunity_coverage": self._coverage(full, selected),
            "no_action_regret": None,
            "selection_triage_auc": None,
        }
        edge = {
            "edge_bucket_monotonicity": edge_bucket_monotonicity([]),
            "blind_to_decision_delay_erosion": round_score(Decimal("0")),
        }
        portfolio = self._portfolio_layer()
        execution = execution_metrics([])
        results = {
            "prediction": prediction,
            "selection": selection,
            "edge": edge,
            "portfolio": portfolio,
            "execution": execution,
        }
        cluster_losses = self._cluster_losses(obs_rows)
        bootstrap: dict = {}
        if cluster_losses:
            bootstrap = cluster_bootstrap(cluster_losses, seed=input_.seed)
        ci = {"prediction": {"full_forecast_set": bootstrap}}
        return results, ci

    def _run_sizes(
        self, uow: UnitOfWork, input_: MetricRunInput, obs_rows: list[dict]
    ) -> tuple[int, int, int, Decimal]:
        markets = {row["market_id"] for row in obs_rows if row.get("market_id")}
        episodes = {row["episode_id"] for row in obs_rows if row.get("episode_id")}
        clusters = {row["cluster_id"] for row in obs_rows if row.get("cluster_id")}
        n_eff: Decimal = Decimal("0")
        cluster_losses = self._cluster_losses(obs_rows)
        if cluster_losses:
            sizes = [len(c) for c in cluster_losses]
            total = sum(sizes)
            denom = sum(s * s for s in sizes)
            if denom:
                from app.domain.trading.scoring import n_eff as _n_eff

                n_eff = _n_eff(sizes, total)
        return len(markets), len(episodes), len(clusters), n_eff

    def _cluster_losses(self, obs_rows: list[dict]) -> list[list[Decimal]]:
        by_cluster: dict[int, list[Decimal]] = {}
        for row in obs_rows:
            cluster_id = row.get("cluster_id")
            if cluster_id is None:
                continue
            by_cluster.setdefault(int(cluster_id), []).append(
                _decimal(row["score_value"], "score_value")
            )
        return [values for _, values in sorted(by_cluster.items()) if values]

    def _portfolio_layer(self) -> dict:
        # 真实运行需提供 operating-cost period 与 ledger/action-set lineage；本层缺数据时
        # 显式 not_evaluable，不 0 填充。
        return portfolio_summary([])

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
        self, uow: UnitOfWork, target: dict, submission: dict
    ) -> Decimal | None:
        """权威 quote checkpoint 当时价；缺失/陈旧 → None（显式 excluded，禁未来回填）。"""
        committed_at = submission.get("committed_at")
        if committed_at is None:
            return None
        token_ids = target.get("membership_token_ids") or []
        if not token_ids:
            return None
        token_id = token_ids[0]
        ext = await self._external_token_id(uow, token_id)
        if ext is None:
            return None
        result = await uow.session.execute(
            text(
                "SELECT best_ask FROM trading.pm_book_checkpoints "
                "WHERE token_id=:ext AND validity='VALID' AND received_at <= :at "
                "ORDER BY received_at DESC LIMIT 1"
            ),
            {"ext": ext, "at": committed_at},
        )
        row = result.first()
        if row is None or row[0] is None:
            return None
        return _decimal(row[0], "score_baseline_checkpoint")

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
        label_ids = self._flatten_label_versions(input_.label_versions)
        result = await uow.session.execute(
            text(
                "SELECT so.*, s.q, s.committed_at, s.status AS submission_status, "
                "       s.episode_id, rl.state AS label_state, rl.resolution_state, "
                "       st.target_type, st.canonical_side, st.members, st.contract_spec_id, "
                "       NULL::bigint AS market_id "
                "FROM trading.score_observations so "
                "JOIN trading.forecast_submissions s ON s.id=so.submission_id "
                "JOIN trading.resolution_labels rl ON rl.id=so.label_version_id "
                "JOIN trading.score_targets st ON st.id=so.score_target_id "
                "WHERE so.split=:split AND so.label_version_id = ANY(:lv) "
                "ORDER BY so.id"
            ),
            {
                "split": input_.split,
                "lv": label_ids or [-1],
            },
        )
        rows = _rows(result)
        return await self._attach_clusters(uow, rows)

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
        if input_.evidence_manifest_hash != _promotion_policy_hash():
            return "promotion_evidence_manifest_mismatch"
        if input_.from_ref == input_.to_ref:
            return "promotion_from_to_required"
        if metric_run["split"] in ("train", "validation"):
            return "promotion_train_validation_only_result"
        if metric_run["split"] != "forward_holdout":
            return "promotion_forward_holdout_required"
        if await self._holdout_tampered(uow):
            return "promotion_holdout_tampered"
        if await self._run_has_inadmissible_label(uow, metric_run):
            return "promotion_inadmissible_label"
        return None

    async def _holdout_tampered(self, uow: UnitOfWork) -> bool:
        result = await uow.session.execute(
            text(
                "SELECT 1 FROM trading.resolution_clusters c "
                "JOIN trading.resolution_cluster_memberships m ON m.resolution_cluster_id=c.id "
                "JOIN trading.resolution_labels rl "
                "  ON rl.contract_spec_id=m.contract_spec_id AND rl.state='final_admissible' "
                "WHERE c.split='forward_holdout' AND c.status IN ('OPEN','FROZEN') LIMIT 1"
            )
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
