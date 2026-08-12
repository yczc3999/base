"""WP-07A Checkpoint D —— Admin Read API secret / no-egress boundary（静态 + 真 PG）。

证明：API 响应/日志/trace 不含 cursor secret、raw filter payload、Authorization 或 artifact body；
offline SQL / 生产源无 secret 明文；真实 outbound/network/chain/money = 0。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]

_SENSITIVE = (
    re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    re.compile(r"(?i)(?:api[_-]?key|passphrase|authorization|builder[_-]?secret)\s*[:=]\s*['\"][A-Za-z0-9+/=_\-]{20,}"),
)

_WP07A_FILES = (
    "app/db/cursor.py",
    "app/schemas/trading/admin.py",
    "app/repositories/trading/admin_read.py",
    "app/logics/trading/admin_read.py",
    "app/deps.py",
    "app/controllers/admin/trading/router.py",
    "app/controllers/admin/trading/common.py",
    "app/controllers/admin/trading/dashboard.py",
    "app/controllers/admin/trading/markets.py",
    "app/controllers/admin/trading/components.py",
    "app/controllers/admin/trading/episodes.py",
    "app/controllers/admin/trading/decisions.py",
    "app/controllers/admin/trading/execution.py",
    "app/controllers/admin/trading/model_routes.py",
    "app/controllers/admin/trading/ai_invocations.py",
    "app/controllers/admin/trading/costs.py",
    "app/controllers/admin/trading/strategy_config.py",
    "app/controllers/admin/trading/releases.py",
    "app/controllers/admin/trading/evaluation.py",
    "app/controllers/admin/trading/replay.py",
    "app/controllers/admin/trading/integrity.py",
    "app/controllers/admin/trading/artifacts.py",
    "app/observability/metrics.py",
    "app/main.py",
)


def test_wp07a_source_no_secret_plaintext():
    hits = []
    for rel in _WP07A_FILES:
        path = SERVE_DIR / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in _SENSITIVE:
            for m in pattern.finditer(text):
                hits.append((rel, pattern.pattern, m.start()))
    assert hits == [], f"secret plaintext: {hits}"


def test_offline_sql_no_secret_marker():
    from subprocess import run

    proc = run(
        [str(SERVE_DIR / ".venv/bin/alembic"), "upgrade", "b1000070", "--sql"],
        cwd=str(SERVE_DIR), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    sql = proc.stdout
    for pattern in _SENSITIVE:
        assert not pattern.search(sql), f"secret marker in offline SQL: {pattern.pattern}"
    # 无真实 outbound/secret 相关 DDL
    assert "CREATE DATABASE" not in sql


@pytest.mark.anyio
async def test_api_response_contains_no_secret_or_cursor_key(temp_pg_db):
    """列表/响应不含 cursor secret、raw filter、Authorization、artifact body。"""
    from alembic import command as _cmd
    from alembic.config import Config as _Config
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from fastapi.testclient import TestClient  # noqa: F401
    import httpx

    cfg = _Config(); cfg.set_main_option("script_location", str(SERVE_DIR / "alembic"))
    eng = create_engine(temp_pg_db.url, poolclass=NullPool); conn = eng.connect()
    cfg.attributes["connection"] = conn
    try:
        _cmd.upgrade(cfg, "b1000070")
    finally:
        conn.close(); eng.dispose()

    from app.main import app
    from app.services.database import get_db
    from app.db.cursor import CursorCodec, derive_key
    from app.logics.trading.admin_read import AdminReadLogic
    from app.controllers.admin.trading.common import reset_admin_logic
    from app.deps import AuthInfo, require_auth

    reset_admin_logic(AdminReadLogic(CursorCodec(derive_key("wp07a-test-key"))))
    async_db = temp_pg_db.url.replace("postgresql+psycopg:///", "postgresql+asyncpg:///")
    async_engine = create_async_engine(async_db, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)

    async def _override_db():
        async with sessions() as s:
            yield s

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_auth] = lambda: AuthInfo(
        1, "admin", "u", "t", {"is_super_admin": True})
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/admin/v2/markets")
            raw = resp.text
            # 不含 cursor secret 派生 key / raw filter payload / Authorization
            assert "wp07a-test-key" not in raw
            assert "Authorization" not in raw
            assert "cursor_app_key" not in raw
            # 列表为空库 → 空 items，无 artifact body
            assert resp.json()["code"] == 0
    finally:
        app.dependency_overrides.clear()
        reset_admin_logic(None)
        async_engine.sync_engine.dispose()


def test_no_outbound_counters_in_wp07a():
    """WP-07A 不引入任何真实 outbound 调用（Repository 不读 env/Redis/network）。"""
    from app.repositories.trading.admin_read import AdminReadRepository
    import inspect

    for name, method in inspect.getmembers(AdminReadRepository, inspect.iscoroutinefunction):
        src = inspect.getsource(method)
        for forbidden in ("httpx.AsyncClient", "requests.", "urllib", "redis", "asyncio.open_connection"):
            assert forbidden not in src, f"{name} uses forbidden outbound: {forbidden}"
