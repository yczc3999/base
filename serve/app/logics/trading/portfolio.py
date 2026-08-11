"""DB-backed G7B minimum-portfolio capacity checks + WP-05 funds/reservation logic."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.portfolio import cap_check
from app.repositories.trading.execution import ExecutionRepository


ZERO = Decimal("0")


@dataclass(frozen=True)
class PortfolioExposure:
    ok: bool
    reason: str | None = None
    market_fraction: Decimal | None = None
    component_fraction: Decimal | None = None
    global_fraction: Decimal | None = None
    market_exposure: Decimal = ZERO
    component_exposure: Decimal = ZERO
    global_exposure: Decimal = ZERO
    bankroll: Decimal = ZERO
    per_market_cap: Decimal = Decimal("0.04")
    per_component_cap: Decimal = Decimal("0.06")
    global_cap: Decimal = Decimal("0.30")


class PortfolioLogic:
    """Derive positions + active intent claims while holding a transaction lock.

    WP-05 追加 funds/reservation 原子占用逻辑（决策 §12）：preflight 用条件 UPDATE 或
    ``SELECT ... FOR UPDATE`` 原子占用，禁止先查后写；HELD/UNKNOWN 计入 local reserved；
    ACK 后同一 UoW 把等额 provider reserve 纳入 current funds 才转 PROVIDER_BOUND。
    """

    def __init__(self, execution: Any | None = None) -> None:
        # Kept for source compatibility; authoritative reads are performed here so
        # callers cannot supply projections.
        self._execution = execution if execution is not None else ExecutionRepository()

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        return Decimal(str(value))

    async def acquire_capacity_lock(
        self,
        uow: UnitOfWork,
        *,
        portfolio_namespace: str,
        component_id: int | None,
        market_id: int | None,
    ) -> None:
        await uow.session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended("
                ":ns || ':' || coalesce(:cmp,'0') || ':' || coalesce(:mkt,'0'), 9913))"
            ),
            {"ns": portfolio_namespace, "cmp": str(component_id), "mkt": str(market_id)},
        )

    @staticmethod
    def frozen_caps(limits: dict[str, Any] | None) -> tuple[Decimal, Decimal, Decimal]:
        limits = limits or {}
        market = min(
            Decimal("0.04"),
            Decimal(str(limits.get("per_market_net_risk_capital_fraction", "0.04"))),
        )
        component = min(
            Decimal("0.06"),
            Decimal(str(limits.get("per_component_net_risk_capital_fraction", "0.06"))),
        )
        global_ = min(
            Decimal("0.30"),
            Decimal(str(limits.get("global_risk_capital_fraction", "0.30"))),
        )
        return market, component, global_

    async def check_capacity(
        self,
        uow: UnitOfWork,
        *,
        portfolio_namespace: str,
        bankroll: Decimal,
        new_market_exposure: Decimal,
        new_component_exposure: Decimal,
        new_global_exposure: Decimal,
        per_market_cap: Decimal = Decimal("0.04"),
        per_component_cap: Decimal = Decimal("0.06"),
        global_cap: Decimal = Decimal("0.30"),
        market_id: int | None = None,
        component_id: int | None = None,
        exclude_decision_id: int | None = None,
        lock: bool = True,
    ) -> PortfolioExposure:
        if lock:
            await self.acquire_capacity_lock(
                uow,
                portfolio_namespace=portfolio_namespace,
                component_id=component_id,
                market_id=market_id,
            )

        # Cost basis is the conservative current risk capital projection.  Active
        # COMMITTED intents are capacity claims until their economic effect exists.
        position_rows = (
            await uow.session.execute(
                text(
                    "SELECT market_id, component_id, "
                    "       CASE WHEN cost_basis > 0 THEN cost_basis ELSE abs(quantity) END AS exposure "
                    "FROM trading.positions WHERE portfolio_namespace=:ns FOR UPDATE"
                ),
                {"ns": portfolio_namespace},
            )
        ).mappings().all()
        claim_rows = (
            await uow.session.execute(
                text(
                    "SELECT pt.market_id, cv.component_id, "
                    "       abs(l.quantity * l.entry_vwap) AS exposure "
                    "FROM trading.economic_action_intents i "
                    "JOIN trading.trade_decisions td ON td.id=i.trade_decision_id "
                    "JOIN trading.forecast_episodes fe ON fe.id=td.episode_id "
                    "JOIN trading.forecast_component_versions cv ON cv.id=fe.component_version_id "
                    "JOIN trading.action_set_legs l ON l.action_set_id=i.action_set_id "
                    "JOIN trading.pm_tokens pt ON pt.id=l.token_id "
                    "WHERE i.status='COMMITTED' "
                    " AND i.preflight->>'portfolio_namespace'=:ns AND "
                    " (CAST(:exclude AS bigint) IS NULL OR td.id<>CAST(:exclude AS bigint)) "
                    " AND NOT EXISTS (SELECT 1 FROM trading.executions e "
                    "  WHERE e.economic_action_intent_id=i.id "
                    "    AND e.action_set_leg_id=l.id "
                    "    AND e.status IN ('PARTIAL','FILLED','REJECTED','FAILED'))"
                ),
                {"exclude": exclude_decision_id, "ns": portfolio_namespace},
            )
        ).mappings().all()

        market_exposure = ZERO
        component_exposure = ZERO
        global_exposure = ZERO
        for row in [*position_rows, *claim_rows]:
            exposure = abs(Decimal(str(row["exposure"] or 0)))
            global_exposure += exposure
            if component_id is not None and row["component_id"] == component_id:
                component_exposure += exposure
            if market_id is not None and row["market_id"] == market_id:
                market_exposure += exposure

        market_total = max(ZERO, market_exposure + new_market_exposure)
        component_total = max(ZERO, component_exposure + new_component_exposure)
        global_total = max(ZERO, global_exposure + new_global_exposure)
        check = cap_check(
            market_exposure=market_total,
            component_exposure=component_total,
            global_exposure=global_total,
            bankroll=bankroll,
            per_market_cap=per_market_cap,
            per_component_cap=per_component_cap,
            global_cap=global_cap,
        )
        return PortfolioExposure(
            check.ok,
            check.reason,
            check.per_market_fraction,
            check.per_component_fraction,
            check.global_fraction,
            market_total,
            component_total,
            global_total,
            bankroll,
            per_market_cap,
            per_component_cap,
            global_cap,
        )

    # ---- WP-05 funds / reservation（决策 §12：原子占用、无漏计/双计）----

    async def reserve_funds(
        self,
        uow: UnitOfWork,
        *,
        reservation_key: str,
        intent_id: int,
        account_id: int,
        asset_key: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """原子占用 local_reserved 并写入 HELD reservation。

        持 funds 行锁 → 幂等判定 → 条件 UPDATE 占用 → INSERT reservation；同一 UoW，
        crash 回滚无半条 reservation。两个并发 reservation 由行锁串行化，不越过可用额。
        """
        funds = await self._execution.get_funds(
            uow.session, account_id=account_id, asset_key=asset_key, for_update=True
        )
        if funds is None:
            raise RuntimeError("funds_projection_missing")
        existing = await self._execution.get_reservation_by_idempotency(
            uow.session, account_id=account_id, asset_key=asset_key,
            idempotency_key=idempotency_key, for_update=True,
        )
        if existing is not None:
            if existing["status"] in ("CONSUMED", "RELEASED"):
                raise RuntimeError("reservation_idempotency_terminal")
            return existing
        by_key = await self._execution.get_reservation_by_key(
            uow.session, reservation_key=reservation_key
        )
        if by_key is not None:
            raise RuntimeError("reservation_key_collision")
        if self._decimal(funds["available"]) < amount:
            raise RuntimeError("funds_insufficient")
        updated = await self._execution.reserve_funds_update(
            uow.session, account_id=account_id, asset_key=asset_key, amount=amount
        )
        if not updated:
            raise RuntimeError("funds_reserve_conflict")
        return await self._execution.insert_reservation(
            uow.session, reservation_key=reservation_key, intent_id=intent_id,
            account_id=account_id, asset_key=asset_key, amount=amount,
            idempotency_key=idempotency_key,
        )

    async def mark_reservation_unknown(self, uow: UnitOfWork, *, reservation_id: int) -> None:
        """HELD→UNKNOWN：提交结果不确定；资金仍计入 local reserved，不释放。"""
        res = await self._execution.get_reservation(
            uow.session, reservation_id=reservation_id, for_update=True
        )
        if res is None or res["status"] != "HELD":
            raise RuntimeError("reservation_not_held")
        if not await self._execution.advance_reservation(
            uow.session, reservation_id=reservation_id, new_status="UNKNOWN"
        ):
            raise RuntimeError("reservation_advance_conflict")

    async def ack_reservation(self, uow: UnitOfWork, *, reservation_id: int) -> None:
        """ACK：同一 UoW 把等额 local_reserved 转入 provider_reserved 才转 PROVIDER_BOUND。

        先 transfer（恒等式保持），再推进状态；任一失败整体回滚，无漏计/双计窗口。
        """
        res = await self._execution.get_reservation(
            uow.session, reservation_id=reservation_id, for_update=True
        )
        if res is None or res["status"] not in ("HELD", "UNKNOWN"):
            raise RuntimeError("reservation_not_ackable")
        amount = self._decimal(res["amount"])
        updated = await self._execution.transfer_funds_local_to_provider(
            uow.session, account_id=res["account_id"], asset_key=res["asset_key"], amount=amount
        )
        if not updated:
            raise RuntimeError("funds_transfer_conflict")
        if not await self._execution.advance_reservation(
            uow.session, reservation_id=reservation_id, new_status="PROVIDER_BOUND"
        ):
            raise RuntimeError("reservation_advance_conflict")

    async def release_reservation(self, uow: UnitOfWork, *, reservation_id: int) -> None:
        """HELD/PROVIDER_BOUND→RELEASED，精确释放等额保留；UNKNOWN 禁止直接 RELEASED。"""
        res = await self._execution.get_reservation(
            uow.session, reservation_id=reservation_id, for_update=True
        )
        if res is None:
            raise RuntimeError("reservation_missing")
        status = res["status"]
        if status == "CONSUMED":
            raise RuntimeError("reservation_already_consumed")
        if status == "UNKNOWN":
            raise RuntimeError("reservation_unknown_not_releasable")
        amount = self._decimal(res["amount"])
        if status == "HELD":
            updated = await self._execution.release_funds_local(
                uow.session, account_id=res["account_id"], asset_key=res["asset_key"],
                amount=amount,
            )
        elif status == "PROVIDER_BOUND":
            updated = await self._execution.release_funds_provider(
                uow.session, account_id=res["account_id"], asset_key=res["asset_key"],
                amount=amount,
            )
        else:
            raise RuntimeError("reservation_release_invalid")
        if not updated:
            raise RuntimeError("funds_release_conflict")
        if not await self._execution.advance_reservation(
            uow.session, reservation_id=reservation_id, new_status="RELEASED"
        ):
            raise RuntimeError("reservation_advance_conflict")

    async def consume_reservation(self, uow: UnitOfWork, *, reservation_id: int) -> None:
        """UNKNOWN/PROVIDER_BOUND→CONSUMED（FILLED）：按实际 quantity 精确消耗保留。"""
        res = await self._execution.get_reservation(
            uow.session, reservation_id=reservation_id, for_update=True
        )
        if res is None or res["status"] not in ("UNKNOWN", "PROVIDER_BOUND"):
            raise RuntimeError("reservation_not_consumable")
        amount = self._decimal(res["amount"])
        if res["status"] == "PROVIDER_BOUND":
            updated = await self._execution.release_funds_provider(
                uow.session, account_id=res["account_id"], asset_key=res["asset_key"],
                amount=amount,
            )
        else:  # UNKNOWN（资金仍记 local reserved）
            updated = await self._execution.release_funds_local(
                uow.session, account_id=res["account_id"], asset_key=res["asset_key"],
                amount=amount,
            )
        if not updated:
            raise RuntimeError("funds_consume_conflict")
        if not await self._execution.advance_reservation(
            uow.session, reservation_id=reservation_id, new_status="CONSUMED"
        ):
            raise RuntimeError("reservation_advance_conflict")
