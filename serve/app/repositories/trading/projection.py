"""Read projection Repository（WP-04 Checkpoint D）。

只拥有 SQL：5 张只读投影的 upsert / replace（整表清空重插）/ keyset list /
high_watermark / count_rows。绝不 commit、不调用网络、不做业务判断。

约定：
- 投影行 append-only（immutable trigger 保护）；重建 = 整表 ``TRUNCATE``（table-level，
  不触发 per-row BEFORE DELETE）+ 批量 INSERT。
- keyset 固定 ``(as_of, id)``；列表只允许 ``sort_ts='as_of'``，filter 走 typed allowlist，
  非法 filter/sort 抛 ``ValueError``；显式列投影，无 OFFSET，无深页 COUNT(*)。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

_PROJECTION_TABLES = frozenset(
    {
        "ops_health_current",
        "pipeline_funnel_hourly",
        "account_risk_current",
        "provider_cost_daily",
        "latest_chain_summary",
    }
)

# 每张投影表的 (列名, jsonb_to_recordset cast type)。
_PROJECTION_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "ops_health_current": (
        ("metric_name", "text"), ("metric_value", "numeric"), ("status", "text"),
        ("as_of", "timestamptz"), ("source_high_watermark", "bigint"),
        ("projection_version", "integer"), ("projection_hash", "text"),
    ),
    "pipeline_funnel_hourly": (
        ("hour_start", "timestamptz"), ("stage", "text"), ("event_count", "bigint"),
        ("as_of", "timestamptz"), ("source_high_watermark", "bigint"),
        ("projection_version", "integer"), ("projection_hash", "text"),
    ),
    "account_risk_current": (
        ("portfolio_namespace", "text"), ("market_id", "bigint"), ("component_id", "bigint"),
        ("exposure", "numeric"), ("net_risk_capital", "numeric"), ("cvar", "numeric"),
        ("capital_days", "numeric"), ("as_of", "timestamptz"),
        ("source_high_watermark", "bigint"), ("projection_version", "integer"),
        ("projection_hash", "text"),
    ),
    "provider_cost_daily": (
        ("cost_date", "date"), ("provider", "text"), ("cost_kind", "text"),
        ("amount", "numeric"), ("as_of", "timestamptz"), ("source_high_watermark", "bigint"),
        ("projection_version", "integer"), ("projection_hash", "text"),
    ),
    "latest_chain_summary": (
        ("chain_key", "text"), ("chain_value", "numeric"), ("period_end", "timestamptz"),
        ("as_of", "timestamptz"), ("source_high_watermark", "bigint"),
        ("projection_version", "integer"), ("projection_hash", "text"),
    ),
}

# list 的 typed filter allowlist（值本身也做类型白名单，非法值抛 ValueError）。
_ALLOWED_FILTERS: dict[str, frozenset[str]] = {
    "ops_health_current": frozenset({"metric_name", "status"}),
    "pipeline_funnel_hourly": frozenset({"stage"}),
    "account_risk_current": frozenset({"portfolio_namespace", "market_id", "component_id"}),
    "provider_cost_daily": frozenset({"provider", "cost_kind"}),
    "latest_chain_summary": frozenset({"chain_key"}),
}
_ALLOWED_SORTS = frozenset({"as_of"})
_HEALTH_STATUS = frozenset({"ok", "degraded", "stale", "error"})
_FUNNEL_STAGES = frozenset(
    {"universe", "screen", "cohort", "episode", "forecast", "decision", "execution"}
)
_COST_KINDS = frozenset(
    {"DATA", "LLM", "SEARCH", "INFRASTRUCTURE", "HUMAN", "OPERATIONAL_LOSS"}
)


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


def _check_table(table: str) -> str:
    if table not in _PROJECTION_TABLES:
        raise ValueError(f"unknown projection table: {table!r}")
    return table


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _json_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 rows 转成 JSON 可序列化 list（Decimal→text、datetime→ISO、date→ISO）。

    由驱动（asyncpg）直接序列化为 JSONB array；禁止先 json.dumps 成字符串
    （否则 jsonb_to_recordset 收到 JSON string 而非 array）。
    """
    return [{k: _json_value(v) for k, v in row.items()} for row in rows]


def _insert_sql(table: str, *, on_conflict: str | None = None) -> tuple[str, str]:
    cols = ", ".join(col for col, _ in _PROJECTION_COLUMNS[table])
    casts = ", ".join(f"{col} {cast}" for col, cast in _PROJECTION_COLUMNS[table])
    sql = (
        f"INSERT INTO trading.{table} ({cols}) "
        f"SELECT {cols} FROM jsonb_to_recordset(:rows) AS x({casts})"
    )
    if on_conflict:
        sql += f" ON CONFLICT {on_conflict} DO NOTHING"
    return sql, cols


async def _insert_batch(
    session: AsyncSession, table: str, rows: list[dict[str, Any]], *, on_conflict: str | None
) -> int:
    if not rows:
        return 0
    sql, _ = _insert_sql(table, on_conflict=on_conflict)
    result = await session.execute(
        text(sql).bindparams(bindparam("rows", type_=JSONB())),
        {"rows": _json_rows(rows)},
    )
    affected = result.rowcount
    if affected == -1:  # 某些驱动/路径返回 -1
        return len(rows)
    return affected


class ProjectionRepository:
    """投影 SQL；不持有状态。"""

    # ---------------- write ----------------

    async def _replace_if_new(
        self,
        session: AsyncSession,
        table: str,
        rows: list[dict[str, Any]],
        *,
        watermark: int,
    ) -> int:
        """Apply a projection generation once; older/identical deliveries are no-ops."""
        # Serialize the compare-and-replace sequence.  Taking the lock before
        # reading the current watermark prevents a delayed older consumer from
        # winning a SELECT→TRUNCATE race against a newer generation.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"v2_projection:{table}"},
        )
        current = await session.execute(
            text(
                f"SELECT source_high_watermark, projection_hash FROM trading.{table} "
                "ORDER BY projection_hash"
            )
        )
        existing = current.fetchall()
        if existing:
            current_watermark = max(int(row[0]) for row in existing)
            if watermark < current_watermark:
                return 0
            current_hashes = [row[1] for row in existing]
            incoming_hashes = sorted(str(row["projection_hash"]) for row in rows)
            if watermark == current_watermark and incoming_hashes == current_hashes:
                return 0
        await session.execute(text(f"TRUNCATE TABLE trading.{table} RESTART IDENTITY"))
        normalized = [{**row, "source_high_watermark": watermark} for row in rows]
        return await _insert_batch(session, table, normalized, on_conflict=None)

    async def replace_health_current(
        self, session: AsyncSession, rows: list[dict[str, Any]], watermark: int
    ) -> int:
        return await self._replace_if_new(
            session, "ops_health_current", rows, watermark=watermark
        )

    async def upsert_health_current(
        self, session: AsyncSession, rows: list[dict[str, Any]]
    ) -> int:
        """ops_health_current 幂等插入：``(metric_name, as_of)`` 冲突即跳过。"""
        return await _insert_batch(
            session, "ops_health_current", rows,
            on_conflict="(metric_name, as_of)",
        )

    async def replace_funnel_hourly(
        self,
        session: AsyncSession,
        rows: list[dict[str, Any]],
        watermark: int | None = None,
    ) -> int:
        """整表清空重插 pipeline_funnel_hourly（可重建；``watermark`` 为来源高水位提示）。"""
        return await self._replace_if_new(
            session, "pipeline_funnel_hourly", rows, watermark=watermark or 0
        )

    async def replace_risk_current(
        self,
        session: AsyncSession,
        rows: list[dict[str, Any]],
        watermark: int | None = None,
    ) -> int:
        """整表清空重插 account_risk_current（可重建）。"""
        return await self._replace_if_new(
            session, "account_risk_current", rows, watermark=watermark or 0
        )

    async def replace_provider_cost_daily(
        self,
        session: AsyncSession,
        rows: list[dict[str, Any]],
        watermark: int | None = None,
    ) -> int:
        """整表清空重插 provider_cost_daily（可重建）。"""
        return await self._replace_if_new(
            session, "provider_cost_daily", rows, watermark=watermark or 0
        )

    async def replace_chain_summary(
        self,
        session: AsyncSession,
        rows: list[dict[str, Any]],
        watermark: int | None = None,
    ) -> int:
        """整表清空重插 latest_chain_summary（可重建）。"""
        return await self._replace_if_new(
            session, "latest_chain_summary", rows, watermark=watermark or 0
        )

    # ---------------- keyset list ----------------

    @staticmethod
    def _validate(projection: str, *, filters: dict[str, Any], sort_ts: str) -> None:
        if sort_ts not in _ALLOWED_SORTS:
            raise ValueError(f"unsupported sort_ts: {sort_ts!r}")
        allowed = _ALLOWED_FILTERS[projection]
        unknown = set(filters or {}) - allowed
        if unknown:
            raise ValueError(f"unsupported filters for {projection}: {sorted(unknown)!r}")

    async def list_health_current(
        self,
        session: AsyncSession,
        *,
        after_id: int | None = None,
        after_as_of: datetime | None = None,
        snapshot_as_of: datetime | None = None,
        limit: int = 200,
        sort_ts: str = "as_of",
        metric_name: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if metric_name is not None:
            filters["metric_name"] = metric_name
        if status is not None:
            if status not in _HEALTH_STATUS:
                raise ValueError(f"unsupported status filter: {status!r}")
            filters["status"] = status
        return await self._list(
            session, "ops_health_current", after_id=after_id, after_as_of=after_as_of,
            snapshot_as_of=snapshot_as_of, limit=limit, sort_ts=sort_ts, filters=filters,
        )

    async def list_funnel_hourly(
        self,
        session: AsyncSession,
        *,
        after_id: int | None = None,
        after_as_of: datetime | None = None,
        snapshot_as_of: datetime | None = None,
        limit: int = 200,
        sort_ts: str = "as_of",
        stage: str | None = None,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if stage is not None:
            if stage not in _FUNNEL_STAGES:
                raise ValueError(f"unsupported stage filter: {stage!r}")
            filters["stage"] = stage
        return await self._list(
            session, "pipeline_funnel_hourly", after_id=after_id, after_as_of=after_as_of,
            snapshot_as_of=snapshot_as_of, limit=limit, sort_ts=sort_ts, filters=filters,
        )

    async def list_risk_current(
        self,
        session: AsyncSession,
        *,
        after_id: int | None = None,
        after_as_of: datetime | None = None,
        snapshot_as_of: datetime | None = None,
        limit: int = 200,
        sort_ts: str = "as_of",
        portfolio_namespace: str | None = None,
        market_id: int | None = None,
        component_id: int | None = None,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if portfolio_namespace is not None:
            filters["portfolio_namespace"] = portfolio_namespace
        if market_id is not None:
            filters["market_id"] = market_id
        if component_id is not None:
            filters["component_id"] = component_id
        return await self._list(
            session, "account_risk_current", after_id=after_id, after_as_of=after_as_of,
            snapshot_as_of=snapshot_as_of, limit=limit, sort_ts=sort_ts, filters=filters,
        )

    async def list_provider_cost_daily(
        self,
        session: AsyncSession,
        *,
        after_id: int | None = None,
        after_as_of: datetime | None = None,
        snapshot_as_of: datetime | None = None,
        limit: int = 200,
        sort_ts: str = "as_of",
        provider: str | None = None,
        cost_kind: str | None = None,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if provider is not None:
            filters["provider"] = provider
        if cost_kind is not None:
            if cost_kind not in _COST_KINDS:
                raise ValueError(f"unsupported cost_kind filter: {cost_kind!r}")
            filters["cost_kind"] = cost_kind
        return await self._list(
            session, "provider_cost_daily", after_id=after_id, after_as_of=after_as_of,
            snapshot_as_of=snapshot_as_of, limit=limit, sort_ts=sort_ts, filters=filters,
        )

    async def list_chain_summary(
        self,
        session: AsyncSession,
        *,
        after_id: int | None = None,
        after_as_of: datetime | None = None,
        snapshot_as_of: datetime | None = None,
        limit: int = 200,
        sort_ts: str = "as_of",
        chain_key: str | None = None,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if chain_key is not None:
            filters["chain_key"] = chain_key
        return await self._list(
            session, "latest_chain_summary", after_id=after_id, after_as_of=after_as_of,
            snapshot_as_of=snapshot_as_of, limit=limit, sort_ts=sort_ts, filters=filters,
        )

    async def _list(
        self,
        session: AsyncSession,
        projection: str,
        *,
        after_id: int | None,
        after_as_of: datetime | None,
        snapshot_as_of: datetime | None,
        limit: int,
        sort_ts: str,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate(projection, filters=filters, sort_ts=sort_ts)
        if limit <= 0:
            raise ValueError("limit must be positive")
        # 显式列投影：id（keyset cursor）+ 内容列；不加载大 JSON / raw payload。
        cols = ", ".join(
            ["id"] + [col for col, _ in _PROJECTION_COLUMNS[projection]]
        )
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit + 1}
        if snapshot_as_of is not None:
            clauses.append("created_at <= :snapshot_as_of")
            params["snapshot_as_of"] = snapshot_as_of
        for field, value in filters.items():
            clauses.append(f"{field} = :{field}")
            params[field] = value
        if (after_as_of is None) != (after_id is None):
            raise ValueError("after_as_of and after_id must be provided together")
        if after_as_of is not None:
            if after_id is None:
                raise ValueError("after_id required when after_as_of is set")
            clauses.append("(as_of, id) > (:after_as_of, :after_id)")
            params["after_as_of"] = after_as_of
            params["after_id"] = after_id
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        result = await session.execute(
            text(
                f"SELECT {cols} FROM trading.{projection}{where} "
                f"ORDER BY as_of, id LIMIT :limit"
            ),
            params,
        )
        rows = _rows(result)
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor: dict[str, Any] | None = None
        if page:
            last = page[-1]
            next_cursor = {"sort_time": last["as_of"], "id": last["id"]}
        return {"rows": page, "next_cursor": next_cursor, "has_more": has_more}

    # ---------------- helpers ----------------

    async def clear_table(self, session: AsyncSession, table: str) -> None:
        """整表清空（TRUNCATE RESTART IDENTITY）；重建前调用（可重建 read model）。"""
        _check_table(table)
        await session.execute(text(f"TRUNCATE TABLE trading.{table} RESTART IDENTITY"))

    async def high_watermark(self, session: AsyncSession, table: str) -> int:
        """读每表 ``source_high_watermark`` 最大值（重建用）。"""
        _check_table(table)
        result = await session.execute(
            text(f"SELECT COALESCE(MAX(source_high_watermark), 0) FROM trading.{table}")
        )
        return int(result.scalar_one())

    async def count_rows(self, session: AsyncSession, table: str) -> int:
        """行数（仅测试用；禁止用于深页分页）。"""
        _check_table(table)
        result = await session.execute(text(f"SELECT count(*) FROM trading.{table}"))
        return int(result.scalar_one())
