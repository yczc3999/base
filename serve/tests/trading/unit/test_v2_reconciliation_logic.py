"""WP-05 Checkpoint C：ReconciliationLogic 纯函数单测（diff=0 收敛、空页不误判）。"""

from __future__ import annotations

import pytest

from app.logics.trading.reconciliation import compute_reconcile_differences
from runtimes.trading.reconciliation import ReconciliationRuntime, _bounded_trade_after


def _order(ext_id, status="ACK"):
    return {"external_order_id": ext_id, "status": status}


def _trade(trade_id):
    return {"external_trade_id": trade_id}


def _position(token_id, quantity):
    return {"token_id": token_id, "quantity": str(quantity)}


def _funds(asset_key, confirmed, provider=0, local=0):
    return {
        "asset_key": asset_key, "confirmed": str(confirmed),
        "provider_reserved": str(provider), "local_reserved": str(local),
    }


def _empty_ctx():
    return {
        "local_orders": [], "remote_orders": [], "local_trades": [], "remote_trades": [],
        "reservations": [], "positions_local": [], "positions_remote": [],
        "funds_local": [], "funds_remote": [], "ledger_sums": [], "unknown_queries": {},
    }


def test_diff_zero_converges():
    ctx = _empty_ctx()
    ctx.update({
        "local_orders": [_order("o1", "FILLED")],
        "remote_orders": [],
        "local_trades": [_trade("t1")],
        "remote_trades": [_trade("t1")],
        "reservations": [{"reservation_key": "r1", "status": "CONSUMED"}],
        "positions_local": [_position("tok-1", 10)],
        "positions_remote": [_position("tok-1", 10)],
        "funds_local": [_funds("USD", 100)],
        "funds_remote": [_funds("USD", 100)],
        "ledger_sums": [{"asset_type": "TOKEN", "asset_key": "tok", "signed_sum": "0"}],
    })
    diffs = compute_reconcile_differences(**ctx)
    assert diffs == []


def test_order_mismatch_flagged():
    ctx = _empty_ctx()
    ctx.update({
        "local_orders": [_order("o1", "ACK")],
        "remote_orders": [],
        "remote_trades": [_trade("t1")],
    })
    diffs = compute_reconcile_differences(**ctx)
    assert any(d["kind"] == "order" for d in diffs)


def test_unknown_empty_page_not_misjudged():
    """一次空页不证明 UNKNOWN 未提交：UNKNOWN 且 REST 无证明 → 差异。"""
    ctx = _empty_ctx()
    ctx.update({
        "local_orders": [_order("o-unknown", "UNKNOWN")],
        "remote_orders": [],
        "remote_trades": [],
    })
    diffs = compute_reconcile_differences(**ctx)
    assert any(d["kind"] == "unknown" and "empty_page_not_proof" in d["detail"] for d in diffs)
    # Diff computation consumes explicit lookup evidence and never invents proof
    # from a complete-list empty page.
    assert ctx["unknown_queries"]["unknown_orders"] == []


def test_unknown_open_proof_converges_without_resolving_hard_stop_projection():
    """逐单 OPEN proof 可完成 observation；UNKNOWN 状态仍由 submit gate 阻断增仓。"""
    ctx = _empty_ctx()
    ctx.update({
        "local_orders": [{
            "id": 7, "external_order_id": "o-open", "status": "UNKNOWN",
        }],
        "remote_orders": [_order("o-open", "ACK")],
        "reservations": [{"reservation_key": "r-open", "status": "PROVIDER_BOUND"}],
        "unknown_queries": {"unknown_orders": [{
            "local_order_id": 7,
            "external_order_id": "o-open",
            "complete": True,
            "resolution": "OPEN",
        }]},
    })
    assert compute_reconcile_differences(**ctx) == []


def test_terminal_order_not_unknown_flagged():
    """非 UNKNOWN 的 terminal 订单不被当作未决 unknown。"""
    ctx = _empty_ctx()
    ctx.update({
        "local_orders": [_order("o-filled", "FILLED"), _order("o-cancelled", "CANCELLED")],
        "remote_orders": [],
        "remote_trades": [],
    })
    diffs = compute_reconcile_differences(**ctx)
    assert not any(d["kind"] == "unknown" for d in diffs)
    assert ctx["unknown_queries"].get("unknown_orders", []) == []


def test_unresolved_reservation_flagged():
    ctx = _empty_ctx()
    ctx.update({
        "reservations": [{"reservation_key": "r1", "status": "UNKNOWN"}],
    })
    diffs = compute_reconcile_differences(**ctx)
    assert any(d["kind"] == "reservation" for d in diffs)


def test_ledger_unbalanced_flagged():
    ctx = _empty_ctx()
    ctx.update({
        "ledger_sums": [{"asset_type": "TOKEN", "asset_key": "tok", "signed_sum": "5"}],
    })
    diffs = compute_reconcile_differences(**ctx)
    assert any(d["kind"] == "ledger" and d["detail"] == "ledger_unbalanced" for d in diffs)


def test_position_mismatch_flagged():
    ctx = _empty_ctx()
    ctx.update({
        "positions_local": [_position("tok-1", 10)],
        "positions_remote": [_position("tok-1", 7)],
    })
    diffs = compute_reconcile_differences(**ctx)
    assert any(d["kind"] == "position" for d in diffs)


def test_funds_mismatch_flagged():
    ctx = _empty_ctx()
    ctx.update({
        "funds_local": [_funds("USD", 100)],
        "funds_remote": [_funds("USD", 90)],
    })
    diffs = compute_reconcile_differences(**ctx)
    assert any(d["kind"] == "funds" for d in diffs)


class _ExactOrderDriver:
    def __init__(self, order):
        self.order = order

    async def get_order(self, *, order_id):
        del order_id
        return self.order


@pytest.mark.asyncio
async def test_filled_label_needs_full_confirmed_trade_facts():
    runtime = ReconciliationRuntime(lambda: None)
    local = [{
        "local_order_id": 1,
        "external_order_id": "order-1",
        "expected_order_hash": "expected-1",
        "size": "10",
        "filled_size": "0",
        "timestamp": 1_700_000_000,
    }]
    exact = {
        "order_id": "order-1", "token_id": "token-1", "side": "SELL",
        "price": "0.5", "size": "10", "size_matched": "10", "status": "FILLED",
    }
    # MATCHED/MINED/RETRYING/missing are lifecycle observations only.  The sole
    # confirmed fact is 2, so even an exact FILLED label cannot finalize UNKNOWN.
    trades = [
        {"trade_id": "m", "order_id": "order-1", "size": "2", "status": "MATCHED"},
        {"trade_id": "n", "order_id": "order-1", "size": "2", "status": "MINED"},
        {"trade_id": "r", "order_id": "order-1", "size": "2", "status": "RETRYING"},
        {"trade_id": "x", "order_id": "order-1", "size": "2"},
        {"trade_id": "c", "order_id": "order-1", "size": "2", "status": "CONFIRMED"},
    ]
    result = await runtime._query_unknowns(
        local,
        private_driver=_ExactOrderDriver(exact),
        complete_orders=[],
        complete_trades=trades,
        trade_history_complete=True,
        trade_after="1700000000",
    )
    proof = result["unknown_orders"][0]
    assert proof["resolution"] is None
    assert proof["filled_size"] == "2"
    assert proof["provider_size_matched"] == "10"
    assert proof["proof"] == "filled_status_missing_confirmed_trade_facts"


@pytest.mark.asyncio
async def test_full_confirmed_history_can_finalize_unknown_filled():
    runtime = ReconciliationRuntime(lambda: None)
    result = await runtime._query_unknowns(
        [{
            "local_order_id": 1, "external_order_id": "order-1",
            "expected_order_hash": "expected-1", "size": "10",
            "filled_size": "0", "timestamp": 1_700_000_000,
        }],
        private_driver=_ExactOrderDriver({
            "order_id": "order-1", "token_id": "token-1", "side": "SELL",
            "price": "0.5", "size": "10", "size_matched": "10", "status": "FILLED",
        }),
        complete_orders=[],
        complete_trades=[{
            "trade_id": "confirmed", "order_id": "order-1", "size": "10",
            "status": "CONFIRMED",
        }],
        trade_history_complete=True,
        trade_after="1700000000",
    )
    assert result["unknown_orders"][0]["resolution"] == "FILLED"
    assert result["unknown_orders"][0]["filled_size"] == "10"


@pytest.mark.asyncio
async def test_exact_404_with_nonconfirmed_trade_is_not_absence_proof():
    runtime = ReconciliationRuntime(lambda: None)
    result = await runtime._query_unknowns(
        [{
            "local_order_id": 1, "external_order_id": "order-1",
            "expected_order_hash": "expected-1", "size": "10",
            "filled_size": "0", "timestamp": 1_700_000_000,
        }],
        private_driver=_ExactOrderDriver(None),
        complete_orders=[],
        complete_trades=[{
            "trade_id": "matched", "order_id": "order-1", "size": "10",
            "status": "MATCHED",
        }],
        trade_history_complete=True,
        trade_after="1700000000",
    )
    proof = result["unknown_orders"][0]
    assert proof["resolution"] is None
    assert proof["proof"] == "exact_404_trade_observed_not_terminal"
    assert proof["confirmed_trade_count"] == 0


def test_unknown_attempt_timestamp_bounds_trade_lookback():
    unknown = [{"timestamp": 1_700_000_000}, {"timestamp": 1_700_000_100}]
    assert _bounded_trade_after(requested=None, unknown_orders=unknown) == "1700000000"
    assert _bounded_trade_after(requested="1800000000", unknown_orders=unknown) == "1700000000"
    assert _bounded_trade_after(requested="1600000000", unknown_orders=unknown) == "1600000000"
