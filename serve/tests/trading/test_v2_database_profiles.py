"""
WP-00a 分进程数据库连接池验收测试。

覆盖：每进程独立 engine、pool 参数（pre_ping/timeout/recycle）、
application_name + statement/lock/idle server_settings、全局连接预算、
lifespan dispose、legacy get_db/async_session 兼容、禁止 20+10。
不连接真实 PostgreSQL（engine 构建是惰性的，构造不触发网络）。
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import settings
from app.services import database as db
from app.services.database import (
    DatabaseEngines,
    build_connect_args,
    build_engine,
    engine_lifespan,
    get_db,
)

PROFILE_NAMES = ("api", "market", "execution", "cognition", "evaluation", "replay")


def _pool(engine: AsyncEngine):
    """AsyncAdaptedQueuePool 内部参数（SQLAlchemy 2.0.51 实测可读）。"""
    return engine.sync_engine.pool


# ---------------- 每进程独立 engine ----------------

def test_six_profiles_have_independent_engines():
    engines = DatabaseEngines(settings)
    built = [engines.engine(name) for name in PROFILE_NAMES]
    assert len(set(id(e) for e in built)) == 6
    assert engines.engine_count() == 6
    asyncio.run(engines.dispose())


def test_lazy_creation():
    engines = DatabaseEngines(settings)
    assert engines.engine_count() == 0
    engines.engine("api")
    assert engines.engine_count() == 1
    asyncio.run(engines.dispose())


# ---------------- 池参数 ----------------

def test_api_pool_parameters():
    engines = DatabaseEngines(settings)
    pool = _pool(engines.engine("api"))
    assert pool._pool.maxsize == 5          # pool_size
    assert pool._max_overflow == 2          # max_overflow
    assert pool._pre_ping is True           # pool_pre_ping
    assert pool._timeout == 3.0             # pool_timeout
    assert pool._recycle == 1800            # pool_recycle
    asyncio.run(engines.dispose())


def test_engine_url_is_asyncpg():
    engine = build_engine(settings, "api")
    assert engine.url.get_backend_name() == "postgresql"
    assert engine.url.get_driver_name() == "asyncpg"
    asyncio.run(engine.dispose())


# ---------------- connect_args / server_settings ----------------

def test_connect_args_application_name():
    engines = DatabaseEngines(settings)
    assert engines.connect_args("api")["application_name"] == "pollymarket_v2_api"
    assert engines.connect_args("replay")["application_name"] == "pollymarket_v2_replay"
    asyncio.run(engines.dispose())


def test_server_settings_timeouts_per_profile():
    engines = DatabaseEngines(settings)
    expected_stmt_ms = {
        "api": "2000", "market": "5000", "execution": "5000",
        "cognition": "5000", "evaluation": "30000", "replay": "30000",
    }
    for name, stmt_ms in expected_stmt_ms.items():
        ss = engines.connect_args(name)["server_settings"]
        assert ss["statement_timeout"] == stmt_ms, name
        assert ss["lock_timeout"] == "1000"
        assert ss["idle_in_transaction_session_timeout"] == "5000"
    asyncio.run(engines.dispose())


def test_build_connect_args_roundtrip():
    profile = settings.pool_profile("execution")
    args = build_connect_args(settings, profile)
    assert args["application_name"] == "pollymarket_v2_execution"
    assert args["server_settings"]["statement_timeout"] == "5000"


# ---------------- 禁止 legacy 20+10 ----------------

def test_no_legacy_20_10_pattern():
    engines = DatabaseEngines(settings)
    for name in PROFILE_NAMES:
        p = settings.pool_profile(name)
        assert (p.pool_size, p.max_overflow) != (20, 10), f"legacy 20+10 leaked into {name}"
    asyncio.run(engines.dispose())


# ---------------- 全局连接预算 ----------------

def test_engine_registry_budget():
    engines = DatabaseEngines(settings)
    b = engines.budget()
    assert b.total == 35
    assert b.limit == 80
    assert b.remaining == 45
    assert b.is_within_limit() is True
    asyncio.run(engines.dispose())


# ---------------- lifespan dispose ----------------

def test_dispose_closes_and_rebuilds():
    engines = DatabaseEngines(settings)
    first = engines.engine("api")
    engines.engine("market")
    assert engines.engine_count() == 2

    asyncio.run(engines.dispose())
    assert engines.engine_count() == 0

    second = engines.engine("api")
    assert second is not first            # dispose 后重建的是新 engine
    assert engines.engine_count() == 1
    asyncio.run(engines.dispose())


def test_dispose_is_idempotent():
    engines = DatabaseEngines(settings)
    engines.engine("api")
    asyncio.run(engines.dispose())
    asyncio.run(engines.dispose())        # 二次调用安全
    assert engines.engine_count() == 0


def test_engine_lifespan_disposes_registry(monkeypatch):
    fresh = DatabaseEngines(settings)
    monkeypatch.setattr(db, "engines", fresh)   # engine_lifespan 读取模块级 engines

    async def _run():
        async with engine_lifespan():
            fresh.engine("api")
            fresh.engine("market")
            assert fresh.engine_count() == 2
        # 退出上下文后全部 dispose
        assert fresh.engine_count() == 0

    asyncio.run(_run())


# ---------------- legacy Base 兼容 ----------------

def test_legacy_engine_and_async_session_present():
    assert isinstance(db.engine, AsyncEngine)
    assert isinstance(db.async_session, async_sessionmaker)


def test_get_db_yields_async_session():
    async def _consume():
        async for session in get_db():
            return session
        return None

    session = asyncio.run(_consume())
    assert isinstance(session, AsyncSession)
    await_session = asyncio.run(_consume())
    assert await_session is not session     # 每次独立 Session


def test_get_db_profile_forwarding():
    async def _consume():
        async for session in get_db("replay"):
            return session
        return None

    session = asyncio.run(_consume())
    assert isinstance(session, AsyncSession)
