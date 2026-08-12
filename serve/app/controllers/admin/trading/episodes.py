"""Episodes read endpoints（WP-07A Checkpoint B）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.db.cursor import CursorError
from app.deps import AuthInfo, require_all_perms
from app.services.database import get_db
from app.utils.response import fail, ok
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_ALLOWED = frozenset({"status"})


@router.get("/v2/episodes")
async def list_episodes(request: Request, session: AsyncSession = Depends(get_db),
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
async def get_episode(episode_id: int, session: AsyncSession = Depends(get_db),
                      auth: AuthInfo = Depends(require_all_perms("v2:episodes:view"))):
    chain = await get_admin_repo().episode_chain(session, episode_id)
    if not chain:
        return fail("not_found", 404)
    return ok(chain)


@router.get("/v2/episodes/{episode_id}/timeline")
async def episode_timeline(episode_id: int, request: Request,
                           session: AsyncSession = Depends(get_db),
                           auth: AuthInfo = Depends(require_all_perms("v2:episodes:view"))):
    direction = get_admin_logic().parse_direction(request.query_params.get("direction"))
    limit = get_admin_logic().clamp_limit(request.query_params.get("limit"))
    cursor = request.query_params.get("cursor")
    if cursor:
        try:
            # timeline 是合并多源的 keyset（endpoint 固定 filter 空）
            fh = get_admin_logic().filter_hash(endpoint="episodes-timeline", filters={}, direction=direction)
            payload = get_admin_logic().decode_cursor(cursor, endpoint="episodes-timeline",
                                                      direction=direction, filter_hash=fh)
            st, cid, as_of = payload.sort_time, payload.id, payload.as_of
        except CursorError as exc:
            return fail(str(exc), 400)
    else:
        as_of = await get_admin_logic().freeze_as_of(session)
        fh = get_admin_logic().filter_hash(endpoint="episodes-timeline", filters={}, direction=direction)
        st, cid = None, None
    rows, has_more = await get_admin_repo().episode_timeline(
        session, episode_id=episode_id, cursor_st=st, cursor_id=cid,
        direction=direction, limit=limit)
    next_cursor = None
    if rows:
        last = rows[-1]
        try:
            from app.db.cursor import parse_utc_iso
            next_cursor = get_admin_logic().encode_cursor(
                endpoint="episodes-timeline", sort_time=parse_utc_iso(last["created_at"]),
                id=last["id"], direction=direction, filter_hash=fh, as_of=as_of)
        except CursorError:
            next_cursor = None
    return ok({"items": rows, "next_cursor": next_cursor, "has_more": has_more,
               "as_of": as_of.isoformat().replace("+00:00", "Z"), "filter_hash": fh})
