"""DB-derived shadow execution unit tests (WP-03)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.logics.trading import ShadowExecutionLogic
from app.schemas.trading.execution import ShadowFillInput


class FakeUoW:
    def __init__(self):
        self.session = SimpleNamespace()


class FakeExecutionRepository:
    def __init__(self, *, role="open", quantity=Decimal("100")):
        now = datetime.now(timezone.utc)
        signed = quantity if role == "open" else -quantity
        self.material = {
            "intent_id": 1, "intent_status": "COMMITTED", "ttl_at": now + timedelta(hours=1),
            "intent_decision_id": 10, "action_set_id": 20, "trade_decision_id": 10,
            "disposition": "ACTION", "leg_id": 1, "contract_spec_id": 1, "token_id": 2,
            "leg_role": role, "quantity": quantity, "signed_quantity": signed,
            "decision_status": "ACTION", "experiment_variant": "champion",
            "release_manifest_id": 7, "external_token_id": "tok-2", "market_id": 3,
            "component_id": 4, "checkpoint_id": 5, "checkpoint_received_at": now,
            "stale_at": now + timedelta(hours=1), "checkpoint_complete": True,
            "checkpoint_validity": "VALID", "execution_spec_status": "active",
            "execution_spec": {"execution_mode": "shadow_only", "short_sell_to_open": False,
                               "fee": {"taker_fee_bps": 0},
                               "depth_walk": {"max_levels": 10}},
            "permission_status": "active", "permission_mode": "shadow",
            "authorized_capital": 0, "kill_switch": False,
            "capability": {"can_open": True, "can_reduce": True, "can_close": True},
            "objective_content": {"units": "USD"},
        }
        self.levels = [(Decimal("0.5"), Decimal("100"))]
        self.calls = []
        self.position = None
        self.prior = None
        self.claim = None
        self.existing_by_key = None

    def _record(self, method, **kwargs):
        self.calls.append((method, kwargs))

    def calls_for(self, method):
        return [kwargs for name, kwargs in self.calls if name == method]

    async def fill_material(self, session, **kwargs):
        self._record("fill_material", **kwargs)
        return self.material

    async def get_execution_by_key(self, session, execution_key):
        return self.existing_by_key

    async def checkpoint_levels(self, session, **kwargs):
        self._record("checkpoint_levels", **kwargs)
        return self.levels

    async def acquire_execution_lock(self, session, **kwargs):
        self._record("acquire_execution_lock", **kwargs)

    async def execution_for_leg(self, session, **kwargs):
        return self.prior

    async def acquire_position_lock(self, session, **kwargs):
        self._record("acquire_position_lock", **kwargs)

    async def insert_execution(self, session, **kwargs):
        self._record("insert_execution", **kwargs)
        if self.claim is not None:
            return self.claim
        return {"id": 1, "inserted": True, "status": "PENDING"}

    async def terminalize_execution(self, session, execution_id, **kwargs):
        self._record("terminalize_execution", execution_id=execution_id, **kwargs)
        return True

    async def get_position(self, session, **kwargs):
        self._record("get_position", **kwargs)
        return self.position

    async def upsert_position(self, session, **kwargs):
        self._record("upsert_position", **kwargs)
        self.position = {**kwargs, "version": 1}

    async def insert_position_lot(self, session, **kwargs):
        self._record("insert_position_lot", **kwargs)

    async def consume_intent_if_complete(self, session, **kwargs):
        self._record("consume_intent_if_complete", **kwargs)
        return True


class FakeLedgerRepository:
    def __init__(self):
        self.calls = []

    def _record(self, method, **kwargs):
        self.calls.append((method, kwargs))

    def calls_for(self, method):
        return [kwargs for name, kwargs in self.calls if name == method]

    async def insert_transaction(self, session, **kwargs):
        self._record("insert_transaction", **kwargs)
        return 500

    async def insert_postings(self, session, **kwargs):
        self._record("insert_postings", **kwargs)

    async def mark_posted(self, session, transaction_id, *, posted_at):
        self._record("mark_posted", transaction_id=transaction_id, posted_at=posted_at)
        return True

    async def transaction_for_execution(self, session, execution_id):
        return None


class FakeOutbox:
    def __init__(self):
        self.events = []

    async def enqueue(self, session, event):
        self.events.append(event)


def _fill(**over):
    base = dict(
        execution_key="exe-1", economic_action_intent_id=1, action_set_leg_id=1,
        contract_spec_id=1, token_id=2, fill_role="open", quantity=Decimal("100"),
        side="buy", depth_levels=[[Decimal("0.5"), Decimal("100")]],
        taker_fee_bps=Decimal("0"), portfolio_namespace="shadow-champion",
    )
    base.update(over)
    return ShadowFillInput(**base)


def _logic(repo=None):
    repo = repo or FakeExecutionRepository()
    ledger = FakeLedgerRepository()
    outbox = FakeOutbox()
    return ShadowExecutionLogic(repo, ledger, outbox), repo, ledger, outbox


async def test_shadow_fill_uses_bound_db_depth_and_writes_atomic_buy_evidence():
    logic, repo, ledger, outbox = _logic()
    # Caller depth is deliberately fake; DB checkpoint depth remains authoritative.
    result = await logic.shadow_fill(
        FakeUoW(), fill=_fill(depth_levels=[[Decimal("0.01"), Decimal("999999")]]),
        portfolio_namespace="shadow-champion", cash_asset_key="usd",
    )
    assert result.ok and result.status == "FILLED" and result.vwap == Decimal("0.500000")
    claim = repo.calls_for("insert_execution")[0]
    assert claim["quote_checkpoint_id"] == 5 and claim["quantity"] == 100
    assert repo.calls_for("upsert_position")[0]["quantity"] == 100
    assert repo.calls_for("insert_position_lot")[0]["quantity"] == 100
    postings = ledger.calls_for("insert_postings")[0]["postings"]
    assert len(postings) == 4
    assert all(sum(Decimal(p["amount"]) for p in postings
                   if (p["asset_type"], p["asset_key"]) == key) == 0
               for key in {(p["asset_type"], p["asset_key"]) for p in postings})
    assert len(outbox.events) == 1


async def test_shadow_fill_partial_comes_only_from_bound_book():
    logic, repo, _, _ = _logic()
    repo.levels = [(Decimal("0.5"), Decimal("60"))]
    result = await logic.shadow_fill(FakeUoW(), fill=_fill(),
                                     portfolio_namespace="shadow-champion", cash_asset_key="usd")
    assert result.status == "PARTIAL" and result.filled_quantity == 60
    assert repo.calls_for("terminalize_execution")[0]["unfilled_reason"] == "insufficient_depth"


async def test_shadow_fill_rejected_has_no_position_lot_or_empty_posted_ledger():
    logic, repo, ledger, outbox = _logic()
    repo.levels = []
    result = await logic.shadow_fill(FakeUoW(), fill=_fill(),
                                     portfolio_namespace="shadow-champion", cash_asset_key="usd")
    assert result.ok and result.status == "REJECTED" and result.ledger_transaction_id is None
    assert not repo.calls_for("upsert_position")
    assert not repo.calls_for("insert_position_lot")
    assert not ledger.calls_for("insert_transaction")
    assert len(outbox.events) == 1


async def test_shadow_sell_reduces_position_cost_and_posts_cash_and_token_inverse():
    repo = FakeExecutionRepository(role="reduce", quantity=Decimal("40"))
    repo.levels = [(Decimal("0.6"), Decimal("40"))]
    repo.position = {"quantity": Decimal("100"), "cost_basis": Decimal("50"), "version": 1}
    logic, repo, ledger, _ = _logic(repo)
    result = await logic.shadow_fill(
        FakeUoW(),
        fill=_fill(fill_role="reduce", quantity=Decimal("40"), side="sell"),
        portfolio_namespace="shadow-champion", cash_asset_key="usd",
    )
    assert result.status == "FILLED"
    assert repo.calls_for("upsert_position")[0]["quantity"] == 60
    assert repo.calls_for("upsert_position")[0]["cost_basis"] == 30
    assert repo.calls_for("insert_position_lot")[0]["quantity"] == -40
    postings = ledger.calls_for("insert_postings")[0]["postings"]
    portfolio = {p["asset_type"]: Decimal(p["amount"])
                 for p in postings if p["counterparty"] == "shadow-champion"}
    assert portfolio == {"CASH": Decimal("24"), "TOKEN": Decimal("-40")}


async def test_shadow_sell_cannot_make_position_negative_and_writes_nothing():
    repo = FakeExecutionRepository(role="close", quantity=Decimal("101"))
    repo.levels = [(Decimal("0.5"), Decimal("101"))]
    repo.position = {"quantity": Decimal("100"), "cost_basis": Decimal("50"), "version": 1}
    logic, repo, ledger, outbox = _logic(repo)
    with pytest.raises(RuntimeError, match="shadow_fill_negative_position"):
        await logic.shadow_fill(
            FakeUoW(), fill=_fill(fill_role="close", quantity=101, side="sell"),
            portfolio_namespace="shadow-champion", cash_asset_key="usd")
    assert not repo.calls_for("insert_execution") and not ledger.calls and not outbox.events


async def test_exact_terminal_retry_returns_existing_without_second_effect():
    logic, repo, ledger, outbox = _logic()
    repo.prior = {"id": 1, "execution_key": "exe-1"}
    repo.existing_by_key = {
        "id": 1, "execution_key": "exe-1", "economic_action_intent_id": 1,
        "action_set_leg_id": 1, "contract_spec_id": 1, "token_id": 2,
        "fill_role": "open", "quantity": Decimal("100"),
        "portfolio_namespace": "shadow-champion", "status": "FILLED",
        "filled_quantity": Decimal("100"), "vwap": Decimal("0.5"), "fee": 0,
    }
    result = await logic.shadow_fill(FakeUoW(), fill=_fill(),
                                     portfolio_namespace="shadow-champion", cash_asset_key="usd")
    assert result.ok and result.replayed
    assert not repo.calls_for("terminalize_execution")
    assert not repo.calls_for("upsert_position") and not repo.calls_for("insert_position_lot")
    assert not ledger.calls and not outbox.events


async def test_same_intent_leg_with_different_execution_key_is_rejected():
    logic, repo, ledger, _ = _logic()
    repo.prior = {"id": 1, "execution_key": "original"}
    with pytest.raises(RuntimeError, match="shadow_fill_leg_already_executed"):
        await logic.shadow_fill(FakeUoW(), fill=_fill(execution_key="different"),
                                portfolio_namespace="shadow-champion", cash_asset_key="usd")
    assert not ledger.calls


async def test_legacy_identity_mismatch_is_fail_closed():
    logic, repo, ledger, _ = _logic()
    with pytest.raises(RuntimeError, match="shadow_fill_payload_mismatch:token_id"):
        await logic.shadow_fill(FakeUoW(), fill=_fill(token_id=999),
                                portfolio_namespace="shadow-champion", cash_asset_key="usd")
    assert not repo.calls_for("insert_execution") and not ledger.calls
