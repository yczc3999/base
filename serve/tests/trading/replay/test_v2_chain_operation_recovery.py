"""WP-06 UNKNOWN restart recovery through EvaluationRuntime (zero resend)."""
from __future__ import annotations

import asyncio
import pytest

from app.services.polymarket.relayer_driver import fixture_relayer_transport
from tests.trading.integration.wp06_runtime_fixture import (
    build_runtime,
    query,
    request,
    seed_authority,
    seed_real_position_lineage,
    seed_settlement,
    upgrade,
)

@pytest.mark.anyio
async def test_1000_unknown_recoveries_twice_are_equal_and_never_resubmit(
    temp_pg_db, tmp_path
):
    """Two 1,000-call restart passes read the same UNKNOWN fact without resend."""
    url = temp_pg_db.url
    upgrade(url)
    ids = seed_authority(url)
    runtime, store, calls, engine = build_runtime(url, tmp_path / "cas", ids)
    ids = await seed_real_position_lineage(runtime._sessions, url, ids)
    try:
        await seed_settlement(runtime, store, ids)
        original_transport = runtime._relayer._transport

        @fixture_relayer_transport
        async def lose_submit_response(method, path, **kwargs):
            response = await original_transport(method, path, **kwargs)
            if path == "/submit":
                raise asyncio.CancelledError
            return response

        runtime._relayer._transport = lose_submit_response
        with pytest.raises(asyncio.CancelledError):
            await runtime.submit_redeem(request(ids, "unknown"))
        operation = query(
            url,
            "SELECT id,status,body_hash,expected_operation_hash,economic_effect_applied "
            "FROM trading.chain_operations WHERE operation_key=:key",
            {"key": request(ids, "unknown").operation_key},
        )
        assert len(operation) == 1
        operation_id = operation[0]["id"]
        assert operation[0]["status"] == "UNKNOWN"
        assert operation[0]["economic_effect_applied"] is False
        assert calls["submit_calls"] == 1

        # With no transaction id/hash, recovery is evidence-only.  It may query
        # nonce and frozen registry code, but cannot call the write endpoint.
        first = []
        for _ in range(1000):
            first.append(await runtime.recover_chain_operation(
                operation_id=operation_id, fencing_token=1
            ))
        first_fact = query(
            url,
            "SELECT status,body_hash,expected_operation_hash,economic_effect_applied "
            "FROM trading.chain_operations WHERE id=:id",
            {"id": operation_id},
        )

        second = []
        for _ in range(1000):
            second.append(await runtime.recover_chain_operation(
                operation_id=operation_id, fencing_token=1
            ))
        second_fact = query(
            url,
            "SELECT status,body_hash,expected_operation_hash,economic_effect_applied "
            "FROM trading.chain_operations WHERE id=:id",
            {"id": operation_id},
        )

        assert second == first
        assert first_fact == second_fact == [{
            "status": "UNKNOWN",
            "body_hash": operation[0]["body_hash"],
            "expected_operation_hash": operation[0]["expected_operation_hash"],
            "economic_effect_applied": False,
        }]
        assert calls["submit_calls"] == 1
        assert not query(url, "SELECT id FROM trading.ledger_transactions WHERE kind='SETTLEMENT'")
        assert not query(
            url,
            "SELECT id FROM trading.transactional_outbox "
            "WHERE topic='chain.settlement.finalized'",
        )
        # One stable natural-key recovery event proves duplicate observations have
        # effect=0 even across two restart-equivalent passes.
        events = query(
            url,
            "SELECT event_key FROM trading.workflow_events "
            "WHERE event_type='CHAIN_RECOVERY_OBSERVATION'",
        )
        assert len(events) == 1
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_runtime_scheduler_recovers_persisted_unknown_from_db(temp_pg_db, tmp_path):
    """A new runtime instance discovers UNKNOWN from DB and still never submits."""
    url = temp_pg_db.url
    upgrade(url)
    ids = seed_authority(url)
    runtime, store, calls, engine = build_runtime(url, tmp_path / "cas-a", ids)
    ids = await seed_real_position_lineage(runtime._sessions, url, ids)
    await seed_settlement(runtime, store, ids)
    original = runtime._relayer._transport

    @fixture_relayer_transport
    async def lost(method, path, **kwargs):
        response = await original(method, path, **kwargs)
        if path == "/submit":
            raise asyncio.CancelledError
        return response

    runtime._relayer._transport = lost
    try:
        with pytest.raises(asyncio.CancelledError):
            await runtime.submit_redeem(request(ids, "restart"))
        assert calls["submit_calls"] == 1
    finally:
        await engine.dispose()

    restarted, _, restarted_calls, restarted_engine = build_runtime(
        url, tmp_path / "cas-b", ids
    )
    try:
        recovered = await restarted.recover_chain_operations(limit=10)
        assert len(recovered) == 1
        assert recovered[0]["status"] == "UNKNOWN"
        assert restarted_calls["submit_calls"] == 0
        assert query(url, "SELECT status FROM trading.chain_operations") == [{"status": "UNKNOWN"}]
    finally:
        await restarted_engine.dispose()
