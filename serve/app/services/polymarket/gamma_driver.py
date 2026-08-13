"""Gamma Driver（WP-01B Checkpoint A）。

只做 wire：keyset 分页（拒绝 offset）、detail、token reverse lookup、超时/重试/限流、
typed 结果 + raw bytes + receipts。不写 DB/Redis、不做业务判断（实施合同 §5.1）。

- 分页只走 ``after_cursor``；``offset`` 参数在任何路径都不被接受。
- cursor 单调链校验由 universe Logic 完成；Driver 只忠实传 cursor。
- ``closed=false`` 扫描由调用方通过 params 显式传入。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from pydantic import ValidationError

from app.schemas.polymarket.common import (
    DriverCallResult,
    PolymarketError,
    REASON_OFFSET_FORBIDDEN,
    REASON_RESPONSE_SCHEMA,
)
from app.schemas.polymarket.gamma import (
    GAMMA_EVENTS_PAGE_LIMIT,
    GAMMA_MARKETS_PAGE_LIMIT,
    GAMMA_TAGS_PAGE_LIMIT,
    GammaEvent,
    GammaEventsKeysetPage,
    GammaMarket,
    GammaMarketsKeysetPage,
    GammaTag,
    GammaTagsPage,
    parse_gamma_keyset_page,
    parse_gamma_tags_page,
)
from app.services.polymarket.base import HttpPolymarketDriver, WirePolicy


def _reject_offset(params: dict[str, Any]) -> None:
    if "offset" in params:
        raise PolymarketError(REASON_OFFSET_FORBIDDEN)
    if params.get("page") is not None:
        raise PolymarketError(REASON_OFFSET_FORBIDDEN)


def _cursor_params(cursor: str | None, limit: int, *, closed: bool) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit, "closed": "false" if not closed else "true"}
    if cursor:
        params["after_cursor"] = cursor
    _reject_offset(params)
    return params


class GammaDriver(HttpPolymarketDriver):
    """Polymarket Gamma API 公共市场发现。"""

    def __init__(
        self,
        base_url: str = "https://gamma-api.polymarket.com",
        *,
        policy: WirePolicy | None = None,
        transport=None,
        clock=None,
    ) -> None:
        super().__init__(
            base_url,
            policy=policy or WirePolicy(),
            transport=transport,
            clock=clock,
        )

    async def keyset_events(
        self,
        cursor: str | None = None,
        limit: int = GAMMA_EVENTS_PAGE_LIMIT,
        *,
        closed: bool = False,
    ) -> DriverCallResult:
        """拉一页 events keyset；返回 typed 页 + raw + receipts。"""
        if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= GAMMA_EVENTS_PAGE_LIMIT):
            raise ValueError(f"events limit must be in 1..{GAMMA_EVENTS_PAGE_LIMIT}, got {limit!r}")
        result = await self.get_json(
            "/events/keyset",
            params=_cursor_params(cursor, limit, closed=closed),
        )
        try:
            items, next_cursor = parse_gamma_keyset_page(result.typed, items_key="events")
            page = GammaEventsKeysetPage(
                items=[GammaEvent.model_validate(item) for item in items],
                next_cursor=next_cursor,
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise PolymarketError(
                REASON_RESPONSE_SCHEMA, receipts=result.receipts
            ) from exc
        return DriverCallResult(typed=page, raw=result.raw, receipts=result.receipts)

    async def keyset_markets(
        self,
        cursor: str | None = None,
        limit: int = GAMMA_MARKETS_PAGE_LIMIT,
        *,
        closed: bool = False,
    ) -> DriverCallResult:
        """拉一页 markets keyset。"""
        if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= GAMMA_MARKETS_PAGE_LIMIT):
            raise ValueError(f"markets limit must be in 1..{GAMMA_MARKETS_PAGE_LIMIT}, got {limit!r}")
        result = await self.get_json(
            "/markets/keyset",
            params=_cursor_params(cursor, limit, closed=closed),
        )
        try:
            items, next_cursor = parse_gamma_keyset_page(result.typed, items_key="markets")
            page = GammaMarketsKeysetPage(
                items=[GammaMarket.model_validate(item) for item in items],
                next_cursor=next_cursor,
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise PolymarketError(
                REASON_RESPONSE_SCHEMA, receipts=result.receipts
            ) from exc
        return DriverCallResult(typed=page, raw=result.raw, receipts=result.receipts)

    async def event_detail(self, event_id: str) -> DriverCallResult:
        """event detail（new_market 触发定向刷新用）。"""
        if not event_id:
            raise ValueError("event_id empty")
        result = await self.get_json(f"/events/{quote(event_id, safe='')}")
        try:
            typed = GammaEvent.model_validate(result.typed)
        except ValidationError as exc:
            raise PolymarketError(
                REASON_RESPONSE_SCHEMA, receipts=result.receipts
            ) from exc
        return DriverCallResult(typed=typed, raw=result.raw, receipts=result.receipts)

    async def market_detail(self, market_id: str) -> DriverCallResult:
        """market detail（new_market 触发定向刷新用）。"""
        if not market_id:
            raise ValueError("market_id empty")
        result = await self.get_json(f"/markets/{quote(market_id, safe='')}")
        try:
            typed = GammaMarket.model_validate(result.typed)
        except ValidationError as exc:
            raise PolymarketError(
                REASON_RESPONSE_SCHEMA, receipts=result.receipts
            ) from exc
        return DriverCallResult(typed=typed, raw=result.raw, receipts=result.receipts)

    async def list_tags(
        self,
        *,
        limit: int = GAMMA_TAGS_PAGE_LIMIT,
        offset: int = 0,
    ) -> DriverCallResult:
        """拉一页 tag 目录。官方 ``GET /tags`` 只有 offset，无 keyset。

        offset 仅用于本目录同步；events/markets 宇宙扫描仍拒绝 offset。
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not (
            1 <= limit <= GAMMA_TAGS_PAGE_LIMIT
        ):
            raise ValueError(
                f"tags limit must be in 1..{GAMMA_TAGS_PAGE_LIMIT}, got {limit!r}"
            )
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError(f"tags offset must be >= 0, got {offset!r}")
        result = await self.get_json("/tags", params={"limit": limit, "offset": offset})
        try:
            raw_items = parse_gamma_tags_page(result.typed)
            page = GammaTagsPage(
                items=[GammaTag.model_validate(item) for item in raw_items],
                offset=offset,
                limit=limit,
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise PolymarketError(
                REASON_RESPONSE_SCHEMA, receipts=result.receipts
            ) from exc
        return DriverCallResult(typed=page, raw=result.raw, receipts=result.receipts)

    async def tag_by_slug(self, slug: str) -> DriverCallResult:
        """按 slug 解析官方 tag（得到稳定 ``id``）。"""
        if not isinstance(slug, str) or not slug.strip() or "/" in slug or "\\" in slug:
            raise ValueError("tag_slug_invalid")
        result = await self.get_json(f"/tags/slug/{quote(slug.strip(), safe='')}")
        try:
            typed = GammaTag.model_validate(result.typed)
        except ValidationError as exc:
            raise PolymarketError(
                REASON_RESPONSE_SCHEMA, receipts=result.receipts
            ) from exc
        if not typed.persistable():
            raise PolymarketError(
                REASON_RESPONSE_SCHEMA, receipts=result.receipts
            )
        return DriverCallResult(typed=typed, raw=result.raw, receipts=result.receipts)
