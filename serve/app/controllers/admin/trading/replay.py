"""Replay read endpoints（WP-07A Checkpoint B）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.db.cursor import CursorError
from app.deps import AuthInfo, get_admin_read_db, require_all_perms
from app.utils.response import fail, ok
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/v2/replay")
async def list_replay(request: Request, session: AsyncSession = Depends(get_admin_read_db),
                      auth: AuthInfo = Depends(require_all_perms("v2:replay:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="replay", params=request.query_params,
            allowed_filters=frozenset(), repo_fn=get_admin_repo().list_replay,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    return ok(page)


@router.get("/v2/replay/{replay_id}")
async def get_replay(replay_id: int, session: AsyncSession = Depends(get_admin_read_db),
                     auth: AuthInfo = Depends(require_all_perms("v2:replay:view"))):
    row = await get_admin_repo().get_replay(session, replay_id)
    if row is None:
        return fail("not_found", 404)
    return ok(row)
