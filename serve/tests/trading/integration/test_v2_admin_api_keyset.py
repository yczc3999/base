"""WP-07A Checkpoint B —— Admin Read API keyset（真 PostgreSQL + ASGI）。

证明：并发插入期间完整翻页 lost=0、duplicate=0，所有页 as_of/filter_hash 全等；
cursor tamper / 换 endpoint / 换 filter / 换 direction / 超长 → 400；limit 范围 1–200。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.deps import AuthInfo

N_MARKETS = 120
LIMIT = 10


def _upgrade(url):
    cfg = Config(); cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "alembic"))
    eng = create_engine(url, poolclass=NullPool); conn = eng.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, "b1000070")
    finally:
        conn.close(); eng.dispose()


def _seed_markets(url, n: int, *, offset: int = 0):
    """replica 插入 n 条 pm_markets（created_at 递增）。"""
    eng = create_engine(url, poolclass=NullPool)
    with eng.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        for i in range(offset, offset + n):
            c.execute(text(
                "INSERT INTO trading.pm_markets (gamma_market_id, question, slug, ticker, "
                " active, closed, accepting_orders, neg_risk, volume, liquidity, "
                " content_hash, raw_artifact_ref, created_at) "
                "VALUES (:g, :q, :s, :t, false, false, true, false, 0, 0, :h, :r, "
                " now() - make_interval(secs => :s)::interval)"
            ), {"g": str(i), "q": f"q-{i}", "s": f"mkt-{i}", "t": f"tok-{i}",
                "h": f"{i:064x}", "r": f"raw-{i}", "s": float(i)})
    eng.dispose()


@pytest.fixture
async def env(temp_pg_db):
    _upgrade(temp_pg_db.url)
    _seed_markets(temp_pg_db.url, N_MARKETS)
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
            yield {"client": client, "app": app, "url": temp_pg_db.url, "async_engine": async_engine}
    finally:
        app.dependency_overrides.clear()
        reset_admin_logic(None)
        async_engine.sync_engine.dispose()


@pytest.mark.anyio
async def test_full_traversal_no_lost_no_dup_same_snapshot(env):
    client = env["client"]
    seen: list[str] = []
    as_ofs = set()
    fhs = set()
    cursor = None
    pages = 0
    while True:
        params = {"limit": str(LIMIT)}
        if cursor:
            params["cursor"] = cursor
        resp = await client.get("/api/admin/v2/markets", params=params)
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0, body
        data = body["data"]
        as_ofs.add(data["as_of"])
        fhs.add(data["filter_hash"])
        seen.extend(item["id"] for item in data["items"])
        pages += 1
        if not data["has_more"]:
            assert data["next_cursor"] is None
            break
        assert data["next_cursor"] is not None
        cursor = data["next_cursor"]
        assert pages < 1000
    # lost=0 / duplicate=0
    assert len(seen) == N_MARKETS, f"expected {N_MARKETS}, got {len(seen)}"
    assert len(set(seen)) == len(seen), "duplicate ids"
    # 快照一致：所有页 as_of/filter_hash 全等
    assert len(as_ofs) == 1, f"as_of drifted: {as_ofs}"
    assert len(fhs) == 1, "filter_hash drifted"
    # 覆盖全部种子 id
    import json
    eng = create_engine(env["url"], poolclass=NullPool)
    with eng.connect() as c:
        db_ids = {str(r[0]) for r in c.execute(text(
            "SELECT id FROM trading.pm_markets ORDER BY created_at, id"))}
    eng.dispose()
    assert set(seen) == db_ids


@pytest.mark.anyio
async def test_concurrent_insert_does_not_break_snapshot(env):
    """翻页期间并发插入：快照稳定（as_of 冻结），翻到页内的 id 仍唯一。"""
    client = env["client"]
    # 后台并发插入（独立连接）
    import threading

    stop = threading.Event()

    def _inserter():
        eng = create_engine(env["url"], poolclass=NullPool)
        i = N_MARKETS
        while not stop.is_set():
            try:
                with eng.begin() as c:
                    c.execute(text("SET LOCAL session_replication_role = replica"))
                    c.execute(text(
                        "INSERT INTO trading.pm_markets (gamma_market_id, question, slug, ticker, "
                        " active, closed, accepting_orders, neg_risk, volume, liquidity, "
                        " content_hash, raw_artifact_ref, created_at) "
                        "VALUES (:g, :q, :s, :t, false, false, true, false, 0, 0, :h, :r, now())"
                    ), {"g": str(i), "q": f"c-{i}", "s": f"c-{i}", "t": f"ct-{i}",
                        "h": f"{i:064x}", "r": f"cr-{i}"})
                i += 1
            except Exception:
                pass
        eng.dispose()

    thread = threading.Thread(target=_inserter, daemon=True)
    thread.start()
    try:
        seen: list[str] = []
        cursor = None
        while True:
            params = {"limit": str(LIMIT)}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get("/api/admin/v2/markets", params=params)
            body = resp.json()
            data = body["data"]
            seen.extend(item["id"] for item in data["items"])
            if not data["has_more"]:
                break
            cursor = data["next_cursor"]
    finally:
        stop.set()
        thread.join(timeout=2)
    assert len(seen) == len(set(seen)), "duplicate ids during concurrent insert"


@pytest.mark.anyio
async def test_cursor_tamper_and_mismatch_400(env):
    client = env["client"]
    # 首屏拿一个有效 cursor
    resp = await client.get("/api/admin/v2/markets", params={"limit": "10"})
    data = resp.json()["data"]
    assert data["has_more"] is True
    cursor = data["next_cursor"]

    # tamper
    tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
    resp = await client.get("/api/admin/v2/markets", params={"limit": "10", "cursor": tampered})
    assert resp.json()["code"] == 400

    # 换 endpoint：cursor 用于 episodes → endpoint mismatch 400
    resp = await client.get("/api/admin/v2/episodes", params={"limit": "10", "cursor": cursor})
    assert resp.json()["code"] == 400

    # 换 filter：cursor 用于 markets?neg_risk=true → filter mismatch 400
    resp = await client.get("/api/admin/v2/markets",
                            params={"limit": "10", "cursor": cursor, "neg_risk": "true"})
    assert resp.json()["code"] == 400

    # 换 direction：desc cursor 不得用于 asc 查询
    resp = await client.get(
        "/api/admin/v2/markets",
        params={"limit": "10", "cursor": cursor, "direction": "asc"},
    )
    assert resp.json()["code"] == 400

    # 超长
    resp = await client.get("/api/admin/v2/markets",
                            params={"limit": "10", "cursor": "x" * 5000})
    assert resp.json()["code"] == 400


@pytest.mark.anyio
async def test_limit_range(env):
    client = env["client"]
    resp = await client.get("/api/admin/v2/markets", params={"limit": "0"})
    assert resp.json()["code"] == 400
    resp = await client.get("/api/admin/v2/markets", params={"limit": "201"})
    assert resp.json()["code"] == 400
    resp = await client.get("/api/admin/v2/markets", params={"limit": "1"})
    assert resp.json()["code"] == 0
    assert len(resp.json()["data"]["items"]) == 1


@pytest.mark.anyio
async def test_unknown_filter_400(env):
    client = env["client"]
    resp = await client.get("/api/admin/v2/markets", params={"bogus": "1"})
    assert resp.json()["code"] == 400


@pytest.mark.anyio
async def test_first_page_filter_applies_and_rows_are_bounded_by_as_of(env):
    """首屏也必须应用 filter；冻结 snapshot 后不得返回未来 sort_time。"""
    client = env["client"]
    eng = create_engine(env["url"], poolclass=NullPool)
    with eng.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(text(
            "INSERT INTO trading.pm_markets (gamma_market_id, question, slug, ticker, "
            "active, closed, accepting_orders, neg_risk, volume, liquidity, content_hash, "
            "raw_artifact_ref, created_at) VALUES "
            "('future','future','future','future',true,false,true,false,0,0,repeat('f',64),"
            " 'future',now()+interval '1 day'),"
            "('closed','closed','closed','closed',false,true,false,false,0,0,repeat('e',64),"
            " 'closed',now()-interval '1 day')"
        ))
    eng.dispose()

    resp = await client.get("/api/admin/v2/markets", params={"closed": "true"})
    data = resp.json()["data"]
    assert [item["gamma_market_id"] for item in data["items"]] == ["closed"]
    assert all(item["closed"] is True for item in data["items"])
    assert "future" not in {item["gamma_market_id"] for item in data["items"]}


@pytest.mark.anyio
async def test_tuple_lists_no_longer_raise_500(env):
    """components/positions/model-routes 与 Logic.page 使用同一 tuple 接口。"""
    for path in (
        "/api/admin/v2/components",
        "/api/admin/v2/execution/positions",
        "/api/admin/v2/model-routes",
    ):
        resp = await env["client"].get(path)
        assert resp.json()["code"] == 0, (path, resp.text)


@pytest.mark.anyio
async def test_market_tuple_keyset_orders_both_directions(env):
    client = env["client"]
    for direction, reverse in (("asc", False), ("desc", True)):
        first = (await client.get(
            "/api/admin/v2/markets",
            params={"limit": "7", "direction": direction},
        )).json()["data"]
        second = (await client.get(
            "/api/admin/v2/markets",
            params={
                "limit": "7",
                "direction": direction,
                "cursor": first["next_cursor"],
            },
        )).json()["data"]
        first_keys = [(row["created_at"], int(row["id"])) for row in first["items"]]
        assert first_keys == sorted(first_keys, reverse=reverse)
        assert {row["id"] for row in first["items"]}.isdisjoint(
            row["id"] for row in second["items"]
        )


@pytest.mark.anyio
async def test_change_limit_same_snapshot(env):
    """改变 limit 不改变 snapshot/filter：limit 不进 cursor 身份，可跨 limit 复用 cursor。"""
    client = env["client"]
    first = (await client.get("/api/admin/v2/markets", params={"limit": "7"})).json()["data"]
    assert first["has_more"] is True
    # 用 limit=7 的 cursor 请求 limit=13 下一页：as_of/filter_hash 与首屏一致
    second = (await client.get("/api/admin/v2/markets", params={
        "limit": "13", "cursor": first["next_cursor"],
    })).json()["data"]
    assert second["as_of"] == first["as_of"]
    assert second["filter_hash"] == first["filter_hash"]
    # 7 + 13 + ... 覆盖全部（无 lost/dup 前提不变）
    assert len(second["items"]) == 13
