"""WP-05 Checkpoint C：PrivateExecutionLogic 单测（envelope / fencing / kill switch / submit）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.trading.hashing import canonical_hash
from app.logics.trading.execution import (
    EXECUTION_AUTHORIZATION_HASH_ALGORITHM_CODE_HASH,
    KillSwitchBlocked,
    PrivateExecutionLogic,
)
from app.orchestrator.trading_state_machine import (
    IllegalTransitionError,
    assert_order_transition,
)
from app.schemas.trading.execution import EnvelopeInput, SubmitOrderInput


class _FakeUoW:
    def __init__(self):
        self.session = _FakeSession()


class _FakeSession:
    def __init__(self):
        self.calls: list[tuple] = []

    async def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _FakeResult()


class _FakeResult:
    rowcount = 1


def _account(cap_permission_id=10):
    return {
        "id": 1, "account_key": "fixture-acct", "capital_permission_manifest_id": cap_permission_id,
        "release_manifest_id": 2, "status": "active", "network_mode": "fixture",
    }


def _release(cap_permission_id=10):
    return {
        "id": 2, "release_name": "release/v1",
        "capital_permission_manifest_id": cap_permission_id,
        "execution_spec_version_id": 3, "total_hash": "e" * 64, "status": "active",
    }


def _intent(intent_hash="a" * 64):
    return {"id": 5, "intent_hash": intent_hash, "status": "COMMITTED"}


def _permission(*, kill_switch=False, authorized_capital=0, mode="shadow", status="active"):
    return {
        "id": 10, "name": "shadow-fixture", "mode": mode, "capability": {},
        "limits": {}, "evaluation_capital": 1000,
        "authorized_capital": authorized_capital, "kill_switch": kill_switch,
        "content_hash": "b" * 64, "status": status,
    }


class _FakeRepo:
    def __init__(self, *, account=None, release=None, intent=None, permission=None,
                 envelope=None):
        self.account = account or _account()
        self.release = release or _release()
        self.intent = intent or _intent()
        self.permission = permission or _permission()
        pf1, pf2 = self._preflight_hashes()
        envelope_values = {
            "id": 7, "envelope_key": "env-1", "status": "ACTIVE",
            "account_id": 1, "fencing_token": 3,
            "intent_id": 5, "capital_permission_manifest_id": 10,
            "release_manifest_id": 2, "execution_spec_version_id": 3,
            "authority": "FAKE_CONFORMANCE", "idempotency_key": "ik-1",
            "intent_hash": "a" * 64, "preflight_hash1": pf1, "preflight_hash2": pf2,
        }
        envelope_values["envelope_hash"] = canonical_hash({
            "schema": "execution-authorization-envelope/v2",
            "algorithm_code_hash": EXECUTION_AUTHORIZATION_HASH_ALGORITHM_CODE_HASH,
            **{
                key: envelope_values[key] for key in (
                    "envelope_key", "authority", "idempotency_key", "fencing_token",
                    "intent_hash", "preflight_hash1", "preflight_hash2",
                )
            },
        })
        self.envelope = envelope or envelope_values
        self.inserted_envelopes = []
        self.inserted_orders = []
        self.inserted_attempts = []
        self.events = []
        self.leg = {
            "leg_id": 8, "contract_spec_id": 3, "internal_token_id": 4,
            "leg_role": "reduce", "external_token_id": "tok-1", "market_id": 9,
            "trade_decision_id": 6, "release_manifest_id": 2, "experiment_variant": "champion",
            "cash_asset_key": "USD",
            "leg_quantity": Decimal("10"), "entry_vwap": Decimal("0.5"),
        }
        self.reservation = {
            "id": 21, "account_id": 1, "intent_id": 5,
            "reservation_key": "reserve-1", "idempotency_key": "reserve-ik-1",
            "asset_key": "tok:3:4", "amount": Decimal("10"), "status": "HELD",
            "consumed_amount": Decimal("0"), "released_amount": Decimal("0"),
        }

    @staticmethod
    def _preflight_material():
        return ({"authoritative": "portfolio-v1"}, {"authoritative": "execution-v1"})

    @classmethod
    def _preflight_hashes(cls):
        first, second = cls._preflight_material()
        return canonical_hash(first), canonical_hash(second)

    async def authoritative_preflight_material(self, session, **kwargs):
        return self._preflight_material()

    async def intent_leg_roles(self, session, *, intent_id):
        return [self.leg["leg_role"]]

    async def get_active_lease_fence(self, session, *, account_id, lease_role, owner,
                                     fencing_token, for_update=True):
        if account_id == 1 and lease_role == "EXECUTION" and owner == "worker-1" and fencing_token == 3:
            return {"owner": owner, "fencing_token": fencing_token,
                    "lease_until": datetime.now(timezone.utc) + timedelta(minutes=5)}
        return None

    async def get_submit_market_material(self, session, **kwargs):
        return {
            "best_bid": Decimal("0.49"), "best_ask": Decimal("0.51"),
            "stale_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            "checkpoint_id": 1, "tick_size": Decimal("0.01"),
            "min_order_size": Decimal("1"), "validity": "VALID",
            "observed_at": datetime.now(timezone.utc), "execution_spec_status": "active",
            "execution_spec_hash": "f" * 64,
            "execution_spec_content": {"staleness": {"max_quote_age_seconds": 300}},
            "neg_risk": False,
        }

    async def get_reservation_by_intent(self, session, *, account_id, intent_id,
                                        for_update=False):
        return self.reservation

    async def get_position(self, session, **kwargs):
        return {"quantity": Decimal("10"), "cost_basis": Decimal("5")}

    async def has_active_reconciliation(self, session, *, account_id):
        return False

    async def list_orders_for_account(self, session, *, account_id, status=None,
                                      for_update=False):
        return []

    async def get_account(self, session, *, account_id, for_update=False):
        return self.account

    async def get_release(self, session, *, release_manifest_id):
        return self.release

    async def get_intent(self, session, *, intent_id):
        return self.intent

    async def get_permission(self, session, *, permission_id):
        return self.permission

    async def insert_envelope(self, session, **kwargs):
        row = dict(kwargs)
        row["id"] = 7
        row["inserted"] = True
        self.inserted_envelopes.append(row)
        return row

    async def get_envelope(self, session, *, envelope_id, for_update=False):
        return self.envelope

    async def get_envelope_by_key(self, session, *, envelope_key):
        return None

    async def resolve_intent_leg(self, session, *, intent_id, external_token_id):
        return self.leg

    async def insert_order(self, session, **kwargs):
        row = dict(kwargs)
        row["id"] = 11
        row["inserted"] = True
        self.inserted_orders.append(row)
        return row

    async def insert_order_state_event(self, session, **kwargs):
        row = dict(kwargs)
        row["id"] = 12
        self.events.append(row)
        return row

    async def next_attempt_no(self, session, *, envelope_id):
        return 1

    async def insert_attempt(self, session, **kwargs):
        row = dict(kwargs)
        row["id"] = 13
        row["inserted"] = True
        self.inserted_attempts.append(row)
        return row

    async def advance_envelope_status(self, session, *, envelope_id, new_status):
        self.envelope["status"] = new_status
        return True

    async def advance_order(self, session, *, order_id, new_status, filled_size=None,
                            external_order_id=None, expected_status):
        order = self._order(order_id)
        order["status"] = new_status
        return True

    def _order(self, order_id):
        for row in self.inserted_orders:
            if row["id"] == order_id:
                return row
        raise KeyError(order_id)


def _logic(repo):
    return PrivateExecutionLogic(execution=repo, ledger=None, audit=None, outbox=None)


def _envelope_input(**overrides):
    pf1, pf2 = _FakeRepo._preflight_hashes()
    values = {
        "envelope_key": "env-1",
        "intent_id": 5,
        "account_id": 1,
        "release_manifest_id": 2,
        "execution_spec_version_id": 3,
        "capital_permission_manifest_id": 10,
        "authority": "FAKE_CONFORMANCE",
        "idempotency_key": "ik-1",
        "fencing_token": 3,
        "intent_hash": "a" * 64,
        "preflight_hash1": pf1,
        "preflight_hash2": pf2,
    }
    values.update(overrides)
    return EnvelopeInput(**values)


@pytest.mark.asyncio
async def test_create_envelope_ok():
    repo = _FakeRepo()
    logic = _logic(repo)
    uow = _FakeUoW()
    envelope = await logic.create_envelope(uow, input_=_envelope_input(), owner="worker-1")
    assert envelope["envelope_key"] == "env-1"
    assert len(repo.inserted_envelopes) == 1


@pytest.mark.asyncio
async def test_create_envelope_rejects_nonzero_capital():
    repo = _FakeRepo(permission=_permission(authorized_capital=100))
    logic = _logic(repo)
    with pytest.raises(RuntimeError, match="envelope_permission_not_shadow_zero"):
        await logic.create_envelope(_FakeUoW(), input_=_envelope_input(), owner="worker-1")


def test_envelope_input_schema_rejects_bad_authority():
    """schema 层拒绝非 FAKE_CONFORMANCE authority（Logic 层 authority 检查为双保险）。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _envelope_input(authority="LIVE")


@pytest.mark.asyncio
async def test_create_envelope_rejects_intent_hash_mismatch():
    repo = _FakeRepo(intent=_intent(intent_hash="d" * 64))
    logic = _logic(repo)
    with pytest.raises(RuntimeError, match="envelope_intent_hash_mismatch"):
        await logic.create_envelope(_FakeUoW(), input_=_envelope_input(), owner="worker-1")


@pytest.mark.asyncio
async def test_create_envelope_rejects_forged_preflight_hash():
    repo = _FakeRepo()
    logic = _logic(repo)
    with pytest.raises(RuntimeError, match="envelope_preflight_hash1_mismatch"):
        await logic.create_envelope(
            _FakeUoW(), input_=_envelope_input(preflight_hash1="f" * 64),
            owner="worker-1",
        )
    assert repo.inserted_envelopes == []


@pytest.mark.asyncio
async def test_create_envelope_stale_owner_has_zero_effect():
    repo = _FakeRepo()
    logic = _logic(repo)
    with pytest.raises(RuntimeError, match="stale_fence_rejected"):
        await logic.create_envelope(
            _FakeUoW(), input_=_envelope_input(), owner="old-worker",
        )
    assert repo.inserted_envelopes == []


@pytest.mark.asyncio
async def test_prepare_submit_kill_switch_blocks_open():
    repo = _FakeRepo(permission=_permission(kill_switch=True))
    repo.leg["leg_role"] = "open"
    repo.reservation["asset_key"] = "usd"
    logic = _logic(repo)
    submit = SubmitOrderInput(
        envelope_id=7, account_id=1, fencing_token=3,
        token_id="tok-1", side="BUY", price="0.5", size="10",
    )
    with pytest.raises(KillSwitchBlocked):
        await logic.prepare_submit(
            _FakeUoW(), input_=submit, owner="worker-1", signed_order=object(),
            body_hash="b" * 64, expected_order_hash="c" * 64, sdk_manifest_hash="d" * 64,
        )
    assert repo.inserted_orders == []


@pytest.mark.asyncio
async def test_prepare_submit_zero_capital_blocks_open():
    repo = _FakeRepo(permission=_permission(authorized_capital=0))
    repo.leg["leg_role"] = "open"
    repo.reservation["asset_key"] = "usd"
    logic = _logic(repo)
    submit = SubmitOrderInput(
        envelope_id=7, account_id=1, fencing_token=3,
        token_id="tok-1", side="BUY", price="0.5", size="10",
    )
    with pytest.raises(KillSwitchBlocked, match="exposure_increasing_blocked_zero_capital"):
        await logic.prepare_submit(
            _FakeUoW(), input_=submit, owner="worker-1", signed_order=object(),
            body_hash="b" * 64, expected_order_hash="c" * 64, sdk_manifest_hash="d" * 64,
        )


@pytest.mark.asyncio
async def test_prepare_submit_stale_fence_rejected():
    repo = _FakeRepo()
    logic = _logic(repo)
    submit = SubmitOrderInput(
        envelope_id=7, account_id=1, fencing_token=999,  # 旧 token
        token_id="tok-1", side="SELL", price="0.5", size="10",
    )
    with pytest.raises(RuntimeError, match="stale_fence_rejected"):
        await logic.prepare_submit(
            _FakeUoW(), input_=submit, owner="worker-1", signed_order=object(),
            body_hash="b" * 64, expected_order_hash="c" * 64, sdk_manifest_hash="d" * 64,
        )


@pytest.mark.asyncio
async def test_prepare_submit_reduce_allowed_under_zero_capital():
    repo = _FakeRepo(permission=_permission(authorized_capital=0))
    logic = _logic(repo)
    submit = SubmitOrderInput(
        envelope_id=7, account_id=1, fencing_token=3,
        token_id="tok-1", side="SELL", price="0.5", size="10",
    )
    prepared = await logic.prepare_submit(
        _FakeUoW(), input_=submit, owner="worker-1", signed_order=object(),
        body_hash="b" * 64, expected_order_hash="c" * 64, sdk_manifest_hash="d" * 64,
    )
    assert prepared.order_id == 11
    assert repo.envelope["status"] == "USED"
    assert len(repo.inserted_orders) == 1
    assert len(repo.inserted_attempts) == 1


@pytest.mark.asyncio
async def test_prepare_submit_rejects_off_tick_price_before_order_effect():
    repo = _FakeRepo(permission=_permission(authorized_capital=0))
    logic = _logic(repo)
    submit = SubmitOrderInput(
        envelope_id=7, account_id=1, fencing_token=3,
        token_id="tok-1", side="SELL", price="0.505", size="10",
    )
    with pytest.raises(RuntimeError, match="submit_price_off_tick"):
        await logic.prepare_submit(
            _FakeUoW(), input_=submit, owner="worker-1", signed_order=object(),
            body_hash="b" * 64, expected_order_hash="c" * 64,
            sdk_manifest_hash="d" * 64,
        )
    assert repo.inserted_orders == []


def test_order_transition_table():
    assert_order_transition("SUBMITTED", "ACK")
    assert_order_transition("PARTIAL", "FILLED")
    assert_order_transition("UNKNOWN", "RECONCILED")
    with pytest.raises(IllegalTransitionError):
        assert_order_transition("FILLED", "PARTIAL")  # 倒退
    with pytest.raises(IllegalTransitionError):
        assert_order_transition("FILLED", "RECONCILED")  # 仅 UNKNOWN 可收敛
    with pytest.raises(IllegalTransitionError):
        assert_order_transition("ACK", "SUBMITTED")  # 倒退
