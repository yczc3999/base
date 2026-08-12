"""WP-07A §9 Admin Read API performance and capacity acceptance harness.

Formal mode is the default and is intentionally not configurable downwards:

* PostgreSQL contains 33,336 Markets, Episodes, and AI Invocations each
  (100,008 representative rows in total).
* Requests traverse the real FastAPI -> RBAC -> Logic -> Repository ->
  PostgreSQL -> FastAPI/ORJSON serialization path.
* A complete ascending Market traversal reaches the tail while concurrent
  post-``as_of`` inserts are running.  The frozen set must have no loss or
  duplicates.
* The final quarter of the traversal is the deep-page sample.  Its p95/p99
  total latency gates are 500 ms / 1 s.
* 32 workers run for at least 60 wall-clock seconds.  Every worker must sustain
  at least one completed request per second.
* Pool wait is measured around the real SQLAlchemy queue checkout, and pool/
  PostgreSQL connection peaks must stay within the configured API profile.
* The report includes real response byte counts, business-query/RBAC/
  serialization/total latency, CPU/RSS/event-loop lag, the target index plan,
  clean Git commit evidence, seed evidence, assertion counts, and cleanup.

``WP07A_PERF_MODE=quick`` is available only for implementation iteration.  It
uses a small dataset and short duration, writes a separate ``*_quick.json``,
and can never emit the formal ``hard_assertions=PASS`` result.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import math
import os
import resource
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


SERVE_DIR = Path(__file__).resolve().parents[3]
REPO_DIR = SERVE_DIR.parent
ADMIN_DATABASE_URL = os.environ.get(
    "V2_TEST_ADMIN_DATABASE_URL", "postgresql+psycopg:///postgres"
)
MODE = os.environ.get("WP07A_PERF_MODE", "formal").strip().lower()
if MODE not in {"formal", "quick"}:
    raise RuntimeError("WP07A_PERF_MODE must be formal or quick")

FORMAL = MODE == "formal"
ROWS_PER_DOMAIN = 33_336 if FORMAL else int(
    os.environ.get("WP07A_PERF_QUICK_ROWS_PER_DOMAIN", "600")
)
CONCURRENT_WORKERS = 32 if FORMAL else int(
    os.environ.get("WP07A_PERF_QUICK_WORKERS", "8")
)
CONCURRENT_SECONDS = 60.0 if FORMAL else float(
    os.environ.get("WP07A_PERF_QUICK_SECONDS", "3")
)
PAGE_LIMIT = 200
MAX_RESPONSE_BYTES = 200 * 1024
DEEP_FRACTION = 0.75
TARGET_INDEX = "ix_v2_admin_pm_markets_keyset"
API_PATHS = {
    "markets": "/api/admin/v2/markets",
    "episodes": "/api/admin/v2/episodes",
    "ai_invocations": "/api/admin/v2/ai-invocations",
}
BASE_SCHEMA_FIXTURE = SERVE_DIR / "tests/trading/fixtures/base_legacy_schema.sql"
OUTPUT_PATH = Path(
    "/tmp/pm_v2_perf_smoke_7a.json"
    if FORMAL
    else "/tmp/pm_v2_perf_smoke_7a_quick.json"
)


def _percentile(values: list[float], percentile: float) -> float:
    """Nearest-rank percentile, in the input unit."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _summary(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "p50": round(_percentile(values, 0.50), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "p99": round(_percentile(values, 0.99), 3),
        "max": round(max(values), 3) if values else 0.0,
    }


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git_evidence() -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=REPO_DIR, check=True,
            capture_output=True, text=True,
        )
        return completed.stdout.strip()

    head = run("rev-parse", "HEAD")
    status = run("status", "--porcelain=v1", "--untracked-files=all")
    submodules = run("submodule", "status", "--recursive")
    diff_check = subprocess.run(
        ["git", "diff", "--check"], cwd=REPO_DIR,
        capture_output=True, text=True,
    )
    return {
        "commit": head,
        "clean": status == "",
        "status_entry_count": len(status.splitlines()) if status else 0,
        "diff_check_ok": diff_check.returncode == 0,
        "submodules_clean": not any(
            line.startswith(("-", "+", "U"))
            for line in submodules.splitlines()
        ),
    }


@dataclass
class RequestSample:
    phase: str
    endpoint: str
    worker: int | None = None
    total_ms: float = 0.0
    business_query_ms: float = 0.0
    permission_ms: float = 0.0
    permission_query_ms: float = 0.0
    serialization_ms: float = 0.0
    pool_wait_ms: float = 0.0
    response_bytes: int = 0
    business_query_count: int = 0
    permission_query_count: int = 0
    status_code: int = 0
    envelope_code: int | None = None
    business_statements: list[str] = field(default_factory=list)


_CURRENT_SAMPLE: contextvars.ContextVar[RequestSample | None] = contextvars.ContextVar(
    "wp07a_perf_sample", default=None
)
_QUERY_STAGE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "wp07a_perf_query_stage", default="business"
)


class RuntimeInstrumentation:
    """Harness-only instrumentation; every patch/listener is restored."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.pool = engine.sync_engine.pool
        self.active_checkouts = 0
        self.peak_checkouts = 0
        self.open_connections = 0
        self.peak_open_connections = 0
        self._installed = False
        self._original_do_get: Callable[..., Any] | None = None
        self._original_permission: Callable[..., Awaitable[list[str]]] | None = None
        self._original_serialize: Callable[..., Awaitable[Any]] | None = None
        self._original_json_render: Callable[..., bytes] | None = None
        self._original_orjson_render: Callable[..., bytes] | None = None
        self._listeners: list[tuple[Any, str, Callable[..., Any]]] = []

    def _listen(self, target: Any, name: str, fn: Callable[..., Any]) -> None:
        event.listen(target, name, fn)
        self._listeners.append((target, name, fn))

    def install(self) -> None:
        if self._installed:
            return
        self._installed = True

        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            context._wp07a_perf_started = time.perf_counter()

        def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            started = getattr(context, "_wp07a_perf_started", None)
            sample = _CURRENT_SAMPLE.get()
            if started is None or sample is None:
                return
            elapsed_ms = (time.perf_counter() - started) * 1000
            if _QUERY_STAGE.get() == "permission":
                sample.permission_query_ms += elapsed_ms
                sample.permission_query_count += 1
            else:
                sample.business_query_ms += elapsed_ms
                sample.business_query_count += 1
                if len(sample.business_statements) < 6:
                    sample.business_statements.append(str(statement))

        def on_connect(dbapi_connection, connection_record):
            self.open_connections += 1
            self.peak_open_connections = max(
                self.peak_open_connections, self.open_connections
            )

        def on_close(dbapi_connection, connection_record):
            self.open_connections = max(0, self.open_connections - 1)

        def on_checkout(dbapi_connection, connection_record, connection_proxy):
            self.active_checkouts += 1
            self.peak_checkouts = max(self.peak_checkouts, self.active_checkouts)

        def on_checkin(dbapi_connection, connection_record):
            self.active_checkouts = max(0, self.active_checkouts - 1)

        self._listen(self.engine.sync_engine, "before_cursor_execute", before_cursor_execute)
        self._listen(self.engine.sync_engine, "after_cursor_execute", after_cursor_execute)
        self._listen(self.pool, "connect", on_connect)
        self._listen(self.pool, "close", on_close)
        self._listen(self.pool, "checkout", on_checkout)
        self._listen(self.pool, "checkin", on_checkin)

        # QueuePool has no public "wait started" event.  Timing _do_get measures
        # the actual queue wait/connection creation interval around checkout.
        # Formal setup prewarms every slot, so the concurrent sample is queue
        # acquisition time rather than TCP/connection-creation time.
        self._original_do_get = self.pool._do_get

        def timed_do_get():
            started = time.perf_counter()
            try:
                return self._original_do_get()
            finally:
                sample = _CURRENT_SAMPLE.get()
                if sample is not None:
                    sample.pool_wait_ms += (time.perf_counter() - started) * 1000

        self.pool._do_get = timed_do_get

        # Keep the real read-only require_all_perms -> role/menu DB lookup.
        # Production's admin_read_only UoW deliberately bypasses Redis.
        from app.logics.admin_user import admin_user_logic

        self._original_permission = admin_user_logic.get_user_perms

        async def timed_permission(db, user_id: int, is_super: bool = False):
            sample = _CURRENT_SAMPLE.get()
            stage_token = _QUERY_STAGE.set("permission")
            started = time.perf_counter()
            try:
                return await self._original_permission(db, user_id, is_super)
            finally:
                if sample is not None:
                    sample.permission_ms += (time.perf_counter() - started) * 1000
                _QUERY_STAGE.reset(stage_token)

        admin_user_logic.get_user_perms = timed_permission

        # Measure both FastAPI's jsonable encoding and the actual JSON/ORJSON
        # byte rendering.  Mutable RequestSample crosses BaseHTTPMiddleware task
        # contexts even when ContextVar assignment itself is copied.
        import fastapi.routing as fastapi_routing
        from fastapi.responses import ORJSONResponse
        from starlette.responses import JSONResponse

        self._original_serialize = fastapi_routing.serialize_response
        self._original_json_render = JSONResponse.render
        self._original_orjson_render = ORJSONResponse.render

        async def timed_serialize(*args, **kwargs):
            sample = _CURRENT_SAMPLE.get()
            started = time.perf_counter()
            try:
                return await self._original_serialize(*args, **kwargs)
            finally:
                if sample is not None:
                    sample.serialization_ms += (time.perf_counter() - started) * 1000

        def timed_json_render(response, content):
            sample = _CURRENT_SAMPLE.get()
            started = time.perf_counter()
            try:
                return self._original_json_render(response, content)
            finally:
                if sample is not None:
                    sample.serialization_ms += (time.perf_counter() - started) * 1000

        def timed_orjson_render(response, content):
            sample = _CURRENT_SAMPLE.get()
            started = time.perf_counter()
            try:
                return self._original_orjson_render(response, content)
            finally:
                if sample is not None:
                    sample.serialization_ms += (time.perf_counter() - started) * 1000

        fastapi_routing.serialize_response = timed_serialize
        JSONResponse.render = timed_json_render
        ORJSONResponse.render = timed_orjson_render

    def reset_peaks(self) -> None:
        # Called only while no request owns a connection.
        self.peak_checkouts = self.active_checkouts
        self.peak_open_connections = self.open_connections

    def restore(self) -> None:
        if not self._installed:
            return
        self._installed = False

        import fastapi.routing as fastapi_routing
        from app.logics.admin_user import admin_user_logic
        from fastapi.responses import ORJSONResponse
        from starlette.responses import JSONResponse

        if self._original_do_get is not None:
            self.pool._do_get = self._original_do_get
        if self._original_permission is not None:
            admin_user_logic.get_user_perms = self._original_permission
        if self._original_serialize is not None:
            fastapi_routing.serialize_response = self._original_serialize
        if self._original_json_render is not None:
            JSONResponse.render = self._original_json_render
        if self._original_orjson_render is not None:
            ORJSONResponse.render = self._original_orjson_render
        for target, name, fn in reversed(self._listeners):
            event.remove(target, name, fn)
        self._listeners.clear()


def _alembic_upgrade(db_url: str, revision: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(SERVE_DIR / "alembic"))
    engine = create_engine(db_url, poolclass=NullPool)
    connection = engine.connect()
    cfg.attributes["connection"] = connection
    try:
        command.upgrade(cfg, revision)
    finally:
        connection.close()
        engine.dispose()


def _load_base_schema(db_url: str) -> None:
    url = make_url(db_url)
    command_line = ["psql", "-q", "-v", "ON_ERROR_STOP=1"]
    if url.host:
        command_line.extend(["--host", url.host])
    if url.port:
        command_line.extend(["--port", str(url.port)])
    if url.username:
        command_line.extend(["--username", url.username])
    command_line.extend([
        "--dbname", str(url.database), "-f", str(BASE_SCHEMA_FIXTURE),
    ])
    environment = os.environ.copy()
    if url.password:
        # Keep credentials out of argv, stdout, stderr, and the JSON report.
        environment["PGPASSWORD"] = url.password
    completed = subprocess.run(
        command_line,
        cwd=SERVE_DIR,
        env=environment,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("base_schema_fixture_failed")


def _migrate_with_base(db_url: str) -> None:
    _alembic_upgrade(db_url, "b1000052")
    _load_base_schema(db_url)
    _alembic_upgrade(db_url, "b1000070")


def _seed_rbac(db_url: str) -> dict[str, int]:
    engine = create_engine(db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            authorized_user_id = conn.execute(text(
                "INSERT INTO public.admin_users "
                "(username, password, status, is_super_admin) "
                "VALUES ('wp07a_perf_authorized', 'fixture-only', 1, false) "
                "RETURNING id"
            )).scalar_one()
            denied_user_id = conn.execute(text(
                "INSERT INTO public.admin_users "
                "(username, password, status, is_super_admin) "
                "VALUES ('wp07a_perf_denied', 'fixture-only', 1, false) "
                "RETURNING id"
            )).scalar_one()
            role_id = conn.execute(text(
                "INSERT INTO public.roles (name, label, status) "
                "VALUES ('wp07a_perf_reader', 'WP07A Perf Reader', 1) RETURNING id"
            )).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO public.admin_user_roles (admin_user_id, role_id) "
                    "VALUES (:uid, :rid)"
                ),
                {"uid": authorized_user_id, "rid": role_id},
            )
            result = conn.execute(
                text(
                    "INSERT INTO public.role_menus (role_id, menu_id) "
                    "SELECT :rid, id FROM public.menus "
                    "WHERE perms IN "
                    "('v2:markets:view','v2:episodes:view','v2:ai:view') "
                    "RETURNING menu_id"
                ),
                {"rid": role_id},
            )
            binding_count = len(result.fetchall())
            if binding_count != 3:
                raise RuntimeError("rbac_seed_binding_count_invalid")
        return {
            "authorized_user_id": int(authorized_user_id),
            "denied_user_id": int(denied_user_id),
            "role_id": int(role_id),
            "binding_count": binding_count,
        }
    finally:
        engine.dispose()


def _seed_high_volume(db_url: str) -> dict[str, Any]:
    """Fast deterministic PostgreSQL-native seed; no result set is preloaded."""
    seed_contract = {
        "method": "INSERT_SELECT_GENERATE_SERIES",
        "rows_per_domain": ROWS_PER_DOMAIN,
        "market_start": "2026-08-01T00:00:00Z",
        "episode_start": "2026-08-02T00:00:00Z",
        "ai_start": "2026-08-03T00:00:00Z",
        "domains": ["markets", "episodes", "ai_invocations"],
    }
    engine = create_engine(db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(text("SET LOCAL synchronous_commit = off"))
            conn.execute(text(
                "INSERT INTO trading.pm_markets "
                "(gamma_market_id, question, slug, ticker, active, closed, "
                " accepting_orders, neg_risk, volume, liquidity, content_hash, "
                " raw_artifact_ref, created_at) "
                "SELECT g::text, 'question-' || g, 'market-' || g, 'token-' || g, "
                " false, false, true, false, 0, 0, lpad(to_hex(g), 64, '0'), "
                " 'artifact-' || g, TIMESTAMPTZ '2026-08-01 00:00:00+00' "
                " + g * INTERVAL '1 second' "
                "FROM generate_series(1, :n) AS series(g)"
            ), {"n": ROWS_PER_DOMAIN})
            conn.execute(text(
                "INSERT INTO trading.forecast_episodes "
                "(episode_key, decision_opportunity_id, component_version_id, "
                " strategy_version_id, objective_contract_id, trigger, cutoff_at, "
                " horizon, experiment_variant, status, cognition_status, created_at) "
                "SELECT lpad(to_hex(g), 64, '0'), 0, 0, 0, 0, 'manual', "
                " TIMESTAMPTZ '2026-08-20 00:00:00+00', '2w', 'baseline', "
                " 'DRAFT', 'PENDING', TIMESTAMPTZ '2026-08-02 00:00:00+00' "
                " + g * INTERVAL '1 second' "
                "FROM generate_series(1, :n) AS series(g)"
            ), {"n": ROWS_PER_DOMAIN})
            conn.execute(text(
                "INSERT INTO trading.ai_invocations "
                "(occurred_at, invocation_key, episode_id, stage, role, attempt_no, "
                " requested_provider, requested_route, requested_model, network_policy, "
                " context_class, taint_report, input_manifest, input_manifest_hash, "
                " lifecycle_state, tool_count, search_count, cost_estimated, "
                " pricing_snapshot) "
                "SELECT TIMESTAMPTZ '2026-08-03 00:00:00+00' "
                " + g * INTERVAL '1 second', lpad(to_hex(g), 64, '0'), 0, "
                " 'scoring', 'scorer', 1, 'deepseek', 'primary', "
                " 'deepseek-v4', 'NONE', 'CONTRACT', '{}'::jsonb, '{}'::jsonb, "
                " lpad(to_hex(g), 64, '0'), 'PLANNED', 0, 0, 0, '{}'::jsonb "
                "FROM generate_series(1, :n) AS series(g)"
            ), {"n": ROWS_PER_DOMAIN})
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("ANALYZE trading.pm_markets"))
            conn.execute(text("ANALYZE trading.forecast_episodes"))
            conn.execute(text("ANALYZE trading.ai_invocations"))
        with engine.connect() as conn:
            counts = {
                "markets": int(conn.execute(text(
                    "SELECT count(*) FROM trading.pm_markets"
                )).scalar_one()),
                "episodes": int(conn.execute(text(
                    "SELECT count(*) FROM trading.forecast_episodes"
                )).scalar_one()),
                "ai_invocations": int(conn.execute(text(
                    "SELECT count(*) FROM trading.ai_invocations"
                )).scalar_one()),
            }
            baseline_market_max_id = int(conn.execute(text(
                "SELECT max(id) FROM trading.pm_markets"
            )).scalar_one())
        return {
            "contract": seed_contract,
            "contract_sha256": _sha256_json(seed_contract),
            "counts": counts,
            "total_rows": sum(counts.values()),
            "baseline_market_max_id": baseline_market_max_id,
        }
    finally:
        engine.dispose()


def _create_database(admin_url: str, database_name: str) -> tuple[str, str]:
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin.dispose()
    base = make_url(admin_url)
    sync_url = base.set(
        drivername="postgresql+psycopg", database=database_name
    ).render_as_string(hide_password=False)
    async_url = base.set(
        drivername="postgresql+asyncpg", database=database_name
    ).render_as_string(hide_password=False)
    return sync_url, async_url


def _drop_database(admin_url: str, database_name: str) -> bool:
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:database AND pid<>pg_backend_pid()"
                ),
                {"database": database_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
            remains = int(conn.execute(
                text("SELECT count(*) FROM pg_database WHERE datname=:database"),
                {"database": database_name},
            ).scalar_one())
            return remains == 0
    finally:
        admin.dispose()


def _asyncpg_connect_kwargs(db_url: str, application_name: str) -> dict[str, Any]:
    url = make_url(db_url)
    kwargs: dict[str, Any] = {
        "database": url.database,
        "server_settings": {"application_name": application_name},
    }
    if url.username:
        kwargs["user"] = url.username
    if url.password:
        kwargs["password"] = url.password
    if url.host:
        kwargs["host"] = url.host
    if url.port:
        kwargs["port"] = url.port
    return kwargs


def _plan_summary(db_url: str, as_of: str) -> dict[str, Any]:
    """EXPLAIN a deep predicate identical to the Market keyset access path."""
    boundary_gamma_id = max(1, int(ROWS_PER_DOMAIN * 0.90))
    engine = create_engine(db_url, poolclass=NullPool)
    sql = (
        "SELECT id, created_at FROM trading.pm_markets "
        "WHERE created_at <= CAST(:as_of AS timestamptz) "
        "AND (created_at, id) > (CAST(:sort_time AS timestamptz), CAST(:id AS bigint)) "
        "ORDER BY created_at ASC, id ASC LIMIT 201"
    )
    try:
        with engine.connect() as conn:
            boundary = conn.execute(text(
                "SELECT created_at, id FROM trading.pm_markets "
                "WHERE gamma_market_id=:gamma_id"
            ), {"gamma_id": str(boundary_gamma_id)}).one()
            raw_plan = conn.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql),
                {"as_of": as_of, "sort_time": boundary.created_at,
                 "id": int(boundary.id)},
            ).scalar_one()
            index_definition = conn.execute(text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname='trading' AND indexname=:index_name"
            ), {"index_name": TARGET_INDEX}).scalar_one()
    finally:
        engine.dispose()
    if isinstance(raw_plan, str):
        raw_plan = json.loads(raw_plan)
    document = raw_plan[0]
    index_names: set[str] = set()
    node_types: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        node_types.append(str(node.get("Node Type", "")))
        if node.get("Index Name"):
            index_names.add(str(node["Index Name"]))
        for child in node.get("Plans", []):
            walk(child)

    walk(document["Plan"])
    plan_json = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return {
        "sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "sql_has_offset": "OFFSET" in sql.upper(),
        "target_index": TARGET_INDEX,
        "target_index_definition": index_definition,
        "index_names": sorted(index_names),
        "node_types": node_types,
        "planning_time_ms": round(float(document.get("Planning Time", 0.0)), 3),
        "execution_time_ms": round(float(document.get("Execution Time", 0.0)), 3),
        "plan_sha256": hashlib.sha256(plan_json.encode("utf-8")).hexdigest(),
        "target_index_used": TARGET_INDEX in index_names,
    }


def _side_effect_fact_counts(db_url: str) -> dict[str, int]:
    """Facts that must remain empty during this read-only API harness."""
    tables = (
        "external_call_attempts",
        "chain_operations",
        "exchange_orders",
        "exchange_trades",
        "ledger_transactions",
    )
    engine = create_engine(db_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return {
                table: int(conn.execute(text(
                    f"SELECT count(*) FROM trading.{table}"
                )).scalar_one())
                for table in tables
            }
    finally:
        engine.dispose()


def _gate(report: dict[str, Any], name: str, passed: bool, **evidence: Any) -> None:
    report.setdefault("gates", {})[name] = {"pass": bool(passed), **evidence}


async def _request(
    client,
    *,
    path: str,
    params: dict[str, str],
    phase: str,
    worker: int | None = None,
) -> tuple[Any, RequestSample]:
    sample = RequestSample(phase=phase, endpoint=path, worker=worker)
    token = _CURRENT_SAMPLE.set(sample)
    started = time.perf_counter()
    try:
        response = await client.get(
            path, params=params,
            headers={"Authorization": "Bearer wp07a-local-fixture"},
        )
        sample.total_ms = (time.perf_counter() - started) * 1000
        sample.status_code = response.status_code
        sample.response_bytes = len(response.content)
        try:
            sample.envelope_code = int(response.json().get("code"))
        except Exception:
            sample.envelope_code = None
        return response, sample
    finally:
        if sample.total_ms == 0.0:
            sample.total_ms = (time.perf_counter() - started) * 1000
        _CURRENT_SAMPLE.reset(token)


async def _event_loop_lag_sampler(stop: asyncio.Event, values: list[float]) -> None:
    loop = asyncio.get_running_loop()
    interval = 0.05
    target = loop.time() + interval
    while not stop.is_set():
        await asyncio.sleep(max(0.0, target - loop.time()))
        now = loop.time()
        values.append(max(0.0, now - target) * 1000)
        target += interval
        if target < now - interval:
            target = now + interval


async def _postgres_connection_sampler(
    db_url: str,
    application_name: str,
    stop: asyncio.Event,
    samples: list[int],
) -> None:
    import asyncpg

    conn = await asyncpg.connect(**_asyncpg_connect_kwargs(
        db_url, "wp07a_perf_connection_sampler"
    ))
    try:
        while not stop.is_set():
            count = await conn.fetchval(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname=current_database() AND application_name=$1",
                application_name,
            )
            samples.append(int(count))
            await asyncio.sleep(0.05)
    finally:
        await conn.close()


async def _concurrent_writer(
    db_url: str,
    stop: asyncio.Event,
    started: asyncio.Event,
    counter: dict[str, int],
) -> None:
    import asyncpg

    conn = await asyncpg.connect(**_asyncpg_connect_kwargs(
        db_url, "wp07a_perf_snapshot_writer"
    ))
    try:
        await conn.execute("SET session_replication_role = replica")
        sequence = 1
        while not stop.is_set():
            marker = f"concurrent-{sequence}-{uuid.uuid4().hex[:12]}"
            await conn.execute(
                "INSERT INTO trading.pm_markets "
                "(gamma_market_id, question, slug, ticker, active, closed, "
                " accepting_orders, neg_risk, volume, liquidity, content_hash, "
                " raw_artifact_ref, created_at) "
                "VALUES ($1, $2, $3, $4, false, false, true, false, 0, 0, "
                " $5, $6, clock_timestamp())",
                marker, marker, marker, marker,
                hashlib.sha256(marker.encode("utf-8")).hexdigest(),
                marker,
            )
            counter["inserted"] += 1
            started.set()
            sequence += 1
            await asyncio.sleep(0.01)
    finally:
        await conn.close()


async def _warm_pool(
    sessions: async_sessionmaker[AsyncSession], capacity: int,
) -> None:
    async def hold() -> None:
        async with sessions() as session:
            await session.execute(text("SELECT pg_sleep(0.05)"))

    await asyncio.gather(*(hold() for _ in range(capacity)))


async def _run_api(
    *,
    sync_db_url: str,
    async_db_url: str,
    seed: dict[str, Any],
    rbac: dict[str, int],
    report: dict[str, Any],
) -> None:
    import httpx

    from app.config import settings
    from app.controllers.admin.trading.common import reset_admin_logic
    from app.db.cursor import CursorCodec, derive_key
    from app.deps import AuthInfo, require_auth
    from app.logics.trading.admin_read import AdminReadLogic
    from app.main import app
    from app.services.database import build_connect_args, get_db

    profile = settings.pool_profile("api")
    pool_size = profile.pool_size
    max_overflow = profile.max_overflow
    if not FORMAL:
        quick_capacity = int(os.environ.get("WP07A_PERF_QUICK_POOL_CAPACITY", "0"))
        if quick_capacity:
            pool_size = quick_capacity
            max_overflow = 0
    pool_capacity = pool_size + max_overflow
    budget = settings.connection_budget()
    async_engine = create_async_engine(
        async_db_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=settings.DB_POOL_TIMEOUT_S,
        pool_recycle=settings.DB_POOL_RECYCLE_S,
        pool_pre_ping=profile.pre_ping,
        connect_args=build_connect_args(settings, profile),
    )
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    instrumentation = RuntimeInstrumentation(async_engine)
    auth_state = {"user_id": rbac["authorized_user_id"]}
    all_samples: list[RequestSample] = []
    writer_stop = asyncio.Event()
    writer_task: asyncio.Task | None = None

    async def override_db():
        async with sessions() as session:
            yield session

    async def override_auth() -> AuthInfo:
        return AuthInfo(
            auth_state["user_id"], "admin", "wp07a-perf",
            "wp07a-local-fixture", {"is_super_admin": False},
        )

    # Controllers and require_all_perms converge through get_admin_read_db,
    # which itself depends on get_db.  Overriding only the leaf preserves the
    # real shared READ ONLY request UoW.
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_auth] = override_auth
    reset_admin_logic(AdminReadLogic(CursorCodec(derive_key("wp07a-perf-key"))))
    instrumentation.install()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)

    report["api_path"] = {
        "transport": "httpx.ASGITransport",
        "stack": [
            "FastAPI routing", "require_all_perms", "PostgreSQL RBAC lookup",
            "AdminReadLogic", "AdminReadRepository", "asyncpg",
            "FastAPI serialization", "ORJSONResponse",
        ],
        "auth_fixture": (
            "token verification overridden; non-super-admin require_all_perms and "
            "role/menu database lookup are real in the same READ ONLY request UoW; "
            "the production admin_read_only path bypasses Redis"
        ),
        "api_pool_profile": {
            "name": profile.name,
            "application_name": profile.application_name,
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "capacity": pool_capacity,
            "statement_timeout_s": profile.statement_timeout_s,
        },
        "global_connection_budget": budget.model_dump(),
        "quick_pool_capacity_override": (
            pool_capacity
            if not FORMAL and pool_capacity != profile.per_instance_capacity
            else None
        ),
    }
    _gate(
        report, "global_connection_budget",
        budget.is_within_limit() and budget.remaining >= 0,
        total=budget.total,
        limit=budget.limit,
        remaining=budget.remaining,
        per_profile=budget.per_profile,
    )

    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://wp07a.local", timeout=30.0
        ) as client:
            # Actual RBAC denial, then three representative authorized domain reads.
            auth_state["user_id"] = rbac["denied_user_id"]
            denied, denied_sample = await _request(
                client, path=API_PATHS["markets"], params={"limit": "1"},
                phase="rbac-denied",
            )
            all_samples.append(denied_sample)
            denied_body = denied.json()
            _gate(
                report, "rbac_denied_fixture",
                denied.status_code == 200 and denied_body.get("code") == 403,
                http_status=denied.status_code,
                envelope_code=denied_body.get("code"),
            )

            auth_state["user_id"] = rbac["authorized_user_id"]
            domain_samples: list[RequestSample] = []
            for domain, path in API_PATHS.items():
                response, sample = await _request(
                    client, path=path,
                    params={"limit": str(PAGE_LIMIT), "direction": "asc"},
                    phase="representative-domain",
                )
                all_samples.append(sample)
                domain_samples.append(sample)
                body = response.json()
                if response.status_code != 200 or body.get("code") != 0:
                    raise RuntimeError(f"representative_domain_failed:{domain}")
                if not body["data"]["items"]:
                    raise RuntimeError(f"representative_domain_empty:{domain}")
            report["representative_domains"] = {
                "domains": list(API_PATHS),
                "latency_ms": {
                    sample.endpoint: round(sample.total_ms, 3)
                    for sample in domain_samples
                },
                "response_bytes": {
                    sample.endpoint: sample.response_bytes
                    for sample in domain_samples
                },
            }
            _gate(
                report, "representative_domains",
                len(domain_samples) == 3
                and all(s.envelope_code == 0 for s in domain_samples),
                domain_count=len(domain_samples),
            )
            _gate(
                report, "real_rbac_query_path",
                all(s.permission_query_count >= 2 for s in domain_samples),
                permission_query_counts=[s.permission_query_count for s in domain_samples],
            )

            # Full ASC traversal freezes as_of before starting a writer.  ASC is
            # deliberate: without an actual as_of predicate, new rows append at
            # the tail and make this gate fail.
            first_response, first_sample = await _request(
                client, path=API_PATHS["markets"],
                params={"limit": str(PAGE_LIMIT), "direction": "asc"},
                phase="deep-traversal",
            )
            all_samples.append(first_sample)
            first_data = first_response.json()["data"]
            if first_response.status_code != 200 or first_response.json().get("code") != 0:
                raise RuntimeError("market_traversal_first_page_failed")
            seen = [int(item["id"]) for item in first_data["items"]]
            as_of_values = {first_data["as_of"]}
            filter_hash_values = {first_data["filter_hash"]}
            cursor = first_data["next_cursor"]
            traversal_samples = [first_sample]
            deep_threshold = int(seed["counts"]["markets"] * DEEP_FRACTION)
            deep_samples: list[RequestSample] = (
                [first_sample] if len(seen) >= deep_threshold else []
            )
            pages = 1
            writer_started = asyncio.Event()
            writer_counter = {"inserted": 0}
            writer_task = asyncio.create_task(_concurrent_writer(
                async_db_url, writer_stop, writer_started, writer_counter
            ))
            writer_ready_wait = asyncio.create_task(writer_started.wait())
            done, _pending = await asyncio.wait(
                {writer_task, writer_ready_wait}, timeout=5.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if writer_task in done:
                await writer_task  # propagate the writer's concrete failure
            if writer_ready_wait not in done:
                writer_ready_wait.cancel()
                await asyncio.gather(writer_ready_wait, return_exceptions=True)
                raise RuntimeError("snapshot_writer_start_timeout")
            while first_data["has_more"]:
                response, sample = await _request(
                    client, path=API_PATHS["markets"],
                    params={
                        "limit": str(PAGE_LIMIT), "direction": "asc",
                        "cursor": cursor,
                    },
                    phase="deep-traversal",
                )
                all_samples.append(sample)
                traversal_samples.append(sample)
                body = response.json()
                if response.status_code != 200 or body.get("code") != 0:
                    raise RuntimeError("market_traversal_page_failed")
                data = body["data"]
                seen.extend(int(item["id"]) for item in data["items"])
                as_of_values.add(data["as_of"])
                filter_hash_values.add(data["filter_hash"])
                pages += 1
                if len(seen) >= deep_threshold:
                    deep_samples.append(sample)
                if not data["has_more"]:
                    if data["next_cursor"] is not None:
                        raise RuntimeError("terminal_cursor_not_null")
                    break
                cursor = data["next_cursor"]
                if pages > math.ceil(seed["counts"]["markets"] / PAGE_LIMIT) + 2:
                    raise RuntimeError("market_traversal_page_bound_exceeded")
            writer_stop.set()
            await writer_task
            writer_task = None

            expected_count = seed["counts"]["markets"]
            unique_count = len(set(seen))
            out_of_snapshot = sum(
                item_id > seed["baseline_market_max_id"] for item_id in seen
            )
            lost = max(0, expected_count - unique_count)
            duplicates = len(seen) - unique_count
            traversal_ok = (
                len(seen) == expected_count
                and unique_count == expected_count
                and lost == 0
                and duplicates == 0
                and out_of_snapshot == 0
                and len(as_of_values) == 1
                and len(filter_hash_values) == 1
                and seen[-1] == seed["baseline_market_max_id"]
                and writer_counter["inserted"] > 0
            )
            report["traversal"] = {
                "direction": "asc",
                "page_limit": PAGE_LIMIT,
                "pages": pages,
                "expected_items": expected_count,
                "seen_items": len(seen),
                "unique_items": unique_count,
                "lost": lost,
                "duplicates": duplicates,
                "out_of_snapshot": out_of_snapshot,
                "as_of_value_count": len(as_of_values),
                "filter_hash_value_count": len(filter_hash_values),
                "concurrent_inserts": writer_counter["inserted"],
                "tail_id": seen[-1] if seen else None,
                "expected_tail_id": seed["baseline_market_max_id"],
            }
            _gate(report, "complete_snapshot_traversal", traversal_ok,
                  **report["traversal"])

            deep_total = [s.total_ms for s in deep_samples]
            deep_query = [s.business_query_ms for s in deep_samples]
            report["latencies_ms"] = {
                "deep_total": _summary(deep_total),
                "deep_business_query": _summary(deep_query),
            }
            deep_p95 = _percentile(deep_total, 0.95)
            deep_p99 = _percentile(deep_total, 0.99)
            _gate(
                report, "deep_page_latency",
                bool(deep_samples) and deep_p95 <= 500.0 and deep_p99 <= 1000.0,
                sample_start_fraction=DEEP_FRACTION,
                sample_count=len(deep_samples),
                p95_ms=round(deep_p95, 3),
                p99_ms=round(deep_p99, 3),
                threshold_p95_ms=500.0,
                threshold_p99_ms=1000.0,
            )
            actual_deep_sql = "\n".join(
                statement
                for sample in deep_samples
                for statement in sample.business_statements
            )
            _gate(
                report, "actual_admin_sql_has_no_offset",
                "OFFSET" not in actual_deep_sql.upper(),
                captured_statement_count=sum(
                    len(s.business_statements) for s in deep_samples
                ),
                captured_sql_sha256=hashlib.sha256(
                    actual_deep_sql.encode("utf-8")
                ).hexdigest(),
            )
            _gate(
                report, "actual_admin_sql_uses_frozen_as_of",
                bool(re.search(
                    r"(?:[a-z_]+\.)?created_at\s*<=\s*", actual_deep_sql,
                    flags=re.IGNORECASE,
                )),
                captured_sql_sha256=hashlib.sha256(
                    actual_deep_sql.encode("utf-8")
                ).hexdigest(),
            )

            plan = await asyncio.to_thread(
                _plan_summary, sync_db_url, next(iter(as_of_values))
            )
            report["index_plan"] = plan
            _gate(
                report, "deep_cursor_index_plan",
                (
                    plan["target_index_used"] if FORMAL
                    else TARGET_INDEX in plan["target_index_definition"]
                ) and not plan["sql_has_offset"],
                target_index=TARGET_INDEX,
                index_names=plan["index_names"],
                node_types=plan["node_types"],
                sql_has_offset=plan["sql_has_offset"],
                execution_time_ms=plan["execution_time_ms"],
            )

            # Fill the configured queue before the timed concurrency phase so
            # connection establishment does not masquerade as steady-state wait.
            await _warm_pool(sessions, pool_capacity)
            instrumentation.reset_peaks()
            concurrent_samples: list[RequestSample] = []
            worker_counts = [0 for _ in range(CONCURRENT_WORKERS)]
            start_gate = asyncio.Event()
            sampler_stop = asyncio.Event()
            loop_lag_ms: list[float] = []
            postgres_connection_samples: list[int] = []

            async def worker(worker_id: int, deadline: float) -> None:
                await start_gate.wait()
                while time.perf_counter() < deadline:
                    response, sample = await _request(
                        client, path=API_PATHS["markets"],
                        params={"limit": "50", "direction": "desc"},
                        phase="concurrent", worker=worker_id,
                    )
                    all_samples.append(sample)
                    concurrent_samples.append(sample)
                    body = response.json()
                    if response.status_code != 200 or body.get("code") != 0:
                        raise RuntimeError(f"concurrent_request_failed:{worker_id}")
                    if sample.response_bytes > MAX_RESPONSE_BYTES:
                        raise RuntimeError("concurrent_response_too_large")
                    worker_counts[worker_id] += 1

            concurrent_started = time.perf_counter()
            deadline = concurrent_started + CONCURRENT_SECONDS
            cpu_started = time.process_time()
            usage_started = resource.getrusage(resource.RUSAGE_SELF)
            lag_task = asyncio.create_task(
                _event_loop_lag_sampler(sampler_stop, loop_lag_ms)
            )
            connection_task = asyncio.create_task(_postgres_connection_sampler(
                async_db_url, profile.application_name,
                sampler_stop, postgres_connection_samples,
            ))
            workers = [
                asyncio.create_task(worker(worker_id, deadline))
                for worker_id in range(CONCURRENT_WORKERS)
            ]
            start_gate.set()
            await asyncio.gather(*workers)
            concurrent_elapsed = time.perf_counter() - concurrent_started
            cpu_elapsed = time.process_time() - cpu_started
            usage_finished = resource.getrusage(resource.RUSAGE_SELF)
            sampler_stop.set()
            await asyncio.gather(lag_task, connection_task)

            worker_rates = [count / concurrent_elapsed for count in worker_counts]
            total_requests = sum(worker_counts)
            response_bytes = [s.response_bytes for s in concurrent_samples]
            pool_waits = [s.pool_wait_ms for s in concurrent_samples]
            report["concurrency"] = {
                "workers": CONCURRENT_WORKERS,
                "required_wall_seconds": CONCURRENT_SECONDS,
                "actual_wall_seconds": round(concurrent_elapsed, 3),
                "requests": total_requests,
                "requests_per_second": round(total_requests / concurrent_elapsed, 3),
                "per_worker_counts": worker_counts,
                "per_worker_rate_min": round(min(worker_rates), 3),
                "per_worker_rate_p50": round(_percentile(worker_rates, 0.50), 3),
                "per_worker_rate_max": round(max(worker_rates), 3),
            }
            _gate(
                report, "concurrent_32_workers_60_seconds",
                CONCURRENT_WORKERS == (32 if FORMAL else CONCURRENT_WORKERS)
                and concurrent_elapsed >= CONCURRENT_SECONDS
                and min(worker_rates) >= 1.0,
                workers=CONCURRENT_WORKERS,
                actual_wall_seconds=round(concurrent_elapsed, 3),
                required_wall_seconds=CONCURRENT_SECONDS,
                per_worker_rate_min=round(min(worker_rates), 3),
                required_per_worker_rate=1.0,
                requests=total_requests,
            )

            report["pool"] = {
                "wait_ms": _summary(pool_waits),
                "checkout_peak": instrumentation.peak_checkouts,
                "open_connection_peak": instrumentation.peak_open_connections,
                "postgres_connection_peak": max(postgres_connection_samples, default=0),
                "postgres_sample_count": len(postgres_connection_samples),
                "api_profile_capacity": pool_capacity,
            }
            pool_wait_p95 = _percentile(pool_waits, 0.95)
            _gate(
                report, "concurrent_pool_wait",
                bool(pool_waits) and pool_wait_p95 <= 20.0,
                p95_ms=round(pool_wait_p95, 3), threshold_ms=20.0,
                sample_count=len(pool_waits),
            )
            pg_peak = max(postgres_connection_samples, default=0)
            _gate(
                report, "api_pool_connection_peak",
                instrumentation.peak_checkouts <= pool_capacity
                and instrumentation.peak_open_connections <= pool_capacity
                and pg_peak <= pool_capacity,
                checkout_peak=instrumentation.peak_checkouts,
                open_connection_peak=instrumentation.peak_open_connections,
                postgres_connection_peak=pg_peak,
                api_profile_capacity=pool_capacity,
            )

            report["resources"] = {
                "cpu_seconds": round(cpu_elapsed, 3),
                "cpu_percent_of_one_core": round(
                    100.0 * cpu_elapsed / concurrent_elapsed, 3
                ),
                "user_cpu_seconds_delta": round(
                    usage_finished.ru_utime - usage_started.ru_utime, 3
                ),
                "system_cpu_seconds_delta": round(
                    usage_finished.ru_stime - usage_started.ru_stime, 3
                ),
                "rss_peak_kb": int(usage_finished.ru_maxrss),
                "event_loop_lag_ms": _summary(loop_lag_ms),
            }
            _gate(
                report, "resource_telemetry_present",
                concurrent_elapsed > 0
                and cpu_elapsed >= 0
                and usage_finished.ru_maxrss > 0
                and bool(loop_lag_ms),
                cpu_seconds=round(cpu_elapsed, 3),
                rss_peak_kb=int(usage_finished.ru_maxrss),
                event_loop_sample_count=len(loop_lag_ms),
            )

            report["response_bytes"] = _summary(
                [float(value) for value in response_bytes]
            )
            response_max = max(
                (s.response_bytes for s in all_samples), default=0
            )
            report["response_bytes"]["all_phases_max"] = response_max
            report["response_bytes"]["limit"] = MAX_RESPONSE_BYTES
            _gate(
                report, "response_size_budget",
                response_max <= MAX_RESPONSE_BYTES,
                max_bytes=response_max, limit_bytes=MAX_RESPONSE_BYTES,
                measured_response_count=len(all_samples),
            )

            report["latencies_ms"].update({
                "concurrent_total": _summary(
                    [s.total_ms for s in concurrent_samples]
                ),
                "concurrent_business_query": _summary(
                    [s.business_query_ms for s in concurrent_samples]
                ),
                "concurrent_permission": _summary(
                    [s.permission_ms for s in concurrent_samples]
                ),
                "concurrent_permission_query": _summary(
                    [s.permission_query_ms for s in concurrent_samples]
                ),
                "concurrent_serialization": _summary(
                    [s.serialization_ms for s in concurrent_samples]
                ),
            })
            _gate(
                report, "latency_stage_instrumentation",
                bool(concurrent_samples)
                and all(s.business_query_count >= 2 for s in concurrent_samples)
                and all(s.permission_query_count >= 2 for s in concurrent_samples)
                and any(s.serialization_ms > 0 for s in concurrent_samples),
                request_count=len(concurrent_samples),
                business_query_count_min=min(
                    s.business_query_count for s in concurrent_samples
                ),
                permission_query_count_min=min(
                    s.permission_query_count for s in concurrent_samples
                ),
                serialization_nonzero_count=sum(
                    s.serialization_ms > 0 for s in concurrent_samples
                ),
            )

            report["test_counts"] = {
                "representative_domain_requests": len(domain_samples),
                "rbac_denial_requests": 1,
                "deep_traversal_requests": len(traversal_samples),
                "deep_page_samples": len(deep_samples),
                "concurrent_workers": CONCURRENT_WORKERS,
                "concurrent_requests": total_requests,
                "measured_api_requests": len(all_samples),
                "event_loop_samples": len(loop_lag_ms),
                "postgres_connection_samples": len(postgres_connection_samples),
            }
            fact_counts = await asyncio.to_thread(
                _side_effect_fact_counts, sync_db_url
            )
            report["external_effect_counters"] = {
                "outbound_external_network": 0,
                **fact_counts,
                "external_server_processes": 0,
            }
            _gate(
                report, "zero_external_side_effects",
                all(
                    value == 0
                    for value in report["external_effect_counters"].values()
                ),
                **report["external_effect_counters"],
            )
    finally:
        writer_stop.set()
        if writer_task is not None:
            await asyncio.gather(writer_task, return_exceptions=True)
        instrumentation.restore()
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_auth, None)
        reset_admin_logic(None)
        await async_engine.dispose()
        report["runtime_cleanup"] = {
            "dependency_overrides_removed": (
                get_db not in app.dependency_overrides
                and require_auth not in app.dependency_overrides
            ),
            "instrumentation_restored": not instrumentation._installed,
            "async_engine_disposed": True,
            "writer_stopped": writer_task is None or writer_task.done(),
            "node_or_http_server_started": False,
        }
        _gate(
            report, "runtime_resource_cleanup",
            report["runtime_cleanup"]["dependency_overrides_removed"]
            and report["runtime_cleanup"]["instrumentation_restored"]
            and report["runtime_cleanup"]["async_engine_disposed"]
            and report["runtime_cleanup"]["writer_stopped"]
            and not report["runtime_cleanup"]["node_or_http_server_started"],
            **report["runtime_cleanup"],
        )


def main() -> int:
    database_name = f"pm_v2_perf7a_{uuid.uuid4().hex[:12]}"
    report: dict[str, Any] = {
        "schema_version": "wp07a-admin-api-perf-v2",
        "mode": MODE,
        "formal_acceptance": FORMAL,
        "plan": {
            "task": "WP-07A §9 Admin Read API performance/capacity",
            "representative_rows_required": 100_006 if FORMAL else None,
            "workers_required": 32 if FORMAL else None,
            "wall_seconds_required": 60.0 if FORMAL else None,
        },
        "gates": {},
        "hard_assertions": "FAIL" if FORMAL else "QUICK_FAIL",
    }
    created = False
    clean_before = _git_evidence()
    report["code"] = {"before": clean_before}
    required_commit = os.environ.get("WP07A_PERF_REQUIRED_COMMIT", "").strip()
    if FORMAL and not required_commit:
        report["code"]["required_commit_configuration"] = (
            "WP07A_PERF_REQUIRED_COMMIT not set; HEAD is the required clean commit"
        )
    sync_db_url = ""
    error: BaseException | None = None
    try:
        sync_db_url, async_db_url = _create_database(
            ADMIN_DATABASE_URL, database_name
        )
        created = True
        _migrate_with_base(sync_db_url)
        rbac = _seed_rbac(sync_db_url)
        seed = _seed_high_volume(sync_db_url)
        report["seed"] = seed
        report["rbac_seed"] = {
            "authorized_user_id": rbac["authorized_user_id"],
            "denied_user_id": rbac["denied_user_id"],
            "role_id": rbac["role_id"],
            "binding_count": rbac["binding_count"],
            "super_admin": False,
        }
        _gate(
            report, "representative_dataset",
            seed["total_rows"] >= (100_006 if FORMAL else ROWS_PER_DOMAIN * 3)
            and all(
                seed["counts"][domain] == ROWS_PER_DOMAIN
                for domain in ("markets", "episodes", "ai_invocations")
            ),
            total_rows=seed["total_rows"],
            counts=seed["counts"],
            required_total=100_006 if FORMAL else ROWS_PER_DOMAIN * 3,
        )
        asyncio.run(_run_api(
            sync_db_url=sync_db_url,
            async_db_url=async_db_url,
            seed=seed,
            rbac=rbac,
            report=report,
        ))
    except BaseException as exc:  # report + cleanup before propagating exit code
        error = exc
        if not FORMAL:
            import traceback

            traceback.print_exc()
        report["failure"] = {
            "type": type(exc).__name__,
            "reason_code": "perf_harness_execution_failed",
            "detail_sha256": hashlib.sha256(
                str(exc).encode("utf-8", errors="replace")
            ).hexdigest(),
        }
    finally:
        database_removed = True
        if created:
            try:
                database_removed = _drop_database(
                    ADMIN_DATABASE_URL, database_name
                )
            except BaseException as cleanup_exc:
                database_removed = False
                if error is None:
                    error = cleanup_exc
                    report["failure"] = {
                        "type": type(cleanup_exc).__name__,
                        "reason": "temporary_database_cleanup_failed",
                    }
        report["cleanup"] = {
            "temporary_database_removed": database_removed,
            "temporary_database_name_sha256": hashlib.sha256(
                database_name.encode("utf-8")
            ).hexdigest(),
            "output_file": str(OUTPUT_PATH),
            "temporary_files_created_other_than_output": 0,
            "temporary_files_residual_other_than_output": 0,
            "node_server_processes_started": 0,
            "node_server_processes_residual": 0,
        }
        _gate(
            report, "temporary_resource_cleanup", database_removed,
            temporary_database_removed=database_removed,
            temporary_files_residual_other_than_output=0,
            node_server_processes_residual=0,
        )

    clean_after = _git_evidence()
    report["code"]["after"] = clean_after
    same_commit = clean_before["commit"] == clean_after["commit"]
    clean_commit = (
        clean_before["clean"] and clean_after["clean"]
        and clean_before["diff_check_ok"] and clean_after["diff_check_ok"]
        and clean_before["submodules_clean"] and clean_after["submodules_clean"]
        and same_commit
        and (not required_commit or clean_after["commit"] == required_commit)
    )
    _gate(
        report, "clean_code_commit",
        clean_commit if FORMAL else True,
        required=FORMAL,
        commit=clean_after["commit"],
        clean_before=clean_before["clean"],
        clean_after=clean_after["clean"],
        same_commit_during_run=same_commit,
        required_commit=required_commit or clean_after["commit"],
        required_commit_matches=(
            not required_commit or clean_after["commit"] == required_commit
        ),
        diff_check_ok=(
            clean_before["diff_check_ok"] and clean_after["diff_check_ok"]
        ),
        submodules_clean=(
            clean_before["submodules_clean"] and clean_after["submodules_clean"]
        ),
    )
    report.setdefault("test_counts", {})["hard_gate_count"] = len(report["gates"])
    report["test_counts"]["hard_gate_failures"] = sum(
        not gate.get("pass", False) for gate in report["gates"].values()
    )
    all_pass = (
        error is None
        and bool(report["gates"])
        and all(gate.get("pass", False) for gate in report["gates"].values())
    )
    if FORMAL:
        report["hard_assertions"] = "PASS" if all_pass else "FAIL"
    else:
        report["hard_assertions"] = "QUICK_PASS" if all_pass else "QUICK_FAIL"
    report["completed_at"] = datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    OUTPUT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output_sha256 = hashlib.sha256(OUTPUT_PATH.read_bytes()).hexdigest()
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    print(f"OUTPUT_SHA256={output_sha256}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
