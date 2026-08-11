"""DB-bound shadow execution and immutable double-entry evidence (WP-03)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.db.uow import UnitOfWork
from app.domain.trading.ledger import build_fill_postings, postings_balanced
from app.domain.trading.rounding import round_cash
from app.domain.trading.valuation import DepthFill, depth_walk
from app.outbox.contracts import create_envelope
from app.outbox.repository import OutboxRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository
from app.schemas.trading.execution import PositionUpdateInput, ShadowFillInput


@dataclass(frozen=True)
class FillResult:
    ok: bool
    execution_id: int | None = None
    ledger_transaction_id: int | None = None
    status: str | None = None
    filled_quantity: Decimal | None = None
    vwap: Decimal | None = None
    fee: Decimal | None = None
    reason: str | None = None
    replayed: bool = False


@dataclass(frozen=True)
class ReversalResult:
    ok: bool
    ledger_transaction_id: int | None = None
    replayed: bool = False
    reason: str | None = None


class ShadowExecutionLogic:
    """Resolve the frozen DB chain and atomically write fill, projection, ledger and outbox."""

    def __init__(
        self,
        execution: ExecutionRepository,
        ledger: LedgerRepository,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._execution = execution
        self._ledger = ledger
        self._outbox = outbox or OutboxRepository()

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        return Decimal(str(value))

    @staticmethod
    def _capability_for_role(role: str) -> str:
        return {"open": "can_open", "reduce": "can_reduce", "close": "can_close"}[role]

    def _validate_material(self, material: dict[str, Any]) -> tuple[str, str, Decimal, int, str]:
        now = datetime.now(timezone.utc)
        if material["intent_status"] != "COMMITTED":
            raise RuntimeError("shadow_fill_intent_not_committed")
        if material["ttl_at"] is not None and material["ttl_at"] <= now:
            raise RuntimeError("shadow_fill_intent_expired")
        if material["intent_decision_id"] != material["trade_decision_id"]:
            raise RuntimeError("shadow_fill_intent_decision_mismatch")
        if material["decision_status"] != "ACTION" or material["disposition"] != "ACTION":
            raise RuntimeError("shadow_fill_decision_not_action")
        if material["execution_spec_status"] != "active":
            raise RuntimeError("shadow_fill_execution_spec_inactive")
        if (material["permission_status"] != "active"
                or material["permission_mode"] != "shadow"
                or self._decimal(material["authorized_capital"]) != 0
                or material["kill_switch"]):
            raise RuntimeError("shadow_fill_permission_invalid")
        role = material["leg_role"]
        if role not in ("open", "reduce", "close"):
            raise RuntimeError("shadow_fill_leg_role_invalid")
        capability = material["capability"] or {}
        if capability.get(self._capability_for_role(role)) is not True:
            raise RuntimeError("shadow_fill_capability_denied")
        signed_quantity = self._decimal(material["signed_quantity"])
        if signed_quantity == 0:
            raise RuntimeError("shadow_fill_leg_quantity_zero")
        side = "buy" if signed_quantity > 0 else "sell"
        if (side == "buy") != (role == "open"):
            raise RuntimeError("shadow_fill_leg_sign_invalid")
        if not material["checkpoint_complete"] or material["checkpoint_validity"] != "VALID":
            raise RuntimeError("shadow_fill_checkpoint_invalid")
        spec = material["execution_spec"] or {}
        if spec.get("execution_mode") != "shadow_only" or spec.get("short_sell_to_open") is not False:
            raise RuntimeError("shadow_fill_frozen_spec_invalid")
        fee_bps = self._decimal((spec.get("fee") or {}).get("taker_fee_bps", 0))
        max_levels = int((spec.get("depth_walk") or {}).get("max_levels", 0))
        if fee_bps < 0 or max_levels <= 0:
            raise RuntimeError("shadow_fill_frozen_cost_invalid")
        namespace = f"shadow-{material['experiment_variant']}"
        units = (material.get("objective_content") or {}).get("units")
        if not isinstance(units, str) or not units.strip():
            raise RuntimeError("shadow_fill_cash_asset_missing")
        return side, namespace, abs(signed_quantity), max_levels, units.lower()

    @staticmethod
    def _legacy_assertions(
        fill: ShadowFillInput,
        material: dict[str, Any],
        *,
        side: str,
        namespace: str,
        quantity: Decimal,
        fee_bps: Decimal,
        portfolio_namespace: str | None,
        cash_asset_key: str | None,
        derived_cash_asset_key: str,
    ) -> None:
        """Legacy payload fields can assert identity, but can never supply execution facts."""
        checks = (
            ("contract_spec_id", fill.contract_spec_id, material["contract_spec_id"]),
            ("token_id", fill.token_id, material["token_id"]),
            ("fill_role", fill.fill_role, material["leg_role"]),
            ("quantity", fill.quantity, quantity),
            ("side", fill.side, side),
            ("portfolio_namespace", fill.portfolio_namespace, namespace),
            ("portfolio_namespace", portfolio_namespace, namespace),
            ("cash_asset_key", cash_asset_key, derived_cash_asset_key),
            ("taker_fee_bps", fill.taker_fee_bps, fee_bps),
        )
        for field, supplied, expected in checks:
            if supplied is None:
                continue
            if field in ("quantity", "taker_fee_bps"):
                supplied = Decimal(str(supplied))
                expected = Decimal(str(expected))
            if supplied != expected:
                raise RuntimeError(f"shadow_fill_payload_mismatch:{field}")

    async def shadow_fill(
        self,
        uow: UnitOfWork,
        *,
        fill: ShadowFillInput,
        portfolio_namespace: str | None = None,
        cash_asset_key: str | None = None,
    ) -> FillResult:
        existing_by_key = await self._execution.get_execution_by_key(
            uow.session, fill.execution_key
        )
        if existing_by_key is not None:
            expected = (
                ("economic_action_intent_id", fill.economic_action_intent_id),
                ("action_set_leg_id", fill.action_set_leg_id),
                ("contract_spec_id", fill.contract_spec_id),
                ("token_id", fill.token_id),
                ("fill_role", fill.fill_role),
                ("quantity", fill.quantity),
                ("portfolio_namespace", fill.portfolio_namespace or portfolio_namespace),
            )
            for field, supplied in expected:
                if supplied is None:
                    continue
                actual = existing_by_key[field]
                if field == "quantity":
                    actual, supplied = self._decimal(actual), self._decimal(supplied)
                if actual != supplied:
                    raise RuntimeError(f"execution_idempotency_mismatch:{field}")
            if existing_by_key["status"] == "PENDING":
                raise RuntimeError("shadow_fill_execution_pending_conflict")
            tx = await self._ledger.transaction_for_execution(
                uow.session, existing_by_key["id"]
            )
            return FillResult(
                True,
                execution_id=existing_by_key["id"],
                ledger_transaction_id=tx["id"] if tx else None,
                status=existing_by_key["status"],
                filled_quantity=self._decimal(existing_by_key["filled_quantity"]),
                vwap=(self._decimal(existing_by_key["vwap"])
                      if existing_by_key["vwap"] is not None else Decimal("0")),
                fee=self._decimal(existing_by_key["fee"]),
                replayed=True,
            )
        material = await self._execution.fill_material(
            uow.session,
            economic_action_intent_id=fill.economic_action_intent_id,
            action_set_leg_id=fill.action_set_leg_id,
        )
        if material is None:
            raise RuntimeError("shadow_fill_material_missing")
        side, namespace, quantity, max_levels, derived_cash_asset = self._validate_material(material)
        fee_bps = self._decimal(
            ((material["execution_spec"] or {}).get("fee") or {}).get("taker_fee_bps", 0)
        )
        self._legacy_assertions(
            fill, material, side=side, namespace=namespace, quantity=quantity,
            fee_bps=fee_bps, portfolio_namespace=portfolio_namespace,
            cash_asset_key=cash_asset_key, derived_cash_asset_key=derived_cash_asset,
        )
        levels = await self._execution.checkpoint_levels(
            uow.session,
            checkpoint_id=material["checkpoint_id"],
            checkpoint_received_at=material["checkpoint_received_at"],
            side=side,
            max_levels=max_levels,
        )
        depth: DepthFill = depth_walk(
            levels, side=side, target_quantity=quantity, taker_fee_bps=fee_bps
        )
        status = "FILLED" if depth.complete else (
            "PARTIAL" if depth.fill_quantity > 0 else "REJECTED"
        )

        # One economic effect per immutable intent leg, independent of caller execution keys.
        await self._execution.acquire_execution_lock(
            uow.session,
            economic_action_intent_id=fill.economic_action_intent_id,
            action_set_leg_id=fill.action_set_leg_id,
        )
        prior_leg_execution = await self._execution.execution_for_leg(
            uow.session,
            economic_action_intent_id=fill.economic_action_intent_id,
            action_set_leg_id=fill.action_set_leg_id,
        )
        if prior_leg_execution is not None and prior_leg_execution["execution_key"] != fill.execution_key:
            raise RuntimeError("shadow_fill_leg_already_executed")

        # Lock the projection before claiming the execution, so different legs cannot lose updates.
        current = None
        new_quantity = Decimal("0")
        new_cost = Decimal("0")
        if depth.fill_quantity > 0:
            await self._execution.acquire_position_lock(
                uow.session,
                portfolio_namespace=namespace,
                component_id=material["component_id"],
                market_id=material["market_id"],
                contract_spec_id=material["contract_spec_id"],
                token_id=material["token_id"],
            )
            current = await self._execution.get_position(
                uow.session,
                portfolio_namespace=namespace,
                contract_spec_id=material["contract_spec_id"],
                token_id=material["token_id"],
                for_update=True,
            )
            old_quantity = self._decimal(current["quantity"]) if current else Decimal("0")
            old_cost = self._decimal(current["cost_basis"]) if current else Decimal("0")
            if side == "sell" and depth.fill_quantity > old_quantity:
                raise RuntimeError("shadow_fill_negative_position")
            signed_fill = depth.fill_quantity if side == "buy" else -depth.fill_quantity
            new_quantity = old_quantity + signed_fill
            if side == "buy":
                new_cost = old_cost + round_cash(depth.fill_quantity * depth.vwap)
            else:
                relieved = old_cost if new_quantity == 0 else round_cash(
                    old_cost * depth.fill_quantity / old_quantity
                )
                new_cost = max(Decimal("0"), old_cost - relieved)

        claimed = await self._execution.insert_execution(
            uow.session,
            execution_key=fill.execution_key,
            economic_action_intent_id=fill.economic_action_intent_id,
            action_set_leg_id=fill.action_set_leg_id,
            contract_spec_id=material["contract_spec_id"],
            token_id=material["token_id"],
            fill_role=material["leg_role"],
            quantity=quantity,
            portfolio_namespace=namespace,
            quote_checkpoint_id=material["checkpoint_id"],
        )
        execution_id = claimed["id"]
        if not claimed["inserted"]:
            if claimed["status"] == "PENDING":
                raise RuntimeError("shadow_fill_execution_pending_conflict")
            tx = await self._ledger.transaction_for_execution(uow.session, execution_id)
            return FillResult(
                True,
                execution_id=execution_id,
                ledger_transaction_id=tx["id"] if tx else None,
                status=claimed["status"],
                filled_quantity=self._decimal(claimed["filled_quantity"]),
                vwap=self._decimal(claimed["vwap"]) if claimed["vwap"] is not None else Decimal("0"),
                fee=self._decimal(claimed["fee"]),
                replayed=True,
            )
        if not await self._execution.terminalize_execution(
            uow.session,
            execution_id,
            status=status,
            filled_quantity=depth.fill_quantity,
            vwap=depth.vwap if depth.fill_quantity > 0 else None,
            fee=depth.fee,
            unfilled_reason=depth.unfilled_reason,
        ):
            raise RuntimeError("shadow_fill_terminal_conflict")

        tx_id: int | None = None
        if depth.fill_quantity > 0:
            await self._execution.upsert_position(
                uow.session,
                portfolio_namespace=namespace,
                contract_spec_id=material["contract_spec_id"],
                token_id=material["token_id"],
                market_id=material["market_id"],
                component_id=material["component_id"],
                quantity=new_quantity,
                cost_basis=new_cost,
            )
            signed_fill = depth.fill_quantity if side == "buy" else -depth.fill_quantity
            await self._execution.insert_position_lot(
                uow.session,
                execution_id=execution_id,
                portfolio_namespace=namespace,
                contract_spec_id=material["contract_spec_id"],
                token_id=material["token_id"],
                quantity=signed_fill,
                entry_vwap=depth.vwap,
                fill_role=material["leg_role"],
            )
            tx_id = await self._ledger.insert_transaction(
                uow.session,
                transaction_key=f"ledger-{fill.execution_key}",
                kind="FILL",
                trade_decision_id=material["trade_decision_id"],
                execution_id=execution_id,
                portfolio_namespace=namespace,
            )
            token_asset_key = (
                f"tok:{material['contract_spec_id']}:{material['token_id']}"
            )
            postings = build_fill_postings(
                side=side,
                venue="shadow",
                portfolio_namespace=namespace,
                cash_asset_key=derived_cash_asset,
                token_asset_key=token_asset_key,
                gross_cash=depth.fill_quantity * depth.vwap,
                fee=depth.fee,
                token_quantity=depth.fill_quantity,
            )
            if not postings_balanced(postings):
                raise RuntimeError("ledger_postings_unbalanced")
            await self._ledger.insert_postings(
                uow.session,
                transaction_id=tx_id,
                postings=[
                    {
                        "posting_no": index,
                        "asset_type": posting.asset_type,
                        "asset_key": posting.asset_key,
                        "amount": str(posting.amount),
                        "counterparty": posting.counterparty,
                    }
                    for index, posting in enumerate(postings)
                ],
            )
            if not await self._ledger.mark_posted(
                uow.session, tx_id, posted_at=datetime.now(timezone.utc)
            ):
                raise RuntimeError("ledger_post_conflict")

        event = create_envelope(
            topic="shadow.execution.terminalized",
            schema_version=1,
            aggregate_type="execution",
            aggregate_id=str(execution_id),
            idempotency_key=f"shadow-execution:{fill.execution_key}",
            priority=100,
            release_manifest_id=material["release_manifest_id"],
            payload={
                "execution_id": execution_id,
                "intent_id": fill.economic_action_intent_id,
                "action_set_leg_id": fill.action_set_leg_id,
                "quote_checkpoint_id": material["checkpoint_id"],
                "status": status,
                "filled_quantity": str(depth.fill_quantity),
                "portfolio_namespace": namespace,
                "ledger_transaction_id": tx_id,
            },
        )
        await self._outbox.enqueue(uow.session, event)
        return FillResult(
            True,
            execution_id=execution_id,
            ledger_transaction_id=tx_id,
            status=status,
            filled_quantity=depth.fill_quantity,
            vwap=depth.vwap,
            fee=depth.fee,
        )

    async def reverse_ledger(
        self,
        uow: UnitOfWork,
        *,
        reference_transaction_id: int,
        transaction_key: str,
    ) -> ReversalResult:
        tx_id, inserted = await self._ledger.create_reversal(
            uow.session,
            reference_transaction_id=reference_transaction_id,
            transaction_key=transaction_key,
            posted_at=datetime.now(timezone.utc),
        )
        return ReversalResult(True, tx_id, replayed=not inserted)

    async def system_net(
        self, uow: UnitOfWork, *, portfolio_namespace: str
    ) -> dict[str, Any]:
        return await self._ledger.system_net(
            uow.session, portfolio_namespace=portfolio_namespace
        )

    async def rebuild_position(
        self,
        uow: UnitOfWork,
        *,
        update: PositionUpdateInput,
    ) -> None:
        await self._execution.acquire_position_lock(
            uow.session,
            portfolio_namespace=update.portfolio_namespace,
            component_id=update.component_id,
            market_id=update.market_id,
            contract_spec_id=update.contract_spec_id,
            token_id=update.token_id,
        )
        lots = await self._execution.position_lots_for(
            uow.session,
            portfolio_namespace=update.portfolio_namespace,
            contract_spec_id=update.contract_spec_id,
            token_id=update.token_id,
        )
        rebuilt_quantity = Decimal("0")
        rebuilt_cost = Decimal("0")
        for lot in lots:
            lot_quantity = self._decimal(lot["quantity"])
            if lot_quantity > 0:
                rebuilt_quantity += lot_quantity
                rebuilt_cost += round_cash(lot_quantity * self._decimal(lot["entry_vwap"]))
                continue
            reduction = -lot_quantity
            if reduction > rebuilt_quantity or rebuilt_quantity <= 0:
                raise RuntimeError("position_rebuild_invalid")
            relieved = rebuilt_cost if reduction == rebuilt_quantity else round_cash(
                rebuilt_cost * reduction / rebuilt_quantity
            )
            rebuilt_quantity -= reduction
            rebuilt_cost = max(Decimal("0"), rebuilt_cost - relieved)
        await self._execution.upsert_position(
            uow.session,
            portfolio_namespace=update.portfolio_namespace,
            contract_spec_id=update.contract_spec_id,
            token_id=update.token_id,
            market_id=update.market_id,
            component_id=update.component_id,
            quantity=rebuilt_quantity,
            cost_basis=rebuilt_cost,
        )
