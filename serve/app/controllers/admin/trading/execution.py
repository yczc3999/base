"""Execution read endpoints（WP-07A Checkpoint B）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.db.cursor import CursorError
from app.deps import AuthInfo, get_admin_read_db, require_all_perms
from app.utils.response import fail, ok
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/v2/execution/intents")
async def list_intents(request: Request, session: AsyncSession = Depends(get_admin_read_db),
                       auth: AuthInfo = Depends(require_all_perms("v2:execution:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="execution-intents", params=request.query_params,
            allowed_filters=frozenset({"status"}), repo_fn=get_admin_repo().list_intents,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    page["authoritative"] = True
    return ok(page)


@router.get("/v2/execution/orders")
async def list_orders(request: Request, session: AsyncSession = Depends(get_admin_read_db),
                      auth: AuthInfo = Depends(require_all_perms("v2:execution:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="execution-orders", params=request.query_params,
            allowed_filters=frozenset({"status"}), repo_fn=get_admin_repo().list_orders,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    page["authoritative"] = True
    return ok(page)


@router.get("/v2/execution/positions")
async def list_positions(request: Request, session: AsyncSession = Depends(get_admin_read_db),
                         auth: AuthInfo = Depends(require_all_perms("v2:execution:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="execution-positions", params=request.query_params,
            allowed_filters=frozenset(), repo_fn=get_admin_repo().list_positions,
            sort_time_col="updated_at",
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    page["authoritative"] = True
    return ok(page)


@router.get("/v2/execution/ledger")
async def list_ledger(request: Request, session: AsyncSession = Depends(get_admin_read_db),
                      auth: AuthInfo = Depends(require_all_perms("v2:execution:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="execution-ledger", params=request.query_params,
            allowed_filters=frozenset({"kind"}), repo_fn=get_admin_repo().list_ledger,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    page["authoritative"] = True
    return ok(page)


@router.get("/v2/execution/{decision_id}/trace")
async def decision_trace(decision_id: int, session: AsyncSession = Depends(get_admin_read_db),
                         auth: AuthInfo = Depends(require_all_perms("v2:execution:view"))):
    trace = await get_admin_repo().decision_trace(session, decision_id)
    return ok({"items": trace})
