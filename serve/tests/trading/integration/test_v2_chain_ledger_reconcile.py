"""WP-06 append-only settlement-ledger reconstruction acceptance."""
from __future__ import annotations

import pytest

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
async def test_ledger_rebuild_from_final_append_only_facts_is_balanced_and_stable(
    temp_pg_db, tmp_path
):
    url = temp_pg_db.url
    upgrade(url)
    ids = seed_authority(url)
    runtime, store, calls, engine = build_runtime(url, tmp_path / "cas", ids)
    ids = await seed_real_position_lineage(runtime._sessions, url, ids)
    try:
        await seed_settlement(runtime, store, ids)
        submitted = await runtime.submit_redeem(request(ids, "ledger"))
        applied = await runtime.recover_chain_operation(
            operation_id=submitted["operation_id"], fencing_token=1
        )
        assert applied["status"] == "FINALIZED" and applied["applied"] is True

        transaction = query(
            url,
            "SELECT id,transaction_key,portfolio_namespace,chain_operation_id,status "
            "FROM trading.ledger_transactions WHERE chain_operation_id=:operation "
            "AND kind='SETTLEMENT'",
            {"operation": submitted["operation_id"]},
        )
        assert len(transaction) == 1
        tx = transaction[0]
        assert tx["chain_operation_id"] == submitted["operation_id"]
        assert tx["portfolio_namespace"] == f"exec-{ids['account']}"
        assert tx["status"] == "POSTED"

        def reconstruct():
            postings = query(
                url,
                "SELECT posting_no,asset_type,asset_key,amount,counterparty "
                "FROM trading.ledger_postings WHERE transaction_id=:tx "
                "ORDER BY posting_no",
                {"tx": tx["id"]},
            )
            totals = {}
            for row in postings:
                key = (row["asset_type"], row["asset_key"])
                totals[key] = totals.get(key, 0) + row["amount"]
            return postings, totals

        postings_before, totals_before = reconstruct()
        assert len(postings_before) == 4
        assert totals_before and all(value == 0 for value in totals_before.values())
        assert len({row["posting_no"] for row in postings_before}) == 4

        # Finalized audit is a read/reconcile replay.  It may append audit evidence,
        # but must not append or mutate the economic ledger effect.
        replay = await runtime.recover_chain_operation(
            operation_id=submitted["operation_id"],
            fencing_token=1,
            audit_finalized=True,
        )
        assert replay == {"status": "FINALIZED", "replayed": True, "applied": False}
        postings_after, totals_after = reconstruct()
        assert postings_after == postings_before
        assert totals_after == totals_before
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

        position = query(
            url,
            "SELECT quantity,cost_basis,version FROM trading.positions "
            "WHERE account_id=:account AND portfolio_namespace=:namespace",
            {"account": ids["account"], "namespace": f"exec-{ids['account']}"},
        )
        assert len(position) == 1
        assert position[0]["quantity"] == 0
        assert position[0]["cost_basis"] == 0
        assert calls["submit_calls"] == 1
    finally:
        await engine.dispose()
