"""Episodes read endpoints（WP-07A Checkpoint B）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.db.cursor import CursorError
from app.deps import AuthInfo, get_admin_read_db, require_all_perms
from app.utils.response import fail, ok
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_ALLOWED = frozenset({"status"})


@router.get("/v2/episodes")
async def list_episodes(request: Request, session: AsyncSession = Depends(get_admin_read_db),
                        auth: AuthInfo = Depends(require_all_perms("v2:episodes:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="episodes", params=request.query_params,
            allowed_filters=_ALLOWED, repo_fn=get_admin_repo().list_episodes,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    return ok(page)


@router.get("/v2/episodes/{episode_id}")
async def get_episode(episode_id: int, session: AsyncSession = Depends(get_admin_read_db),
                      auth: AuthInfo = Depends(require_all_perms("v2:episodes:view"))):
    chain = await get_admin_repo().episode_chain(session, episode_id)
    if not chain:
        return fail("not_found", 404)
    return ok(chain)


@router.get("/v2/episodes/{episode_id}/timeline")
async def episode_timeline(episode_id: int, request: Request,
                           session: AsyncSession = Depends(get_admin_read_db),
                           auth: AuthInfo = Depends(require_all_perms("v2:episodes:view"))):
    try:
        page = await get_admin_logic().page(
            session,
            endpoint="episodes-timeline",
            params=request.query_params,
            allowed_filters=frozenset(),
            fixed_filters={"episode_id": episode_id},
            repo_fn=get_admin_repo().episode_timeline,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    return ok(page)
