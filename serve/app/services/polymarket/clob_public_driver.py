"""Current Polymarket CLOB public REST driver."""

from __future__ import annotations

from typing import Any, Callable, TypeVar
from urllib.parse import quote

from pydantic import ValidationError

from app.schemas.polymarket.clob_public import (
    CLOB_BOOKS_BATCH_LIMIT,
    ClobBatchBooksResult,
    ClobBook,
    ClobBookBatchItem,
    ClobFeeRate,
    ClobMarketConfig,
    ClobPriceQuote,
    ClobServerTime,
    ClobTickSize,
    ClobTokenMarketMapping,
)
from app.schemas.polymarket.common import (
    DriverCallResult,
    PolymarketError,
    REASON_HTTP_BATCH_TOO_LARGE,
    REASON_RESPONSE_SCHEMA,
)
from app.services.polymarket.base import HttpPolymarketDriver, WirePolicy

T = TypeVar("T")


def _token(value: str, name: str = "token_id") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} empty")
    return value.strip()


def _parse_result(result: DriverCallResult, parser: Callable[[], T]) -> T:
    try:
        return parser()
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        raise PolymarketError(
            REASON_RESPONSE_SCHEMA, receipts=result.receipts
        ) from exc


class ClobPublicDriver(HttpPolymarketDriver):
    """Public book/config/time endpoints; no valuation decisions."""

    def __init__(
        self,
        base_url: str = "https://clob.polymarket.com",
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

    async def book(self, token_id: str) -> DriverCallResult:
        token_id = _token(token_id)
        result = await self.get_json("/book", params={"token_id": token_id})
        typed = _parse_result(result, lambda: ClobBook.model_validate(result.typed))
        return DriverCallResult(typed=typed, raw=result.raw, receipts=result.receipts)

    async def books_batch(self, token_ids: list[str]) -> DriverCallResult:
        """Official body is a JSON array of ``{"token_id": ...}`` objects."""
        if not token_ids:
            return DriverCallResult(
                typed=ClobBatchBooksResult(items=[]), raw=b"[]", receipts=()
            )
        if len(token_ids) > CLOB_BOOKS_BATCH_LIMIT:
            raise PolymarketError(
                REASON_HTTP_BATCH_TOO_LARGE,
                detail=f"batch {len(token_ids)} exceeds {CLOB_BOOKS_BATCH_LIMIT}",
            )
        normalized = [_token(token) for token in token_ids]
        result = await self.post_json(
            "/books", json_body=[{"token_id": token} for token in normalized]
        )

        def parse() -> ClobBatchBooksResult:
            if not isinstance(result.typed, list):
                raise ValueError("books_batch_not_array")
            items: list[ClobBookBatchItem] = []
            seen: set[str] = set()
            for entry in result.typed:
                if isinstance(entry, dict) and "error" in entry:
                    token_id = entry.get("asset_id") or entry.get("token_id")
                    if token_id is None or str(token_id) not in normalized:
                        raise ValueError("books_batch_unrequested_error_token")
                    if str(token_id) in seen:
                        raise ValueError("books_batch_duplicate_token")
                    seen.add(str(token_id))
                    items.append(
                        ClobBookBatchItem(
                            ok=False,
                            token_id=str(token_id) if token_id is not None else None,
                            error=str(entry["error"]),
                        )
                    )
                    continue
                book = ClobBook.model_validate(entry)
                if book.asset_id not in normalized:
                    raise ValueError("books_batch_unrequested_token")
                if book.asset_id in seen:
                    raise ValueError("books_batch_duplicate_token")
                seen.add(book.asset_id)
                items.append(
                    ClobBookBatchItem(ok=True, book=book, token_id=book.asset_id)
                )
            if seen != set(normalized):
                raise ValueError("books_batch_missing_token")
            return ClobBatchBooksResult(items=items)

        typed = _parse_result(result, parse)
        return DriverCallResult(typed=typed, raw=result.raw, receipts=result.receipts)

    async def clob_market(self, condition_id: str) -> DriverCallResult:
        condition_id = _token(condition_id, "condition_id")
        result = await self.get_json(
            f"/clob-markets/{quote(condition_id, safe='')}"
        )
        typed = _parse_result(
            result, lambda: ClobMarketConfig.model_validate(result.typed)
        )
        return DriverCallResult(
            typed=typed,
            raw=result.raw,
            receipts=result.receipts,
            extra={"condition_id": condition_id},
        )

    async def server_time(self) -> DriverCallResult:
        result = await self.get_json("/time")
        typed = _parse_result(
            result, lambda: ClobServerTime.model_validate(result.typed)
        )
        return DriverCallResult(typed=typed, raw=result.raw, receipts=result.receipts)

    async def tick_size(self, token_id: str) -> DriverCallResult:
        token_id = _token(token_id)
        result = await self.get_json("/tick-size", params={"token_id": token_id})
        typed = _parse_result(
            result, lambda: ClobTickSize.model_validate(result.typed)
        )
        return DriverCallResult(typed=typed, raw=result.raw, receipts=result.receipts)

    async def fee_rate(self, token_id: str) -> DriverCallResult:
        token_id = _token(token_id)
        result = await self.get_json("/fee-rate", params={"token_id": token_id})
        typed = _parse_result(
            result, lambda: ClobFeeRate.model_validate(result.typed)
        )
        return DriverCallResult(typed=typed, raw=result.raw, receipts=result.receipts)

    async def price(self, token_id: str, side: str) -> DriverCallResult:
        """Official semantics: BUY returns best bid; SELL returns best ask."""
        token_id = _token(token_id)
        if not isinstance(side, str):
            raise ValueError("side must be BUY|SELL")
        side_upper = side.upper()
        if side_upper not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY|SELL, got {side!r}")
        result = await self.get_json(
            "/price", params={"token_id": token_id, "side": side_upper}
        )

        def parse() -> ClobPriceQuote:
            if not isinstance(result.typed, dict) or "price" not in result.typed:
                raise ValueError("price_response_invalid")
            return ClobPriceQuote(
                price=result.typed["price"],
                requested_side=side_upper,
                quote_role="BEST_BID" if side_upper == "BUY" else "BEST_ASK",
            )

        typed = _parse_result(result, parse)
        return DriverCallResult(typed=typed, raw=result.raw, receipts=result.receipts)

    async def market_by_token(self, token_id: str) -> DriverCallResult:
        """Token reverse mapping is a CLOB endpoint, not Gamma."""
        token_id = _token(token_id)
        result = await self.get_json(
            f"/markets-by-token/{quote(token_id, safe='')}"
        )

        def parse() -> ClobTokenMarketMapping:
            mapping = ClobTokenMarketMapping.model_validate(result.typed)
            if token_id not in {
                mapping.primary_token_id,
                mapping.secondary_token_id,
            }:
                raise ValueError("token_mapping_requested_token_missing")
            return mapping

        typed = _parse_result(result, parse)
        return DriverCallResult(typed=typed, raw=result.raw, receipts=result.receipts)
