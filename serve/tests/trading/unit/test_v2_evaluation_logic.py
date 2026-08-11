"""P3 evaluation / settlement / replay Logic 单测（WP-04 Checkpoint C）。

用 fake/in-memory repo + fake session（不连 DB，快速）覆盖：
delta_loss 符号、proper_loss_guard、score_observation 拒绝非 final label、
baseline 缺失 → excluded、champion/challenger hash 差异 → INVALIDATED、
capital promotion 恒拒、holdout tamper → promotion 拒、replay_original 两次 hash 全等、
error_review_selection 固定 seed 可复现 + taxonomy 拒绝未知值、五层报告 full vs selected 分开。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.trading.hashing import canonical_hash
from app.domain.trading.scoring import delta_loss, proper_loss_guard
from app.logics.trading.evaluation import (
    EvaluationLogic,
)
from app.domain.trading.evaluation_policy import evaluation_policy, evaluation_policy_hash
from app.logics.trading.replay import ReplayLogic
from app.logics.trading.settlement import SettlementLogic
from app.schemas.trading.evaluation import (
    MetricRunInput,
    PromotionDecisionInput,
    ScoreObservationInput,
)
from app.schemas.trading.settlement import LabelRevisionInput

D = Decimal

_TABLE_RE = re.compile(r"FROM trading\.([a-z_]+)")


class FakeResult:
    def __init__(self, rows, keys):
        self._rows = rows
        self._keys = keys

    def keys(self):
        return list(self._keys)

    def fetchall(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0][0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0][0] if self._rows else None

    def scalars(self):
        return [row[0] for row in self._rows]


class FakeSession:
    """按 SQL 中的表名返回 canned rows；未配置的表返回空。"""

    def __init__(self):
        self.rows_by_table: dict[str, tuple[list, list]] = {}

    def set(self, table: str, rows: list, keys: list):
        self.rows_by_table[table] = (list(rows), list(keys))

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        match = _TABLE_RE.search(sql)
        if match:
            table = match.group(1)
            if table in self.rows_by_table:
                rows, keys = self.rows_by_table[table]
                return FakeResult(list(rows), list(keys))
        return FakeResult([], ["id"])


class FakeUoW:
    def __init__(self, session: FakeSession):
        self.session = session


class FakeSettlementRepository:
    def __init__(self):
        self.labels: dict[int, dict] = {}
        self.clusters: dict[int, dict] = {}
        self._next_label = 1
        self._next_cluster = 1

    async def get_label_by_version(self, session, label_id):
        return self.labels.get(label_id)

    async def get_label_current(self, session, contract_spec_id, label_key):
        for row in self.labels.values():
            if row["contract_spec_id"] == contract_spec_id and row["label_key"] == label_key:
                superseded = any(
                    s["supersedes_id"] == row["id"] for s in self.labels.values()
                )
                if not superseded:
                    return row
        return None

    async def insert_label_revision(self, session, **kw):
        row = dict(kw)
        row["id"] = self._next_label
        self._next_label += 1
        self.labels[row["id"]] = row
        return row["id"]

    async def insert_cluster(self, session, **kw):
        row = dict(kw)
        row["id"] = self._next_cluster
        self._next_cluster += 1
        self.clusters[row["id"]] = row
        return row["id"]

    async def insert_cluster_membership(self, session, **kw):
        return None


class FakeEvaluationRepository:
    def __init__(self):
        self.observations: list[dict] = []
        self.promotions: list[dict] = []
        self.metric_runs: list[dict] = []
        self.error_reviews: list[dict] = []
        self.ablation_runs: list[dict] = []
        self._next = 1

    async def insert_score_observation(self, session, **kw):
        row = dict(kw)
        row["id"] = self._next
        self._next += 1
        self.observations.append(row)
        return row["id"]

    async def insert_metric_run(self, session, **kw):
        row = dict(kw)
        row["id"] = self._next
        row["status"] = "RUNNING"
        self._next += 1
        self.metric_runs.append(row)
        return row["id"]

    async def advance_metric_run_status(self, session, run_key, *, to_status, completed_at):
        for row in self.metric_runs:
            if row["run_key"] == run_key and row["status"] == "RUNNING":
                row["status"] = to_status
                row["completed_at"] = completed_at
                return True
        return False

    async def insert_promotion_decision(self, session, **kw):
        row = dict(kw)
        row["id"] = self._next
        self._next += 1
        self.promotions.append(row)
        return row["id"]

    async def insert_error_review(self, session, **kw):
        row = dict(kw)
        row["id"] = self._next
        self._next += 1
        self.error_reviews.append(row)
        return row["id"]

    async def insert_ablation_run(self, session, **kw):
        row = dict(kw)
        row["id"] = self._next
        self._next += 1
        self.ablation_runs.append(row)
        return row["id"]

    async def advance_experiment_status(
        self, session, experiment_id, *, from_status, to_status
    ):
        rows, keys = session.rows_by_table.get("experiments", ([], []))
        for index, values in enumerate(rows):
            row = dict(zip(keys, values))
            if row.get("id") == experiment_id and row.get("status") == from_status:
                row["status"] = to_status
                rows[index] = tuple(row[key] for key in keys)
                return True
        return False


class FakeAuditRepository:
    def __init__(self):
        self.replay_runs: list[dict] = []
        self._next = 1

    async def insert_replay_run(self, session, **kw):
        for row in self.replay_runs:
            if row["run_key"] == kw["run_key"]:
                if {k: row[k] for k in kw} != kw:
                    raise RuntimeError("replay_idempotency_conflict")
                return row["id"], False
        row = dict(kw)
        row["id"] = self._next
        self._next += 1
        self.replay_runs.append(row)
        return row["id"], True

    async def metric_run_by_artifact_hash(self, session, artifact_hash):
        rows, keys = session.rows_by_table.get("metric_runs", ([], []))
        for values in rows:
            row = dict(zip(keys, values))
            if row.get("artifact_hash") == artifact_hash:
                return row
        return None

    async def get_replay_run(self, session, run_key):
        return next((r for r in self.replay_runs if r["run_key"] == run_key), None)

    async def list_replay_runs(self, session, manifest_hash):
        return [r for r in self.replay_runs if r["manifest_hash"] == manifest_hash]


def _seed_contract_material(session: FakeSession, *, contract_spec_id=10,
                            yes_token=20, no_token=21):
    session.set(
        "contract_specs",
        [(["YES", "NO"], f"spec-{contract_spec_id}")],
        ["kc_resolution_states", "contract_key"],
    )
    session.set(
        "payout_functions",
        [(yes_token, {"YES": "1", "NO": "0"}),
         (no_token, {"YES": "0", "NO": "1"})],
        ["pm_token_id", "function_ir"],
    )
    session.set(
        "forecast_component_contract_specs",
        [({"w0": "YES", "w1": "NO"},)],
        ["h_c"],
    )


def _seed_score_target(session: FakeSession, *, contract_spec_id=10, yes_token=20,
                       target_type="bernoulli", canonical_side="YES", members=None):
    session.set(
        "score_targets",
        [(f"target-{target_type}", target_type, contract_spec_id, None,
          canonical_side, members, None, 1, None)],
        ["target_key", "target_type", "contract_spec_id", "payout_function_id",
         "canonical_side", "members", "payout_type", "id", "created_at"],
    )
    session.set(
        "score_target_memberships",
        [(yes_token, D("1.0"))],
        ["token_id", "member_weight"],
    )
    session.set(
        "pm_tokens",
        [("token-yes",), ("token-no",)],
        ["token_id"],
    )


def _seed_blind_submission(session: FakeSession, *, q=None, committed_at=None):
    session.set(
        "forecast_submissions",
        [(1, q or {"w0": "0.7", "w1": "0.3"}, [{"w0": "0.7", "w1": "0.3"}],
          "BLIND_COMMITTED", committed_at or datetime(2026, 8, 1, tzinfo=timezone.utc),
          "c" * 64, 7)],
        ["id", "q", "u", "status", "committed_at", "algorithm_hash", "episode_id"],
    )


def _final_admissible_label(sett: FakeSettlementRepository, *, state="final_admissible",
                            resolution_state="YES", raw_outcome=None,
                            token_cashflow=None):
    sett.labels[1] = {
        "id": 1, "contract_spec_id": 10, "label_key": "k1", "version_no": 3,
        "state": state, "resolution_state": resolution_state,
        "raw_outcome": raw_outcome or {},
        "token_cashflow": token_cashflow or {"20": "1"},
    }
    return 1


def _metric_observation_keys():
    return [
        "id", "observation_key", "score_target_id", "submission_id",
        "trade_decision_id", "label_version_id", "baseline_quote",
        "baseline_policy_hash", "split", "algorithm_hash", "metric_id", "score_value",
        "q", "committed_at", "submission_status", "episode_id", "label_state",
        "resolution_state", "target_type", "canonical_side", "members",
        "contract_spec_id", "token_cashflow", "cluster_id", "horizon", "time_block",
        "market_id",
    ]


def _score_input(**overrides) -> ScoreObservationInput:
    baseline_value = {"20": D("0.575")}
    base = dict(
        observation_key="o1", score_target_id=1, submission_id=1,
        label_version_id=1, baseline_quote=D("0.575"),
        baseline_quote_binding_ids=[1], baseline_value=baseline_value,
        baseline_value_hash=canonical_hash({"20": "0.575"}),
        baseline_checkpoint_received_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        baseline_policy_hash=evaluation_policy_hash("baseline_convention"),
        split="train", algorithm_hash="c" * 64, metric_id="bernoulli_brier",
        score_value=D("0.09"),
    )
    base.update(overrides)
    return ScoreObservationInput(**base)


def _seed_baseline_binding(session: FakeSession):
    at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    session.set(
        "pm_quote_bindings",
        [(1, 20, D("0.575"), at, at, at, at + timedelta(hours=1), "VALID")],
        ["id", "internal_token_id", "mid", "checkpoint_received_at", "as_of",
         "received_at", "stale_at", "validity"],
    )


# ---------------- pure functions ----------------

def test_delta_loss_sign_lower_is_better():
    assert delta_loss(D("0.30"), D("0.20")) == D("0.10")
    assert delta_loss(D("0.15"), D("0.25")) == D("-0.10")


def test_proper_loss_guard_only_final_admissible():
    assert proper_loss_guard("final_admissible") is True
    for state in ("pending", "provisional", "disputed", "final_excluded"):
        assert proper_loss_guard(state) is False


def test_final_admissible_conflict_detects_missing_token_and_h_mapping():
    logic = SettlementLogic(FakeSettlementRepository())
    input_ = LabelRevisionInput(
        contract_spec_id=10,
        label_key="label",
        state="final_admissible",
        resolution_state="YES",
        resolution_source="gamma",
        evidence_artifact_id=1,
        raw_outcome={"resolution": "YES"},
        token_cashflow={"20": "1"},
        policy_code_hash=evaluation_policy_hash("label_policy"),
        auditor_identity="auditor",
    )
    conflicts = logic._detect_conflicts(
        evaluation_policy("label_policy"),
        input_,
        ["YES", "NO"],
        {20: {"YES": "1", "NO": "0"}, 21: {"YES": "0", "NO": "1"}},
        {"w0": "NO"},
        "gamma",
    )
    assert "token_mapping" in conflicts
    assert "rule" in conflicts


# ---------------- score_observation ----------------

@pytest.mark.asyncio
async def test_score_observation_rejects_non_final_label():
    session = FakeSession()
    uow = FakeUoW(session)
    sett = FakeSettlementRepository()
    _final_admissible_label(sett, state="provisional")
    logic = EvaluationLogic(FakeEvaluationRepository(), sett)
    result = await logic.score_observation(uow, input_=_score_input())
    assert result.ok is False
    assert result.reason == "score_label_not_admissible"


@pytest.mark.asyncio
async def test_score_observation_baseline_missing_is_excluded():
    session = FakeSession()
    uow = FakeUoW(session)
    sett = FakeSettlementRepository()
    _final_admissible_label(sett)
    _seed_score_target(session)
    _seed_blind_submission(session)
    _seed_contract_material(session)
    # 不配置 pm_book_checkpoints → 权威 baseline 缺失。
    logic = EvaluationLogic(FakeEvaluationRepository(), sett)
    result = await logic.score_observation(uow, input_=_score_input())
    assert result.ok is False
    assert result.reason == "score_baseline_missing"
    assert result.state == "excluded"


@pytest.mark.asyncio
async def test_score_observation_computes_bernoulli_brier_golden():
    session = FakeSession()
    uow = FakeUoW(session)
    sett = FakeSettlementRepository()
    _final_admissible_label(sett, resolution_state="YES")
    _seed_score_target(session)
    _seed_blind_submission(session)
    _seed_contract_material(session)
    session.set(
        "pm_book_checkpoints",
        [(D("0.65"), datetime(2026, 8, 1, tzinfo=timezone.utc), "VALID")],
        ["best_ask", "received_at", "validity"],
    )
    _seed_baseline_binding(session)
    eval_repo = FakeEvaluationRepository()
    logic = EvaluationLogic(eval_repo, sett)
    result = await logic.score_observation(uow, input_=_score_input(score_value=D("0.09")))
    assert result.ok is True, result.reason
    assert eval_repo.observations and eval_repo.observations[0]["score_value"] == D("0.09")


@pytest.mark.asyncio
async def test_score_observation_rejects_caller_substituted_value():
    session = FakeSession()
    uow = FakeUoW(session)
    sett = FakeSettlementRepository()
    _final_admissible_label(sett, resolution_state="YES")
    _seed_score_target(session)
    _seed_blind_submission(session)
    _seed_contract_material(session)
    session.set(
        "pm_book_checkpoints",
        [(D("0.65"), datetime(2026, 8, 1, tzinfo=timezone.utc), "VALID")],
        ["best_ask", "received_at", "validity"],
    )
    _seed_baseline_binding(session)
    logic = EvaluationLogic(FakeEvaluationRepository(), sett)
    # caller 篡改 score_value → 权威拒绝（禁调用方替换）。
    result = await logic.score_observation(uow, input_=_score_input(score_value=D("0.99")))
    assert result.ok is False
    assert result.reason == "score_value_authority_mismatch"


# ---------------- champion / challenger ----------------

@pytest.mark.asyncio
async def test_champion_challenger_hash_difference_invalidates():
    session = FakeSession()
    uow = FakeUoW(session)
    session.set(
        "experiments",
        [(1, "exp-1", "h", "metric", {}, "strategy_version_id",
          "a" * 64, "b" * 64, {}, {}, 1, "PLANNED", None, None)],
        ["id", "experiment_key", "hypothesis_hash", "primary_metric", "guardrails",
         "unique_change_field", "champion_input_manifest_hash",
         "challenger_input_manifest_hash", "sample_policy", "stopping_rule",
         "seed", "status", "time_block_start", "time_block_end"],
    )
    session.set(
        "experiment_variants",
        [(1, "champion", "champion", "a" * 64, 1, 1),
         (1, "challenger", "challenger", "b" * 64, 1, 1)],
        ["experiment_id", "variant_key", "variant_type", "input_manifest_hash",
         "strategy_version_id", "release_manifest_id"],
    )
    # 唯一变化字段之外还改了第二个因素 → INVALIDATED。
    session.set(
        "challenger_variants",
        [(1, "challenger", "strategy", {"strategy_version_id": "x", "objective_id": "y"},
          "p" * 64, "ACTIVE")],
        ["experiment_id", "variant_key", "challenger_type", "changed_fields",
         "policy_hash", "status"],
    )
    logic = EvaluationLogic(FakeEvaluationRepository(), FakeSettlementRepository())
    result = await logic.champion_challenger_pair(uow, experiment_key="exp-1")
    assert result.ok is True
    assert result.status == "INVALIDATED"
    assert result.reason == "experiment_multiple_factors_changed"


# ---------------- promotion ----------------

@pytest.mark.asyncio
async def test_capital_promotion_always_rejected():
    session = FakeSession()
    uow = FakeUoW(session)
    eval_repo = FakeEvaluationRepository()
    logic = EvaluationLogic(eval_repo, FakeSettlementRepository())
    result = await logic.promote(
        uow,
        input_=PromotionDecisionInput(
            promotion_key="promo-cap", metric_run_id=1, promotion_type="capital",
            from_ref="a" * 64, to_ref="b" * 64, evidence_manifest_hash="c" * 64,
            status="APPROVED", capital_amount=D("0"),
        ),
    )
    assert result.ok is True
    assert result.status == "REJECTED"
    assert result.reason == "capital_promotion_fail_closed"
    assert eval_repo.promotions[0]["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_promotion_rejected_on_holdout_tamper():
    session = FakeSession()
    uow = FakeUoW(session)
    sett = FakeSettlementRepository()
    session.set(
        "metric_runs",
        [(1, "run-1", "a" * 64, 1, 1, {"v": [1]}, "forward_holdout", {},
          "c" * 64, "d" * 64, 42, 1, 1, 1, D("1"), {}, {}, "e" * 64,
          "COMPLETED", None)],
        ["id", "run_key", "cohort_query_hash", "strategy_version_id",
         "release_manifest_id", "label_versions", "split", "time_blocks",
         "code_hash", "config_hash", "seed", "n_market", "n_episode",
         "n_resolution_cluster", "n_eff", "results", "ci", "artifact_hash",
         "status", "completed_at"],
    )
    # Run-scoped assignment already had an outcome-known label at assignment.
    session.set("resolution_labels", [(1,)], ["id"])
    eval_repo = FakeEvaluationRepository()
    logic = EvaluationLogic(eval_repo, sett)
    result = await logic.promote(
        uow,
        input_=PromotionDecisionInput(
            promotion_key="promo-strat", metric_run_id=1, promotion_type="strategy",
            from_ref="a" * 64, to_ref="b" * 64,
            evidence_manifest_hash="e" * 64,
            status="APPROVED",
            future_effective_at=datetime.now(timezone.utc) + timedelta(days=1),
        ),
    )
    assert result.ok is True
    assert result.status == "REJECTED"
    assert result.reason == "promotion_holdout_tampered"
    assert eval_repo.promotions[0]["status"] == "REJECTED"


# ---------------- replay ----------------

@pytest.mark.asyncio
async def test_replay_original_twice_hash_equal(monkeypatch):
    session = FakeSession()
    uow = FakeUoW(session)
    manifest = canonical_hash({"run_key": "metric-1", "results": {}, "ci": {}})
    session.set(
        "metric_runs",
        [(1, "metric-1", "b" * 64, 7, manifest, "COMPLETED",
          datetime(2026, 8, 1, tzinfo=timezone.utc))],
        ["id", "run_key", "code_hash", "seed", "artifact_hash", "status", "completed_at"],
    )
    audit = FakeAuditRepository()
    logic = ReplayLogic(audit, FakeEvaluationRepository())
    async def recompute(_uow, source):
        return {
            "ok": True,
            "artifact_hash": manifest,
            "artifact": {"run_key": "metric-1", "results": {}, "ci": {}},
            "observation_set_hash": canonical_hash([]),
            "observations": [],
        }
    monkeypatch.setattr(logic, "_recompute_metric", recompute)
    first = await logic.replay_original(uow, run_key="replay-1", manifest_hash=manifest, seed=7)
    second = await logic.replay_original(uow, run_key="replay-2", manifest_hash=manifest, seed=7)
    assert first.ok and second.ok
    assert first.output_artifact_hash == second.output_artifact_hash
    assert first.output_artifact_hash is not None
    assert len(first.output_artifact_hash) == 64
    retry = await logic.replay_original(
        uow, run_key="replay-1", manifest_hash=manifest, seed=7
    )
    assert retry.ok and retry.idempotent


# ---------------- error review selection ----------------

def _seed_metric_run(session: FakeSession, *, run_key="run-1"):
    session.set(
        "metric_runs",
        [(1, run_key, 1, [1, 2, 3], "f" * 64,
          "a" * 64, 1, 1, {"v": [1, 2, 3]}, "forward_holdout", {},
          "c" * 64, "d" * 64, 42, 3, 3, 1, D("1"), {}, {}, "e" * 64,
          "COMPLETED", None)],
        ["id", "run_key", "cohort_id", "observation_ids", "observation_set_hash",
         "cohort_query_hash", "strategy_version_id",
         "release_manifest_id", "label_versions", "split", "time_blocks",
         "code_hash", "config_hash", "seed", "n_market", "n_episode",
         "n_resolution_cluster", "n_eff", "results", "ci", "artifact_hash",
         "status", "completed_at"],
    )


def _seed_observations(session: FakeSession):
    session.set(
        "score_observations",
        [
            ("obs-a", 1, 1, 5, 1, D("0.50"), "split", "alg", "bernoulli_brier",
             D("0.01"),),
            ("obs-b", 1, 1, None, 2, D("0.90"), "split", "alg", "bernoulli_brier",
             D("0.49"),),
            ("obs-c", 1, 1, None, 3, D("0.30"), "split", "alg", "bernoulli_brier",
             D("0.25"),),
        ],
        ["observation_key", "score_target_id", "submission_id", "trade_decision_id",
         "label_version_id", "baseline_quote", "split", "algorithm_hash",
         "metric_id", "score_value"],
    )


@pytest.mark.asyncio
async def test_error_review_selection_deterministic_and_taxonomy_rejects_unknown():
    session = FakeSession()
    uow = FakeUoW(session)
    _seed_metric_run(session)
    _seed_observations(session)
    eval_repo = FakeEvaluationRepository()
    logic = ReplayLogic(FakeAuditRepository(), eval_repo)

    first = await logic.error_review_selection(uow, metric_run_id=1, seed=42)
    second = await logic.error_review_selection(uow, metric_run_id=1, seed=42)
    assert first.ok and second.ok
    assert first.count == second.count
    keys_first = {(r["review_type"], r["observation_key"]) for r in eval_repo.error_reviews[:first.count]}
    keys_second = {(r["review_type"], r["observation_key"]) for r in eval_repo.error_reviews[first.count:]}
    assert keys_first == keys_second

    bad = await logic.error_review_selection(
        uow, metric_run_id=1, seed=42,
        explicit_taxonomies={"obs-a": "bogus_taxonomy"},
    )
    assert bad.ok is False
    assert bad.reason == "error_review_taxonomy_unknown:bogus_taxonomy"


# ---------------- five-layer report full vs selected ----------------

@pytest.mark.asyncio
async def test_five_layer_report_full_and_selected_separate():
    session = FakeSession()
    uow = FakeUoW(session)
    session.set(
        "score_observations",
        [
            ("obs-a", 1, 1, 5, 1, D("0.09")),
            ("obs-b", 1, 1, 6, 2, D("0.25")),
            ("obs-c", 1, 1, None, 3, D("0.49")),
        ],
        ["observation_key", "score_target_id", "submission_id", "trade_decision_id",
         "label_version_id", "score_value"],
    )
    session.set(
        "resolution_cluster_memberships",
        [(1, 100), (2, 200), (3, 300)],
        ["contract_spec_id", "resolution_cluster_id"],
    )
    # run_metric 需要观察行的完整列（含 join 列）。
    obs_keys = _metric_observation_keys()
    session.set(
        "score_observations",
        [
            (1, "obs-a", 1, 1, 5, 1, D("0.65"), "b" * 64, "train", "c" * 64,
             "bernoulli_brier", D("0.09"), {"w0": "0.7", "w1": "0.3"},
             datetime(2026, 8, 1, tzinfo=timezone.utc), "BLIND_COMMITTED", 7,
             "final_admissible", "YES", "bernoulli", "YES", None, 1,
             {"20": "1"}, 100, "resolution", "resolution:t0", 50),
            (2, "obs-b", 1, 1, 6, 2, D("0.65"), "b" * 64, "train", "c" * 64,
             "bernoulli_brier", D("0.25"), {"w0": "0.7", "w1": "0.3"},
             datetime(2026, 8, 1, tzinfo=timezone.utc), "BLIND_COMMITTED", 7,
             "final_admissible", "NO", "bernoulli", "YES", None, 1,
             {"20": "0"}, 100, "resolution", "resolution:t0", 50),
            (3, "obs-c", 1, 1, None, 3, D("0.65"), "b" * 64, "train", "c" * 64,
             "bernoulli_brier", D("0.49"), {"w0": "0.7", "w1": "0.3"},
             datetime(2026, 8, 1, tzinfo=timezone.utc), "BLIND_COMMITTED", 7,
             "final_admissible", "YES", "bernoulli", "YES", None, 2,
             {"20": "1"}, 200, "resolution", "resolution:t0", 51),
        ],
        obs_keys,
    )
    eval_repo = FakeEvaluationRepository()
    logic = EvaluationLogic(eval_repo, FakeSettlementRepository())
    observation_material = {
        "cohort_id": 1,
        "split": "train",
        "ordered_observation_ids": [1, 2, 3],
        "label_versions": {"v": [1, 2, 3]},
        "time_blocks": {"t0": "2026-08-01"},
        "strategy_version_id": 1,
        "release_manifest_id": 1,
    }
    input_ = MetricRunInput(
        run_key="metric-1", cohort_id=1, observation_ids=[1, 2, 3],
        observation_set_hash=canonical_hash(observation_material),
        cohort_query_hash="a" * 64, strategy_version_id=1,
        release_manifest_id=1, label_versions={"v": [1, 2, 3]}, split="train",
        time_blocks={"t0": "2026-08-01"}, code_hash="c" * 64, config_hash="d" * 64,
        seed=42, n_market=2, n_episode=3, n_resolution_cluster=1, n_eff=D("1"),
        results={}, ci={}, artifact_hash="e" * 64,
    )
    result = await logic.run_metric(uow, input_=input_)
    assert result.ok is True, result.reason
    run = eval_repo.metric_runs[0]
    results = json.loads(run["results"])
    assert set(results.keys()) == {"prediction", "selection", "edge",
                                   "portfolio", "execution"}
    prediction = results["prediction"]
    assert {"full_forecast_set", "selected_action_set", "evaluable",
            "hard_guardrail_pass"} <= set(prediction)
    assert prediction["full_forecast_set"]["count"] == 3
    assert prediction["selected_action_set"]["count"] == 2
    assert run["status"] == "COMPLETED"


def test_run_sizes_uses_authoritative_target_market_union():
    logic = EvaluationLogic(FakeEvaluationRepository(), FakeSettlementRepository())
    rows = [
        {"market_ids": [50, 51], "episode_id": 7, "cluster_id": 100},
        {"market_ids": [51], "episode_id": 8, "cluster_id": 100},
    ]
    n_market, n_episode, n_cluster, _ = logic._run_sizes(None, None, rows)
    assert (n_market, n_episode, n_cluster) == (2, 2, 1)


def test_prediction_paired_delta_recomputes_raw_losses_for_all_target_types():
    # Positive raw losses can still be improvements over the exact frozen
    # baseline; their sign must never be used as a delta proxy.
    bernoulli = {
        "metric_id": "bernoulli_brier", "score_value": D("0.09"),
        "target_type": "bernoulli", "resolution_state": "YES",
        "canonical_side": "YES", "baseline_quote": D("0.5"),
        "baseline_value": {"20": "0.5"},
    }
    assert EvaluationLogic._paired_delta(bernoulli) == D("-0.16")

    multiclass = {
        "metric_id": "multiclass_brier", "score_value": D("0.08"),
        "target_type": "multiclass", "resolution_state": "A",
        "members": ["A", "B"], "target_token_ids": [20, 21],
        "baseline_value": {"20": "0.5", "21": "0.5"},
    }
    assert EvaluationLogic._paired_delta(multiclass) == D("-0.42")

    mean_only = {
        "metric_id": "mean_squared_payout_loss", "score_value": D("0.01"),
        "target_type": "mean_only", "baseline_quote": D("0.5"),
        "baseline_value": {"20": "0.5"},
        "raw_outcome": {"actual_mean": "0.8"},
    }
    assert EvaluationLogic._paired_delta(mean_only) == D("-0.08")


@pytest.mark.asyncio
async def test_portfolio_uses_posted_ledger_nets_reversal_and_keeps_excluded_economics():
    session = FakeSession()
    session.set(
        "ledger_transactions",
        [
            (1, 5, "shadow-champion", "FILL", datetime(2026, 8, 1, tzinfo=timezone.utc),
             "CASH", "usd", D("-0.4")),
            (1, 5, "shadow-champion", "FILL", datetime(2026, 8, 1, tzinfo=timezone.utc),
             "TOKEN", "tok:10:20", D("1")),
            (2, 5, "shadow-champion", "REVERSAL", datetime(2026, 8, 2, tzinfo=timezone.utc),
             "CASH", "usd", D("0.4")),
            (2, 5, "shadow-champion", "REVERSAL", datetime(2026, 8, 2, tzinfo=timezone.utc),
             "TOKEN", "tok:10:20", D("-1")),
            (3, 5, "shadow-champion", "FILL", datetime(2026, 8, 3, tzinfo=timezone.utc),
             "CASH", "usd", D("-0.3")),
            (3, 5, "shadow-champion", "FILL", datetime(2026, 8, 3, tzinfo=timezone.utc),
             "TOKEN", "tok:10:20", D("1")),
        ],
        ["transaction_id", "trade_decision_id", "portfolio_namespace", "kind",
         "posted_at", "asset_type", "asset_key", "amount"],
    )
    session.set("operating_cost_entries", [(1, 5, D("0.1"))],
                ["id", "trade_decision_id", "amount"])
    session.set("action_candidates", [(5, D("1"))],
                ["trade_decision_id", "capital_days"])
    logic = EvaluationLogic(FakeEvaluationRepository(), FakeSettlementRepository())
    summary, economic = await logic._portfolio_layer(
        FakeUoW(session),
        [{"id": 1, "status": "EXCLUDED", "trade_decision_id": 5,
          "contract_spec_id": 10, "token_cashflow": {"20": "1"}}],
    )
    assert summary["not_evaluable"] is False
    assert summary["system_net"] == D("0.6")
    assert economic["decision_system_net"] == {5: D("0.6")}
