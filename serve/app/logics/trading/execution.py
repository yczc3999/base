"""DB-bound shadow execution and immutable double-entry evidence (WP-03).

WP-05 Checkpoint B 追加 ``ExecutionLeaseLogic``：per-account 单一 execution/heartbeat
leader 租约 + 单调 fencing token（决策 §13）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.ledger import build_fill_postings, postings_balanced
from app.domain.trading.rounding import round_cash
from app.domain.trading.valuation import DepthFill, depth_walk
from app.outbox.contracts import create_envelope
from app.outbox.repository import OutboxRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository
from app.schemas.trading.execution import (
    CancelOrderInput,
    EnvelopeInput,
    PositionUpdateInput,
    ShadowFillInput,
    SubmitOrderInput,
)


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


class LeaseError(RuntimeError):
    """租约被其他活跃 leader 持有（未过期、非本人）。"""


class StaleFenceError(RuntimeError):
    """fencing 校验失败：迟到 ack/heartbeat 只追加 stale evidence，不改 current 状态。"""


class ExecutionLeaseLogic:
    """per-account 单一 execution/heartbeat leader 租约；fencing token 单调（WP-05 决策 13）。"""

    def __init__(self, execution: ExecutionRepository | None = None) -> None:
        self._execution = execution if execution is not None else ExecutionRepository()

    @staticmethod
    def _deadline(ttl_s: float) -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=ttl_s)

    async def acquire_lease(
        self,
        uow: UnitOfWork,
        *,
        account_id: int,
        lease_role: str,
        owner: str,
        ttl_s: float,
    ) -> dict[str, Any]:
        """获取/续期/过期接管租约；接管必须使 fencing token 单调递增。"""
        inserted = await self._execution.insert_lease(
            uow.session, account_id=account_id, lease_role=lease_role,
            owner=owner, lease_until=self._deadline(ttl_s),
        )
        if inserted is not None:
            return inserted
        lease = await self._execution.get_lease(
            uow.session, account_id=account_id, lease_role=lease_role, for_update=True
        )
        if lease is None:
            raise RuntimeError("lease_missing_after_conflict")
        now = datetime.now(timezone.utc)
        if lease["owner"] == owner and lease["lease_until"] > now:
            renewed = await self._execution.renew_lease(
                uow.session, account_id=account_id, lease_role=lease_role,
                owner=owner, lease_until=self._deadline(ttl_s),
                fencing_token=lease["fencing_token"],
            )
            if not renewed:
                raise RuntimeError("lease_renew_conflict")
            refreshed = await self._execution.get_lease(
                uow.session, account_id=account_id, lease_role=lease_role
            )
            if refreshed is None:
                raise RuntimeError("lease_missing_after_renew")
            return refreshed
        if lease["lease_until"] <= now:
            taken = await self._execution.takeover_lease(
                uow.session, account_id=account_id, lease_role=lease_role,
                owner=owner, lease_until=self._deadline(ttl_s),
                expected_version=lease["version"],
            )
            if not taken:
                raise RuntimeError("lease_takeover_conflict")
            refreshed = await self._execution.get_lease(
                uow.session, account_id=account_id, lease_role=lease_role
            )
            if refreshed is None:
                raise RuntimeError("lease_missing_after_takeover")
            return refreshed
        raise LeaseError("lease_busy")

    async def renew_lease(
        self,
        uow: UnitOfWork,
        *,
        account_id: int,
        lease_role: str,
        owner: str,
        fencing_token: int,
        ttl_s: float,
    ) -> bool:
        """续期（token 不变）；owner/token/未过期任一不符 → STALE_FENCE_REJECTED。"""
        renewed = await self._execution.renew_lease(
            uow.session, account_id=account_id, lease_role=lease_role,
            owner=owner, lease_until=self._deadline(ttl_s), fencing_token=fencing_token,
        )
        if not renewed:
            raise StaleFenceError("stale_fence_rejected")
        return True

    async def assert_fence(
        self, uow: UnitOfWork, *, account_id: int, lease_role: str, token: int
    ) -> None:
        """side effect 前校验 token；不匹配即 STALE_FENCE_REJECTED（不改 current 状态）。"""
        lease = await self._execution.get_lease(
            uow.session, account_id=account_id, lease_role=lease_role
        )
        if lease is None or token != lease["fencing_token"]:
            raise StaleFenceError("stale_fence_rejected")


# ---------------- WP-05 Checkpoint C：authorization envelope / private submit / cancel / fill ----------------

class KillSwitchBlocked(RuntimeError):
    """kill switch 或 authorized_capital=0 阻止 exposure-increasing submit。"""


class StaleFenceRejectedError(StaleFenceError):
    """迟到 ack/heartbeat 的 fence 拒绝（兼容命名）。"""


@dataclass(frozen=True)
class PreparedSubmit:
    attempt_id: int
    order_id: int
    envelope_id: int
    account_id: int
    intent_id: int
    fencing_token: int
    body_hash: str
    expected_order_hash: str
    sdk_manifest_hash: str
    salt: int
    timestamp: int


@dataclass(frozen=True)
class SubmitApplyResult:
    ok: bool
    status: str | None = None
    order_id: str | None = None
    reason: str | None = None
    replayed: bool = False


@dataclass(frozen=True)
class FillApplyResult:
    ok: bool
    trade_id: int | None = None
    order_status: str | None = None
    reason: str | None = None
    replayed: bool = False


class PrivateExecutionLogic:
    """authorization envelope 与私有 CLOB submit/cancel/fill（DB 双保险 + 单次发送）。

    本类只做 DB/业务规则；wire 由 Driver 完成（单次发送）。每个方法在一个外层 UoW 内；
    submit 的 pre-send persist 与 apply 是两次独立 UoW（网络调用绝不在事务内）。
    """

    def __init__(
        self,
        execution: ExecutionRepository | None = None,
        ledger: LedgerRepository | None = None,
        audit: Any | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._execution = execution if execution is not None else ExecutionRepository()
        self._ledger = ledger if ledger is not None else LedgerRepository()
        self._audit = audit
        self._outbox = outbox or OutboxRepository()

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        return Decimal(str(value))

    @staticmethod
    def _canonical(content: Any) -> str:
        from app.domain.trading.hashing import canonical_hash

        return canonical_hash(content)

    async def _record_external_call(
        self, uow: UnitOfWork, *, driver: str, endpoint: str, method: str,
        request_hash: str, response_hash: str | None, status_code: int | None,
        latency_ms: int, fence_token: int, error_reason: str | None,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.insert_external_call_attempt(
            uow.session,
            attempt_key=f"ext:{driver}:{request_hash[:24]}",
            driver=driver, endpoint=endpoint, method=method,
            request_hash=request_hash, response_hash=response_hash,
            status_code=status_code, latency_ms=latency_ms,
            rate_limit_remaining=None, error_reason=error_reason,
            fence_token=fence_token,
        )

    async def _workflow_event(
        self, uow: UnitOfWork, *, event_key: str, event_type: str,
        aggregate_type: str, aggregate_id: str, payload: dict,
    ) -> None:
        if self._audit is None:
            return
        from app.domain.trading.hashing import canonical_hash

        await self._audit.insert_workflow_event(
            uow.session,
            event_key=event_key,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload_hash=canonical_hash(payload),
            payload=payload,
        )

    async def _alert(
        self, uow: UnitOfWork, *, severity: str, code: str, message_redacted: str,
        alert_key: str,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.insert_alert_event(
            uow.session, alert_key=alert_key, severity=severity, code=code,
            message_redacted=message_redacted,
        )

    # ---- envelope ----

    async def create_envelope(
        self, uow: UnitOfWork, *, input_: EnvelopeInput,
    ) -> dict[str, Any]:
        account = await self._execution.get_account(uow.session, account_id=input_.account_id)
        if account is None:
            raise RuntimeError("envelope_account_missing")
        release = await self._execution.get_release(
            uow.session, release_manifest_id=input_.release_manifest_id
        )
        if release is None:
            raise RuntimeError("envelope_release_missing")
        intent = await self._execution.get_intent(uow.session, intent_id=input_.intent_id)
        if intent is None:
            raise RuntimeError("envelope_intent_missing")
        if intent["intent_hash"] != input_.intent_hash:
            raise RuntimeError("envelope_intent_hash_mismatch")
        if account["capital_permission_manifest_id"] != input_.capital_permission_manifest_id:
            raise RuntimeError("envelope_account_permission_mismatch")
        if release["capital_permission_manifest_id"] != input_.capital_permission_manifest_id:
            raise RuntimeError("envelope_release_permission_mismatch")
        permission = await self._execution.get_permission(
            uow.session, permission_id=input_.capital_permission_manifest_id
        )
        if permission is None:
            raise RuntimeError("envelope_permission_missing")
        if (
            permission["status"] != "active"
            or permission["mode"] != "shadow"
            or self._decimal(permission["authorized_capital"]) != 0
        ):
            raise RuntimeError("envelope_permission_not_shadow_zero")
        if input_.authority != "FAKE_CONFORMANCE":
            raise RuntimeError("envelope_authority_invalid")
        envelope_hash = self._canonical({
            "intent_id": input_.intent_id,
            "account_id": input_.account_id,
            "release_manifest_id": input_.release_manifest_id,
            "execution_spec_version_id": input_.execution_spec_version_id,
            "capital_permission_manifest_id": input_.capital_permission_manifest_id,
            "authority": input_.authority,
            "idempotency_key": input_.idempotency_key,
            "fencing_token": input_.fencing_token,
            "intent_hash": input_.intent_hash,
            "preflight_hash1": input_.preflight_hash1,
            "preflight_hash2": input_.preflight_hash2,
        })
        envelope = await self._execution.insert_envelope(
            uow.session,
            envelope_key=input_.envelope_key,
            intent_id=input_.intent_id,
            account_id=input_.account_id,
            release_manifest_id=input_.release_manifest_id,
            execution_spec_version_id=input_.execution_spec_version_id,
            capital_permission_manifest_id=input_.capital_permission_manifest_id,
            authority=input_.authority,
            idempotency_key=input_.idempotency_key,
            fencing_token=input_.fencing_token,
            intent_hash=input_.intent_hash,
            preflight_hash1=input_.preflight_hash1,
            preflight_hash2=input_.preflight_hash2,
            envelope_hash=envelope_hash,
        )
        await self._workflow_event(
            uow, event_key=f"wf:envelope:{envelope['id']}:created",
            event_type="envelope.created", aggregate_type="envelope",
            aggregate_id=str(envelope["id"]),
            payload={"envelope_id": envelope["id"], "account_id": input_.account_id,
                     "intent_id": input_.intent_id, "envelope_hash": envelope_hash},
        )
        return envelope

    # ---- submit ----

    async def prepare_submit(
        self,
        uow: UnitOfWork,
        *,
        input_: SubmitOrderInput,
        signed_order: Any,
        body_hash: str,
        expected_order_hash: str,
        sdk_manifest_hash: str,
    ) -> PreparedSubmit:
        envelope = await self._execution.get_envelope(
            uow.session, envelope_id=input_.envelope_id, for_update=True
        )
        if envelope is None:
            raise RuntimeError("submit_envelope_missing")
        if envelope["status"] != "ACTIVE":
            raise RuntimeError("submit_envelope_not_active")
        if envelope["account_id"] != input_.account_id:
            raise RuntimeError("submit_envelope_account_mismatch")
        if envelope["fencing_token"] != input_.fencing_token:
            raise StaleFenceError("stale_fence_rejected")
        account = await self._execution.get_account(uow.session, account_id=input_.account_id)
        permission = await self._execution.get_permission(
            uow.session, permission_id=account["capital_permission_manifest_id"]
        )
        if permission is None:
            raise RuntimeError("submit_permission_missing")
        if permission["kill_switch"]:
            raise KillSwitchBlocked("exposure_increasing_blocked_kill_switch")
        if self._decimal(permission["authorized_capital"]) == 0 and input_.side == "BUY":
            raise KillSwitchBlocked("exposure_increasing_blocked_zero_capital")
        leg = await self._execution.resolve_intent_leg(
            uow.session, intent_id=envelope["intent_id"], external_token_id=input_.token_id
        )
        if leg is None:
            raise RuntimeError("submit_intent_leg_missing")
        order = await self._execution.insert_order(
            uow.session,
            order_key=f"ord-{envelope['id']}-{self._canonical({'s': input_.side, 'tk': input_.token_id, 'sz': str(input_.size)})[:32]}",
            account_id=input_.account_id,
            token_id=input_.token_id,
            side=input_.side,
            price=input_.price,
            size=input_.size,
        )
        order_id = order["id"]
        salt = int(getattr(signed_order, "salt", 0))
        timestamp = int(getattr(signed_order, "timestamp", 0))
        event = await self._execution.insert_order_state_event(
            uow.session,
            event_key=f"ev:{order_id}:submitted",
            order_id=order_id,
            event_type="SUBMITTED",
            transition_from="INTENT",
            transition_to="SUBMITTED",
            event_payload={
                "token_id": input_.token_id, "side": input_.side,
                "price": str(input_.price), "size": str(input_.size),
                "salt": salt, "timestamp": timestamp,
            },
            event_hash=self._canonical({
                "order_id": order_id, "event_type": "SUBMITTED",
                "transition_from": "INTENT", "transition_to": "SUBMITTED",
                "token_id": input_.token_id, "salt": salt, "timestamp": timestamp,
            }),
            fence_token=input_.fencing_token,
        )
        attempt_no = await self._execution.next_attempt_no(
            uow.session, envelope_id=envelope["id"]
        )
        attempt = await self._execution.insert_attempt(
            uow.session,
            attempt_key=f"att-{envelope['id']}-{attempt_no}",
            envelope_id=envelope["id"],
            attempt_no=attempt_no,
            body_hash=body_hash,
            expected_order_hash=expected_order_hash,
            sdk_manifest_hash=sdk_manifest_hash,
            salt=salt,
            timestamp=timestamp,
            fencing_token=input_.fencing_token,
            state_event_id=event["id"],
        )
        attempt_id = attempt["id"]
        await uow.session.execute(
            text(
                "UPDATE trading.exchange_orders SET attempt_id=:a WHERE id=:o"
            ),
            {"a": attempt_id, "o": order_id},
        )
        await self._execution.advance_envelope_status(
            uow.session, envelope_id=envelope["id"], new_status="USED"
        )
        return PreparedSubmit(
            attempt_id=attempt_id, order_id=order_id, envelope_id=envelope["id"],
            account_id=input_.account_id, intent_id=envelope["intent_id"],
            fencing_token=input_.fencing_token, body_hash=body_hash,
            expected_order_hash=expected_order_hash, sdk_manifest_hash=sdk_manifest_hash,
            salt=salt, timestamp=timestamp,
        )

    async def apply_submit_outcome(
        self,
        uow: UnitOfWork,
        *,
        prepared: PreparedSubmit,
        outcome: Any,
        response_hash: str | None = None,
        http_status: int | None = None,
        error_reason: str | None = None,
    ) -> SubmitApplyResult:
        order = await self._execution.get_order(
            uow.session, order_id=prepared.order_id, for_update=True
        )
        if order is None:
            raise RuntimeError("submit_order_missing")
        attempt = await self._execution.get_attempt(
            uow.session, attempt_id=prepared.attempt_id
        )
        cls = getattr(outcome, "cls", "UNKNOWN")
        external_order_id = getattr(outcome, "order_id", None)
        result: SubmitApplyResult
        if cls == "ACK":
            if not await self._execution.advance_order(
                uow.session, order_id=prepared.order_id, new_status="ACK",
                external_order_id=external_order_id, expected_status="OPEN",
            ):
                # 已 ACK（重复）→ 只补 provenance。
                await self._workflow_event(
                    uow, event_key=f"wf:order:{prepared.order_id}:duplicate_ack",
                    event_type="order.duplicate_ack", aggregate_type="order",
                    aggregate_id=str(prepared.order_id),
                    payload={"attempt_id": prepared.attempt_id},
                )
                return SubmitApplyResult(True, "ACK", external_order_id, replayed=True)
            await self._execution.insert_order_state_event(
                uow.session,
                event_key=f"ev:{prepared.order_id}:ack",
                order_id=prepared.order_id,
                event_type="ACK",
                transition_from="SUBMITTED",
                transition_to="ACK",
                event_payload={"order_id": external_order_id},
                event_hash=self._canonical({
                    "order_id": prepared.order_id, "event_type": "ACK",
                    "external_order_id": external_order_id,
                }),
                fence_token=prepared.fencing_token,
            )
            await self._execution.advance_attempt_result(
                uow.session, attempt_id=prepared.attempt_id, result="ACK"
            )
            await self._ack_reservation(uow, prepared)
            result = SubmitApplyResult(True, "ACK", external_order_id)
        elif cls == "REJECTED":
            await self._execution.advance_order(
                uow.session, order_id=prepared.order_id, new_status="REJECTED",
                expected_status="OPEN",
            )
            await self._execution.insert_order_state_event(
                uow.session,
                event_key=f"ev:{prepared.order_id}:rejected",
                order_id=prepared.order_id,
                event_type="REJECTED",
                transition_from="SUBMITTED",
                transition_to="REJECTED",
                event_payload={"reason": error_reason or "rejected"},
                event_hash=self._canonical({
                    "order_id": prepared.order_id, "event_type": "REJECTED",
                }),
                fence_token=prepared.fencing_token,
            )
            await self._execution.advance_attempt_result(
                uow.session, attempt_id=prepared.attempt_id, result="REJECTED"
            )
            await self._release_reservation(uow, prepared)
            result = SubmitApplyResult(True, "REJECTED", None)
        else:
            # UNKNOWN / AUTH_STOP / THROTTLED：保留 reservation + hard stop。
            await self._execution.advance_order(
                uow.session, order_id=prepared.order_id, new_status="UNKNOWN",
                expected_status="OPEN",
            )
            await self._execution.insert_order_state_event(
                uow.session,
                event_key=f"ev:{prepared.order_id}:unknown",
                order_id=prepared.order_id,
                event_type="UNKNOWN",
                transition_from="SUBMITTED",
                transition_to="UNKNOWN",
                event_payload={"reason": error_reason or cls.lower()},
                event_hash=self._canonical({
                    "order_id": prepared.order_id, "event_type": "UNKNOWN",
                    "reason": error_reason or cls.lower(),
                }),
                fence_token=prepared.fencing_token,
            )
            await self._execution.advance_attempt_result(
                uow.session, attempt_id=prepared.attempt_id, result="UNKNOWN"
            )
            await self._unknown_reservation(uow, prepared)
            await self._alert(
                uow, severity="ERROR", code="order_unknown_hard_stop",
                message_redacted="submit result indeterminate; reservation retained; hard stop",
                alert_key=f"alert:{prepared.order_id}:unknown",
            )
            result = SubmitApplyResult(True, "UNKNOWN", None)
        await self._record_external_call(
            uow, driver="clob_trading", endpoint="POST /order", method="POST",
            request_hash=prepared.body_hash, response_hash=response_hash,
            status_code=http_status, latency_ms=0, fence_token=prepared.fencing_token,
            error_reason=error_reason,
        )
        await self._workflow_event(
            uow, event_key=f"wf:order:{prepared.order_id}:submit:{cls.lower()}",
            event_type=f"order.submit.{cls.lower()}", aggregate_type="order",
            aggregate_id=str(prepared.order_id),
            payload={"attempt_id": prepared.attempt_id, "result": cls},
        )
        return result

    async def _ack_reservation(self, uow: UnitOfWork, prepared: PreparedSubmit) -> None:
        reservation = await self._execution.get_reservation_by_intent(
            uow.session, account_id=prepared.account_id, intent_id=prepared.intent_id,
            for_update=True,
        )
        if reservation is None:
            return
        if reservation["status"] == "HELD":
            await self._execution.transfer_funds_local_to_provider(
                uow.session, account_id=prepared.account_id, asset_key=reservation["asset_key"],
                amount=reservation["amount"],
            )
            await self._execution.advance_reservation(
                uow.session, reservation_id=reservation["id"], new_status="PROVIDER_BOUND"
            )
        elif reservation["status"] == "UNKNOWN":
            await self._execution.transfer_funds_local_to_provider(
                uow.session, account_id=prepared.account_id, asset_key=reservation["asset_key"],
                amount=reservation["amount"],
            )
            await self._execution.advance_reservation(
                uow.session, reservation_id=reservation["id"], new_status="PROVIDER_BOUND"
            )

    async def _release_reservation(self, uow: UnitOfWork, prepared: PreparedSubmit) -> None:
        reservation = await self._execution.get_reservation_by_intent(
            uow.session, account_id=prepared.account_id, intent_id=prepared.intent_id,
            for_update=True,
        )
        if reservation is None:
            return
        if reservation["status"] in ("HELD", "UNKNOWN"):
            await self._execution.release_funds_local(
                uow.session, account_id=prepared.account_id, asset_key=reservation["asset_key"],
                amount=reservation["amount"],
            )
            await self._execution.advance_reservation(
                uow.session, reservation_id=reservation["id"], new_status="RELEASED"
            )
        elif reservation["status"] == "PROVIDER_BOUND":
            await self._execution.release_funds_provider(
                uow.session, account_id=prepared.account_id, asset_key=reservation["asset_key"],
                amount=reservation["amount"],
            )
            await self._execution.advance_reservation(
                uow.session, reservation_id=reservation["id"], new_status="RELEASED"
            )

    async def _unknown_reservation(self, uow: UnitOfWork, prepared: PreparedSubmit) -> None:
        reservation = await self._execution.get_reservation_by_intent(
            uow.session, account_id=prepared.account_id, intent_id=prepared.intent_id,
            for_update=True,
        )
        if reservation is None:
            return
        if reservation["status"] == "HELD":
            # 保持 local_reserved（不释放）；reservation 转 UNKNOWN。
            await self._execution.advance_reservation(
                uow.session, reservation_id=reservation["id"], new_status="UNKNOWN"
            )

    # ---- cancel ----

    async def cancel_order(
        self,
        uow: UnitOfWork,
        *,
        input_: CancelOrderInput,
        outcome: Any,
        response_hash: str | None = None,
        error_reason: str | None = None,
    ) -> SubmitApplyResult:
        order = await self._execution.get_order_by_external(
            uow.session, account_id=input_.account_id,
            external_order_id=input_.external_order_id, for_update=True,
        )
        if order is None:
            raise RuntimeError("cancel_order_missing")
        if order["status"] in ("FILLED", "CANCELLED", "RECONCILED", "REJECTED"):
            return SubmitApplyResult(True, order["status"], order["external_order_id"], replayed=True)
        if not await self._execution.advance_order(
            uow.session, order_id=order["id"], new_status="CANCELLED",
            expected_status=order["status"],
        ):
            return SubmitApplyResult(False, None, reason="cancel_transition_conflict")
        await self._execution.insert_order_state_event(
            uow.session,
            event_key=f"ev:{order['id']}:cancelled",
            order_id=order["id"],
            event_type="CANCELLED",
            transition_from=order["status"],
            transition_to="CANCELLED",
            event_payload={"order_id": input_.external_order_id},
            event_hash=self._canonical({
                "order_id": order["id"], "event_type": "CANCELLED",
            }),
            fence_token=input_.fencing_token,
        )
        await self._record_external_call(
            uow, driver="clob_trading", endpoint="DELETE /order", method="DELETE",
            request_hash=self._canonical({"cancel": input_.external_order_id}),
            response_hash=response_hash, status_code=None, latency_ms=0,
            fence_token=input_.fencing_token, error_reason=error_reason,
        )
        return SubmitApplyResult(True, "CANCELLED", order["external_order_id"])

    # ---- fill ----

    async def apply_fill(
        self,
        uow: UnitOfWork,
        *,
        order_id: int,
        account_id: int,
        envelope_id: int,
        intent_id: int,
        fencing_token: int,
        external_trade_id: str,
        side: str,
        price: Any,
        size: Any,
        fee: Any,
        trade_time: datetime,
    ) -> FillApplyResult:
        order = await self._execution.get_order(
            uow.session, order_id=order_id, for_update=True
        )
        if order is None:
            raise RuntimeError("fill_order_missing")
        if order["account_id"] != account_id:
            raise RuntimeError("fill_cross_account_reference")
        trade = await self._execution.insert_trade(
            uow.session,
            trade_key=f"trd-{account_id}-{external_trade_id}",
            order_id=order_id,
            account_id=account_id,
            external_trade_id=external_trade_id,
            side=side,
            price=price,
            size=size,
            fee=fee,
            trade_time=trade_time,
        )
        if not trade.get("inserted", True):
            # 重复 trade：只补 provenance，不重复 economic effect。
            await self._workflow_event(
                uow, event_key=f"wf:order:{order_id}:duplicate_trade:{external_trade_id}",
                event_type="order.duplicate_trade", aggregate_type="order",
                aggregate_id=str(order_id),
                payload={"external_trade_id": external_trade_id},
            )
            return FillApplyResult(True, trade["id"], order["status"], replayed=True)
        leg = await self._execution.resolve_intent_leg(
            uow.session, intent_id=intent_id, external_token_id=order["token_id"]
        )
        if leg is None:
            raise RuntimeError("fill_intent_leg_missing")
        fill_size = self._decimal(size)
        old_filled = self._decimal(order["filled_size"])
        new_filled = old_filled + fill_size
        order_size = self._decimal(order["size"])
        if new_filled > order_size:
            raise RuntimeError("fill_exceeds_order_size")
        new_status = "FILLED" if new_filled >= order_size else "PARTIAL"
        if not await self._execution.advance_order(
            uow.session, order_id=order_id, new_status=new_status,
            filled_size=new_filled, expected_status=order["status"],
        ):
            raise RuntimeError("fill_order_transition_conflict")
        await self._execution.insert_order_state_event(
            uow.session,
            event_key=f"ev:{order_id}:{new_status.lower()}:{external_trade_id}",
            order_id=order_id,
            event_type=new_status,
            transition_from=order["status"],
            transition_to=new_status,
            event_payload={
                "external_trade_id": external_trade_id, "size": str(fill_size),
                "price": str(price), "fee": str(fee),
            },
            event_hash=self._canonical({
                "order_id": order_id, "event_type": new_status,
                "external_trade_id": external_trade_id,
            }),
            fence_token=fencing_token,
        )
        namespace = f"exec-{account_id}"
        side_lower = "buy" if side == "BUY" else "sell"
        leg_role = leg["leg_role"]
        if side_lower == "buy":
            signed_fill = fill_size
        else:
            signed_fill = -fill_size
        await self._execution.acquire_position_lock(
            uow.session,
            portfolio_namespace=namespace,
            component_id=None,
            market_id=leg["market_id"],
            contract_spec_id=leg["contract_spec_id"],
            token_id=leg["internal_token_id"],
        )
        current = await self._execution.get_position(
            uow.session,
            portfolio_namespace=namespace,
            contract_spec_id=leg["contract_spec_id"],
            token_id=leg["internal_token_id"],
            for_update=True,
        )
        old_quantity = self._decimal(current["quantity"]) if current else Decimal("0")
        old_cost = self._decimal(current["cost_basis"]) if current else Decimal("0")
        new_quantity = old_quantity + signed_fill
        if new_quantity < 0:
            raise RuntimeError("fill_negative_position")
        if side_lower == "buy":
            new_cost = old_cost + round_cash(fill_size * self._decimal(price))
        else:
            relieved = old_cost if new_quantity == 0 else round_cash(
                old_cost * fill_size / old_quantity
            )
            new_cost = max(Decimal("0"), old_cost - relieved)
        await self._execution.upsert_position(
            uow.session,
            portfolio_namespace=namespace,
            contract_spec_id=leg["contract_spec_id"],
            token_id=leg["internal_token_id"],
            market_id=leg["market_id"],
            component_id=None,
            quantity=new_quantity,
            cost_basis=new_cost,
            account_id=account_id,
            envelope_id=envelope_id,
            order_id=order_id,
        )
        # 真实 CLOB fill 不创建 executions 行（executions 是 shadow 一次性 fill 事实、
        # immutable 且 per-intent-leg）；lot 直接以 NULL execution_id + order/trade lineage 落库。
        await self._execution.insert_position_lot(
            uow.session,
            execution_id=None,
            portfolio_namespace=namespace,
            contract_spec_id=leg["contract_spec_id"],
            token_id=leg["internal_token_id"],
            quantity=signed_fill,
            entry_vwap=self._decimal(price),
            fill_role=leg_role,
            account_id=account_id,
            order_id=order_id,
            trade_id=trade["id"],
        )
        token_asset_key = f"tok:{leg['contract_spec_id']}:{leg['internal_token_id']}"
        cash_asset_key = (leg.get("cash_asset_key") or "USD").lower()
        postings = build_fill_postings(
            side=side_lower,
            venue="polymarket",
            portfolio_namespace=namespace,
            cash_asset_key=cash_asset_key,
            token_asset_key=token_asset_key,
            gross_cash=fill_size * self._decimal(price),
            fee=self._decimal(fee),
            token_quantity=fill_size,
        )
        if not postings_balanced(postings):
            raise RuntimeError("ledger_postings_unbalanced")
        tx_id = await self._ledger.insert_transaction(
            uow.session,
            transaction_key=f"ledger-{external_trade_id}",
            kind="FILL",
            trade_decision_id=leg["trade_decision_id"],
            execution_id=None,
            portfolio_namespace=namespace,
            account_id=account_id,
            envelope_id=envelope_id,
            order_id=order_id,
            trade_id=trade["id"],
        )
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
        reservation = await self._execution.get_reservation_by_intent(
            uow.session, account_id=account_id, intent_id=intent_id, for_update=True,
        )
        if reservation is not None and new_status == "FILLED" and reservation["status"] == "PROVIDER_BOUND":
            await self._execution.release_funds_provider(
                uow.session, account_id=account_id, asset_key=reservation["asset_key"],
                amount=reservation["amount"],
            )
            await self._execution.advance_reservation(
                uow.session, reservation_id=reservation["id"], new_status="CONSUMED"
            )
        await self._workflow_event(
            uow, event_key=f"wf:order:{order_id}:fill:{external_trade_id}",
            event_type="order.fill", aggregate_type="order", aggregate_id=str(order_id),
            payload={"external_trade_id": external_trade_id, "size": str(fill_size),
                     "price": str(price), "status": new_status},
        )
        return FillApplyResult(True, trade["id"], new_status)
