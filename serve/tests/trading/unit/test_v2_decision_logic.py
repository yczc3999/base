"""WP-03 decision-chain authority and fail-closed counterexamples."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.logics.trading.decision import DecisionLogic
from app.logics.trading.portfolio import PortfolioExposure
from app.schemas.trading.decision import (
    ActionCandidateInput,
    ActionSetInput,
    MarketRelativeInput,
    PortfolioGateInput,
    UnderwritingInput,
)


NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)
HASH = "a" * 64


class FakeUoW:
    session = SimpleNamespace()


class FakeWorkflow:
    def __init__(self):
        self.calls = []

    async def insert_gate_decision(self, session, **kwargs):
        self.calls.append(kwargs)
        return kwargs


class FakePortfolio:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or PortfolioExposure(
            True, market_fraction=Decimal("0.01"), component_fraction=Decimal("0.01"),
            global_fraction=Decimal("0.01"), market_exposure=Decimal("52"),
            component_exposure=Decimal("52"), global_exposure=Decimal("52"),
            bankroll=Decimal("100000"),
        )

    @staticmethod
    def frozen_caps(limits):
        return Decimal("0.04"), Decimal("0.06"), Decimal("0.30")

    async def check_capacity(self, uow, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _context(status="QUOTE_BOUND", **over):
    row = {
        "id": 1, "decision_key": HASH, "status": status, "episode_id": 1,
        "episode_key": "episode-natural-key", "submission_key": "submission-natural-key",
        "decision_class": "CHAMPION",
        "forecast_submission_id": 5, "release_manifest_id": 3,
        "experiment_variant": "champion", "submission_status": "BLIND_COMMITTED",
        "lease_valid_until": NOW + timedelta(days=30), "release_status": "active",
        "exec_spec_status": "active", "capital_status": "active", "capital_mode": "shadow",
        "authorized_capital": Decimal("0"), "evaluation_capital": Decimal("100000"),
        "kill_switch": False, "exec_spec_hash": HASH, "capital_hash": "b" * 64,
        "limits": {
            "per_market_net_risk_capital_fraction": "0.04",
            "per_component_net_risk_capital_fraction": "0.06",
            "global_risk_capital_fraction": "0.30",
        },
        "exec_spec_content": {
            "price_convention": "executable_depth_walk",
            "staleness": {"ttl_seconds": 300, "max_quote_age_seconds": 300},
            "fee": {"taker_fee_bps": 0}, "slippage": {"bps": 0},
            "capacity": {"minimum_deployable_capacity": 1, "maximum_single_leg_units": 1000},
            "depth_walk": {"max_levels": 10},
        },
        "strategy_content": {},
    }
    row.update(over)
    return row


def _required():
    common = {
        "contract_spec_id": 1, "market_id": 1, "component_id": 1,
        "market_active": True, "market_closed": False, "accepting_orders": True,
        "enable_order_book": True, "end_date": None,
        "h_c": {"w0": "YES", "w1": "NO"},
    }
    return [
        {**common, "token_id": 1, "external_token_id": "1", "outcome_index": 0,
         "payout_ir": {"YES": "1", "NO": "0"}},
        {**common, "token_id": 2, "external_token_id": "2", "outcome_index": 1,
         "payout_ir": {"YES": "0", "NO": "1"}},
    ]


def _bound(yes_ask="0.52", no_ask="0.48"):
    rows = []
    for required, bid, ask, side_levels in (
        (_required()[0], "0.50", yes_ask, [{"side": "ask", "price": Decimal(yes_ask), "size": Decimal("100"), "ordinal": 0}]),
        (_required()[1], "0.46", no_ask, [{"side": "ask", "price": Decimal(no_ask), "size": Decimal("100"), "ordinal": 0}]),
    ):
        rows.append({**required, "checkpoint_id": required["token_id"] + 10,
                     "checkpoint_received_at": NOW, "best_bid": Decimal(bid),
                     "best_ask": Decimal(ask), "as_of": NOW, "received_at": NOW,
                     "stale_at": NOW + timedelta(minutes=5), "levels": side_levels})
    return rows


class FakeRepo:
    def __init__(self):
        self.responses = {
            "decision_material": {
                "episode_status": "BLIND_COMMITTED", "episode_key": "ep", "submission_id": 5,
                "submission_key": "sub", "forecast_input_manifest_hash": "c" * 64,
                "contract_schema_prior_evidence_hash": "d" * 64,
                "algorithm_hash": "e" * 64,
                "lease_id": 7, "valid_until": NOW + timedelta(days=30), "evidence_hash": HASH,
                "schema_hash": HASH, "spec_hash": HASH, "cohort_key": "c",
                "release_manifest_id": 3, "objective_contract_id": 2, "strategy_version_id": 4,
            },
            "release_by_episode": {
                "release_status": "active", "exec_spec_status": "active",
                "capital_status": "active", "mode": "shadow", "authorized_capital": 0,
                "kill_switch": False, "execution_spec_version_id": 11,
                "capital_permission_manifest_id": 12, "exec_spec_hash": HASH,
                "capital_hash": "b" * 64, "release_name": "release-natural-key",
                "release_hash": "f" * 64,
            },
            "episode_markets": [{"active": True, "closed": False, "accepting_orders": True,
                                  "enable_order_book": True, "end_date": None}],
            "decision_context": _context(), "required_tokens_for_decision": _required(),
            "bound_market_material": _bound(),
            "get_submission_qu": {"Q": {"w0": "0.6", "w1": "0.4"},
                                  "U": [{"w0": "0.6", "w1": "0.4"},
                                        {"w0": "0.55", "w1": "0.45"}]},
            "get_market_relative": {"q_decision": {"w0": "0.6", "w1": "0.4"},
                                    "u_decision": [{"w0": "0.6", "w1": "0.4"},
                                                   {"w0": "0.55", "w1": "0.45"}]},
            "allocated_operating_cost": {"evidence_count": 1, "amount": Decimal("0"),
                                         "policies": [{"evidence": "observed_zero"}]},
            "candidates_for_decision": [], "review_passed": True,
            "cognition_review_passed": True,
            "has_position_for_decision": True, "has_valid_underwriting": True,
            "get_action_intent_by_hash": None,
        }
        self.calls = []
        self.next_id = 100

    def _record(self, name, **kwargs):
        self.calls.append((name, kwargs))

    def calls_for(self, name):
        return [kwargs for method, kwargs in self.calls if method == name]

    async def decision_material(self, session, episode_id): return self.responses["decision_material"]
    async def release_by_episode(self, session, episode_id): return self.responses["release_by_episode"]
    async def episode_markets(self, session, episode_id): return self.responses["episode_markets"]
    async def cognition_review_passed(self, session, **kwargs): return self.responses["cognition_review_passed"]
    async def insert_trade_decision(self, session, **kwargs): self._record("insert_trade_decision", **kwargs); return 42
    async def decision_context(self, session, trade_decision_id, **kwargs): return self.responses["decision_context"]
    async def required_tokens_for_decision(self, session, trade_decision_id): return self.responses["required_tokens_for_decision"]

    async def checkpoint_material(self, session, *, external_token_id, checkpoint_id, checkpoint_received_at):
        row = next((r for r in _bound() if r["external_token_id"] == external_token_id), None)
        if row is None or row["checkpoint_id"] != checkpoint_id or checkpoint_received_at != NOW:
            return None
        return {"checkpoint_id": checkpoint_id, "external_token_id": external_token_id,
                "received_at": NOW, "best_bid": row["best_bid"], "best_ask": row["best_ask"],
                "completeness": True, "validity": "VALID", "levels": row["levels"]}

    async def bind_quote(self, session, **kwargs): self._record("bind_quote", **kwargs)
    async def mark_quote_bound(self, session, trade_decision_id, **kwargs): return True
    async def insert_discrepancy_review(self, session, **kwargs): self._record("review", **kwargs); return 1
    async def get_submission_qu(self, session, forecast_submission_id): return self.responses["get_submission_qu"]
    async def bound_market_material(self, session, trade_decision_id): return self.responses["bound_market_material"]
    async def insert_market_relative_decision(self, session, **kwargs): self._record("insert_mr", **kwargs); return 1
    async def get_market_relative(self, session, trade_decision_id): return self.responses["get_market_relative"]
    async def allocated_operating_cost(self, session, trade_decision_id): return self.responses["allocated_operating_cost"]
    async def insert_action_candidate(self, session, **kwargs): self._record("insert_candidate", **kwargs); return 300
    async def insert_cashflow(self, session, **kwargs): self._record("insert_cashflow", **kwargs)
    async def advance_status(self, session, trade_decision_id, **kwargs): self._record("advance", **kwargs); return True
    async def candidates_for_decision(self, session, trade_decision_id): return self.responses["candidates_for_decision"]
    async def review_passed(self, session, trade_decision_id): return self.responses["review_passed"]
    async def has_position_for_decision(self, session, trade_decision_id, namespace): return self.responses["has_position_for_decision"]
    async def has_valid_underwriting(self, session, trade_decision_id, at): return self.responses["has_valid_underwriting"]
    async def get_action_intent_by_hash(self, session, intent_hash): return self.responses["get_action_intent_by_hash"]
    async def insert_action_set(self, session, **kwargs): self._record("insert_action_set", **kwargs); return 400
    async def insert_action_set_leg(self, session, **kwargs): self._record("insert_leg", **kwargs)
    async def insert_underwriting_plan(self, session, **kwargs): self._record("insert_underwriting", **kwargs); return 1
    async def terminal_decision(self, session, trade_decision_id, **kwargs): self._record("terminal", **kwargs); return True
    async def insert_action_intent(self, session, **kwargs): self._record("insert_intent", **kwargs); return 500


def _quotes():
    return {
        "1": {"checkpoint_id": 11, "checkpoint_received_at": NOW,
              "best_bid": Decimal("0.50"), "best_ask": Decimal("0.52"),
              "as_of": NOW, "received_at": NOW, "stale_at": NOW + timedelta(minutes=5)},
        "2": {"checkpoint_id": 12, "checkpoint_received_at": NOW,
              "best_bid": Decimal("0.46"), "best_ask": Decimal("0.48"),
              "as_of": NOW, "received_at": NOW, "stale_at": NOW + timedelta(minutes=5)},
    }


def _candidate(**over):
    data = {"contract_spec_id": 1, "token_id": 1, "action_type": "BUY_TOKEN",
            "target_quantity": Decimal("100")}
    data.update(over)
    return ActionCandidateInput(**data)


def _candidate_row(**over):
    row = {"id": 300, "contract_spec_id": 1, "token_id": 1, "action_type": "BUY_TOKEN",
           "contract_key": "contract-natural-key", "contract_spec_hash": "c" * 64,
           "external_token_id": "external-token-natural-key",
           "fill_quantity": Decimal("100"), "vwap": Decimal("0.52"),
           "robust_ev": Decimal("3"), "expected_log_growth": Decimal("0.01"),
           "cost_components": {"entry": "52"}}
    row.update(over)
    return row


async def test_create_rejects_terminal_market_and_accepts_tradeable_market():
    repo = FakeRepo(); repo.responses["episode_markets"][0]["closed"] = True
    logic = DecisionLogic(repo, FakeWorkflow(), FakePortfolio())
    bad = await logic.create_decision(FakeUoW(), episode_id=1, trigger_at=NOW, experiment_variant="champion")
    assert not bad.ok and bad.reason == "decision_market_terminal"
    repo.responses["episode_markets"][0]["closed"] = False
    good = await logic.create_decision(FakeUoW(), episode_id=1, trigger_at=NOW, experiment_variant="champion")
    assert good.ok and good.trade_decision_id == 42
    assert repo.calls_for("insert_trade_decision")[-1]["decision_class"] == "CHAMPION"
    repo.responses["cognition_review_passed"] = False
    review = await logic.create_decision(
        FakeUoW(), episode_id=1, trigger_at=NOW, experiment_variant="review"
    )
    assert review.ok
    assert repo.calls_for("insert_trade_decision")[-1]["decision_class"] == "RISK_REVIEW"


async def test_create_input_hash_ignores_submission_lease_and_release_ids():
    repo = FakeRepo()
    logic = DecisionLogic(repo, FakeWorkflow(), FakePortfolio())
    first = await logic.create_decision(
        FakeUoW(), episode_id=1, trigger_at=NOW, experiment_variant="champion"
    )
    assert first.ok
    clean_hash = repo.calls_for("insert_trade_decision")[-1]["input_hash"]

    repo.responses["decision_material"].update(
        submission_id=5005,
        lease_id=7007,
        release_manifest_id=3003,
    )
    repo.responses["release_by_episode"].update(
        execution_spec_version_id=11011,
        capital_permission_manifest_id=12012,
    )
    retried = await logic.create_decision(
        FakeUoW(), episode_id=9009, trigger_at=NOW, experiment_variant="champion"
    )
    assert retried.ok
    retry_hash = repo.calls_for("insert_trade_decision")[-1]["input_hash"]

    assert retry_hash == clean_hash


async def test_reveal_requires_exact_token_set_and_db_quote_values():
    repo = FakeRepo(); repo.responses["decision_context"] = _context("CREATED")
    logic = DecisionLogic(repo, FakeWorkflow(), FakePortfolio())
    missing = await logic.reveal(FakeUoW(), trade_decision_id=1,
                                 quote_reveal_at=NOW + timedelta(seconds=1), quotes={"1": _quotes()["1"]})
    assert not missing.ok and missing.reason == "decision_quote_missing"
    forged = _quotes(); forged["1"]["best_ask"] = Decimal("0.01")
    bad = await logic.reveal(FakeUoW(), trade_decision_id=1,
                             quote_reveal_at=NOW + timedelta(seconds=1), quotes=forged)
    assert not bad.ok and bad.reason == "decision_quote_authority_mismatch:best_ask"
    good = await logic.reveal(FakeUoW(), trade_decision_id=1,
                              quote_reveal_at=NOW + timedelta(seconds=1), quotes=_quotes())
    assert good.ok and len(repo.calls_for("bind_quote")) == 2


async def test_blind_only_uses_committed_q_and_rejects_caller_substitution():
    repo = FakeRepo(); logic = DecisionLogic(repo, FakeWorkflow(), FakePortfolio())
    bad = await logic.market_relative(
        FakeUoW(), trade_decision_id=1,
        input_=MarketRelativeInput(decision_mode="BLIND_ONLY", q_blind={"w0": "0.55", "w1": "0.45"}),
    )
    assert not bad.ok and bad.reason == "decision_q_blind_authority_mismatch"
    good = await logic.market_relative(FakeUoW(), trade_decision_id=1,
                                       input_=MarketRelativeInput(decision_mode="BLIND_ONLY"))
    assert good.ok
    assert repo.calls_for("insert_mr")[-1]["q_decision"] == {"w0": "0.6", "w1": "0.4"}


async def test_identified_shrinkage_maps_tokens_to_world_states():
    repo = FakeRepo()
    repo.responses["decision_context"]["strategy_content"]["optional_shrinkage"] = {
        "enabled": True, "w_blind": "0.5"
    }
    repo.responses["bound_market_material"] = _bound("0.52", "0.48")
    logic = DecisionLogic(repo, FakeWorkflow(), FakePortfolio())
    result = await logic.market_relative(
        FakeUoW(), trade_decision_id=1,
        input_=MarketRelativeInput(decision_mode="LINEAR_SHRINKAGE"),
    )
    assert result.ok
    assert repo.calls_for("insert_mr")[-1]["q_decision"] == {"w0": "0.560", "w1": "0.440"}


async def test_g7a_rejects_forged_depth_and_negative_robust_ev():
    repo = FakeRepo(); wf = FakeWorkflow(); logic = DecisionLogic(repo, wf, FakePortfolio())
    forged = await logic.run_g7a(
        FakeUoW(), trade_decision_id=1,
        candidates=[_candidate(depth_levels=[[Decimal("0.01"), 100]])],
    )
    assert not forged.ok and forged.reason == "g7a_depth_authority_mismatch"
    repo = FakeRepo(); repo.responses["get_market_relative"] = {
        "q_decision": {"w0": "0.1", "w1": "0.9"},
        "u_decision": [{"w0": "0.1", "w1": "0.9"}],
    }
    wf = FakeWorkflow(); logic = DecisionLogic(repo, wf, FakePortfolio())
    negative = await logic.run_g7a(FakeUoW(), trade_decision_id=1, candidates=[_candidate()])
    assert not negative.ok and negative.reason == "g7a_robust_ev_below_margin"
    assert wf.calls[-1]["result"] == "FAIL"


async def test_g7a_full_cost_is_not_double_subtracted_and_requires_cost_evidence():
    repo = FakeRepo(); logic = DecisionLogic(repo, FakeWorkflow(), FakePortfolio())
    good = await logic.run_g7a(FakeUoW(), trade_decision_id=1, candidates=[_candidate()])
    assert good.ok and good.candidate_count == 1
    stored = repo.calls_for("insert_candidate")[0]
    assert stored["robust_ev"] == Decimal("3")
    assert stored["net_edge"] == stored["robust_ev"]
    assert stored["cost_components"]["entry"] == "52"
    repo = FakeRepo(); repo.responses["allocated_operating_cost"] = None
    missing = await DecisionLogic(repo, FakeWorkflow(), FakePortfolio()).run_g7a(
        FakeUoW(), trade_decision_id=1, candidates=[_candidate()]
    )
    assert not missing.ok and missing.reason == "g7a_operating_cost_evidence_missing"


async def test_g7b_uses_db_capacity_and_rejects_caller_exposure():
    repo = FakeRepo(); repo.responses["decision_context"] = _context("G7A")
    repo.responses["candidates_for_decision"] = [_candidate_row()]
    portfolio = FakePortfolio()
    logic = DecisionLogic(repo, FakeWorkflow(), portfolio)
    forged = await logic.run_g7b(
        FakeUoW(), trade_decision_id=1,
        portfolio=PortfolioGateInput(market_exposure=Decimal("0")),
    )
    assert not forged.ok and forged.reason == "g7b_market_exposure_authority_mismatch"
    good = await logic.run_g7b(FakeUoW(), trade_decision_id=1, portfolio=PortfolioGateInput())
    assert good.ok and portfolio.calls


async def test_terminal_rejects_unvalued_leg_and_creates_committed_intent_for_valid_candidate():
    repo = FakeRepo(); repo.responses["decision_context"] = _context("G7B")
    repo.responses["candidates_for_decision"] = [_candidate_row()]
    logic = DecisionLogic(repo, FakeWorkflow(), FakePortfolio())
    bad = await logic.terminalize(
        FakeUoW(), trade_decision_id=1,
        action_set=ActionSetInput(disposition="ACTION", selected_action_type="BUY_TOKEN",
                                  legs={"open": {1: {2: Decimal("100")}}}),
        underwriting=UnderwritingInput(plan_version=1, thesis_hash=HASH), decided_at=NOW,
    )
    assert not bad.ok and bad.reason == "decision_leg_not_g7a_candidate"
    good = await logic.terminalize(
        FakeUoW(), trade_decision_id=1,
        action_set=ActionSetInput(disposition="ACTION", selected_action_type="BUY_TOKEN",
                                  legs={"open": {1: {1: Decimal("100")}}}),
        underwriting=UnderwritingInput(plan_version=1, thesis_hash=HASH), decided_at=NOW,
    )
    assert good.ok
    assert repo.calls_for("insert_leg")[0]["entry_vwap"] == Decimal("0.52")
    assert repo.calls_for("insert_intent")[0]["status"] == "COMMITTED"


async def test_terminal_business_hashes_ignore_surrogate_ids_and_leg_order():
    """A rollback sequence gap cannot change intent/action-set business identity."""

    async def run(*, ids: tuple[int, int, int], reverse: bool):
        episode_id, submission_id, first_candidate_id = ids
        repo = FakeRepo()
        repo.responses["decision_context"] = _context(
            "G7B",
            episode_id=episode_id,
            forecast_submission_id=submission_id,
        )
        candidates = [
            _candidate_row(id=first_candidate_id),
            _candidate_row(
                id=first_candidate_id + 1,
                token_id=2,
                external_token_id="external-token-natural-key-2",
            ),
        ]
        repo.responses["candidates_for_decision"] = list(
            reversed(candidates) if reverse else candidates
        )
        token_map = (
            {2: Decimal("50"), 1: Decimal("50")}
            if reverse
            else {1: Decimal("50"), 2: Decimal("50")}
        )
        result = await DecisionLogic(
            repo, FakeWorkflow(), FakePortfolio()
        ).terminalize(
            FakeUoW(),
            trade_decision_id=1,
            action_set=ActionSetInput(
                disposition="ACTION",
                selected_action_type="BUY_TOKEN",
                legs={"open": {1: token_map}},
            ),
            underwriting=UnderwritingInput(plan_version=1, thesis_hash=HASH),
            decided_at=NOW,
        )
        assert result.ok
        return (
            repo.calls_for("insert_intent")[0]["intent_hash"],
            repo.calls_for("insert_action_set")[0]["action_set_hash"],
            repo.calls_for("insert_intent")[0]["preflight"]["intent_material"],
        )

    clean = await run(ids=(1, 5, 300), reverse=False)
    sequence_gap = await run(ids=(91, 105, 9300), reverse=True)

    assert clean == sequence_gap
    serialized = str(clean[2])
    assert "episode_id" not in serialized
    assert "forecast_submission_id" not in serialized
    assert "candidate_id" not in serialized


async def test_v1_gold_unreviewed_does_not_open_new_exposure():
    repo = FakeRepo()
    repo.responses["decision_context"] = _context("G7B")
    repo.responses["candidates_for_decision"] = [_candidate_row()]
    repo.responses["review_passed"] = False
    result = await DecisionLogic(repo, FakeWorkflow(), FakePortfolio()).terminalize(
        FakeUoW(),
        trade_decision_id=1,
        action_set=ActionSetInput(
            disposition="ACTION",
            selected_action_type="BUY_TOKEN",
            legs={"open": {1: {1: Decimal("100")}}},
        ),
        underwriting=UnderwritingInput(plan_version=1, thesis_hash=HASH),
        decided_at=NOW,
    )
    assert not result.ok and result.reason == "decision_unreviewed"
    assert not repo.calls_for("insert_intent")


async def test_hold_is_action_with_zero_legs_and_no_intent():
    repo = FakeRepo(); repo.responses["decision_context"] = _context("G7B")
    logic = DecisionLogic(repo, FakeWorkflow(), FakePortfolio())
    result = await logic.terminalize(
        FakeUoW(), trade_decision_id=1,
        action_set=ActionSetInput(disposition="ACTION", selected_action_type="HOLD"),
        underwriting=None, decided_at=NOW,
    )
    assert result.ok
    assert repo.calls_for("terminal")[0]["selected_action_type"] == "HOLD"
    assert not repo.calls_for("insert_intent")
