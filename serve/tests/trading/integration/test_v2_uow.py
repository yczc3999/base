"""
WP-01A-02 UnitOfWork —— 真 PostgreSQL 集成验收（Checkpoint D）。

前置：``V2_TEST_ADMIN_DATABASE_URL`` 存在，否则整模块 skip。``migrated_pg_db`` 在独立
临时库先跑 v2_0002。覆盖：成功 commit、body/commit 异常 rollback、after-commit hook
只 commit 成功后运行且 hook 失败不伪装 DB rollback、显式 rollback、嵌套禁止。
"""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork

_INSERT = (
    "INSERT INTO trading.artifact_objects "
    "(sha256, original_size, stored_size, mime, compression, storage_driver, storage_version, locator) "
    "VALUES (:sha, 10, 10, 'application/octet-stream', 'none', 'local', 'cas/v1', :locator)"
)


def _artifact_params(char: str) -> dict[str, str]:
    sha = char * 64
    return {
        "sha": sha,
        "locator": f"cas/v1/sha256/{sha[:2]}/{sha[2:4]}/{sha}.raw",
    }


def _async_url(db_url: str) -> str:
    return make_url(db_url).set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )


@pytest.fixture
async def sm(migrated_pg_db):
    engine = create_async_engine(_async_url(migrated_pg_db.url), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _count(sm, table="artifact_objects"):
    async with sm() as s:
        return (await s.execute(text(f"SELECT count(*) FROM trading.{table}"))).scalar()


async def test_uow_commit_persists(sm):
    async with UnitOfWork(sm) as uow:
        await uow.session.execute(text(_INSERT), _artifact_params("a"))
    assert await _count(sm) == 1
    assert uow.committed is True
    assert uow.rolled_back is False


async def test_uow_body_exception_rolls_back(sm):
    with pytest.raises(RuntimeError, match="boom"):
        async with UnitOfWork(sm) as uow:
            await uow.session.execute(text(_INSERT), _artifact_params("b"))
            raise RuntimeError("boom")
    assert await _count(sm) == 0
    assert uow.rolled_back is True
    assert uow.committed is False


async def test_uow_commit_exception_rolls_back_and_propagates(sm):
    with pytest.raises(Exception):
        async with UnitOfWork(sm) as uow:
            # 违反唯一约束 → commit 失败 → rollback + 重抛
            await uow.session.execute(text(_INSERT), _artifact_params("c"))
            await uow.session.execute(text(_INSERT), _artifact_params("c"))
    assert await _count(sm) == 0
    assert uow.rolled_back is True


async def test_after_commit_hook_runs_only_on_success(sm):
    calls = []

    async def hook():
        calls.append("ran")

    async with UnitOfWork(sm) as uow:
        await uow.session.execute(text(_INSERT), _artifact_params("d"))
        uow.after_commit(hook)
    assert calls == ["ran"]
    assert await _count(sm) == 1

    # 失败路径 hook 不运行
    calls.clear()
    with pytest.raises(RuntimeError, match="boom"):
        async with UnitOfWork(sm) as uow:
            await uow.session.execute(text(_INSERT), _artifact_params("e"))
            uow.after_commit(hook)
            raise RuntimeError("boom")
    assert calls == []
    assert await _count(sm) == 1


async def test_after_commit_hook_failure_does_not_rollback_or_propagate(sm):
    async def bad_hook():
        raise RuntimeError("hook-failed")

    async with UnitOfWork(sm) as uow:
        await uow.session.execute(text(_INSERT), _artifact_params("f"))
        uow.after_commit(bad_hook)
    # hook 异常被吞；DB 已提交（不伪装 rollback），且不重抛
    assert await _count(sm) == 1
    assert uow.committed is True


async def test_explicit_rollback_prevents_commit(sm):
    async with UnitOfWork(sm) as uow:
        await uow.session.execute(text(_INSERT), _artifact_params("7"))
        await uow.rollback()
    assert uow.rolled_back is True
    assert uow.committed is False
    assert await _count(sm) == 0


async def test_nested_uow_prohibited(sm):
    uow = UnitOfWork(sm)
    await uow.__aenter__()
    try:
        with pytest.raises(RuntimeError, match="uow_already_entered"):
            await uow.__aenter__()
    finally:
        await uow.__aexit__(None, None, None)


async def test_session_access_before_enter_raises(sm):
    uow = UnitOfWork(sm)
    with pytest.raises(RuntimeError, match="uow_not_entered"):
        _ = uow.session


async def test_uow_is_single_use_and_rollback_hooks_do_not_leak(sm):
    calls = []

    async def hook():
        calls.append("stale")

    uow = UnitOfWork(sm)
    with pytest.raises(RuntimeError, match="boom"):
        async with uow:
            uow.after_commit(hook)
            raise RuntimeError("boom")
    with pytest.raises(RuntimeError, match="uow_already_used"):
        await uow.__aenter__()
    assert calls == []


async def test_after_commit_registration_requires_active_uow(sm):
    async def hook():
        return None

    uow = UnitOfWork(sm)
    with pytest.raises(RuntimeError, match="uow_not_active"):
        uow.after_commit(hook)
