"""WP-07C：outbox → trading handler 分发适配的单元测试（无 DB）。

验证 :class:`runtimes.trading._dispatch.TradingEventDispatch` 的路由、
事件重建与 fail-closed 边界；handler 用 fake 替身，不触 DB。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.outbox.contracts import create_envelope

from runtimes.trading._dispatch import (
    ALL_TOPICS,
    TOPIC_BLIND_COMMIT,
    TOPIC_CHAIN_SETTLEMENT_FINALIZED,
    TOPIC_MARKET_BOOK,
    TOPIC_MARKET_CONFIG_REFRESH,
    TOPIC_SHADOW_EXECUTION_TERMINALIZED,
    TOPIC_UNIVERSE_FRAME,
    TOPIC_UNIVERSE_REFRESH,
    TradingEventDispatch,
)


@dataclass
class _Result:
    ok: bool
    reason: str | None = None


class _RecordingHandler:
    """记录被调用的 (event.kind, kwargs)，按预设返回 ok。"""

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def handle(self, uow, event, **kwargs):
        self.calls.append((getattr(event, "kind", None), kwargs))
        return _Result(self.ok)


def _env(topic: str, payload: dict):
    return create_envelope(
        topic=topic,
        schema_version=1,
        aggregate_type="t",
        aggregate_id="a-1",
        idempotency_key=f"idem-{topic}",
        payload=payload,
    )


def _dispatch(ok=True):
    handlers = {k: _RecordingHandler(ok) for k in
                ("cognition", "decision", "evaluation", "execution", "settlement")}
    return TradingEventDispatch(**handlers), handlers


def _run(coro):
    return asyncio.run(coro)


def test_all_topics_cover_routing():
    """每个订阅 topic 都必须能路由到一个 handler（不丢消息）。"""
    dispatch, handlers = _dispatch()
    for topic in ALL_TOPICS:
        env = _env(topic, {"kind": "x", "episode_id": 1})
        assert _run(dispatch.dispatch(env, uow=None)) is True, topic
        assert any(h.calls for h in handlers.values()), topic


def test_blind_commit_routes_to_decision_create():
    dispatch, handlers = _dispatch()
    env = _env(TOPIC_BLIND_COMMIT, {"episode_id": 7})
    assert _run(dispatch.dispatch(env, uow=None)) is True
    kind, kwargs = handlers["decision"].calls[0]
    assert kind == "create"
    assert kwargs.get("version_manifest_id") is None  # release_manifest_id 缺省 None


def test_market_book_routes_to_decision_market_relative():
    dispatch, handlers = _dispatch()
    env = _env(TOPIC_MARKET_BOOK, {"asset_id": "t1"})
    assert _run(dispatch.dispatch(env, uow=None)) is True
    assert handlers["decision"].calls[0][0] == "market_relative"


def test_universe_frame_routes_to_evaluation():
    dispatch, handlers = _dispatch()
    for topic in (TOPIC_UNIVERSE_FRAME, TOPIC_UNIVERSE_REFRESH):
        env = _env(topic, {"frame_id": 1})
        assert _run(dispatch.dispatch(env, uow=None)) is True
    assert handlers["evaluation"].calls[0][0] == "score_observation"


def test_chain_settlement_routes_to_settlement():
    dispatch, handlers = _dispatch()
    env = _env(TOPIC_CHAIN_SETTLEMENT_FINALIZED, {"operation_key": "k"})
    assert _run(dispatch.dispatch(env, uow=None)) is True
    assert handlers["settlement"].calls[0][0] == "label_revision"


def test_shadow_execution_routes_to_execution():
    dispatch, handlers = _dispatch()
    env = _env(TOPIC_SHADOW_EXECUTION_TERMINALIZED, {"execution_id": 3})
    assert _run(dispatch.dispatch(env, uow=None)) is True
    assert handlers["execution"].calls[0][0] == "shadow_fill"


def test_market_config_refresh_routes_to_decision():
    dispatch, handlers = _dispatch()
    env = _env(TOPIC_MARKET_CONFIG_REFRESH, {"asset_id": "a"})
    assert _run(dispatch.dispatch(env, uow=None)) is True
    assert handlers["decision"].calls[0][0] == "market_relative"


def test_unknown_topic_fail_closed():
    dispatch, _ = _dispatch()
    env = _env("unknown.topic.v9", {"kind": "x"})
    assert _run(dispatch.dispatch(env, uow=None)) is False


def test_handler_not_ok_propagates_false():
    dispatch, _ = _dispatch(ok=False)
    env = _env(TOPIC_BLIND_COMMIT, {"episode_id": 1})
    assert _run(dispatch.dispatch(env, uow=None)) is False


def test_handle_raises_on_not_ok_for_consumer():
    """OutboxHandler.handle：dispatch 失败必须 raise（让 consumer 记 retry/dead）。"""
    dispatch, _ = _dispatch(ok=False)
    env = _env(TOPIC_BLIND_COMMIT, {"episode_id": 1})
    try:
        _run(dispatch.handle(env, uow=None, fencing_token=0))
    except RuntimeError as exc:
        assert "trading_dispatch_failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
