"""WP-04 hard performance contract on a real PostgreSQL database (read projections).

Run from ``/code/pollymarket/v2/serve``::

    V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \\
      .venv/bin/python -m tests.trading.performance.evaluation_projection_smoke

Bounded evaluation pool = ``3+1`` (pool_size=3, max_overflow=1).  Uses the real
ProjectionLogic/ProjectionRepository/UnitOfWork/constraints; no bulk INSERT impersonating
the measured path.  Source facts are seeded through the real decision→shadow-fill chain
plus a set-based deterministic fixture of 100k shadow positions (fixture excluded from
the measured windows, mirroring the WP-03 smoke harness).

Contracts（任务 §8 / docs/performance-cache-database-design.md §2、§9）:
- Gate 1: keyset list over ≥100,000 account_risk_current rows → p95≤500ms, p99≤1s,
  per-page response ≤200KiB;
- Gate 2: scientific replay of one frozen canonical score set through the real
  ``ReplayLogic`` (exact quote/label cutoff → score recomputation → five-layer artifact
  hash) → p95≤5s, p99≤15s; the replay path cannot invoke network/search/execution.
- Gate 3: DB pool wait p95≤20ms; projection rebuild lost/duplicate=0; rebuild twice →
  projection row hash 全等;
- Gate 4: fixed metric workload → throughput, WAL, RSS, CPU/connection peaks, 10s window;
- 输出 ``/tmp/pm_v2_perf_smoke_4.json`` 含 seed / git commit / 数据规模 / p50/p95/p99 /
  SQL plan 摘要 / hard assertions；临时数据库清理为 0。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SERVE_DIR))

from app.db.uow import UnitOfWork  # noqa: E402
from app.logics.trading.decision import DecisionLogic  # noqa: E402
from app.logics.trading.execution import ShadowExecutionLogic  # noqa: E402
from app.logics.trading.projection import ProjectionLogic  # noqa: E402
from app.logics.trading.replay import ReplayLogic  # noqa: E402
from app.repositories.trading.audit import AuditRepository  # noqa: E402
from app.repositories.trading.evaluation import EvaluationRepository  # noqa: E402
from app.repositories.trading.cohort import CohortRepository  # noqa: E402
from app.repositories.trading.decision import DecisionRepository  # noqa: E402
from app.repositories.trading.execution import ExecutionRepository  # noqa: E402
from app.repositories.trading.forecast import ForecastRepository  # noqa: E402
from app.repositories.trading.ledger import LedgerRepository  # noqa: E402
from app.repositories.trading.projection import ProjectionRepository  # noqa: E402
from app.repositories.trading.semantics import SemanticsRepository  # noqa: E402
from app.repositories.trading.workflow import WorkflowRepository  # noqa: E402
from app.schemas.trading.decision import (  # noqa: E402
    ActionCandidateInput,
    ActionSetInput,
    MarketRelativeInput,
    PortfolioGateInput,
)
from app.schemas.trading.execution import ShadowFillInput  # noqa: E402
from tests.trading.integration.test_v2_decision_shadow_workflow import (  # noqa: E402
    _build_blind_committed_episode,
    _quote_map,
    _seed,
)
from tests.trading.replay.test_v2_p3_learning_replay import (  # noqa: E402
    _insert_empty_metric_run,
)
from datetime import datetime, timezone  # noqa: E402

# 与 replay 集成测试一致：book checkpoints 落在已建分区（当前日）。
FIXED = datetime(2026, 8, 11, 3, 4, 5, tzinfo=timezone.utc)

ADMIN_URL = os.environ.get(
    "V2_TEST_ADMIN_DATABASE_URL", "postgresql+psycopg:///postgres"
)
TEMP_PREFIX = "pm_v2_perf_4_"
POOL_SIZE = 3
MAX_OVERFLOW = 1
POOL_BUDGET = POOL_SIZE + MAX_OVERFLOW

# 硬门阈值（任务 §8 / 设计 §2）。
MAX_KEYSET_P95_MS = 500.0
MAX_KEYSET_P99_MS = 1000.0
MAX_RESPONSE_BYTES = 200 * 1024
MAX_REPLAY_P95_MS = 5000.0
MAX_REPLAY_P99_MS = 15000.0
MAX_POOL_WAIT_P95_MS = 20.0

RISK_ROW_TARGET = 100_000
REPLAY_RUNS = 5
METRIC_WINDOW_SECONDS = 10.0
KEYSET_PAGE_LIMIT = 500
SEED_LABEL = "deterministic/wp-04-read-projection-performance-v1"
OUT_PATH = Path("/tmp/pm_v2_perf_smoke_4.json")

PROJECTION_TABLES = [
    "ops_health_current", "pipeline_funnel_hourly", "account_risk_current",
    "provider_cost_daily", "latest_chain_summary",
]


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(SERVE_DIR), text=True
        ).strip()
    except Exception:
        return "unknown"


def _git_worktree_evidence() -> dict[str, Any]:
    """Record whether timings came from HEAD or a dirty remediation worktree."""
    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=str(SERVE_DIR),
        )
        return {
            "git_worktree_clean": not bool(status),
            "git_status_sha256": hashlib.sha256(status).hexdigest(),
            "git_status_entries": len(status.splitlines()),
        }
    except Exception:
        return {
            "git_worktree_clean": None,
            "git_status_sha256": "unknown",
            "git_status_entries": None,
        }


def _pct(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * p / 100)))
    return round(ordered[index], 3)


def _percentiles(values: list[float]) -> dict[str, float]:
    return {"p50": _pct(values, 50), "p95": _pct(values, 95), "p99": _pct(values, 99)}


async def _run_replay_once(
    *,
    env: dict,
    ctx: dict,
    episode: int,
    spec_id: int,
    sequence: int,
    benchmark_started: float,
    engine: Any,
    pool_wait_ms: list[float],
) -> float:
    """单条完整决策 deterministic replay：create→reveal→MR→G7A→G7B→terminalize→fill。"""
    logic = DecisionLogic(env["decision"], env["wf"])
    exec_logic = ShadowExecutionLogic(env["execution"], env["ledger"])
    trigger_at = FIXED + timedelta(hours=12, microseconds=sequence)
    quote_reveal_at = trigger_at + timedelta(seconds=1)
    decided_at = trigger_at + timedelta(seconds=2)
    ns = f"shadow-perf-replay-{sequence}"
    started = time.perf_counter()

    async with UnitOfWork(env["sessions"]) as uow:
        wait_started = time.perf_counter()
        await uow.session.connection()
        pool_wait_ms.append((time.perf_counter() - wait_started) * 1000)
        created = await logic.create_decision(
            uow, episode_id=episode, trigger_at=trigger_at,
            experiment_variant=f"perf-replay-{sequence}",
        )
        assert created.ok, created.reason
        revealed = await logic.reveal(
            uow, trade_decision_id=created.trade_decision_id,
            quote_reveal_at=quote_reveal_at, quotes=_quote_map(ctx),
        )
        assert revealed.ok, revealed.reason
        await uow.session.execute(
            text(
                "INSERT INTO trading.operating_cost_entries "
                "(cost_key,cost_kind,amount,release_manifest_id,episode_id,trade_decision_id,"
                " allocation_policy) VALUES (:k,'INFRASTRUCTURE',0,:r,:e,:d,"
                " '{\"kind\":\"fixed_marginal\",\"evidence\":\"observed_zero\","
                " \"provider\":\"perf-infra\"}'::jsonb)"
            ),
            {"k": f"perf-replay-cost-{created.trade_decision_id}", "r": ctx["release"],
             "e": episode, "d": created.trade_decision_id},
        )
        mr = await logic.market_relative(
            uow, trade_decision_id=created.trade_decision_id,
            input_=MarketRelativeInput(decision_mode="BLIND_ONLY"),
        )
        assert mr.ok, mr.reason
        # SELL_TO_CLOSE（close leg）：同 opportunity 只允许一个 open intent（DB trigger），
        # close 每条 replay 使用独立 namespace + 预置 position，避免 open-intent 冲突。
        quantity = Decimal(sequence + 1)
        g7a = await logic.run_g7a(
            uow, trade_decision_id=created.trade_decision_id,
            candidates=[
                ActionCandidateInput(
                    contract_spec_id=spec_id, token_id=ctx["yes_token"],
                    action_type="SELL_TOKEN_TO_CLOSE", target_quantity=quantity,
                )
            ],
        )
        assert g7a.ok, g7a.reason
        g7b = await logic.run_g7b(
            uow, trade_decision_id=created.trade_decision_id,
            portfolio=PortfolioGateInput(portfolio_namespace=ns),
        )
        assert g7b.ok, g7b.reason
        terminal = await logic.terminalize(
            uow, trade_decision_id=created.trade_decision_id,
            action_set=ActionSetInput(
                disposition="ACTION",
                selected_action_type="SELL_TOKEN_TO_CLOSE",
                legs={"close": {spec_id: {ctx["yes_token"]: quantity}}},
            ),
            underwriting=None,
            decided_at=decided_at,
        )
        assert terminal.ok, terminal.reason
        action_sets = (
            await uow.session.execute(
                text("SELECT id FROM trading.action_sets WHERE trade_decision_id=:d"),
                {"d": created.trade_decision_id},
            )
        ).scalars().all()
        legs = await env["decision"].action_set_legs(uow.session, action_sets[0])
        intent_id = (
            await uow.session.execute(
                text(
                    "SELECT id FROM trading.economic_action_intents "
                    "WHERE trade_decision_id=:d AND status='COMMITTED'"
                ),
                {"d": created.trade_decision_id},
            )
        ).scalar_one()
        fill = await exec_logic.shadow_fill(
            uow,
            fill=ShadowFillInput(
                execution_key=f"perf-exec-{created.trade_decision_id}",
                economic_action_intent_id=intent_id,
                action_set_leg_id=legs[0]["id"],
            ),
        )
        assert fill.ok, fill.reason
        assert fill.status == "FILLED"
    return time.perf_counter() - started


async def _keyset_page(
    *,
    env: dict,
    logic: ProjectionLogic,
    cursor: dict[str, Any] | None,
    pool_wait_ms: list[float],
    peak_holder: dict[str, int] | None = None,
    engine: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, bool]:
    async with UnitOfWork(env["sessions"]) as uow:
        wait_started = time.perf_counter()
        await uow.session.connection()
        pool_wait_ms.append((time.perf_counter() - wait_started) * 1000)
        if peak_holder is not None and engine is not None:
            peak_holder["peak"] = max(
                peak_holder["peak"], int(engine.pool.checkedout())
            )
        page = await logic.list(
            uow, "risk_current",
            cursor=cursor, limit=KEYSET_PAGE_LIMIT,
        )
    return page["rows"], page["next_cursor"], page["has_more"]


async def _run_scientific_replay_once(
    *, env: dict, manifest_hash: str, seed: int, sequence: int,
    pool_wait_ms: list[float],
) -> float:
    """Replay one frozen score set and recompute its five-layer artifact."""
    logic = ReplayLogic(AuditRepository(), EvaluationRepository())
    started = time.perf_counter()
    async with UnitOfWork(env["sessions"]) as uow:
        wait_started = time.perf_counter()
        await uow.session.connection()
        pool_wait_ms.append((time.perf_counter() - wait_started) * 1000)
        result = await logic.replay_original(
            uow,
            run_key=f"perf-scientific-replay-{sequence}",
            manifest_hash=manifest_hash,
            seed=seed,
        )
        assert result.ok, result.reason
        assert result.output_artifact_hash == manifest_hash
    return time.perf_counter() - started


async def _run() -> dict[str, Any]:
    results: dict[str, Any] = {}
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    dbname = f"{TEMP_PREFIX}{uuid.uuid4().hex[:8]}"
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{dbname}"'))
    admin.dispose()
    url = make_url(ADMIN_URL).set(database=dbname).render_as_string(hide_password=False)

    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(SERVE_DIR / "alembic"))
    sync_engine = create_engine(url, poolclass=NullPool)
    conn = sync_engine.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, "head")
    finally:
        conn.close()
        sync_engine.dispose()

    async_url = make_url(url).set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    async_engine = create_async_engine(
        async_url, pool_size=POOL_SIZE, max_overflow=MAX_OVERFLOW,
        pool_timeout=5, pool_pre_ping=False,
    )
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    env = {
        "sessions": sessions,
        "decision": DecisionRepository(),
        "execution": ExecutionRepository(),
        "ledger": LedgerRepository(),
        "forecast": ForecastRepository(),
        "wf": WorkflowRepository(),
        "cohort": CohortRepository(),
        "sem": SemanticsRepository(),
        "url": url,
    }
    try:
        # ---- fixture：真实决策链 + 100k shadow positions（不计入计时窗口） ----
        ctx = await _seed(env, book_received_at=FIXED + timedelta(hours=11, minutes=59))
        episode, spec_ids = await _build_blind_committed_episode(env, ctx)
        spec_id = spec_ids[0]
        async with UnitOfWork(sessions) as uow:
            component_id = (
                await uow.session.execute(
                    text(
                        "SELECT component_id FROM trading.forecast_component_versions "
                        "WHERE id=(SELECT component_version_id FROM trading.forecast_episodes "
                        "WHERE id=:episode)"
                    ),
                    {"episode": episode},
                )
            ).scalar_one()
            await uow.session.execute(
                text(
                    "INSERT INTO trading.positions "
                    "(portfolio_namespace, contract_spec_id, token_id, market_id, component_id, "
                    " quantity, cost_basis) "
                    "SELECT 'shadow-perf-' || lpad(g::text,6,'0'), :spec, :token, :market, "
                    ":component, 1, 0 FROM generate_series(1,:n) AS g"
                ),
                {"spec": spec_id, "token": ctx["yes_token"], "market": ctx["market"],
                 "component": component_id, "n": RISK_ROW_TARGET},
            )
            # replay namespace 预置 position（每条 replay SELL_TO_CLOSE 一个独立 namespace）。
            await uow.session.execute(
                text(
                    "INSERT INTO trading.positions "
                    "(portfolio_namespace, contract_spec_id, token_id, market_id, component_id, "
                    " quantity, cost_basis) "
                    "SELECT 'shadow-perf-replay-' || g::text, :spec, :token, :market, "
                    ":component, g + 1, 0 FROM generate_series(0,:n) AS g"
                ),
                {"spec": spec_id, "token": ctx["yes_token"], "market": ctx["market"],
                 "component": component_id, "n": REPLAY_RUNS},
            )
            wal_start_lsn = (
                await uow.session.execute(text("SELECT pg_current_wal_lsn()"))
            ).scalar_one()
        # fixture replay：建立 baseline 事实（trade_decision/execution/ledger/…）。
        await _run_replay_once(
            env=env, ctx=ctx, episode=episode, spec_id=spec_id, sequence=0,
            benchmark_started=0.0, engine=async_engine, pool_wait_ms=[],
        )
        _, replay_manifest = await _insert_empty_metric_run(
            env,
            {
                "obj": ctx["objective"],
                "strat": ctx["strategy"],
                "rel": ctx["release"],
                "cohort": ctx["cohort"],
            },
            run_key="perf-frozen-metric",
            seed=42,
        )

        logic = ProjectionLogic(ProjectionRepository())
        pool_wait_ms: list[float] = []
        rebuild_started = time.perf_counter()
        rebuild = await logic.rebuild_all(lambda: UnitOfWork(env["sessions"]))
        rebuild_seconds = time.perf_counter() - rebuild_started

        # ---- Gate 3：rebuild hash 幂等 + lost/duplicate=0（同一事实集重建两次） ----
        async def _projection_hashes() -> dict[str, list[str]]:
            async with UnitOfWork(sessions) as uow:
                return {
                    table: sorted(
                        row[0] for row in (
                            await uow.session.execute(
                                text(
                                    "SELECT projection_hash FROM trading.%s "
                                    "ORDER BY projection_hash" % table
                                )
                            )
                        ).fetchall()
                    )
                    for table in PROJECTION_TABLES
                }

        async def _projection_counts() -> dict[str, int]:
            async with UnitOfWork(sessions) as uow:
                return {
                    table: (
                        await uow.session.execute(text(f"SELECT count(*) FROM trading.{table}"))
                    ).scalar_one()
                    for table in PROJECTION_TABLES
                }

        async def _duplicate_unique_keys() -> int:
            checks = (
                ("ops_health_current", "metric_name, as_of"),
                ("pipeline_funnel_hourly", "stage, hour_start"),
                ("account_risk_current", "portfolio_namespace, market_id, component_id"),
                ("provider_cost_daily", "provider, cost_kind, cost_date"),
                ("latest_chain_summary", "chain_key, period_end"),
            )
            total = 0
            async with UnitOfWork(sessions) as uow:
                for table, key_cols in checks:
                    total += (
                        await uow.session.execute(text(
                            "SELECT count(*) FROM (SELECT %s FROM trading.%s "
                            "GROUP BY %s HAVING count(*) > 1) d"
                            % (key_cols, table, key_cols)
                        ))
                    ).scalar_one()
            return total

        first_hashes = await _projection_hashes()
        risk_counts_before = await _projection_counts()
        await logic.rebuild_all(lambda: UnitOfWork(env["sessions"]))
        second_hashes = await _projection_hashes()
        risk_counts_after = await _projection_counts()
        rebuild_lost = sum(
            before - after
            for before, after in zip(risk_counts_before.values(), risk_counts_after.values())
            if before > after
        )
        rebuild_duplicate = await _duplicate_unique_keys()
        rebuild_hash_identical = first_hashes == second_hashes

        # ---- Gate 1：keyset 列表 ≥100k 行 ----
        page_latency_ms: list[float] = []
        response_bytes: list[int] = []
        rows_paged = 0
        pool_wait_g1: list[float] = []
        next_cursor = None
        cursor = None
        g1_started = time.perf_counter()
        # warm one page（不计入指标）
        await _keyset_page(env=env, logic=logic, cursor=None,
                           pool_wait_ms=[])
        while True:
            started = time.perf_counter()
            rows, cursor, has_more = await _keyset_page(
                env=env, logic=logic, cursor=cursor,
                pool_wait_ms=pool_wait_g1,
            )
            page_latency_ms.append((time.perf_counter() - started) * 1000)
            response_bytes.append(len(json.dumps(rows, default=str, sort_keys=True).encode()))
            rows_paged += len(rows)
            if not has_more:
                break
            next_cursor = cursor
        g1_elapsed = time.perf_counter() - g1_started

        # ---- Gate 2：单条完整决策 deterministic replay ----
        replay_ms: list[float] = []
        replay_pool_wait: list[float] = []
        replay_started = time.perf_counter()
        for seq in range(1, REPLAY_RUNS + 1):
            replay_ms.append(await _run_scientific_replay_once(
                env=env, manifest_hash=replay_manifest, seed=42, sequence=seq,
                pool_wait_ms=replay_pool_wait,
            ))
        replay_elapsed = time.perf_counter() - replay_started

        # ---- Gate 4：固定 metric workload（10s，4 路并发 keyset 读） ----
        metric_started = time.perf_counter()
        metric_pool_wait: list[float] = []
        window_offsets: list[float] = []
        metric_peak_holder: dict[str, int] = {"peak": 0}

        async def metric_worker() -> int:
            cur: dict[str, Any] | None = None
            done = 0
            while time.perf_counter() - metric_started < METRIC_WINDOW_SECONDS:
                rows, cur, has_more = await _keyset_page(
                    env=env, logic=logic, cursor=cur,
                    pool_wait_ms=metric_pool_wait,
                    peak_holder=metric_peak_holder, engine=async_engine,
                )
                window_offsets.append(time.perf_counter() - metric_started)
                done += 1
                if not has_more:
                    cur = None
                await asyncio.sleep(0)
            return done

        tasks = [asyncio.create_task(metric_worker()) for _ in range(4)]
        worker_counts = await asyncio.gather(*tasks)
        metric_elapsed = time.perf_counter() - metric_started
        completions = sum(worker_counts)
        async with UnitOfWork(sessions) as uow:
            wal_bytes = int(
                (
                    await uow.session.execute(
                        text("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), :start_lsn)"),
                        {"start_lsn": wal_start_lsn},
                    )
                ).scalar_one()
            )
            db_version = (await uow.session.execute(text("SELECT version()"))).scalar_one()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu_seconds = usage.ru_utime + usage.ru_stime

        # ---- SQL plan 摘要 ----
        async with UnitOfWork(sessions) as uow:
            plan = (
                await uow.session.execute(
                    text(
                        "EXPLAIN (FORMAT JSON) "
                        "SELECT id, as_of, portfolio_namespace, exposure FROM "
                        "trading.account_risk_current ORDER BY as_of, id LIMIT 1000"
                    )
                )
            ).scalar_one()
        if isinstance(plan, list):
            plan_node = plan[0].get("Plan", plan[0]) if plan else {}
        elif isinstance(plan, dict):
            plan_node = plan.get("Plan", plan)
        else:
            plan_node = {}
        plan_summary = _plan_summary(plan_node)

        # ---- hard assertions ----
        keyset_pct = _percentiles(page_latency_ms)
        replay_pct = _percentiles([ms * 1000 for ms in replay_ms])
        g1_pool_pct = _percentiles(pool_wait_g1)
        metric_pool_pct = _percentiles(metric_pool_wait)
        assert rows_paged >= RISK_ROW_TARGET, f"keyset rows below target: {rows_paged}"
        assert keyset_pct["p95"] <= MAX_KEYSET_P95_MS, "keyset_p95_exceeded"
        assert keyset_pct["p99"] <= MAX_KEYSET_P99_MS, "keyset_p99_exceeded"
        assert max(response_bytes, default=0) <= MAX_RESPONSE_BYTES, "keyset_response_over_200kib"
        assert replay_pct["p95"] <= MAX_REPLAY_P95_MS, "replay_p95_exceeded"
        assert replay_pct["p99"] <= MAX_REPLAY_P99_MS, "replay_p99_exceeded"
        assert g1_pool_pct["p95"] <= MAX_POOL_WAIT_P95_MS, "keyset_pool_wait_p95"
        assert metric_pool_pct["p95"] <= MAX_POOL_WAIT_P95_MS, "metric_pool_wait_p95"
        assert rebuild_lost == 0, "rebuild_lost_rows"
        assert rebuild_duplicate == 0, "rebuild_duplicate_unique_keys"
        assert rebuild_hash_identical, "rebuild_hash_not_identical"
        assert risk_counts_before == risk_counts_after, "rebuild_count_drift"

        # 10s 窗口速率
        window_rates: list[float] = []
        w = 0.0
        while w + METRIC_WINDOW_SECONDS <= metric_elapsed:
            end = w + METRIC_WINDOW_SECONDS
            window_rates.append(
                sum(w <= o < end for o in window_offsets) / METRIC_WINDOW_SECONDS
            )
            w = end

        results["seed"] = SEED_LABEL
        results["environment"] = {
            "git_commit": _git_sha(),
            **_git_worktree_evidence(),
            "node": platform.node(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "database_version": db_version,
            "driver": "postgresql+asyncpg",
            "pool_size": POOL_SIZE,
            "max_overflow": MAX_OVERFLOW,
        }
        results["data_scale"] = {
            "risk_positions_seeded": RISK_ROW_TARGET,
            "account_risk_rows": risk_counts_before["account_risk_current"],
            "projection_rows": {k: int(v) for k, v in risk_counts_before.items()},
            "rebuild_seconds": round(rebuild_seconds, 3),
        }
        results["gate1_keyset_list"] = {
            "rows_paged": rows_paged,
            "pages": len(page_latency_ms),
            "page_limit": KEYSET_PAGE_LIMIT,
            "elapsed_seconds": round(g1_elapsed, 3),
            "page_latency_ms": keyset_pct,
            "max_response_bytes": max(response_bytes, default=0),
            "response_limit_bytes": MAX_RESPONSE_BYTES,
            "pool_wait_ms": g1_pool_pct,
            "sql_plan_summary": plan_summary,
        }
        results["gate2_replay"] = {
            "definition": (
                "scientific replay of one frozen canonical score set: exact quote/label "
                "cutoff validation → score recomputation → five-layer artifact hash "
                "(ReplayLogic; network/search/execution=false)"
            ),
            "runs": len(replay_ms),
            "wall_seconds": round(replay_elapsed, 3),
            "replay_ms": replay_pct,
            "pool_wait_ms": _percentiles(replay_pool_wait),
        }
        results["gate3_rebuild"] = {
            "lost_rows": rebuild_lost,
            "duplicate_unique_key_rows": rebuild_duplicate,
            "rebuild_hash_identical": rebuild_hash_identical,
        }
        results["gate4_metric_workload"] = {
            "window_seconds": METRIC_WINDOW_SECONDS,
            "workers": 4,
            "completions": completions,
            "throughput_queries_per_second": round(completions / metric_elapsed, 3),
            "pool_wait_ms": metric_pool_pct,
            "window_rates_per_second": [round(r, 3) for r in window_rates],
            "wal_bytes": int(wal_bytes),
            "max_rss_kib": usage.ru_maxrss,
            "cpu_seconds": round(cpu_seconds, 3),
            "peak_checked_out": metric_peak_holder["peak"],
            "pool_budget": POOL_BUDGET,
        }
        results["hard_assertions"] = "PASS"
        OUT_PATH.write_text(json.dumps(results, indent=2, sort_keys=True))
        return results
    finally:
        await async_engine.dispose()
        admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            with admin.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=:name AND pid<>pg_backend_pid()"
                    ),
                    {"name": dbname},
                )
                connection.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        finally:
            admin.dispose()


def _plan_summary(plan: Any) -> dict[str, Any]:
    """从 EXPLAIN JSON 提取 node type + 是否 Index Scan（摘要，不 dump 全 plan）。"""
    node_types: list[str] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        node_types.append(node.get("Node Type", "?"))
        for child in node.get("Plans", []) or []:
            walk(child)

    walk(plan)
    return {
        "node_types": node_types,
        "uses_index_scan": any("Index Scan" in t or "Index Only Scan" in t for t in node_types),
    }


def main() -> None:
    results = asyncio.run(_run())
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
