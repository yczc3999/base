"""
WP-01A-02 Outbox Repository —— 真 PostgreSQL 集成验收（Checkpoint D）。

覆盖：enqueue 与业务事实同一事务原子性；claim 设 lease 且不重复认领；DISPATCHED 条件更新
（非 owner/token 拒绝）；completion/history/收敛；payload XOR artifact 由 DB CHECK 拒绝。
"""

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork
from app.outbox.contracts import create_envelope
from app.outbox.repository import (
    STATUS_COMPLETED,
    STATUS_DISPATCHED,
    STATUS_PENDING,
    OutboxRepository,
    OutboxConflictError,
)


def _async_url(db_url: str) -> str:
    return make_url(db_url).set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )


@pytest.fixture
async def stack(migrated_pg_db):
    engine = create_async_engine(_async_url(migrated_pg_db.url), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = OutboxRepository()
    try:
        yield factory, repo, migrated_pg_db
    finally:
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
        idempotency_key=f"idem-{uuid.uuid4().hex[:8]}",
        payload={"outcome": "yes"},
    )
    base.update(kw)
    return create_envelope(**base)


async def test_enqueue_and_business_write_atomic(stack):
    sm, repo, _db = stack
    env = _env()
    with pytest.raises(RuntimeError, match="boom"):
        async with UnitOfWork(sm) as uow:
            await uow.session.execute(
                text(
                    "INSERT INTO trading.artifact_objects "
                    "(sha256, original_size, stored_size, mime, compression, storage_driver, storage_version, locator) "
                    "VALUES (:sha, 5, 5, 'application/octet-stream', 'none', 'local', 'cas/v1', :locator)"
                ),
                {
                    "sha": "0" * 64,
                    "locator": f"cas/v1/sha256/00/00/{'0' * 64}.raw",
                },
            )
            await repo.enqueue(uow.session, env)
            raise RuntimeError("boom")
    # 两者都回滚：业务行与 outbox 行都不存在
    assert await _q(sm, "SELECT count(*) FROM trading.artifact_objects") == [(0,)]
    assert await _q(sm, "SELECT count(*) FROM trading.transactional_outbox") == [(0,)]


async def test_enqueue_persists_on_commit(stack):
    sm, repo, _db = stack
    env = _env()
    async with UnitOfWork(sm) as uow:
        await repo.enqueue(uow.session, env)
    rows = await _q(
        sm,
        "SELECT event_id, topic, status FROM trading.transactional_outbox WHERE event_id=:e",
        {"e": env.event_id},
    )
    assert rows == [(env.event_id, env.topic, STATUS_PENDING)]


async def test_enqueue_same_idempotency_key_different_content_rejected(stack):
    sm, repo, _db = stack
    first = _env(idempotency_key="stable-key", payload={"value": 1})
    second = _env(idempotency_key="stable-key", payload={"value": 2})
    async with UnitOfWork(sm) as uow:
        await repo.enqueue(uow.session, first)
    with pytest.raises(OutboxConflictError, match="outbox_idempotency_key_reused"):
        async with UnitOfWork(sm) as uow:
            await repo.enqueue(uow.session, second)
    assert await _q(sm, "SELECT count(*) FROM trading.transactional_outbox") == [(1,)]


async def test_enqueue_exact_retry_is_idempotent(stack):
    sm, repo, _db = stack
    env = _env(idempotency_key="exact-retry")
    async with UnitOfWork(sm) as uow:
        await repo.enqueue(uow.session, env)
    async with UnitOfWork(sm) as uow:
        await repo.enqueue(uow.session, env)
    assert await _q(sm, "SELECT count(*) FROM trading.transactional_outbox") == [(1,)]


async def test_claim_sets_lease_and_no_double_claim(stack):
    sm, repo, _db = stack
    env = _env()
    async with UnitOfWork(sm) as uow:
        await repo.enqueue(uow.session, env)
    async with UnitOfWork(sm) as uow:
        claimed = await repo.claim(uow.session, "p1", 10, 60)
    assert len(claimed) == 1
    assert claimed[0]["lease_owner"] == "p1"
    assert claimed[0]["lease_token"]
    # 同一行在 visibility 内不可再次认领
    async with UnitOfWork(sm) as uow:
        again = await repo.claim(uow.session, "p2", 10, 60)
    assert again == []


async def test_mark_dispatched_conditional(stack):
    sm, repo, _db = stack
    env = _env()
    async with UnitOfWork(sm) as uow:
        await repo.enqueue(uow.session, env)
    async with UnitOfWork(sm) as uow:
        claimed = await repo.claim(uow.session, "p1", 10, 60)
    row = claimed[0]
    async with UnitOfWork(sm) as uow:
        # 错误 token → 拒绝
        assert await repo.mark_dispatched(uow.session, row["id"], "p1", "wrong") is False
        # 正确 owner/token → 成功
        assert await repo.mark_dispatched(uow.session, row["id"], row["lease_owner"], row["lease_token"]) is True
    rows = await _q(sm, f"SELECT status FROM trading.transactional_outbox WHERE event_id='{env.event_id}'")
    assert rows == [(STATUS_DISPATCHED,)]


async def test_complete_completion_history_flow(stack):
    sm, repo, _db = stack
    env = _env()
    async with UnitOfWork(sm) as uow:
        await repo.enqueue(uow.session, env)
        row = await repo.get_by_event_id(uow.session, env.event_id)
        await repo.insert_completion(uow.session, "consumer-x", env.idempotency_key, "success")
        await repo.insert_history(
            uow.session,
            outbox_event_id=env.event_id,
            outbox_id=row["id"],
            status="DELIVERED",
            consumer="consumer-x",
            attempt=0,
            error_reason=None,
        )
        assert await repo.complete(uow.session, row["id"]) is True
    assert await _q(sm, f"SELECT status FROM trading.transactional_outbox WHERE event_id='{env.event_id}'") == [(STATUS_COMPLETED,)]
    assert await _q(sm, f"SELECT count(*) FROM trading.job_completions WHERE idempotency_key='{env.idempotency_key}'") == [(1,)]
    assert await _q(sm, "SELECT count(*) FROM trading.outbox_delivery_history WHERE status='DELIVERED'") == [(1,)]
    # 重复 completion 唯一约束拒绝
    with pytest.raises(Exception):
        async with UnitOfWork(sm) as uow:
            await repo.insert_completion(uow.session, "consumer-x", env.idempotency_key, "success")


async def test_payload_xor_artifact_enforced_by_db(stack):
    sm, repo, _db = stack
    # 绕过应用层，直接写两个都非空 → DB CHECK 拒绝
    with pytest.raises(Exception):
        async with UnitOfWork(sm) as uow:
            await uow.session.execute(
                text(
                    "INSERT INTO trading.transactional_outbox "
                    "(event_id, topic, schema_version, aggregate_type, aggregate_id, idempotency_key, "
                    " payload, artifact_ref, status) "
                    "VALUES (:e, 't', 1, 'a', 'i', 'k', '{\"a\":1}', :ar, 'PENDING')"
                ),
                {"e": uuid.uuid4().hex, "ar": "a" * 64},
            )


async def test_two_concurrent_claims_single_claim(stack):
    """两个并发 claim（同批）不会重复认领同一行。"""
    sm, repo, _db = stack
    for i in range(5):
        env = _env()
        async with UnitOfWork(sm) as uow:
            await repo.enqueue(uow.session, env)

    results: dict[str, list] = {}
    async def _claim(owner):
        async with UnitOfWork(sm) as uow:
            rows = await repo.claim(uow.session, owner, 10, 60)
        results[owner] = rows

    await asyncio.gather(_claim("p1"), _claim("p2"))
    assert sorted(len(v) for v in results.values()) == [0, 5]  # 一个 publisher 拿到全部，另一个 0
    # 赢家的 5 个 id 互不相同（无重复认领）
    winner = max(results.values(), key=len)
    assert len({r["id"] for r in winner}) == 5
