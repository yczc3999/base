"""Outbox Sweeper（WP-01A-02，Checkpoint D）。

只认领 visibility 已过期且未 completion 的行；原子重投并递增 attempt；到上限写 terminal
history（DEAD）。未知状态 fail-closed，不删除。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.db.uow import UnitOfWork
from app.outbox.repository import OutboxRepository

logger = logging.getLogger(__name__)


class OutboxSweeper:
    def __init__(
        self,
        session_factory,
        repo: OutboxRepository | None = None,
        *,
        max_attempts: int = 5,
        backoff_s: int = 30,
        batch_size: int = 20,
    ) -> None:
        self._session_factory = session_factory
        self._repo = repo or OutboxRepository()
        self._max_attempts = max_attempts
        self._backoff_s = backoff_s
        self._batch_size = batch_size

    async def run_once(self) -> int:
        """回收过期行；返回 requeue + dead 总数。"""
        async with UnitOfWork(self._session_factory) as uow:
            rows = await self._repo.claim_expired(
                uow.session, self._batch_size, self._backoff_s, self._max_attempts
            )
            handled = 0
            for row in rows:
                if row["deadline"] is not None and row["deadline"] <= datetime.now(timezone.utc):
                    ok = await self._repo.mark_dead(
                        uow.session, row["id"], "deadline_expired"
                    )
                    if ok:
                        await self._repo.insert_history(
                            uow.session,
                            outbox_event_id=row["event_id"],
                            outbox_id=row["id"],
                            status="EXPIRED",
                            consumer="sweeper",
                            attempt=row["attempt"],
                            error_reason="deadline_expired",
                        )
                        handled += 1
                elif row["attempt"] >= self._max_attempts:
                    # 到上限 → terminal history + DEAD
                    ok = await self._repo.dead_by_event_id(
                        uow.session, row["event_id"], "max_attempts"
                    )
                    if ok:
                        await self._repo.insert_history(
                            uow.session,
                            outbox_event_id=row["event_id"],
                            outbox_id=row["id"],
                            status="DEAD",
                            consumer="sweeper",
                            attempt=row["attempt"] + 1,
                            error_reason="max_attempts",
                        )
                        handled += 1
                else:
                    # 原子重投：attempt+1，available_at 后移
                    next_avail = datetime.now(timezone.utc) + timedelta(seconds=self._backoff_s)
                    ok = await self._repo.retry_by_event_id(
                        uow.session, row["event_id"], next_avail
                    )
                    if ok:
                        await self._repo.insert_history(
                            uow.session,
                            outbox_event_id=row["event_id"],
                            outbox_id=row["id"],
                            status="REQUEUED",
                            consumer="sweeper",
                            attempt=row["attempt"] + 1,
                            error_reason=None,
                        )
                        handled += 1
        return handled
