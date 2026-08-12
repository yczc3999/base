"""WP-06 production runtime vertical acceptance on real PostgreSQL.

No operation, observation, state, ledger or artifact row is inserted under replica mode:
all chain facts flow through EvaluationRuntime/Handler/Logic/Repository.
"""
from __future__ import annotations

import asyncio
import copy
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.services.polymarket.polygon_driver import fixture_polygon_transport
from app.services.polymarket.relayer_driver import fixture_relayer_transport
from tests.trading.integration.wp06_runtime_fixture import (
    RUNTIME_IDENTITY,
    build_runtime,
    query,
    request,
    seed_authority,
    seed_real_position_lineage,
    seed_settlement,
    upgrade,
)


async def _environment(temp_pg_db, tmp_path):
    upgrade(temp_pg_db.url)
    ids = seed_authority(temp_pg_db.url)
    runtime, store, state, async_engine = build_runtime(temp_pg_db.url, tmp_path, ids)
    ids = await seed_real_position_lineage(runtime._sessions, temp_pg_db.url, ids)
    settlement_key = await seed_settlement(runtime, store, ids)
    return ids, runtime, state, async_engine, settlement_key


async def _submit_and_finalize(runtime, ids, suffix="vertical"):
    submitted = await runtime.submit_redeem(request(ids, suffix))
    assert submitted["status"] == "RELAYER_NEW" and submitted["replayed"] is False
    finalized = await runtime.recover_chain_operation(
        operation_id=submitted["operation_id"], fencing_token=1
    )
    assert finalized["status"] == "FINALIZED" and finalized["applied"] is True
    return submitted["operation_id"]


@pytest.mark.anyio
async def test_runtime_submit_recover_finalized_multi_namespace_exactly_once(
    temp_pg_db, tmp_path
):
    ids, runtime, state, async_engine, settlement_key = await _environment(temp_pg_db, tmp_path)
    try:
        operation_id = await _submit_and_finalize(runtime, ids)
        assert state["submit_calls"] == 1 and state["status_calls"] == 1
        assert state["transport_in_tx"] and all(count == 0 for _, count in state["transport_in_tx"])

        operation = query(temp_pg_db.url,
            "SELECT status,economic_effect_applied,settlement_set_key,geo_allowed,"
            "geo_evidence_hash,registry_evidence_hash,balance_evidence_hash FROM trading.chain_operations "
            "WHERE id=:id", {"id": operation_id})[0]
        assert operation["status"] == "FINALIZED"
        assert operation["economic_effect_applied"] is True
        assert operation["settlement_set_key"] == settlement_key
        assert operation["geo_allowed"] is True
        assert all(operation[key] for key in (
            "geo_evidence_hash", "registry_evidence_hash", "balance_evidence_hash"
        ))

        positions = query(temp_pg_db.url,
            "SELECT portfolio_namespace,quantity,cost_basis FROM trading.positions ORDER BY portfolio_namespace")
        assert [(row["portfolio_namespace"], row["quantity"], row["cost_basis"]) for row in positions] == [
            (f"exec-{ids['account']}", 0, 0)
        ]
        ledgers = query(temp_pg_db.url,
            "SELECT id,portfolio_namespace,status FROM trading.ledger_transactions "
            "WHERE chain_operation_id=:id ORDER BY portfolio_namespace", {"id": operation_id})
        assert [(row["portfolio_namespace"], row["status"]) for row in ledgers] == [
            (f"exec-{ids['account']}", "POSTED")
        ]
        for ledger in ledgers:
            imbalance = query(temp_pg_db.url,
                "SELECT asset_type,asset_key,sum(amount) AS total FROM trading.ledger_postings "
                "WHERE transaction_id=:id GROUP BY asset_type,asset_key HAVING sum(amount)<>0",
                {"id": ledger["id"]})
            assert imbalance == []
        assert query(temp_pg_db.url,
            "SELECT count(*) AS n FROM trading.transactional_outbox WHERE aggregate_id=:id",
            {"id": "wp06-redeem-vertical"})[0]["n"] == 1
        assert query(temp_pg_db.url,
            "SELECT count(*) AS n FROM trading.external_call_attempts WHERE attempt_key LIKE :prefix",
            {"prefix": "chain:wp06-redeem-vertical:%"})[0]["n"] >= 7
        assert query(temp_pg_db.url,
            "SELECT count(*) AS n FROM trading.workflow_events WHERE event_key LIKE :prefix",
            {"prefix": "chain-recovery:wp06-redeem-vertical:%"})[0]["n"] >= 1

        # Periodic finalized audit re-reads provider facts and never reapplies economics.
        replay = await runtime.recover_chain_operation(
            operation_id=operation_id, fencing_token=1, audit_finalized=True
        )
        assert replay == {"status": "FINALIZED", "replayed": True, "applied": False}
        assert query(temp_pg_db.url,
            "SELECT count(*) AS n FROM trading.ledger_transactions WHERE chain_operation_id=:id",
            {"id": operation_id})[0]["n"] == 1
    finally:
        await async_engine.dispose()


@pytest.mark.anyio
async def test_confirmed_receipt_is_provisional_before_later_finality(
    temp_pg_db, tmp_path
):
    """Two recovery passes preserve CONFIRMED proof before economic finality."""
    ids, runtime, _state, async_engine, _ = await _environment(temp_pg_db, tmp_path)
    original = runtime._polygon._transport
    provisional = True

    @fixture_polygon_transport
    async def delayed_finality(payload, endpoint):
        response = await original(payload, endpoint)
        if (
            provisional
            and payload["method"] == "eth_getBlockByNumber"
            and payload["params"][0] == "finalized"
        ):
            response = copy.deepcopy(response)
            # Same height as the receipt: canonical/mined, but not finalized-after.
            response["result"].update({
                "number": "0x5796657",
                "hash": "0x" + "de" * 32,
            })
        return response

    runtime._polygon._transport = delayed_finality
    try:
        submitted = await runtime.submit_redeem(request(ids, "two-pass-finality"))
        first = await runtime.recover_chain_operation(
            operation_id=submitted["operation_id"], fencing_token=1
        )
        assert first == {
            "status": "MINED_PROVISIONAL", "replayed": False, "applied": False
        }
        history = query(
            temp_pg_db.url,
            "SELECT transition_to FROM trading.chain_operation_state_history "
            "WHERE operation_id=:id ORDER BY sequence_no",
            {"id": submitted["operation_id"]},
        )
        states = [row["transition_to"] for row in history]
        assert states.index("RELAYER_CONFIRMED") < states.index("MINED_PROVISIONAL")
        assert query(
            temp_pg_db.url,
            "SELECT id FROM trading.ledger_transactions WHERE chain_operation_id=:id",
            {"id": submitted["operation_id"]},
        ) == []

        provisional = False
        second = await runtime.recover_chain_operation(
            operation_id=submitted["operation_id"], fencing_token=1
        )
        assert second["status"] == "FINALIZED" and second["applied"] is True
        assert len(query(
            temp_pg_db.url,
            "SELECT id FROM trading.ledger_transactions WHERE chain_operation_id=:id",
            {"id": submitted["operation_id"]},
        )) == 1
    finally:
        await async_engine.dispose()


@pytest.mark.anyio
async def test_wrong_provider_chain_rejected_before_prepare_or_submit(temp_pg_db, tmp_path):
    ids, runtime, state, async_engine, _ = await _environment(temp_pg_db, tmp_path)
    original = runtime._polygon._transport
    prepared = False

    @fixture_polygon_transport
    async def wrong_chain(payload, endpoint):
        response = await original(payload, endpoint)
        if payload["method"] == "eth_chainId":
            response = {**response, "result": "0x1"}
        return response

    async def prepare_forbidden(**_kwargs):
        nonlocal prepared
        prepared = True
        raise AssertionError("relayer prepare reached on wrong chain")

    runtime._polygon._transport = wrong_chain
    runtime._relayer.prepare_batch = prepare_forbidden
    try:
        with pytest.raises(Exception, match="rpc_chain_id_mismatch"):
            await runtime.submit_redeem(request(ids, "wrong-chain"))
        assert prepared is False and state["submit_calls"] == 0
        assert query(temp_pg_db.url, "SELECT id FROM trading.chain_operations") == []
    finally:
        await async_engine.dispose()


@pytest.mark.anyio
async def test_concurrent_identical_submit_has_one_transport_owner(temp_pg_db, tmp_path):
    ids, runtime, _state, async_engine, _ = await _environment(temp_pg_db, tmp_path)
    # During a concurrent test, one task may legitimately own a DB connection while the
    # other is at its outside-UoW boundary; the single-task test above proves own-UoW=0.
    _state["enforce_no_tx"] = False
    try:
        results = await asyncio.gather(
            runtime.submit_redeem(request(ids, "concurrent")),
            runtime.submit_redeem(request(ids, "concurrent")),
        )
        assert _state["submit_calls"] == 1
        assert {row["operation_id"] for row in results}.__len__() == 1
        assert sum(row.get("replayed") is False for row in results) == 1
        assert query(temp_pg_db.url,
            "SELECT count(*) AS n FROM trading.chain_operations WHERE operation_key='wp06-redeem-concurrent'")[0]["n"] == 1
    finally:
        await async_engine.dispose()


@pytest.mark.anyio
async def test_cancelled_submit_persists_unknown_and_recovery_never_resends(temp_pg_db, tmp_path):
    ids, runtime, state, async_engine, _ = await _environment(temp_pg_db, tmp_path)
    original = runtime._relayer._transport

    @fixture_relayer_transport
    async def cancelling(method, path, **kwargs):
        if path == "/submit":
            state["submit_calls"] += 1
            raise asyncio.CancelledError()
        return await original(method, path, **kwargs)

    runtime._relayer._transport = cancelling
    try:
        with pytest.raises(asyncio.CancelledError):
            await runtime.submit_redeem(request(ids, "cancelled"))
        row = query(temp_pg_db.url,
            "SELECT id,status FROM trading.chain_operations WHERE operation_key='wp06-redeem-cancelled'")[0]
        assert row["status"] == "UNKNOWN" and state["submit_calls"] == 1
        # Restore provider reads only. Recovery queries status/nonce/RPC and has no submit API.
        runtime._relayer._transport = original
        await runtime.recover_chain_operation(operation_id=row["id"], fencing_token=1)
        assert state["submit_calls"] == 1
    finally:
        await async_engine.dispose()


@pytest.mark.anyio
async def test_post_finality_canonical_contradiction_appends_conflict_and_alert(
    temp_pg_db, tmp_path
):
    ids, runtime, state, async_engine, _ = await _environment(temp_pg_db, tmp_path)
    try:
        operation_id = await _submit_and_finalize(runtime, ids, "post-final-audit")
        original = runtime._polygon._transport

        @fixture_polygon_transport
        async def removed_receipt(payload, endpoint):
            raw = await original(payload, endpoint)
            if payload["method"] == "eth_getTransactionReceipt" and raw.get("result"):
                raw = {**raw, "result": {**raw["result"], "logs": [{"removed": True}]}}
            return raw

        runtime._polygon._transport = removed_receipt
        result = await runtime.recover_chain_operation(
            operation_id=operation_id, fencing_token=1, audit_finalized=True
        )
        assert result["status"] == "SETTLEMENT_CONFLICT" and result["applied"] is False
        assert query(temp_pg_db.url,
            "SELECT count(*) AS n FROM trading.alert_events WHERE code='CHAIN_FINALITY_CONTRADICTION'")[0]["n"] == 1
        assert query(temp_pg_db.url,
            "SELECT count(*) AS n FROM trading.ledger_transactions WHERE chain_operation_id=:id",
            {"id": operation_id})[0]["n"] == 1
    finally:
        await async_engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["missing_receipt", "relayer_failed", "shifted_receipt"])
async def test_post_final_audit_requires_same_affirmative_provider_facts(
    temp_pg_db, tmp_path, mode
):
    ids, runtime, state, async_engine, _ = await _environment(temp_pg_db, tmp_path)
    try:
        operation_id = await _submit_and_finalize(runtime, ids, f"audit-{mode}")
        polygon_original = runtime._polygon._transport
        relayer_original = runtime._relayer._transport
        shifted_number = "0x5796658"
        shifted_hash = "0x" + "d1" * 32

        @fixture_polygon_transport
        async def audited_polygon(payload, endpoint):
            if (
                mode == "shifted_receipt"
                and payload["method"] == "eth_getBlockByNumber"
                and payload["params"][0] == shifted_number
            ):
                original_block = await polygon_original(
                    {**payload, "params": ["0x5796657", False]}, endpoint
                )
                response = copy.deepcopy(original_block)
                response["result"].update({"number": shifted_number, "hash": shifted_hash})
                return response
            response = await polygon_original(payload, endpoint)
            if payload["method"] == "eth_getTransactionReceipt":
                response = copy.deepcopy(response)
                if mode == "missing_receipt":
                    response["result"] = None
                elif mode == "shifted_receipt":
                    response["result"].update({
                        "blockNumber": shifted_number, "blockHash": shifted_hash,
                    })
            return response

        @fixture_relayer_transport
        async def audited_relayer(method, path, **kwargs):
            status, body = await relayer_original(method, path, **kwargs)
            if mode == "relayer_failed" and path.startswith("/v1/account/transactions/tx-"):
                parsed = json.loads(body)
                parsed["state"] = "STATE_FAILED"
                body = json.dumps(parsed, separators=(",", ":")).encode()
            return status, body

        runtime._polygon._transport = audited_polygon
        runtime._relayer._transport = audited_relayer
        result = await runtime.recover_chain_operation(
            operation_id=operation_id, fencing_token=1, audit_finalized=True
        )
        assert result == {
            "status": "SETTLEMENT_CONFLICT", "replayed": False, "applied": False
        }
        assert state["submit_calls"] == 1
        assert len(query(
            temp_pg_db.url,
            "SELECT id FROM trading.ledger_transactions WHERE chain_operation_id=:id",
            {"id": operation_id},
        )) == 1
        assert len(query(
            temp_pg_db.url,
            "SELECT id FROM trading.alert_events WHERE code='CHAIN_FINALITY_CONTRADICTION'",
        )) == 1
    finally:
        await async_engine.dispose()
