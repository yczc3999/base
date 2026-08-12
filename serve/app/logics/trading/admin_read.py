"""V2 Admin Read API logic（WP-07A Checkpoint A/B）。

- cursor/filter/as_of 语义；Controller 只 DTO/鉴权/UoW/响应。
- 列表统一 keyset；filter/sort/响应字段显式 allowlist；未知 filter 400。
- 首屏冻结 ``as_of=statement_timestamp()``；后续页必须复用同一 as_of。
- ``filter_hash = H(endpoint + query_version + canonical_filters + direction)``。
- cursor tamper / endpoint / filter / direction mismatch / 非 UTC / 超长 / 坏签名 → 400。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.cursor import (
    CursorCodec,
    CursorError,
    CursorPayload,
    canonical_filter_hash,
    derive_key,
    parse_utc_iso,
)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
QUERY_VERSION = "v1"

ListFn = Callable[..., Awaitable[tuple[list[dict], bool]]]


@dataclass(frozen=True)
class PageSpec:
    """一次分页请求的解析结果（Controller 不重复解析）。"""

    endpoint: str
    direction: str
    limit: int
    filters: dict
    as_of: datetime
    filter_hash: str
    cursor: CursorPayload | None


class AdminReadLogic:
    """分页/cursor/filter 语义；持有注入式 codec（生产从 APP_KEY 派生 key）。"""

    def __init__(self, codec: CursorCodec | None = None) -> None:
        if codec is None:
            codec = CursorCodec(derive_key(settings.APP_KEY))
        self._codec = codec

    # ---- filter / limit / direction 解析 ----

    def parse_direction(self, raw: str | None) -> str:
        if raw is None or raw == "":
            return "desc"
        if raw not in ("asc", "desc"):
            raise CursorError("cursor_direction_invalid")
        return raw

    def clamp_limit(self, raw: str | int | None) -> int:
        if raw is None or raw == "":
            return DEFAULT_LIMIT
        try:
            value = int(str(raw))
        except (TypeError, ValueError) as exc:
            raise CursorError("limit_invalid") from exc
        if value < 1 or value > MAX_LIMIT:
            raise CursorError("limit_out_of_range")
        return value

    def parse_filters(self, params: dict, *, allowed: frozenset[str]) -> dict:
        """显式 allowlist；未知 filter → CursorError(400)，不静默忽略。"""
        out: dict = {}
        for key, value in params.items():
            if key in ("cursor", "limit", "direction"):
                continue
            if key not in allowed:
                raise CursorError(f"unknown_filter:{key}")
            if isinstance(value, list):
                raise CursorError(f"filter_multi_value:{key}")
            out[key] = value
        return out

    # ---- as_of / filter_hash ----

    async def freeze_as_of(self, session: AsyncSession) -> datetime:
        """首屏冻结 ``statement_timestamp()``（UTC）。"""
        result = await session.execute(text("SELECT statement_timestamp()"))
        value = result.scalar_one()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def filter_hash(self, *, endpoint: str, filters: dict, direction: str) -> str:
        return canonical_filter_hash(
            endpoint=endpoint, query_version=QUERY_VERSION,
            filters=filters, direction=direction,
        )

    # ---- cursor ----

    def decode_cursor(self, token: str, *, endpoint: str, direction: str,
                      filter_hash: str) -> CursorPayload:
        return self._codec.decode(token, endpoint=endpoint, direction=direction,
                                  filter_hash=filter_hash)

    def encode_cursor(self, *, endpoint: str, sort_time: datetime, id: str,
                      direction: str, filter_hash: str, as_of: datetime) -> str:
        return self._codec.encode(
            endpoint=endpoint, sort_time=sort_time, id=id,
            direction=direction, filter_hash=filter_hash, as_of=as_of,
        )

    @staticmethod
    def _parse_row_time(value: str, col: str) -> datetime:
        """解析 pg timestamptz::text（如 '2026-08-12 01:02:03.123456+00'）或 ISO-Z。"""
        try:
            return parse_utc_iso(value)
        except CursorError:
            pass
        try:
            dt = datetime.fromisoformat(value.replace(" ", "T"))
        except ValueError as exc:
            raise CursorError(f"page_sort_time_invalid:{col}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    # ---- 分页编排 ----

    async def page(
        self,
        session: AsyncSession,
        *,
        endpoint: str,
        params: dict,
        allowed_filters: frozenset[str],
        repo_fn: ListFn,
        sort_time_col: str = "created_at",
    ) -> dict:
        """列表分页：解析 → 首屏冻结 as_of / 后续复用 → keyset 查询 → next_cursor。

        repo_fn(session, cursor_st, cursor_id, direction, limit, **filters) →
        (rows, has_more)。rows 的每个元素必须含 ``id`` 与 ``{sort_time_col}``（UTC text）。
        """
        direction = self.parse_direction(params.get("direction"))
        limit = self.clamp_limit(params.get("limit"))
        filters = self.parse_filters(params, allowed=allowed_filters)
        token = params.get("cursor")

        if token:
            # 后续页：先算 filter_hash 用于校验 cursor 绑定（不信任传入 hash）
            fh = self.filter_hash(endpoint=endpoint, filters=filters, direction=direction)
            cursor = self.decode_cursor(token, endpoint=endpoint,
                                        direction=direction, filter_hash=fh)
            as_of = cursor.as_of
        else:
            fh = self.filter_hash(endpoint=endpoint, filters=filters, direction=direction)
            as_of = await self.freeze_as_of(session)
            cursor = None

        cursor_st = cursor.sort_time if cursor else None
        cursor_id = cursor.id if cursor else None
        rows, has_more = await repo_fn(
            session, cursor_st=cursor_st, cursor_id=cursor_id,
            direction=direction, limit=limit, **filters,
        )
        if rows and has_more:
            last = rows[-1]
            sort_time = self._parse_row_time(str(last[sort_time_col]), sort_time_col)
            next_cursor = self.encode_cursor(
                endpoint=endpoint, sort_time=sort_time, id=str(last["id"]),
                direction=direction, filter_hash=fh, as_of=as_of,
            )
        else:
            next_cursor = None
        return {
            "items": rows,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "as_of": as_of.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "filter_hash": fh,
        }
