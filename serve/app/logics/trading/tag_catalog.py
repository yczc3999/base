"""Gamma tag 目录同步与本地处置 overlay。

同步只写官方 {id,slug,label}；处置是本地字段，catalog upsert 不覆盖。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.trading.universe import UniverseLogic
from app.repositories.trading.market import MarketRepository
from app.schemas.polymarket.gamma import GAMMA_TAGS_PAGE_LIMIT

TAG_DISPOSITIONS = ("SELECT", "DEFER", "REJECT")


class TagCatalogLogic:
    def __init__(
        self,
        market_repo: MarketRepository | None = None,
        universe: UniverseLogic | None = None,
    ) -> None:
        self._market = market_repo or MarketRepository()
        self._universe = universe or UniverseLogic(self._market)

    async def set_disposition(
        self,
        session: AsyncSession,
        *,
        tag_id: int,
        disposition: str | None,
    ) -> dict[str, Any]:
        if isinstance(tag_id, bool) or tag_id <= 0:
            raise ValueError("tag_id_invalid")
        if disposition is not None:
            if disposition not in TAG_DISPOSITIONS:
                raise ValueError("tag_disposition_invalid")
        row = await self._market.update_tag_disposition(
            session, tag_id=tag_id, disposition=disposition
        )
        if row is None:
            raise ValueError("tag_not_found")
        return row

    async def sync_catalog(
        self,
        session: AsyncSession,
        *,
        gamma: Any,
        observed_at: datetime | None = None,
        page_limit: int | None = None,
        max_pages: int | None = None,
    ) -> dict[str, Any]:
        policy = self._universe.policy
        limit = page_limit or policy.tag_page_limit or GAMMA_TAGS_PAGE_LIMIT
        cap = max_pages or policy.tag_catalog_max_pages
        now = observed_at or datetime.now(timezone.utc)
        uow = SimpleNamespace(session=session)
        upserted = 0
        pages = 0
        offset = 0
        last_count = 0
        while pages < cap:
            result = await gamma.list_tags(limit=limit, offset=offset)
            items = result.typed.items
            last_count = len(items)
            if last_count == 0:
                break
            for tag in items:
                written = await self._universe.persist_tag(
                    uow,  # type: ignore[arg-type]
                    tag,
                    observed_at=now,
                    seen_in_catalog=True,
                )
                if written is not None:
                    upserted += 1
            commit = getattr(session, "commit", None)
            if callable(commit):
                await commit()
            pages += 1
            if last_count < limit:
                break
            offset += limit
        return {
            "ok": True,
            "upserted": upserted,
            "pages": pages,
            "truncated": pages >= cap and last_count == limit,
        }
