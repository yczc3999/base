"""
WP-00d2 RuntimeResources 生命周期与健康快照验收测试。

覆盖：全绿 ready；DB/Control/Artifact fail/timeout → unready；Cache fail/timeout →
ready+degraded；响应字段精确且无敏感 marker/路径/URL；初始/关闭不可 ready；并发快照不改
资源身份；关闭逆序尽力幂等；DB dispose 一次。
"""

import asyncio

import pytest

from app.config import Settings
from app.services.runtime import RuntimeResources, build_runtime_resources


class FakeRedis:
    """模拟 Control/Cache Redis client：health/aclose 计数。"""

    def __init__(self, ok: bool = True, hang: float = 0.0, close_raises: bool = False):
        self._ok = ok
        self._hang = hang
        self.close_raises = close_raises
        self.health_calls = 0
        self.close_calls = 0

    async def health(self) -> dict:
        self.health_calls += 1
        if self._hang:
            await asyncio.sleep(self._hang)
        return {"ok": self._ok}

    async def aclose(self):
        self.close_calls += 1
        if self.close_raises:
            raise RuntimeError("close boom")


class FakeArtifact:
    def __init__(self, ok: bool = True):
        self._ok = ok
        self.driver = "local"
        self.health_calls = 0
        self.close_calls = 0

    def health(self):
        self.health_calls += 1
        return type("H", (), {"ok": self._ok, "driver": self.driver})()

    def aclose(self):
        self.close_calls += 1


class FakeDBEngines:
    def __init__(self, dispose_raises: bool = False):
        self.dispose_calls = 0
        self._raises = dispose_raises

    async def dispose(self):
        self.dispose_calls += 1
        if self._raises:
            raise RuntimeError("dispose boom")


def _cfg(timeout: float = 2.0) -> Settings:
    return Settings(
        _env_file=None,
        ARTIFACT_DRIVER="local",
        ARTIFACT_LOCAL_ROOT="/tmp/v2-rt",
        ARTIFACT_INLINE_THRESHOLD_BYTES=1,
        ARTIFACT_COMPRESSION_THRESHOLD_BYTES=1,
        ARTIFACT_MAX_OBJECT_BYTES=67_108_864,
        RUNTIME_HEALTH_TIMEOUT_S=timeout,
    )


def _runtime(*, db_ok=True, db_hang=0.0, control_ok=True, cache_ok=True,
             control_hang=0.0, cache_hang=0.0, art_ok=True, timeout=2.0,
             dispose_raises=False):
    cfg = _cfg(timeout=timeout)
    control = FakeRedis(ok=control_ok, hang=control_hang)
    cache = FakeRedis(ok=cache_ok, hang=cache_hang)
    art = FakeArtifact(ok=art_ok)
    engines = FakeDBEngines(dispose_raises=dispose_raises)

    async def _db_probe():
        if db_hang:
            await asyncio.sleep(db_hang)
        if not db_ok:
            raise RuntimeError("db down")

    rt = RuntimeResources(
        cfg, db_probe=_db_probe, control=control, cache=cache,
        artifact=art, db_engines=engines,
    )
    return rt, (control, cache, art, engines)


def test_all_green_ready():
    rt, (control, cache, art, engines) = _runtime()
    rt.mark_started()
    snap = asyncio.run(rt.health_snapshot())
    assert snap["status"] == "ready"
    assert snap["components"] == {
        "database": {"state": "ready"},
        "control_redis": {"state": "ready"},
        "cache_redis": {"state": "ready"},
        "artifact_store": {"state": "ready", "driver": "local"},
    }
    assert snap["degraded"] == []
    assert "T" in snap["checked_at"] and snap["checked_at"].endswith("Z")


def test_not_started_unready():
    rt, _ = _runtime()
    snap = asyncio.run(rt.health_snapshot())
    assert snap["status"] == "unready"  # 未 mark_started


@pytest.mark.parametrize("kw,comp", [
    ({"db_ok": False}, "database"),
    ({"control_ok": False}, "control_redis"),
    ({"art_ok": False}, "artifact_store"),
])
def test_required_component_fail_unready(kw, comp):
    rt, _ = _runtime(**kw)
    rt.mark_started()
    snap = asyncio.run(rt.health_snapshot())
    assert snap["status"] == "unready"
    assert snap["components"][comp]["state"] == "unready"


def test_db_timeout_marks_unready():
    rt, _ = _runtime(db_hang=0.5, timeout=0.05)
    rt.mark_started()
    snap = asyncio.run(rt.health_snapshot())
    assert snap["status"] == "unready"
    assert snap["components"]["database"]["state"] == "unready"


def test_control_timeout_marks_unready():
    rt, _ = _runtime(control_hang=0.5, timeout=0.05)
    rt.mark_started()
    snap = asyncio.run(rt.health_snapshot())
    assert snap["status"] == "unready"
    assert snap["components"]["control_redis"]["state"] == "unready"


def test_cache_fail_ready_degraded():
    rt, _ = _runtime(cache_ok=False)
    rt.mark_started()
    snap = asyncio.run(rt.health_snapshot())
    assert snap["status"] == "ready"            # cache 不使 readiness 失败
    assert snap["components"]["cache_redis"]["state"] == "degraded"
    assert snap["degraded"] == ["cache_redis"]


def test_cache_timeout_ready_degraded():
    rt, _ = _runtime(cache_hang=0.5, timeout=0.05)
    rt.mark_started()
    snap = asyncio.run(rt.health_snapshot())
    assert snap["status"] == "ready"
    assert snap["components"]["cache_redis"]["state"] == "degraded"
    assert snap["degraded"] == ["cache_redis"]


def test_no_sensitive_leak_in_snapshot():
    """未知异常映射到固定状态，不泄异常消息/路径/URL/凭据。"""
    class LeakyControl:
        async def health(self):
            raise RuntimeError("TOPSECRET at /var/secret/db?user=admin&pw=abc123")
        async def aclose(self):
            pass

    cfg = _cfg()
    rt = RuntimeResources(
        cfg, db_probe=lambda: (_ for _ in ()).throw(AssertionError("no db")),
        control=LeakyControl(), cache=FakeRedis(), artifact=FakeArtifact(),
        db_engines=FakeDBEngines(),
    )
    rt.mark_started()
    snap = asyncio.run(rt.health_snapshot())
    text = repr(snap)
    assert snap["status"] == "unready"
    assert "TOPSECRET" not in text
    assert "/var/secret" not in text
    assert "admin" not in text
    assert "pw=" not in text
    assert snap["components"]["control_redis"]["state"] == "unready"


def test_close_idempotent_and_db_dispose_once():
    rt, (control, cache, art, engines) = _runtime()
    asyncio.run(rt.close())
    asyncio.run(rt.close())                     # 重复幂等
    assert art.close_calls == 1
    assert cache.close_calls == 1
    assert control.close_calls == 1
    assert engines.dispose_calls == 1


def test_close_continues_on_failure():
    """某一 close 失败仍继续：control close 抛错，cache/artifact/db 仍关闭。"""
    rt, (control, cache, art, engines) = _runtime()
    control.close_raises = True
    asyncio.run(rt.close())
    assert cache.close_calls == 1
    assert art.close_calls == 1
    assert engines.dispose_calls == 1


def test_after_close_unready():
    rt, _ = _runtime()
    rt.mark_started()
    asyncio.run(rt.close())
    snap = asyncio.run(rt.health_snapshot())
    assert snap["status"] == "unready"


def test_concurrent_snapshots_do_not_mutate_identity():
    """并发 health 不改资源身份、不重复构造；每资源 health 恰好调用一次。"""
    rt, (control, cache, art, _) = _runtime()
    rt.mark_started()

    async def _concurrent():
        return await asyncio.gather(*(rt.health_snapshot() for _ in range(5)))

    snaps = asyncio.run(_concurrent())
    for s in snaps:
        assert s["status"] == "ready"
    # 每次快照每组件探测一次 → 5 次快照各 5 次 health
    assert control.health_calls == 5
    assert cache.health_calls == 5
    assert art.health_calls == 5


def test_build_runtime_resources_constructs_real_clients():
    """缺省依赖时构造真实 client（构造零网络；用 local artifact + 注入 DB engines 规避 DB）。"""
    from app.services.database import DatabaseEngines

    cfg = _cfg()
    fake_engines = DatabaseEngines(cfg)  # 惰性 engine，不连接
    rt = asyncio.run(build_runtime_resources(cfg, db_engines=fake_engines))
    assert isinstance(rt, RuntimeResources)
    assert rt._artifact.health().driver == "local"
    # 未 started / 关闭前不可 ready（避免触发 DB 探测）
    asyncio.run(rt.close())


# ---------------- R1：lifespan finally 清理 ----------------

class _LifespanHarness:
    """monkeypatch main.lifespan 的依赖，记录调用次数。"""

    def __init__(self, monkeypatch, *, runtime=None, build_raises=None):
        import app.main as main
        from types import SimpleNamespace

        self.main = main
        self.calls = {"runtime_close": 0, "close_redis": 0, "shutdown_tracing": 0}
        self.close_order: list[str] = []
        self.app = SimpleNamespace(state=SimpleNamespace())
        harness = self

        class FakeRuntime:
            def __init__(self):
                self.close_calls = 0

            def mark_started(self):
                pass

            async def health_snapshot(self):
                return {"status": "ready"}

            async def close(self):
                self.close_calls += 1
                harness.close_order.append("runtime")
                return []

        if runtime is None:
            runtime = FakeRuntime()

        async def _build(*a, **k):
            if build_raises is not None:
                raise build_raises
            return runtime

        async def _close_redis():
            self.calls["close_redis"] += 1
            self.close_order.append("legacy_redis")

        def _shutdown_tracing():
            self.calls["shutdown_tracing"] += 1
            self.close_order.append("tracing")

        def _noop_sync(*a, **k):
            """configure_tracing/configure_logging 是同步调用（main 不带 await）。"""
            return None

        async def _noop_db(*a, **k):
            """get_db 是 async generator：yield 一个假 session 即可。"""
            yield None

        async def _noop_redis(*a, **k):
            class _R:
                async def ping(self):
                    return True

            return _R()

        monkeypatch.setattr(main, "build_runtime_resources", _build)
        monkeypatch.setattr(main, "close_redis", _close_redis)
        monkeypatch.setattr(main, "shutdown_tracing", _shutdown_tracing)
        monkeypatch.setattr(main, "configure_tracing", _noop_sync)
        monkeypatch.setattr(main, "configure_logging", _noop_sync)
        monkeypatch.setattr(main, "get_db", _noop_db)
        monkeypatch.setattr(main, "get_redis", _noop_redis)
        self.runtime = runtime

    def close_count(self):
        return self.runtime.close_calls


def test_lifespan_normal_exit_cleans_all(monkeypatch):
    import asyncio

    h = _LifespanHarness(monkeypatch)
    async def _run():
        async with h.main.lifespan(h.app):
            pass
        assert h.runtime.close_calls == 1
        assert h.calls["close_redis"] == 1
        assert h.calls["shutdown_tracing"] == 1
        assert h.close_order == ["runtime", "legacy_redis", "tracing"]
        assert getattr(h.app.state, "trading_runtime", None) is None
    asyncio.run(_run())


def test_lifespan_body_raises_still_cleans(monkeypatch):
    import asyncio

    h = _LifespanHarness(monkeypatch)
    async def _run():
        with pytest.raises(RuntimeError):
            async with h.main.lifespan(h.app):
                raise RuntimeError("body boom")
        assert h.runtime.close_calls == 1
        assert h.calls["close_redis"] == 1
        assert h.calls["shutdown_tracing"] == 1
        assert h.close_order == ["runtime", "legacy_redis", "tracing"]
    asyncio.run(_run())


def test_lifespan_body_cancellation_still_cleans(monkeypatch):
    import asyncio

    h = _LifespanHarness(monkeypatch)
    async def _run():
        with pytest.raises(asyncio.CancelledError):
            async with h.main.lifespan(h.app):
                raise asyncio.CancelledError()
        assert h.runtime.close_calls == 1
        assert h.calls["close_redis"] == 1
        assert h.calls["shutdown_tracing"] == 1
        assert h.close_order == ["runtime", "legacy_redis", "tracing"]
    asyncio.run(_run())


def test_lifespan_build_failure_propagates_and_shuts_tracing(monkeypatch):
    """runtime factory 抛异常：异常传播（阻止 startup），tracing 仍释放。"""
    import asyncio

    h = _LifespanHarness(monkeypatch, build_raises=RuntimeError("construct boom"))
    async def _run():
        with pytest.raises(RuntimeError):
            async with h.main.lifespan(h.app):
                pass
        assert h.calls["close_redis"] == 1
        assert h.calls["shutdown_tracing"] == 1
        assert h.runtime.close_calls == 0  # runtime 未构造
        assert h.close_order == ["legacy_redis", "tracing"]
        assert getattr(h.app.state, "trading_runtime", None) is None
    asyncio.run(_run())


def test_lifespan_tracing_init_failure_still_cleans(monkeypatch):
    """Tracing 初始化部分成功后报错也必须进 finally，清理全局资源与旧指针。"""
    h = _LifespanHarness(monkeypatch)
    h.app.state.trading_runtime = "STALE"

    def _tracing_boom(*a, **k):
        raise RuntimeError("TOPSECRET tracing init")

    monkeypatch.setattr(h.main, "configure_tracing", _tracing_boom)

    async def _run():
        with pytest.raises(RuntimeError, match="tracing init"):
            async with h.main.lifespan(h.app):
                pass
        assert h.runtime.close_calls == 0
        assert h.calls["close_redis"] == 1
        assert h.calls["shutdown_tracing"] == 1
        assert h.close_order == ["legacy_redis", "tracing"]
        assert h.app.state.trading_runtime is None

    asyncio.run(_run())


def test_lifespan_tracing_shutdown_failure_does_not_mask_body(monkeypatch):
    """Tracing shutdown 失败不得覆盖 body 异常，也不得留下 runtime 指针。"""
    h = _LifespanHarness(monkeypatch)

    def _shutdown_boom():
        h.calls["shutdown_tracing"] += 1
        h.close_order.append("tracing")
        raise RuntimeError("TOPSECRET tracing shutdown")

    monkeypatch.setattr(h.main, "shutdown_tracing", _shutdown_boom)

    async def _run():
        with pytest.raises(ValueError, match="body failure"):
            async with h.main.lifespan(h.app):
                raise ValueError("body failure")
        assert h.close_order == ["runtime", "legacy_redis", "tracing"]
        assert h.app.state.trading_runtime is None

    asyncio.run(_run())


def test_lifespan_cleanup_continues_on_close_failure(monkeypatch):
    """runtime.close 抛错 → close_redis/shutdown_tracing 仍执行。"""
    import asyncio

    class BoomRuntime:
        async def close(self):
            raise RuntimeError("TOPSECRET close boom")

        def mark_started(self):
            pass

        async def health_snapshot(self):
            return {"status": "ready"}

    h = _LifespanHarness(monkeypatch, runtime=BoomRuntime())
    async def _run():
        async with h.main.lifespan(h.app):
            pass
        assert h.calls["close_redis"] == 1
        assert h.calls["shutdown_tracing"] == 1
    asyncio.run(_run())


# ---------------- R1：builder 失败注入 ----------------

def test_builder_artifact_failure_closes_created(monkeypatch):
    """Artifact 构造失败 → 本次创建的 Cache/Control 逆序关闭、注入对象不误关、原异常传播。"""
    import app.services.runtime as R

    created_close: list[str] = []

    class TrackRedis:
        def __init__(self, name):
            self.name = name

        async def aclose(self):
            created_close.append(self.name)

    monkeypatch.setattr(R, "ControlRedisClient", lambda ep: TrackRedis("control"))
    monkeypatch.setattr(R, "CacheRedisClient", lambda ep: TrackRedis("cache"))

    def _boom(cfg):
        raise RuntimeError("TOPSECRET artifact boom")

    monkeypatch.setattr(R, "build_artifact_store", _boom)
    cfg = _cfg()
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(R.build_runtime_resources(cfg))
    # 原异常继续传播（构造失败是编程/配置错误，阻止 startup）；marker 泄漏禁止点是日志/HTTP
    assert "artifact boom" in str(ei.value)
    assert created_close == ["cache", "control"]  # 逆序关闭自建资源
    # 注入对象不被误关：注入 control 后 artifact 失败 → control 不在 created，不 close
    created_close.clear()
    injected = TrackRedis("injected-control")
    with pytest.raises(RuntimeError):
        asyncio.run(R.build_runtime_resources(cfg, control=injected))
    assert "injected-control" not in created_close  # 注入对象不误关


def test_builder_cache_failure_closes_created(monkeypatch):
    """Cache 构造失败 → 本次创建的 Control 已关闭；注入对象不误关。"""
    import app.services.runtime as R

    created_close: list[str] = []

    class TrackRedis:
        def __init__(self, name):
            self.name = name

        async def aclose(self):
            created_close.append(self.name)

    monkeypatch.setattr(R, "ControlRedisClient", lambda ep: TrackRedis("control"))

    def _boom_cache(ep):
        raise RuntimeError("cache construct boom")

    monkeypatch.setattr(R, "CacheRedisClient", _boom_cache)
    cfg = _cfg()
    with pytest.raises(RuntimeError):
        asyncio.run(R.build_runtime_resources(cfg))
    assert created_close == ["control"]  # 先建的控制被逆序关闭


def test_builder_control_failure_no_created_to_close(monkeypatch):
    """Control 构造失败（首个）→ 无 created 可关，异常传播。"""
    import app.services.runtime as R

    def _boom(ep):
        raise RuntimeError("control construct boom")

    monkeypatch.setattr(R, "ControlRedisClient", _boom)
    cfg = _cfg()
    with pytest.raises(RuntimeError):
        asyncio.run(R.build_runtime_resources(cfg))


# ---------------- R1：状态与固定 schema ----------------

def test_close_replaces_last_snapshot_unready():
    """close 前 ready → close 后 last_snapshot 立即 unready；随后 health 不再访问依赖。"""
    rt, (control, cache, art, _) = _runtime()
    rt.mark_started()
    asyncio.run(rt.health_snapshot())
    assert rt.last_snapshot["status"] == "ready"
    asyncio.run(rt.close())
    assert rt.last_snapshot["status"] == "unready"
    assert rt.last_snapshot["components"]["artifact_store"]["driver"] == "local"
    # 关闭后 health 不再访问依赖（调用计数不增加）
    before = (control.health_calls, cache.health_calls, art.health_calls)
    asyncio.run(rt.health_snapshot())
    assert (control.health_calls, cache.health_calls, art.health_calls) == before
    assert rt.last_snapshot["status"] == "unready"


def test_inflight_health_cannot_restore_ready_after_close():
    """close 与在途 probe 竞态时，已关闭 runtime 不得被晚到 health 结果重写 ready。"""
    async def _run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def _db_probe():
            entered.set()
            await release.wait()

        cfg = _cfg()
        rt = RuntimeResources(
            cfg,
            db_probe=_db_probe,
            control=FakeRedis(),
            cache=FakeRedis(),
            artifact=FakeArtifact(),
            db_engines=None,
        )
        rt.mark_started()
        probe = asyncio.create_task(rt.health_snapshot())
        await entered.wait()
        await rt.close()
        assert rt.last_snapshot["status"] == "unready"
        release.set()
        assert (await probe)["status"] == "unready"
        assert rt.last_snapshot["status"] == "unready"

    asyncio.run(_run())


def test_health_snapshot_not_started_no_dependency_access():
    """not-started 的 health_snapshot 不访问依赖。"""
    rt, (control, cache, art, _) = _runtime()
    snap = asyncio.run(rt.health_snapshot())
    assert snap["status"] == "unready"
    assert control.health_calls == 0 and cache.health_calls == 0 and art.health_calls == 0
    assert snap["components"]["artifact_store"]["driver"] == "local"


def test_artifact_fail_driver_is_config_value():
    """artifact 失败 → driver 仍为配置值（不输出 unknown）。"""
    rt, _ = _runtime(art_ok=False)
    rt.mark_started()
    snap = asyncio.run(rt.health_snapshot())
    assert snap["status"] == "unready"
    assert snap["components"]["artifact_store"]["driver"] == "local"


@pytest.mark.parametrize(
    "target,expected_status,expected_component_state",
    [
        ("database", "unready", "unready"),
        ("control_redis", "unready", "unready"),
        ("cache_redis", "ready", "degraded"),
        ("artifact_store", "unready", "unready"),
    ],
)
def test_malformed_probe_result_fails_closed(
    target, expected_status, expected_component_state
):
    """Truthy 但非合同类型的 probe 结果不得被当作健康。"""
    class MalformedRedis(FakeRedis):
        async def health(self):
            self.health_calls += 1
            return {"ok": "non-bool"}

    class MalformedArtifact(FakeArtifact):
        def health(self):
            self.health_calls += 1
            return type("H", (), {"ok": "non-bool"})()

    async def _db_probe():
        return {"ok": "non-contract"} if target == "database" else None

    rt = RuntimeResources(
        _cfg(),
        db_probe=_db_probe,
        control=MalformedRedis() if target == "control_redis" else FakeRedis(),
        cache=MalformedRedis() if target == "cache_redis" else FakeRedis(),
        artifact=(
            MalformedArtifact() if target == "artifact_store" else FakeArtifact()
        ),
        db_engines=None,
    )
    rt.mark_started()
    snap = asyncio.run(rt.health_snapshot())
    assert snap["status"] == expected_status
    assert snap["components"][target]["state"] == expected_component_state


def test_safe_unready_snapshot_four_component_schema():
    from app.services.runtime import safe_unready_snapshot

    snap = safe_unready_snapshot("s3")
    assert snap == {
        "status": "unready",
        "components": {
            "database": {"state": "unready"},
            "control_redis": {"state": "unready"},
            "cache_redis": {"state": "degraded"},
            "artifact_store": {"state": "unready", "driver": "s3"},
        },
        "degraded": ["cache_redis"],
        "checked_at": snap["checked_at"],
    }
    assert "T" in snap["checked_at"] and snap["checked_at"].endswith("Z")
    # 非法 driver 回落 local
    assert safe_unready_snapshot("nope")["components"]["artifact_store"]["driver"] == "local"


def test_close_returns_failed_components():
    """close 返回固定失败 component 集合；重复 close 幂等返回同集合。"""
    rt, (control, cache, art, _) = _runtime()
    control.close_raises = True
    failed = asyncio.run(rt.close())
    assert "control_redis" in failed
    assert failed == asyncio.run(rt.close())  # 重复幂等
