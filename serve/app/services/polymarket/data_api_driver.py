"""Data API REST driver（WP-05 Checkpoint C）。

封装 ``data-api.polymarket.com`` 的账户 trades / positions keyset 分页。
账户 open orders 的权威路径是 CLOB ``/data/orders``，由
``ClobTradingDriver.list_open_orders`` 封装；本 Driver 故意不伪造
Data API ``/orders`` 路径。
只做 wire；认证 header 由调用方（Logic）按账户注入（L2 HMAC），Driver 不持有 secret。

- keyset 分页：拒绝 ``offset``；cursor 单调链由 Logic 校验。
- 构造器可注入 fake transport / clock；未注入 transport 时任何真实 connect 立即失败。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.schemas.polymarket.common import (
    Cursor,
    DriverCallResult,
    PolymarketError,
    REASON_OFFSET_FORBIDDEN,
    REASON_RESPONSE_SCHEMA,
)
from app.schemas.polymarket.data_api import DataApiPositions, DataApiTrades
from app.services.polymarket.base import HttpPolymarketDriver, WirePolicy

DATA_API_BASE_URL = "https://data-api.polymarket.com"
_KEYSET_LIMIT_MAX = 1000


def _reject_offset(params: dict[str, Any]) -> None:
    if "offset" in params:
        raise PolymarketError(REASON_OFFSET_FORBIDDEN)
    if params.get("page") is not None:
        raise PolymarketError(REASON_OFFSET_FORBIDDEN)


def _cursor_params(cursor: Cursor, limit: int) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= _KEYSET_LIMIT_MAX):
        raise ValueError(f"limit must be in 1..{_KEYSET_LIMIT_MAX}, got {limit!r}")
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["next_cursor"] = cursor
    _reject_offset(params)
    return params


class DataApiDriver(HttpPolymarketDriver):
    """Data API keyset 分页；认证 header 由调用方提供。"""

    def __init__(
        self,
        base_url: str = DATA_API_BASE_URL,
        *,
        policy: WirePolicy | None = None,
        transport=None,
        clock=None,
    ) -> None:
        super().__init__(
            base_url,
            policy=policy or WirePolicy(max_retries=1),
            transport=transport,
            clock=clock,
            require_injected_transport=True,
        )

    async def trades(
        self,
        cursor: Cursor = None,
        limit: int = 100,
        *,
        after: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> DriverCallResult:
        params = _cursor_params(cursor, limit)
        if after:
            params["after"] = after
        result = await self.get_json("/trades", params=params, headers=headers)
        typed = self._parse(result, DataApiTrades)
        return DriverCallResult(typed=typed, raw=result.raw, receipts=result.receipts)

    async def positions(
        self, cursor: Cursor = None, limit: int = 100, *, headers: dict[str, str] | None = None,
    ) -> DriverCallResult:
        result = await self.get_json(
            "/positions", params=_cursor_params(cursor, limit), headers=headers,
        )
        typed = self._parse(result, DataApiPositions)
        return DriverCallResult(typed=typed, raw=result.raw, receipts=result.receipts)

    @staticmethod
    def _parse(result: DriverCallResult, model: type[Any]) -> Any:
        try:
            return model.model_validate(result.typed)
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            raise PolymarketError(REASON_RESPONSE_SCHEMA, receipts=result.receipts) from exc
