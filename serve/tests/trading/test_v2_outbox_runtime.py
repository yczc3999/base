"""
WP-01A-02 Outbox runtime —— 无数据库的单测（真实 Redis consumer-group + 纯逻辑）。

覆盖：consumer-group ensure/read/ack/pending/claim 原语；consumer envelope 解码/校验；
publisher stream 字段序列化往返；RetryPolicy 默认值；fail-closed 语义。

每个 Redis 测试用随机 namespace，结束后只删除自己创建的精确 key，不 flushdb。
"""

import asyncio
import json
from uuid import uuid4

import pytest

from app.config import ControlRedisEndpoint
from app.outbox.contracts import OutboxEnvelope, OutboxValidationError, create_envelope
from app.outbox.consumer import OutboxConsumer, RetryPolicy
from app.outbox.publisher import OutboxPublisher
from app.outbox.repository import OutboxRepository
from app.services.redis_control import ControlRedisClient
from app.services.redis_keys import build_redis_key

HOST = "localhost"
PORT = 6379


def _endpoint() -> ControlRedisEndpoint:
    return ControlRedisEndpoint(
        url=f"redis://{HOST}:{PORT}/0",
        max_connections=5,
        connect_timeout_s=2.0,
        read_timeout_s=2.0,
        health_check_interval_s=30.0,
        namespace=f"pm:it:{uuid4().hex}:control",
    )


def _client():
    return ControlRedisClient(_endpoint())


async def _cleanup(c: ControlRedisClient, keys: list[str]) -> None:
    if keys:
        await c._client.delete(*keys)


def _run(coro):
    return asyncio.run(coro)


def _env(**kw):
    base = dict(
        topic="market.resolved",
        schema_version=1,
        aggregate_type="market",
        aggregate_id="m-1",
        idempotency_key="idem-1",
        payload={"outcome": "yes"},
    )
    base.update(kw)
    return create_envelope(**base)


def _dummy_consumer(redis):
    class _Factory:
        pass
    class _Handler:
        handler_name = "dummy"

        async def handle(self, env, uow, fencing_token):
            return None
    return OutboxConsumer(
        session_factory=_Factory(),
        redis=redis,
        repo=OutboxRepository(),
        consumer_id="c1",
        handler=_Handler(),
        policy=RetryPolicy(),
    )


# ---------------- consumer-group 原语（真实 Redis） ----------------

def test_stream_group_ensure_idempotent():
    async def _t():
        c = _client()
        name, group = f"g{uuid4().hex[:6]}", "grp1"
        key = build_redis_key(c.namespace, "stream", name)
        try:
            assert await c.stream_group_ensure(name, group) is True
            assert await c.stream_group_ensure(name, group) is False  # BUSYGROUP
        finally:
            await _cleanup(c, [key])
            await c.aclose()
    _run(_t())


def test_stream_group_ensure_does_not_swallow_non_busygroup_response_error(monkeypatch):
    from redis.exceptions import ResponseError

    async def _t():
        c = _client()
        async def bad(*args, **kwargs):
            raise ResponseError("WRONGTYPE operation against a key")
        monkeypatch.setattr(c._client, "xgroup_create", bad)
        try:
            with pytest.raises(ResponseError, match="WRONGTYPE"):
                await c.stream_group_ensure("x", "g")
        finally:
            await c.aclose()
    _run(_t())


def test_stream_group_read_ack_pending_flow():
    async def _t():
        c = _client()
        name, group, consumer = f"g{uuid4().hex[:6]}", "grp", "consumer-a"
        key = build_redis_key(c.namespace, "stream", name)
        try:
            await c.stream_group_ensure(name, group)
            mid = await c.stream_add(name, {"event_id": "abc", "attempt": "0"})
            items = await c.stream_group_read(name, group, consumer)
            assert len(items) == 1
            assert items[0][0] == mid
            assert items[0][1]["event_id"] == "abc"
            # 未 ACK 前消息在 pending 明细
            detail = await c.stream_group_pending_detail(name, group)
            assert any(p["id"] == mid for p in detail)
            # ACK 后 pending 清零
            acked = await c.stream_group_ack(name, group, mid)
            assert acked == 1
            detail2 = await c.stream_group_pending_detail(name, group)
            assert all(p["id"] != mid for p in detail2)
        finally:
            await _cleanup(c, [key])
            await c.aclose()
    _run(_t())


def test_stream_group_claim_steals_idle_pending():
    async def _t():
        c = _client()
        name, group = f"g{uuid4().hex[:6]}", "grp"
        key = build_redis_key(c.namespace, "stream", name)
        try:
            await c.stream_group_ensure(name, group)
            mid = await c.stream_add(name, {"event_id": "x"})
            # consumer A 读取但不 ACK（模拟崩溃遗留）
            await c.stream_group_read(name, group, "consumer-a")
            await asyncio.sleep(1.1)
            # consumer B 认领 idle 超 1s 的 pending
            claimed = await c.stream_group_claim(name, group, "consumer-b", 1000, mid)
            assert len(claimed) == 1
            detail = await c.stream_group_pending_detail(name, group, consumer="consumer-b")
            assert detail and detail[0]["id"] == mid
        finally:
            await _cleanup(c, [key])
            await c.aclose()
    _run(_t())


# ---------------- consumer envelope 解码 / 校验 ----------------

def test_consumer_decode_envelope_roundtrip():
    async def _t():
        c = _client()
        try:
            consumer = _dummy_consumer(c)
            env = _env()
            fields = OutboxPublisher(None, c)._stream_fields(
                {
                    "topic": env.topic,
                    "schema_version": env.schema_version,
                    "aggregate_type": env.aggregate_type,
                    "aggregate_id": env.aggregate_id,
                    "idempotency_key": env.idempotency_key,
                    "priority": env.priority,
                    "payload": env.payload,
                    "artifact_ref": env.artifact_ref,
                    "release_manifest_id": env.release_manifest_id,
                    "deadline": env.deadline,
                    "available_at": env.available_at,
                    "event_id": env.event_id,
                    "attempt": 0,
                }
            )
            decoded = consumer._decode_envelope(fields)
            assert isinstance(decoded, OutboxEnvelope)
            assert decoded.event_id == env.event_id
            assert decoded.payload == env.payload
        finally:
            await c.aclose()
    _run(_t())


def test_consumer_decode_malformed_envelope_fails():
    async def _t():
        c = _client()
        try:
            consumer = _dummy_consumer(c)
            with pytest.raises(OutboxValidationError, match="outbox_envelope_malformed"):
                consumer._decode_envelope({"event_id": "x", "envelope": "{not-json"})
            with pytest.raises(OutboxValidationError):
                consumer._decode_envelope(
                    {"event_id": "x" * 64, "envelope": json.dumps({"topic": ""})}
                )
        finally:
            await c.aclose()
    _run(_t())


# ---------------- publisher 序列化 / retry policy ----------------

def test_publisher_stream_fields_json_roundtrip():
    env = _env()
    fields = OutboxPublisher(None, None)._stream_fields(
        {
            "topic": env.topic,
            "schema_version": env.schema_version,
            "aggregate_type": env.aggregate_type,
            "aggregate_id": env.aggregate_id,
            "idempotency_key": env.idempotency_key,
            "priority": env.priority,
            "payload": env.payload,
            "artifact_ref": env.artifact_ref,
            "release_manifest_id": env.release_manifest_id,
            "deadline": env.deadline,
            "available_at": env.available_at,
            "event_id": env.event_id,
            "attempt": 0,
        }
    )
    assert fields["event_id"] == env.event_id
    parsed = json.loads(fields["envelope"])
    assert parsed["payload"] == env.payload
    assert parsed["idempotency_key"] == env.idempotency_key


def test_retry_policy_defaults():
    p = RetryPolicy()
    assert p.max_attempts == 5
    assert p.base_backoff_s > 0
    assert p.lease_ttl_s > 0
    assert p.group == "outbox-group"


def test_publisher_stream_maps_to_topic():
    p = OutboxPublisher(None, None)
    assert p._stream_for("market.resolved") == "market.resolved"
