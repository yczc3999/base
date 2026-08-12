"""WP-07C：RuntimeSupervisor 注册 / 首失败 fence / 优雅关闭（无 DB、无网络）。"""

from __future__ import annotations

import asyncio

from runtimes.trading.supervisor import RuntimeSpec, RuntimeSupervisor, SupervisorContext


def _ctx():
    return SupervisorContext(
        session_factory_for=lambda p: None,
        control_redis=None,
        cache_redis=None,
        artifacts=None,
        gateway=None,
    )


def _run(coro):
    return asyncio.run(coro)


def _ok_runner(events, name, stop_after=None):
    async def run(stop_event):
        events.append(("start", name))
        # 常驻循环：直到 stop_event
        while not stop_event.is_set():
            await asyncio.sleep(0.005)
        events.append(("stop", name))
    return run


def _failing_runner(events, name):
    async def run(stop_event):
        events.append(("start", name))
        await asyncio.sleep(0.005)
        raise RuntimeError(f"boom-{name}")
    return run


def test_duplicate_register_rejected():
    sup = RuntimeSupervisor(_ctx())
    spec = RuntimeSpec("a", "outbox", lambda c: _ok_runner([], "a"))
    sup.register(spec)
    try:
        sup.register(RuntimeSpec("a", "outbox", lambda c: _ok_runner([], "a")))
    except ValueError as exc:
        assert "duplicate_runtime" in str(exc)
    else:
        raise AssertionError("expected duplicate rejection")


def test_registry_snapshot_lists_pool_profiles():
    sup = RuntimeSupervisor(_ctx())
    sup.register(RuntimeSpec("outbox-publisher", "outbox", lambda c: _ok_runner([], "x")))
    sup.register(RuntimeSpec("outbox-consumer", "outbox", lambda c: _ok_runner([], "y")))
    snap = sup.registry_snapshot()
    assert [r["name"] for r in snap] == ["outbox-publisher", "outbox-consumer"]
    assert all(r["pool_profile"] == "outbox" for r in snap)


def test_first_failure_fences_group_and_stops_all():
    events = []
    sup = RuntimeSupervisor(_ctx())
    sup.register(RuntimeSpec("ok", "outbox", lambda c: _ok_runner(events, "ok")))
    sup.register(RuntimeSpec("bad", "outbox", lambda c: _failing_runner(events, "bad")))
    failures = _run(sup.run())
    # bad 失败 → fence；ok 应被停掉
    assert ("start", "bad") in events
    assert any(e == ("stop", "ok") for e in events), events


def test_graceful_shutdown_via_stop_event():
    events = []
    sup = RuntimeSupervisor(_ctx())
    sup.register(RuntimeSpec("a", "outbox", lambda c: _ok_runner(events, "a")))
    sup.register(RuntimeSpec("b", "outbox", lambda c: _ok_runner(events, "b")))

    async def main():
        task = asyncio.create_task(sup.run())
        await asyncio.sleep(0.02)
        sup.stop_event.set()
        failures = await task
        return failures

    failures = _run(main())
    assert failures == 0
    assert ("stop", "a") in events and ("stop", "b") in events
