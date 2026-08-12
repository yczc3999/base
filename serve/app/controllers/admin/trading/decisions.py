"""Decisions read endpoints（WP-07A Checkpoint B）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.db.cursor import CursorError
from app.deps import AuthInfo, get_admin_read_db, require_all_perms
from app.utils.response import fail, ok
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_ALLOWED = frozenset({"status", "decision_class"})


@router.get("/v2/decisions")
async def list_decisions(request: Request, session: AsyncSession = Depends(get_admin_read_db),
                         auth: AuthInfo = Depends(require_all_perms("v2:decisions:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="decisions", params=request.query_params,
            allowed_filters=_ALLOWED, repo_fn=get_admin_repo().list_decisions,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    return ok(page)


@router.get("/v2/decisions/{decision_id}")
async def get_decision(decision_id: int, session: AsyncSession = Depends(get_admin_read_db),
                       auth: AuthInfo = Depends(require_all_perms("v2:decisions:view"))):
    chain = await get_admin_repo().decision_chain(session, decision_id)
    if not chain:
        return fail("not_found", 404)
    return ok(chain)
