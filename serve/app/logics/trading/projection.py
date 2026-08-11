"""Read projection Logic（WP-04 Checkpoint D）。

从事实表聚合 → 写五张只读投影；重建幂等：同一事实集重建投影行 hash 精确相同。
只读投影不是事实源：不反向修改 label/forecast/decision/ledger/permission。

约定（任务 §5.5 / §8）：
- 每行 ``projection_hash`` = 内容列（排除 id/created_at/projection_hash/projection_version）
  的 canonical sha256（``app.domain.trading.hashing.canonical_hash``，sort_keys）。
- ``projection_version`` 为重建代数（重建前读当前 max+1）；hash 不含 version，
  因此同事实集重建两次 hash 全等，而 version 可反映重建代数。
- ``as_of`` 一律由事实确定（来源事实最新时间/周期边界），禁止用 clock；保证重建确定。
- ``source_high_watermark`` = 来源事实 max(id)。
- list 转调 repository，做 typed allowlist 校验；响应≤200KiB 由调用方验证。
- 禁止 float：全部 Decimal；``_dec`` 拒绝 float/bool。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.repositories.trading.projection import ProjectionRepository

ZERO = Decimal("0")
# 空事实集的确定性 as_of（epoch；禁止 clock）。
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# 各 rebuild 使用的 source 表与时间列。
_HEALTH_SOURCES: tuple[tuple[str, str], ...] = (
    ("screening_episodes", "created_at"),
    ("forecast_episodes", "created_at"),
    ("trade_decisions", "created_at"),
    ("executions", "created_at"),
    ("ledger_postings", "created_at"),
    ("operating_cost_entries", "created_at"),
    ("ai_invocations", "occurred_at"),
)

_FUNNEL_STAGES: tuple[tuple[str, str, str], ...] = (
    ("universe", "universe_memberships", "created_at"),
    ("screen", "screening_episodes", "created_at"),
    ("cohort", "evaluation_cohorts", "opened_at"),
    ("episode", "forecast_episodes", "created_at"),
    ("forecast", "forecast_submissions", "committed_at"),
    ("decision", "trade_decisions", "trigger_at"),
    ("execution", "executions", "created_at"),
)

_CHAIN_SOURCES: tuple[tuple[str, str], ...] = (
    ("decision.count", "trade_decisions"),
    ("shadow_fill.count", "executions"),
    ("ledger.transaction.count", "ledger_transactions"),
    ("ledger.posting.count", "ledger_postings"),
    ("action_set.count", "action_sets"),
    ("intent.count", "economic_action_intents"),
    ("operating_cost.count", "operating_cost_entries"),
)

# list allowlist（与 repository 层一致）。
_ALLOWED_FILTERS: dict[str, frozenset[str]] = {
    "health_current": frozenset({"metric_name", "status"}),
    "funnel_hourly": frozenset({"stage"}),
    "risk_current": frozenset({"portfolio_namespace", "market_id", "component_id"}),
    "provider_cost_daily": frozenset({"provider", "cost_kind"}),
    "chain_summary": frozenset({"chain_key"}),
}
_ALLOWED_SORTS = frozenset({"as_of"})

_HASH_EXCLUDED = frozenset({"id", "created_at", "projection_hash", "projection_version"})


def _hash_value(value: Any) -> Any:
    # canonical_hash 不处理 datetime.date（仅 datetime）；date → ISO 文本保持确定性。
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    return value


def compute_row_hash(row: dict[str, Any]) -> str:
    """每行投影 hash：内容列（排除 id/created_at/projection_hash/projection_version）的 canonical sha256。"""
    content = {
        key: _hash_value(value)
        for key, value in row.items()
        if key not in _HASH_EXCLUDED
    }
    return canonical_hash(content)


def _dec(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("projection_bool_forbidden")
    if isinstance(value, float):
        raise ValueError("projection_float_forbidden")
    return Decimal(str(value))


# ---------------- 纯确定性风险函数（自实现等价，Decimal-only） ----------------

def net_risk_capital(rows: list[dict[str, Any]]) -> Decimal:
    """净风险资本 = Σ cost_basis（shadow 持仓已占用资本；与 PortfolioLogic 保守口径一致）。"""
    return sum((_dec(row["cost_basis"]) for row in rows), ZERO)


def worst_loss_cvar(
    rows: list[dict[str, Any]], *, alpha: Decimal | str = "0.05"
) -> Decimal:
    """最坏损失 CVaR（确定性下限）。

    净多头 shadow 持仓（positions.quantity >= 0）全部归零时的损失 = Σ cost_basis。
    用冻结 alpha 校验输入，返回同一确定性下限；禁止 float。
    """
    alpha_dec = _dec(alpha)
    if not (ZERO < alpha_dec <= Decimal("1")):
        raise ValueError("projection_alpha_out_of_range")
    return net_risk_capital(rows)


def capital_days(
    rows: list[dict[str, Any]], *, horizon_days: Decimal | str = "0"
) -> Decimal:
    """资本占用天数。

    当前 shadow 为 HOLD_TO_RESOLUTION 且无时间衰减数据：固定冻结 horizon（默认 0）。
    """
    del rows  # 未来可从 executions/position_lots 时间线细化持有天数。
    horizon = _dec(horizon_days)
    if horizon < ZERO:
        raise ValueError("projection_horizon_nonnegative")
    return horizon


def _risk_metrics(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    return {
        "exposure": sum(
            (
                _dec(row["cost_basis"])
                if _dec(row["cost_basis"]) > ZERO
                else abs(_dec(row["quantity"]))
                for row in rows
            ),
            ZERO,
        ),
        "net_risk_capital": net_risk_capital(rows),
        "cvar": worst_loss_cvar(rows),
        "capital_days": capital_days(rows),
    }


class ProjectionLogic:
    """聚合事实 → 写投影；list 做 allowlist 校验。不持有状态。"""

    def __init__(self, repo: ProjectionRepository | None = None) -> None:
        self._repo = repo or ProjectionRepository()

    # ---------------- 纯 helper ----------------

    @staticmethod
    async def _next_version(session, table: str) -> int:
        result = await session.execute(
            text(f"SELECT COALESCE(MAX(projection_version), 0) FROM trading.{table}")
        )
        return int(result.scalar_one()) + 1

    # ---------------- ops_health_current ----------------

    async def rebuild_health_current(self, uow: UnitOfWork) -> int:
        session = uow.session
        count_selects = ", ".join(
            f"(SELECT count(*) FROM trading.{t}) AS c_{i}"
            for i, (t, _) in enumerate(_HEALTH_SOURCES)
        )
        maxid_selects = ", ".join(
            f"(SELECT COALESCE(max(id), 0) FROM trading.{t}) AS m_{i}"
            for i, (t, _) in enumerate(_HEALTH_SOURCES)
        )
        maxts_selects = ", ".join(
            f"(SELECT COALESCE(max({tc}), :epoch) FROM trading.{t}) AS t_{i}"
            for i, (t, tc) in enumerate(_HEALTH_SOURCES)
        )
        row = (
            await session.execute(
                text(
                    f"SELECT {count_selects}, {maxid_selects}, {maxts_selects}"
                ),
                {"epoch": _EPOCH},
            )
        ).mappings().one()
        metrics: list[dict[str, Any]] = []
        for i, (table, _) in enumerate(_HEALTH_SOURCES):
            metrics.append(
                {
                    "table": table,
                    "count": int(row[f"c_{i}"]),
                    "max_id": int(row[f"m_{i}"]),
                    "max_ts": row[f"t_{i}"],
                }
            )
        total = sum(metric["count"] for metric in metrics)
        as_of = max((metric["max_ts"] for metric in metrics), default=_EPOCH)
        watermark = max((metric["max_id"] for metric in metrics), default=0)
        version = await self._next_version(session, "ops_health_current")
        await self._repo.clear_table(session, "ops_health_current")
        rows: list[dict[str, Any]] = []
        for metric in metrics:
            if total == 0:
                status = "error"
            elif metric["count"] > 0:
                status = "ok"
            else:
                status = "stale"
            row_data = {
                "metric_name": metric["table"],
                "metric_value": Decimal(str(metric["count"])),
                "status": status,
                "as_of": as_of,
                "source_high_watermark": watermark,
                "projection_version": version,
            }
            row_data["projection_hash"] = compute_row_hash(row_data)
            rows.append(row_data)
        return await self._repo.upsert_health_current(session, rows)

    # ---------------- pipeline_funnel_hourly ----------------

    async def rebuild_funnel_hourly(
        self, uow: UnitOfWork, hour_start: datetime, hour_end: datetime
    ) -> int:
        session = uow.session
        queries: list[str] = []
        for stage, table, ts in _FUNNEL_STAGES:
            queries.append(
                f"SELECT '{stage}' AS stage, date_trunc('hour', {ts}) AS hour_start, "
                f"count(*) AS event_count FROM trading.{table} "
                f"WHERE {ts} >= :start AND {ts} < :end GROUP BY 1, 2"
            )
        union_sql = " UNION ALL ".join(queries)
        result = await session.execute(
            text(union_sql), {"start": hour_start, "end": hour_end}
        )
        raw = result.fetchall()
        maxid_parts = ", ".join(
            f"(SELECT COALESCE(max(id), 0) FROM trading.{t} "
            f"WHERE {ts} >= :start AND {ts} < :end)"
            for _, t, ts in _FUNNEL_STAGES
        )
        watermark = int(
            (
                await session.execute(
                    text(f"SELECT GREATEST({maxid_parts})"),
                    {"start": hour_start, "end": hour_end},
                )
            ).scalar_one()
        )
        version = await self._next_version(session, "pipeline_funnel_hourly")
        rows: list[dict[str, Any]] = []
        for stage, bucket_hour, count in raw:
            row_data = {
                "hour_start": bucket_hour,
                "stage": stage,
                "event_count": int(count),
                "as_of": hour_end,
                "source_high_watermark": watermark,
                "projection_version": version,
            }
            row_data["projection_hash"] = compute_row_hash(row_data)
            rows.append(row_data)
        return await self._repo.replace_funnel_hourly(
            session, rows, watermark=watermark
        )

    # ---------------- account_risk_current ----------------

    async def rebuild_risk_current(
        self, uow: UnitOfWork, portfolio_namespace: str | None = None
    ) -> int:
        if portfolio_namespace is not None and not portfolio_namespace.startswith("shadow-"):
            raise ValueError("account_risk_current only covers shadow-* namespaces")
        session = uow.session
        where = "WHERE portfolio_namespace LIKE 'shadow-%'"
        params: dict[str, Any] = {}
        if portfolio_namespace is not None:
            where += " AND portfolio_namespace = :ns"
            params["ns"] = portfolio_namespace
        result = await session.execute(
            text(
                "SELECT id, portfolio_namespace, market_id, component_id, quantity, "
                "cost_basis, updated_at FROM trading.positions "
                f"{where} ORDER BY portfolio_namespace, market_id, component_id, id"
            ),
            params,
        )
        source_rows = result.mappings().all()
        watermark = max((int(r["id"]) for r in source_rows), default=0)
        version = await self._next_version(session, "account_risk_current")
        groups: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
        for r in source_rows:
            key = (r["portfolio_namespace"], r["market_id"], r["component_id"])
            groups.setdefault(key, []).append(r)
        rows: list[dict[str, Any]] = []
        for (ns, market_id, component_id), group in groups.items():
            metrics = _risk_metrics(group)
            as_of = max((r["updated_at"] for r in group), default=_EPOCH)
            row_data = {
                "portfolio_namespace": ns,
                "market_id": market_id,
                "component_id": component_id,
                "exposure": metrics["exposure"],
                "net_risk_capital": metrics["net_risk_capital"],
                "cvar": metrics["cvar"],
                "capital_days": metrics["capital_days"],
                "as_of": as_of,
                "source_high_watermark": watermark,
                "projection_version": version,
            }
            row_data["projection_hash"] = compute_row_hash(row_data)
            rows.append(row_data)
        return await self._repo.replace_risk_current(session, rows, watermark=watermark)

    # ---------------- provider_cost_daily ----------------

    async def rebuild_provider_cost_daily(
        self, uow: UnitOfWork, day_start: datetime, day_end: datetime
    ) -> int:
        session = uow.session
        result = await session.execute(
            text(
                "SELECT date_trunc('day', COALESCE(period_end, period_start, created_at))::date "
                "       AS cost_date, "
                "       COALESCE(allocation_policy->>'provider', 'system') AS provider, "
                "       cost_kind, SUM(amount) AS amount "
                "FROM trading.operating_cost_entries "
                "WHERE COALESCE(period_end, period_start, created_at) >= :start "
                "  AND COALESCE(period_end, period_start, created_at) < :end "
                "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
            ),
            {"start": day_start, "end": day_end},
        )
        raw = result.fetchall()
        watermark = int(
            (
                await session.execute(
                    text(
                        "SELECT COALESCE(max(id), 0) FROM trading.operating_cost_entries "
                        "WHERE COALESCE(period_end, period_start, created_at) >= :start "
                        "  AND COALESCE(period_end, period_start, created_at) < :end"
                    ),
                    {"start": day_start, "end": day_end},
                )
            ).scalar_one()
        )
        version = await self._next_version(session, "provider_cost_daily")
        rows: list[dict[str, Any]] = []
        for cost_date, provider, cost_kind, amount in raw:
            row_data = {
                "cost_date": cost_date,
                "provider": provider,
                "cost_kind": cost_kind,
                "amount": _dec(amount),
                "as_of": day_end,
                "source_high_watermark": watermark,
                "projection_version": version,
            }
            row_data["projection_hash"] = compute_row_hash(row_data)
            rows.append(row_data)
        return await self._repo.replace_provider_cost_daily(
            session, rows, watermark=watermark
        )

    # ---------------- latest_chain_summary ----------------

    async def rebuild_chain_summary(
        self, uow: UnitOfWork, period_end: datetime
    ) -> int:
        session = uow.session
        count_selects = ", ".join(
            f"(SELECT count(*) FROM trading.{t}) AS c_{i}"
            for i, (_, t) in enumerate(_CHAIN_SOURCES)
        )
        maxid_selects = ", ".join(
            f"(SELECT COALESCE(max(id), 0) FROM trading.{t}) AS m_{i}"
            for i, (_, t) in enumerate(_CHAIN_SOURCES)
        )
        row = (
            await session.execute(text(f"SELECT {count_selects}, {maxid_selects}"))
        ).mappings().one()
        watermark = max(
            (int(row[f"m_{i}"]) for i in range(len(_CHAIN_SOURCES))), default=0
        )
        version = await self._next_version(session, "latest_chain_summary")
        rows: list[dict[str, Any]] = []
        for i, (chain_key, _) in enumerate(_CHAIN_SOURCES):
            row_data = {
                "chain_key": chain_key,
                "chain_value": Decimal(str(int(row[f"c_{i}"]))),
                "period_end": period_end,
                "as_of": period_end,
                "source_high_watermark": watermark,
                "projection_version": version,
            }
            row_data["projection_hash"] = compute_row_hash(row_data)
            rows.append(row_data)
        return await self._repo.replace_chain_summary(
            session, rows, watermark=watermark
        )

    # ---------------- rebuild_all ----------------

    async def _source_bounds(self, session) -> dict[str, datetime]:
        parts: list[str] = []
        for _, table, ts in _FUNNEL_STAGES:
            parts.append(f"(SELECT min({ts}) FROM trading.{table})")
            parts.append(f"(SELECT max({ts}) FROM trading.{table})")
        for table, ts in _HEALTH_SOURCES:
            parts.append(f"(SELECT min({ts}) FROM trading.{table})")
        row = (await session.execute(text(f"SELECT {', '.join(parts)}"))).one()
        values = [v for v in row if v is not None]
        if not values:
            return {
                "hour_start": _EPOCH,
                "hour_end": _EPOCH,
                "day_start": _EPOCH,
                "day_end": _EPOCH,
                "period_end": _EPOCH,
            }
        first = min(values)
        last = max(values)
        from datetime import timedelta

        hour_start = first.replace(minute=0, second=0, microsecond=0)
        hour_end = last.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        day_start = first.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = last.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return {
            "hour_start": hour_start,
            "hour_end": hour_end,
            "day_start": day_start,
            "day_end": day_end,
            "period_end": last,
        }

    async def rebuild_all(self, uow_factory: Callable[[], UnitOfWork]) -> dict[str, int]:
        """顺序重建五表；每表一个独立事务（UoW）。

        ``uow_factory`` 为零参 callable，每次调用返回一个新的 ``UnitOfWork``
        （如 ``functools.partial(UnitOfWork, sessions)`` 或 ``app.db.uow.uow_factory``）。
        可被多次调用；重建前清空，同一事实集重建 hash 精确相同。
        """
        async with uow_factory() as uow:
            bounds = await self._source_bounds(uow.session)
        results: dict[str, int] = {}

        async with uow_factory() as uow:
            results["ops_health_current"] = await self.rebuild_health_current(uow)

        async with uow_factory() as uow:
            results["pipeline_funnel_hourly"] = await self.rebuild_funnel_hourly(
                uow, hour_start=bounds["hour_start"], hour_end=bounds["hour_end"]
            )

        async with uow_factory() as uow:
            results["account_risk_current"] = await self.rebuild_risk_current(uow)

        async with uow_factory() as uow:
            results["provider_cost_daily"] = await self.rebuild_provider_cost_daily(
                uow, day_start=bounds["day_start"], day_end=bounds["day_end"]
            )

        async with uow_factory() as uow:
            results["latest_chain_summary"] = await self.rebuild_chain_summary(
                uow, period_end=bounds["period_end"]
            )
        return results

    # ---------------- list ----------------

    async def list(
        self,
        uow: UnitOfWork,
        projection: str,
        *,
        after_id: int | None = None,
        after_as_of: datetime | None = None,
        limit: int = 200,
        sort_ts: str = "as_of",
        filters: dict[str, Any] | None = None,
        sorts: list[str] | None = None,
    ) -> dict[str, Any]:
        if projection not in _ALLOWED_FILTERS:
            raise ValueError(f"unknown projection: {projection!r}")
        if sort_ts not in _ALLOWED_SORTS:
            raise ValueError(f"unsupported sort_ts: {sort_ts!r}")
        filters = filters or {}
        sorts = sorts or []
        unknown_filters = set(filters) - _ALLOWED_FILTERS[projection]
        if unknown_filters:
            raise ValueError(
                f"unsupported filters for {projection}: {sorted(unknown_filters)!r}"
            )
        unknown_sorts = set(sorts) - _ALLOWED_SORTS
        if unknown_sorts:
            raise ValueError(
                f"unsupported sorts for {projection}: {sorted(unknown_sorts)!r}"
            )
        method = getattr(self._repo, f"list_{projection}")
        return await method(
            uow.session,
            after_id=after_id,
            after_as_of=after_as_of,
            limit=limit,
            sort_ts=sort_ts,
            **filters,
        )
