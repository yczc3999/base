"""Fenced User-WS gap -> complete REST observation -> idempotent apply -> diff."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.logics.trading.reconciliation import ReconciliationLogic
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository
from app.schemas.polymarket.user_ws import UserOrderEvent, UserTradeEvent
from runtimes.trading.execution import UserWsExecutionRuntime


class ReconciliationRuntime:
    """No network in a transaction; completion is fenced after all facts are applied."""

    def __init__(self, sessions_factory: Any, audit: AuditRepository | None = None) -> None:
        self._sessions = sessions_factory
        self._audit = audit or AuditRepository()
        self._logic = ReconciliationLogic(
            ExecutionRepository(), LedgerRepository(), self._audit,
        )
        self._ws_apply = UserWsExecutionRuntime(sessions_factory, self._audit)

    async def reconcile(
        self,
        *,
        reconcile_input: Any,
        owner: str,
        clob_driver: Any | None = None,
        data_driver: Any | None = None,
        driver: Any | None = None,
        auth_headers: dict[str, str] | None = None,
        trade_after: str | None = None,
        funds_loader: Any | None = None,
        max_pages: int = 10_000,
    ) -> dict[str, Any]:
        async with UnitOfWork(self._sessions) as uow:
            reconciliation = await self._logic.start_reconcile(
                uow, input_=reconcile_input, owner=owner,
            )
            reconciliation_id = reconciliation["id"]
            account_id = reconcile_input.account_id
            unknown_local = await self._unknown_orders(
                uow, account_id=account_id,
            )
            local_orders = await self._logic._execution.list_orders_for_account(
                uow.session, account_id=account_id
            )
            local_trade_ids = set(
                await self._logic._execution.all_external_trade_ids(
                    uow.session, account_id=account_id
                )
            )

        # UNKNOWN attempts define the minimum REST lookback.  A caller may request
        # more history, never less; otherwise an explicit order 404 plus a truncated
        # trade window could be misclassified as proof that the submit never landed.
        effective_trade_after = _bounded_trade_after(
            requested=trade_after,
            unknown_orders=unknown_local,
        )

        # Do not cross the first REST boundary after a takeover/expiry.
        async with UnitOfWork(self._sessions) as uow:
            fence = await self._logic._execution.get_active_lease_fence(
                uow.session,
                account_id=account_id,
                lease_role="EXECUTION",
                owner=owner,
                fencing_token=reconcile_input.fencing_token,
                for_update=True,
            )
            if fence is None:
                raise RuntimeError("stale_fence_rejected")

        clob_driver = clob_driver or driver
        data_driver = data_driver or driver
        if clob_driver is None or data_driver is None:
            raise RuntimeError("reconcile_drivers_required")
        remote_orders, order_pages = await self._all_clob_orders(
            clob_driver, max_pages=max_pages, auth_headers=auth_headers,
        )
        remote_trades, trade_pages = await self._all_clob_trades(
            clob_driver,
            max_pages=max_pages,
            auth_headers=auth_headers,
            trade_after=effective_trade_after,
        )
        remote_positions, position_pages = await self._all_pages(
            loader=lambda cursor: data_driver.positions(
                cursor=cursor, limit=200, headers=auth_headers
            ),
            kind="positions",
            max_pages=max_pages,
        )
        fund_pages: list[dict[str, Any]] = []
        if funds_loader is None:
            candidate = getattr(data_driver, "funds", None)
            if not callable(candidate):
                raise RuntimeError("reconcile_funds_loader_required")
            fund_items, fund_pages = await self._all_pages(
                loader=lambda cursor: candidate(
                    cursor=cursor, limit=200, headers=auth_headers
                ),
                kind="funds",
                max_pages=max_pages,
            )
            remote_funds = _normalize_funds(fund_items)
        else:
            funds_value = funds_loader(account_id=account_id, headers=auth_headers)
            if hasattr(funds_value, "__await__"):
                funds_value = await funds_value
            remote_funds = _normalize_funds(funds_value)

        normalized_orders = [_normalize_order(item) for item in remote_orders]
        normalized_trades = [_normalize_trade(item) for item in remote_trades]
        normalized_positions = [_normalize_position(item) for item in remote_positions]

        unknown_queries = await self._query_unknowns(
            unknown_local,
            private_driver=clob_driver,
            complete_orders=normalized_orders,
            complete_trades=normalized_trades,
            trade_history_complete=True,
            trade_after=effective_trade_after,
        )
        await self._bind_unknown_provider_ids(
            account_id=account_id,
            owner=owner,
            fencing_token=reconcile_input.fencing_token,
            unknown_queries=unknown_queries,
        )

        # Apply every missed provider fact before taking the local diff snapshot.
        apply_results: list[dict[str, Any]] = []
        local_order_by_external = {
            item.get("external_order_id"): item
            for item in local_orders if item.get("external_order_id")
        }
        unresolved_external_ids = {
            item.get("external_order_id")
            for item in unknown_queries.get("unknown_orders", [])
            if item.get("external_order_id")
        }
        for item in normalized_orders:
            existing = local_order_by_external.get(item["external_order_id"])
            # UNKNOWN provider order facts are consumed only by _apply_unknown_proofs.
            # Sending them through the WS state projector would either request another
            # reconcile or prematurely resolve uncertainty.
            if item["external_order_id"] in unresolved_external_ids:
                continue
            expected_status = _normalize_provider_order_status(item.get("status"))
            if existing is not None and (
                expected_status is None or existing.get("status") == expected_status
            ):
                continue
            result = await self._ws_apply.apply_event(
                account_id=account_id,
                owner=owner,
                fencing_token=reconcile_input.fencing_token,
                event=UserOrderEvent(
                    event_type="order",
                    order_id=item["external_order_id"],
                    token_id=item.get("token_id") or "",
                    side=item.get("side"),
                    price=item.get("price"),
                    size=item.get("size"),
                    status=item.get("status"),
                    timestamp=None,
                ),
            )
            if isinstance(result, dict):
                apply_results.append(result)
        for item in sorted(
            normalized_trades,
            key=lambda row: (row.get("matched_at") or "", row["external_trade_id"]),
        ):
            if item["external_trade_id"] in local_trade_ids:
                continue
            reconciliation_only = item.get("external_order_id") in unresolved_external_ids
            result = await self._ws_apply.apply_event(
                account_id=account_id,
                owner=owner,
                fencing_token=reconcile_input.fencing_token,
                event=UserTradeEvent(
                    event_type="trade",
                    trade_id=item["external_trade_id"],
                    order_id=item.get("external_order_id") or "",
                    token_id=item.get("token_id") or "",
                    side=item.get("side"),
                    price=item.get("price"),
                    size=item.get("size"),
                    fee=item.get("fee") or 0,
                    status=item.get("status"),
                    timestamp=_timestamp_ms(item.get("matched_at")),
                ),
            )
            if reconciliation_only:
                # PrivateExecutionLogic's UNKNOWN path is the reconciliation-only
                # projector: idempotent trade/position/ledger/reservation economics,
                # with order status and filled_size intentionally untouched.
                projected_status = (
                    (
                        "UNKNOWN"
                        if isinstance(result, dict)
                        and result.get("status") == "RECONCILE_REQUIRED"
                        else result.get("status")
                    ) if isinstance(result, dict)
                    else getattr(result, "order_status", None)
                )
                if projected_status != "UNKNOWN":
                    raise RuntimeError("reconcile_unknown_trade_projection_conflict")
            if isinstance(result, dict):
                apply_results.append({**result, "reconciliation_only": reconciliation_only})
            else:
                apply_results.append({
                    "ok": bool(getattr(result, "ok", True)),
                    "status": getattr(result, "order_status", None),
                    "replayed": bool(getattr(result, "replayed", False)),
                    "reconciliation_only": reconciliation_only,
                })

        page_evidence = {
            "orders": order_pages,
            "trades": trade_pages,
            "positions": position_pages,
            "funds": fund_pages,
            "unknown_trade_lookback": {
                "after": effective_trade_after,
                "derived_from_earliest_attempt": bool(unknown_local),
                "complete": True,
            },
        }
        async with UnitOfWork(self._sessions) as uow:
            result = await self._logic.complete_reconcile(
                uow,
                reconciliation_id=reconciliation_id,
                account_id=account_id,
                owner=owner,
                fencing_token=reconcile_input.fencing_token,
                remote_orders=normalized_orders,
                remote_trades=normalized_trades,
                remote_positions=normalized_positions,
                remote_funds=remote_funds,
                unknown_queries=unknown_queries,
                observation_manifest=page_evidence,
            )
        return {
            "status": result.status,
            "differences": result.differences,
            "reconciliation_id": reconciliation_id,
            "pages": len(order_pages),
            "page_evidence": page_evidence,
            "unknown_queries": unknown_queries,
            "apply_results": apply_results,
            "output_manifest_hash": result.output_manifest_hash,
        }

    async def _all_clob_orders(
        self, driver: Any, *, max_pages: int, auth_headers: Any
    ) -> tuple[list[Any], list[dict[str, Any]]]:
        loader = getattr(driver, "list_open_orders", None)
        if callable(loader):
            items = list(await loader())
            material = sorted(
                (_normalize_order(item) for item in items),
                key=lambda row: (
                    str(row.get("external_order_id") or ""), canonical_hash(row),
                ),
            )
            digest = canonical_hash({"kind": "orders", "items": material})
            return items, [{"page": 1, "cursor_in": None, "cursor_out": None,
                            "item_count": len(items), "page_hash": digest}]
        return await self._all_pages(
            loader=lambda cursor: driver.open_orders(
                cursor=cursor, limit=200, headers=auth_headers
            ),
            kind="orders", max_pages=max_pages,
        )

    async def _all_clob_trades(
        self, driver: Any, *, max_pages: int, auth_headers: Any, trade_after: str | None
    ) -> tuple[list[Any], list[dict[str, Any]]]:
        loader = getattr(driver, "list_trades", None)
        if callable(loader):
            items = list(await loader(after=trade_after))
            material = sorted(
                (_normalize_trade(item) for item in items),
                key=lambda row: (
                    str(row.get("external_trade_id") or ""), canonical_hash(row),
                ),
            )
            digest = canonical_hash({"kind": "trades", "items": material})
            return items, [{"page": 1, "cursor_in": None, "cursor_out": None,
                            "item_count": len(items), "page_hash": digest}]
        return await self._all_pages(
            loader=lambda cursor: driver.trades(
                cursor=cursor, limit=200, after=trade_after, headers=auth_headers
            ),
            kind="trades", max_pages=max_pages,
        )

    async def _all_pages(
        self, *, loader: Any, kind: str, max_pages: int
    ) -> tuple[list[Any], list[dict[str, Any]]]:
        cursor: str | None = None
        seen: set[str] = set()
        items: list[Any] = []
        evidence: list[dict[str, Any]] = []
        for page_no in range(1, max_pages + 1):
            result = await loader(cursor)
            page = result.typed
            page_items = list(page.items)
            raw = getattr(result, "raw", b"") or b""
            evidence.append({
                "page": page_no,
                "cursor_in": cursor,
                "cursor_out": page.next_cursor,
                "item_count": len(page_items),
                "page_hash": hashlib.sha256(raw).hexdigest(),
            })
            items.extend(page_items)
            next_cursor = page.next_cursor
            if not next_cursor:
                return items, evidence
            if next_cursor in seen or next_cursor == cursor:
                raise RuntimeError(f"reconcile_{kind}_cursor_cycle")
            seen.add(next_cursor)
            cursor = next_cursor
        raise RuntimeError(f"reconcile_{kind}_page_limit")

    async def _unknown_orders(
        self, uow: UnitOfWork, *, account_id: int
    ) -> list[dict[str, Any]]:
        rows = await uow.session.execute(
            text(
                "SELECT o.id AS local_order_id, o.external_order_id, "
                "o.size, o.filled_size, a.expected_order_hash, a.body_hash, a.timestamp "
                "FROM trading.exchange_orders o "
                "JOIN trading.exchange_order_attempts a ON a.id=o.attempt_id "
                "WHERE o.account_id=:account AND o.status='UNKNOWN' ORDER BY o.id"
            ),
            {"account": account_id},
        )
        return [dict(row) for row in rows.mappings().all()]

    async def _query_unknowns(
        self,
        unknown_local: list[dict[str, Any]],
        *,
        private_driver: Any | None,
        complete_orders: list[dict[str, Any]],
        complete_trades: list[dict[str, Any]],
        trade_history_complete: bool,
        trade_after: str | None,
    ) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        all_trades_by_order: dict[str, list[dict[str, Any]]] = {}
        complete_trades_by_order: dict[str, list[dict[str, Any]]] = {}
        for raw_item in complete_trades:
            item = _normalize_trade(raw_item)
            if item.get("external_order_id"):
                all_trades_by_order.setdefault(item["external_order_id"], []).append(item)
            if (
                item.get("external_order_id")
                and str(item.get("status") or "").upper() == "CONFIRMED"
            ):
                complete_trades_by_order.setdefault(item["external_order_id"], []).append(item)
        for local in unknown_local:
            external = local.get("external_order_id")
            # Once a provider id is bound it is the exact lookup key.  Until then the
            # deterministic expected order hash is the only stable provider identity.
            lookup_id = external or local["expected_order_hash"]
            if private_driver is None:
                raise RuntimeError("reconcile_private_driver_required")
            get_order = getattr(private_driver, "get_order", None)
            if not callable(get_order):
                raise RuntimeError("reconcile_exact_order_lookup_required")
            exact_result = await get_order(order_id=lookup_id)
            exact_item = getattr(exact_result, "typed", exact_result)
            matched_order = _normalize_order(exact_item) if exact_item is not None else None
            explicit_404 = exact_item is None
            if matched_order is not None:
                external = matched_order.get("external_order_id") or external

            trade_observations = all_trades_by_order.get(external or lookup_id, [])
            if not trade_observations and external and external != lookup_id:
                trade_observations = all_trades_by_order.get(lookup_id, [])
            trades = complete_trades_by_order.get(external or lookup_id, [])
            if not trades and external and external != lookup_id:
                trades = complete_trades_by_order.get(lookup_id, [])
            if trade_observations:
                external = external or trade_observations[0].get("external_order_id")

            observed_fill = sum(
                (Decimal(str(item.get("size") or 0)) for item in trades),
                Decimal("0"),
            )
            local_filled = Decimal(str(local.get("filled_size") or 0))
            provider_filled = Decimal(str(
                (matched_order or {}).get("size_matched") or 0
            ))
            # Only CONFIRMED trades are economic facts.  Provider status and
            # size_matched are supporting observations, not ledger authority.
            reconciled_filled_size = max(local_filled, observed_fill)
            order_size = Decimal(str(local.get("size") or 0))
            if reconciled_filled_size > order_size:
                raise RuntimeError("reconcile_unknown_fill_exceeds_order")

            normalized_status = _normalize_provider_order_status(
                (matched_order or {}).get("status")
            )
            terminal_statuses = {"FILLED", "CANCELLED", "REJECTED"}
            lookback_covers_attempt = _lookback_covers_attempt(
                trade_after=trade_after,
                attempt_timestamp=local.get("timestamp"),
            )
            if normalized_status == "FILLED" and observed_fill < order_size:
                # A FILLED label without the complete confirmed economic facts cannot
                # finalize capital, position or ledger projections.
                resolution = None
                proof = "filled_status_missing_confirmed_trade_facts"
            elif normalized_status in terminal_statuses:
                resolution = normalized_status
                proof = "exact_order_terminal"
            elif order_size > 0 and observed_fill >= order_size:
                resolution = "FILLED"
                reconciled_filled_size = order_size
                proof = "complete_trade_history"
            elif observed_fill > 0 or normalized_status == "PARTIAL":
                resolution = "PARTIAL"
                proof = "exact_order_and_trade_history"
            elif normalized_status in {"ACK", "OPEN"}:
                resolution = "OPEN"
                proof = "exact_order_open"
            elif (
                explicit_404
                and not trade_observations
                and trade_history_complete
                and lookback_covers_attempt
            ):
                # This is the sole absence proof: exact 404 plus a fully consumed CLOB
                # trades window that reaches back through the persisted send timestamp.
                resolution = "NOT_FOUND_TERMINAL"
                proof = "exact_404_complete_trade_lookback"
            else:
                resolution = None
                proof = (
                    "exact_404_trade_observed_not_terminal"
                    if explicit_404 and trade_observations
                    else (
                        "exact_404_insufficient_trade_lookback"
                        if explicit_404 else "not_observed_not_terminal_proof"
                    )
                )
            records.append({
                **local,
                "external_order_id": external,
                "lookup_id": lookup_id,
                "complete": True,
                "resolution": resolution,
                "proof": proof,
                "filled_size": str(reconciled_filled_size),
                "trade_history_complete": trade_history_complete,
                "trade_after": trade_after,
                "lookback_covers_attempt": lookback_covers_attempt,
                "provider_size_matched": str(provider_filled),
                "trade_observation_count": len(trade_observations),
                "confirmed_trade_count": len(trades),
            })
        return {"unknown_orders": records}

    async def _bind_unknown_provider_ids(
        self,
        *,
        account_id: int,
        owner: str,
        fencing_token: int,
        unknown_queries: dict[str, Any],
    ) -> None:
        async with UnitOfWork(self._sessions) as uow:
            fence = await self._logic._execution.get_active_lease_fence(
                uow.session,
                account_id=account_id,
                lease_role="EXECUTION",
                owner=owner,
                fencing_token=fencing_token,
                for_update=True,
            )
            if fence is None:
                raise RuntimeError("stale_fence_rejected")
            for proof in unknown_queries.get("unknown_orders", []):
                external = proof.get("external_order_id")
                if not external:
                    continue
                order = await self._logic._execution.get_order(
                    uow.session,
                    order_id=int(proof["local_order_id"]),
                    for_update=True,
                )
                if order is None or order["account_id"] != account_id:
                    raise RuntimeError("reconcile_unknown_order_missing")
                if order["status"] == "UNKNOWN" and not order.get("external_order_id"):
                    if not await self._logic._execution.advance_order(
                        uow.session,
                        order_id=order["id"],
                        new_status="UNKNOWN",
                        external_order_id=external,
                        expected_status="UNKNOWN",
                    ):
                        raise RuntimeError("reconcile_unknown_bind_conflict")


def _normalize_order(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        value = dict(item)
    else:
        value = {
            "external_order_id": getattr(item, "order_id", None),
            "token_id": getattr(item, "token_id", None),
            "side": getattr(item, "side", None),
            "price": getattr(item, "price", None),
            "size": getattr(item, "size", None),
            "original_size": getattr(item, "original_size", None),
            "size_matched": getattr(item, "size_matched", None),
            "status": (
                getattr(item, "status", None)
                or (getattr(item, "raw_extra", {}) or {}).get("status")
            ),
        }
    if "external_order_id" not in value:
        value["external_order_id"] = value.get("order_id")
    for field in ("price", "size", "original_size", "size_matched"):
        if value.get(field) is not None:
            value[field] = str(value[field])
    return value


def _normalize_trade(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        value = dict(item)
    else:
        value = {
            "external_trade_id": getattr(item, "trade_id", None),
            "external_order_id": getattr(item, "order_id", None),
            "token_id": getattr(item, "token_id", None),
            "side": getattr(item, "side", None),
            "price": getattr(item, "price", None),
            "size": getattr(item, "size", None),
            "fee": getattr(item, "fee", None),
            "status": getattr(item, "status", None),
            "matched_at": getattr(item, "matched_at", None),
        }
    if "external_trade_id" not in value:
        value["external_trade_id"] = value.get("trade_id")
    if "external_order_id" not in value:
        value["external_order_id"] = value.get("order_id")
    for field in ("price", "size", "fee"):
        if value.get(field) is not None:
            value[field] = str(value[field])
    if value.get("matched_at") is not None:
        value["matched_at"] = str(value["matched_at"])
    if value.get("status") is not None:
        value["status"] = str(value["status"]).upper()
    return value


def _normalize_position(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        value = dict(item)
    else:
        value = {
            "token_id": getattr(item, "token_id", None),
            "size": getattr(item, "size", None),
            "avg_price": getattr(item, "avg_price", None),
        }
    for field in ("size", "avg_price"):
        if value.get(field) is not None:
            value[field] = str(value[field])
    return value


def _normalize_funds(value: Any) -> list[dict[str, Any]]:
    typed = getattr(value, "typed", value)
    if hasattr(typed, "items") and not isinstance(typed, (list, tuple, dict)):
        typed = typed.items
    if isinstance(typed, dict):
        typed = typed.get("data", typed.get("items", [typed]))
    rows = list(typed or [])
    normalized = []
    for item in rows:
        row = dict(item) if isinstance(item, dict) else {
            "asset_key": getattr(item, "asset_key", None),
            "confirmed": getattr(item, "confirmed", None),
            "provider_reserved": getattr(item, "provider_reserved", None),
            "local_reserved": getattr(item, "local_reserved", None),
        }
        for field in ("confirmed", "provider_reserved", "local_reserved"):
            row[field] = str(row.get(field) or 0)
        normalized.append(row)
    return normalized


def _timestamp_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text_value = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text_value)
        except ValueError:
            number = float(value)
            return int(number if number > 100_000_000_000 else number * 1000)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _normalize_provider_order_status(value: Any) -> str | None:
    status = str(value or "").strip().lower()
    if status in {"live", "open", "ack", "matched"}:
        return "ACK"
    if status in {"partial", "partially_filled"}:
        return "PARTIAL"
    if status in {"filled", "complete"}:
        return "FILLED"
    if status in {"cancelled", "canceled"}:
        return "CANCELLED"
    if status in {"rejected", "failed"}:
        return "REJECTED"
    return None


def _as_unix_seconds(value: Any) -> Decimal | None:
    """Normalize numeric/ISO timestamps without losing integer-second ordering."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return Decimal(str(dt.timestamp()))
    try:
        numeric = Decimal(str(value))
    except Exception:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("reconcile_trade_after_invalid") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return Decimal(str(dt.timestamp()))
    if numeric > Decimal("100000000000"):
        numeric /= Decimal("1000")
    return numeric


def _bounded_trade_after(
    *, requested: str | None, unknown_orders: list[dict[str, Any]],
) -> str | None:
    """Clamp the caller lookback to the earliest persisted UNKNOWN send timestamp."""
    attempts = [
        stamp for item in unknown_orders
        if (stamp := _as_unix_seconds(item.get("timestamp"))) is not None and stamp >= 0
    ]
    if not attempts:
        return requested
    earliest = min(attempts)
    derived = str(int(earliest))
    if requested is None:
        return derived
    requested_seconds = _as_unix_seconds(requested)
    if requested_seconds is None:
        return derived
    return requested if requested_seconds <= earliest else derived


def _lookback_covers_attempt(*, trade_after: str | None, attempt_timestamp: Any) -> bool:
    attempt = _as_unix_seconds(attempt_timestamp)
    if attempt is None or attempt < 0:
        return False
    after = _as_unix_seconds(trade_after)
    return after is None or after <= attempt
