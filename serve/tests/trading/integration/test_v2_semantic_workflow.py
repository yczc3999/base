"""Real-PostgreSQL G0→R0→G1→G2→episode→R1 integration."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.uow import UnitOfWork
from app.repositories.trading.cohort import CohortRepository
from app.repositories.trading.semantics import SemanticsRepository
from app.repositories.trading.workflow import WorkflowRepository
from tests.trading.replay.test_v2_p1a_semantics_replay import _run_chain


@pytest_asyncio.fixture
async def wf_env(migrated_pg_db):
    async_url = make_url(migrated_pg_db.url).set(
        drivername="postgresql+asyncpg"
    ).render_as_string(hide_password=False)
    engine = create_async_engine(async_url, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    env = {
        "sessions": sessions,
        "sem": SemanticsRepository(),
        "wf": WorkflowRepository(),
        "cohort": CohortRepository(),
        "url": migrated_pg_db.url,
    }
    yield env
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_typed_semantic_workflow_and_failed_siblings(wf_env):
    snapshot = await _run_chain(wf_env)
    assert len(snapshot["memberships"]) == 2
    assert [row[2] for row in snapshot["screenings"]] == ["SELECT", "REJECT"]
    assert len(snapshot["audits"]) == 1 and snapshot["audits"][0][-1] is True
    assert len(snapshot["episodes"]) == 1
    assert snapshot["r1"] == [("standard", "completed", False, False, False, False)]

    by_contract = {row[0]: row for row in snapshot["specs"]}
    assert by_contract["spec-replay-1"][1] == "pass"
    assert by_contract["spec-replay-2"][1] == "pass"
    assert by_contract["spec-replay-fail"][1] == "fail"
    terminal = [row for row in snapshot["opportunities"] if row[2] == "PRE_COMMIT_TERMINAL"]
    assert len(terminal) == 2


@pytest.mark.asyncio
async def test_retry_has_one_episode_exact_component_spec_set_and_no_eligibility(wf_env):
    await _run_chain(wf_env)
    async with UnitOfWork(wf_env["sessions"]) as uow:
        counts = (
            await uow.session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM trading.forecast_episodes),"
                    "(SELECT count(*) FROM trading.episode_memberships),"
                    "(SELECT count(*) FROM trading.gate_decisions WHERE gate='R1'),"
                    "(SELECT count(*) FROM trading.episode_contract_specs),"
                    "(SELECT count(*) FROM trading.forecast_component_contract_specs)"
                )
            )
        ).one()
        eligibility = (
            await uow.session.execute(
                text(
                    "SELECT bool_or(action_eligible),bool_or(qualification_eligible),"
                    "bool_or(capital_evidence_eligible) FROM trading.episode_memberships"
                )
            )
        ).one()
    assert counts[:3] == (1, 1, 1)
    assert counts[3] == counts[4] == 2
    assert eligibility == (False, False, False)
