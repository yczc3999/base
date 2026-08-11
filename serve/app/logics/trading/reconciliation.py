"""确定性对账 Logic（WP-05 Checkpoint C）。

- ``start_reconcile``：创建 RECONCILING 对账（停止增仓）。
- ``complete_reconcile``：对比本地 order/trade/reservation/position/funds/ledger 与 REST
  观察，计算差异；只有完整 pages + diff=0 可 COMPLETED；任一非 0 → FAILED + hard stop/alert。
- 一次空页**不**证明 UNKNOWN 未提交。逐单证明为 OPEN/PARTIAL 时只绑定 provider
  identity，订单仍保持 UNKNOWN（继续阻止增仓），但该次对账可以收敛；只有权威
  terminal 证明才执行 UNKNOWN→RECONCILED。
- 纯函数 ``compute_reconcile_differences`` 可独立单测（diff=0 收敛 / 空页不误判）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository
from app.schemas.trading.execution import ReconcileInput

ZERO = Decimal("0")

_UNRESOLVED_STATUSES = ("UNKNOWN", "HELD")


def _dec(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def compute_reconcile_differences(
    *,
    local_orders: list[dict[str, Any]],
    remote_orders: list[dict[str, Any]],
    local_trades: list[dict[str, Any]],
    remote_trades: list[dict[str, Any]],
    reservations: list[dict[str, Any]],
    positions_local: list[dict[str, Any]],
    positions_remote: list[dict[str, Any]],
    funds_local: list[dict[str, Any]],
    funds_remote: list[dict[str, Any]],
    ledger_sums: list[dict[str, Any]],
    unknown_queries: dict[str, Any],
) -> list[dict[str, Any]]:
    """返回差异列表；空列表 = 收敛（可 COMPLETED）。

    - order：本地 external_order_id 集合 vs REST open orders。
    - trade：本地 external_trade_id 集合 vs REST trades。
    - reservation：UNKNOWN/HELD 未定案→差异；PROVIDER_BOUND 是正常 open-order 投影。
    - position：本地 (token_id→size) vs REST (token_id→size)。
    - funds：本地 confirmed/provider_reserved/local_reserved vs REST balance。
    - ledger：每 asset signed 合计必须为 0。
    - UNKNOWN 订单若没有逐单权威证明 → 差异（一次空页不证明未提交）。
    """
    diffs: list[dict[str, Any]] = []

    # 只有仍处于 open-ish 状态的本地订单必须出现在 REST open orders；terminal 订单不在 open 列表。
    reconciled_open_ids = {
        item.get("external_order_id")
        for item in unknown_queries.get("unknown_orders", [])
        if item.get("complete") and item.get("resolution") in {"OPEN", "ACK", "PARTIAL"}
    }
    open_statuses = {"OPEN", "ACK", "PARTIAL", "UNKNOWN"}
    local_order_ids = {
        o["external_order_id"]
        for o in local_orders
        if o.get("external_order_id") and (
            o.get("status") in open_statuses
            or (
                o.get("status") == "RECONCILED"
                and o.get("external_order_id") in reconciled_open_ids
            )
        )
    }
    remote_order_ids = {o["external_order_id"] for o in remote_orders if o.get("external_order_id")}
    for order_id in sorted(local_order_ids - remote_order_ids):
        diffs.append({
            "kind": "order", "external_order_id": order_id,
            "detail": "local_order_not_in_rest",
        })
    for order_id in sorted(remote_order_ids - local_order_ids):
        diffs.append({
            "kind": "order", "external_order_id": order_id,
            "detail": "rest_order_not_local",
        })

    local_trade_ids = {
        t["external_trade_id"] for t in local_trades if t.get("external_trade_id")
    }
    remote_trade_ids = {
        t["external_trade_id"] for t in remote_trades if t.get("external_trade_id")
    }
    for trade_id in sorted(local_trade_ids - remote_trade_ids):
        diffs.append({
            "kind": "trade", "external_trade_id": trade_id,
            "detail": "local_trade_not_in_rest",
        })
    for trade_id in sorted(remote_trade_ids - local_trade_ids):
        diffs.append({
            "kind": "trade", "external_trade_id": trade_id,
            "detail": "rest_trade_not_local",
        })

    for reservation in reservations:
        if reservation.get("status") in _UNRESOLVED_STATUSES:
            diffs.append({
                "kind": "reservation", "reservation_key": reservation.get("reservation_key"),
                "detail": f"unresolved_{reservation.get('status', '').lower()}",
            })

    def _position_key(p: dict[str, Any]) -> str:
        return str(p.get("external_token_id") or p.get("token_id") or "")

    def _position_qty(p: dict[str, Any]) -> Decimal:
        value = p.get("quantity")
        if value is None:
            value = p.get("size")
        return _dec(value)

    local_positions = {
        _position_key(p): _position_qty(p) for p in positions_local if _position_key(p)
    }
    remote_positions = {
        _position_key(p): _position_qty(p) for p in positions_remote if _position_key(p)
    }
    for token_id in sorted(set(local_positions) | set(remote_positions)):
        local_qty = local_positions.get(token_id, ZERO)
        remote_qty = remote_positions.get(token_id, ZERO)
        if local_qty != remote_qty:
            diffs.append({
                "kind": "position", "token_id": token_id,
                "local": str(local_qty), "remote": str(remote_qty),
                "detail": "position_mismatch",
            })

    local_funds = {
        f["asset_key"]: f for f in funds_local if f.get("asset_key")
    }
    remote_funds = {
        f["asset_key"]: f for f in funds_remote if f.get("asset_key")
    }
    for asset_key in sorted(set(local_funds) | set(remote_funds)):
        lf = local_funds.get(asset_key, {})
        rf = remote_funds.get(asset_key, {})
        l_confirmed = _dec(lf.get("confirmed"))
        r_confirmed = _dec(rf.get("confirmed"))
        l_provider = _dec(lf.get("provider_reserved"))
        r_provider = _dec(rf.get("provider_reserved"))
        l_local = _dec(lf.get("local_reserved"))
        r_local = _dec(rf.get("local_reserved"))
        if l_confirmed != r_confirmed or l_provider != r_provider or l_local != r_local:
            diffs.append({
                "kind": "funds", "asset_key": asset_key,
                "local": {"confirmed": str(l_confirmed), "provider_reserved": str(l_provider),
                          "local_reserved": str(l_local)},
                "remote": {"confirmed": str(r_confirmed), "provider_reserved": str(r_provider),
                           "local_reserved": str(r_local)},
                "detail": "funds_mismatch",
            })

    for row in ledger_sums:
        if _dec(row.get("signed_sum")) != ZERO:
            diffs.append({
                "kind": "ledger", "asset_type": row.get("asset_type"),
                "asset_key": row.get("asset_key"), "signed_sum": str(row.get("signed_sum")),
                "detail": "ledger_unbalanced",
            })

    # UNKNOWN 订单：一次空页不证明未提交。OPEN/PARTIAL 的逐单证明足以让本次
    # observation 收敛，但订单继续保持 UNKNOWN，因此 execution preflight 仍 hard-stop。
    for order in local_orders:
        if order.get("status") != "UNKNOWN":
            continue
        ext_id = order.get("external_order_id")
        records = unknown_queries.setdefault("unknown_orders", [])
        record = next(
            (
                item for item in records
                if item.get("local_order_id") == order.get("id")
                or (ext_id and item.get("external_order_id") == ext_id)
            ),
            None,
        )
        proven = bool(record and record.get("complete") and record.get("resolution"))
        if not proven:
            diffs.append({
                "kind": "unknown", "external_order_id": ext_id,
                "detail": "unknown_unresolved_empty_page_not_proof",
            })
    return diffs


@dataclass(frozen=True)
class ReconcileResult:
    ok: bool
    status: str
    differences: list[dict[str, Any]]
    reconciliation_id: int | None = None
    output_manifest_hash: str | None = None


class ReconciliationLogic:
    """DB-backed 对账：start（RECONCILING，停止增仓）→ REST 观察 → complete（COMPLETED/FAILED）。"""

    def __init__(
        self,
        execution: ExecutionRepository | None = None,
        ledger: LedgerRepository | None = None,
        audit: AuditRepository | None = None,
    ) -> None:
        self._execution = execution if execution is not None else ExecutionRepository()
        self._ledger = ledger if ledger is not None else LedgerRepository()
        self._audit = audit if audit is not None else AuditRepository()

    async def start_reconcile(
        self, uow: UnitOfWork, *, input_: ReconcileInput, owner: str,
    ) -> dict[str, Any]:
        lease = await self._execution.get_active_lease_fence(
            uow.session,
            account_id=input_.account_id,
            lease_role="EXECUTION",
            owner=owner,
            fencing_token=input_.fencing_token,
            for_update=True,
        )
        if lease is None:
            raise RuntimeError("stale_fence_rejected")
        account = await self._execution.get_account(uow.session, account_id=input_.account_id)
        if account is None:
            raise RuntimeError("reconcile_account_missing")
        rest_page_cursor: dict[str, Any] = {"cursor": input_.rest_cursor}
        input_manifest_hash = canonical_hash({
            "account_id": input_.account_id,
            "trigger_reason": input_.trigger_reason,
            "ws_watermark": input_.ws_watermark,
            "rest_cursor": input_.rest_cursor,
        })
        reconciliation = await self._execution.insert_reconciliation(
            uow.session,
            reconciliation_key=input_.reconciliation_key,
            account_id=input_.account_id,
            trigger_reason=input_.trigger_reason,
            ws_watermark=input_.ws_watermark,
            rest_page_cursor=rest_page_cursor,
            rest_page_hash=canonical_hash(rest_page_cursor),
            unknown_queries={"unknown_orders": []},
            input_manifest_hash=input_manifest_hash,
            fencing_token=input_.fencing_token,
        )
        if self._audit is not None:
            await self._audit.insert_workflow_event(
                uow.session,
                event_key=f"wf:reconcile:{reconciliation['id']}:start",
                event_type="reconciliation.start",
                aggregate_type="reconciliation",
                aggregate_id=str(reconciliation["id"]),
                payload_hash=canonical_hash({"account_id": input_.account_id}),
                payload={"account_id": input_.account_id, "reconciliation_id": reconciliation["id"]},
            )
        return reconciliation

    async def complete_reconcile(
        self,
        uow: UnitOfWork,
        *,
        reconciliation_id: int,
        account_id: int,
        owner: str,
        fencing_token: int,
        remote_orders: list[dict[str, Any]],
        remote_trades: list[dict[str, Any]],
        remote_positions: list[dict[str, Any]],
        remote_funds: list[dict[str, Any]],
        unknown_queries: dict[str, Any],
        observation_manifest: dict[str, Any] | None = None,
    ) -> ReconcileResult:
        lease = await self._execution.get_active_lease_fence(
            uow.session,
            account_id=account_id,
            lease_role="EXECUTION",
            owner=owner,
            fencing_token=fencing_token,
            for_update=True,
        )
        if lease is None:
            raise RuntimeError("stale_fence_rejected")
        reconciliation = await self._execution.get_reconciliation(
            uow.session, reconciliation_id=reconciliation_id, for_update=True
        )
        if reconciliation is None:
            raise RuntimeError("reconcile_missing")
        if reconciliation["account_id"] != account_id:
            raise RuntimeError("reconcile_cross_account")
        if reconciliation["fencing_token"] != fencing_token:
            raise RuntimeError("stale_fence_rejected")
        await self._apply_unknown_proofs(
            uow,
            account_id=account_id,
            fencing_token=fencing_token,
            unknown_queries=unknown_queries,
        )
        local_orders = await self._execution.list_orders_for_account(
            uow.session, account_id=account_id
        )
        local_trade_ids = await self._execution.all_external_trade_ids(
            uow.session, account_id=account_id
        )
        local_trade_rows = [
            {"external_trade_id": trade_id} for trade_id in local_trade_ids
        ]
        reservations = await _collect(
            _query_reservations(self._execution, uow.session, account_id)
        )
        positions_local = await self._execution.positions_for_account(
            uow.session, account_id=account_id
        )
        funds_local = await _query_funds(self._execution, uow.session, account_id)
        ledger_sums = await self._execution.per_asset_ledger_sums(
            uow.session, account_id=account_id
        )
        differences = compute_reconcile_differences(
            local_orders=local_orders,
            remote_orders=remote_orders,
            local_trades=local_trade_rows,
            remote_trades=remote_trades,
            reservations=reservations,
            positions_local=positions_local,
            positions_remote=remote_positions,
            funds_local=funds_local,
            funds_remote=remote_funds,
            ledger_sums=ledger_sums,
            unknown_queries=unknown_queries,
        )
        output_manifest_hash = canonical_hash({
            "remote_orders": remote_orders,
            "remote_trades": remote_trades,
            "remote_positions": remote_positions,
            "remote_funds": remote_funds,
            "unknown_queries": unknown_queries,
            "observation_manifest": observation_manifest or {},
        })
        new_status = "COMPLETED" if not differences else "FAILED"
        completed_at = datetime.now(timezone.utc) if new_status == "COMPLETED" else None
        updated = await self._execution.complete_reconciliation(
            uow.session,
            reconciliation_id=reconciliation_id,
            output_manifest_hash=output_manifest_hash,
            differences=differences,
            new_status=new_status,
            completed_at=completed_at,
        )
        if not updated:
            raise RuntimeError("reconcile_complete_conflict")
        if new_status == "FAILED":
            if self._audit is not None:
                await self._audit.insert_alert_event(
                    uow.session,
                    alert_key=f"alert:reconcile:{reconciliation_id}:failed",
                    severity="CRITICAL",
                    code="reconcile_differences_nonzero",
                    message_redacted=f"reconcile failed with {len(differences)} differences; hard stop",
                )
        return ReconcileResult(
            ok=new_status == "COMPLETED",
            status=new_status,
            differences=differences,
            reconciliation_id=reconciliation_id,
            output_manifest_hash=output_manifest_hash,
        )

    async def _apply_unknown_proofs(
        self,
        uow: UnitOfWork,
        *,
        account_id: int,
        fencing_token: int,
        unknown_queries: dict[str, Any],
    ) -> None:
        """Apply complete per-order REST proofs before computing the convergence diff.

        Non-terminal proofs deliberately do *not* resolve the UNKNOWN order.  They only
        bind its provider id and move the still-live reservation to PROVIDER_BOUND.  This
        lets a complete REST observation finish while ``submit_blocked_unknown_order``
        remains in force.  UNKNOWN -> RECONCILED is reserved for terminal authority.
        """
        from sqlalchemy import text as _text

        for proof in unknown_queries.get("unknown_orders", []):
            if not proof.get("complete") or not proof.get("resolution"):
                continue
            local_order_id = proof.get("local_order_id")
            if local_order_id is None:
                continue
            order = await self._execution.get_order(
                uow.session, order_id=int(local_order_id), for_update=True
            )
            if order is None or order["account_id"] != account_id:
                raise RuntimeError("reconcile_unknown_order_missing")
            if order["status"] != "UNKNOWN":
                continue
            resolution = str(proof["resolution"]).upper()
            non_terminal = resolution in {"OPEN", "ACK", "PARTIAL"}
            terminal = resolution in {
                "FILLED", "CANCELLED", "REJECTED", "NOT_FOUND_TERMINAL",
            }
            if not non_terminal and not terminal:
                # Unknown provider vocabulary is not authority to mutate projections.
                continue
            external_order_id = proof.get("external_order_id") or order.get("external_order_id")
            intent_id = (
                await uow.session.execute(
                    _text(
                        "SELECT e.intent_id FROM trading.exchange_order_attempts a "
                        "JOIN trading.execution_authorization_envelopes e ON e.id=a.envelope_id "
                        "WHERE a.id=:attempt"
                    ),
                    {"attempt": order["attempt_id"]},
                )
            ).scalar_one()
            reservation = await self._execution.get_reservation_by_intent(
                uow.session,
                account_id=account_id,
                intent_id=int(intent_id),
                for_update=True,
            )
            if reservation is not None and reservation["status"] in {
                "UNKNOWN", "PROVIDER_BOUND",
            }:
                amount = _dec(reservation["amount"])
                consumed = _dec(reservation.get("consumed_amount"))
                released = _dec(reservation.get("released_amount"))
                remaining = max(ZERO, amount - consumed - released)
                if non_terminal and reservation["status"] == "UNKNOWN" and remaining:
                    if not await self._execution.transfer_funds_local_to_provider(
                        uow.session,
                        account_id=account_id,
                        asset_key=reservation["asset_key"],
                        amount=remaining,
                    ):
                        raise RuntimeError("reconcile_funds_transfer_conflict")
                    if not await self._execution.advance_reservation(
                        uow.session,
                        reservation_id=reservation["id"],
                        new_status="PROVIDER_BOUND",
                        expected_status="UNKNOWN",
                    ):
                        raise RuntimeError("reconcile_reservation_conflict")
                elif resolution in {"CANCELLED", "REJECTED", "NOT_FOUND_TERMINAL"} and remaining:
                    if reservation["status"] == "PROVIDER_BOUND":
                        released_ok = await self._execution.release_funds_provider(
                            uow.session,
                            account_id=account_id,
                            asset_key=reservation["asset_key"],
                            amount=remaining,
                        )
                    else:
                        released_ok = await self._execution.release_funds_local(
                            uow.session,
                            account_id=account_id,
                            asset_key=reservation["asset_key"],
                            amount=remaining,
                        )
                    if not released_ok:
                        raise RuntimeError("reconcile_funds_release_conflict")
                    if not await self._execution.advance_reservation(
                        uow.session,
                        reservation_id=reservation["id"],
                        new_status="RELEASED",
                        released_delta=remaining,
                        expected_status=reservation["status"],
                    ):
                        raise RuntimeError("reconcile_reservation_conflict")
            # OPEN/PARTIAL proves that the provider owns the reservation, not that the
            # local uncertainty is terminal.  Bind identity without changing status or
            # filled_size; a later REST trade is applied economically in UNKNOWN mode.
            if non_terminal:
                if external_order_id and not order.get("external_order_id"):
                    if not await self._execution.advance_order(
                        uow.session,
                        order_id=order["id"],
                        new_status="UNKNOWN",
                        external_order_id=external_order_id,
                        expected_status="UNKNOWN",
                    ):
                        raise RuntimeError("reconcile_unknown_bind_conflict")
                continue
            if not await self._execution.advance_order(
                uow.session,
                order_id=order["id"],
                new_status="RECONCILED",
                filled_size=_dec(proof.get("filled_size", order.get("filled_size"))),
                external_order_id=external_order_id,
                expected_status="UNKNOWN",
            ):
                raise RuntimeError("reconcile_unknown_transition_conflict")
            await self._execution.insert_order_state_event(
                uow.session,
                event_key=f"ev:{order['id']}:reconciled:{resolution.lower()}",
                order_id=order["id"],
                event_type="RECONCILED",
                transition_from="UNKNOWN",
                transition_to="RECONCILED",
                event_payload={"resolution": resolution, "proof": proof.get("proof")},
                event_hash=canonical_hash({
                    "order_id": order["id"],
                    "resolution": resolution,
                    "external_order_id": external_order_id,
                }),
                fence_token=fencing_token,
            )


async def _collect(awaitable: Any) -> list[Any]:
    result = await awaitable
    return result if isinstance(result, list) else list(result)


async def _query_reservations(
    execution: ExecutionRepository, session: Any, account_id: int,
) -> list[dict[str, Any]]:
    from sqlalchemy import text as _text

    result = await session.execute(
        _text(
            "SELECT reservation_key, status, asset_key, amount, intent_id "
            "FROM trading.capital_reservations WHERE account_id=:a ORDER BY id"
        ),
        {"a": account_id},
    )
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


async def _query_funds(
    execution: ExecutionRepository, session: Any, account_id: int,
) -> list[dict[str, Any]]:
    from sqlalchemy import text as _text

    result = await session.execute(
        _text(
            "SELECT asset_key, confirmed, provider_reserved, local_reserved "
            "FROM trading.account_funds_current WHERE account_id=:a ORDER BY asset_key"
        ),
        {"a": account_id},
    )
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]
