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
from app.domain.trading.hashing import canonical_hash
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


EXECUTION_AUTHORIZATION_HASH_ALGORITHM_CODE_HASH = canonical_hash({
    "authority_schema": "execution-preflight-authority/v2",
    "market_schema": "execution-preflight-market/v2",
    "envelope_schema": "execution-authorization-envelope/v2",
    "identity": "natural-keys/content-hashes",
})

# Polygon CLOB verifying contracts frozen by the WP-05 readiness specification.
POLYMARKET_EXCHANGE_BY_NEG_RISK = {
    False: "0xE111180000d2663C0091e4f400237545B87B996B",
    True: "0xe2222d279d744050d28e00520010520000310F59",
}


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
        self,
        uow: UnitOfWork,
        *,
        account_id: int,
        lease_role: str,
        owner: str,
        token: int,
    ) -> None:
        """Lock and validate the exact unexpired owner/token fence before an effect."""
        lease = await self._execution.get_active_lease_fence(
            uow.session,
            account_id=account_id,
            lease_role=lease_role,
            owner=owner,
            fencing_token=token,
            for_update=True,
        )
        if lease is None:
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
    owner: str
    fencing_token: int
    body_hash: str
    expected_order_hash: str
    sdk_manifest_hash: str
    salt: int
    timestamp: int
    exposure_increasing: bool = False


@dataclass(frozen=True)
class PreparedCancel:
    order_id: int
    account_id: int
    intent_id: int
    owner: str
    fencing_token: int
    external_order_id: str
    expected_status: str


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

    async def authoritative_preflight_hashes(
        self,
        uow: UnitOfWork,
        *,
        intent_id: int,
        account_id: int,
        release_manifest_id: int,
        execution_spec_version_id: int,
        capital_permission_manifest_id: int,
        fencing_token: int,
    ) -> tuple[str, str]:
        """Derive both preflight hashes exclusively from locked DB material."""
        fixture_hook = getattr(self._execution, "authoritative_preflight_material", None)
        if callable(fixture_hook):
            material = await fixture_hook(
                uow.session,
                intent_id=intent_id,
                account_id=account_id,
                release_manifest_id=release_manifest_id,
                execution_spec_version_id=execution_spec_version_id,
                capital_permission_manifest_id=capital_permission_manifest_id,
                fencing_token=fencing_token,
            )
            return self._canonical(material[0]), self._canonical(material[1])
        intent = await self._execution.get_intent(uow.session, intent_id=intent_id)
        account = await self._execution.get_account(uow.session, account_id=account_id)
        release = (
            await uow.session.execute(
                text(
                    "SELECT release_name, total_hash, status, git_sha, image_digest, "
                    "db_revision, execution_spec_version_id, capital_permission_manifest_id "
                    "FROM trading.release_manifests WHERE id=:id"
                ),
                {"id": release_manifest_id},
            )
        ).mappings().one_or_none()
        permission = await self._execution.get_permission(
            uow.session, permission_id=capital_permission_manifest_id
        )
        spec = (
            await uow.session.execute(
                text(
                    "SELECT spec_key, version_no, content, content_hash, status "
                    "FROM trading.execution_spec_versions WHERE id=:id"
                ),
                {"id": execution_spec_version_id},
            )
        ).mappings().one_or_none()
        if None in (intent, account, release, permission, spec):
            raise RuntimeError("preflight_material_missing")
        if account["release_manifest_id"] != release_manifest_id:
            raise RuntimeError("preflight_account_release_mismatch")
        if account["capital_permission_manifest_id"] != capital_permission_manifest_id:
            raise RuntimeError("preflight_account_permission_mismatch")
        if release["execution_spec_version_id"] != execution_spec_version_id:
            raise RuntimeError("preflight_release_execution_spec_mismatch")
        if release["capital_permission_manifest_id"] != capital_permission_manifest_id:
            raise RuntimeError("preflight_release_permission_mismatch")
        # Business hashes are deliberately free of database identities.  IDs above
        # are only lookup/FK material; every hashed lineage item uses a stable natural
        # key and/or immutable content hash so a rolled-back sequence allocation cannot
        # change signing authority on replay.
        first = {
            "schema": "execution-preflight-authority/v2",
            "algorithm_code_hash": EXECUTION_AUTHORIZATION_HASH_ALGORITHM_CODE_HASH,
            "intent": {
                "intent_key": intent["intent_key"],
                "intent_hash": intent["intent_hash"],
                "status": intent["status"],
            },
            "account": {
                key: account.get(key) for key in (
                    "account_key", "provider", "chain_id", "identity_type",
                    "funder_address", "maker_address", "signing_identity",
                    "wallet_type", "signature_type", "network_mode", "status",
                )
            },
            "release": {
                key: release.get(key) for key in (
                    "release_name", "total_hash", "status", "git_sha",
                    "image_digest", "db_revision",
                )
            },
            "execution_spec": {
                "spec_key": spec["spec_key"],
                "version_no": spec["version_no"],
                "content_hash": spec["content_hash"],
                "status": spec["status"],
                "fee_hash": self._canonical((spec.get("content") or {}).get("fee") or {}),
            },
            "permission": {
                key: permission.get(key) for key in (
                    "name", "mode", "capability", "limits", "evaluation_capital",
                    "authorized_capital", "kill_switch", "content_hash", "status",
                )
            },
            "fencing_token": fencing_token,
        }
        legs = (
            await uow.session.execute(
                text(
                    "SELECT cs.contract_key, cs.version_no AS contract_version_no, "
                    "cs.content_hash AS contract_content_hash, t.token_id AS external_token_id, "
                    "leg.leg_role, leg.quantity, leg.signed_quantity, leg.entry_vwap "
                    "FROM trading.economic_action_intents i "
                    "JOIN trading.action_set_legs leg ON leg.action_set_id=i.action_set_id "
                    "JOIN trading.contract_specs cs ON cs.id=leg.contract_spec_id "
                    "JOIN trading.pm_tokens t ON t.id=leg.token_id "
                    "WHERE i.id=:intent "
                    "ORDER BY cs.contract_key, cs.version_no, t.token_id, leg.leg_role"
                ),
                {"intent": intent_id},
            )
        ).mappings().all()
        quotes = (
            await uow.session.execute(
                text(
                    "SELECT qb.token_id, cp.book_hash AS checkpoint_book_hash, "
                    "qb.checkpoint_received_at, qb.best_bid, qb.best_ask, "
                    "qb.price_convention, qb.as_of, qb.stale_at, bc.tick_size, "
                    "bc.min_order_size, bc.validity, bc.observed_at, bc.depth_hash, "
                    "m.gamma_market_id, m.condition_id, m.content_hash AS market_content_hash, "
                    "m.neg_risk "
                    "FROM trading.economic_action_intents i "
                    "JOIN trading.pm_quote_bindings qb ON qb.trade_decision_id=i.trade_decision_id "
                    "JOIN trading.pm_book_checkpoints cp "
                    " ON cp.id=qb.checkpoint_id "
                    "AND cp.received_at=qb.checkpoint_received_at "
                    "AND cp.token_id=qb.token_id "
                    "JOIN trading.pm_tokens pt ON pt.token_id=qb.token_id "
                    "JOIN trading.pm_markets m ON m.id=pt.market_id "
                    "LEFT JOIN trading.pm_book_current bc ON bc.token_id=qb.token_id "
                    "WHERE i.id=:intent ORDER BY qb.token_id, qb.checkpoint_received_at"
                ),
                {"intent": intent_id},
            )
        ).mappings().all()
        if not quotes or any(
            row.get("checkpoint_book_hash") is None
            or row.get("tick_size") is None
            or row.get("min_order_size") is None
            or row.get("validity") != "VALID"
            or row.get("neg_risk") is None
            for row in quotes
        ):
            raise RuntimeError("preflight_quote_material_missing")
        reservation = await self._execution.get_reservation_by_intent(
            uow.session, account_id=account_id, intent_id=intent_id, for_update=True
        )
        if reservation is None:
            raise RuntimeError("preflight_reservation_missing")
        positions = await self._execution.positions_for_account(
            uow.session, account_id=account_id
        )
        for leg in (
            await uow.session.execute(
                text(
                    "SELECT leg.contract_spec_id, leg.token_id, leg.leg_role, leg.quantity "
                    "FROM trading.economic_action_intents i "
                    "JOIN trading.action_set_legs leg ON leg.action_set_id=i.action_set_id "
                    "WHERE i.id=:intent ORDER BY leg.id"
                ),
                {"intent": intent_id},
            )
        ).mappings().all():
            if leg["leg_role"] not in ("reduce", "close"):
                continue
            position = next(
                (
                    row for row in positions
                    if row.get("contract_spec_id") == leg["contract_spec_id"]
                    and row.get("token_id") == leg["token_id"]
                ),
                None,
            )
            if position is None or self._decimal(position.get("quantity")) < self._decimal(
                leg["quantity"]
            ):
                raise RuntimeError("preflight_reduce_position_insufficient")
        stable_positions = (
            await uow.session.execute(
                text(
                    "SELECT pos.portfolio_namespace, cs.contract_key, "
                    "cs.version_no AS contract_version_no, "
                    "cs.content_hash AS contract_content_hash, "
                    "t.token_id AS external_token_id, pos.quantity, pos.cost_basis "
                    "FROM trading.positions pos "
                    "JOIN trading.contract_specs cs ON cs.id=pos.contract_spec_id "
                    "JOIN trading.pm_tokens t ON t.id=pos.token_id "
                    "WHERE pos.account_id=:account "
                    "ORDER BY pos.portfolio_namespace, cs.contract_key, "
                    "cs.version_no, t.token_id FOR UPDATE OF pos"
                ),
                {"account": account_id},
            )
        ).mappings().all()
        funds = (
            await uow.session.execute(
                text(
                    "SELECT f.asset_key, f.confirmed, f.provider_reserved, "
                    "f.local_reserved, f.available, f.reconcile_watermark, "
                    "s.request_hash AS source_request_hash, "
                    "s.observed_at AS source_observed_at, "
                    "s.completeness AS source_completeness "
                    "FROM trading.account_funds_current f "
                    "JOIN trading.pm_balance_allowance_snapshots s "
                    " ON s.account_id=f.account_id AND s.asset_key=f.asset_key "
                    "AND s.id=f.source_snapshot_id "
                    "WHERE f.account_id=:account ORDER BY f.asset_key FOR UPDATE OF f"
                ),
                {"account": account_id},
            )
        ).mappings().all()
        second = {
            "schema": "execution-preflight-market/v2",
            "algorithm_code_hash": EXECUTION_AUTHORIZATION_HASH_ALGORITHM_CODE_HASH,
            "intent": {
                "intent_key": intent["intent_key"],
                "intent_hash": intent["intent_hash"],
            },
            "legs": [dict(row) for row in legs],
            "quotes": [dict(row) for row in quotes],
            "reservation": {
                key: reservation.get(key) for key in (
                    "reservation_key", "idempotency_key", "asset_key", "amount",
                    "status", "consumed_amount", "released_amount",
                )
            },
            "positions": [dict(row) for row in stable_positions],
            "funds": [dict(row) for row in funds],
        }
        return self._canonical(first), self._canonical(second)

    async def assert_active_fence(
        self,
        uow: UnitOfWork,
        *,
        account_id: int,
        owner: str,
        fencing_token: int,
        lease_role: str = "EXECUTION",
    ) -> dict[str, Any]:
        """Hold the exact active lease row for the rest of the transaction."""
        if not owner:
            raise StaleFenceError("stale_fence_rejected")
        lease = await self._execution.get_active_lease_fence(
            uow.session,
            account_id=account_id,
            lease_role=lease_role,
            owner=owner,
            fencing_token=fencing_token,
            for_update=True,
        )
        if lease is None:
            raise StaleFenceError("stale_fence_rejected")
        return lease

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
        self, uow: UnitOfWork, *, input_: EnvelopeInput, owner: str,
    ) -> dict[str, Any]:
        await self.assert_active_fence(
            uow,
            account_id=input_.account_id,
            owner=owner,
            fencing_token=input_.fencing_token,
        )
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
        if release.get("execution_spec_version_id") != input_.execution_spec_version_id:
            raise RuntimeError("envelope_release_execution_spec_mismatch")
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
        roles_hook = getattr(self._execution, "intent_leg_roles", None)
        if callable(roles_hook):
            leg_roles = list(await roles_hook(uow.session, intent_id=input_.intent_id))
        else:
            leg_roles = list((
                await uow.session.execute(
                    text(
                        "SELECT leg.leg_role FROM trading.economic_action_intents i "
                        "JOIN trading.action_set_legs leg ON leg.action_set_id=i.action_set_id "
                        "WHERE i.id=:intent ORDER BY leg.id"
                    ),
                    {"intent": input_.intent_id},
                )
            ).scalars().all())
        if "open" in leg_roles and (
            permission.get("kill_switch")
            or self._decimal(permission["authorized_capital"]) == 0
        ):
            raise KillSwitchBlocked("exposure_increasing_envelope_blocked")
        if input_.authority != "FAKE_CONFORMANCE":
            raise RuntimeError("envelope_authority_invalid")
        preflight_hash1, preflight_hash2 = await self.authoritative_preflight_hashes(
            uow,
            intent_id=input_.intent_id,
            account_id=input_.account_id,
            release_manifest_id=input_.release_manifest_id,
            execution_spec_version_id=input_.execution_spec_version_id,
            capital_permission_manifest_id=input_.capital_permission_manifest_id,
            fencing_token=input_.fencing_token,
        )
        if input_.preflight_hash1 != preflight_hash1:
            raise RuntimeError("envelope_preflight_hash1_mismatch")
        if input_.preflight_hash2 != preflight_hash2:
            raise RuntimeError("envelope_preflight_hash2_mismatch")
        envelope_hash = self._canonical({
            "schema": "execution-authorization-envelope/v2",
            "algorithm_code_hash": EXECUTION_AUTHORIZATION_HASH_ALGORITHM_CODE_HASH,
            "envelope_key": input_.envelope_key,
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

    async def preflight_submit(
        self,
        uow: UnitOfWork,
        *,
        input_: SubmitOrderInput,
        owner: str,
    ) -> dict[str, Any]:
        """Authoritative, fenced preflight used both before signing and in prepare TX."""
        await self.assert_active_fence(
            uow,
            account_id=input_.account_id,
            owner=owner,
            fencing_token=input_.fencing_token,
        )
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
        # Recompute the complete immutable envelope binding, including both preflight
        # hashes.  A caller/row that substitutes any hash or lineage is rejected.
        expected_envelope_hash = self._canonical({
            "schema": "execution-authorization-envelope/v2",
            "algorithm_code_hash": EXECUTION_AUTHORIZATION_HASH_ALGORITHM_CODE_HASH,
            "envelope_key": envelope["envelope_key"],
            "authority": envelope["authority"],
            "idempotency_key": envelope["idempotency_key"],
            "fencing_token": envelope["fencing_token"],
            "intent_hash": envelope["intent_hash"],
            "preflight_hash1": envelope["preflight_hash1"],
            "preflight_hash2": envelope["preflight_hash2"],
        })
        if envelope.get("envelope_hash") != expected_envelope_hash:
            raise RuntimeError("submit_envelope_hash_mismatch")
        current_hash1, current_hash2 = await self.authoritative_preflight_hashes(
            uow,
            intent_id=envelope["intent_id"],
            account_id=envelope["account_id"],
            release_manifest_id=envelope["release_manifest_id"],
            execution_spec_version_id=envelope["execution_spec_version_id"],
            capital_permission_manifest_id=envelope["capital_permission_manifest_id"],
            fencing_token=envelope["fencing_token"],
        )
        if current_hash1 != envelope["preflight_hash1"]:
            raise RuntimeError("submit_preflight_hash1_drift")
        if current_hash2 != envelope["preflight_hash2"]:
            raise RuntimeError("submit_preflight_hash2_drift")
        intent = await self._execution.get_intent(
            uow.session, intent_id=envelope["intent_id"]
        )
        if intent is None or intent.get("status") != "COMMITTED":
            raise RuntimeError("submit_intent_not_committed")
        if intent.get("intent_hash") != envelope["intent_hash"]:
            raise RuntimeError("submit_intent_hash_mismatch")
        account = await self._execution.get_account(
            uow.session, account_id=input_.account_id
        )
        if account is None or account.get("status", "active") != "active":
            raise RuntimeError("submit_account_inactive")
        if account["capital_permission_manifest_id"] != envelope[
            "capital_permission_manifest_id"
        ]:
            raise RuntimeError("submit_permission_lineage_mismatch")
        permission = await self._execution.get_permission(
            uow.session, permission_id=account["capital_permission_manifest_id"]
        )
        if permission is None or permission.get("status") != "active":
            raise RuntimeError("submit_permission_missing")
        leg = await self._execution.resolve_intent_leg(
            uow.session,
            intent_id=envelope["intent_id"],
            external_token_id=input_.token_id,
        )
        if leg is None:
            raise RuntimeError("submit_intent_leg_missing")
        role = leg.get("leg_role")
        if input_.side == "BUY":
            if role != "open":
                raise RuntimeError("submit_side_leg_role_mismatch")
            if permission.get("kill_switch"):
                raise KillSwitchBlocked("exposure_increasing_blocked_kill_switch")
            if self._decimal(permission["authorized_capital"]) == 0:
                raise KillSwitchBlocked("exposure_increasing_blocked_zero_capital")
        elif role not in ("reduce", "close"):
            raise RuntimeError("submit_side_leg_role_mismatch")
        if self._decimal(input_.size) > self._decimal(leg.get("leg_quantity", 0)):
            raise RuntimeError("submit_size_exceeds_intent_leg")
        market_hook = getattr(self._execution, "get_submit_market_material", None)
        if callable(market_hook):
            market = await market_hook(
                uow.session,
                execution_spec_version_id=envelope["execution_spec_version_id"],
                trade_decision_id=leg["trade_decision_id"],
                token_id=input_.token_id,
            )
        else:
            market = (
                await uow.session.execute(
                    text(
                        "SELECT qb.best_bid, qb.best_ask, qb.stale_at, qb.checkpoint_id, "
                        "bc.tick_size, bc.min_order_size, bc.validity, bc.observed_at, "
                        "m.neg_risk, m.gamma_market_id, m.condition_id, "
                        "m.content_hash AS market_content_hash, "
                        "es.status AS execution_spec_status, es.content_hash AS execution_spec_hash, "
                        "es.content AS execution_spec_content "
                        "FROM trading.pm_quote_bindings qb "
                        "JOIN trading.pm_book_current bc ON bc.token_id=qb.token_id "
                        "JOIN trading.pm_tokens pt ON pt.token_id=qb.token_id "
                        "JOIN trading.pm_markets m ON m.id=pt.market_id "
                    "JOIN trading.execution_spec_versions es ON es.id=:spec "
                    "WHERE qb.trade_decision_id=:decision AND qb.token_id=:token "
                    "ORDER BY qb.id DESC LIMIT 1 FOR UPDATE OF bc"
                    ),
                    {
                    "spec": envelope["execution_spec_version_id"],
                    "decision": leg["trade_decision_id"],
                    "token": input_.token_id,
                    },
                )
            ).mappings().one_or_none()
        if market is None:
            raise RuntimeError("submit_quote_material_missing")
        if market.get("neg_risk") is None:
            raise RuntimeError("submit_market_neg_risk_missing")
        expected_exchange_address = POLYMARKET_EXCHANGE_BY_NEG_RISK[
            bool(market["neg_risk"])
        ]
        if market["execution_spec_status"] != "active":
            raise RuntimeError("submit_execution_spec_inactive")
        now = datetime.now(timezone.utc)
        if market["validity"] != "VALID" or market["stale_at"] <= now:
            raise RuntimeError("submit_book_stale")
        observed_at = market["observed_at"]
        max_quote_age = self._decimal(
            ((market["execution_spec_content"] or {}).get("staleness") or {}).get(
                "max_quote_age_seconds", 300
            )
        )
        age_seconds = Decimal(str((now - observed_at).total_seconds()))
        if age_seconds < Decimal("-0.5") or age_seconds > max_quote_age:
            raise RuntimeError("submit_book_observation_stale")
        tick = self._decimal(market["tick_size"])
        minimum = self._decimal(market["min_order_size"])
        if tick <= 0 or minimum <= 0:
            raise RuntimeError("submit_market_constraints_missing")
        if self._decimal(input_.size) < minimum:
            raise RuntimeError("submit_size_below_minimum")
        if self._decimal(input_.price) % tick != 0:
            raise RuntimeError("submit_price_off_tick")
        accepted_price_value = leg.get("entry_vwap")
        if accepted_price_value is None:
            accepted_price_value = (
                await uow.session.execute(
                    text("SELECT entry_vwap FROM trading.action_set_legs WHERE id=:leg"),
                    {"leg": leg["leg_id"]},
                )
            ).scalar_one()
        accepted_price = self._decimal(accepted_price_value)
        if input_.side == "SELL" and self._decimal(input_.price) < accepted_price:
            raise RuntimeError("submit_sell_price_below_intent")
        if input_.side == "BUY" and self._decimal(input_.price) > accepted_price:
            raise RuntimeError("submit_buy_price_above_intent")
        reservation = await self._execution.get_reservation_by_intent(
            uow.session,
            account_id=input_.account_id,
            intent_id=envelope["intent_id"],
            for_update=True,
        )
        if reservation is None or reservation["status"] not in ("HELD", "UNKNOWN"):
            raise RuntimeError("submit_reservation_missing")
        expected_asset = (
            str(leg.get("cash_asset_key") or "USD").lower()
            if input_.side == "BUY"
            else f"tok:{leg['contract_spec_id']}:{leg['internal_token_id']}"
        )
        if str(reservation["asset_key"]).lower() != expected_asset.lower():
            raise RuntimeError("submit_reservation_asset_mismatch")
        remaining = (
            self._decimal(reservation["amount"])
            - self._decimal(reservation.get("consumed_amount", 0))
            - self._decimal(reservation.get("released_amount", 0))
        )
        if remaining <= 0:
            raise RuntimeError("submit_reservation_exhausted")
        if input_.side == "SELL":
            required_reservation = self._decimal(input_.size)
        else:
            gross = round_cash(self._decimal(input_.size) * self._decimal(input_.price))
            fee_bps = self._decimal(
                ((market["execution_spec_content"] or {}).get("fee") or {}).get(
                    "taker_fee_bps", 0
                )
            )
            required_reservation = gross + round_cash(gross * fee_bps / Decimal("10000"))
        if remaining < required_reservation:
            raise RuntimeError("submit_reservation_insufficient")
        if input_.side == "SELL":
            position = await self._execution.get_position(
                uow.session,
                portfolio_namespace=f"exec-{input_.account_id}",
                contract_spec_id=leg["contract_spec_id"],
                token_id=leg["internal_token_id"],
                for_update=True,
            )
            if position is None or self._decimal(position["quantity"]) < self._decimal(input_.size):
                raise RuntimeError("submit_reduce_position_insufficient")
        if role == "open":
            if await self._execution.has_active_reconciliation(
                uow.session, account_id=input_.account_id
            ):
                raise RuntimeError("submit_blocked_active_reconciliation")
            if await self._execution.list_orders_for_account(
                uow.session, account_id=input_.account_id, status="UNKNOWN"
            ):
                raise RuntimeError("submit_blocked_unknown_order")
        return {
            "envelope": envelope,
            "intent": intent,
            "account": account,
            "permission": permission,
            "leg": leg,
            "reservation": reservation,
            "market_neg_risk": bool(market["neg_risk"]),
            "expected_exchange_address": expected_exchange_address,
            "preflight_hash": self._canonical({
                "envelope_hash": expected_envelope_hash,
                "token_id": input_.token_id,
                "side": input_.side,
                "price": str(input_.price),
                "size": str(input_.size),
                "reservation_key": reservation["reservation_key"],
                "reservation_idempotency_key": reservation["idempotency_key"],
                "reservation_remaining": str(remaining),
            }),
        }

    async def prepare_submit(
        self,
        uow: UnitOfWork,
        *,
        input_: SubmitOrderInput,
        owner: str,
        signed_order: Any,
        body_hash: str,
        expected_order_hash: str,
        sdk_manifest_hash: str,
    ) -> PreparedSubmit:
        material = await self.preflight_submit(uow, input_=input_, owner=owner)
        envelope = material["envelope"]
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
            owner=owner,
            fencing_token=input_.fencing_token, body_hash=body_hash,
            expected_order_hash=expected_order_hash, sdk_manifest_hash=sdk_manifest_hash,
            salt=salt, timestamp=timestamp,
            exposure_increasing=material["leg"].get("leg_role") == "open",
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
        await self.assert_active_fence(
            uow,
            account_id=prepared.account_id,
            owner=prepared.owner,
            fencing_token=prepared.fencing_token,
        )
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
        if order["status"] != "OPEN":
            if cls == "ACK" and order["status"] == "UNKNOWN":
                if not external_order_id:
                    raise RuntimeError("submit_late_ack_order_id_missing")
                if order.get("external_order_id") not in (None, external_order_id):
                    raise RuntimeError("submit_late_ack_order_id_mismatch")
                if not await self._execution.advance_order(
                    uow.session,
                    order_id=prepared.order_id,
                    new_status="UNKNOWN",
                    external_order_id=external_order_id,
                    expected_status="UNKNOWN",
                ):
                    raise RuntimeError("submit_late_ack_bind_conflict")
                await self._ack_reservation(uow, prepared)
                result = SubmitApplyResult(
                    True,
                    "UNKNOWN",
                    external_order_id,
                    reason="late_ack_requires_reconcile",
                )
            elif cls == "REJECTED" and order["status"] == "UNKNOWN":
                if not await self._execution.advance_order(
                    uow.session,
                    order_id=prepared.order_id,
                    new_status="RECONCILED",
                    filled_size=order.get("filled_size"),
                    expected_status="UNKNOWN",
                ):
                    raise RuntimeError("submit_late_reject_reconcile_conflict")
                await self._execution.insert_order_state_event(
                    uow.session,
                    event_key=f"ev:{prepared.order_id}:reconciled:submit_rejected",
                    order_id=prepared.order_id,
                    event_type="RECONCILED",
                    transition_from="UNKNOWN",
                    transition_to="RECONCILED",
                    event_payload={"resolution": "REJECTED"},
                    event_hash=self._canonical({
                        "order_id": prepared.order_id,
                        "event_type": "RECONCILED",
                        "resolution": "REJECTED",
                    }),
                    fence_token=prepared.fencing_token,
                )
                await self._release_reservation(uow, prepared)
                result = SubmitApplyResult(True, "RECONCILED", None)
            else:
                if (
                    cls == "ACK"
                    and order.get("external_order_id")
                    and external_order_id
                    and order["external_order_id"] != external_order_id
                ):
                    raise RuntimeError("submit_duplicate_ack_order_id_mismatch")
                await self._workflow_event(
                    uow,
                    event_key=(
                        f"wf:order:{prepared.order_id}:late_submit:"
                        f"{str(cls).lower()}:{str(order['status']).lower()}"
                    ),
                    event_type="order.submit.late_evidence",
                    aggregate_type="order",
                    aggregate_id=str(prepared.order_id),
                    payload={
                        "attempt_id": prepared.attempt_id,
                        "result": cls,
                        "current_status": order["status"],
                    },
                )
                result = SubmitApplyResult(
                    True,
                    order["status"],
                    order.get("external_order_id") or external_order_id,
                    replayed=True,
                )
        elif cls == "ACK":
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
                result = SubmitApplyResult(True, "ACK", external_order_id, replayed=True)
            else:
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
        status = reservation["status"]
        if status not in ("HELD", "UNKNOWN"):
            return
        remaining = max(
            Decimal("0"),
            self._decimal(reservation["amount"])
            - self._decimal(reservation.get("consumed_amount", 0))
            - self._decimal(reservation.get("released_amount", 0)),
        )
        if remaining and not await self._execution.transfer_funds_local_to_provider(
            uow.session,
            account_id=prepared.account_id,
            asset_key=reservation["asset_key"],
            amount=remaining,
        ):
            raise RuntimeError("funds_transfer_conflict")
        if not await self._execution.advance_reservation(
            uow.session,
            reservation_id=reservation["id"],
            new_status="PROVIDER_BOUND",
            expected_status=status,
        ):
            raise RuntimeError("reservation_advance_conflict")

    async def _release_reservation(self, uow: UnitOfWork, prepared: PreparedSubmit) -> None:
        reservation = await self._execution.get_reservation_by_intent(
            uow.session, account_id=prepared.account_id, intent_id=prepared.intent_id,
            for_update=True,
        )
        if reservation is None:
            return
        remaining = max(
            Decimal("0"),
            self._decimal(reservation["amount"])
            - self._decimal(reservation.get("consumed_amount", 0))
            - self._decimal(reservation.get("released_amount", 0)),
        )
        if remaining == 0:
            return
        if reservation["status"] in ("HELD", "UNKNOWN"):
            updated = await self._execution.release_funds_local(
                uow.session, account_id=prepared.account_id, asset_key=reservation["asset_key"],
                amount=remaining,
            )
        elif reservation["status"] == "PROVIDER_BOUND":
            updated = await self._execution.release_funds_provider(
                uow.session, account_id=prepared.account_id, asset_key=reservation["asset_key"],
                amount=remaining,
            )
        else:
            return
        if not updated:
            raise RuntimeError("funds_release_conflict")
        if not await self._execution.advance_reservation(
            uow.session,
            reservation_id=reservation["id"],
            new_status="RELEASED",
            released_delta=remaining,
            expected_status=reservation["status"],
        ):
            raise RuntimeError("reservation_advance_conflict")

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
                uow.session,
                reservation_id=reservation["id"],
                new_status="UNKNOWN",
                expected_status="HELD",
            )

    # ---- cancel ----

    async def prepare_cancel(
        self,
        uow: UnitOfWork,
        *,
        input_: CancelOrderInput,
        owner: str,
    ) -> PreparedCancel | SubmitApplyResult:
        """Fenced DB precommit.  The caller must commit before provider I/O."""
        await self.assert_active_fence(
            uow,
            account_id=input_.account_id,
            owner=owner,
            fencing_token=input_.fencing_token,
        )
        order = await self._execution.get_order_by_external(
            uow.session, account_id=input_.account_id,
            external_order_id=input_.external_order_id, for_update=True,
        )
        if order is None:
            raise RuntimeError("cancel_order_missing")
        if order["status"] in ("FILLED", "CANCELLED", "RECONCILED", "REJECTED"):
            return SubmitApplyResult(
                True, order["status"], order["external_order_id"], replayed=True
            )
        intent_id = (
            await uow.session.execute(
                text(
                    "SELECT e.intent_id FROM trading.exchange_order_attempts a "
                    "JOIN trading.execution_authorization_envelopes e ON e.id=a.envelope_id "
                    "WHERE a.id=:attempt"
                ),
                {"attempt": order["attempt_id"]},
            )
        ).scalar_one()
        await self._workflow_event(
            uow,
            event_key=f"wf:order:{order['id']}:cancel_prepared",
            event_type="order.cancel.prepared",
            aggregate_type="order",
            aggregate_id=str(order["id"]),
            payload={"external_order_id": input_.external_order_id},
        )
        return PreparedCancel(
            order_id=order["id"],
            account_id=input_.account_id,
            intent_id=int(intent_id),
            owner=owner,
            fencing_token=input_.fencing_token,
            external_order_id=input_.external_order_id,
            expected_status=order["status"],
        )

    @staticmethod
    def _cancel_was_confirmed(outcome: Any, external_order_id: str) -> bool:
        if outcome is None:
            return False
        items = getattr(outcome, "items", None)
        if items is not None:
            return any(
                str(getattr(item, "order_id", "")) == external_order_id
                and bool(getattr(item, "ok", False))
                for item in items
            )
        cancelled = getattr(outcome, "canceled", None)
        if cancelled is None and isinstance(outcome, dict):
            cancelled = outcome.get("canceled")
        return external_order_id in set(cancelled or ())

    async def apply_cancel_outcome(
        self,
        uow: UnitOfWork,
        *,
        prepared: PreparedCancel,
        outcome: Any,
        response_hash: str | None = None,
        error_reason: str | None = None,
    ) -> SubmitApplyResult:
        """Apply exactly one provider item; failed/absent/indeterminate means UNKNOWN."""
        await self.assert_active_fence(
            uow,
            account_id=prepared.account_id,
            owner=prepared.owner,
            fencing_token=prepared.fencing_token,
        )
        order = await self._execution.get_order(
            uow.session, order_id=prepared.order_id, for_update=True
        )
        if order is None:
            raise RuntimeError("cancel_order_missing")
        if order["status"] in ("FILLED", "CANCELLED", "RECONCILED", "REJECTED"):
            return SubmitApplyResult(True, order["status"], order["external_order_id"], replayed=True)
        confirmed = self._cancel_was_confirmed(outcome, prepared.external_order_id)
        # UNKNOWN has one legal exit: authoritative reconciliation.  A confirmed
        # provider cancellation is terminal proof, so record UNKNOWN->RECONCILED;
        # an indeterminate cancellation keeps UNKNOWN without inventing an illegal
        # UNKNOWN->UNKNOWN state event.
        if order["status"] == "UNKNOWN" and not confirmed:
            await self._alert(
                uow,
                severity="ERROR",
                code="cancel_unknown_hard_stop",
                message_redacted="cancel outcome indeterminate; reservation retained; hard stop",
                alert_key=f"alert:{order['id']}:cancel_unknown",
            )
            await self._record_external_call(
                uow, driver="clob_trading", endpoint="DELETE /order", method="DELETE",
                request_hash=self._canonical({"cancel": prepared.external_order_id}),
                response_hash=response_hash, status_code=None, latency_ms=0,
                fence_token=prepared.fencing_token, error_reason=error_reason,
            )
            return SubmitApplyResult(True, "UNKNOWN", order["external_order_id"])
        target = (
            "RECONCILED"
            if confirmed and order["status"] == "UNKNOWN"
            else "CANCELLED" if confirmed else "UNKNOWN"
        )
        if not await self._execution.advance_order(
            uow.session, order_id=order["id"], new_status=target,
            expected_status=order["status"],
        ):
            return SubmitApplyResult(False, None, reason="cancel_transition_conflict")
        await self._execution.insert_order_state_event(
            uow.session,
            event_key=f"ev:{order['id']}:cancel:{target.lower()}",
            order_id=order["id"],
            event_type=target,
            transition_from=order["status"],
            transition_to=target,
            event_payload={
                "order_id": prepared.external_order_id,
                "resolution": "CANCELLED" if target == "RECONCILED" else target,
                "reason": error_reason if not confirmed else None,
            },
            event_hash=self._canonical({
                "order_id": order["id"], "event_type": target,
                "external_order_id": prepared.external_order_id,
            }),
            fence_token=prepared.fencing_token,
        )
        if confirmed:
            await self._release_order_remainder(uow, prepared)
        else:
            await self._alert(
                uow,
                severity="ERROR",
                code="cancel_unknown_hard_stop",
                message_redacted="cancel outcome indeterminate; reservation retained; hard stop",
                alert_key=f"alert:{order['id']}:cancel_unknown",
            )
        await self._record_external_call(
            uow, driver="clob_trading", endpoint="DELETE /order", method="DELETE",
            request_hash=self._canonical({"cancel": prepared.external_order_id}),
            response_hash=response_hash, status_code=None, latency_ms=0,
            fence_token=prepared.fencing_token, error_reason=error_reason,
        )
        return SubmitApplyResult(True, target, order["external_order_id"])

    async def _release_order_remainder(
        self, uow: UnitOfWork, prepared: PreparedCancel
    ) -> None:
        reservation = await self._execution.get_reservation_by_intent(
            uow.session,
            account_id=prepared.account_id,
            intent_id=prepared.intent_id,
            for_update=True,
        )
        if reservation is None or reservation["status"] in ("CONSUMED", "RELEASED"):
            return
        amount = self._decimal(reservation["amount"])
        remaining = max(
            Decimal("0"),
            amount
            - self._decimal(reservation.get("consumed_amount", 0))
            - self._decimal(reservation.get("released_amount", 0)),
        )
        if remaining == 0:
            return
        status = reservation["status"]
        if status == "PROVIDER_BOUND":
            updated = await self._execution.release_funds_provider(
                uow.session,
                account_id=prepared.account_id,
                asset_key=reservation["asset_key"],
                amount=remaining,
            )
        elif status in ("HELD", "UNKNOWN"):
            updated = await self._execution.release_funds_local(
                uow.session,
                account_id=prepared.account_id,
                asset_key=reservation["asset_key"],
                amount=remaining,
            )
        else:
            return
        if not updated:
            raise RuntimeError("funds_release_conflict")
        if not await self._execution.advance_reservation(
            uow.session,
            reservation_id=reservation["id"],
            new_status="RELEASED",
            released_delta=remaining,
            expected_status=status,
        ):
            raise RuntimeError("reservation_advance_conflict")

    async def cancel_order(
        self,
        uow: UnitOfWork,
        *,
        input_: CancelOrderInput,
        owner: str,
        outcome: Any,
        response_hash: str | None = None,
        error_reason: str | None = None,
    ) -> SubmitApplyResult:
        """Compatibility DB-only apply; runtimes must use prepare/apply across I/O."""
        prepared = await self.prepare_cancel(uow, input_=input_, owner=owner)
        if isinstance(prepared, SubmitApplyResult):
            return prepared
        return await self.apply_cancel_outcome(
            uow,
            prepared=prepared,
            outcome=outcome,
            response_hash=response_hash,
            error_reason=error_reason,
        )

    # ---- persisted-before-send recovery ----

    async def recover_submitted_attempts(
        self,
        uow: UnitOfWork,
        *,
        account_id: int,
        owner: str,
        fencing_token: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Convert orphaned SUBMITTED attempts to UNKNOWN without provider resend."""
        await self.assert_active_fence(
            uow,
            account_id=account_id,
            owner=owner,
            fencing_token=fencing_token,
        )
        attempts = await self._execution.list_submitted_attempts_for_recovery(
            uow.session, account_id=account_id, limit=limit, for_update=True
        )
        recovered: list[dict[str, Any]] = []
        for attempt in attempts:
            order_id = attempt.get("order_id")
            if order_id is None:
                # Persistence invariant breach: retain attempt as UNKNOWN and reconcile.
                if not await self._execution.advance_attempt_result(
                    uow.session, attempt_id=attempt["id"], result="UNKNOWN"
                ):
                    raise RuntimeError("recovery_attempt_transition_conflict")
                recovered.append({
                    "attempt_id": attempt["id"],
                    "order_id": None,
                    "expected_order_hash": attempt["expected_order_hash"],
                })
                continue
            order = await self._execution.get_order(
                uow.session, order_id=order_id, for_update=True
            )
            if order is None:
                raise RuntimeError("recovery_order_missing")
            if order["status"] == "OPEN":
                if not await self._execution.advance_order(
                    uow.session,
                    order_id=order_id,
                    new_status="UNKNOWN",
                    expected_status="OPEN",
                ):
                    raise RuntimeError("recovery_order_transition_conflict")
                event = await self._execution.insert_order_state_event(
                    uow.session,
                    event_key=f"ev:{order_id}:recovery_unknown:{attempt['id']}",
                    order_id=order_id,
                    event_type="UNKNOWN",
                    transition_from="SUBMITTED",
                    transition_to="UNKNOWN",
                    event_payload={
                        "reason": "persisted_submitted_recovery",
                        "expected_order_hash": attempt["expected_order_hash"],
                    },
                    event_hash=self._canonical({
                        "order_id": order_id,
                        "attempt_id": attempt["id"],
                        "event_type": "UNKNOWN",
                    }),
                    fence_token=fencing_token,
                )
                del event
            if not await self._execution.advance_attempt_result(
                uow.session,
                attempt_id=attempt["id"],
                result="UNKNOWN",
            ):
                raise RuntimeError("recovery_attempt_transition_conflict")
            envelope = await self._execution.get_envelope(
                uow.session, envelope_id=attempt["envelope_id"]
            )
            if envelope is not None:
                prepared = PreparedSubmit(
                    attempt_id=attempt["id"],
                    order_id=order_id,
                    envelope_id=attempt["envelope_id"],
                    account_id=account_id,
                    intent_id=envelope["intent_id"],
                    owner=owner,
                    fencing_token=fencing_token,
                    body_hash=attempt["body_hash"],
                    expected_order_hash=attempt["expected_order_hash"],
                    sdk_manifest_hash=attempt["sdk_manifest_hash"],
                    salt=int(attempt["salt"]),
                    timestamp=int(attempt["timestamp"]),
                )
                await self._unknown_reservation(uow, prepared)
            recovered.append({
                "attempt_id": attempt["id"],
                "order_id": order_id,
                "expected_order_hash": attempt["expected_order_hash"],
            })
        return recovered

    # ---- fill ----

    async def apply_fill(
        self,
        uow: UnitOfWork,
        *,
        order_id: int,
        account_id: int,
        envelope_id: int,
        intent_id: int,
        owner: str,
        fencing_token: int,
        external_trade_id: str,
        side: str,
        price: Any,
        size: Any,
        fee: Any,
        trade_time: datetime,
        trade_status: str | None = None,
    ) -> FillApplyResult:
        await self.assert_active_fence(
            uow,
            account_id=account_id,
            owner=owner,
            fencing_token=fencing_token,
        )
        order = await self._execution.get_order(
            uow.session, order_id=order_id, for_update=True
        )
        if order is None:
            raise RuntimeError("fill_order_missing")
        if order["account_id"] != account_id:
            raise RuntimeError("fill_cross_account_reference")
        status = str(trade_status or "").upper()
        allowed_trade_statuses = {
            "MATCHED", "MINED", "CONFIRMED", "RETRYING", "FAILED",
        }
        if status and status not in allowed_trade_statuses:
            raise RuntimeError("fill_trade_status_invalid")
        evidence_status = status or "MISSING"
        await self._workflow_event(
            uow,
            event_key=(
                f"wf:order:{order_id}:trade-observation:"
                f"{external_trade_id}:{evidence_status.lower()}"
            ),
            event_type=f"order.trade.{evidence_status.lower()}",
            aggregate_type="order",
            aggregate_id=str(order_id),
            payload={
                "external_trade_id": external_trade_id,
                "provider_status": status or None,
                "side": side,
                "price": str(price),
                "size": str(size),
                "trade_time": trade_time.astimezone(timezone.utc).isoformat(),
            },
        )
        # MATCHED/MINED/RETRYING are provider lifecycle evidence, not final
        # economic facts.  Missing status is equally fail-closed.  In particular,
        # do not claim exchange_trades here: a later CONFIRMED event must still be
        # able to insert the immutable trade and apply its economics exactly once.
        if status != "CONFIRMED":
            reason = (
                "trade_status_missing_reconcile_required"
                if not status
                else f"trade_{status.lower()}_evidence_only"
            )
            if status == "FAILED":
                prior = (
                    await uow.session.execute(
                        text(
                            "SELECT id FROM trading.exchange_trades "
                            "WHERE account_id=:account AND external_trade_id=:trade "
                            "FOR SHARE"
                        ),
                        {"account": account_id, "trade": external_trade_id},
                    )
                ).scalar_one_or_none()
                if prior is not None:
                    # The present schema makes reservation consumption monotonic and
                    # does not retain the pre-fill cost-basis delta.  An automatic
                    # guessed reversal would corrupt capital.  Persist a hard stop and
                    # require authoritative reconciliation instead.
                    await self._alert(
                        uow,
                        severity="CRITICAL",
                        code="confirmed_trade_later_failed",
                        message_redacted=(
                            "provider marked an economically applied trade failed; "
                            "exact reconciliation required"
                        ),
                        alert_key=f"alert:trade:{account_id}:{external_trade_id}:failed",
                    )
                    reason = "confirmed_trade_failed_reconcile_required"
            return FillApplyResult(
                status in {"MATCHED", "MINED", "RETRYING"},
                None,
                order["status"],
                reason=reason,
            )
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
        provenance_only_statuses = {"UNKNOWN", "CANCELLED", "RECONCILED"}
        if order["status"] in provenance_only_statuses:
            all_trades = await self._execution.get_trades_for_order(
                uow.session, order_id=order_id
            )
            new_filled = sum(
                (self._decimal(row["size"]) for row in all_trades), Decimal("0")
            )
        else:
            old_filled = self._decimal(order["filled_size"])
            new_filled = old_filled + fill_size
        order_size = self._decimal(order["size"])
        if new_filled > order_size:
            raise RuntimeError("fill_exceeds_order_size")
        new_status = "FILLED" if new_filled >= order_size else "PARTIAL"
        projection_status = (
            order["status"] if order["status"] in provenance_only_statuses else new_status
        )
        if order["status"] not in provenance_only_statuses:
            if not await self._execution.advance_order(
                uow.session, order_id=order_id, new_status=projection_status,
                filled_size=new_filled, expected_status=order["status"],
            ):
                raise RuntimeError("fill_order_transition_conflict")
            await self._execution.insert_order_state_event(
                uow.session,
                event_key=f"ev:{order_id}:{new_status.lower()}:{external_trade_id}",
                order_id=order_id,
                event_type=new_status,
                transition_from=order["status"],
                transition_to=projection_status,
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
        if reservation is not None and reservation["status"] in (
            "HELD", "UNKNOWN", "PROVIDER_BOUND"
        ):
            reservation_status = reservation["status"]
            reservation_amount = self._decimal(reservation["amount"])
            already_consumed = self._decimal(reservation.get("consumed_amount", 0))
            already_released = self._decimal(reservation.get("released_amount", 0))
            remaining = max(
                Decimal("0"),
                reservation_amount - already_consumed - already_released,
            )
            # Consume the actual fill capital, never the whole reservation merely because
            # a partial fill arrived.  Any conservative headroom is released at terminal.
            actual_capital = (
                max(
                    Decimal("0"),
                    round_cash(fill_size * self._decimal(price)) + self._decimal(fee),
                )
                if side_lower == "buy"
                else fill_size
            )
            consumed_delta = min(remaining, actual_capital)
            if consumed_delta:
                if reservation_status == "PROVIDER_BOUND":
                    consumed = await self._execution.consume_funds_provider(
                        uow.session,
                        account_id=account_id,
                        asset_key=reservation["asset_key"],
                        amount=consumed_delta,
                    )
                else:
                    consumed = await self._execution.consume_funds_local(
                        uow.session,
                        account_id=account_id,
                        asset_key=reservation["asset_key"],
                        amount=consumed_delta,
                    )
                if not consumed:
                    raise RuntimeError("funds_consume_conflict")
            after_consume = remaining - consumed_delta
            released_delta = after_consume if new_status == "FILLED" else Decimal("0")
            if released_delta:
                if reservation_status == "PROVIDER_BOUND":
                    released = await self._execution.release_funds_provider(
                        uow.session,
                        account_id=account_id,
                        asset_key=reservation["asset_key"],
                        amount=released_delta,
                    )
                else:
                    released = await self._execution.release_funds_local(
                        uow.session,
                        account_id=account_id,
                        asset_key=reservation["asset_key"],
                        amount=released_delta,
                    )
                if not released:
                    raise RuntimeError("funds_release_conflict")
            reservation_target = "CONSUMED" if new_status == "FILLED" else reservation_status
            if consumed_delta or released_delta:
                if not await self._execution.advance_reservation(
                    uow.session,
                    reservation_id=reservation["id"],
                    new_status=reservation_target,
                    consumed_delta=consumed_delta,
                    released_delta=released_delta,
                    expected_status=reservation_status,
                ):
                    raise RuntimeError("reservation_advance_conflict")
        await self._workflow_event(
            uow, event_key=f"wf:order:{order_id}:fill:{external_trade_id}",
            event_type="order.fill", aggregate_type="order", aggregate_id=str(order_id),
            payload={"external_trade_id": external_trade_id, "size": str(fill_size),
                     "price": str(price), "status": new_status},
        )
        return FillApplyResult(True, trade["id"], projection_status)
