"""
数据库服务 — 分进程 engine profile（V2 交易域基础设施，扩展 Base）。

每个 V2 进程使用独立的小连接池 async engine：
    api / market / execution / cognition / evaluation / replay
统一应用 pool_pre_ping、pool_timeout、pool_recycle、application_name 与
statement/lock/idle-in-transaction timeout（asyncpg server_settings，毫秒）。

全局连接预算可计算（app.config.Settings.connection_budget）：
    Σ(replica_count × (pool_size + max_overflow)) ≤ max_connections − 管理保留

Legacy Base 仍经默认 api profile 提供 get_db / async_session，
不改 legacy CRUD / SEO / RBAC / Worker 行为。

禁止：业务查询、内部 commit、全进程统一 20+10。
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import ConnectionBudget, PoolProfile, Settings, settings


def build_connect_args(cfg: Settings, profile: PoolProfile) -> dict[str, Any]:
    """
    asyncpg 连接参数：application_name + server_settings。

    statement_timeout 按进程区分（API 2s / 热 worker 5s / batch-replay 30s）；
    lock_timeout 与 idle_in_transaction_session_timeout 为部署级默认。
    PG 这些参数以毫秒计。
    """
    server_settings = {
        # asyncpg.connect() 不接受顶层 application_name kwarg；PostgreSQL
        # runtime parameter 必须随 server_settings 下发。
        "application_name": profile.application_name,
        "statement_timeout": str(profile.statement_timeout_s * 1000),
        "lock_timeout": str(cfg.DB_LOCK_TIMEOUT_S * 1000),
        "idle_in_transaction_session_timeout": str(cfg.DB_IDLE_IN_TX_TIMEOUT_S * 1000),
    }
    return {"server_settings": server_settings}


def build_engine(cfg: Settings, profile_name: str) -> AsyncEngine:
    """按 profile 构建独立 async engine（小连接池，不照搬 legacy 20+10）。"""
    profile = cfg.pool_profile(profile_name)
    return create_async_engine(
        cfg.database_url,
        echo=cfg.APP_DEBUG,
        pool_size=profile.pool_size,
        max_overflow=profile.max_overflow,
        pool_pre_ping=profile.pre_ping,
        pool_timeout=cfg.DB_POOL_TIMEOUT_S,
        pool_recycle=cfg.DB_POOL_RECYCLE_S,
        connect_args=build_connect_args(cfg, profile),
    )


class DatabaseEngines:
    """按 profile 持有 engine 与 session factory，支持统一 lifespan dispose。"""

    def __init__(self, cfg: Settings | None = None) -> None:
        self._cfg = cfg or settings
        self._engines: dict[str, AsyncEngine] = {}
        self._factories: dict[str, async_sessionmaker[AsyncSession]] = {}
        self._connect_args: dict[str, dict[str, Any]] = {}

    def engine(self, profile_name: str) -> AsyncEngine:
        """按需构建/返回 profile 对应的 engine（惰性）。"""
        if profile_name not in self._engines:
            engine = build_engine(self._cfg, profile_name)
            self._engines[profile_name] = engine
            self._factories[profile_name] = async_sessionmaker(engine, expire_on_commit=False)
            self._connect_args[profile_name] = build_connect_args(
                self._cfg, self._cfg.pool_profile(profile_name)
            )
        return self._engines[profile_name]

    def session_factory(self, profile_name: str) -> async_sessionmaker[AsyncSession]:
        """返回 profile 对应的 session factory（expire_on_commit=False）。"""
        self.engine(profile_name)
        return self._factories[profile_name]

    def connect_args(self, profile_name: str) -> dict[str, Any]:
        """返回 profile 实际下发的 asyncpg 连接参数（application_name/server_settings）。"""
        self.engine(profile_name)
        return self._connect_args[profile_name]

    def budget(self, replica_counts: dict[str, int] | None = None) -> ConnectionBudget:
        """全局连接预算（跨所有副本），即当前配置的验证值。"""
        return self._cfg.connection_budget(replica_counts)

    def engine_count(self) -> int:
        """当前已构建的 engine 数（dispose 后为 0）。"""
        return len(self._engines)

    async def dispose(self) -> None:
        """关闭全部 engine，清空注册表；幂等，重复调用安全。"""
        for name, engine in self._engines.items():
            await engine.dispose()
        self._engines.clear()
        self._factories.clear()
        self._connect_args.clear()


# 模块级单例：全进程共享同一注册表
engines = DatabaseEngines(settings)

# ---- Legacy Base 兼容：默认走 api profile ----
engine = engines.engine("api")
async_session = engines.session_factory("api")


async def get_db(profile: str = "api") -> AsyncIterator[AsyncSession]:
    """
    获取数据库 Session（FastAPI 依赖注入用）。

    每个请求分配一个独立 Session，请求结束后自动关闭。
    通过 Depends(get_db) 注入到路由函数中。V2 进程可显式传 profile 取对应池。
    """
    factory = engines.session_factory(profile)
    async with factory() as session:
        yield session


@asynccontextmanager
async def engine_lifespan() -> AsyncIterator[None]:
    """lifespan 上下文：进程退出时 dispose 全部 engine。"""
    yield
    await engines.dispose()


async def dispose_engines() -> None:
    """主动释放全部 engine（供 V2 runtime / FastAPI lifespan 调用）。"""
    await engines.dispose()
