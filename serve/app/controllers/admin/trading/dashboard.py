"""Dashboard —— 只读 WP-04 五张 projection（WP-07A Checkpoint B）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.deps import AuthInfo, require_all_perms
from app.services.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.utils.response import ok

router = APIRouter()
_PROJECTIONS = ("ops_health_current", "pipeline_funnel_hourly", "account_risk_current",
                "provider_cost_daily", "latest_chain_summary")


@router.get("/v2/dashboard")
async def dashboard(request: Request, session: AsyncSession = Depends(get_db),
                    auth: AuthInfo = Depends(require_all_perms("v2:dashboard:view"))):
    logic = get_admin_logic()
    as_of = (await logic.freeze_as_of(session)).isoformat().replace("+00:00", "Z")
    blocks = {}
    for name in _PROJECTIONS:
        rows = await get_admin_repo().dashboard_projection(session, name)
        blocks[name] = {
            "rows": rows,
            "freshness_status": "missing" if not rows else "fresh",
            "as_of": as_of,
        }
    return ok({
        "blocks": blocks,
        "as_of": as_of,
    })
