"""DB-backed G7B minimum-portfolio capacity checks."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.portfolio import cap_check


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
    """Derive positions + active intent claims while holding a transaction lock."""

    def __init__(self, execution: Any | None = None) -> None:
        # Kept for source compatibility; authoritative reads are performed here so
        # callers cannot supply projections.
        self._execution = execution

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
