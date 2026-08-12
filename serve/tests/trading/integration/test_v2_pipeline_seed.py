"""WP-07C：pipeline 种子 bootstrap 集成验收（真 PostgreSQL）。

验证 ensure_pipeline_seed 幂等建/复用冻结配置（objective/strategy/release/cohort/
policy_freeze），cohort 落为 OPEN，G0 前置就绪。缺 V2_TEST_ADMIN_DATABASE_URL 时 skip。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork
from runtimes.trading.seed import ensure_pipeline_seed


def _async_url(db_url: str) -> str:
    return make_url(db_url).set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )


@pytest.fixture
async def factory(migrated_pg_db):
    engine = create_async_engine(_async_url(migrated_pg_db.url), poolclass=NullPool)
    f = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield f
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_seed_creates_open_cohort(factory):
    async with UnitOfWork(factory) as uow:
        result = await ensure_pipeline_seed(uow.session, cohort_key="cohort-it-seed")
        assert result.created is True
        row = (
            await uow.session.execute(
                text("SELECT status FROM trading.evaluation_cohorts WHERE id=:i"),
                {"i": result.cohort_id},
            )
        ).scalar_one()
        assert row == "OPEN"


@pytest.mark.anyio
async def test_seed_idempotent_reuses_cohort(factory):
    async with UnitOfWork(factory) as uow:
        first = await ensure_pipeline_seed(uow.session, cohort_key="cohort-it-idem")
    async with UnitOfWork(factory) as uow:
        second = await ensure_pipeline_seed(uow.session, cohort_key="cohort-it-idem")
        assert second.created is False
        assert second.cohort_id == first.cohort_id
        assert second.release_id == first.release_id


@pytest.mark.anyio
async def test_seed_policy_freezes_cover_required(factory):
    async with UnitOfWork(factory) as uow:
        result = await ensure_pipeline_seed(uow.session, cohort_key="cohort-it-pol")
        rows = (
            await uow.session.execute(
                text(
                    "SELECT count(*) FROM trading.policy_freezes "
                    "WHERE scope_key=:k AND status='frozen'"
                ),
                {"k": "cohort-it-pol"},
            )
        ).scalar_one()
        assert rows == 10  # REQUIRED_COHORT_POLICIES 全 10 项
