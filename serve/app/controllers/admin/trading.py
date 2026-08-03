"""交易 bot 管理后台接口"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from app.controllers.base import crud_router
from app.services.database import get_db
from app.deps import AuthInfo, require_perms
from app.utils.response import ok
from app.logics.trading import (
    rb_trade_logic, rb_position_logic, rb_strategy_logic, rb_heartbeat_logic,
)
from app.models.trading import RbTrade, RbPosition, RbStrategy, RbHeartbeat

router = APIRouter()

router.include_router(
    crud_router("trading/trade", rb_trade_logic, perms_prefix="admin:trading")
)
router.include_router(
    crud_router("trading/position", rb_position_logic, perms_prefix="admin:trading")
)
router.include_router(
    crud_router("trading/strategy", rb_strategy_logic, perms_prefix="admin:trading")
)
router.include_router(
    crud_router("trading/heartbeat", rb_heartbeat_logic, perms_prefix="admin:trading")
)


# ==================== 看板聚合 ====================

@router.get("/trading/dashboard/stats")
async def trading_stats(
    auth: AuthInfo = Depends(require_perms("admin:trading:list")),
    db: AsyncSession = Depends(get_db),
):
    """今日/累计：信号、交易、胜率、PnL、熔断状态、最新心跳"""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    async def trade_stats(since=None):
        conds = []
        if since is not None:
            conds.append(RbTrade.exit_time >= since)
        stmt = select(
            func.count().label("trades"),
            func.coalesce(func.sum(RbTrade.pnl_usd), 0).label("pnl"),
            func.coalesce(
                func.sum(case((RbTrade.pnl_usd > 0, 1), else_=0)), 0
            ).label("wins"),
        ).where(*conds)
        row = (await db.execute(stmt)).one()
        trades = row.trades or 0
        return {
            "trades": trades,
            "pnl": float(row.pnl or 0),
            "win_rate": round((row.wins or 0) / trades, 4) if trades else None,
        }

    total = await trade_stats()
    today = await trade_stats(today_start)

    # 信号/持仓计数
    signals_total = (
        await db.execute(select(func.count()).select_from(RbPosition))
    ).scalar_one()
    open_positions = (
        await db.execute(
            select(func.count())
            .select_from(RbPosition)
            .where(RbPosition.status == "open")
        )
    ).scalar_one()
    signals_today = (
        await db.execute(
            select(func.count())
            .select_from(RbPosition)
            .where(RbPosition.entry_time >= today_start)
        )
    ).scalar_one()

    # 最新心跳
    hb = (
        await db.execute(
            select(RbHeartbeat).order_by(RbHeartbeat.ts.desc()).limit(1)
        )
    ).scalar_one_or_none()
    latest_heartbeat = (
        rb_heartbeat_logic.format_data(hb) if hb is not None else None
    )

    # 熔断状态：累计 PnL 是否击穿 daily_loss_breaker_usd
    strategy = (
        await db.execute(select(RbStrategy).order_by(RbStrategy.id).limit(1))
    ).scalar_one_or_none()
    breaker = None
    breaker_triggered = False
    if strategy is not None:
        params = strategy.params or {}
        breaker = params.get("daily_loss_breaker_usd")
        if breaker is not None:
            breaker_triggered = total["pnl"] <= -float(breaker)

    return ok({
        "today": {
            "signals": signals_today,
            "trades": today["trades"],
            "pnl": today["pnl"],
            "win_rate": today["win_rate"],
        },
        "total": {
            "signals": signals_total,
            "trades": total["trades"],
            "pnl": total["pnl"],
            "win_rate": total["win_rate"],
        },
        "open_positions": open_positions,
        "mode": strategy.mode if strategy else None,
        "breaker": {
            "daily_loss_breaker_usd": breaker,
            "triggered": breaker_triggered,
        },
        "latest_heartbeat": latest_heartbeat,
    })


@router.get("/trading/dashboard/equity")
async def trading_equity(
    auth: AuthInfo = Depends(require_perms("admin:trading:list")),
    db: AsyncSession = Depends(get_db),
):
    """资金曲线：按出场时间累计 PnL（含每笔数据点）"""
    stmt = (
        select(RbTrade.exit_time, RbTrade.pnl_usd, RbTrade.token_symbol)
        .where(RbTrade.exit_time.is_not(None))
        .order_by(RbTrade.exit_time.asc())
    )
    rows = (await db.execute(stmt)).all()
    cum = 0.0
    points = []
    for exit_time, pnl, symbol in rows:
        cum += float(pnl or 0)
        points.append({
            "ts": exit_time.isoformat(),
            "pnl": float(pnl or 0),
            "cum_pnl": round(cum, 8),
            "symbol": symbol,
        })
    return ok({"points": points})
