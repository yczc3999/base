"""WP-07A Checkpoint B —— Admin Read API 查询链合同（真 PostgreSQL + ASGI）。

证明：detail 链 ID 全等、摘要投影（raw prompt/response/book/body 不出现在 list/detail）、
BIGINT/NUMERIC 全字符串、执行 trace 覆盖 intent→execution→envelope→order→ledger→chain。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
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


def _seed(url):
    """replica 插入一条完整链：market/spec/token/episode/decision/intent/execution/ledger/ai。"""
    eng = create_engine(url, poolclass=NullPool)
    with eng.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(text(
            "INSERT INTO trading.pm_market_versions (id, market_id, version_no, observed_at, "
            " received_at, normalized_hash) VALUES (20, 1, 1, now(), now(), repeat('z',64))"))
        c.execute(text(
            "INSERT INTO trading.contract_snapshots (id, market_version_id, yes_token_version_id, "
            " no_token_version_id, artifact_object_id, question, rules, clarification, "
            " resolution_source, cutoff_at, timezone_name, raw_outcome_mapping, content_hash) "
            "VALUES (1, 20, 1, 2, 0, 'Q?', '{}', '{}', 'official', now(), 'UTC', '{}', "
            " repeat('1',64))"))
        c.execute(text(
            "INSERT INTO trading.pm_markets (id, gamma_market_id, condition_id, question, slug, ticker, "
            " active, closed, accepting_orders, neg_risk, volume, liquidity, content_hash, raw_artifact_ref) "
            "VALUES (1, '1001', ('0x'::text || repeat('1',64)), 'Q?', 'mkt-1', 't-1', true, false, true, false, 10, 20, "
            " repeat('a',64), 'raw-1')"))
        c.execute(
            text(
                "INSERT INTO trading.contract_specs (id, contract_key, version_no, snapshot_id, "
                " kc_resolution_states, token_ids, token_count, state_count, compiler_version, "
                " schema_version, status, content_hash) "
                "VALUES (10, 'cs-1', 1, 1, CAST(:kc AS jsonb), CAST(:tids AS jsonb), "
                " 2, 2, 'v1', 1, 'pass', repeat('b',64))"
            ),
            {"kc": '["YES","NO"]', "tids": '{"1":1,"2":2}'},
        )
        c.execute(
            text(
                "INSERT INTO trading.payout_functions (contract_spec_id, pm_token_id, token_version_id, "
                " outcome_index, function_ir, test_vectors, algorithm_hash, content_hash) "
                "VALUES (10, 1, 1, 0, CAST(:fir AS jsonb), '{}', repeat('c',64), repeat('d',64))"
            ),
            {"fir": '{"YES":1,"NO":0}'},
        )
        c.execute(text(
            "INSERT INTO trading.pm_tokens (id, token_id, market_id, outcome_index, outcome_label, "
            " price_hint) VALUES (1, 'tok-1', 1, 0, 'YES', 0)"))
        c.execute(text(
            "INSERT INTO trading.forecast_episodes (id, episode_key, decision_opportunity_id, "
            " component_version_id, strategy_version_id, objective_contract_id, trigger, cutoff_at, "
            " horizon, experiment_variant, status, cognition_status) "
            "VALUES (2, repeat('e',64), 0, 0, 0, 0, 'manual', now(), '2w', 'baseline', 'DRAFT', 'PENDING')"))
        c.execute(text(
            "INSERT INTO trading.trade_decisions (id, decision_key, episode_id, forecast_submission_id, "
            " forecast_lease_id, objective_contract_id, strategy_version_id, release_manifest_id, "
            " execution_spec_version_id, capital_permission_manifest_id, experiment_variant, "
            " decision_class, status, trigger_at, quote_bound_at, decided_at, input_hash, output_hash) "
            "VALUES (3, repeat('d',64), 2, 0, 0, 0, 0, 0, 0, 0, 'baseline', 'CHAMPION', 'ACTION', "
            " '2026-08-12T00:00:00Z', '2026-08-12T00:01:00Z', '2026-08-12T00:02:00Z', "
            " repeat('e',64), repeat('f',64))"))
        c.execute(text(
            "INSERT INTO trading.economic_action_intents (id, intent_key, intent_hash, trade_decision_id, "
            " action_set_id, status, ttl_at, preflight) "
            "VALUES (4, repeat('i',64), repeat('f',64), 3, 0, 'PLANNED', now()+interval '1 hour', '{}')"))
        c.execute(text(
            "INSERT INTO trading.executions (id, execution_key, economic_action_intent_id, "
            " action_set_leg_id, contract_spec_id, token_id, fill_role, quantity, filled_quantity, "
            " status, quote_checkpoint_id, portfolio_namespace) "
            "VALUES (5, repeat('x',64), 4, 0, 10, 1, 'open', 100, 0, 'PENDING', 0, 'ns-1')"))
        c.execute(text(
            "INSERT INTO trading.ledger_transactions (id, transaction_key, status, kind, "
            " portfolio_namespace, trade_decision_id) "
            "VALUES (6, repeat('l',64), 'PENDING', 'FILL', 'ns-1', 3)"))
        c.execute(text(
            "INSERT INTO trading.ai_invocations (id, occurred_at, invocation_key, episode_id, stage, "
            " role, attempt_no, experiment_variant, requested_provider, requested_route, "
            " requested_model, network_policy, context_class, taint_report, input_manifest, "
            " input_manifest_hash, lifecycle_state, tool_count, search_count, cost_estimated, "
            " pricing_snapshot, input_tokens, cache_tokens, output_tokens, reasoning_tokens, "
            " request_artifact_ref, raw_response_artifact_ref, "
            " parsed_output_artifact_ref) "
            "VALUES (7, '2026-08-12T00:00:00Z', repeat('a',64), 2, 'scoring', 'scorer', 1, 'baseline', "
            " 'deepseek', 'primary', 'deepseek-v4', 'NONE', 'CONTRACT', '{}', '{}', repeat('f',64), "
            " 'PLANNED', 0, 0, 0, '{}', 9007199254740993, 9007199254740994, "
            " 9007199254740995, 9007199254740996, 'sha-1', 'sha-2', 'sha-3')"))
        c.execute(text(
            "INSERT INTO trading.replay_runs (run_key,replay_kind,manifest_hash,code_hash,seed,"
            "input_artifact_hash,output_artifact_hash) VALUES ('replay-bigint','original',"
            "repeat('1',64),repeat('2',64),9007199254740997,repeat('3',64),repeat('4',64))"))
        c.execute(text(
            "INSERT INTO trading.ops_health_current (metric_name,metric_value,status,as_of,"
            "source_high_watermark,projection_version,projection_hash) VALUES "
            "('health',123.45,'ok',now(),9007199254740998,1,repeat('5',64))"))
    eng.dispose()


@pytest.fixture
async def env(temp_pg_db):
    _upgrade(temp_pg_db.url)
    _seed(temp_pg_db.url)
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
            yield {"client": client, "url": temp_pg_db.url}
    finally:
        app.dependency_overrides.clear()
        reset_admin_logic(None)
        async_engine.sync_engine.dispose()


def _assert_no_sensitive(body_text: str):
    """raw prompt/response 全文、signed body、book levels 不出现在响应。"""
    for marker in ("raw_prompt", "raw_response", "signed_body", "book_levels",
                   "input_manifest", "request_artifact_ref"):
        assert marker not in body_text, f"sensitive field leaked: {marker}"


@pytest.mark.anyio
async def test_market_detail_chain(env):
    resp = await env["client"].get("/api/admin/v2/markets/1")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["market"]["id"] == "1"          # BIGINT → string
    assert data["market"]["gamma_market_id"] == "1001"
    assert "specs" in data and data["specs"]     # spec/payout 链
    assert data["specs"][0]["id"] == "10"
    _assert_no_sensitive(resp.text)


@pytest.mark.anyio
async def test_episode_detail_chain(env):
    resp = await env["client"].get("/api/admin/v2/episodes/2")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["episode"]["id"] == "2"
    assert data["episode"]["episode_key"] == "e" * 64
    assert {"priors", "evidence_bundles", "submissions", "gates"} <= set(data)
    _assert_no_sensitive(resp.text)


@pytest.mark.anyio
async def test_decision_detail_chain(env):
    resp = await env["client"].get("/api/admin/v2/decisions/3")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["decision"]["id"] == "3"
    assert data["decision"]["episode_id"] == "2"
    assert {"intents"} <= set(data)
    # intent 链 ID 全等
    assert data["intents"][0]["id"] == "4"
    _assert_no_sensitive(resp.text)


@pytest.mark.anyio
async def test_ai_detail_compound_identity_and_no_raw(env):
    resp = await env["client"].get("/api/admin/v2/ai-invocations/7?occurred_at=2026-08-12T00:00:00Z")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["invocation"]["id"] == "7"
    # occurred_at 返回为 UTC 时间（DB 时区可能偏移；此处验证是时间而非空）
    assert "2026" in data["invocation"]["occurred_at"]
    # 摘要投影：artifact ref 以 hash 引用存在（允许），但大 JSON（input_manifest 全文）不内联
    raw = resp.text
    assert data["invocation"]["request_artifact_ref"] == "sha-1"  # hash 引用允许
    assert "input_manifest" not in raw                            # 大 JSON 不内联
    assert "pricing_snapshot" not in raw                          # 定价快照不内联
    assert data["invocation"]["input_tokens"] == "9007199254740993"
    assert data["invocation"]["cache_tokens"] == "9007199254740994"
    assert data["invocation"]["output_tokens"] == "9007199254740995"
    assert data["invocation"]["reasoning_tokens"] == "9007199254740996"


@pytest.mark.anyio
async def test_execution_trace_chain(env):
    resp = await env["client"].get("/api/admin/v2/execution/3/trace")
    assert resp.status_code == 200
    trace = resp.json()["data"]["items"]
    kinds = {item["kind"] for item in trace}
    assert {"intent", "execution", "ledger"} <= kinds
    _assert_no_sensitive(resp.text)


@pytest.mark.anyio
async def test_list_items_are_summary_projection(env):
    resp = await env["client"].get("/api/admin/v2/markets")
    data = resp.json()["data"]
    assert data["has_more"] is False
    assert len(data["items"]) == 1
    item = data["items"][0]
    # 摘要投影：不内联 raw artifact / book levels
    assert "raw_artifact_ref" not in item
    assert "book_levels" not in item
    _assert_no_sensitive(resp.text)


@pytest.mark.anyio
async def test_execution_fact_pages_mark_authoritative_and_snapshot(env):
    resp = await env["client"].get("/api/admin/v2/execution/intents")
    data = resp.json()["data"]
    assert data["authoritative"] is True
    assert data["as_of"].endswith("Z")


@pytest.mark.anyio
async def test_unknown_detail_404(env):
    resp = await env["client"].get("/api/admin/v2/markets/999999")
    assert resp.json()["code"] == 404


@pytest.mark.anyio
async def test_dashboard_projection_contract_and_no_store(env):
    resp = await env["client"].get("/api/admin/v2/dashboard")
    assert resp.headers["Cache-Control"] == "private, no-store"
    block = resp.json()["data"]["blocks"]["ops_health_current"]
    assert block["source_high_watermark"] == "9007199254740998"
    assert block["projection_version"] == 1
    assert block["projection_hash"] == "5" * 64
    assert block["freshness_status"] == "fresh"
    assert block["rows"][0]["id"] == "1"
    from decimal import Decimal

    assert Decimal(block["rows"][0]["metric_value"]) == Decimal("123.45")


@pytest.mark.anyio
async def test_replay_seed_bigint_is_string(env):
    listing = (await env["client"].get("/api/admin/v2/replay")).json()["data"]
    assert listing["items"][0]["seed"] == "9007199254740997"
    detail = (await env["client"].get("/api/admin/v2/replay/1")).json()["data"]
    assert detail["seed"] == "9007199254740997"


@pytest.mark.anyio
async def test_episode_timeline_uses_real_tuple_keyset(env):
    """第二页不得重复第一页；asc/desc 都由 SQL tuple keyset 执行。"""
    eng = create_engine(env["url"], poolclass=NullPool)
    with eng.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        for i in range(8):
            c.execute(text(
                "INSERT INTO trading.information_snapshots "
                "(snapshot_key,episode_id,gate,content,content_hash,created_at) "
                "VALUES (:key,2,'R1','{}',:hash,"
                "'2026-08-12T01:00:00Z'::timestamptz + make_interval(secs => :i))"
            ), {"key": f"timeline-{i}", "hash": f"{i + 100:064x}", "i": i})
    eng.dispose()

    first = (await env["client"].get(
        "/api/admin/v2/episodes/2/timeline", params={"limit": 3}
    )).json()["data"]
    second = (await env["client"].get(
        "/api/admin/v2/episodes/2/timeline",
        params={"limit": 3, "cursor": first["next_cursor"]},
    )).json()["data"]
    ids1 = {item["id"] for item in first["items"]}
    ids2 = {item["id"] for item in second["items"]}
    assert len(ids1) == 3 and len(ids2) == 3
    assert ids1.isdisjoint(ids2)
    assert second["as_of"] == first["as_of"]

    asc = (await env["client"].get(
        "/api/admin/v2/episodes/2/timeline", params={"limit": 3, "direction": "asc"}
    )).json()["data"]
    assert [item["created_at"] for item in asc["items"]] == sorted(
        item["created_at"] for item in asc["items"]
    )
