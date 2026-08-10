"""Outbox Publisher（WP-01A-02，Checkpoint D）。

短事务 claim → commit → Redis Stream publish → 新事务标 ``DISPATCHED``。publish 超时/异常
保持可恢复状态（行保持 PENDING + lease，visibility 过期后重投），**不写 completion**。
绝不在持锁事务中做网络调用。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from redis.exceptions import RedisError

from app.db.uow import UnitOfWork
from app.outbox.repository import OutboxRepository
from app.services.redis_control import ControlRedisClient


class OutboxPublisher:
    def __init__(
        self,
        session_factory,
        redis: ControlRedisClient,
        repo: OutboxRepository | None = None,
        *,
        owner: str | None = None,
        visibility_seconds: int = 60,
        stream_maxlen: int = 5000,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis
        self._repo = repo or OutboxRepository()
        self._owner = owner or "publisher:default"
        self._visibility_seconds = visibility_seconds
        self._stream_maxlen = stream_maxlen

    def _stream_for(self, topic: str) -> str:
        # stream 名 = topic；redis_control 在其 namespace 下统一加 stream: 前缀
        return topic

    def _stream_fields(self, row: Mapping[str, Any]) -> dict[str, str]:
        envelope = {
            "topic": row["topic"],
            "schema_version": row["schema_version"],
            "aggregate_type": row["aggregate_type"],
            "aggregate_id": row["aggregate_id"],
            "idempotency_key": row["idempotency_key"],
            "priority": row["priority"],
            "payload": row["payload"],
            "artifact_ref": row["artifact_ref"],
            "release_manifest_id": row["release_manifest_id"],
            "deadline": row["deadline"].isoformat() if row["deadline"] else None,
            "available_at": row["available_at"].isoformat() if row["available_at"] else None,
        }
        return {
            "event_id": row["event_id"],
            "attempt": str(row["attempt"]),
            "envelope": json.dumps(envelope, sort_keys=True, ensure_ascii=False),
        }

    async def run_once(self, batch_size: int = 10) -> int:
        """claim → commit → publish → DISPATCHED；返回成功发布并标记的条数。"""
        # TX1：短事务 claim（设 lease），commit 后网络调用
        async with UnitOfWork(self._session_factory) as uow:
            claimed = await self._repo.claim(
                uow.session, self._owner, batch_size, self._visibility_seconds
            )

        published = 0
        for row in claimed:
            stream = self._stream_for(row["topic"])
            try:
                await self._redis.stream_add(
                    stream, self._stream_fields(row), maxlen=self._stream_maxlen, approximate=True
                )
            except RedisError:
                # 保持可恢复：行仍是 PENDING + lease，visibility 过期后由另一 publisher 重投
                continue

            # TX2：新事务标 DISPATCHED（非 owner / 状态已变 → 不写、不计成功）
            async with UnitOfWork(self._session_factory) as uow:
                ok = await self._repo.mark_dispatched(
                    uow.session, row["id"], row["lease_owner"], row["lease_token"]
                )
                if not ok:
                    continue
            published += 1
        return published
