"""Outbox → trading handler 分发适配（WP-07C）。

架构边界（修正后）：
- **认知/决策/执行链由 pipeline driver 主动推进**（`runtimes/trading/pipeline.py`，
  状态机表轮询），**不经 outbox 触发**。outbox 只承载**事后事实通知**——某 gate 已
  封账/某帧已完成/某 fill 已终态——供下游投影/审计/告警消费。
- 现有生产 topic（blind_commit / chain.settlement.finalized / shadow.execution.
  terminalized / universe.frame / universe.refresh / market.book / market.config.
  refresh）的 payload 是**事实摘要**（frame_id/episode_key/operation_key/…），
  **不是** handler 的输入结构（DecisionEvent/EvaluationEvent 等需要 episode_id /
  trade_decision_id / 强类型 input）。把这些摘要认真塞进 handler 会因字段不匹配
  dead-letter——这是 Checkpoint A 初版的错误，本版纠正。
- 因此本适配层对**事实通知类 topic 默认安全确认**（返回 ok=True，consumer 标记
  complete），不驱业务。真正消费事实做投影/审计的 handler 用显式 ``kind`` 注册进
  ``_KIND_ROUTES``，按需扩展——未知 kind / 缺字段 fail closed。

Handler 固定为 5 个域 handler 的工厂注入；只在显式 kind 路由命中时才调用。
"""

from __future__ import annotations

from typing import Any

from app.db.uow import UnitOfWork
from app.handlers.trading.cognition import CognitionEvent, CognitionHandler
from app.handlers.trading.decision import DecisionEvent, DecisionHandler
from app.handlers.trading.evaluation import EvaluationEvent, EvaluationHandler
from app.handlers.trading.execution import ExecutionEvent, ExecutionHandler
from app.handlers.trading.settlement import SettlementEvent, SettlementHandler
from app.outbox.contracts import OutboxEnvelope

# 生产侧事实通知 topic（与 create_envelope 调用点对齐）。
TOPIC_BLIND_COMMIT = "trading.blind_commit.v1"
TOPIC_CHAIN_SETTLEMENT_FINALIZED = "chain.settlement.finalized"
TOPIC_SHADOW_EXECUTION_TERMINALIZED = "shadow.execution.terminalized"
TOPIC_UNIVERSE_FRAME = "universe.frame"
TOPIC_UNIVERSE_REFRESH = "universe.refresh"
TOPIC_MARKET_BOOK = "market.book"
TOPIC_MARKET_CONFIG_REFRESH = "market.config.refresh"

# 消费端订阅的全部 outbox topic（未列出的 topic 不会被 consumer 订阅）。
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
    """把 outbox envelope 路由到 trading handler。

    默认对事实通知安全确认；仅当 ``payload["kind"]`` 显式命中 ``_KIND_ROUTES``
    且字段齐备时才调对应 handler。这样 outbox 不会因事实摘要 payload 与 handler
    输入结构不匹配而 dead-letter，业务推进留给 pipeline driver。
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
        # 显式 kind → (handler, event 构造器)。只在事实需要驱动 handler 时登记；
        # kind 由生产侧写入 payload["kind"]。缺省空表 = 全部安全确认。
        self._KIND_ROUTES: dict[str, tuple[Any, Any]] = {}

    # ---- OutboxHandler 协议 ----

    async def handle(
        self,
        envelope: OutboxEnvelope,
        uow: UnitOfWork,
        fencing_token: int,
    ) -> None:
        result = await self.dispatch(envelope, uow)
        if result is not True:
            raise RuntimeError("trading_dispatch_failed")

    async def dispatch(self, envelope: OutboxEnvelope, uow: UnitOfWork) -> bool:
        """路由 envelope；默认安全确认（True），显式 kind 命中才调 handler。"""
        payload = envelope.payload or {}
        kind = payload.get("kind")
        if kind is None:
            return True  # 事实通知：安全确认，不驱业务
        route = self._KIND_ROUTES.get(kind)
        if route is None:
            # 显式 kind 但未注册 → fail closed（不静默吞掉一个本该驱动的事件）。
            return False
        handler, event_factory = route
        event = event_factory(payload)
        return await self._call(handler, event, uow, envelope)

    def register_kind(self, kind: str, handler: Any, event_factory: Any) -> None:
        """注册一个显式 kind 的路由（pipeline/投影侧按需扩展）。"""
        self._KIND_ROUTES[kind] = (handler, event_factory)

    async def _call(self, handler: Any, event: Any, uow: UnitOfWork, env: OutboxEnvelope) -> bool:
        kwargs: dict[str, Any] = {}
        if isinstance(event, (CognitionEvent, DecisionEvent)):
            kwargs["version_manifest_id"] = env.release_manifest_id
            policy_hash = (env.payload or {}).get("policy_hash")
            if policy_hash is not None:
                kwargs["policy_hash"] = policy_hash
        result = await handler.handle(uow, event, **kwargs)
        return bool(result.ok)
