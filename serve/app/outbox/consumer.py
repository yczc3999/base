"""Reliable Outbox consumer.

Correctness boundary:
- Redis consumer-group/lease is a delivery and load-shedding mechanism, not completion evidence.
- PostgreSQL advisory-xact lock serializes one logical ``handler_name + idempotency_key``.
- The DB-only handler, job completion, history, and outbox terminal transition share one UoW.
- Redis ACK/release happen only after the DB transaction commits.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from redis.exceptions import RedisError

from app.db.uow import UnitOfWork
from app.outbox.contracts import OutboxEnvelope, OutboxValidationError
from app.outbox.repository import OutboxConflictError, OutboxRepository
from app.services.redis_control import ControlRedisClient, LeaseHandle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_backoff_s: float = 1.0
    lease_ttl_s: float = 30.0
    group: str = "outbox-group"

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise ValueError("outbox_retry_max_attempts_invalid")
        if isinstance(self.base_backoff_s, bool) or self.base_backoff_s < 0:
            raise ValueError("outbox_retry_backoff_invalid")
        if isinstance(self.lease_ttl_s, bool) or self.lease_ttl_s <= 0:
            raise ValueError("outbox_retry_lease_ttl_invalid")
        if not isinstance(self.group, str) or not self.group.strip():
            raise ValueError("outbox_retry_group_invalid")


class OutboxConsumer:
    def __init__(
        self,
        session_factory,
        redis: ControlRedisClient,
        repo: OutboxRepository | None,
        consumer_id: str,
        handler,
        policy: RetryPolicy | None = None,
    ) -> None:
        handler_name = getattr(handler, "handler_name", None)
        if not isinstance(consumer_id, str) or not consumer_id.strip():
            raise ValueError("outbox_consumer_id_invalid")
        if not isinstance(handler_name, str) or not handler_name.strip():
            raise ValueError("outbox_handler_name_required")
        if not callable(getattr(handler, "handle", None)):
            raise TypeError("outbox_handler_invalid")
        self._session_factory = session_factory
        self._redis = redis
        self._repo = repo or OutboxRepository()
        self._consumer_id = consumer_id  # process/instance identity for Redis PEL
        self._handler_name = handler_name  # stable logical identity for DB idempotency
        self._handler = handler
        self._policy = policy or RetryPolicy()

    def _lease_name(self, event_id: str) -> str:
        return f"outbox:{self._handler_name}:{event_id}"

    async def _ensure_group(self, topic: str) -> None:
        await self._redis.stream_group_ensure(topic, self._policy.group)

    def _decode_envelope(self, fields: Mapping[str, str]) -> OutboxEnvelope:
        try:
            parts = json.loads(fields["envelope"])
            for key in ("deadline", "available_at"):
                if parts.get(key):
                    parts[key] = datetime.fromisoformat(parts[key])
            env = OutboxEnvelope(event_id=fields["event_id"], **parts)
        except (KeyError, ValueError, TypeError) as exc:
            raise OutboxValidationError("outbox_envelope_malformed") from exc
        env.validate()  # includes event-id/content-hash binding and aware timestamps
        return env

    async def _ack(self, topic: str, msg_id: str) -> None:
        await self._redis.stream_group_ack(topic, self._policy.group, msg_id)

    async def _release_best_effort(self, lease: LeaseHandle) -> None:
        try:
            await self._redis.release_lease(lease)
        except RedisError:
            logger.exception("outbox lease release failed")

    async def run_once(self, topics, count: int = 10, block_ms: int = 1000) -> int:
        processed = 0
        for topic in topics:
            await self._ensure_group(topic)
            try:
                new = await self._redis.stream_group_read(
                    topic,
                    self._policy.group,
                    self._consumer_id,
                    count=count,
                    block_ms=block_ms,
                )
                reclaimed = await self._redis.stream_group_reclaim(
                    topic,
                    self._policy.group,
                    self._consumer_id,
                    min_idle_ms=max(1000, int(self._policy.lease_ttl_s * 1000)),
                    count=count,
                )
            except RedisError:
                continue

            # XREADGROUP/XAUTOCLAIM 理论上互斥；仍按 id 去重以防 provider 兼容差异。
            messages = {msg_id: fields for msg_id, fields in [*new, *reclaimed]}
            for msg_id, fields in messages.items():
                try:
                    if await self._process(topic, msg_id, fields):
                        processed += 1
                except OutboxValidationError as exc:
                    await self._terminal_invalid(topic, msg_id, fields, exc.reason_code)
                    processed += 1
        return processed

    async def _process(
        self, topic: str, msg_id: str, fields: Mapping[str, str]
    ) -> bool:
        env = self._decode_envelope(fields)
        if env.topic != topic:
            raise OutboxValidationError("outbox_stream_topic_mismatch")
        lease = await self._redis.acquire_lease(
            self._lease_name(env.event_id),
            self._consumer_id,
            self._policy.lease_ttl_s,
        )
        if lease is None:
            return False
        # Explicit renewal validates current owner/token before opening the DB transaction.
        if not await self._redis.renew_lease(lease, self._policy.lease_ttl_s):
            await self._release_best_effort(lease)
            return False

        handler_started = False
        try:
            async with UnitOfWork(self._session_factory) as uow:
                await self._repo.lock_completion_key(
                    uow.session, self._handler_name, env.idempotency_key
                )
                if await self._repo.has_completion(
                    uow.session, self._handler_name, env.idempotency_key
                ):
                    pass
                else:
                    row = await self._repo.get_by_event_id(
                        uow.session, env.event_id, for_update=True
                    )
                    if row is None:
                        raise OutboxValidationError("outbox_event_not_found")
                    if row["topic"] != topic:
                        raise OutboxValidationError("outbox_database_topic_mismatch")

                    now = datetime.now(timezone.utc)
                    # Redis may still contain an old delivery after a retry/dead/complete
                    # transition.  PostgreSQL is authoritative: terminal or not-yet-due
                    # rows are ACKed without invoking the handler.  A due PENDING row may
                    # still be handled (e.g. publisher crashed after XADD but before the
                    # DISPATCHED update).
                    if row["status"] in {"COMPLETED", "DEAD"}:
                        pass
                    elif row["status"] not in {"PENDING", "DISPATCHED"}:
                        raise OutboxValidationError("outbox_database_status_invalid")
                    elif (
                        row["status"] == "PENDING"
                        and row["available_at"] > now
                    ):
                        pass
                    elif env.deadline is not None and env.deadline <= now:
                        await self._repo.insert_completion(
                            uow.session,
                            self._handler_name,
                            env.idempotency_key,
                            "dead",
                        )
                        if not await self._repo.dead_by_event_id(
                            uow.session, env.event_id, "deadline_expired"
                        ):
                            raise OutboxConflictError("outbox_deadline_transition_conflict")
                        await self._repo.insert_history(
                            uow.session,
                            outbox_event_id=env.event_id,
                            outbox_id=row["id"],
                            status="EXPIRED",
                            consumer=self._handler_name,
                            attempt=row["attempt"],
                            error_reason="deadline_expired",
                        )
                    else:
                        handler_started = True
                        await self._handler.handle(env, uow, lease.token)
                        await self._repo.insert_completion(
                            uow.session,
                            self._handler_name,
                            env.idempotency_key,
                            "success",
                        )
                        await self._repo.insert_history(
                            uow.session,
                            outbox_event_id=env.event_id,
                            outbox_id=row["id"],
                            status="DELIVERED",
                            consumer=self._handler_name,
                            attempt=row["attempt"],
                            error_reason=None,
                        )
                        if not await self._repo.complete(uow.session, row["id"]):
                            raise OutboxConflictError("outbox_completion_transition_conflict")
        except Exception as original:
            if handler_started:
                await self._record_failure_preserving_original(
                    topic, msg_id, env, original
                )
            await self._release_best_effort(lease)
            raise

        try:
            await self._ack(topic, msg_id)
        finally:
            await self._release_best_effort(lease)
        return True

    async def _record_failure_preserving_original(
        self,
        topic: str,
        msg_id: str,
        env: OutboxEnvelope,
        original: Exception,
    ) -> None:
        """Persist retry/dead without ever replacing the handler's original exception."""
        should_ack = False
        try:
            async with UnitOfWork(self._session_factory) as uow:
                await self._repo.lock_completion_key(
                    uow.session, self._handler_name, env.idempotency_key
                )
                if await self._repo.has_completion(
                    uow.session, self._handler_name, env.idempotency_key
                ):
                    should_ack = True
                else:
                    row = await self._repo.get_by_event_id(
                        uow.session, env.event_id, for_update=True
                    )
                    if row is None:
                        return
                    attempt = row["attempt"] + 1
                    if attempt < self._policy.max_attempts:
                        next_available = datetime.now(timezone.utc) + timedelta(
                            seconds=self._policy.base_backoff_s
                        )
                        if not await self._repo.retry_by_event_id(
                            uow.session, env.event_id, next_available
                        ):
                            raise OutboxConflictError("outbox_retry_transition_conflict")
                    else:
                        if not await self._repo.dead_by_event_id(
                            uow.session, env.event_id, "max_attempts"
                        ):
                            raise OutboxConflictError("outbox_dead_transition_conflict")
                        await self._repo.insert_history(
                            uow.session,
                            outbox_event_id=env.event_id,
                            outbox_id=row["id"],
                            status="DEAD",
                            consumer=self._handler_name,
                            attempt=attempt,
                            error_reason="max_attempts",
                        )
                    should_ack = True
        except Exception:
            logger.exception(
                "outbox failure transition failed; original handler error preserved"
            )
            return
        # DB recovery decision committed; ACK is best-effort. A lost ACK is safe via completion/retry.
        if should_ack:
            try:
                await self._ack(topic, msg_id)
            except RedisError:
                logger.exception(
                    "outbox failure ACK failed; original handler error preserved"
                )

    async def _terminal_invalid(
        self,
        topic: str,
        msg_id: str,
        fields: Mapping[str, str],
        reason: str,
    ) -> None:
        """Quarantine an invalid Redis delivery without mutating PostgreSQL facts.

        Redis is disposable transport.  A malformed/copied message must never be able to
        mark the authoritative outbox row DEAD merely by presenting a valid-looking event
        id.  ACK removes this transport artifact; the DB row remains eligible for normal
        publisher/sweeper recovery.
        """
        logger.warning(
            "outbox invalid transport delivery quarantined",
            extra={"reason_code": reason, "stream_topic": topic},
        )
        await self._ack(topic, msg_id)
