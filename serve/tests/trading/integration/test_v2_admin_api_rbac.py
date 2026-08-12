"""WP-07A Checkpoint B —— Admin Read API RBAC（真 PostgreSQL + ASGI transport）。

证明：401 未登录、缺权限 403、单域授权、跨域拒绝、超级管理员通过；
AI artifact 同时要求 v2:artifact:read + v2:ai:artifact，generic artifact hash 不得绕过。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.deps import AuthInfo


def _upgrade(url):
    cfg = Config(); cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "alembic"))
    eng = create_engine(url, poolclass=NullPool); conn = eng.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, "b1000070")
    finally:
        conn.close(); eng.dispose()


@pytest.fixture
async def env(temp_pg_db):
    """app + 真 session + 注入 AuthInfo/perms；httpx ASGITransport（单一 event loop）。"""
    _upgrade(temp_pg_db.url)
    from app.main import app
    from app.services.database import get_db
    from app.db.cursor import CursorCodec, derive_key
    from app.logics.trading.admin_read import AdminReadLogic
    from app.controllers.admin.trading.common import reset_admin_logic

    reset_admin_logic(AdminReadLogic(CursorCodec(derive_key("wp07a-test-key"))))

    async_db = temp_pg_db.url.replace("postgresql+psycopg:///", "postgresql+asyncpg:///")
    async_engine = create_async_engine(async_db, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)

    async def _override_db():
        async with sessions() as s:
            yield s

    app.dependency_overrides[get_db] = _override_db
    state = {"perms": set(), "is_super": False}

    from app.deps import require_auth

    def _auth():
        return AuthInfo(1, "admin", "u", "t", {"is_super_admin": state["is_super"]})

    app.dependency_overrides[require_auth] = _auth

    from app.logics.admin_user import admin_user_logic

    orig = admin_user_logic.get_user_perms

    async def fake(db, user_id, is_super=False):
        return list(state["perms"])

    admin_user_logic.get_user_perms = fake
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield {"client": client, "state": state, "app": app, "async_engine": async_engine}
    finally:
        admin_user_logic.get_user_perms = orig
        reset_admin_logic(None)
        app.dependency_overrides.clear()
        async_engine.sync_engine.dispose()


async def _get(env, path):
    resp = await env["client"].get(path)
    return resp.status_code, resp.json()


@pytest.mark.anyio
async def test_unauthenticated_401(env):
    from app.deps import require_auth

    env["app"].dependency_overrides.pop(require_auth, None)
    env["state"]["perms"] = set()
    _status, body = await _get(env, "/api/admin/v2/markets")
    assert body["code"] == 401


@pytest.mark.anyio
async def test_missing_permission_403(env):
    env["state"]["perms"] = set()
    _status, body = await _get(env, "/api/admin/v2/markets")
    assert body["code"] == 403


@pytest.mark.anyio
async def test_single_domain_grant_ok_and_cross_domain_reject(env):
    env["state"]["perms"] = {"v2:markets:view"}
    status, body = await _get(env, "/api/admin/v2/markets")
    assert status == 200
    assert body["code"] == 0
    _status2, body2 = await _get(env, "/api/admin/v2/episodes")
    assert body2["code"] == 403


@pytest.mark.anyio
async def test_super_admin_bypasses(env):
    env["state"]["perms"] = set()
    env["state"]["is_super"] = True
    for path in ("/api/admin/v2/dashboard", "/api/admin/v2/markets",
                 "/api/admin/v2/integrity/runtime"):
        status, body = await _get(env, path)
        assert status == 200, path
        assert body["code"] == 0, path


@pytest.mark.anyio
async def test_ai_artifact_double_permission(env):
    """generic artifact metadata 需 v2:artifact:read；AI artifact content 需 +v2:ai:artifact。"""
    from app.repositories.trading.admin_read import AdminReadRepository

    original = AdminReadRepository.is_ai_artifact

    async def fake_is_ai(self, session, content_hash):
        return True

    AdminReadRepository.is_ai_artifact = fake_is_ai
    try:
        # 只有 v2:artifact:read，无 v2:ai:artifact → content 403
        env["state"]["perms"] = {"v2:artifact:read"}
        _status, body = await _get(env, "/api/admin/v2/artifacts/" + "a" * 64 + "/content")
        assert body["code"] == 403

        # 双权限 → 非 403（无 artifact → 404；无 Range → 416）
        env["state"]["perms"] = {"v2:artifact:read", "v2:ai:artifact"}
        _status, body = await _get(env, "/api/admin/v2/artifacts/" + "a" * 64 + "/content")
        assert body.get("code") != 403
    finally:
        AdminReadRepository.is_ai_artifact = original


@pytest.mark.anyio
async def test_legacy_runtime_still_requires_admin_monitor(env):
    env["state"]["perms"] = {"admin:monitor:list"}
    _status, body = await _get(env, "/api/admin/trading/runtime")
    assert body["code"] == 0
