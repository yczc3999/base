"""Evaluation read endpoints（labels/metrics/promotions）（WP-07A Checkpoint B）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.db.cursor import CursorError
from app.deps import AuthInfo, get_admin_read_db, require_all_perms
from app.utils.response import fail, ok
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/v2/evaluation/labels")
async def list_labels(request: Request, session: AsyncSession = Depends(get_admin_read_db),
                      auth: AuthInfo = Depends(require_all_perms("v2:evaluation:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="evaluation-labels", params=request.query_params,
            allowed_filters=frozenset({"state"}), repo_fn=get_admin_repo().list_labels,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    return ok(page)


@router.get("/v2/evaluation/metrics")
async def list_metrics(request: Request, session: AsyncSession = Depends(get_admin_read_db),
                       auth: AuthInfo = Depends(require_all_perms("v2:evaluation:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="evaluation-metrics", params=request.query_params,
            allowed_filters=frozenset({"status"}), repo_fn=get_admin_repo().list_metrics,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    return ok(page)


@router.get("/v2/evaluation/promotions")
async def list_promotions(request: Request, session: AsyncSession = Depends(get_admin_read_db),
                          auth: AuthInfo = Depends(require_all_perms("v2:evaluation:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="evaluation-promotions", params=request.query_params,
            allowed_filters=frozenset({"status"}), repo_fn=get_admin_repo().list_promotions,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    return ok(page)
