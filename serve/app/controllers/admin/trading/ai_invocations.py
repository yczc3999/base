"""AI invocations read endpoints（复合身份 (occurred_at,id)）（WP-07A Checkpoint B）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.db.cursor import CursorError
from app.deps import AuthInfo, require_all_perms
from app.services.database import get_db
from app.utils.response import fail, ok
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_ALLOWED = frozenset({"role", "lifecycle_state"})


@router.get("/v2/ai-invocations")
async def list_ai(request: Request, session: AsyncSession = Depends(get_db),
                  auth: AuthInfo = Depends(require_all_perms("v2:ai:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="ai-invocations", params=request.query_params,
            allowed_filters=_ALLOWED, repo_fn=get_admin_repo().list_ai,
            sort_time_col="occurred_at",
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    return ok(page)


@router.get("/v2/ai-invocations/{ai_id}")
async def get_ai(ai_id: int, request: Request, session: AsyncSession = Depends(get_db),
                 auth: AuthInfo = Depends(require_all_perms("v2:ai:view"))):
    occurred_at = request.query_params.get("occurred_at")
    if not occurred_at:
        return fail("occurred_at_required", 400)
    chain = await get_admin_repo().ai_chain(session, occurred_at=occurred_at, ai_id=ai_id)
    if not chain:
        return fail("not_found", 404)
    return ok(chain)
