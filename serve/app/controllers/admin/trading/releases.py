"""Releases read endpoints（WP-07A Checkpoint B）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.db.cursor import CursorError
from app.deps import AuthInfo, require_all_perms
from app.services.database import get_db
from app.utils.response import fail, ok
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/v2/releases")
async def list_releases(request: Request, session: AsyncSession = Depends(get_db),
                        auth: AuthInfo = Depends(require_all_perms("v2:release:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="releases", params=request.query_params,
            allowed_filters=frozenset({"status"}), repo_fn=get_admin_repo().list_releases,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    return ok(page)


@router.get("/v2/releases/{release_id}")
async def get_release(release_id: int, session: AsyncSession = Depends(get_db),
                      auth: AuthInfo = Depends(require_all_perms("v2:release:view"))):
    chain = await get_admin_repo().release_chain(session, release_id)
    if not chain:
        return fail("not_found", 404)
    return ok(chain)
