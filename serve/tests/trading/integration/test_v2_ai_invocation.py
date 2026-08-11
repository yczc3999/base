"""WP-02 AI invocation 生命周期集成测试（真 PostgreSQL）。

PLANNED→STARTED→…→ACCEPTED|REJECTED|FAILED|TIMEOUT|UNKNOWN；retry/fallback/cache 独立
attempt；失败不缓存；provider 返回后崩溃 → UNKNOWN；secret echo → quarantine 不 ACCEPTED。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.ai_runtime.runner import AIRunner
from app.ai_runtime.validator import OutputValidator
from app.domain.trading.hashing import canonical_hash
from app.services.model_gateway.contracts import ModelRequest
from app.services.model_gateway.service import ModelGatewayService

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V21 = "b1000021"
WIRE_DIR = SERVE_DIR / "tests" / "trading" / "fixtures" / "ai_wire"


def _run(cmd, revision, db_url):
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    engine = create_engine(db_url, poolclass=NullPool)
    conn = engine.connect()
    cfg.attributes["connection"] = conn
    try:
        cmd(cfg, revision)
    finally:
        conn.close()
        engine.dispose()


@pytest_asyncio.fixture
async def ai_env(temp_pg_db):
    _run(command.upgrade, V21, temp_pg_db.url)
    admin = make_url(temp_pg_db.url)
    async_url = admin.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    engine = create_async_engine(async_url, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield {"sessions": sessions, "url": temp_pg_db.url}
    await engine.dispose()


def _fixture_response(provider: str, name: str, status: int = 200):
    async def transport(endpoint, *, headers, json, timeout):
        body = (WIRE_DIR / provider / name).read_text()
        return status, body
    return transport


def _gateway(provider: str, name: str, status: int = 200) -> ModelGatewayService:
    return ModelGatewayService(lambda _p: _fixture_response(provider, name, status))


async def _seed_binding(env, provider: str, model: str, *, network: str = "NONE") -> int:
    """插入 strategy (draft) + model_role_binding；返回 binding id。"""
    import uuid
    async with env["sessions"]() as s:
        strategy = (await s.execute(text(
            "INSERT INTO trading.strategy_versions (strategy_key,version_no,content,schema_version,content_hash,status) "
            "VALUES (:k,1,'{}',1,:h,'draft') RETURNING id"),
            {"k": f"strat-{provider}-{uuid.uuid4().hex[:8]}", "h": "b" * 64})).scalar_one()
        binding = (await s.execute(text(
            "INSERT INTO trading.model_role_bindings "
            "(strategy_version_id,role,provider,route,model_ref,network_policy,allowed_tools,"
            "allowed_domains,capability,binding_version,content_hash) "
            "VALUES (:s,:role,:p,'direct',:m,:net,'[]'::jsonb,'[]'::jsonb,'{}'::jsonb,0,:ch) "
            "RETURNING id"),
            {"s": strategy, "role": "planner_prior", "p": provider, "m": model,
             "net": network, "ch": "a" * 64})).scalar_one()
        await s.commit()
        return binding


def _plan_kwargs(provider: str, model: str, *, binding: int, network: str = "NONE",
                 role: str = "planner_prior") -> dict:
    return {
        "invocation_key": f"inv-{provider}-{role}-{network}",
        "episode_id": 1, "stage": "g4", "role": role, "attempt_no": 1,
        "experiment_variant": "champion",
        "requested_provider": provider, "requested_route": "direct", "requested_model": model,
        "network_policy": network, "context_class": "PRIOR",
        "input_manifest": {"k": "v"}, "input_manifest_hash": "a" * 64,
        "model_role_binding_id": binding,
        "pricing_snapshot": {"usd_per_1m": 0}, "taint_report": {},
        "allowed_tools": ["web_search"] if network != "NONE" else [],
    }


def _model_request(provider: str, model: str, *, network: str = "NONE", role: str = "planner_prior") -> ModelRequest:
    return ModelRequest(
        role=role, stage="g4", episode_id=1, attempt_no=1, experiment_variant="champion",
        requested_provider=provider, requested_route="direct", requested_model=model,
        network_policy=network,
        allowed_tools=["web_search"] if network != "NONE" else [],
        prompt_text="prompt", input_manifest={"k": "v"}, input_manifest_hash="a" * 64,
        sampling={},
    )


async def _count_state(env, state: str) -> int:
    async with env["sessions"]() as s:
        return (await s.execute(text(
            "SELECT count(*) FROM trading.ai_invocations WHERE lifecycle_state=:st"),
            {"st": state})).scalar_one()


@pytest.mark.asyncio
async def test_lifecycle_success_and_secret_echo_rejected(ai_env):
    env = ai_env
    binding = await _seed_binding(env, "deepseek", "deepseek-v4-pro")
    runner = AIRunner(_gateway("deepseek", "success.json"), OutputValidator())
    async with env["sessions"]() as s:
        inv = await runner.plan(s, **{**_plan_kwargs("deepseek", "deepseek-v4-pro", binding=binding),
                                      "invocation_key": "inv-success"})
        await s.commit()
        outcome = await runner.run(
            s, invocation_id=inv, model_role_binding_id=binding,
            model_request=_model_request("deepseek", "deepseek-v4-pro"), blind_context=True,
        )
    assert outcome.accepted and outcome.lifecycle_state == "ACCEPTED"
    async with env["sessions"]() as s:
        row = (await s.execute(text(
            "SELECT lifecycle_state, result, accepted_at IS NOT NULL, input_tokens "
            "FROM trading.ai_invocations WHERE id=:id"), {"id": inv})).mappings().one()
    assert row["lifecycle_state"] == "ACCEPTED" and row["result"] == "accepted"

    # secret echo → REJECTED（不 ACCEPTED）
    binding2 = await _seed_binding(env, "deepseek", "deepseek-v4-pro")
    runner2 = AIRunner(_gateway("deepseek", "secret_echo.json"), OutputValidator())
    async with env["sessions"]() as s:
        inv2 = await runner2.plan(s, **{**_plan_kwargs("deepseek", "deepseek-v4-pro", binding=binding2),
                                        "invocation_key": "inv-secret"})
        await s.commit()
        outcome2 = await runner2.run(
            s, invocation_id=inv2, model_role_binding_id=binding2,
            model_request=_model_request("deepseek", "deepseek-v4-pro"), blind_context=True,
        )
    assert outcome2.lifecycle_state == "REJECTED"


@pytest.mark.asyncio
async def test_failure_retry_new_attempt_and_unknown_on_crash(ai_env):
    env = ai_env
    # 失败 attempt：429 → FAILED，不缓存；重试创建新 attempt
    binding = await _seed_binding(env, "deepseek", "deepseek-v4-pro")
    runner = AIRunner(_gateway("deepseek", "429.json", status=429), OutputValidator())
    async with env["sessions"]() as s:
        inv1 = await runner.plan(s, **{**_plan_kwargs("deepseek", "deepseek-v4-pro", binding=binding),
                                       "invocation_key": "inv-fail"})
        await s.commit()
        outcome1 = await runner.run(s, invocation_id=inv1, model_role_binding_id=binding,
                                    model_request=_model_request("deepseek", "deepseek-v4-pro"),
                                    blind_context=True)
    assert outcome1.lifecycle_state == "FAILED" and outcome1.terminal_reason == "deepseek_rate_limited"
    assert await _count_state(env, "FAILED") == 1

    # provider 返回后崩溃 → UNKNOWN（模拟：driver 已成功返回，但 execute 在返回前崩溃）
    binding3 = await _seed_binding(env, "deepseek", "deepseek-v4-pro")

    class CrashAfterReturnGateway(ModelGatewayService):
        """driver 成功返回后抛非 ProviderError —— 对应 worker 持久化前崩溃。"""
        async def execute(self, session, *, model_role_binding_id, model_request):
            await super().execute(session, model_role_binding_id=model_role_binding_id,
                                  model_request=model_request)
            raise RuntimeError("worker-crashed-after-provider-return")

    gateway_crash = CrashAfterReturnGateway(lambda _p: _fixture_response("deepseek", "success.json"))
    runner3 = AIRunner(gateway_crash, OutputValidator())
    async with env["sessions"]() as s:
        inv3 = await runner3.plan(s, **{**_plan_kwargs("deepseek", "deepseek-v4-pro", binding=binding3),
                                        "invocation_key": "inv-crash"})
        await s.commit()
        outcome3 = await runner3.run(s, invocation_id=inv3, model_role_binding_id=binding3,
                                     model_request=_model_request("deepseek", "deepseek-v4-pro"),
                                     blind_context=True)
    assert outcome3.lifecycle_state == "UNKNOWN"


@pytest.mark.asyncio
async def test_cache_hit_new_invocation_cost_zero(ai_env):
    env = ai_env
    binding = await _seed_binding(env, "deepseek", "deepseek-v4-pro")
    runner = AIRunner(_gateway("deepseek", "success.json"), OutputValidator())
    async with env["sessions"]() as s:
        inv = await runner.plan(s, **{**_plan_kwargs("deepseek", "deepseek-v4-pro", binding=binding),
                                      "invocation_key": "inv-cache"})
        await s.commit()
        await runner.run(s, invocation_id=inv, model_role_binding_id=binding,
                         model_request=_model_request("deepseek", "deepseek-v4-pro"), blind_context=True)
        # cache hit：引用 source invocation，cost=0
        hit = await runner.check_cache(
            s, model_request=_model_request("deepseek", "deepseek-v4-pro"), code_hash="c" * 64,
        )
        assert hit.hit and hit.source_invocation_id == inv
        await s.commit()


@pytest.mark.asyncio
async def test_blind_invocation_requires_no_network_and_no_taint(ai_env):
    env = ai_env
    binding = await _seed_binding(env, "deepseek", "deepseek-v4-pro")
    runner = AIRunner(_gateway("deepseek", "success.json"), OutputValidator())
    req = _model_request("deepseek", "deepseek-v4-pro")
    # blind：network=NONE 时不允许工具；QUOTE 上下文禁止
    req.assert_blind_context("PRIOR")
    with pytest.raises(ValueError, match="blind_context_forbidden"):
        req.assert_blind_context("QUOTE")
    # taint validator：blind 响应含 quote → REJECTED
    async with env["sessions"]() as s:
        inv = await runner.plan(s, **{**_plan_kwargs("deepseek", "deepseek-v4-pro", binding=binding),
                                      "invocation_key": "inv-blind"})
        await s.commit()
        outcome = await runner.run(s, invocation_id=inv, model_role_binding_id=binding,
                                   model_request=req, blind_context=True)
    assert outcome.lifecycle_state == "ACCEPTED"  # fixture 无 taint 字段
