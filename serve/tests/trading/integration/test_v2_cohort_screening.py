"""Real-PostgreSQL G0, hydrated enrollment, R0 and audit integration."""

from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.uow import UnitOfWork
from app.logics.trading.screening import ScreeningLogic
from app.repositories.trading.cohort import CohortRepository
from app.repositories.trading.semantics import SemanticsRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.schemas.trading.workflow import R0BatchItemInput, R0Input
from tests.trading.replay.test_v2_p1a_semantics_replay import (
    AUDIT_POLICY,
    FIXED,
    FULL_OBJECTIVE,
    OBJECTIVE_HASH,
    R0_POLICY,
    _seed,
)


@pytest_asyncio.fixture
async def cohort_env(migrated_pg_db):
    async_url = make_url(migrated_pg_db.url).set(
        drivername="postgresql+asyncpg"
    ).render_as_string(hide_password=False)
    engine = create_async_engine(async_url, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    env = {
        "sessions": sessions,
        "cohort": CohortRepository(),
        "sem": SemanticsRepository(),
        "wf": WorkflowRepository(),
        "url": migrated_pg_db.url,
    }
    yield env
    await engine.dispose()


@pytest.mark.asyncio
async def test_g0_reads_exact_frozen_db_bindings(cohort_env):
    ctx = await _seed(cohort_env)
    logic = ScreeningLogic(cohort_env["cohort"], cohort_env["wf"])
    async with UnitOfWork(cohort_env["sessions"]) as uow:
        mismatch = await logic.run_g0(
            uow,
            cohort_id=ctx["cohort"],
            objective_content={**FULL_OBJECTIVE, "units": "EUR"},
            expected_objective_hash=OBJECTIVE_HASH,
        )
        passed = await logic.run_g0(
            uow,
            cohort_id=ctx["cohort"],
            objective_content=FULL_OBJECTIVE,
            expected_objective_hash=OBJECTIVE_HASH,
        )
    assert not mismatch.ok and mismatch.reason == "g0_hash_mismatch"
    assert passed.ok
    assert passed.objective_contract_id == ctx["objective"]
    assert passed.strategy_version_id == ctx["strategy"]
    assert passed.release_manifest_id == ctx["release"]
    assert passed.policy_hashes == ctx["policy_hashes"]


@pytest.mark.asyncio
async def test_ws_hint_hydrated_confirmation_r0_and_audit_are_idempotent(cohort_env):
    ctx = await _seed(cohort_env)
    logic = ScreeningLogic(cohort_env["cohort"], cohort_env["wf"])
    async with UnitOfWork(cohort_env["sessions"]) as uow:
        g0 = await logic.run_g0(uow, cohort_id=ctx["cohort"])
    assert g0.ok

    # First observation is a WS hint.  The exact hydrated COMPLETE frame later
    # confirms it without rewriting first-seen provenance, and enrolls market 2.
    async with UnitOfWork(cohort_env["sessions"]) as uow:
        assert await logic.enroll_hint(
            uow,
            cohort_id=ctx["cohort"],
            market_id=ctx["markets"][0],
            metadata={"market_key": "market-replay-1"},
            observed_at=FIXED,
            ingested_at=FIXED,
            g0=g0,
        )
    async with UnitOfWork(cohort_env["sessions"]) as uow:
        assert await logic.enroll_frame(
            uow,
            cohort_id=ctx["cohort"],
            frame=ctx["frame"],
            observed_at=FIXED,
            ingested_at=FIXED,
            g0=g0,
        ) == 1
    async with UnitOfWork(cohort_env["sessions"]) as uow:
        assert await logic.enroll_frame(
            uow,
            cohort_id=ctx["cohort"],
            frame=ctx["frame"],
            observed_at=FIXED,
            ingested_at=FIXED,
            g0=g0,
        ) == 0

    async def run_r0_pair():
        async with UnitOfWork(cohort_env["sessions"]) as uow:
            selected, rejected = await logic.run_r0_batch(
                uow,
                cohort_id=ctx["cohort"],
                items=[
                    R0BatchItemInput(
                        market_id=ctx["markets"][0],
                        episode_no=1,
                        r0_input=R0Input(
                            market_metadata={"market_key": "market-replay-1"},
                            best_bid=Decimal("0.50"),
                            best_ask=Decimal("0.52"),
                            rule_completeness=Decimal("0.9"),
                            minimum_deployable_capacity=Decimal("10"),
                            objective_ref=OBJECTIVE_HASH,
                        ),
                    ),
                    R0BatchItemInput(
                        market_id=ctx["markets"][1],
                        episode_no=1,
                        r0_input=R0Input(
                            market_metadata={"market_key": "market-replay-2"},
                            best_bid=Decimal("0.40"),
                            best_ask=Decimal("0.42"),
                            minimum_deployable_capacity=Decimal("0"),
                            objective_ref=OBJECTIVE_HASH,
                        ),
                    ),
                ],
                g0=g0,
                r0_policy=R0_POLICY,
                audit_policy=AUDIT_POLICY,
            )
        return selected, rejected

    first = await run_r0_pair()
    second = await run_r0_pair()
    assert first[0].result == second[0].result == "SELECT"
    assert first[1].result == second[1].result == "REJECT"
    assert (
        first[1].audit_u,
        first[1].audit_probability,
        first[1].audit_selected,
    ) == (
        second[1].audit_u,
        second[1].audit_probability,
        second[1].audit_selected,
    )

    async with UnitOfWork(cohort_env["sessions"]) as uow:
        row = (
            await uow.session.execute(
                text(
                    "SELECT first_seen_source,confirmed_frame_id FROM trading.universe_memberships "
                    "WHERE cohort_id=:cohort AND market_id=:market"
                ),
                {"cohort": ctx["cohort"], "market": ctx["markets"][0]},
            )
        ).one()
        counts = (
            await uow.session.execute(
                text(
                    "SELECT (SELECT count(*) FROM trading.universe_memberships),"
                    "(SELECT count(*) FROM trading.screening_episodes),"
                    "(SELECT count(*) FROM trading.audit_samples)"
                )
            )
        ).one()
    assert row == ("WS_HINT", ctx["frame"].frame_id)
    assert counts == (2, 2, 1)
