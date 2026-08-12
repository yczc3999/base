"""Outbox → trading handler 分发适配（WP-07C）。

把 outbox 传输层的 ``OutboxEnvelope`` 翻译为 trading 域事件并调用对应
``<X>Handler.handle(uow, event, …)``；把 ``HandlerResult.ok`` 映射为消费成功/失败，
由 :class:`app.outbox.consumer.OutboxConsumer` 据此 complete / retry / dead。

设计边界（v2-implementation-contract §8 / ARCHITECTURE §3.1）：
- Handler 只解析 event、调用一个 Logic/UoW、返回 completion；本适配层也只负责
  envelope→event 的结构翻译与路由，**不写业务 SQL、不重算 Gate/PnL**。
- outbox topic 是**事后事实通知**（blind_commit / chain.settlement / shadow.execution /
  universe.frame / market.book …）。消费端把对应事实转发给域 handler，让域按
  ``kind`` 决定是否推进 gate。未知 topic 或缺关键字段 → fail closed（返回 False），
  由 consumer 记 retry/dead，绝不静默丢弃。
- 事件 ``kind`` 与 payload 取自 envelope ``payload``；``payload["kind"]`` 约定由生产侧
  写入（forecast/execution/settlement/market_ingest 的 create_envelope payload）。
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine

from app.db.uow import UnitOfWork
from app.handlers.trading.cognition import CognitionEvent, CognitionHandler
from app.handlers.trading.decision import DecisionEvent, DecisionHandler
from app.handlers.trading.evaluation import EvaluationEvent, EvaluationHandler
from app.handlers.trading.execution import ExecutionEvent, ExecutionHandler
from app.handlers.trading.settlement import SettlementEvent, SettlementHandler
from app.outbox.contracts import OutboxEnvelope

# 生产侧 topic（与 create_envelope 调用点对齐；改生产 topic 必须同步此表）。
TOPIC_BLIND_COMMIT = "trading.blind_commit.v1"
TOPIC_CHAIN_SETTLEMENT_FINALIZED = "chain.settlement.finalized"
TOPIC_SHADOW_EXECUTION_TERMINALIZED = "shadow.execution.terminalized"
TOPIC_UNIVERSE_FRAME = "universe.frame"
TOPIC_UNIVERSE_REFRESH = "universe.refresh"
TOPIC_MARKET_BOOK = "market.book"
TOPIC_MARKET_CONFIG_REFRESH = "market.config.refresh"

# 消费端订阅的全部 outbox topic（7 个；未列出的 topic 不会被 consumer 订阅）。
ALL_TOPICS: tuple[str, ...] = (
    TOPIC_BLIND_COMMIT,
    TOPIC_CHAIN_SETTLEMENT_FINALIZED,
    TOPIC_SHADOW_EXECUTION_TERMINALIZED,
    TOPIC_UNIVERSE_FRAME,
    TOPIC_UNIVERSE_REFRESH,
    TOPIC_MARKET_BOOK,
    TOPIC_MARKET_CONFIG_REFRESH,
)


class TradingEventDispatch:
    """把 outbox envelope 分发到固定 trading handler 集合。

    持有 5 个域 handler 的工厂注入；每次消费用 consumer 传入的 UoW 调 handler，
    让业务写与 job completion 共用同一事务（OutboxConsumer 正确性边界）。
    """

    def __init__(
        self,
        *,
        cognition: CognitionHandler,
        decision: DecisionHandler,
        evaluation: EvaluationHandler,
        execution: ExecutionHandler,
        settlement: SettlementHandler,
    ) -> None:
        self._cognition = cognition
        self._decision = decision
        self._evaluation = evaluation
        self._execution = execution
        self._settlement = settlement

    # ---- OutboxHandler 协议 ----

    async def handle(
        self,
        envelope: OutboxEnvelope,
        uow: UnitOfWork,
        fencing_token: int,
    ) -> None:
        result = await self.dispatch(envelope, uow)
        if result is not True:
            # fail closed：让 consumer 记 retry/dead；reason 由日志侧探查，不透传敏感。
            raise RuntimeError("trading_dispatch_failed")

    async def dispatch(self, envelope: OutboxEnvelope, uow: UnitOfWork) -> bool:
        """路由并按 topic 重建域事件；返回 True 表示 handler 接受（ok=True）。"""
        payload = envelope.payload or {}
        topic = envelope.topic
        handler, event = self._route(topic, payload)
        if handler is None:
            return False
        # 部分 handler 需要 version_manifest_id / policy_hash；release_manifest_id 即版本清单。
        return await self._call(handler, event, uow, envelope)

    # ---- 路由：topic → (handler, event) ----

    def _route(
        self, topic: str, payload: dict[str, Any]
    ) -> tuple[Any | None, Any | None]:
        kind = payload.get("kind")
        if topic == TOPIC_BLIND_COMMIT:
            # 盲提交已封账 → 触发 reveal/decision 链（worker 7）。episode 由 payload 提供。
            if kind is None:
                kind = "create"
            return self._decision, DecisionEvent(
                kind=kind,
                episode_id=payload.get("episode_id"),
                payload=payload,
            )
        if topic == TOPIC_CHAIN_SETTLEMENT_FINALIZED:
            return self._settlement, SettlementEvent(
                kind=kind or "label_revision",
                payload=payload,
            )
        if topic == TOPIC_SHADOW_EXECUTION_TERMINALIZED:
            return self._execution, ExecutionEvent(
                kind=kind or "shadow_fill",
                payload=payload,
            )
        if topic in (TOPIC_UNIVERSE_FRAME, TOPIC_UNIVERSE_REFRESH):
            # 行情/名册事实 → 评价/学习链（worker 8）按 kind 决定 enroll/score。
            return self._evaluation, EvaluationEvent(
                kind=kind or "score_observation",
                payload=payload,
            )
        if topic in (TOPIC_MARKET_BOOK, TOPIC_MARKET_CONFIG_REFRESH):
            # 实时 quote/tick → 决策链（worker 7 的 quote trigger，不新增 cognition episode）。
            return self._decision, DecisionEvent(
                kind=kind or "market_relative",
                payload=payload,
            )
        return None, None

    async def _call(self, handler: Any, event: Any, uow: UnitOfWork, env: OutboxEnvelope) -> bool:
        kwargs: dict[str, Any] = {}
        # cognition/decision 支持版本与策略 hash；其余 handler 只收 uow+event。
        if isinstance(event, (CognitionEvent, DecisionEvent)):
            kwargs["version_manifest_id"] = env.release_manifest_id
            policy_hash = (env.payload or {}).get("policy_hash")
            if policy_hash is not None:
                kwargs["policy_hash"] = policy_hash
        result = await handler.handle(uow, event, **kwargs)
        return bool(result.ok)
