"""Tags：只读同步目录 + 本地处置 / 手动同步。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.db.cursor import CursorError
from app.deps import AuthInfo, get_admin_read_db, get_db, require_all_perms, require_perms
from app.logics.trading.tag_catalog import TagCatalogLogic
from app.utils.response import fail, ok

router = APIRouter()
_ALLOWED = frozenset({"slug", "seen_in_catalog", "disposition"})
_DISPOSITION_FILTERS = frozenset({"unset", "SELECT", "DEFER", "REJECT"})
_catalog = TagCatalogLogic()


class TagDispositionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disposition: str | None = Field(default=None)


@router.get("/v2/tags")
async def list_tags(
    request: Request,
    session: AsyncSession = Depends(get_admin_read_db),
    auth: AuthInfo = Depends(require_all_perms("v2:tags:view")),
):
    raw_disposition = request.query_params.get("disposition")
    if raw_disposition and raw_disposition not in _DISPOSITION_FILTERS:
        return fail("filter_disposition_invalid", 400)
    try:
        page = await get_admin_logic().page(
            session,
            endpoint="tags",
            params=request.query_params,
            allowed_filters=_ALLOWED,
            repo_fn=get_admin_repo().list_tags,
        )
    except CursorError as exc:
        return fail(str(exc), 400)
    return ok(page)


@router.patch("/v2/tags/{tag_id}")
async def patch_tag_disposition(
    tag_id: int,
    body: TagDispositionBody,
    session: AsyncSession = Depends(get_db),
    auth: AuthInfo = Depends(require_perms("v2:tags:manage")),
):
    try:
        row = await _catalog.set_disposition(
            session, tag_id=tag_id, disposition=body.disposition
        )
    except ValueError as exc:
        code = str(exc)
        if code == "tag_not_found":
            return fail(code, 404)
        return fail(code, 400)
    await session.commit()
    return ok(row)


@router.post("/v2/tags/sync")
async def sync_tags(
    session: AsyncSession = Depends(get_db),
    auth: AuthInfo = Depends(require_perms("v2:tags:manage")),
):
    from app.schemas.polymarket.common import PolymarketError
    from app.services.polymarket import PolymarketService

    try:
        result = await _catalog.sync_catalog(session, gamma=PolymarketService().gamma())
    except PolymarketError as exc:
        await session.rollback()
        return fail(exc.reason_code, 502)
    except Exception as exc:  # noqa: BLE001 - 同步失败对操作员可见 reason
        await session.rollback()
        return fail(type(exc).__name__, 502)
    await session.commit()
    return ok(result)
