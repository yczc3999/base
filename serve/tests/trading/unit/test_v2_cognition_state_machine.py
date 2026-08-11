"""Cognition state machine unit tests（WP-02 Checkpoint C）。

- ORDER 扩展为 G0→R0→G1→G2→R1→G4→G5A→G5B→G6。
- assert_order 拒绝越步/未知 gate。
- terminal_g6_fail 需要 G6 FAIL 证据。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.orchestrator.trading_state_machine import IllegalTransitionError, TradingStateMachine
from runtimes.trading.cognition import CognitionRuntime

ORDER = ("G0", "R0", "G1", "G2", "R1", "G4", "G5A", "G5B", "G6")


class _FakeSession:
    pass


class _FakeUoW:
    session = _FakeSession()


class _FakeWorkflow:
    def __init__(self) -> None:
        self.gate = None
        self.episode = None
        self.terminal_calls: list[tuple[int, str]] = []

    async def get_gate_decision(self, session, *, gate, target_kind, target_id):
        return self.gate

    async def get_episode(self, session, episode_id):
        return self.episode

    async def terminal_episode(self, session, episode_id, *, drop_reason):
        self.terminal_calls.append((episode_id, drop_reason))
        self.episode = {"id": episode_id, "status": "PRE_COMMIT_TERMINAL", "drop_reason": drop_reason}
        return True


def _machine() -> tuple[TradingStateMachine, _FakeWorkflow]:
    wf = _FakeWorkflow()
    return TradingStateMachine(wf), wf


class TestOrder:
    @pytest.mark.parametrize("pair", [("R1", "G4"), ("G4", "G5A"), ("G5A", "G5B"), ("G5B", "G6")])
    def test_sequential_cognition_transitions(self, pair):
        machine, _ = _machine()
        machine.assert_order(pair[0], pair[1])

    @pytest.mark.parametrize("pair", [
        ("R0", "G4"), ("G2", "G5A"), ("R1", "G6"), ("G4", "G6"), ("G6", "G4"),
    ])
    def test_skip_rejected(self, pair):
        machine, _ = _machine()
        with pytest.raises(IllegalTransitionError):
            machine.assert_order(pair[0], pair[1])

    def test_unknown_gate(self):
        machine, _ = _machine()
        with pytest.raises(IllegalTransitionError):
            machine.assert_order("G0", "G99")

    def test_full_order_successive(self):
        machine, _ = _machine()
        for i in range(len(ORDER) - 1):
            machine.assert_order(ORDER[i], ORDER[i + 1])


class TestTerminalG6Fail:
    async def _gate(self, result: str, reason: str | None):
        wf = _FakeWorkflow()
        wf.gate = {"gate": "G6", "target_kind": "episode", "target_id": 1,
                   "result": result, "reason_code": reason}
        wf.episode = {"id": 1, "status": "ROUTED"}
        return TradingStateMachine(wf), wf

    async def test_terminal_on_g6_fail(self):
        machine, wf = await self._gate("FAIL", "g6_q_incoherent")
        ok = await machine.terminal_g6_fail(_FakeUoW(), 1, "g6_q_incoherent")
        assert ok
        assert wf.terminal_calls == [(1, "g6_q_incoherent")]

    async def test_missing_g6_fail_evidence(self):
        machine, wf = await self._gate("PASS", None)
        with pytest.raises(IllegalTransitionError):
            await machine.terminal_g6_fail(_FakeUoW(), 1, "g6_q_incoherent")

    async def test_reason_mismatch(self):
        machine, _wf = await self._gate("FAIL", "g6_q_incoherent")
        with pytest.raises(IllegalTransitionError):
            await machine.terminal_g6_fail(_FakeUoW(), 1, "g6_other_reason")


class _RuntimeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def close(self) -> None:
        self.closes += 1


class _RecordingHandler:
    def __init__(self) -> None:
        self.events = []

    async def handle(self, uow, event, **kwargs):
        # Accessing this property is the regression check: passing an un-entered
        # UnitOfWork used to raise ``uow_not_entered`` here.
        assert uow.session is not None
        self.events.append((event, kwargs))
        return SimpleNamespace(ok=True, reason=None)


@pytest.mark.asyncio
async def test_cognition_runtime_enters_uow_and_binds_accepted_invocations():
    session = _RuntimeSession()
    runtime = CognitionRuntime(
        lambda: session,
        gateway=object(),
        artifacts=object(),
    )
    handler = _RecordingHandler()
    runtime._handler = handler

    result = await runtime.run_cognition_chain(
        episode_id=7,
        version_manifest_id=3,
        evidence_coverage_policy_hash="p" * 64,
        prior_payload={},
        revision_payloads=[{}],
        bundle_payload={},
        coverage_policy_payload={},
        covered_branches=[],
        submission_payload={},
        material_payload={},
        lease_payload={},
        prior_invocation_id=101,
        revision_invocation_ids=[102],
        forecast_invocation_id=103,
    )

    assert result["ok"] is True
    assert session.commits == 1 and session.rollbacks == 0 and session.closes == 1
    by_kind = {event.kind: event for event, _kwargs in handler.events}
    assert by_kind["g4_prior"].accepted_invocation_id == 101
    assert by_kind["evidence_revision"].accepted_invocation_id == 102
    assert by_kind["g6_commit"].accepted_invocation_id == 103
