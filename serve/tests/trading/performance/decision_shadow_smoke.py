"""WP-03 hard performance contract on a real PostgreSQL database.

Run from ``/code/pollymarket/v2/serve``::

    V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \\
      .venv/bin/python -m tests.trading.performance.decision_shadow_smoke

Contracts（任务 §4 / WP-03）：
- 门 1：deterministic decision valuation（create→reveal→BLIND_ONLY→G7A，2 token depth）
  ≥100/s 持续 ≥60s；
- 门 2：atomic shadow terminalization（create→reveal→G7A→G7B→terminalize(ACTION)
  →intent→shadow_fill→ledger POSTED，同一 UoW）≥10/s 持续 ≥60s；
- lost/duplicate/unbalanced/negative-position=0；DB transaction p99≤50ms；
  pool wait p95≤20ms；有界 pool=16/overflow=0；真实 domain/repository/UoW/constraint。

快速自检：设 ``PM_V2_VALUATION_SECONDS`` / ``PM_V2_TERMINALIZATION_SECONDS``（如 5）
缩短窗口；完整验收必须 60s+60s。
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import platform
import resource
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from app.repositories.trading.cohort import CohortRepository  # noqa: E402
from app.repositories.trading.decision import DecisionRepository  # noqa: E402
from app.repositories.trading.execution import ExecutionRepository  # noqa: E402
from app.repositories.trading.forecast import ForecastRepository  # noqa: E402
from app.repositories.trading.ledger import LedgerRepository  # noqa: E402
from app.repositories.trading.semantics import SemanticsRepository  # noqa: E402
from app.repositories.trading.workflow import WorkflowRepository  # noqa: E402
from app.schemas.trading.decision import (  # noqa: E402
    ActionCandidateInput,
    ActionSetInput,
    MarketRelativeInput,
    PortfolioGateInput,
    UnderwritingInput,
)
from app.schemas.trading.execution import ShadowFillInput  # noqa: E402
from tests.trading.integration.test_v2_decision_shadow_workflow import (  # noqa: E402
    FIXED,
    _build_blind_committed_episode,
    _quote_map,
    _seed,
)

ADMIN_URL = os.environ.get(
    "V2_TEST_ADMIN_DATABASE_URL", "postgresql+psycopg:///postgres"
)
TEMP_PREFIX = "pm_v2_perf_3_"
POOL_SIZE = 16
MAX_POOL_WAIT_P95_MS = 20.0
MAX_TX_P99_MS = 50.0

# valuation 并发 worker 数：pool 保持 16/0（有界），但实测 16 路并发同写决策/账本
# 会使 tx p99 超过 50ms（本地 PG 单机吞吐上限 ~300 tx/s，超发只增延迟不增吞吐）。
# 4 路并发：~300/s、tx p99~16ms，远高于 100/s 门限，同时满足 tx p99≤50ms。
VALUATION_WORKERS = int(os.environ.get("PM_V2_VALUATION_WORKERS", "4"))

# 完整验收 = 60s+60s；快速自检用 PM_V2_*_SECONDS 缩短（如 5）。
VALUATION_SECONDS = float(os.environ.get("PM_V2_VALUATION_SECONDS", "60"))
TERMINALIZATION_SECONDS = float(os.environ.get("PM_V2_TERMINALIZATION_SECONDS", "60"))
MIN_VALUATIONS_PER_SECOND = 100
MIN_TERMINALIZATIONS_PER_SECOND = 10
# Keep this leg deliberately paced instead of flooding the local PostgreSQL
# instance.  The fixture below seeds one independent shadow namespace per run;
# 12/s leaves headroom above the 10/s acceptance floor while bounding fixture
# cardinality and making every completion a distinct economic effect.
TERMINALIZATION_TARGET_RATE = 12
MAX_TERMINALIZATION_FIXTURES = int(TERMINALIZATION_SECONDS * TERMINALIZATION_TARGET_RATE) + 32

# 持续窗口：完整跑用 10s；缩短自检自动收窄（保证至少 1 个窗口可断言）。
SUSTAINED_WINDOW_SECONDS = max(
    2.0, min(10.0, min(VALUATION_SECONDS, TERMINALIZATION_SECONDS) / 2.0)
)

SEED_LABEL = "deterministic/wp-03-decision-shadow-performance-v1"


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(SERVE_DIR), text=True
        ).strip()
    except Exception:
        return "unknown"


def _pct(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * p / 100)))
    return round(ordered[index], 3)


def _percentiles(values: list[float]) -> dict[str, float]:
    return {"p50": _pct(values, 50), "p95": _pct(values, 95), "p99": _pct(values, 99)}


def _window_rates(offsets: list[float], elapsed: float) -> list[float]:
    rates: list[float] = []
    w = 0.0
    while w + SUSTAINED_WINDOW_SECONDS <= elapsed:
        end = w + SUSTAINED_WINDOW_SECONDS
        rates.append(
            sum(w <= o < end for o in offsets) / SUSTAINED_WINDOW_SECONDS
        )
        w = end
    return rates


@dataclass
class ValuationMetrics:
    pool_wait_ms: list[float]
    tx_ms: list[float]
    completion_offsets: list[float]
    peak_checked_out: int = 0
    total: int = 0


@dataclass
class TerminalizationMetrics:
    pool_wait_ms: list[float]
    tx_ms: list[float]
    completion_offsets: list[float]
    peak_checked_out: int = 0
    total: int = 0


async def _run_valuation_once(
    *,
    env: dict,
    logic: DecisionLogic,
    metrics: ValuationMetrics,
    seq_source: itertools.count,
    benchmark_started: float,
    engine: Any,
    ctx: dict,
    spec_id: int,
    yes_token: int,
    no_token: int,
) -> None:
    # 单线程事件循环内同步 next() 原子，保证跨 worker 全局唯一（避免 decision_key 冲突）。
    seq = next(seq_source)
    trigger_at = ctx["decision_time_base"] + timedelta(microseconds=seq)
    quote_reveal_at = trigger_at + timedelta(seconds=1)
    started = time.perf_counter()
    async with UnitOfWork(env["sessions"]) as uow:
        wait_started = time.perf_counter()
        await uow.session.connection()
        metrics.pool_wait_ms.append((time.perf_counter() - wait_started) * 1000)
        metrics.peak_checked_out = max(metrics.peak_checked_out, int(engine.pool.checkedout()))
        created = await logic.create_decision(
            uow, episode_id=ctx["episode"], trigger_at=trigger_at,
            experiment_variant="perf-valuation")
        if not created.ok:
            raise RuntimeError(f"create_decision failed: {created.reason}")
        revealed = await logic.reveal(
            uow, trade_decision_id=created.trade_decision_id,
            quote_reveal_at=quote_reveal_at, quotes=_quote_map(ctx))
        if not revealed.ok:
            raise RuntimeError(f"reveal failed: {revealed.reason}")
        # An explicit observed zero is cost evidence.  Absence must remain fail-closed.
        await uow.session.execute(
            text(
                "INSERT INTO trading.operating_cost_entries "
                "(cost_key,cost_kind,amount,release_manifest_id,episode_id,"
                " trade_decision_id,allocation_policy) "
                "VALUES (:k,'INFRASTRUCTURE',0,:r,:e,:d,"
                " '{\"kind\":\"fixed_marginal\",\"evidence\":\"observed_zero\"}'::jsonb)"
            ),
            {
                "k": f"perf-valuation-cost-{created.trade_decision_id}",
                "r": ctx["release"],
                "e": ctx["episode"],
                "d": created.trade_decision_id,
            },
        )
        mr = await logic.market_relative(
            uow, trade_decision_id=created.trade_decision_id,
            input_=MarketRelativeInput(decision_mode="BLIND_ONLY"))
        if not mr.ok:
            raise RuntimeError(f"market_relative failed: {mr.reason}")
        g7a = await logic.run_g7a(
            uow, trade_decision_id=created.trade_decision_id,
            candidates=[
                ActionCandidateInput(
                    contract_spec_id=spec_id, token_id=yes_token,
                    action_type="BUY_TOKEN", target_quantity=Decimal("100"),
                    depth_levels=[[Decimal("0.52"), 100], [Decimal("0.53"), 200]],
                    side="buy", taker_fee_bps=Decimal("0"),
                    horizon_days=Decimal("1"), bankroll=Decimal("100000"),
                ),
                ActionCandidateInput(
                    contract_spec_id=spec_id, token_id=no_token,
                    action_type="BUY_TOKEN", target_quantity=Decimal("100"),
                    depth_levels=[[Decimal("0.50"), 100], [Decimal("0.51"), 200]],
                    side="buy", taker_fee_bps=Decimal("0"),
                    horizon_days=Decimal("1"), bankroll=Decimal("100000"),
                ),
            ],
        )
        if not g7a.ok:
            raise RuntimeError(f"g7a failed: {g7a.reason}")
    # 完整事务时间（含 commit）
    metrics.total += 1
    metrics.tx_ms.append((time.perf_counter() - started) * 1000)
    metrics.completion_offsets.append(time.perf_counter() - benchmark_started)


async def _run_valuation_shard(
    *,
    env: dict,
    logic: DecisionLogic,
    start_event: asyncio.Event,
    metrics: ValuationMetrics,
    seq_source: itertools.count,
    benchmark_started: float,
    engine: Any,
    ctx: dict,
    spec_id: int,
    yes_token: int,
    no_token: int,
) -> None:
    await start_event.wait()
    while time.perf_counter() - benchmark_started < VALUATION_SECONDS:
        try:
            await _run_valuation_once(
                env=env, logic=logic, metrics=metrics, seq_source=seq_source,
                benchmark_started=benchmark_started, engine=engine, ctx=ctx,
                spec_id=spec_id, yes_token=yes_token, no_token=no_token)
        except Exception:
            import traceback
            traceback.print_exc()
            return


async def _run_terminalization_once(
    *,
    env: dict,
    logic: DecisionLogic,
    exec_logic: ShadowExecutionLogic,
    metrics: TerminalizationMetrics,
    sequence: int,
    benchmark_started: float,
    engine: Any,
    ctx: dict,
    spec_id: int,
    yes_token: int,
) -> None:
    quantity = Decimal(sequence + 1)
    trigger_at = ctx["decision_time_base"] + timedelta(microseconds=sequence)
    quote_reveal_at = trigger_at + timedelta(seconds=1)
    decided_at = trigger_at + timedelta(seconds=2)
    started = time.perf_counter()
    async with UnitOfWork(env["sessions"]) as uow:
        wait_started = time.perf_counter()
        await uow.session.connection()
        metrics.pool_wait_ms.append((time.perf_counter() - wait_started) * 1000)
        metrics.peak_checked_out = max(metrics.peak_checked_out, int(engine.pool.checkedout()))
        created = await logic.create_decision(
            uow, episode_id=ctx["episode"], trigger_at=trigger_at,
            # Each run is an independent shadow arm; otherwise the capacity cap would
            # correctly stop this throughput benchmark after the shared arm fills.
            experiment_variant=f"perf-terminalize-{sequence}")
        assert created.ok, created.reason
        revealed = await logic.reveal(
            uow, trade_decision_id=created.trade_decision_id,
            quote_reveal_at=quote_reveal_at, quotes=_quote_map(ctx))
        assert revealed.ok, revealed.reason
        await uow.session.execute(
            text(
                "INSERT INTO trading.operating_cost_entries "
                "(cost_key,cost_kind,amount,release_manifest_id,episode_id,"
                " trade_decision_id,allocation_policy) "
                "VALUES (:k,'INFRASTRUCTURE',0,:r,:e,:d,"
                " '{\"kind\":\"fixed_marginal\",\"evidence\":\"observed_zero\"}'::jsonb)"
            ),
            {
                "k": f"perf-terminal-cost-{created.trade_decision_id}",
                "r": ctx["release"],
                "e": ctx["episode"],
                "d": created.trade_decision_id,
            },
        )
        mr = await logic.market_relative(
            uow, trade_decision_id=created.trade_decision_id,
            input_=MarketRelativeInput(decision_mode="BLIND_ONLY"))
        assert mr.ok, mr.reason
        g7a = await logic.run_g7a(
            uow, trade_decision_id=created.trade_decision_id,
            candidates=[
                ActionCandidateInput(
                    contract_spec_id=spec_id, token_id=yes_token,
                    action_type="SELL_TOKEN_TO_CLOSE", target_quantity=quantity,
                )
            ],
        )
        assert g7a.ok, g7a.reason
        g7b = await logic.run_g7b(
            uow, trade_decision_id=created.trade_decision_id,
            portfolio=PortfolioGateInput(
                portfolio_namespace=f"shadow-perf-terminalize-{sequence}"
            ),
        )
        assert g7b.ok, g7b.reason
        terminal = await logic.terminalize(
            uow, trade_decision_id=created.trade_decision_id,
            action_set=ActionSetInput(
                disposition="ACTION",
                selected_action_type="SELL_TOKEN_TO_CLOSE",
                legs={"close": {spec_id: {yes_token: quantity}}},
            ),
            underwriting=None,
            decided_at=decided_at)
        assert terminal.ok, terminal.reason
        action_sets = (await uow.session.execute(
            text("SELECT id FROM trading.action_sets WHERE trade_decision_id=:d"),
            {"d": created.trade_decision_id})).scalars().all()
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
                execution_key=f"exec-{created.trade_decision_id}",
                economic_action_intent_id=intent_id,
                action_set_leg_id=legs[0]["id"],
            ),
        )
        assert fill.ok, fill.reason
        assert fill.status == "FILLED"
    # 完整事务时间（含 commit）
    metrics.total += 1
    metrics.tx_ms.append((time.perf_counter() - started) * 1000)
    metrics.completion_offsets.append(time.perf_counter() - benchmark_started)


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

    async_url = make_url(url).set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    async_engine = create_async_engine(async_url, pool_size=POOL_SIZE, max_overflow=0)
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
    }
    try:
        code_hash = _git_sha()
        ctx = await _seed(env)
        ctx["episode"], spec_ids = await _build_blind_committed_episode(env, ctx)
        ctx["spec_id"] = spec_ids[0]
        ctx["decision_time_base"] = max(
            ctx["checkpoint_recv_yes"], ctx["checkpoint_recv_no"]
        ) + timedelta(seconds=1)
        logic = DecisionLogic(env["decision"], env["wf"])
        exec_logic = ShadowExecutionLogic(env["execution"], env["ledger"])
        spec_id = ctx["spec_id"]
        yes_token = ctx["yes_token"]
        no_token = ctx["no_token"]

        # Gate 2 deliberately exercises the risk-reducing SELL/CLOSE path.  Each
        # terminalization owns an independent namespace and a distinct quantity,
        # so the benchmark cannot evade intent idempotency or capacity claims by
        # replaying one economic action.  Seeded projections are outside the timed
        # interval; every timed transaction still performs the full decision →
        # intent → execution → signed lot → balanced ledger → outbox chain.
        async with UnitOfWork(sessions) as uow:
            component_id = (
                await uow.session.execute(
                    text(
                        "SELECT component_id FROM trading.forecast_component_versions "
                        "WHERE id=(SELECT component_version_id FROM trading.forecast_episodes "
                        "WHERE id=:episode)"
                    ),
                    {"episode": ctx["episode"]},
                )
            ).scalar_one()
            await uow.session.execute(
                text(
                    "INSERT INTO trading.positions "
                    "(portfolio_namespace,contract_spec_id,token_id,market_id,component_id,"
                    " quantity,cost_basis) "
                    "SELECT 'shadow-perf-terminalize-' || gs::text,:spec,:token,:market,:component,"
                    " gs + 1, 0 FROM generate_series(0,:last) AS gs"
                ),
                {
                    "spec": spec_id,
                    "token": yes_token,
                    "market": ctx["market"],
                    "component": component_id,
                    "last": MAX_TERMINALIZATION_FIXTURES - 1,
                },
            )
            await uow.session.execute(
                text(
                    "INSERT INTO trading.pm_book_levels "
                    "(checkpoint_id,received_at,side,price,size,ordinal) "
                    "VALUES (:checkpoint,:received,'bid',0.48,:size,2)"
                ),
                {
                    "checkpoint": ctx["checkpoint_yes"],
                    "received": ctx["checkpoint_recv_yes"],
                    "size": MAX_TERMINALIZATION_FIXTURES,
                },
            )

        async with UnitOfWork(sessions) as uow:
            wal_start_lsn = (
                await uow.session.execute(text("SELECT pg_current_wal_lsn()"))
            ).scalar_one()

        # ---- Gate 1：deterministic decision valuation（并发，≥100/s） ----
        val_metrics = ValuationMetrics(pool_wait_ms=[], tx_ms=[], completion_offsets=[])
        start_event = asyncio.Event()
        benchmark_started = time.perf_counter()
        seq_source = itertools.count()
        workers = [
            _run_valuation_shard(
                env=env, logic=logic, start_event=start_event, metrics=val_metrics,
                seq_source=seq_source, benchmark_started=benchmark_started,
                engine=async_engine, ctx=ctx, spec_id=spec_id, yes_token=yes_token,
                no_token=no_token,
            )
            for _ in range(VALUATION_WORKERS)
        ]
        tasks = [asyncio.create_task(w) for w in workers]
        start_event.set()
        while time.perf_counter() - benchmark_started < VALUATION_SECONDS:
            await asyncio.sleep(0.1)
        await asyncio.gather(*tasks, return_exceptions=True)
        valuation_elapsed = time.perf_counter() - benchmark_started
        valuation_rate = val_metrics.total / valuation_elapsed

        # ---- Gate 2：atomic shadow terminalization（串行，≥10/s） ----
        term_metrics = TerminalizationMetrics(pool_wait_ms=[], tx_ms=[], completion_offsets=[])
        term_start = time.perf_counter()
        done = 0
        while time.perf_counter() - term_start < TERMINALIZATION_SECONDS:
            if done >= MAX_TERMINALIZATION_FIXTURES:
                raise RuntimeError("terminalization_fixture_budget_exhausted")
            await _run_terminalization_once(
                env=env, logic=logic, exec_logic=exec_logic, metrics=term_metrics,
                sequence=done, benchmark_started=term_start, engine=async_engine,
                ctx=ctx, spec_id=spec_id, yes_token=yes_token,
            )
            done += 1
            target_elapsed = done / TERMINALIZATION_TARGET_RATE
            remaining = target_elapsed - (time.perf_counter() - term_start)
            if remaining > 0:
                await asyncio.sleep(remaining)
        terminalization_elapsed = time.perf_counter() - term_start
        terminalization_rate = done / terminalization_elapsed

        # ---- correctness deltas ----
        async with UnitOfWork(sessions) as uow:
            valuation_rows = (
                await uow.session.execute(text(
                    "SELECT count(*) FROM trading.trade_decisions "
                    "WHERE experiment_variant='perf-valuation' AND status='G7A'"))
            ).scalar_one()
            terminal_decisions = (
                await uow.session.execute(text(
                    "SELECT count(*) FROM trading.trade_decisions "
                    "WHERE experiment_variant LIKE 'perf-terminalize-%' AND status='ACTION'"))
            ).scalar_one()
            lost = (
                await uow.session.execute(text(
                    "SELECT count(*) FROM trading.trade_decisions "
                    "WHERE experiment_variant LIKE 'perf-terminalize-%' AND status<>'ACTION'"))
            ).scalar_one()
            duplicate = (
                await uow.session.execute(text(
                    "SELECT count(*) FROM (SELECT decision_key FROM trading.trade_decisions "
                    "GROUP BY decision_key HAVING count(*)>1) d"))
            ).scalar_one()
            unbalanced = (
                await uow.session.execute(text(
                    "SELECT count(*) FROM (SELECT transaction_id, asset_type, asset_key, "
                    "sum(amount) FROM trading.ledger_postings "
                    "GROUP BY transaction_id, asset_type, asset_key HAVING sum(amount)<>0) u"))
            ).scalar_one()
            negative_position = (
                await uow.session.execute(text(
                    "SELECT count(*) FROM trading.positions WHERE quantity < 0"))
            ).scalar_one()
            ledger_not_posted = (
                await uow.session.execute(text(
                    "SELECT count(*) FROM trading.ledger_transactions WHERE status<>'POSTED'"))
            ).scalar_one()
            ledger_tx_count = (
                await uow.session.execute(text("SELECT count(*) FROM trading.ledger_transactions"))
            ).scalar_one()
            ledger_posting_count = (
                await uow.session.execute(text("SELECT count(*) FROM trading.ledger_postings"))
            ).scalar_one()
            outbox_count = (
                await uow.session.execute(text(
                    "SELECT count(*) FROM trading.transactional_outbox "
                    "WHERE topic='shadow.execution.terminalized'"))
            ).scalar_one()
            wal_bytes = (
                await uow.session.execute(
                    text("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), :start_lsn)"),
                    {"start_lsn": wal_start_lsn},
                )
            ).scalar_one() or 0
            db_version = (
                await uow.session.execute(text("SELECT version()"))
            ).scalar_one()

        results["valuation"] = {
            "duration_seconds": round(valuation_elapsed, 3),
            "completed_valuations": val_metrics.total,
            "g7a_decisions": valuation_rows,
            "candidates_per_valuation": 2,
            "rate_per_second": round(valuation_rate, 3),
            "required_rate_per_second": MIN_VALUATIONS_PER_SECOND,
            "sustained_window_seconds": SUSTAINED_WINDOW_SECONDS,
            "window_rates_per_second": [round(r, 3) for r in _window_rates(
                val_metrics.completion_offsets, valuation_elapsed)],
            "pool_wait_ms": _percentiles(val_metrics.pool_wait_ms),
            "tx_ms": _percentiles(val_metrics.tx_ms),
            "peak_checked_out": val_metrics.peak_checked_out,
            "pool_size": POOL_SIZE,
            "max_overflow": 0,
            "valuation_workers": VALUATION_WORKERS,
        }
        results["terminalization"] = {
            "duration_seconds": round(terminalization_elapsed, 3),
            "completed_terminalizations": done,
            "terminal_decisions": terminal_decisions,
            "rate_per_second": round(terminalization_rate, 3),
            "required_rate_per_second": MIN_TERMINALIZATIONS_PER_SECOND,
            "sustained_window_seconds": SUSTAINED_WINDOW_SECONDS,
            "window_rates_per_second": [round(r, 3) for r in _window_rates(
                term_metrics.completion_offsets, terminalization_elapsed)],
            "pool_wait_ms": _percentiles(term_metrics.pool_wait_ms),
            "tx_ms": _percentiles(term_metrics.tx_ms),
            "peak_checked_out": term_metrics.peak_checked_out,
            "ledger_transactions": ledger_tx_count,
            "ledger_postings": ledger_posting_count,
            "execution_outbox_events": outbox_count,
            "wal_bytes": int(wal_bytes),
            "lost": lost,
            "duplicate_decision": duplicate,
            "unbalanced_postings": unbalanced,
            "negative_position": negative_position,
            "ledger_not_posted": ledger_not_posted,
        }

        # ---- hard assertions ----
        val_windows = _window_rates(val_metrics.completion_offsets, valuation_elapsed)
        term_windows = _window_rates(term_metrics.completion_offsets, terminalization_elapsed)
        assert valuation_elapsed >= VALUATION_SECONDS - 0.5, "valuation_window_shorter_than_required"
        assert valuation_rate >= MIN_VALUATIONS_PER_SECOND, "valuation_rate_below_100_per_second"
        assert val_windows and min(val_windows) >= MIN_VALUATIONS_PER_SECOND, (
            "valuation_not_sustained_at_100_per_second"
        )
        assert _pct(val_metrics.pool_wait_ms, 95) <= MAX_POOL_WAIT_P95_MS, "valuation_pool_wait_p95"
        assert _pct(val_metrics.tx_ms, 99) <= MAX_TX_P99_MS, "valuation_tx_p99_exceeded"

        assert terminalization_elapsed >= TERMINALIZATION_SECONDS - 0.5, (
            "terminalization_window_shorter_than_required"
        )
        assert terminalization_rate >= MIN_TERMINALIZATIONS_PER_SECOND, (
            "terminalization_rate_below_10_per_second"
        )
        assert term_windows and min(term_windows) >= MIN_TERMINALIZATIONS_PER_SECOND, (
            "terminalization_not_sustained_at_10_per_second"
        )
        assert _pct(term_metrics.pool_wait_ms, 95) <= MAX_POOL_WAIT_P95_MS, (
            "terminalization_pool_wait_p95"
        )
        assert _pct(term_metrics.tx_ms, 99) <= MAX_TX_P99_MS, "terminalization_tx_p99_exceeded"
        assert lost == 0, "lost_terminalization"
        assert duplicate == 0, "duplicate_decision"
        assert unbalanced == 0, "ledger_unbalanced"
        assert negative_position == 0, "negative_position"
        assert ledger_not_posted == 0, "ledger_not_posted"
        assert terminal_decisions == done, "terminal_count_mismatch"
        assert ledger_tx_count == done, "ledger_tx_count_mismatch"
        assert outbox_count == done, "execution_outbox_count_mismatch"

        usage = resource.getrusage(resource.RUSAGE_SELF)
        results["environment"] = {
            "git_commit": code_hash,
            "node": platform.node(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "max_rss_kib": usage.ru_maxrss,
            "database_version": db_version,
            "driver": "postgresql+asyncpg",
            "seed": SEED_LABEL,
            "timestamp": FIXED.isoformat(),
        }
        results["hard_assertions"] = "PASS"
        OUT_PATH = Path("/tmp/pm_v2_perf_smoke_3.json")
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


def main() -> None:
    results = asyncio.run(_run())
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
