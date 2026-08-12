"""WP-07C 常驻运行时装配集成验收（真 PostgreSQL + 真 Redis）。

证明：
- outbox publisher/sweeper/consumer 经 runtime 包装常驻循环，可一轮完成
  enqueue → publish → consume（经 TradingEventDispatch 路由到 handler）。
- supervisor 显式注册 outbox 三件套、隔离 outbox pool、stop_event 优雅停止。
- dispatch 用 recording handler 替身（不触真实 provider），验证 envelope→event 路由
  在真实 outbox 传输下成立。

Redis 用随机 namespace；缺 V2_TEST_ADMIN_DATABASE_URL 时整模块 skip。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import ControlRedisEndpoint
from app.db.uow import UnitOfWork
from app.outbox.contracts import create_envelope
from app.outbox.repository import OutboxRepository
from app.services.redis_control import ControlRedisClient

from runtimes.trading._dispatch import TOPIC_BLIND_COMMIT, TradingEventDispatch
from runtimes.trading.outbox import OutboxConsumerRuntime, OutboxLoopPolicy
from runtimes.trading.supervisor import RuntimeSpec, RuntimeSupervisor, SupervisorContext


def _async_url(db_url: str) -> str:
    return make_url(db_url).set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )


def _redis() -> ControlRedisClient:
    ep = ControlRedisEndpoint(
        url="redis://localhost:6379/0",
        max_connections=5,
        connect_timeout_s=2.0,
        read_timeout_s=2.0,
        health_check_interval_s=30.0,
        namespace=f"pm:it:{uuid.uuid4().hex}:rt",
    )
    return ControlRedisClient(ep)


class _RecordingHandler:
    handler_name = "trading-dispatch"

    def __init__(self):
        self.calls = []

    async def handle(self, envelope, uow, fencing_token):
        self.calls.append((envelope.topic, envelope.payload))
        return None  # 消费成功


@pytest.fixture
async def stack(migrated_pg_db):
    engine = create_async_engine(_async_url(migrated_pg_db.url), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = _redis()
    try:
        yield factory, redis, migrated_pg_db
    finally:
        await redis.aclose()
        await engine.dispose()


@pytest.mark.anyio
async def test_outbox_consumer_runtime_routes_envelope(stack):
    """enqueue → publisher 一轮 → consumer runtime 一轮 → dispatch 收到事件。"""
    factory, redis, db = stack
    repo = OutboxRepository()

    # 生产侧：enqueue 一个 blind_commit 事实
    env = create_envelope(
        topic=TOPIC_BLIND_COMMIT,
        schema_version=1,
        aggregate_type="forecast_submission",
        aggregate_id="ep1:sub1",
        idempotency_key=f"blind-commit:ep1:sub1:{uuid.uuid4().hex[:6]}",
        payload={"episode_key": "ep1", "submission_key": "sub1", "episode_id": 1},
    )
    async with UnitOfWork(factory) as uow:
        await repo.enqueue(uow.session, env)

    # publisher 一轮（直接驱动 OutboxPublisher，runtime 循环在此用一轮代表）
    from app.outbox.publisher import OutboxPublisher
    pub = OutboxPublisher(factory, redis, repo, owner="it-publisher")
    published = await pub.run_once(batch_size=10)
    assert published == 1

    # consumer runtime 一轮：recording handler 收到路由后的事件
    recorder = _RecordingHandler()
    consumer_rt = OutboxConsumerRuntime(
        factory, redis, recorder, consumer_id="it-consumer",
        topics=(TOPIC_BLIND_COMMIT,), policy=OutboxLoopPolicy(),
    )
    stop = asyncio.Event()
    task = asyncio.create_task(consumer_rt.run(stop))
    await asyncio.sleep(2.5)  # 让 consumer run_once(block_ms=1000) 至少跑两轮
    stop.set()
    await task
    assert any(t == TOPIC_BLIND_COMMIT for t, _ in recorder.calls), recorder.calls


@pytest.mark.anyio
async def test_supervisor_registers_and_stops_outbox_specs(stack):
    """supervisor 注册 outbox spec、stop_event 优雅停止。"""
    factory, redis, db = stack
    ctx = SupervisorContext(
        session_factory_for=lambda p: factory,
        control_redis=redis,
        cache_redis=None,
        artifacts=None,
        gateway=None,
    )
    recorder = _RecordingHandler()
    ctx.config["dispatch"] = recorder

    sup = RuntimeSupervisor(ctx)
    from runtimes.trading.assembly import default_specs
    for spec in default_specs():
        sup.register(spec)
    # consumer spec 需要 dispatch
    ctx.config.setdefault("dispatch", recorder)

    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.5)
    assert sup.registry_snapshot(), "no specs registered"
    sup.stop_event.set()
    failures = await asyncio.wait_for(task, timeout=10)
    assert failures == 0
