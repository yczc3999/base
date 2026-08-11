"""WP-05 Checkpoint C：PrivateExecutionLogic 单测（envelope / fencing / kill switch / submit）。"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.trading.hashing import canonical_hash
from app.logics.trading.execution import KillSwitchBlocked, PrivateExecutionLogic
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
    }


def _release(cap_permission_id=10):
    return {
        "id": 2, "release_name": "release/v1",
        "capital_permission_manifest_id": cap_permission_id,
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
        self.envelope = envelope or {
            "id": 7, "status": "ACTIVE", "account_id": 1, "fencing_token": 3,
            "intent_id": 5, "capital_permission_manifest_id": 10,
        }
        self.inserted_envelopes = []
        self.inserted_orders = []
        self.inserted_attempts = []
        self.events = []
        self.leg = {
            "leg_id": 8, "contract_spec_id": 3, "internal_token_id": 4,
            "leg_role": "reduce", "external_token_id": "tok-1", "market_id": 9,
            "trade_decision_id": 6, "release_manifest_id": 2, "experiment_variant": "champion",
            "cash_asset_key": "USD",
        }

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
        "preflight_hash1": "b" * 64,
        "preflight_hash2": "c" * 64,
    }
    values.update(overrides)
    return EnvelopeInput(**values)


@pytest.mark.asyncio
async def test_create_envelope_ok():
    repo = _FakeRepo()
    logic = _logic(repo)
    uow = _FakeUoW()
    envelope = await logic.create_envelope(uow, input_=_envelope_input())
    assert envelope["envelope_key"] == "env-1"
    assert len(repo.inserted_envelopes) == 1


@pytest.mark.asyncio
async def test_create_envelope_rejects_nonzero_capital():
    repo = _FakeRepo(permission=_permission(authorized_capital=100))
    logic = _logic(repo)
    with pytest.raises(RuntimeError, match="envelope_permission_not_shadow_zero"):
        await logic.create_envelope(_FakeUoW(), input_=_envelope_input())


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
        await logic.create_envelope(_FakeUoW(), input_=_envelope_input())


@pytest.mark.asyncio
async def test_prepare_submit_kill_switch_blocks_open():
    repo = _FakeRepo(permission=_permission(kill_switch=True))
    logic = _logic(repo)
    submit = SubmitOrderInput(
        envelope_id=7, account_id=1, fencing_token=3,
        token_id="tok-1", side="BUY", price="0.5", size="10",
    )
    with pytest.raises(KillSwitchBlocked):
        await logic.prepare_submit(
            _FakeUoW(), input_=submit, signed_order=object(),
            body_hash="b" * 64, expected_order_hash="c" * 64, sdk_manifest_hash="d" * 64,
        )
    assert repo.inserted_orders == []


@pytest.mark.asyncio
async def test_prepare_submit_zero_capital_blocks_open():
    repo = _FakeRepo(permission=_permission(authorized_capital=0))
    logic = _logic(repo)
    submit = SubmitOrderInput(
        envelope_id=7, account_id=1, fencing_token=3,
        token_id="tok-1", side="BUY", price="0.5", size="10",
    )
    with pytest.raises(KillSwitchBlocked, match="exposure_increasing_blocked_zero_capital"):
        await logic.prepare_submit(
            _FakeUoW(), input_=submit, signed_order=object(),
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
            _FakeUoW(), input_=submit, signed_order=object(),
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
        _FakeUoW(), input_=submit, signed_order=object(),
        body_hash="b" * 64, expected_order_hash="c" * 64, sdk_manifest_hash="d" * 64,
    )
    assert prepared.order_id == 11
    assert repo.envelope["status"] == "USED"
    assert len(repo.inserted_orders) == 1
    assert len(repo.inserted_attempts) == 1


def test_order_transition_table():
    assert_order_transition("OPEN", "ACK")
    assert_order_transition("PARTIAL", "FILLED")
    assert_order_transition("UNKNOWN", "RECONCILED")
    with pytest.raises(IllegalTransitionError):
        assert_order_transition("FILLED", "PARTIAL")  # 倒退
    with pytest.raises(IllegalTransitionError):
        assert_order_transition("ACK", "OPEN")  # 倒退
