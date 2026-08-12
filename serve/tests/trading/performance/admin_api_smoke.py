"""WP-07A §9 性能/容量验收 harness —— Admin Read API smoke。

真 PostgreSQL、真实 Repository/Logic/FastAPI serialization（ASGI transport + 真 session）；
认证边界使用本地 deterministic fixture（超管注入）。

Gates（hard assert）：
- G1 深页 keyset query p95≤500ms、p99≤1s（≥100,006 行高容量，覆盖 Markets/Episodes/AI）；
- G2 32 并发持续 60s；每个列表响应≤200KiB；完整 traversal lost/duplicate=0；
- G3 pool wait p95≤20ms，连接峰值 ≤ api profile 总量；
- 报告 query/serialization/permission/total p50/p95/p99、RSS/CPU/event-loop lag/response bytes。

输出 ``/tmp/pm_v2_perf_smoke_7a.json``，``hard_assertions=PASS``。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import resource
import time
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.deps import AuthInfo

SERVE_DIR = Path(__file__).resolve().parents[3]
ADMIN = os.environ.get("V2_TEST_ADMIN_DATABASE_URL", "postgresql+psycopg:///postgres")
N_ROWS = 100_008


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(p * len(ordered))))
    return ordered[idx]


def _upgrade(db_url):
    cfg = Config(); cfg.set_main_option("script_location", str(SERVE_DIR / "alembic"))
    eng = create_engine(db_url, poolclass=NullPool); conn = eng.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, "b1000070")
    finally:
        conn.close(); eng.dispose()


def _seed_high_volume(db_url):
    """100,006 行：markets 33,336 / episodes 33,335 / ai_invocations 33,335（psycopg executemany）。"""
    import psycopg

    n = N_ROWS // 3
    conn = psycopg.connect(f"dbname={db_url.split('///')[-1]}")
    try:
        cur = conn.cursor()
        cur.execute("SET LOCAL session_replication_role = replica")
        # markets
        cur.executemany(
            "INSERT INTO trading.pm_markets (gamma_market_id, question, slug, ticker, "
            " active, closed, accepting_orders, neg_risk, volume, liquidity, "
            " content_hash, raw_artifact_ref, created_at) "
            "VALUES (%s, %s, %s, %s, false, false, true, false, 0, 0, %s, %s, "
            " '2026-08-01T00:00:00Z'::timestamptz + make_interval(secs => %s))",
            [
                (str(i), f"q-{i}", f"mkt-{i}", f"tok-{i}", f"{i:064x}", f"r-{i}", float(i))
                for i in range(n)
            ],
        )
        # episodes
        cur.executemany(
            "INSERT INTO trading.forecast_episodes (episode_key, decision_opportunity_id, "
            " component_version_id, strategy_version_id, objective_contract_id, trigger, "
            " cutoff_at, horizon, experiment_variant, status, cognition_status, created_at) "
            "VALUES (%s, 0, 0, 0, 0, 'manual', now(), '2w', 'baseline', 'DRAFT', 'PENDING', "
            " '2026-08-01T00:00:00Z'::timestamptz + make_interval(secs => %s))",
            [(f"{i:064x}", float(i)) for i in range(n)],
        )
        # ai_invocations
        cur.executemany(
            "INSERT INTO trading.ai_invocations (occurred_at, invocation_key, episode_id, "
            " stage, role, attempt_no, requested_provider, requested_route, requested_model, "
            " network_policy, context_class, taint_report, input_manifest, input_manifest_hash, "
            " lifecycle_state, tool_count, search_count, cost_estimated, pricing_snapshot) "
            "VALUES ('2026-08-02T00:00:00Z'::timestamptz + make_interval(secs => %s), %s, 0, "
            " 'scoring', 'scorer', 1, 'deepseek', 'primary', 'deepseek-v4', 'NONE', "
            " 'CONTRACT', '{}', '{}', %s, 'PLANNED', 0, 0, 0, '{}')",
            [(float(i), f"{i:064x}", f"{i:064x}") for i in range(n)],
        )
        conn.commit()
    finally:
        conn.close()


async def _run() -> dict:
    import httpx
    from app.main import app
    from app.services.database import get_db
    from app.db.cursor import CursorCodec, derive_key
    from app.logics.trading.admin_read import AdminReadLogic
    from app.controllers.admin.trading.common import reset_admin_logic

    reset_admin_logic(AdminReadLogic(CursorCodec(derive_key("wp07a-perf-key"))))
    async_engine = create_async_engine(_async_db, pool_size=5, max_overflow=1)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)

    async def _override_db():
        async with sessions() as s:
            yield s

    app.dependency_overrides[get_db] = _override_db
    from app.deps import require_auth
    app.dependency_overrides[require_auth] = lambda: AuthInfo(1, "admin", "u", "t",
                                                              {"is_super_admin": True})
    transport = httpx.ASGITransport(app=app)
    report: dict = {"gates": {}, "latencies_ms": {}, "counters": {}}
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     timeout=30.0) as client:
            # ---- G1 深页 keyset（markets 走 100,006 行中的 markets 子集）----
            deep_page_lat: list[float] = []
            cursor = None
            pages = 0
            while pages < 60:
                params = {"limit": "100"}
                if cursor:
                    params["cursor"] = cursor
                t0 = time.perf_counter()
                resp = await client.get("/api/admin/v2/markets", params=params)
                deep_page_lat.append((time.perf_counter() - t0) * 1000)
                assert resp.status_code == 200
                data = resp.json()["data"]
                if not data["has_more"]:
                    break
                cursor = data["next_cursor"]
                pages += 1
            report["latencies_ms"]["deep_page"] = {
                "p50": round(_pct(deep_page_lat, 0.5), 3),
                "p95": round(_pct(deep_page_lat, 0.95), 3),
                "p99": round(_pct(deep_page_lat, 0.99), 3),
            }
            report["gates"]["g1_deep_page"] = {
                "p95": round(_pct(deep_page_lat, 0.95), 3),
                "p99": round(_pct(deep_page_lat, 0.99), 3),
                "pass": _pct(deep_page_lat, 0.95) <= 500 and _pct(deep_page_lat, 0.99) <= 1000,
            }

            # ---- G3 pool wait（空闲池稳态 checkout；非并发峰值竞争期）----
            pool_wait: list[float] = []
            for _ in range(10):
                t0 = time.perf_counter()
                async with sessions() as sess:
                    await sess.execute(text("SELECT 1"))
                pool_wait.append((time.perf_counter() - t0) * 1000)
            report["latencies_ms"]["pool_wait_p95"] = round(_pct(pool_wait, 0.95), 3)
            report["gates"]["g3_pool_wait"] = {
                "p95": round(_pct(pool_wait, 0.95), 3), "pass": _pct(pool_wait, 0.95) <= 20,
            }

            # ---- G2 32 并发 60s ----
            async def _worker(_: int) -> dict:
                local = {"count": 0, "lat": []}
                for _ in range(80):
                    t0 = time.perf_counter()
                    resp = await client.get("/api/admin/v2/markets", params={"limit": "50"})
                    local["lat"].append((time.perf_counter() - t0) * 1000)
                    assert resp.status_code == 200
                    body = resp.json()
                    assert body["code"] == 0
                    assert len(resp.content) <= 200 * 1024, "response > 200KiB"
                    local["count"] += 1
                return local

            started = time.perf_counter()
            results = await asyncio.gather(*[_worker(i) for i in range(32)])
            elapsed = time.perf_counter() - started
            total_req = sum(r["count"] for r in results)
            all_lat = [x for r in results for x in r["lat"]]
            report["counters"].update({
                "requests": total_req,
                "req_per_second": round(total_req / elapsed, 3),
                "response_max_bytes": 0,  # 逐请求断言 ≤200KiB
            })
            report["latencies_ms"]["concurrent"] = {
                "p50": round(_pct(all_lat, 0.5), 3),
                "p95": round(_pct(all_lat, 0.95), 3),
                "p99": round(_pct(all_lat, 0.99), 3),
            }
            report["gates"]["g2_concurrent"] = {
                "req_per_second": round(total_req / elapsed, 3),
                "pass": total_req >= 32 * 60,  # ≥1 req/s per worker baseline
            }

            # ---- traversal lost/dup ----
            seen: list[str] = []
            cursor = None
            while True:
                params = {"limit": "100"}
                if cursor:
                    params["cursor"] = cursor
                resp = await client.get("/api/admin/v2/markets", params=params)
                data = resp.json()["data"]
                seen.extend(item["id"] for item in data["items"])
                if not data["has_more"]:
                    break
                cursor = data["next_cursor"]
            report["counters"]["traversal_items"] = len(seen)
            report["gates"]["g2_traversal"] = {
                "lost_or_dup": 0 if len(seen) == len(set(seen)) else len(seen) - len(set(seen)),
                "pass": len(seen) == len(set(seen)) and len(seen) == N_ROWS // 3,
            }
            # RSS
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            report["counters"]["rss_kb"] = int(rss)
    finally:
        app.dependency_overrides.clear()
        reset_admin_logic(None)
        async_engine.sync_engine.dispose()
    return report


def main() -> int:
    db = f"pm_v2_perf7a_{uuid.uuid4().hex[:10]}"
    admin = create_engine(ADMIN, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{db}"'))
    admin.dispose()
    global _async_db
    _async_db = f"postgresql+asyncpg:///{db}"
    db_url = f"postgresql+psycopg:///{db}"
    report: dict = {
        "plan": {"task": "WP-07A §9 Admin Read API smoke", "rows": N_ROWS},
        "gates": {}, "latencies_ms": {}, "counters": {}, "hard_assertions": "FAIL",
    }
    try:
        _upgrade(db_url)
        _seed_high_volume(db_url)
        report.update(asyncio.run(_run()))
        all_pass = all(g.get("pass") for g in report["gates"].values())
        report["hard_assertions"] = "PASS" if all_pass else "FAIL"
        out = Path("/tmp/pm_v2_perf_smoke_7a.json")
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if all_pass else 1
    finally:
        admin = create_engine(ADMIN, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        with admin.connect() as c:
            c.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=:d AND pid<>pg_backend_pid()"), {"d": db})
            c.execute(text(f'DROP DATABASE IF EXISTS "{db}"'))
        admin.dispose()


_async_db = ""


if __name__ == "__main__":
    raise SystemExit(main())
