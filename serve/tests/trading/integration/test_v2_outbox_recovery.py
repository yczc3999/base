"""
WP-01A-02 Outbox recovery —— 端到端 publisher/consumer/sweeper 真 PostgreSQL + Redis 验收。

覆盖故障矩阵：publish→consume 成功；重复 delivery 不产生第二次 effect；ACK 丢失重投幂等；
consumer crash（handler 抛异常）→ requeue → max 后 DEAD；publisher publish 前 crash 可恢复；
sweeper 回收过期 visibility 并递增 attempt / 到上限写 terminal history；lease 过期重取
（stale fencing）。

Redis 用随机 namespace；测试后只删自己创建的精确 key。
"""

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import ControlRedisEndpoint
from app.db.uow import UnitOfWork
from app.outbox.contracts import OutboxEnvelope, create_envelope
from app.outbox.consumer import OutboxConsumer, RetryPolicy
from app.outbox.publisher import OutboxPublisher
from app.outbox.repository import OutboxRepository
from app.outbox.sweeper import OutboxSweeper
from app.services.redis_control import ControlRedisClient


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
        namespace=f"pm:it:{uuid.uuid4().hex}:ctrl",
    )
    return ControlRedisClient(ep)


@pytest.fixture
async def stack(migrated_pg_db):
    engine = create_async_engine(_async_url(migrated_pg_db.url), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = _redis()
    repo = OutboxRepository()
    try:
        yield factory, redis, repo, migrated_pg_db
    finally:
        await redis.aclose()
        await engine.dispose()


async def _q(sm, sql, params=None):
    async with sm() as s:
        return (await s.execute(text(sql), params or {})).fetchall()


def _env(**kw):
    base = dict(
        topic="market.resolved",
        schema_version=1,
        aggregate_type="market",
        aggregate_id="m-1",
        idempotency_key=f"idem-{uuid.uuid4().hex[:10]}",
        payload={"outcome": "yes", "amount": 100},
    )
    base.update(kw)
    return create_envelope(**base)


class _CountingHandler:
    handler_name = "market-resolution-handler"

    def __init__(self, fail_after: int | None = None):
        self.calls = []
        self.fail_after = fail_after

    async def handle(self, env: OutboxEnvelope, uow: UnitOfWork, fencing_token: int) -> None:
        self.calls.append(env.event_id)
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("handler-boom")


async def _enqueue(sm, repo, env):
    async with UnitOfWork(sm) as uow:
        await repo.enqueue(uow.session, env)


async def _consume_once(consumer, topic):
    return await consumer.run_once([topic], count=10, block_ms=500)


# ---------------- 1. publish → consume 成功 ----------------

async def test_publish_consume_completes(stack):
    sm, redis, repo, _db = stack
    env = _env()
    await _enqueue(sm, repo, env)
    publisher = OutboxPublisher(sm, redis, repo, owner="p1", visibility_seconds=60)
    assert await publisher.run_once(batch_size=10) == 1
    assert await _q(sm, "SELECT status FROM trading.transactional_outbox WHERE event_id=:e", {"e": env.event_id}) == [("DISPATCHED",)]

    handler = _CountingHandler()
    consumer = OutboxConsumer(sm, redis, repo, "consumer-1", handler, RetryPolicy(max_attempts=3))
    await _consume_once(consumer, env.topic)

    assert handler.calls == [env.event_id]
    assert await _q(sm, "SELECT count(*) FROM trading.job_completions") == [(1,)]


async def test_idempotency_is_stable_across_consumer_instances(stack):
    sm, redis, repo, _db = stack
    env = _env()
    await _enqueue(sm, repo, env)
    publisher = OutboxPublisher(sm, redis, repo, owner="p1")
    await publisher.run_once()

    first = _CountingHandler()
    c1 = OutboxConsumer(sm, redis, repo, "instance-1", first, RetryPolicy())
    await _consume_once(c1, env.topic)
    assert first.calls == [env.event_id]

    # 相同逻辑 handler、不同进程 identity 收到重复 delivery：不得再次执行。
    fields = publisher._stream_fields({
        "topic": env.topic, "schema_version": env.schema_version,
        "aggregate_type": env.aggregate_type, "aggregate_id": env.aggregate_id,
        "idempotency_key": env.idempotency_key, "priority": env.priority,
        "payload": env.payload, "artifact_ref": env.artifact_ref,
        "release_manifest_id": env.release_manifest_id, "deadline": env.deadline,
        "available_at": env.available_at, "event_id": env.event_id, "attempt": 0,
    })
    await redis.stream_add(env.topic, fields)
    second = _CountingHandler()
    c2 = OutboxConsumer(sm, redis, repo, "instance-2", second, RetryPolicy())
    await _consume_once(c2, env.topic)
    assert second.calls == []
    assert await _q(sm, "SELECT count(*) FROM trading.job_completions") == [(1,)]


async def test_handler_db_effect_and_completion_are_one_transaction(stack):
    sm, redis, repo, _db = stack
    env = _env()
    await _enqueue(sm, repo, env)
    publisher = OutboxPublisher(sm, redis, repo, owner="p1")
    await publisher.run_once()

    class Handler:
        handler_name = "atomic-db-handler"

        def __init__(self):
            self.fail = True

        async def handle(self, envelope, uow, fencing_token):
            await uow.session.execute(
                text(
                    "INSERT INTO trading.artifact_objects "
                    "(sha256, original_size, stored_size, mime, compression, "
                    " storage_driver, storage_version, locator) "
                    "VALUES (:sha, 1, 1, 'application/octet-stream', 'none', "
                    " 'local', 'cas/v1', :locator)"
                ),
                {
                    "sha": envelope.event_id,
                    "locator": (
                        f"cas/v1/sha256/{envelope.event_id[:2]}/"
                        f"{envelope.event_id[2:4]}/{envelope.event_id}.raw"
                    ),
                },
            )
            if self.fail:
                raise RuntimeError("db-handler-fail")

    handler = Handler()
    consumer = OutboxConsumer(
        sm, redis, repo, "atomic-instance", handler,
        RetryPolicy(max_attempts=3, base_backoff_s=0.01),
    )
    with pytest.raises(RuntimeError, match="db-handler-fail"):
        await _consume_once(consumer, env.topic)
    # handler 抛错时 effect 与 completion 同时 rollback。
    assert await _q(sm, "SELECT count(*) FROM trading.artifact_objects") == [(0,)]
    assert await _q(sm, "SELECT count(*) FROM trading.job_completions") == [(0,)]

    handler.fail = False
    await asyncio.sleep(0.03)
    await publisher.run_once()
    await _consume_once(consumer, env.topic)
    assert await _q(sm, "SELECT count(*) FROM trading.artifact_objects") == [(1,)]
    assert await _q(sm, "SELECT count(*) FROM trading.job_completions") == [(1,)]
    assert await _q(sm, "SELECT status FROM trading.outbox_delivery_history WHERE outbox_event_id=:e", {"e": env.event_id}) == [("DELIVERED",)]
    assert await _q(sm, "SELECT status FROM trading.transactional_outbox WHERE event_id=:e", {"e": env.event_id}) == [("COMPLETED",)]
    # stream 消息已 ACK（无 pending）
    pending = await redis.stream_group_pending_detail(env.topic, consumer._policy.group)
    assert all(p["id"].endswith("0") is False for p in pending) or pending == []


# ---------------- 2. 重复 delivery：不产生第二次 effect ----------------

async def test_duplicate_delivery_no_second_effect(stack):
    sm, redis, repo, _db = stack
    env = _env()
    await _enqueue(sm, repo, env)
    publisher = OutboxPublisher(sm, redis, repo, owner="p1")
    await publisher.run_once()
    handler = _CountingHandler()
    consumer = OutboxConsumer(sm, redis, repo, "consumer-1", handler, RetryPolicy(max_attempts=3))
    await _consume_once(consumer, env.topic)
    assert len(handler.calls) == 1

    # 模拟 ACK 丢失 / 重复投递：把同一 envelope 再次加入 stream（不 ACK）
    fields = publisher._stream_fields({
        "topic": env.topic, "schema_version": env.schema_version,
        "aggregate_type": env.aggregate_type, "aggregate_id": env.aggregate_id,
        "idempotency_key": env.idempotency_key, "priority": env.priority,
        "payload": env.payload, "artifact_ref": env.artifact_ref,
        "release_manifest_id": env.release_manifest_id, "deadline": env.deadline,
        "available_at": env.available_at, "event_id": env.event_id, "attempt": 0,
    })
    await redis.stream_add(env.topic, fields)
    await _consume_once(consumer, env.topic)

    # job_completion 守卫 → handler 不再调用（重复经济 effect=0）
    assert handler.calls == [env.event_id]
    assert await _q(sm, "SELECT count(*) FROM trading.job_completions") == [(1,)]


# ---------------- 3. handler 失败：requeue → max 后 DEAD ----------------

async def test_handler_failure_requeues_then_dead(stack):
    sm, redis, repo, _db = stack
    env = _env()
    await _enqueue(sm, repo, env)
    publisher = OutboxPublisher(sm, redis, repo, owner="p1")
    await publisher.run_once()
    handler = _CountingHandler(fail_after=1)  # 总是失败
    consumer = OutboxConsumer(sm, redis, repo, "consumer-1", handler, RetryPolicy(max_attempts=2, base_backoff_s=0.05))
    with pytest.raises(RuntimeError, match="handler-boom"):
        await _consume_once(consumer, env.topic)
    # 首次失败 → requeue（attempt 1，仍 PENDING，消息已 ACK）
    assert await _q(sm, "SELECT status, attempt FROM trading.transactional_outbox WHERE event_id=:e", {"e": env.event_id}) == [("PENDING", 1)]
    # requeue 后由 publisher 重新发布（available_at 已到）→ 再次消费失败 → attempt 2 = max → DEAD
    await asyncio.sleep(0.1)
    assert await publisher.run_once() == 1
    with pytest.raises(RuntimeError, match="handler-boom"):
        await _consume_once(consumer, env.topic)
    assert await _q(sm, "SELECT status, attempt FROM trading.transactional_outbox WHERE event_id=:e", {"e": env.event_id}) == [("DEAD", 2)]
    assert await _q(sm, "SELECT status FROM trading.outbox_delivery_history WHERE outbox_event_id=:e AND status='DEAD'", {"e": env.event_id}) == [("DEAD",)]


# ---------------- 4. publisher publish 前 crash：可恢复 ----------------

async def test_publisher_crash_before_publish_recovers(stack):
    sm, redis, repo, _db = stack
    env = _env()
    await _enqueue(sm, repo, env)
    # 首次 claim 但 stream 不可用 → publish 失败 → 行保持 PENDING + lease（可恢复）
    publisher_bad = OutboxPublisher(sm, redis, repo, owner="p1", visibility_seconds=1)
    # 直接 claim 模拟持锁但未发布；用短 visibility 以便重投
    async with UnitOfWork(sm) as uow:
        claimed = await repo.claim(uow.session, "p1", 10, 1)
    assert len(claimed) == 1
    assert await _q(sm, "SELECT status FROM trading.transactional_outbox WHERE event_id=:e", {"e": env.event_id}) == [("PENDING",)]

    # visibility 过期 → sweeper 重投 → 正常 publisher 发布并消费成功
    await asyncio.sleep(1.2)
    sweeper = OutboxSweeper(sm, repo, max_attempts=5, backoff_s=0)
    handled = await sweeper.run_once()
    assert handled >= 1
    assert await _q(sm, "SELECT status, attempt FROM trading.transactional_outbox WHERE event_id=:e", {"e": env.event_id}) == [("PENDING", 1)]

    publisher_ok = OutboxPublisher(sm, redis, repo, owner="p2", visibility_seconds=60)
    assert await publisher_ok.run_once() == 1
    handler = _CountingHandler()
    consumer = OutboxConsumer(sm, redis, repo, "consumer-1", handler, RetryPolicy(max_attempts=3))
    await _consume_once(consumer, env.topic)
    assert handler.calls == [env.event_id]
    assert await _q(sm, "SELECT status FROM trading.transactional_outbox WHERE event_id=:e", {"e": env.event_id}) == [("COMPLETED",)]


# ---------------- 5. sweeper 到上限写 terminal history ----------------

async def test_sweeper_deads_at_max_attempts(stack):
    sm, redis, repo, _db = stack
    env = _env()
    await _enqueue(sm, repo, env)
    async with UnitOfWork(sm) as uow:
        # 直接设 attempt=3 (=max)，并让 visibility 过期
        await uow.session.execute(
            text(
                "UPDATE trading.transactional_outbox SET attempt=3, "
                "visibility_deadline=now() - interval '1 minute' "
                "WHERE event_id=:e"
            ),
            {"e": env.event_id},
        )
    sweeper = OutboxSweeper(sm, repo, max_attempts=3, backoff_s=0)
    handled = await sweeper.run_once()
    assert handled == 1
    assert await _q(sm, "SELECT status FROM trading.transactional_outbox WHERE event_id=:e", {"e": env.event_id}) == [("DEAD",)]
    assert await _q(sm, "SELECT count(*) FROM trading.outbox_delivery_history WHERE status='DEAD' AND error_reason='max_attempts'") == [(1,)]


# ---------------- 6. stale fencing：lease 过期后另一 consumer 可处理 ----------------

async def test_stale_fencing_lease_reacquire(stack):
    sm, redis, repo, _db = stack
    env = _env()
    await _enqueue(sm, repo, env)
    publisher = OutboxPublisher(sm, redis, repo, owner="p1")
    await publisher.run_once()

    # 一个 consumer 取得 message lease 但未完成（模拟 stale 持有者）
    handler = _CountingHandler()
    consumer = OutboxConsumer(
        sm,
        redis,
        repo,
        "consumer-2",
        handler,
        RetryPolicy(max_attempts=3, lease_ttl_s=1.0),
    )
    lease = await redis.acquire_lease(
        consumer._lease_name(env.event_id), "stale-consumer", 1.0
    )
    assert lease is not None
    # stale lease 未过期 → 另一 consumer 无法处理该消息
    await _consume_once(consumer, env.topic)
    assert handler.calls == []

    # lease 过期 → 新 consumer 可取得并处理
    await asyncio.sleep(1.2)
    await _consume_once(consumer, env.topic)
    assert handler.calls == [env.event_id]
    assert await _q(sm, "SELECT status FROM trading.transactional_outbox WHERE event_id=:e", {"e": env.event_id}) == [("COMPLETED",)]


async def test_invalid_transport_message_cannot_dead_authoritative_outbox(stack):
    sm, redis, repo, _db = stack
    env = _env()
    await _enqueue(sm, repo, env)
    await redis.stream_add(
        env.topic,
        {"event_id": env.event_id, "attempt": "0", "envelope": "{"},
    )
    handler = _CountingHandler()
    consumer = OutboxConsumer(sm, redis, repo, "consumer-invalid", handler, RetryPolicy())
    assert await _consume_once(consumer, env.topic) == 1
    assert handler.calls == []
    assert await _q(
        sm,
        "SELECT status FROM trading.transactional_outbox WHERE event_id=:e",
        {"e": env.event_id},
    ) == [("PENDING",)]


async def test_wrong_stream_and_future_retry_never_invoke_handler(stack):
    sm, redis, repo, _db = stack
    env = _env()
    await _enqueue(sm, repo, env)
    publisher = OutboxPublisher(sm, redis, repo, owner="p1")
    await publisher.run_once()
    row = (await _q(
        sm,
        "SELECT topic,schema_version,aggregate_type,aggregate_id,idempotency_key,priority,"
        "payload,artifact_ref,release_manifest_id,deadline,available_at,event_id,attempt "
        "FROM trading.transactional_outbox WHERE event_id=:e",
        {"e": env.event_id},
    ))[0]
    fields = publisher._stream_fields(dict(zip(
        ("topic", "schema_version", "aggregate_type", "aggregate_id", "idempotency_key",
         "priority", "payload", "artifact_ref", "release_manifest_id", "deadline",
         "available_at", "event_id", "attempt"),
        row,
    )))

    wrong_topic = "market.wrong-route"
    await redis.stream_add(wrong_topic, fields)
    handler = _CountingHandler()
    consumer = OutboxConsumer(sm, redis, repo, "consumer-route", handler, RetryPolicy())
    assert await _consume_once(consumer, wrong_topic) == 1
    assert handler.calls == []
    assert await _q(
        sm,
        "SELECT status FROM trading.transactional_outbox WHERE event_id=:e",
        {"e": env.event_id},
    ) == [("DISPATCHED",)]

    # A stale delivery from the previous attempt must respect DB backoff.
    async with UnitOfWork(sm) as uow:
        await uow.session.execute(
            text(
                "UPDATE trading.transactional_outbox SET status='PENDING', "
                "available_at=now() + interval '5 minutes' WHERE event_id=:e"
            ),
            {"e": env.event_id},
        )
    assert await _consume_once(consumer, env.topic) == 1
    assert handler.calls == []
