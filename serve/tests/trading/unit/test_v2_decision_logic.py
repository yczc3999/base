"""Decision Logic 单测（WP-03 Checkpoint B）。

create_decision → reveal → market_relative → run_g7a → run_g7b → terminalize。
纯单元测试：用 fake repository stub 记录调用并返回预设行，不连 DB。
"""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.logics.trading import DecisionLogic
from app.schemas.trading.decision import (
    ActionCandidateInput,
    ActionSetInput,
    MarketRelativeInput,
    PortfolioGateInput,
    UnderwritingInput,
)

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FakeUoW:
    def __init__(self):
        self.session = SimpleNamespace()


class FakeWorkflowRepository:
    def __init__(self):
        self.calls = []

    def _record(self, method, **kwargs):
        self.calls.append((method, kwargs))

    def calls_for(self, method):
        return [kwargs for name, kwargs in self.calls if name == method]

    async def insert_gate_decision(self, session, **kwargs):
        self._record("insert_gate_decision", **kwargs)
        return {"id": 1, **kwargs}


class FakeDecisionRepository:
    """记录调用；insert_* 返回稳定 id；其余方法从 responses 取预设值。"""

    def __init__(self):
        self.calls = []
        self.responses = {}
        self._ids = {
            "insert_trade_decision": 100,
            "insert_market_relative_decision": 200,
            "insert_action_candidate": 300,
            "insert_action_set": 400,
        }

    def _record(self, method, **kwargs):
        self.calls.append((method, kwargs))

    def calls_for(self, method):
        return [kwargs for name, kwargs in self.calls if name == method]

    def _resp(self, method, default=None):
        return self.responses.get(method, default)

    # ---------------- trade_decision ----------------
    async def decision_material(self, session, episode_id):
        self._record("decision_material", episode_id=episode_id)
        return self._resp("decision_material")

    async def release_by_episode(self, session, episode_id):
        self._record("release_by_episode", episode_id=episode_id)
        return self._resp("release_by_episode")

    async def insert_trade_decision(self, session, **kwargs):
        self._record("insert_trade_decision", **kwargs)
        return self._resp("insert_trade_decision", self._ids["insert_trade_decision"])

    async def get_trade_decision_by_id(self, session, trade_decision_id):
        self._record("get_trade_decision_by_id", trade_decision_id=trade_decision_id)
        return self._resp("get_trade_decision_by_id")

    async def bind_quote(self, session, **kwargs):
        self._record("bind_quote", **kwargs)

    async def mark_quote_bound(self, session, trade_decision_id, *, quote_bound_at):
        self._record("mark_quote_bound", trade_decision_id=trade_decision_id,
                     quote_bound_at=quote_bound_at)
        return self._resp("mark_quote_bound", True)

    async def insert_discrepancy_review(self, session, **kwargs):
        self._record("insert_discrepancy_review", **kwargs)
        return 1

    async def advance_status(self, session, trade_decision_id, *, to_status):
        self._record("advance_status", trade_decision_id=trade_decision_id, to_status=to_status)
        return self._resp("advance_status", True)

    async def terminal_decision(self, session, trade_decision_id, **kwargs):
        self._record("terminal_decision", trade_decision_id=trade_decision_id, **kwargs)
        return self._resp("terminal_decision", True)

    # ---------------- market-relative ----------------
    async def get_submission_qu(self, session, forecast_submission_id):
        self._record("get_submission_qu", forecast_submission_id=forecast_submission_id)
        return self._resp("get_submission_qu")

    async def insert_market_relative_decision(self, session, **kwargs):
        self._record("insert_market_relative_decision", **kwargs)
        return self._ids["insert_market_relative_decision"]

    async def get_market_relative(self, session, trade_decision_id):
        self._record("get_market_relative", trade_decision_id=trade_decision_id)
        return self._resp("get_market_relative")

    # ---------------- G7A ----------------
    async def get_spec_payout_hc(self, session, contract_spec_id):
        self._record("get_spec_payout_hc", contract_spec_id=contract_spec_id)
        return self._resp("get_spec_payout_hc")

    async def insert_action_candidate(self, session, **kwargs):
        self._record("insert_action_candidate", **kwargs)
        return self._ids["insert_action_candidate"]

    async def insert_cashflow(self, session, **kwargs):
        self._record("insert_cashflow", **kwargs)

    # ---------------- action set / underwriting ----------------
    async def insert_action_set(self, session, **kwargs):
        self._record("insert_action_set", **kwargs)
        return self._ids["insert_action_set"]

    async def insert_action_set_leg(self, session, **kwargs):
        self._record("insert_action_set_leg", **kwargs)

    async def insert_underwriting_plan(self, session, **kwargs):
        self._record("insert_underwriting_plan", **kwargs)
        return 1


def _valid_material(**over):
    material = {
        "episode_status": "BLIND_COMMITTED",
        "episode_key": "ep-key",
        "submission_id": 5,
        "lease_id": 7,
        "valid_until": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "evidence_hash": "a" * 64,
        "schema_hash": "b" * 64,
        "spec_hash": "c" * 64,
        "cohort_key": "cohort-1",
        "release_manifest_id": 3,
        "objective_contract_id": 2,
        "strategy_version_id": 4,
    }
    material.update(over)
    return material


def _valid_release(**over):
    release = {
        "release_status": "active",
        "exec_spec_status": "active",
        "capital_status": "active",
        "mode": "shadow",
        "authorized_capital": 0,
        "kill_switch": False,
        "execution_spec_version_id": 11,
        "capital_permission_manifest_id": 12,
    }
    release.update(over)
    return release


def _quote(**over):
    quote = {
        "checkpoint_id": 10,
        "checkpoint_received_at": NOW,
        "best_bid": Decimal("0.52"),
        "best_ask": Decimal("0.53"),
        "as_of": NOW,
        "received_at": NOW,
        "stale_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
    }
    quote.update(over)
    return quote


def _candidate(**over):
    base = dict(
        contract_spec_id=1,
        token_id=1,
        action_type="BUY_TOKEN",
        target_quantity=Decimal("100"),
        depth_levels=[[Decimal("0.5"), Decimal("100")]],
        side="buy",
        taker_fee_bps=Decimal("0"),
        horizon_days=Decimal("1"),
        bankroll=Decimal("1000"),
    )
    base.update(over)
    return ActionCandidateInput(**base)


def _portfolio(**over):
    base = dict(
        bankroll=Decimal("1000"),
        per_market_cap=Decimal("0.04"),
        per_component_cap=Decimal("0.06"),
        global_cap=Decimal("0.30"),
        market_exposure=Decimal("30"),
        component_exposure=Decimal("50"),
        global_exposure=Decimal("200"),
    )
    base.update(over)
    return PortfolioGateInput(**base)


# ================= create_decision =================

async def test_create_decision_episode_not_blind_committed():
    repo = FakeDecisionRepository()
    repo.responses["decision_material"] = _valid_material(episode_status="DRAFT")
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.create_decision(
        FakeUoW(), episode_id=1, trigger_at=NOW, experiment_variant="control"
    )
    assert result.ok is False
    assert result.reason == "decision_episode_not_blind_committed"


async def test_create_decision_lease_expired():
    repo = FakeDecisionRepository()
    repo.responses["decision_material"] = _valid_material(valid_until=NOW)  # valid_until <= trigger
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.create_decision(
        FakeUoW(), episode_id=1, trigger_at=NOW, experiment_variant="control"
    )
    assert result.ok is False
    assert result.reason == "decision_lease_invalid"


async def test_create_decision_lease_missing():
    repo = FakeDecisionRepository()
    repo.responses["decision_material"] = _valid_material(lease_id=None)
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.create_decision(
        FakeUoW(), episode_id=1, trigger_at=NOW, experiment_variant="control"
    )
    assert result.ok is False
    assert result.reason == "decision_lease_invalid"


@pytest.mark.parametrize("release", [
    None,
    _valid_release(release_status="draft"),
    _valid_release(exec_spec_status="draft"),
    _valid_release(capital_status="draft"),
    _valid_release(mode="live"),
    _valid_release(authorized_capital=100),
    _valid_release(kill_switch=True),
])
async def test_create_decision_freeze_invalid(release):
    repo = FakeDecisionRepository()
    repo.responses["decision_material"] = _valid_material()
    repo.responses["release_by_episode"] = release
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.create_decision(
        FakeUoW(), episode_id=1, trigger_at=NOW, experiment_variant="control"
    )
    assert result.ok is False
    assert result.reason == "decision_freeze_invalid"


async def test_create_decision_ok_persists_decision():
    repo = FakeDecisionRepository()
    repo.responses["decision_material"] = _valid_material()
    repo.responses["release_by_episode"] = _valid_release()
    repo.responses["insert_trade_decision"] = 42
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.create_decision(
        FakeUoW(), episode_id=1, trigger_at=NOW, experiment_variant="control"
    )
    assert result.ok is True
    assert result.trade_decision_id == 42
    inserts = repo.calls_for("insert_trade_decision")
    assert len(inserts) == 1
    assert inserts[0]["episode_id"] == 1
    assert inserts[0]["forecast_submission_id"] == 5
    assert inserts[0]["forecast_lease_id"] == 7
    assert inserts[0]["experiment_variant"] == "control"
    assert inserts[0]["decision_class"] == "CHAMPION"
    assert inserts[0]["release_manifest_id"] == 3


# ================= reveal =================

async def test_reveal_decision_not_created():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "QUOTE_BOUND"}
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.reveal(
        FakeUoW(), trade_decision_id=1, quote_reveal_at=NOW, quotes={1: _quote()}
    )
    assert result.ok is False
    assert result.reason == "decision_not_created"


async def test_reveal_quote_missing_when_empty():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "CREATED"}
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.reveal(FakeUoW(), trade_decision_id=1, quote_reveal_at=NOW, quotes={})
    assert result.ok is False
    assert result.reason == "decision_quote_missing"


async def test_reveal_quote_crossed():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "CREATED"}
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.reveal(
        FakeUoW(), trade_decision_id=1, quote_reveal_at=NOW,
        quotes={1: _quote(best_bid=Decimal("0.55"), best_ask=Decimal("0.50"))},
    )
    assert result.ok is False
    assert result.reason == "decision_quote_crossed"


async def test_reveal_quote_stale():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "CREATED"}
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.reveal(
        FakeUoW(), trade_decision_id=1, quote_reveal_at=NOW,
        quotes={1: _quote(stale_at=NOW)},  # stale_at <= reveal_at
    )
    assert result.ok is False
    assert result.reason == "decision_quote_stale"


async def test_reveal_ok_binds_quotes_and_marks_bound():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "CREATED"}
    repo.responses["mark_quote_bound"] = True
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.reveal(
        FakeUoW(), trade_decision_id=7, quote_reveal_at=NOW,
        quotes={
            1: _quote(),
            2: _quote(best_bid=Decimal("0.40"), best_ask=Decimal("0.41")),
        },
    )
    assert result.ok is True
    assert result.trade_decision_id == 7
    assert len(repo.calls_for("bind_quote")) == 2
    assert len(repo.calls_for("mark_quote_bound")) == 1
    assert len(repo.calls_for("insert_discrepancy_review")) == 1


# ================= market_relative =================

async def test_market_relative_decision_not_quote_bound():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "CREATED"}
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    input_ = MarketRelativeInput(decision_mode="BLIND_ONLY", q_blind={"w0": "0.6", "w1": "0.4"})
    result = await logic.market_relative(FakeUoW(), trade_decision_id=1, input_=input_)
    assert result.ok is False
    assert result.reason == "decision_not_quote_bound"


async def test_market_relative_submission_qu_missing():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "QUOTE_BOUND", "forecast_submission_id": 5}
    repo.responses["get_submission_qu"] = None
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    input_ = MarketRelativeInput(decision_mode="BLIND_ONLY", q_blind={"w0": "0.6", "w1": "0.4"})
    result = await logic.market_relative(FakeUoW(), trade_decision_id=1, input_=input_)
    assert result.ok is False
    assert result.reason == "submission_qu_missing"


async def test_market_relative_linear_shrinkage_unidentified_abstains():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "QUOTE_BOUND", "forecast_submission_id": 5}
    repo.responses["get_submission_qu"] = {"U": [{"w0": "0.6", "w1": "0.4"}]}
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    input_ = MarketRelativeInput(
        decision_mode="LINEAR_SHRINKAGE",
        w_blind=Decimal("0.5"),
        q_blind={"w0": "0.6", "w1": "0.4"},
        token_prices={1: "0.5"},  # 不全（sum≠1）→ Q_market 不可识别
    )
    result = await logic.market_relative(FakeUoW(), trade_decision_id=1, input_=input_)
    assert result.ok is False
    assert result.reason == "ABSTAIN_MARKET_REFERENCE_UNIDENTIFIED"
    assert len(repo.calls_for("insert_market_relative_decision")) == 0


async def test_market_relative_blind_only_writes_u_decision_with_q():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "QUOTE_BOUND", "forecast_submission_id": 5}
    repo.responses["get_submission_qu"] = {
        "U": [{"w0": "0.6", "w1": "0.4"}, {"w0": "0.5", "w1": "0.5"}],
    }
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    input_ = MarketRelativeInput(decision_mode="BLIND_ONLY", q_blind={"w0": "0.6", "w1": "0.4"})
    result = await logic.market_relative(FakeUoW(), trade_decision_id=1, input_=input_)
    assert result.ok is True
    writes = repo.calls_for("insert_market_relative_decision")
    assert len(writes) == 1
    assert writes[0]["decision_mode"] == "BLIND_ONLY"
    assert {"w0": "0.6", "w1": "0.4"} in writes[0]["u_decision"]


# ================= run_g7a =================

async def test_run_g7a_decision_not_quote_bound():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "CREATED"}
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.run_g7a(
        FakeUoW(), trade_decision_id=1, candidates=[], policy_hash="p", version_manifest_id=1
    )
    assert result.ok is False
    assert result.reason == "decision_not_quote_bound"


async def test_run_g7a_market_relative_missing():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "QUOTE_BOUND"}
    repo.responses["get_market_relative"] = None
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.run_g7a(
        FakeUoW(), trade_decision_id=1, candidates=[_candidate()], policy_hash="p", version_manifest_id=1
    )
    assert result.ok is False
    assert result.reason == "market_relative_missing"


async def test_run_g7a_spec_missing():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "QUOTE_BOUND"}
    repo.responses["get_market_relative"] = {"u_decision": [{"w0": "0.6", "w1": "0.4"}]}
    repo.responses["get_spec_payout_hc"] = None
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.run_g7a(
        FakeUoW(), trade_decision_id=1, candidates=[_candidate()], policy_hash="p", version_manifest_id=1
    )
    assert result.ok is False
    assert result.reason == "spec_missing:1"


async def test_run_g7a_payout_missing_token():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "QUOTE_BOUND"}
    repo.responses["get_market_relative"] = {"u_decision": [{"w0": "0.6", "w1": "0.4"}]}
    repo.responses["get_spec_payout_hc"] = {
        "h_c": {"w0": "res0", "w1": "res1"}, "payouts": {},
    }
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.run_g7a(
        FakeUoW(), trade_decision_id=1, candidates=[_candidate()], policy_hash="p", version_manifest_id=1
    )
    assert result.ok is False
    assert result.reason == "payout_missing_token:1"


async def test_run_g7a_ok_writes_candidate_cashflow_and_gate():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "QUOTE_BOUND"}
    repo.responses["get_market_relative"] = {"u_decision": [{"w0": "0.6", "w1": "0.4"}]}
    repo.responses["get_spec_payout_hc"] = {
        "h_c": {"w0": "res0", "w1": "res1"},
        "payouts": {1: {"res0": "1", "res1": "0"}},
    }
    wf = FakeWorkflowRepository()
    logic = DecisionLogic(repo, wf)
    result = await logic.run_g7a(
        FakeUoW(), trade_decision_id=1, candidates=[_candidate()], policy_hash="p", version_manifest_id=1
    )
    assert result.ok is True
    assert result.candidate_count == 1
    assert len(repo.calls_for("insert_action_candidate")) == 1
    assert len(repo.calls_for("insert_cashflow")) == 2  # w0 + w1
    gates = wf.calls_for("insert_gate_decision")
    assert len(gates) == 1
    assert gates[0]["gate"] == "G7A"
    assert gates[0]["result"] == "PASS"
    adv = repo.calls_for("advance_status")
    assert len(adv) == 1
    assert adv[0]["to_status"] == "G7A"


# ================= run_g7b =================

async def test_run_g7b_decision_not_g7a():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "QUOTE_BOUND"}
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    result = await logic.run_g7b(
        FakeUoW(), trade_decision_id=1, portfolio=_portfolio(),
        policy_hash="p", version_manifest_id=1,
    )
    assert result.ok is False
    assert result.reason == "decision_not_g7a"


async def test_run_g7b_cap_fail_reason():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "G7A"}
    wf = FakeWorkflowRepository()
    logic = DecisionLogic(repo, wf)
    portfolio = _portfolio(market_exposure=Decimal("50"))  # 5% > 4%
    result = await logic.run_g7b(
        FakeUoW(), trade_decision_id=1, portfolio=portfolio,
        policy_hash="p", version_manifest_id=1,
    )
    assert result.ok is False
    assert result.reason == "per_market_cap_exceeded"
    gates = wf.calls_for("insert_gate_decision")
    assert len(gates) == 1
    assert gates[0]["gate"] == "G7B"
    assert gates[0]["result"] == "FAIL"
    assert gates[0]["reason_code"] == "per_market_cap_exceeded"


async def test_run_g7b_ok_passes_gate():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "G7A"}
    wf = FakeWorkflowRepository()
    logic = DecisionLogic(repo, wf)
    result = await logic.run_g7b(
        FakeUoW(), trade_decision_id=1, portfolio=_portfolio(),
        policy_hash="p", version_manifest_id=1,
    )
    assert result.ok is True
    gates = wf.calls_for("insert_gate_decision")
    assert len(gates) == 1
    assert gates[0]["result"] == "PASS"
    adv = repo.calls_for("advance_status")
    assert len(adv) == 1
    assert adv[0]["to_status"] == "G7B"


# ================= terminalize =================

async def test_terminalize_decision_not_g7b():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "G7A"}
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    action_set = ActionSetInput(disposition="ACTION", legs={"open": {1: {2: Decimal("100")}}})
    result = await logic.terminalize(
        FakeUoW(), trade_decision_id=1, action_set=action_set,
        underwriting=None, decided_at=NOW,
    )
    assert result.ok is False
    assert result.reason == "decision_not_g7b"


async def test_terminalize_action_writes_legs_signed_and_underwriting():
    repo = FakeDecisionRepository()
    repo.responses["get_trade_decision_by_id"] = {"status": "G7B"}
    repo.responses["terminal_decision"] = True
    logic = DecisionLogic(repo, FakeWorkflowRepository())
    action_set = ActionSetInput(
        disposition="ACTION",
        reason_code="edge",
        wake_condition="recheck",
        legs={
            "open": {1: {2: Decimal("100")}},
            "close": {1: {2: Decimal("50")}},
        },
    )
    underwriting = UnderwritingInput(
        plan_version=1, entry_range={}, hold_to_resolution=True,
        thesis_hash="a" * 64, invalidation={},
    )
    result = await logic.terminalize(
        FakeUoW(), trade_decision_id=1, action_set=action_set,
        underwriting=underwriting, decided_at=NOW,
    )
    assert result.ok is True
    assert result.disposition == "ACTION"
    legs = repo.calls_for("insert_action_set_leg")
    assert len(legs) == 2
    by_role = {leg["leg_role"]: leg["signed_quantity"] for leg in legs}
    assert by_role["open"] == Decimal("100")
    assert by_role["close"] == Decimal("-50")
    assert len(repo.calls_for("insert_underwriting_plan")) == 1
    assert len(repo.calls_for("terminal_decision")) == 1
