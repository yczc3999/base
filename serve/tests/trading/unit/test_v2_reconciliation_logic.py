"""WP-05 Checkpoint C：ReconciliationLogic 纯函数单测（diff=0 收敛、空页不误判）。"""

from __future__ import annotations

from app.logics.trading.reconciliation import compute_reconcile_differences


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
    assert ctx["unknown_queries"]["unknown_orders"] == [
        {"external_order_id": "o-unknown", "proven_terminal": False},
    ]


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
