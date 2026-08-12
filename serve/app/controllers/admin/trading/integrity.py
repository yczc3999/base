"""Integrity read endpoints（WP-07A Checkpoint B）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.db.cursor import CursorError
from app.deps import AuthInfo, require_all_perms
from app.services.database import get_db
from app.utils.response import fail, ok
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_ALLOWED_AGG_TYPES = {"episode", "decision", "intent", "chain_operation",
                      "forecast_submission", "evidence_bundle"}


@router.get("/v2/integrity/runtime")
async def integrity_runtime(request: Request, auth: AuthInfo = Depends(require_all_perms("v2:integrity:view"))):
    """V2 runtime 最近一次安全健康快照（与旧 trading/runtime 同源，独立 v2 权限）。"""
    runtime = getattr(request.app.state, "trading_runtime", None)
    if runtime is None:
        from app.config import settings
        from app.services.runtime import safe_unready_snapshot
        return ok(safe_unready_snapshot(settings.ARTIFACT_DRIVER))
    snapshot = runtime.last_snapshot
    if snapshot is None:
        from app.config import settings
        from app.services.runtime import safe_unready_snapshot
        return ok(safe_unready_snapshot(settings.ARTIFACT_DRIVER))
    return ok(snapshot)


@router.get("/v2/integrity/alerts")
async def list_alerts(request: Request, session: AsyncSession = Depends(get_db),
                      auth: AuthInfo = Depends(require_all_perms("v2:integrity:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="integrity-alerts", params=request.query_params,
            allowed_filters=frozenset({"severity"}), repo_fn=get_admin_repo().list_alerts,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    return ok(page)


@router.get("/v2/integrity/workflows/{aggregate_type}/{aggregate_id}")
async def integrity_workflow(aggregate_type: str, aggregate_id: str, request: Request,
                             session: AsyncSession = Depends(get_db),
                             auth: AuthInfo = Depends(require_all_perms("v2:integrity:view"))):
    if aggregate_type not in _ALLOWED_AGG_TYPES:
        return fail("aggregate_type_not_allowed", 400)
    chain = await get_admin_repo().integrity_chain(
        session, aggregate_type=aggregate_type, aggregate_id=aggregate_id)
    return ok(chain)
