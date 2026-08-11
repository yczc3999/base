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
    """WP-05 Checkpoint C：envelope / fake submit / cancel / heartbeat / reconcile 编排。

    Driver 由调用方注入（fake-only）；prepare→driver→apply 三阶段，网络绝不在事务内。
    """

    def __init__(self, sessions_factory: Any, audit: AuditRepository | None = None) -> None:
        self._sessions = sessions_factory
        self._audit = audit or AuditRepository()
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

    async def create_envelope(self, event: ExecutionEvent) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._handler.handle(uow, event)

    async def submit_order(
        self,
        *,
        submit_input: Any,
        driver: Any,
        chain_id: int = 137,
        exchange_address: str = "",
    ) -> Any:
        """prepare（UoW1）→ driver 单次发送 → apply（UoW2）。"""
        signed_order = await driver.create_signed_order(
            token_id=submit_input.token_id,
            price=submit_input.price,
            size=submit_input.size,
            side=submit_input.side,
            post_only=submit_input.post_only,
        )
        body_hash = canonical_order_body_hash(signed_order)
        expected_order_hash = expected_order_hash_for(
            signed_order, chain_id=chain_id, exchange_address=exchange_address,
        )
        sdk_hash = sdk_manifest_hash_for(signed_order)
        async with UnitOfWork(self._sessions) as uow:
            prepared = await self._private_logic.prepare_submit(
                uow, input_=submit_input, signed_order=signed_order,
                body_hash=body_hash, expected_order_hash=expected_order_hash,
                sdk_manifest_hash=sdk_hash,
            )
        outcome = await driver.submit_order(signed_order)
        async with UnitOfWork(self._sessions) as uow:
            return await self._private_logic.apply_submit_outcome(
                uow, prepared=prepared, outcome=outcome,
                response_hash=body_hash, http_status=outcome.http_status,
                error_reason=outcome.error_code,
            )

    async def cancel_order(self, *, cancel_input: Any, driver: Any) -> Any:
        outcome = await driver.cancel_orders([cancel_input.external_order_id])
        async with UnitOfWork(self._sessions) as uow:
            return await self._private_logic.cancel_order(
                uow, input_=cancel_input, outcome=outcome,
                response_hash=None, error_reason=None,
            )

    async def apply_fill(self, event: ExecutionEvent) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._handler.handle(uow, event)

    async def heartbeat(
        self, *, account_id: int, owner: str, ttl_s: float, driver: Any,
        last_heartbeat_id: str | None = None,
    ) -> Any:
        import hashlib

        from sqlalchemy import text as sql_text

        from app.logics.trading.execution import ExecutionLeaseLogic

        lease_logic = ExecutionLeaseLogic(self._private_logic._execution)
        async with UnitOfWork(self._sessions) as uow:
            lease = await lease_logic.acquire_lease(
                uow, account_id=account_id, lease_role="HEARTBEAT",
                owner=owner, ttl_s=ttl_s,
            )
            fencing_token = lease["fencing_token"]
            current_id = last_heartbeat_id or lease.get("latest_heartbeat_id") or ""
        outcome = await driver.send_heartbeat(current_id)
        async with UnitOfWork(self._sessions) as uow:
            lease = await self._private_logic._execution.get_lease(
                uow.session, account_id=account_id, lease_role="HEARTBEAT",
            )
            if lease is None or lease["fencing_token"] != fencing_token:
                return {"status": "STALE_FENCE_REJECTED", "ok": False}
            if not outcome.get("ok"):
                return {"status": "HEARTBEAT_FAILED", "ok": False, "outcome": outcome}
            next_id = outcome.get("heartbeat_id")
            await uow.session.execute(
                sql_text(
                    "UPDATE trading.execution_leases SET latest_heartbeat_id=:h, "
                    "latest_heartbeat_hash=:hh, version=version+1, updated_at=now() "
                    "WHERE account_id=:a AND lease_role='HEARTBEAT' AND fencing_token=:f"
                ),
                {"h": next_id, "hh": hashlib.sha256(
                    str(next_id).encode()).hexdigest(), "a": account_id, "f": fencing_token},
            )
            return {"status": "HEARTBEAT_OK", "ok": True, "heartbeat_id": next_id,
                    "fencing_token": fencing_token}
