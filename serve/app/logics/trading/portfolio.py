"""Portfolio Logic（WP-03 Checkpoint C）。

- 组合保险丝 4%/6%/30% 以 DB-backed 原子计算（SELECT ... FOR UPDATE / advisory lock）。
- 不同 shadow variant 使用独立 portfolio namespace，禁止合并 PnL/风险。
- exposure-increasing 候选须在并发下不得共同越限。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.portfolio import cap_check
from app.repositories.trading.execution import ExecutionRepository


@dataclass(frozen=True)
class PortfolioExposure:
    ok: bool
    reason: str | None = None
    market_fraction: Decimal | None = None
    component_fraction: Decimal | None = None
    global_fraction: Decimal | None = None


class PortfolioLogic:
    """组合保险丝；DB 原子，禁止先查后写。"""

    def __init__(
        self,
        execution: ExecutionRepository,
    ) -> None:
        self._execution = execution

    async def acquire_capacity_lock(
        self,
        uow: UnitOfWork,
        *,
        portfolio_namespace: str,
        component_id: int | None,
        market_id: int | None,
    ) -> None:
        """transaction-scoped advisory lock on (namespace, component, market)，固定顺序。"""
        await uow.session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended(:ns, 9913), "
                "hashtextextended(coalesce(:cmp,'0') || ':' || coalesce(:mkt,'0'), 9914))"
            ),
            {"ns": portfolio_namespace, "cmp": str(component_id), "mkt": str(market_id)},
        )

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
    ) -> PortfolioExposure:
        """DB-backed cap check：以当前 positions 净敞口 + 候选新增，并发下共同不越限。"""
        positions = await self._execution.positions_for_namespace(
            uow.session, portfolio_namespace
        )
        market_exposure = Decimal("0")
        component_exposure = Decimal("0")
        global_exposure = Decimal("0")
        for position in positions:
            # 净敞口 = quantity × 参考价（base-unit）；此处用 quantity 绝对值聚合。
            exposure = abs(position["quantity"])
            global_exposure += exposure
            component_exposure += exposure if position["component_id"] is not None else Decimal("0")
            market_exposure += exposure if position["market_id"] is not None else Decimal("0")
        check = cap_check(
            market_exposure=market_exposure + new_market_exposure,
            component_exposure=component_exposure + new_component_exposure,
            global_exposure=global_exposure + new_global_exposure,
            bankroll=bankroll,
            per_market_cap=per_market_cap,
            per_component_cap=per_component_cap,
            global_cap=global_cap,
        )
        if not check.ok:
            return PortfolioExposure(
                False, check.reason, check.per_market_fraction,
                check.per_component_fraction, check.global_fraction,
            )
        return PortfolioExposure(
            True, None, check.per_market_fraction,
            check.per_component_fraction, check.global_fraction,
        )
