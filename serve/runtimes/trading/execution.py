"""Shadow execution runtime（WP-03 Checkpoint D；WP-05 Checkpoint C private 追加）。

只注册 DB-backed decision/shadow handlers；不 import 私有 CLOB SDK、vault、wallet、签名、
Data API 或真实下单 Driver。每个 Handler 只做一次 UoW；外部/长计算不持有 DB transaction。

WP-05 Checkpoint C 追加 ``PrivateExecutionRuntime``：authorization envelope / fake
submit / cancel / heartbeat / reconcile 编排。Driver 由调用方注入（fake-only）；prepare 与
apply 之间必须经过 Driver（网络/单次发送），绝不在事务内调网络。
"""

from __future__ import annotations

import logging
from typing import Any

from app.db.uow import UnitOfWork
from app.handlers.trading.decision import DecisionEvent, DecisionHandler
from app.handlers.trading.execution import ExecutionEvent, ExecutionHandler
from app.logics.trading.decision import DecisionLogic
from app.logics.trading.execution import (
    PrivateExecutionLogic,
    ShadowExecutionLogic,
)
from app.logics.trading.reconciliation import ReconciliationLogic
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.decision import DecisionRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.outbox.repository import OutboxRepository
from app.services.polymarket.clob_trading_driver import (
    canonical_order_body_hash,
    expected_order_hash_for,
    sdk_manifest_hash_for,
)

logger = logging.getLogger(__name__)


class ShadowExecutionRuntime:
    """编排 decision → G7A/G7B → terminal → intent → shadow fill（每步一次 UoW）。"""

    def __init__(self, sessions_factory: Any) -> None:
        self._sessions = sessions_factory
        self._decision_logic = DecisionLogic(DecisionRepository(), WorkflowRepository())
        self._decision_handler = DecisionHandler(self._decision_logic)
        self._execution_logic = ShadowExecutionLogic(
            ExecutionRepository(), LedgerRepository(), OutboxRepository()
        )
        self._execution_handler = ExecutionHandler(self._execution_logic)

    async def handle_decision_event(
        self,
        event: DecisionEvent,
        *,
        policy_hash: str | None = None,
        version_manifest_id: int | None = None,
    ) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._decision_handler.handle(
                uow, event, policy_hash=policy_hash,
                version_manifest_id=version_manifest_id,
            )

    async def handle_execution_event(
        self,
        event: ExecutionEvent,
        *,
        portfolio_namespace: str | None = None,
        cash_asset_key: str | None = None,
    ) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._execution_handler.handle(
                uow, event, portfolio_namespace=portfolio_namespace,
                cash_asset_key=cash_asset_key,
            )


class PrivateExecutionRuntime:
    """Fenced private execution orchestration; provider I/O never holds a DB TX."""

    def __init__(
        self,
        sessions_factory: Any,
        audit: AuditRepository | None = None,
        *,
        chain_id: int = 137,
        exchange_address: str | None = None,
        heartbeat_failure_handler: Any | None = None,
    ) -> None:
        self._sessions = sessions_factory
        self._audit = audit or AuditRepository()
        self._chain_id = int(chain_id)
        self._exchange_address = exchange_address
        self._heartbeat_failure_handler = heartbeat_failure_handler
        self._private_logic = PrivateExecutionLogic(
            ExecutionRepository(), LedgerRepository(), self._audit,
        )
        self._reconcile_logic = ReconciliationLogic(
            ExecutionRepository(), LedgerRepository(), self._audit,
        )
        self._handler = ExecutionHandler(
            ShadowExecutionLogic(ExecutionRepository(), LedgerRepository(), OutboxRepository()),
            private_logic=self._private_logic,
            reconcile_logic=self._reconcile_logic,
        )

    async def create_envelope(self, event: ExecutionEvent, *, owner: str) -> Any:
        payload = dict(event.payload or {})
        payload["owner"] = owner
        async with UnitOfWork(self._sessions) as uow:
            return await self._handler.handle(
                uow, ExecutionEvent(kind=event.kind, payload=payload)
            )

    async def submit_order(
        self,
        *,
        submit_input: Any,
        owner: str,
        driver: Any,
    ) -> Any:
        """preflight -> sign -> durable prepare -> one write -> fenced apply."""
        # Frozen domain identity is a pre-decryption/pre-signing boundary.  Never
        # invoke the signer when the chain/verifying contract is absent, malformed,
        # or conflicts with a driver-declared capability.
        if (
            self._chain_id != 137
            or not isinstance(self._exchange_address, str)
            or len(self._exchange_address) != 42
            or not self._exchange_address.startswith("0x")
            or any(char not in "0123456789abcdefABCDEF" for char in self._exchange_address[2:])
        ):
            raise RuntimeError("submit_frozen_exchange_identity_missing")
        declared_chain = getattr(driver, "chain_id", None)
        if declared_chain is not None and int(declared_chain) != self._chain_id:
            raise RuntimeError("submit_driver_chain_identity_mismatch")
        declared_exchange = getattr(driver, "exchange_address", None)
        if declared_exchange is not None and str(declared_exchange).lower() != self._exchange_address.lower():
            raise RuntimeError("submit_driver_exchange_identity_mismatch")
        assert_identity = getattr(driver, "assert_execution_identity", None)
        if not callable(assert_identity):
            raise RuntimeError("submit_driver_execution_identity_required")
        # Signature boundary: validate the exact active owner/token and all DB facts first.
        async with UnitOfWork(self._sessions) as uow:
            material = await self._private_logic.preflight_submit(
                uow, input_=submit_input, owner=owner,
            )
            if int(material["account"]["chain_id"]) != self._chain_id:
                raise RuntimeError("submit_account_chain_identity_mismatch")
            expected_exchange_address = material["expected_exchange_address"]
            if self._exchange_address.lower() != expected_exchange_address.lower():
                raise RuntimeError("submit_market_exchange_identity_mismatch")
            assert_identity(
                token_id=submit_input.token_id,
                chain_id=self._chain_id,
                exchange_address=expected_exchange_address,
            )
        signed_order = await driver.create_signed_order(
            token_id=submit_input.token_id,
            price=submit_input.price,
            size=submit_input.size,
            side=submit_input.side,
            post_only=submit_input.post_only,
        )
        body_hash = canonical_order_body_hash(signed_order)
        expected_order_hash = expected_order_hash_for(
            signed_order,
            chain_id=self._chain_id,
            exchange_address=self._exchange_address,
        )
        sdk_hash = sdk_manifest_hash_for(signed_order)
        # Same authoritative preflight is repeated while persisting the exact send material.
        async with UnitOfWork(self._sessions) as uow:
            prepared = await self._private_logic.prepare_submit(
                uow,
                input_=submit_input,
                owner=owner,
                signed_order=signed_order,
                body_hash=body_hash,
                expected_order_hash=expected_order_hash,
                sdk_manifest_hash=sdk_hash,
            )
        # Network boundary: a takeover/reconciliation between prepare and send is a hard stop.
        async with UnitOfWork(self._sessions) as uow:
            await self._private_logic.assert_active_fence(
                uow,
                account_id=prepared.account_id,
                owner=owner,
                fencing_token=prepared.fencing_token,
            )
            if prepared.exposure_increasing and await self._private_logic._execution.has_active_reconciliation(
                uow.session, account_id=prepared.account_id
            ):
                raise RuntimeError("submit_blocked_active_reconciliation")
        try:
            outcome = await driver.submit_order(signed_order)
            error_reason = getattr(outcome, "error_code", None)
        except Exception as exc:  # the write may have reached the provider: never retry
            outcome = _RuntimeSubmitUnknown()
            error_reason = getattr(exc, "reason_code", None) or type(exc).__name__
        async with UnitOfWork(self._sessions) as uow:
            return await self._private_logic.apply_submit_outcome(
                uow,
                prepared=prepared,
                outcome=outcome,
                response_hash=body_hash,
                http_status=getattr(outcome, "http_status", None),
                error_reason=error_reason,
            )

    async def recover_submitted(
        self,
        *,
        account_id: int,
        owner: str,
        fencing_token: int,
        reconcile_callback: Any | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Startup scanner: SUBMITTED -> UNKNOWN and reconcile, with zero resend path."""
        async with UnitOfWork(self._sessions) as uow:
            recovered = await self._private_logic.recover_submitted_attempts(
                uow,
                account_id=account_id,
                owner=owner,
                fencing_token=fencing_token,
                limit=limit,
            )
        if recovered and reconcile_callback is not None:
            value = reconcile_callback(
                account_id=account_id,
                owner=owner,
                fencing_token=fencing_token,
                reason="persisted_submitted_recovery",
                recovered=recovered,
            )
            if hasattr(value, "__await__"):
                await value
        return recovered

    async def cancel_order(
        self, *, cancel_input: Any, owner: str, driver: Any
    ) -> Any:
        # Fenced precommit establishes the item being cancelled before network I/O.
        async with UnitOfWork(self._sessions) as uow:
            prepared = await self._private_logic.prepare_cancel(
                uow, input_=cancel_input, owner=owner,
            )
        from app.logics.trading.execution import SubmitApplyResult
        if isinstance(prepared, SubmitApplyResult):
            return prepared
        async with UnitOfWork(self._sessions) as uow:
            await self._private_logic.assert_active_fence(
                uow,
                account_id=prepared.account_id,
                owner=owner,
                fencing_token=prepared.fencing_token,
            )
        try:
            outcome = await driver.cancel_orders([prepared.external_order_id])
            error_reason = None
        except Exception as exc:
            outcome = None
            error_reason = getattr(exc, "reason_code", None) or type(exc).__name__
        async with UnitOfWork(self._sessions) as uow:
            return await self._private_logic.apply_cancel_outcome(
                uow,
                prepared=prepared,
                outcome=outcome,
                response_hash=None,
                error_reason=error_reason,
            )

    async def apply_fill(self, event: ExecutionEvent, *, owner: str) -> Any:
        payload = dict(event.payload or {})
        payload["owner"] = owner
        async with UnitOfWork(self._sessions) as uow:
            return await self._handler.handle(
                uow, ExecutionEvent(kind=event.kind, payload=payload)
            )

    async def heartbeat_once(
        self,
        *,
        account_id: int,
        owner: str,
        fencing_token: int,
        driver: Any,
        on_failure: Any | None = None,
    ) -> dict[str, Any]:
        """Send exactly the persisted heartbeat ID under an active HEARTBEAT fence."""
        import hashlib
        from sqlalchemy import text as sql_text

        failure_handler = on_failure or self._heartbeat_failure_handler
        if failure_handler is None:
            raise RuntimeError("heartbeat_failure_handler_required")
        async with UnitOfWork(self._sessions) as uow:
            lease = await self._private_logic.assert_active_fence(
                uow,
                account_id=account_id,
                owner=owner,
                fencing_token=fencing_token,
                lease_role="HEARTBEAT",
            )
            current_id = lease.get("latest_heartbeat_id") or ""
        try:
            outcome = await driver.send_heartbeat(current_id)
        except Exception as exc:
            outcome = {
                "ok": False,
                "heartbeat_id": None,
                "reason": getattr(exc, "reason_code", None) or type(exc).__name__,
            }
        async with UnitOfWork(self._sessions) as uow:
            await self._private_logic.assert_active_fence(
                uow,
                account_id=account_id,
                owner=owner,
                fencing_token=fencing_token,
                lease_role="HEARTBEAT",
            )
            if not outcome.get("ok") or not outcome.get("heartbeat_id"):
                result = {
                    "status": "HEARTBEAT_FAILED",
                    "ok": False,
                    "outcome": outcome,
                    "fencing_token": fencing_token,
                }
            else:
                next_id = str(outcome["heartbeat_id"])
                updated = await uow.session.execute(
                    sql_text(
                        "UPDATE trading.execution_leases SET latest_heartbeat_id=:h, "
                        "latest_heartbeat_hash=:hh, version=version+1, updated_at=now() "
                        "WHERE account_id=:a AND lease_role='HEARTBEAT' AND owner=:o "
                        "AND fencing_token=:f AND lease_until > statement_timestamp()"
                    ),
                    {
                        "h": next_id,
                        "hh": hashlib.sha256(next_id.encode()).hexdigest(),
                        "a": account_id,
                        "o": owner,
                        "f": fencing_token,
                    },
                )
                if updated.rowcount != 1:
                    raise RuntimeError("heartbeat_fence_update_conflict")
                result = {
                    "status": "HEARTBEAT_OK",
                    "ok": True,
                    "heartbeat_id": next_id,
                    "fencing_token": fencing_token,
                }
        if not result["ok"]:
            await self._handle_heartbeat_failure(
                account_id=account_id,
                owner=owner,
                fencing_token=fencing_token,
                reason="heartbeat_failed",
                failure_handler=failure_handler,
            )
        return result

    async def _handle_heartbeat_failure(
        self,
        *,
        account_id: int,
        owner: str,
        fencing_token: int,
        reason: str,
        failure_handler: Any,
    ) -> None:
        """Durably hard-stop first, then trigger cancel/reconcile exactly once."""
        if failure_handler is None:
            raise RuntimeError("heartbeat_failure_handler_required")
        async with UnitOfWork(self._sessions) as uow:
            await self._private_logic.assert_active_fence(
                uow,
                account_id=account_id,
                owner=owner,
                fencing_token=fencing_token,
                lease_role="HEARTBEAT",
            )
            await self._private_logic._alert(
                uow,
                severity="CRITICAL",
                code="heartbeat_hard_stop",
                message_redacted=(
                    "heartbeat failed; stop execution and run cancel/reconcile plan"
                ),
                alert_key=f"alert:heartbeat:{account_id}:{fencing_token}:{reason}",
            )
            await self._private_logic._workflow_event(
                uow,
                event_key=f"wf:heartbeat:{account_id}:{fencing_token}:{reason}",
                event_type="heartbeat.hard_stop",
                aggregate_type="account",
                aggregate_id=str(account_id),
                payload={
                    "reason": reason,
                    "fencing_token": fencing_token,
                    "plan": ["stop_new_orders", "cancel_open_orders", "reconcile"],
                },
            )
        await _await_callback(
            failure_handler,
            account_id=account_id,
            owner=owner,
            fencing_token=fencing_token,
            reason=reason,
        )

    async def heartbeat(
        self,
        *,
        account_id: int,
        owner: str,
        ttl_s: float,
        driver: Any,
        last_heartbeat_id: str | None = None,
        on_failure: Any | None = None,
    ) -> Any:
        """Acquire the heartbeat leader once; caller values never override its ID chain."""
        del last_heartbeat_id
        if on_failure is None and self._heartbeat_failure_handler is None:
            raise RuntimeError("heartbeat_failure_handler_required")
        from app.logics.trading.execution import ExecutionLeaseLogic
        async with UnitOfWork(self._sessions) as uow:
            lease = await ExecutionLeaseLogic(
                self._private_logic._execution
            ).acquire_lease(
                uow,
                account_id=account_id,
                lease_role="HEARTBEAT",
                owner=owner,
                ttl_s=ttl_s,
            )
        return await self.heartbeat_once(
            account_id=account_id,
            owner=owner,
            fencing_token=lease["fencing_token"],
            driver=driver,
            on_failure=on_failure,
        )

    async def run_heartbeat_loop(
        self,
        *,
        account_id: int,
        owner: str,
        fencing_token: int,
        driver: Any,
        stop_event: Any,
        on_failure: Any,
        interval_s: float = 5.0,
        max_drift_s: float = 0.5,
        monotonic: Any | None = None,
        sleep: Any | None = None,
    ) -> dict[str, Any]:
        """Monotonic 5s heartbeat scheduler; the first failure stops and fences work."""
        import asyncio
        import time

        clock = monotonic or time.monotonic
        sleeper = sleep or asyncio.sleep
        failure_handler = on_failure or self._heartbeat_failure_handler
        if failure_handler is None:
            raise RuntimeError("heartbeat_failure_handler_required")
        next_due = clock()
        ticks = 0
        max_drift = 0.0
        while not stop_event.is_set():
            now = clock()
            wait = next_due - now
            if wait > 0:
                await sleeper(wait)
            actual = clock()
            drift = max(0.0, actual - next_due)
            max_drift = max(max_drift, drift)
            if drift > max_drift_s:
                await self._handle_heartbeat_failure(
                    account_id=account_id,
                    owner=owner,
                    fencing_token=fencing_token,
                    reason="heartbeat_scheduler_drift",
                    failure_handler=failure_handler,
                )
                return {"ok": False, "status": "HEARTBEAT_DRIFT", "max_drift_s": max_drift}
            result = await self.heartbeat_once(
                account_id=account_id,
                owner=owner,
                fencing_token=fencing_token,
                driver=driver,
                on_failure=failure_handler,
            )
            ticks += 1
            if not result["ok"]:
                return {**result, "ticks": ticks, "max_drift_s": max_drift}
            next_due += interval_s
        return {"ok": True, "status": "STOPPED", "ticks": ticks, "max_drift_s": max_drift}


class UserWsExecutionRuntime:
    """Apply User WS frames under EXECUTION fencing; any gap/disconnect reconciles."""

    def __init__(self, sessions_factory: Any, audit: AuditRepository | None = None) -> None:
        self._sessions = sessions_factory
        self._logic = PrivateExecutionLogic(
            ExecutionRepository(), LedgerRepository(), audit or AuditRepository(),
        )

    async def apply_event(
        self,
        *,
        account_id: int,
        owner: str,
        fencing_token: int,
        event: Any,
    ) -> Any:
        from datetime import datetime, timezone
        from app.schemas.polymarket.user_ws import UserOrderEvent, UserTradeEvent

        frame = getattr(event, "frame", event)
        async with UnitOfWork(self._sessions) as uow:
            await self._logic.assert_active_fence(
                uow,
                account_id=account_id,
                owner=owner,
                fencing_token=fencing_token,
            )
            if isinstance(frame, UserTradeEvent):
                order = await self._logic._execution.get_order_by_external(
                    uow.session,
                    account_id=account_id,
                    external_order_id=frame.order_id,
                    for_update=True,
                )
                if order is None:
                    return {"ok": False, "status": "RECONCILE_REQUIRED", "reason": "ws_trade_order_missing"}
                lineage = (
                    await uow.session.execute(
                        __import__("sqlalchemy").text(
                            "SELECT a.envelope_id, e.intent_id FROM trading.exchange_order_attempts a "
                            "JOIN trading.execution_authorization_envelopes e ON e.id=a.envelope_id "
                            "WHERE a.id=:attempt"
                        ),
                        {"attempt": order["attempt_id"]},
                    )
                ).mappings().one()
                trade_time = _user_ws_timestamp(frame.timestamp)
                result = await self._logic.apply_fill(
                    uow,
                    order_id=order["id"],
                    account_id=account_id,
                    envelope_id=lineage["envelope_id"],
                    intent_id=lineage["intent_id"],
                    owner=owner,
                    fencing_token=fencing_token,
                    external_trade_id=frame.trade_id,
                    side=frame.side or order["side"],
                    price=frame.price,
                    size=frame.size,
                    fee=frame.fee or 0,
                    trade_time=trade_time,
                    trade_status=frame.status,
                )
                if not result.ok:
                    return {
                        "ok": False,
                        "status": "RECONCILE_REQUIRED",
                        "reason": result.reason or "ws_trade_not_confirmed",
                    }
                return result
            if isinstance(frame, UserOrderEvent):
                order = await self._logic._execution.get_order_by_external(
                    uow.session,
                    account_id=account_id,
                    external_order_id=frame.order_id,
                    for_update=True,
                )
                if order is None:
                    return {"ok": False, "status": "RECONCILE_REQUIRED", "reason": "ws_order_missing"}
                if frame.token_id and frame.token_id != order["token_id"]:
                    raise RuntimeError("ws_order_token_mismatch")
                normalized = _normalize_ws_order_status(frame.status)
                if normalized is None or normalized == order["status"]:
                    return {"ok": True, "status": order["status"], "replayed": True}
                # Terminal FILLED facts are applied from trade frames/REST trades so the
                # economic effect cannot be skipped. UNKNOWN resolves only via reconcile.
                if order["status"] == "UNKNOWN" or normalized == "FILLED":
                    return {"ok": False, "status": "RECONCILE_REQUIRED", "reason": "ws_terminal_needs_rest"}
                if not await self._logic._execution.advance_order(
                    uow.session,
                    order_id=order["id"],
                    new_status=normalized,
                    expected_status=order["status"],
                ):
                    raise RuntimeError("ws_order_transition_conflict")
                await self._logic._execution.insert_order_state_event(
                    uow.session,
                    event_key=f"ev:{order['id']}:ws:{normalized.lower()}:{getattr(event, 'receive_seq', 0)}",
                    order_id=order["id"],
                    event_type=normalized,
                    transition_from=order["status"],
                    transition_to=normalized,
                    event_payload={"artifact_hash": getattr(event, "artifact_hash", None)},
                    event_hash=self._logic._canonical({
                        "order_id": order["id"],
                        "status": normalized,
                        "receive_seq": getattr(event, "receive_seq", 0),
                    }),
                    fence_token=fencing_token,
                )
                return {"ok": True, "status": normalized}
            return {"ok": True, "status": "IGNORED"}

    async def run(
        self,
        *,
        account_id: int,
        owner: str,
        fencing_token: int,
        driver: Any,
        credentials: Any,
        reconcile_callback: Any,
        stop_event: Any,
    ) -> dict[str, Any]:
        async with UnitOfWork(self._sessions) as uow:
            await self._logic.assert_active_fence(
                uow,
                account_id=account_id,
                owner=owner,
                fencing_token=fencing_token,
            )
        await driver.connect(credentials)
        previous_seq = 0
        applied = 0
        try:
            while not stop_event.is_set():
                message = await driver.next_frame()
                if previous_seq and message.receive_seq != previous_seq + 1:
                    await _await_callback(
                        reconcile_callback,
                        account_id=account_id,
                        owner=owner,
                        fencing_token=fencing_token,
                        reason="user_ws_sequence_gap",
                        ws_watermark=previous_seq,
                    )
                    return {"ok": False, "status": "RECONCILING", "watermark": previous_seq}
                result = await self.apply_event(
                    account_id=account_id,
                    owner=owner,
                    fencing_token=fencing_token,
                    event=message,
                )
                previous_seq = message.receive_seq
                applied += 1
                result_ok = (
                    result.get("ok", True)
                    if isinstance(result, dict)
                    else getattr(result, "ok", True)
                )
                if not result_ok:
                    failure_reason = (
                        result.get("reason")
                        if isinstance(result, dict)
                        else getattr(result, "reason", None)
                    )
                    await _await_callback(
                        reconcile_callback,
                        account_id=account_id,
                        owner=owner,
                        fencing_token=fencing_token,
                        reason=failure_reason or "user_ws_apply_gap",
                        ws_watermark=previous_seq,
                    )
                    return {"ok": False, "status": "RECONCILING", "watermark": previous_seq}
        except Exception:
            await _await_callback(
                reconcile_callback,
                account_id=account_id,
                owner=owner,
                fencing_token=fencing_token,
                reason="user_ws_disconnect",
                ws_watermark=previous_seq,
            )
            return {"ok": False, "status": "RECONCILING", "watermark": previous_seq}
        finally:
            await driver.aclose()
        return {"ok": True, "status": "STOPPED", "applied": applied, "watermark": previous_seq}


def build_execution_vault_service(
    sessions_factory: Any,
    keyring: Any,
    *,
    runtime_identity: str,
    env: str = "dev",
) -> Any:
    """Production VaultService construction with an independent durable failure sink."""
    from app.domain.trading.hashing import canonical_hash
    from app.repositories.trading.vault import VaultRepository
    from app.services.vault import VaultService

    vault_repo = VaultRepository()
    audit_repo = AuditRepository()

    async def failure_audit(event: Any) -> None:
        material = {
            "operation": str(event.get("operation", "unknown")),
            "entry_id": int(event.get("entry_id", 0) or 0),
            "secret_version_id": event.get("secret_version_id"),
            "identity": str(event.get("identity", "unknown")),
            "purpose": str(event.get("purpose", "unknown")),
            "key_version": event.get("key_version"),
            "result": str(event.get("result", "FAILED")),
            "reason": str(event.get("reason", "unknown")),
        }
        async with UnitOfWork(sessions_factory) as audit_uow:
            entry = None
            if material["entry_id"] > 0:
                entry = await vault_repo.get_entry(
                    audit_uow.session, entry_id=material["entry_id"]
                )
            if entry is not None:
                await vault_repo.insert_access_event(
                    audit_uow.session,
                    entry_id=material["entry_id"],
                    secret_version_id=material["secret_version_id"],
                    subject=f"vault.{material['operation']}",
                    identity=material["identity"],
                    purpose=material["purpose"],
                    key_version=material["key_version"],
                    result=material["result"],
                    result_reason=material["reason"],
                )
            digest = canonical_hash(material)
            await audit_repo.insert_alert_event(
                audit_uow.session,
                alert_key=f"vault-failure:{digest}",
                severity="ERROR",
                code="vault_failure_audit",
                message_redacted=(
                    f"vault.{material['operation']} {material['result']} "
                    f"reason={material['reason']} entry={material['entry_id']}"
                ),
            )

    return VaultService(
        vault_repo,
        keyring,
        env=env,
        runtime_identity=runtime_identity,
        failure_audit=failure_audit,
    )


class _RuntimeSubmitUnknown:
    cls = "UNKNOWN"
    order_id = None
    http_status = None
    error_code = "wire_result_indeterminate"


async def _await_callback(callback: Any, **kwargs: Any) -> Any:
    value = callback(**kwargs)
    if hasattr(value, "__await__"):
        return await value
    return value


def _normalize_ws_order_status(status: str | None) -> str | None:
    value = str(status or "").strip().lower()
    if value in {"live", "open", "ack", "matched"}:
        return "ACK"
    if value in {"partial", "partially_filled"}:
        return "PARTIAL"
    if value in {"filled", "complete"}:
        return "FILLED"
    if value in {"cancelled", "canceled"}:
        return "CANCELLED"
    if value in {"rejected", "failed"}:
        return "REJECTED"
    return None


def _user_ws_timestamp(value: int | None) -> Any:
    from datetime import datetime, timezone
    import math

    if value is None:
        return datetime.now(timezone.utc)
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise RuntimeError("user_ws_timestamp_invalid")
    if seconds > 100_000_000_000:
        seconds /= 1000.0
    result = datetime.fromtimestamp(seconds, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    if result.year < 2020 or result.timestamp() > now.timestamp() + 86_400:
        raise RuntimeError("user_ws_timestamp_out_of_range")
    return result
