"""WP-06 redeem uniqueness/idempotency on the production runtime path."""
from __future__ import annotations

import pytest

from app.schemas.trading.settlement import ChainRedeemRequest
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
async def test_redeem_has_one_active_operation_and_exact_replay(
    temp_pg_db, tmp_path
):
    """Only one active wallet/condition redeem exists; an exact request replays."""
    url = temp_pg_db.url
    upgrade(url)
    ids = seed_authority(url)
    runtime, store, calls, engine = build_runtime(url, tmp_path / "cas", ids)
    ids = await seed_real_position_lineage(runtime._sessions, url, ids)
    try:
        await seed_settlement(runtime, store, ids)
        original = request(ids, "active")
        submitted = await runtime.submit_redeem(original)
        assert submitted["status"] == "RELAYER_NEW"
        assert calls["submit_calls"] == 1

        replay = await runtime.submit_redeem(original)
        assert replay == {
            "operation_id": submitted["operation_id"],
            "status": "RELAYER_NEW",
            "replayed": True,
            "recovery_required": True,
        }
        assert calls["submit_calls"] == 1

        # Same logical operation key is an exact-content contract, not a loose alias.
        mismatched = ChainRedeemRequest(
            **{**original.model_dump(), "idempotency_key": "wp06-idem-different"}
        )
        with pytest.raises(
            RuntimeError, match="chain_existing_operation_mismatch:idempotency_key"
        ):
            await runtime.submit_redeem(mismatched)

        # A different key cannot create a second active redeem for the same
        # account/wallet/condition.  This goes through both provider preflights and
        # the normal TX1 constraint; no operation fact is synthesized by the test.
        competing = request(ids, "competing")
        with pytest.raises(Exception):
            await runtime.submit_redeem(competing)
        assert calls["submit_calls"] == 1

        rows = query(
            url,
            "SELECT operation_key,idempotency_key,status FROM trading.chain_operations",
        )
        assert rows == [{
            "operation_key": original.operation_key,
            "idempotency_key": original.idempotency_key,
            "status": "RELAYER_NEW",
        }]
        claims = query(
            url,
            "SELECT key,owner FROM trading.idempotency_claims "
            "WHERE scope='chain_operation' ORDER BY id",
        )
        assert len(claims) == 1 and claims[0]["key"] == original.idempotency_key
        assert len(claims[0]["owner"]) == 64
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_finality_contradiction_is_conflict_without_second_effect(
    temp_pg_db, tmp_path
):
    """A post-final contradictory receipt records conflict and never pays twice."""
    url = temp_pg_db.url
    upgrade(url)
    ids = seed_authority(url)
    runtime, store, calls, engine = build_runtime(url, tmp_path / "cas", ids)
    ids = await seed_real_position_lineage(runtime._sessions, url, ids)
    try:
        await seed_settlement(runtime, store, ids)
        submitted = await runtime.submit_redeem(request(ids, "conflict"))
        finalized = await runtime.recover_chain_operation(
            operation_id=submitted["operation_id"], fencing_token=1
        )
        assert finalized["status"] == "FINALIZED"

        original_transport = runtime._polygon._transport
        from app.services.polymarket.polygon_driver import fixture_polygon_transport

        @fixture_polygon_transport
        async def removed_receipt(payload, endpoint):
            response = await original_transport(payload, endpoint)
            if payload["method"] == "eth_getTransactionReceipt" and response.get("result"):
                import copy

                response = copy.deepcopy(response)
                response["result"]["logs"] = [{"removed": True}]
            return response

        runtime._polygon._transport = removed_receipt
        conflict = await runtime.recover_chain_operation(
            operation_id=submitted["operation_id"],
            fencing_token=1,
            audit_finalized=True,
        )
        assert conflict == {
            "status": "SETTLEMENT_CONFLICT",
            "replayed": False,
            "applied": False,
        }
        assert calls["submit_calls"] == 1
        assert len(query(
            url,
            "SELECT id FROM trading.ledger_transactions "
            "WHERE chain_operation_id=:operation AND kind='SETTLEMENT'",
            {"operation": submitted["operation_id"]},
        )) == 1
        assert len(query(
            url,
            "SELECT id FROM trading.transactional_outbox "
            "WHERE topic='chain.settlement.finalized'",
        )) == 1
    finally:
        await engine.dispose()
