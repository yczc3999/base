"""Markets read endpoints（WP-07A Checkpoint B）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.db.cursor import CursorError
from app.deps import AuthInfo, get_admin_read_db, require_all_perms
from app.utils.response import fail, ok
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_ALLOWED = frozenset({"neg_risk", "closed"})


@router.get("/v2/markets")
async def list_markets(request: Request, session: AsyncSession = Depends(get_admin_read_db),
                       auth: AuthInfo = Depends(require_all_perms("v2:markets:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="markets", params=request.query_params,
            allowed_filters=_ALLOWED, repo_fn=get_admin_repo().list_markets,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    return ok(page)


@router.get("/v2/markets/{market_id}")
async def get_market(market_id: int, session: AsyncSession = Depends(get_admin_read_db),
                     auth: AuthInfo = Depends(require_all_perms("v2:markets:view"))):
    chain = await get_admin_repo().market_chain(session, market_id)
    if not chain:
        return fail("not_found", 404)
    return ok(chain)
