"""WP-07A Checkpoint B —— artifact Range（真 PostgreSQL + local driver + ASGI）。

证明：metadata 与 content 分离；content 单段 Range → 206/Content-Range/ETag；
无 Range / 多 Range / 越界 / 超 1 MiB → 416 fail closed；无路径/凭证泄露。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.deps import AuthInfo

MAX_BYTES = 1024 * 1024


def _upgrade(url):
    cfg = Config(); cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "alembic"))
    eng = create_engine(url, poolclass=NullPool); conn = eng.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, "b1000070")
    finally:
        conn.close(); eng.dispose()


def _put_artifact(url, data: bytes, mime: str = "application/json"):
    """用 ArtifactStore（local driver）写入一个 artifact，落 artifact_objects 行。

    复用全局 settings（与 controller 的 build_artifact_store(settings) 同一 root）。
    """
    from app.config import settings
    from app.services.artifact_store.factory import build_artifact_store

    settings.ARTIFACT_LOCAL_ROOT = "/tmp/wp07a-artifacts-test"
    settings.ARTIFACT_DRIVER = "local"
    store = build_artifact_store(settings)
    ref = store.put_bytes(data, mime)
    eng = create_engine(url, poolclass=NullPool)
    with eng.begin() as c:
        c.execute(text(
            "INSERT INTO trading.artifact_objects (sha256, original_size, stored_size, mime, "
            " compression, storage_driver, storage_version, locator) "
            "VALUES (:sha, :os, :ss, :m, :c, :drv, :sv, :loc)"
        ), {"sha": ref.sha256, "os": ref.original_size, "ss": ref.stored_size,
            "m": ref.mime, "c": ref.compression, "drv": ref.storage_driver,
            "sv": ref.storage_version, "loc": ref.locator})
    eng.dispose()
    return ref.sha256


@pytest.fixture
async def env(temp_pg_db):
    _upgrade(temp_pg_db.url)
    import shutil
    shutil.rmtree("/tmp/wp07a-artifacts-test", ignore_errors=True)
    sha = _put_artifact(temp_pg_db.url, b"0123456789abcdef" * 20, "application/octet-stream")
    zstd_sha = _put_artifact(temp_pg_db.url, b"Z" * 20_000, "application/octet-stream")
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
    from app.deps import require_auth
    app.dependency_overrides[require_auth] = lambda: AuthInfo(1, "admin", "u", "t",
                                                              {"is_super_admin": True})
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield {"client": client, "sha": sha, "zstd_sha": zstd_sha, "total": 320}
    finally:
        app.dependency_overrides.clear()
        reset_admin_logic(None)
        async_engine.sync_engine.dispose()
        import shutil
        shutil.rmtree("/tmp/wp07a-artifacts-test", ignore_errors=True)


@pytest.mark.anyio
async def test_metadata_no_content_no_path(env):
    resp = await env["client"].get(f"/api/admin/v2/artifacts/{env['sha']}/metadata")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["content_hash"] == env["sha"]
    assert data["content_length"] == str(env["total"])
    # 无存储路径/凭证
    raw = resp.text
    assert "locator" not in raw and "storage_driver" not in raw and "bucket" not in raw


@pytest.mark.anyio
async def test_single_range_206(env):
    resp = await env["client"].get(
        f"/api/admin/v2/artifacts/{env['sha']}/content",
        headers={"Range": "bytes=0-9"},
    )
    assert resp.status_code == 206
    assert resp.content == b"0123456789"
    assert resp.headers["Content-Range"] == f"bytes 0-9/{env['total']}"
    assert resp.headers["Accept-Ranges"] == "bytes"
    assert resp.headers["ETag"] == f'"{env["sha"]}"'


@pytest.mark.anyio
async def test_zstd_original_byte_range_206(env):
    resp = await env["client"].get(
        f"/api/admin/v2/artifacts/{env['zstd_sha']}/content",
        headers={"Range": "bytes=100-109"},
    )
    assert resp.status_code == 206
    assert resp.content == b"Z" * 10
    assert resp.headers["Content-Range"] == "bytes 100-109/20000"


@pytest.mark.anyio
async def test_no_range_416(env):
    resp = await env["client"].get(f"/api/admin/v2/artifacts/{env['sha']}/content")
    assert resp.status_code == 416
    assert resp.headers["Accept-Ranges"] == "bytes"


@pytest.mark.anyio
async def test_multi_range_416(env):
    resp = await env["client"].get(
        f"/api/admin/v2/artifacts/{env['sha']}/content",
        headers={"Range": "bytes=0-9,20-29"},
    )
    assert resp.status_code == 416


@pytest.mark.anyio
async def test_out_of_bounds_416(env):
    resp = await env["client"].get(
        f"/api/admin/v2/artifacts/{env['sha']}/content",
        headers={"Range": "bytes=999-2000"},
    )
    assert resp.status_code == 416


@pytest.mark.anyio
async def test_oversize_range_416(env):
    resp = await env["client"].get(
        f"/api/admin/v2/artifacts/{env['sha']}/content",
        headers={"Range": f"bytes=0-{MAX_BYTES + 1}"},
    )
    assert resp.status_code == 416


@pytest.mark.anyio
async def test_malformed_range_416(env):
    resp = await env["client"].get(
        f"/api/admin/v2/artifacts/{env['sha']}/content",
        headers={"Range": "bytes=abc"},
    )
    assert resp.status_code == 416


@pytest.mark.anyio
async def test_unknown_artifact_404(env):
    resp = await env["client"].get(f"/api/admin/v2/artifacts/{'b' * 64}/metadata")
    assert resp.json()["code"] == 404
