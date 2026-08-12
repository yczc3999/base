"""WP-07C：outbox → trading handler 分发适配的单元测试（无 DB）。

验证修正后的语义：事实通知类 topic 默认安全确认；显式 kind 路由命中才调 handler；
未注册 kind fail closed。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.outbox.contracts import create_envelope

from runtimes.trading._dispatch import (
    ALL_TOPICS,
    TOPIC_BLIND_COMMIT,
    TOPIC_MARKET_BOOK,
    TOPIC_UNIVERSE_FRAME,
    TradingEventDispatch,
)


@dataclass
class _Result:
    ok: bool
    reason: str | None = None


class _RecordingHandler:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def handle(self, uow, event, **kwargs):
        self.calls.append((event, kwargs))
        return _Result(self.ok)


def _env(topic: str, payload: dict):
    return create_envelope(
        topic=topic,
        schema_version=1,
        aggregate_type="t",
        aggregate_id="a-1",
        idempotency_key=f"idem-{topic}-{payload.get('kind')}",
        payload=payload,
    )


def _dispatch(ok=True):
    handlers = {k: _RecordingHandler(ok) for k in
                ("cognition", "decision", "evaluation", "execution", "settlement")}
    return TradingEventDispatch(**handlers), handlers


def _run(coro):
    return asyncio.run(coro)


def test_fact_notifications_safely_acknowledged_without_handler():
    """全部事实通知 topic：无 kind → 安全确认，且不调任何 handler。"""
    dispatch, handlers = _dispatch()
    for topic in ALL_TOPICS:
        env = _env(topic, {"frame_id": 1, "episode_key": "e"})
        assert _run(dispatch.dispatch(env, uow=None)) is True, topic
    assert not any(h.calls for h in handlers.values()), "handler 不应被事实通知触发"


def test_registered_kind_invokes_handler():
    dispatch, handlers = _dispatch()
    dispatch.register_kind(
        "do_thing",
        handlers["decision"],
        lambda payload: ("evt", payload),  # 简单事件工厂
    )
    env = _env(TOPIC_BLIND_COMMIT, {"kind": "do_thing", "x": 1})
    assert _run(dispatch.dispatch(env, uow=None)) is True
    assert handlers["decision"].calls, "已注册 kind 应调用对应 handler"


def test_unregistered_kind_fail_closed():
    """显式 kind 但未注册路由 → fail closed（返回 False）。"""
    dispatch, _ = _dispatch()
    env = _env(TOPIC_MARKET_BOOK, {"kind": "never_registered"})
    assert _run(dispatch.dispatch(env, uow=None)) is False


def test_handler_not_ok_propagates_false():
    dispatch, handlers = _dispatch(ok=False)
    dispatch.register_kind("k", handlers["evaluation"], lambda p: ("e", p))
    env = _env(TOPIC_UNIVERSE_FRAME, {"kind": "k"})
    assert _run(dispatch.dispatch(env, uow=None)) is False


def test_handle_raises_on_failure_for_consumer():
    dispatch, _ = _dispatch()
    dispatch._KIND_ROUTES["bad"] = (_RecordingHandler(ok=False), lambda p: ("e", p))
    env = _env(TOPIC_BLIND_COMMIT, {"kind": "bad"})
    try:
        _run(dispatch.handle(env, uow=None, fencing_token=0))
    except RuntimeError as exc:
        assert "trading_dispatch_failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
