"""Outbox 常驻进程包装（WP-07C）。

把 :class:`app.outbox` 的 Publisher/Sweeper/Consumer 三个类包装成可常驻运行的
async 循环。进程隔离边界（合同 §8）：

- 本 runtime 独占 ``outbox`` DB pool + control Redis；不与 execution 共用 pool/并发池。
- Publisher / Sweeper / Consumer 各自一个常驻任务，统一由 supervisor 启停。
- 每个循环都尊重共享 ``stop_event``；每轮 sleep 让出调度，不阻塞其他 runtime。

Consumer 经 :class:`runtimes.trading._dispatch.TradingEventDispatch` 把 envelope
路由到 trading handler；Redis 只作可丢弃传输，PG outbox 表是权威事实源。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.outbox.consumer import OutboxConsumer, RetryPolicy
from app.outbox.publisher import OutboxPublisher
from app.outbox.repository import OutboxRepository
from app.outbox.sweeper import OutboxSweeper
from app.services.redis_control import ControlRedisClient

from runtimes.trading._dispatch import ALL_TOPICS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboxLoopPolicy:
    """常驻循环节奏；全部秒级，便于 supervisor 配置。"""

    publish_interval_s: float = 0.5
    sweep_interval_s: float = 5.0
    publish_batch: int = 20
    sweep_batch: int = 20
    consumer_count: int = 10
    consumer_block_ms: int = 1000
    retry_max_attempts: int = 5
    lease_ttl_s: float = 30.0


class OutboxPublisherRuntime:
    """常驻 publisher：循环 claim→XADD→DISPATCHED。"""

    def __init__(
        self,
        session_factory,
        redis: ControlRedisClient,
        *,
        owner: str = "outbox:publisher",
        policy: OutboxLoopPolicy | None = None,
    ) -> None:
        self._policy = policy or OutboxLoopPolicy()
        self._publisher = OutboxPublisher(
            session_factory, redis, OutboxRepository(), owner=owner
        )

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self._publisher.run_once(batch_size=self._policy.publish_batch)
            except Exception:  # noqa: BLE001 - 传输/DB 抖动不终止常驻循环
                logger.exception("outbox_publisher_tick_failed")
            await asyncio.sleep(self._policy.publish_interval_s)


class OutboxSweeperRuntime:
    """常驻 sweeper：循环回收过期租约 / DEAD / requeue。"""

    def __init__(
        self,
        session_factory,
        *,
        policy: OutboxLoopPolicy | None = None,
    ) -> None:
        self._policy = policy or OutboxLoopPolicy()
        self._sweeper = OutboxSweeper(
            session_factory,
            OutboxRepository(),
            max_attempts=self._policy.retry_max_attempts,
            batch_size=self._policy.sweep_batch,
        )

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self._sweeper.run_once()
            except Exception:  # noqa: BLE001
                logger.exception("outbox_sweeper_tick_failed")
            await asyncio.sleep(self._policy.sweep_interval_s)


class OutboxConsumerRuntime:
    """常驻 consumer：循环消费 outbox topic 并经 dispatch 分发到 trading handler。"""

    def __init__(
        self,
        session_factory,
        redis: ControlRedisClient,
        dispatch,
        *,
        consumer_id: str = "outbox:consumer",
        topics: tuple[str, ...] = ALL_TOPICS,
        policy: OutboxLoopPolicy | None = None,
    ) -> None:
        self._policy = policy or OutboxLoopPolicy()
        self._topics = topics
        # dispatch 需暴露 handler_name（OutboxConsumer 用它做 DB 幂等锁）。
        if not getattr(dispatch, "handler_name", None):
            dispatch.handler_name = "trading-dispatch"
        self._consumer = OutboxConsumer(
            session_factory,
            redis,
            OutboxRepository(),
            consumer_id,
            dispatch,
            RetryPolicy(
                max_attempts=self._policy.retry_max_attempts,
                lease_ttl_s=self._policy.lease_ttl_s,
            ),
        )

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self._consumer.run_once(
                    self._topics,
                    count=self._policy.consumer_count,
                    block_ms=self._policy.consumer_block_ms,
                )
            except Exception:  # noqa: BLE001
                logger.exception("outbox_consumer_tick_failed")
            await asyncio.sleep(0)  # block_ms 已提供节奏；让出调度即可
