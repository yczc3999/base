"""Strategy config read endpoints（只读 runtime_config_versions；无 mutation）（WP-07A）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.db.cursor import CursorError
from app.deps import AuthInfo, get_admin_read_db, require_all_perms
from app.utils.response import fail, ok
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/v2/strategy-config")
async def list_config(request: Request, session: AsyncSession = Depends(get_admin_read_db),
                      auth: AuthInfo = Depends(require_all_perms("v2:config:view"))):
    try:
        page = await get_admin_logic().page(
            session, endpoint="strategy-config", params=request.query_params,
            allowed_filters=frozenset({"status"}), repo_fn=get_admin_repo().list_config,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    return ok(page)


@router.get("/v2/strategy-config/{config_id}")
async def get_config(config_id: int, session: AsyncSession = Depends(get_admin_read_db),
                     auth: AuthInfo = Depends(require_all_perms("v2:config:view"))):
    row = await get_admin_repo().get_config(session, config_id)
    if row is None:
        return fail("not_found", 404)
    return ok(row)
