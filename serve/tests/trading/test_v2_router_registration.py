"""
WP-00d2 路由注册与接入验收测试。

覆盖：三个 public health 状态码、metrics on/off + content-type、Admin 401/403/200、
runtime 最近快照复用、legacy 关键路由仍注册、Web SEO catch-all 不遮蔽 health/metrics。
不要求真实 PostgreSQL/Redis/S3/OTLP 服务。
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.deps import AuthInfo, get_db, require_auth

# `app`/`client` 在 `_load_app` 模块级 fixture 中延迟初始化：不能在模块顶层 import
# app.main（那会在 collection 时执行，预先导入 legacy admin controller 的
# `from app.services.redis import get_redis` 绑定，破坏其后 test_session_cache 的
# conftest `mock_redis` patch）。
app = None
client = None


@pytest.fixture(scope="module", autouse=True)
def _load_app():
    """执行期才 import app.main 并建 TestClient；本文件先于/后于 legacy session 测试
    都不污染其 get_redis 绑定。"""
    global app, client
    from app.main import app as _app

    app = _app
    client = TestClient(app)
    yield


@pytest.fixture(scope="module", autouse=True)
def _restore_legacy_redis_bindings():
    """本模块结束后从 sys.modules 弹出受 `app.main` 导入影响的 legacy admin controller，
    使后续 legacy 测试在各自 mock_redis fixture patch 后重新导入并绑定 fake get_redis。"""
    yield
    import sys

    for name in (
        "app.controllers.admin.session",
        "app.controllers.admin.cache",
    ):
        sys.modules.pop(name, None)


@pytest.fixture
def clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _fake_db():
    yield object()


# ---------------- public health ----------------

def test_health_and_live_200():
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/live").json() == {"status": "ok"}


def test_health_ready_503_without_runtime():
    """lifespan 未运行 → runtime 未设 → 503 同结构 unready。"""
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unready"
    assert "components" in body
    assert "degraded" in body


# ---------------- metrics ----------------

def test_metrics_200_and_content_type():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")


def test_metrics_404_when_disabled(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.settings, "PROMETHEUS_ENABLED", False)
    assert client.get("/metrics").status_code == 404


# ---------------- Admin trading runtime ----------------

def test_trading_runtime_401_unauthenticated():
    """未登录 → BizError(401)（HTTP 200 包装，code=401）。"""
    resp = client.get("/api/admin/trading/runtime")
    assert resp.json()["code"] == 401


def test_trading_runtime_403_insufficient_perms(monkeypatch, clear_overrides):
    """非超管且无 admin:monitor:list → code=403。"""
    app.dependency_overrides[require_auth] = lambda: AuthInfo(
        1, "admin", "u", "t", {})
    app.dependency_overrides[get_db] = _fake_db

    from app.logics.admin_user import admin_user_logic

    async def no_perms(db, user_id):
        return []

    monkeypatch.setattr(admin_user_logic, "get_user_perms", no_perms)
    resp = client.get("/api/admin/trading/runtime")
    assert resp.json()["code"] == 403


def test_trading_runtime_200_super_admin(clear_overrides):
    """超管放行；无 runtime 时返回安全 unready 占位快照。"""
    app.dependency_overrides[require_auth] = lambda: AuthInfo(
        1, "admin", "root", "t", {"is_super_admin": True})
    app.dependency_overrides[get_db] = _fake_db
    resp = client.get("/api/admin/trading/runtime")
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "unready"
    assert "components" in body["data"]


def test_trading_runtime_reuses_recent_snapshot(clear_overrides):
    """复用 runtime 最近安全快照，不重做 health。"""
    fake_runtime = SimpleNamespace(last_snapshot={
        "status": "ready",
        "components": {"database": {"state": "ready"}},
        "degraded": [],
        "checked_at": "2026-08-09T00:00:00.000Z",
    })
    app.state.trading_runtime = fake_runtime
    app.dependency_overrides[require_auth] = lambda: AuthInfo(
        1, "admin", "root", "t", {"is_super_admin": True})
    app.dependency_overrides[get_db] = _fake_db
    resp = client.get("/api/admin/trading/runtime")
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["status"] == "ready"
    assert resp.json()["data"]["components"]["database"]["state"] == "ready"


# ---------------- legacy 与 SEO 不遮蔽 ----------------

def test_legacy_public_dict_route_still_registered(monkeypatch, clear_overrides):
    """legacy 公开路由 /api/dict/items 仍注册可达。"""
    app.dependency_overrides[get_db] = _fake_db
    from app.logics.dict import dict_logic

    async def empty_items(db, type_name):
        return []

    monkeypatch.setattr(dict_logic, "get_items_by_type", empty_items)
    resp = client.get("/api/dict/items", params={"type": "x"})
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_seo_catch_all_does_not_shadow_health_or_metrics():
    """Web SEO /{name} 兜底不遮蔽 /health /metrics。"""
    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200


# ---------------- R1：health/metrics 错误边界 + 固定 schema ----------------

def test_health_ready_runtime_absent_fixed_four_component_schema():
    """runtime 缺失 → 503 固定四组件 schema（database/control/artifact=unready、
    cache=degraded、degraded=["cache_redis"]、driver=配置值）。"""
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unready"
    assert set(body["components"].keys()) == {
        "database", "control_redis", "cache_redis", "artifact_store"}
    assert body["components"]["cache_redis"]["state"] == "degraded"
    assert body["components"]["artifact_store"]["driver"] in ("local", "s3")
    assert body["degraded"] == ["cache_redis"]
    assert body["checked_at"].endswith("Z")


def test_health_ready_health_snapshot_raises_503(monkeypatch, clear_overrides):
    """health 编排抛异常 → 503 固定 schema（非 200），body 无 marker。"""
    import app.main as main

    class BoomRuntime:
        async def health_snapshot(self):
            raise RuntimeError("TOPSECRET health boom")

    app.state.trading_runtime = BoomRuntime()
    try:
        resp = client.get("/health/ready")
        assert resp.status_code == 503
        text = resp.text
        assert "TOPSECRET" not in text
        assert resp.json()["status"] == "unready"
    finally:
        app.state.trading_runtime = None


def test_metrics_render_raises_503(monkeypatch):
    """metrics 渲染抛异常 → 固定纯文本 503（非 200），body 无 marker。"""
    import app.main as main

    def _boom():
        raise RuntimeError("TOPSECRET metrics boom")

    monkeypatch.setattr(main, "render_metrics", _boom)
    resp = client.get("/metrics")
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("text/plain")
    assert "TOPSECRET" not in resp.text


def test_trading_runtime_absent_fixed_schema(clear_overrides):
    """Admin trading/runtime 无 runtime → 固定四组件 unready schema。"""
    app.dependency_overrides[require_auth] = lambda: AuthInfo(
        1, "admin", "root", "t", {"is_super_admin": True})
    app.dependency_overrides[get_db] = _fake_db
    resp = client.get("/api/admin/trading/runtime")
    data = resp.json()["data"]
    assert data["status"] == "unready"
    assert set(data["components"].keys()) == {
        "database", "control_redis", "cache_redis", "artifact_store"}
    assert data["components"]["cache_redis"]["state"] == "degraded"
    assert data["degraded"] == ["cache_redis"]


def test_trading_runtime_closed_last_snapshot_unready(clear_overrides):
    """close 后 Admin 复用 last_snapshot=unready（非 ready）。"""
    class ClosedRuntime:
        last_snapshot = {
            "status": "unready",
            "components": {"database": {"state": "unready"}},
            "degraded": [],
            "checked_at": "2026-08-09T00:00:00.000Z",
        }

    app.state.trading_runtime = ClosedRuntime()
    app.dependency_overrides[require_auth] = lambda: AuthInfo(
        1, "admin", "root", "t", {"is_super_admin": True})
    app.dependency_overrides[get_db] = _fake_db
    try:
        resp = client.get("/api/admin/trading/runtime")
        assert resp.json()["data"]["status"] == "unready"
    finally:
        app.state.trading_runtime = None


def test_main_never_calls_basic_config():
    """main.py 不得调用 logging.basicConfig（非 V2 handler 绕过统一 redactor）。
    允许 docstring/注释提及，但不允许可执行调用。"""
    import inspect
    import app.main as main

    src = inspect.getsource(main)
    # 过滤注释行（含 docstring 说明）
    exec_lines = [ln for ln in src.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    assert not any("basicConfig(" in ln for ln in exec_lines)


def test_prewarm_failure_logs_no_raw_exception(monkeypatch):
    """prewarm 依赖失败含敏感 marker → 捕获日志 marker=0，只有 V2 handler 由本模块安装。"""
    import io
    import logging
    import sys

    import app.main as main

    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers[:] = []
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    try:
        # 构造 harness：runtime 正常，db/redis prewarm 抛含 marker 异常
        from types import SimpleNamespace

        class FakeRuntime:
            async def health_snapshot(self):
                return {"status": "ready"}

            def mark_started(self):
                pass

            async def close(self):
                return []

        async def _build(*a, **k):
            return FakeRuntime()

        async def _db_fail(*a, **k):
            yield None
            raise RuntimeError("TOPSECRET prewarm db")

        async def _redis_fail(*a, **k):
            raise RuntimeError("TOPSECRET prewarm redis")

        async def _close_redis():
            pass

        def _shutdown_tracing():
            pass

        def _noop(*a, **k):
            return None

        monkeypatch.setattr(main, "build_runtime_resources", _build)
        monkeypatch.setattr(main, "get_db", _db_fail)
        monkeypatch.setattr(main, "get_redis", _redis_fail)
        monkeypatch.setattr(main, "close_redis", _close_redis)
        monkeypatch.setattr(main, "shutdown_tracing", _shutdown_tracing)
        monkeypatch.setattr(main, "configure_tracing", _noop)
        monkeypatch.setattr(main, "configure_logging", _noop)

        fake_app = SimpleNamespace(state=SimpleNamespace())

        import asyncio

        async def _run():
            async with main.lifespan(fake_app):
                pass

        asyncio.run(_run())
        out = buf.getvalue()
        assert "TOPSECRET" not in out
        # prewarm 失败日志是固定 reason code（无 raw exception message/traceback）
        assert "startup prewarm db failed" in out or "startup prewarm" in out
    finally:
        root.handlers[:] = saved


def test_orjson_importable_from_requirements():
    """orjson 可复现：requirements 声明 + import 均证明，不在测试中 pip install。"""
    import orjson  # noqa: F401 - 依赖已由 requirements 声明

    req = (__import__("pathlib").Path(__file__).resolve().parents[2] / "requirements.txt").read_text()
    assert "orjson" in req
    assert "orjson>=3.10,<4" in req


def test_web_seo_and_trading_routers_registered():
    """trading router 与 web_seo router 均包含；health/metrics 在第一个 include 之前。"""
    from app.controllers.admin import trading as admin_trading
    from app.controllers.web import seo as web_seo
    from fastapi.routing import APIRoute

    router_paths = [r.path for r in admin_trading.router.routes]
    assert "/trading/runtime" in router_paths
    seo_paths = [r.path for r in web_seo.router.routes]
    assert "/{name}" in seo_paths

    # app.routes 中 health/metrics 作为独立 APIRoute 在第一个 _IncludedRouter 之前
    first_include = None
    health_idx: dict[str, int] = {}
    for i, r in enumerate(app.routes):
        if type(r).__name__ == "_IncludedRouter" and first_include is None:
            first_include = i
        if isinstance(r, APIRoute) and r.path in ("/health", "/metrics"):
            health_idx[r.path] = i
    assert first_include is not None
    assert health_idx["/health"] < first_include
    assert health_idx["/metrics"] < first_include
