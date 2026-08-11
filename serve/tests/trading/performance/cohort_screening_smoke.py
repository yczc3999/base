"""WP-01C hard performance contract on a real PostgreSQL database.

Run from ``/code/pollymarket/v2/serve``::

    V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
      .venv/bin/python -m tests.trading.performance.cohort_screening_smoke

The benchmark deliberately goes through the same typed Logic, Repository,
state-machine, constraints, and UoW commits as production code.  Fixture setup
is excluded from the timings; no direct/fake Gate inserts are used.
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
import time
import uuid
from dataclasses import dataclass
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
from app.logics.trading.component import ComponentLogic  # noqa: E402
from app.logics.trading.contract import ContractLogic  # noqa: E402
from app.logics.trading.screening import ScreeningLogic  # noqa: E402
from app.orchestrator.trading_state_machine import (  # noqa: E402
    EpisodeInput,
    TradingStateMachine,
)
from app.repositories.trading.cohort import CohortRepository  # noqa: E402
from app.repositories.trading.semantics import SemanticsRepository  # noqa: E402
from app.repositories.trading.workflow import WorkflowRepository  # noqa: E402
from app.schemas.trading.semantics import WorldSchemaInput  # noqa: E402
from app.schemas.trading.workflow import (  # noqa: E402
    HydratedUniverseFrameInput,
    R0BatchItemInput,
    R0Input,
)
from tests.trading.replay.test_v2_p1a_semantics_replay import (  # noqa: E402
    AUDIT_POLICY,
    FIXED,
    FULL_OBJECTIVE,
    OBJECTIVE_HASH,
    R0_POLICY,
    _contract_candidate,
    _seed,
)

ADMIN_URL = os.environ.get(
    "V2_TEST_ADMIN_DATABASE_URL", "postgresql+psycopg:///postgres"
)
TEMP_PREFIX = "pm_v2_perf_1c_"
MARKET_COUNT = 50_000
POOL_SIZE = 16
PIPELINE_SECONDS = max(
    60.0, float(os.environ.get("V2_PERF_PIPELINE_SECONDS", "60"))
)
SUSTAINED_WINDOW_SECONDS = 10.0
MIN_PIPELINES_PER_SECOND = 100.0
MAX_POOL_WAIT_P95_MS = 20.0
OUT_PATH = Path("/tmp/pm_v2_perf_smoke_1c.json")


def _pct(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1))))
    return round(ordered[index], 3)


def _percentiles(values: list[float]) -> dict[str, float | int]:
    return {
        "p50": _pct(values, 50),
        "p95": _pct(values, 95),
        "p99": _pct(values, 99),
        "max": round(max(values), 3) if values else 0.0,
        "count": len(values),
    }


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=SERVE_DIR,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip()


async def _prepare_50k_frame(env: dict[str, Any]) -> HydratedUniverseFrameInput:
    """Create the already-hydrated source fixture outside the measured window."""

    frame_hash = "7" * 64
    async with UnitOfWork(env["sessions"]) as uow:
        session = uow.session
        # _seed creates markets 1 and 2.  Set-based setup keeps fixture creation
        # out of the behavior under measurement without bypassing enrollment.
        await session.execute(
            text(
                "INSERT INTO trading.pm_markets "
                "(gamma_market_id,condition_id,active,closed,archived,accepting_orders,"
                " enable_order_book,neg_risk) "
                "SELECT 'market-perf-' || lpad(g::text,6,'0'),"
                "       'condition-perf-' || lpad(g::text,6,'0'),"
                "       true,false,false,true,true,false "
                "FROM generate_series(3,:market_count) AS g"
            ),
            {"market_count": MARKET_COUNT},
        )
        artifact_id = (
            await session.execute(
                text(
                    "INSERT INTO trading.artifact_objects "
                    "(sha256,original_size,stored_size,mime,compression,storage_driver,"
                    " storage_version,locator) "
                    "VALUES (:hash,1,1,'application/json','none','local','cas/v1',:locator) "
                    "RETURNING id"
                ),
                {
                    "hash": frame_hash,
                    "locator": (
                        f"cas/v1/sha256/{frame_hash[:2]}/{frame_hash[2:4]}/"
                        f"{frame_hash}.raw"
                    ),
                },
            )
        ).scalar_one()
        frame_id = (
            await session.execute(
                text(
                    "INSERT INTO trading.pm_universe_frames "
                    "(status,started_at,owner,lease_expires_at,fencing_token,completed_at,"
                    " page_count,total_events,total_markets,content_hash,artifact_id,artifact_ref) "
                    "VALUES ('COMPLETE',:at,'perf',:lease,1,:at,1,0,:count,:hash,:artifact,:hash) "
                    "RETURNING id"
                ),
                {
                    "at": FIXED,
                    "lease": FIXED + timedelta(minutes=5),
                    "count": MARKET_COUNT,
                    "hash": frame_hash,
                    "artifact": artifact_id,
                },
            )
        ).scalar_one()
        rows = (
            await session.execute(
                text(
                    "SELECT id,gamma_market_id FROM trading.pm_markets "
                    "ORDER BY id"
                )
            )
        ).fetchall()

    assert len(rows) == MARKET_COUNT
    return HydratedUniverseFrameInput(
        frame_id=frame_id,
        content_hash=frame_hash,
        artifact_object_id=artifact_id,
        artifact_ref=frame_hash,
        markets=[
            {"market_id": row[0], "metadata": {"market_key": row[1]}}
            for row in rows
        ],
    )


def _one_spec_schema(spec_id: int, shard: int) -> WorldSchemaInput:
    return WorldSchemaInput(
        component_key=f"component-perf-{shard:02d}",
        variables={"outcome": {"type": "enum"}},
        domains={"outcome": ["yes", "no"]},
        constraints=[],
        factorization={"independent": ["outcome"]},
        world_states=[
            {"world_state_id": "world-yes", "assignment": {"outcome": "yes"}},
            {"world_state_id": "world-no", "assignment": {"outcome": "no"}},
        ],
        state_count=2,
        h_c={str(spec_id): {"world-yes": "YES", "world-no": "NO"}},
        schema_version=1,
    )


@dataclass
class PipelineShard:
    index: int
    contract: Any
    spec_id: int | None = None
    schema: WorldSchemaInput | None = None
    component_version_id: int | None = None


@dataclass
class PipelineMetrics:
    pool_wait_ms: list[float]
    g1_ms: list[float]
    g2_ms: list[float]
    episode_r1_ms: list[float]
    commit_ms: list[float]
    pipeline_ms: list[float]
    completion_offsets: list[float]
    peak_checked_out: int = 0


async def _pipeline_commit(
    *,
    env: dict[str, Any],
    ctx: dict[str, Any],
    state: TradingStateMachine,
    contract_logic: ContractLogic,
    component_logic: ComponentLogic,
    shard: PipelineShard,
    source_screening_episode_id: int,
    sequence: int,
    benchmark_started: float | None,
    metrics: PipelineMetrics | None,
    engine: Any,
) -> int:
    """One real G1→G2→episode→R1 pipeline and one atomic UoW commit."""

    started = time.perf_counter()
    commit_started = started
    async with UnitOfWork(env["sessions"]) as uow:
        wait_started = time.perf_counter()
        await uow.session.connection()
        pool_wait = (time.perf_counter() - wait_started) * 1000
        if metrics is not None:
            metrics.pool_wait_ms.append(pool_wait)
            metrics.peak_checked_out = max(
                metrics.peak_checked_out, int(engine.pool.checkedout())
            )

        triggered_at = FIXED + timedelta(days=1, microseconds=sequence)
        parent = await state.create_parent_opportunity(
            uow,
            cohort_id=ctx["cohort"],
            chain_type="DECISION",
            objective_contract_id=ctx["objective"],
            strategy_version_id=ctx["strategy"],
            source_screening_episode_id=source_screening_episode_id,
            triggered_at=triggered_at,
            market_ids=[ctx["markets"][0]],
        )
        g1_child = await state.create_g1_child(
            uow,
            parent_id=parent,
            cohort_id=ctx["cohort"],
            chain_type="DECISION",
            objective_contract_id=ctx["objective"],
            strategy_version_id=ctx["strategy"],
            triggered_at=triggered_at,
            market_id=ctx["markets"][0],
            seq=0,
        )

        stage_started = time.perf_counter()
        g1 = await contract_logic.run_g1(
            uow,
            candidate=shard.contract,
            cutoff_at=FIXED,
            timezone_name="UTC",
            raw_outcome_mapping={"YES": 0, "NO": 1},
            opportunity_id=g1_child,
            policy_hash=ctx["policy_hashes"]["eligibility"],
            version_manifest_id=ctx["release"],
        )
        if not g1.ok or g1.spec_id is None:
            raise AssertionError(f"performance_g1_failed:{g1.reason}")
        if metrics is not None:
            metrics.g1_ms.append((time.perf_counter() - stage_started) * 1000)
        if shard.spec_id is None:
            shard.spec_id = g1.spec_id
            shard.schema = _one_spec_schema(g1.spec_id, shard.index)
        elif shard.spec_id != g1.spec_id:
            raise AssertionError("performance_g1_spec_identity_drift")
        assert shard.schema is not None

        g2_child = await state.create_g2_child(
            uow,
            parent_id=parent,
            cohort_id=ctx["cohort"],
            chain_type="DECISION",
            objective_contract_id=ctx["objective"],
            strategy_version_id=ctx["strategy"],
            triggered_at=triggered_at,
            component_key=shard.schema.component_key,
            g1_child_ids=[g1_child],
        )
        stage_started = time.perf_counter()
        g2 = await component_logic.run_g2(
            uow,
            candidate=shard.schema,
            contract_spec_ids=[g1.spec_id],
            member_hc={g1.spec_id: shard.schema.h_c[str(g1.spec_id)]},
            cost_budget=Decimal("10"),
            opportunity_id=g2_child,
            policy_hash=ctx["policy_hashes"]["taxonomy"],
            version_manifest_id=ctx["release"],
        )
        if not g2.ok or g2.component_version_id is None:
            raise AssertionError(f"performance_g2_failed:{g2.reason}")
        if metrics is not None:
            metrics.g2_ms.append((time.perf_counter() - stage_started) * 1000)
        if shard.component_version_id is None:
            shard.component_version_id = g2.component_version_id
        elif shard.component_version_id != g2.component_version_id:
            raise AssertionError("performance_component_identity_drift")

        stage_started = time.perf_counter()
        episode_id = await state.create_episode(
            uow,
            input_=EpisodeInput(
                decision_opportunity_id=g2_child,
                component_version_id=g2.component_version_id,
                strategy_version_id=ctx["strategy"],
                objective_contract_id=ctx["objective"],
                trigger="complete_frame",
                cutoff_at=FIXED,
                horizon="resolution",
                experiment_variant="performance",
                contract_spec_ids=[g1.spec_id],
            ),
        )
        await state.route_episode(
            uow,
            episode_id=episode_id,
            route_channel="standard",
            first_rejected_gate=None,
            reason_code=None,
            recheck_at=None,
            recheck_condition=None,
            audit_selected=False,
            policy_hash=ctx["policy_hashes"]["r1"],
            version_manifest_id=ctx["release"],
        )
        if metrics is not None:
            metrics.episode_r1_ms.append(
                (time.perf_counter() - stage_started) * 1000
            )
        commit_started = time.perf_counter()

    completed = time.perf_counter()
    if metrics is not None:
        metrics.commit_ms.append((completed - commit_started) * 1000)
        metrics.pipeline_ms.append((completed - started) * 1000)
        if benchmark_started is not None:
            metrics.completion_offsets.append(completed - benchmark_started)
    return episode_id


async def _database_counts(env: dict[str, Any]) -> dict[str, int | str]:
    async with UnitOfWork(env["sessions"]) as uow:
        session = uow.session
        row = (
            await session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM trading.decision_opportunities),"
                    "(SELECT count(*) FROM trading.forecast_episodes),"
                    "(SELECT COALESCE(max(id),0) FROM trading.forecast_episodes),"
                    "(SELECT count(*) FROM trading.episode_memberships),"
                    "(SELECT count(*) FROM trading.episode_contract_specs),"
                    "(SELECT count(*) FROM trading.gate_decisions WHERE gate='G1'),"
                    "(SELECT count(*) FROM trading.gate_decisions WHERE gate='G2'),"
                    "(SELECT count(*) FROM trading.gate_decisions WHERE gate='R1'),"
                    "pg_current_wal_lsn(),pg_database_size(current_database())"
                )
            )
        ).one()
    keys = (
        "opportunities",
        "episodes",
        "max_episode_id",
        "memberships",
        "episode_specs",
        "g1",
        "g2",
        "r1",
        "wal_lsn",
        "database_bytes",
    )
    return dict(zip(keys, row))


async def _run() -> dict[str, Any]:
    dbname = f"{TEMP_PREFIX}{uuid.uuid4().hex[:12]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{dbname}"'))
    admin.dispose()
    url = make_url(ADMIN_URL).set(database=dbname).render_as_string(
        hide_password=False
    )

    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(SERVE_DIR / "alembic"))
    sync_engine = create_engine(url, poolclass=NullPool)
    migration_connection = sync_engine.connect()
    config.attributes["connection"] = migration_connection
    command.upgrade(config, "head")
    migration_connection.close()
    sync_engine.dispose()

    async_url = make_url(url).set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    async_engine = create_async_engine(
        async_url,
        pool_size=POOL_SIZE,
        max_overflow=0,
        pool_timeout=5,
        pool_pre_ping=False,
    )
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    env: dict[str, Any] = {
        "sessions": sessions,
        "sem": SemanticsRepository(),
        "wf": WorkflowRepository(),
        "cohort": CohortRepository(),
        "url": url,
    }
    results: dict[str, Any] = {}

    try:
        ctx = await _seed(env)
        frame = await _prepare_50k_frame(env)
        screening = ScreeningLogic(env["cohort"], env["wf"])
        async with UnitOfWork(sessions) as uow:
            g0 = await screening.run_g0(
                uow,
                cohort_id=ctx["cohort"],
                objective_content=FULL_OBJECTIVE,
                expected_objective_hash=OBJECTIVE_HASH,
            )
        assert g0.ok, g0.reason

        shared_r0 = R0Input(
            market_metadata={"source": "hydrated_complete_frame"},
            best_bid=Decimal("0.50"),
            best_ask=Decimal("0.52"),
            rule_completeness=Decimal("0.90"),
            minimum_deployable_capacity=Decimal("10"),
            objective_ref=OBJECTIVE_HASH,
        )
        r0_items = [
            R0BatchItemInput(
                market_id=market.market_id,
                episode_no=1,
                r0_input=shared_r0,
            )
            for market in frame.markets
        ]

        # Contract 1: exact hydrated 50k prospective enrollment + real R0.
        contract_started = time.perf_counter()
        stage_started = contract_started
        async with UnitOfWork(sessions) as uow:
            enrolled = await screening.enroll_frame(
                uow,
                cohort_id=ctx["cohort"],
                frame=frame,
                observed_at=FIXED,
                ingested_at=FIXED,
                g0=g0,
            )
        enroll_seconds = time.perf_counter() - stage_started
        stage_started = time.perf_counter()
        async with UnitOfWork(sessions) as uow:
            dispositions = await screening.run_r0_batch(
                uow,
                cohort_id=ctx["cohort"],
                items=r0_items,
                g0=g0,
                r0_policy=R0_POLICY,
                audit_policy=AUDIT_POLICY,
            )
        r0_seconds = time.perf_counter() - stage_started
        enroll_r0_seconds = time.perf_counter() - contract_started

        async with UnitOfWork(sessions) as uow:
            screening_facts = (
                await uow.session.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM trading.universe_memberships WHERE cohort_id=:c),"
                        "(SELECT count(DISTINCT market_id) FROM trading.universe_memberships "
                        " WHERE cohort_id=:c),"
                        "(SELECT count(*) FROM trading.screening_episodes WHERE cohort_id=:c),"
                        "(SELECT count(DISTINCT market_id) FROM trading.screening_episodes "
                        " WHERE cohort_id=:c),"
                        "(SELECT count(*) FROM trading.gate_decisions "
                        " WHERE gate='G0' AND target_kind='screening'),"
                        "(SELECT count(*) FROM trading.gate_decisions "
                        " WHERE gate='R0' AND target_kind='screening'),"
                        "(SELECT count(*) FROM trading.screening_episodes "
                        " WHERE cohort_id=:c AND result<>'SELECT')"
                    ),
                    {"c": ctx["cohort"]},
                )
            ).one()
        member_count, distinct_members, episode_count, distinct_screened, g0_count, r0_count, non_select = screening_facts
        results["enrollment_r0"] = {
            "markets": MARKET_COUNT,
            "enrolled": enrolled,
            "dispositions": len(dispositions),
            "wall_seconds": round(enroll_r0_seconds, 3),
            "enroll_seconds": round(enroll_seconds, 3),
            "r0_seconds": round(r0_seconds, 3),
            "members": member_count,
            "distinct_members": distinct_members,
            "screening_episodes": episode_count,
            "distinct_screened_markets": distinct_screened,
            "g0_gates": g0_count,
            "r0_gates": r0_count,
            "non_select": non_select,
            "fixture_preparation_included": False,
            "hard_limit_seconds": 60.0,
        }
        assert enroll_r0_seconds <= 60.0, "50k_enrollment_r0_exceeded_60s"
        assert enrolled == MARKET_COUNT
        assert len(dispositions) == MARKET_COUNT
        assert all(item.result == "SELECT" for item in dispositions)
        assert (
            member_count
            == distinct_members
            == episode_count
            == distinct_screened
            == g0_count
            == r0_count
            == MARKET_COUNT
        ), "50k_missing_or_duplicate_disposition"
        assert non_select == 0

        source_screening_episode_id = dispositions[0].episode_id
        assert source_screening_episode_id is not None
        state = TradingStateMachine(env["wf"])
        contract_logic = ContractLogic(env["sem"], env["wf"])
        component_logic = ComponentLogic(env["sem"], env["wf"])
        shards = [
            PipelineShard(
                index=index,
                contract=_contract_candidate(ctx, f"spec-perf-{index:02d}"),
            )
            for index in range(POOL_SIZE)
        ]

        # Sequential real warmup creates the immutable spec/component per shard;
        # the sustained window then measures the normal idempotent read/reuse path.
        for shard in shards:
            await _pipeline_commit(
                env=env,
                ctx=ctx,
                state=state,
                contract_logic=contract_logic,
                component_logic=component_logic,
                shard=shard,
                source_screening_episode_id=source_screening_episode_id,
                sequence=-(shard.index + 1),
                benchmark_started=None,
                metrics=None,
                engine=async_engine,
            )
        baseline = await _database_counts(env)

        metrics = PipelineMetrics([], [], [], [], [], [], [])
        sequence = itertools.count(1)
        start_event = asyncio.Event()
        benchmark_started = time.perf_counter()
        deadline = benchmark_started + PIPELINE_SECONDS

        async def worker(shard: PipelineShard) -> int:
            await start_event.wait()
            completed = 0
            while time.perf_counter() < deadline:
                item_sequence = next(sequence)
                await _pipeline_commit(
                    env=env,
                    ctx=ctx,
                    state=state,
                    contract_logic=contract_logic,
                    component_logic=component_logic,
                    shard=shard,
                    source_screening_episode_id=source_screening_episode_id,
                    sequence=item_sequence,
                    benchmark_started=benchmark_started,
                    metrics=metrics,
                    engine=async_engine,
                )
                completed += 1
            return completed

        tasks = [asyncio.create_task(worker(shard)) for shard in shards]
        start_event.set()
        worker_counts = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - benchmark_started
        completed = sum(worker_counts)
        rate = completed / elapsed

        after = await _database_counts(env)
        base_episode_id = int(baseline["max_episode_id"])
        async with UnitOfWork(sessions) as uow:
            session = uow.session
            correctness = (
                await session.execute(
                    text(
                        "SELECT "
                        "count(*),count(DISTINCT e.episode_key),"
                        "count(*) FILTER (WHERE e.status='ROUTED'),"
                        "count(em.*),"
                        "count(em.*) FILTER (WHERE em.route_channel='standard' "
                        " AND em.processing_disposition='completed'),"
                        "count(em.*) FILTER (WHERE em.action_eligible "
                        " OR em.qualification_eligible OR em.capital_evidence_eligible),"
                        "count(ecs.*) "
                        "FROM trading.forecast_episodes e "
                        "LEFT JOIN trading.episode_memberships em ON em.episode_id=e.id "
                        "LEFT JOIN trading.episode_contract_specs ecs ON ecs.episode_id=e.id "
                        "WHERE e.id>:baseline"
                    ),
                    {"baseline": base_episode_id},
                )
            ).one()
            spec_set_mismatch = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM trading.forecast_episodes e "
                        "WHERE e.id>:baseline AND "
                        "(ARRAY(SELECT contract_spec_id FROM trading.episode_contract_specs "
                        "       WHERE episode_id=e.id ORDER BY contract_spec_id) "
                        " IS DISTINCT FROM "
                        " ARRAY(SELECT contract_spec_id FROM trading.forecast_component_contract_specs "
                        "       WHERE component_version_id=e.component_version_id "
                        "       ORDER BY contract_spec_id))"
                    ),
                    {"baseline": base_episode_id},
                )
            ).scalar_one()
            wal_bytes = int(
                (
                    await session.execute(
                        text(
                            "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(),:start_lsn)"
                        ),
                        {"start_lsn": baseline["wal_lsn"]},
                    )
                ).scalar_one()
            )
            db_version = (await session.execute(text("SELECT version()"))).scalar_one()

        opportunity_delta = int(after["opportunities"]) - int(baseline["opportunities"])
        episode_delta = int(after["episodes"]) - int(baseline["episodes"])
        membership_delta = int(after["memberships"]) - int(baseline["memberships"])
        episode_spec_delta = int(after["episode_specs"]) - int(baseline["episode_specs"])
        gate_deltas = {
            gate: int(after[gate.lower()]) - int(baseline[gate.lower()])
            for gate in ("G1", "G2", "R1")
        }
        rows_seen, unique_keys, routed, membership_rows, completed_memberships, eligible_rows, spec_rows = correctness

        window_rates: list[float] = []
        window_start = 0.0
        while window_start + SUSTAINED_WINDOW_SECONDS <= PIPELINE_SECONDS:
            window_end = window_start + SUSTAINED_WINDOW_SECONDS
            count = sum(
                window_start <= offset < window_end
                for offset in metrics.completion_offsets
            )
            window_rates.append(count / SUSTAINED_WINDOW_SECONDS)
            window_start = window_end

        results["pipeline"] = {
            "definition": "G1+G2+episode+R1 in one committed UnitOfWork",
            "duration_seconds": round(elapsed, 3),
            "required_duration_seconds": PIPELINE_SECONDS,
            "completed_commits": completed,
            "worker_commits": worker_counts,
            "rate_per_second": round(rate, 3),
            "required_rate_per_second": MIN_PIPELINES_PER_SECOND,
            "sustained_window_seconds": SUSTAINED_WINDOW_SECONDS,
            "window_rates_per_second": [round(item, 3) for item in window_rates],
            "pool_wait_ms": _percentiles(metrics.pool_wait_ms),
            "g1_ms": _percentiles(metrics.g1_ms),
            "g2_ms": _percentiles(metrics.g2_ms),
            "episode_r1_ms": _percentiles(metrics.episode_r1_ms),
            "commit_ms": _percentiles(metrics.commit_ms),
            "pipeline_ms": _percentiles(metrics.pipeline_ms),
            "peak_checked_out": metrics.peak_checked_out,
            "pool_size": POOL_SIZE,
            "max_overflow": 0,
            "opportunity_delta": opportunity_delta,
            "episode_delta": episode_delta,
            "membership_delta": membership_delta,
            "episode_spec_delta": episode_spec_delta,
            "gate_deltas": gate_deltas,
            "measured_episode_rows": rows_seen,
            "unique_episode_keys": unique_keys,
            "routed_episodes": routed,
            "membership_rows": membership_rows,
            "completed_memberships": completed_memberships,
            "eligibility_true_rows": eligible_rows,
            "episode_spec_rows": spec_rows,
            "spec_set_mismatch": spec_set_mismatch,
            "wal_bytes": wal_bytes,
            "database_growth_bytes": int(after["database_bytes"])
            - int(baseline["database_bytes"]),
        }

        # Contract 2: genuinely sustained full-pipeline commits, bounded pool,
        # exact effects/spec sets, and R1 authority remaining false.
        assert elapsed >= 60.0, "pipeline_window_shorter_than_60s"
        assert completed >= 6_000, "pipeline_fewer_than_6000_commits"
        assert rate >= MIN_PIPELINES_PER_SECOND, "pipeline_rate_below_100_per_second"
        assert window_rates and min(window_rates) >= MIN_PIPELINES_PER_SECOND, (
            "pipeline_not_sustained_at_100_per_second"
        )
        assert _pct(metrics.pool_wait_ms, 95) <= MAX_POOL_WAIT_P95_MS, (
            "pool_wait_p95_exceeded_20ms"
        )
        assert metrics.peak_checked_out <= POOL_SIZE
        assert opportunity_delta == completed * 3
        assert episode_delta == membership_delta == episode_spec_delta == completed
        assert gate_deltas == {"G1": completed, "G2": completed, "R1": completed}
        assert (
            rows_seen
            == unique_keys
            == routed
            == membership_rows
            == completed_memberships
            == spec_rows
            == completed
        ), "pipeline_missing_or_duplicate_effect"
        assert eligible_rows == 0, "r1_eligibility_must_remain_false"
        assert spec_set_mismatch == 0, "episode_component_spec_set_mismatch"

        usage = resource.getrusage(resource.RUSAGE_SELF)
        results["environment"] = {
            "git_commit": _git_sha(),
            "node": platform.node(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "max_rss_kib": usage.ru_maxrss,
            "database_version": db_version,
            "driver": "postgresql+asyncpg",
            "seed": "deterministic/wp-01c-performance-v1",
            "timestamp": FIXED.isoformat(),
        }
        results["hard_assertions"] = "PASS"
        OUT_PATH.write_text(json.dumps(results, indent=2, sort_keys=True))
        return results
    finally:
        await async_engine.dispose()
        admin = create_engine(
            ADMIN_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool
        )
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
