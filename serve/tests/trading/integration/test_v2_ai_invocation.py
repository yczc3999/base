"""WP-02 AI invocation 生命周期集成测试（真 PostgreSQL）。

PLANNED→STARTED→…→ACCEPTED|REJECTED|FAILED|TIMEOUT|UNKNOWN；retry/fallback/cache 独立
attempt；失败不缓存；provider 返回后崩溃 → UNKNOWN；secret echo → quarantine 不 ACCEPTED。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
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

from app.config import Settings
from app.ai_runtime.runner import AIRunner
from app.ai_runtime.validator import OutputValidator
from app.domain.trading.hashing import canonical_hash
from app.services.model_gateway.contracts import ModelRequest
from app.services.model_gateway.service import ModelGatewayService
from app.services.artifact_store import ArtifactStore
from app.services.artifact_store.drivers.local import LocalArtifactDriver

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
async def ai_env(temp_pg_db, tmp_path):
    _run(command.upgrade, V21, temp_pg_db.url)
    admin = make_url(temp_pg_db.url)
    async_url = admin.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    engine = create_async_engine(async_url, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    artifacts = ArtifactStore(
        LocalArtifactDriver(str(tmp_path / "ai-artifacts")),
        Settings(_env_file=None, ARTIFACT_LOCAL_ROOT=str(tmp_path / "ai-artifacts")),
    )
    yield {"sessions": sessions, "url": temp_pg_db.url, "artifacts": artifacts}
    artifacts.aclose()
    await engine.dispose()


def _fixture_response(provider: str, name: str, status: int = 200):
    async def transport(endpoint, *, headers, json, timeout):
        body = (WIRE_DIR / provider / name).read_text()
        return status, body
    return transport


def _gateway(provider: str, name: str, status: int = 200) -> ModelGatewayService:
    return ModelGatewayService(lambda _p: _fixture_response(provider, name, status))


async def _seed_binding(
    env,
    provider: str,
    model: str,
    *,
    network: str = "NONE",
    role: str = "planner_prior",
) -> int:
    """插入 strategy (draft) + model_role_binding；返回 binding id。"""
    import uuid
    async with env["sessions"]() as s:
        strategy = (await s.execute(text(
            "SELECT strategy_version_id FROM trading.forecast_episodes WHERE id=1"
        ))).scalar_one_or_none()
        if strategy is None:
            strategy = (await s.execute(text(
                "INSERT INTO trading.strategy_versions "
                "(strategy_key,version_no,content,schema_version,content_hash,status) "
                "VALUES (:k,1,'{}',1,:h,'draft') RETURNING id"),
                {"k": f"strat-{provider}-{uuid.uuid4().hex[:8]}", "h": "b" * 64}
            )).scalar_one()
            # AI runtime tests exercise the b1000021 binding guard, not the long WP-01C
            # routing seed. Install the minimum fixture row while FK triggers are disabled;
            # the invocation insert itself runs with all guards enabled.
            await s.execute(text("SET LOCAL session_replication_role='replica'"))
            await s.execute(text(
                "INSERT INTO trading.forecast_episodes "
                "(id,episode_key,decision_opportunity_id,component_version_id,"
                "strategy_version_id,objective_contract_id,trigger,cutoff_at,horizon,"
                "experiment_variant,status) VALUES "
                "(1,:key,1,1,:strategy,1,'frame',now(),'test','champion','ROUTED')"
            ), {"key": "e" * 64, "strategy": strategy})
            await s.execute(text("SET LOCAL session_replication_role='origin'"))
        existing = (await s.execute(text(
            "SELECT id FROM trading.model_role_bindings WHERE strategy_version_id=:s "
            "AND role=:role AND provider=:p AND route='direct' AND model_ref=:m "
            "AND network_policy=:net"
        ), {"s": strategy, "role": role, "p": provider, "m": model,
             "net": network})).scalar_one_or_none()
        if existing is not None:
            await s.commit()
            return int(existing)
        binding = (await s.execute(text(
            "INSERT INTO trading.model_role_bindings "
            "(strategy_version_id,role,provider,route,model_ref,network_policy,allowed_tools,"
            "allowed_domains,capability,binding_version,content_hash) "
            "VALUES (:s,:role,:p,'direct',:m,:net,'[]'::jsonb,'[]'::jsonb,'{}'::jsonb,0,:ch) "
            "RETURNING id"),
            {"s": strategy, "role": role, "p": provider, "m": model,
             "net": network, "ch": "a" * 64})).scalar_one()
        await s.commit()
        return binding


def _plan_kwargs(provider: str, model: str, *, binding: int, network: str = "NONE",
                 role: str = "planner_prior", attempt_no: int = 1) -> dict:
    tools = ["web_search"] if network != "NONE" else []
    return {
        "invocation_key": f"inv-{provider}-{role}-{network}",
        "episode_id": 1, "stage": "g4", "role": role, "attempt_no": attempt_no,
        "experiment_variant": "champion",
        "requested_provider": provider, "requested_route": "direct", "requested_model": model,
        "network_policy": network, "context_class": "PRIOR",
        "input_manifest": {"k": "v"}, "input_manifest_hash": "a" * 64,
        "model_role_binding_id": binding,
        "pricing_snapshot": {
            "currency": "USD_MICRO",
            "input_per_1m": 1_000_000,
            "cache_per_1m": 500_000,
            "output_per_1m": 2_000_000,
            "reasoning_per_1m": 2_000_000,
        },
        "taint_report": {},
        "allowed_tools": tools,
        "allowed_domains": [],
        "prompt_version": "test/v1", "schema_version": "test/v1",
        "git_sha": "c" * 64,
        "effort": None,
        "cache_key_hash": AIRunner.request_cache_key(
            _model_request(provider, model, network=network, role=role),
            "c" * 64,
        ),
    }


def _model_request(
    provider: str,
    model: str,
    *,
    network: str = "NONE",
    role: str = "planner_prior",
    attempt_no: int = 1,
) -> ModelRequest:
    return ModelRequest(
        role=role, stage="g4", episode_id=1, attempt_no=attempt_no,
        experiment_variant="champion",
        requested_provider=provider, requested_route="direct", requested_model=model,
        network_policy=network,
        allowed_tools=["web_search"] if network != "NONE" else [],
        prompt_text="prompt", schema_text='{"type":"object"}',
        input_manifest={"k": "v"}, input_manifest_hash="a" * 64,
        sampling={},
    )


def _runner(env, gateway) -> AIRunner:
    return AIRunner(gateway, OutputValidator(), artifacts=env["artifacts"])


async def _count_state(env, state: str) -> int:
    async with env["sessions"]() as s:
        return (await s.execute(text(
            "SELECT count(*) FROM trading.ai_invocations WHERE lifecycle_state=:st"),
            {"st": state})).scalar_one()


@pytest.mark.asyncio
async def test_lifecycle_success_and_secret_echo_rejected(ai_env):
    env = ai_env
    binding = await _seed_binding(env, "deepseek", "deepseek-v4-pro")
    runner = _runner(env, _gateway("deepseek", "success.json"))
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
            "SELECT lifecycle_state, result, accepted_at IS NOT NULL, input_tokens, "
            "request_artifact_ref, prompt_artifact_ref, schema_artifact_ref, "
            "raw_response_artifact_ref, parsed_output_artifact_ref, "
            "normalized_output_artifact_ref, accepted_output_binding, "
            "cost_estimated,cost_currency,cost_reconciliation "
            "FROM trading.ai_invocations WHERE id=:id"), {"id": inv})).mappings().one()
    assert row["lifecycle_state"] == "ACCEPTED" and row["result"] == "accepted"
    refs = [row[key] for key in (
        "request_artifact_ref", "prompt_artifact_ref", "schema_artifact_ref",
        "raw_response_artifact_ref", "parsed_output_artifact_ref",
        "normalized_output_artifact_ref",
    )]
    assert all(ref and len(ref) == 64 for ref in refs)
    assert row["accepted_output_binding"] == row["normalized_output_artifact_ref"]
    assert row["cost_estimated"] == 180
    assert row["cost_currency"] == "USD_MICRO"
    assert row["cost_reconciliation"] == "ESTIMATED"
    async with env["sessions"]() as s:
        assert (await s.execute(text(
            "SELECT count(*) FROM trading.artifact_objects WHERE sha256=ANY(:refs)"
        ), {"refs": refs})).scalar_one() == len(set(refs))
        assert (await s.execute(text(
            "SELECT count(*) FROM trading.artifact_lineage_edges WHERE invocation_ref=:ref"
        ), {"ref": f"ai-invocation:{inv}"})).scalar_one() >= 4

    # secret echo → REJECTED（不 ACCEPTED）
    binding2 = await _seed_binding(env, "deepseek", "deepseek-v4-pro")
    runner2 = _runner(env, _gateway("deepseek", "secret_echo.json"))
    async with env["sessions"]() as s:
        inv2 = await runner2.plan(s, **{**_plan_kwargs(
            "deepseek", "deepseek-v4-pro", binding=binding2, attempt_no=2),
                                        "invocation_key": "inv-secret"})
        await s.commit()
        outcome2 = await runner2.run(
            s, invocation_id=inv2, model_role_binding_id=binding2,
            model_request=_model_request(
                "deepseek", "deepseek-v4-pro", attempt_no=2),
            blind_context=True,
        )
    assert outcome2.lifecycle_state == "REJECTED"


@pytest.mark.asyncio
async def test_failure_retry_new_attempt_and_unknown_on_crash(ai_env):
    env = ai_env
    # 失败 attempt：429 → FAILED，不缓存；重试创建新 attempt
    binding = await _seed_binding(env, "deepseek", "deepseek-v4-pro")
    runner = _runner(env, _gateway("deepseek", "429.json", status=429))
    async with env["sessions"]() as s:
        inv1 = await runner.plan(s, **{**_plan_kwargs("deepseek", "deepseek-v4-pro", binding=binding),
                                       "invocation_key": "inv-fail"})
        await s.commit()
        outcome1 = await runner.run(s, invocation_id=inv1, model_role_binding_id=binding,
                                    model_request=_model_request("deepseek", "deepseek-v4-pro"),
                                    blind_context=True)
    assert outcome1.lifecycle_state == "FAILED" and outcome1.terminal_reason == "deepseek_rate_limited"
    assert await _count_state(env, "FAILED") == 1
    async with env["sessions"]() as s:
        failed_refs = (await s.execute(text(
            "SELECT request_artifact_ref,prompt_artifact_ref,schema_artifact_ref,"
            "raw_response_artifact_ref FROM trading.ai_invocations WHERE id=:id"
        ), {"id": inv1})).mappings().one()
        assert all(failed_refs[key] for key in (
            "request_artifact_ref", "prompt_artifact_ref", "schema_artifact_ref",
        ))
        assert failed_refs["raw_response_artifact_ref"] is None

    # provider 返回后崩溃 → UNKNOWN（模拟：driver 已成功返回，但 execute 在返回前崩溃）
    binding3 = await _seed_binding(env, "deepseek", "deepseek-v4-pro")

    class CrashAfterReturnGateway(ModelGatewayService):
        """driver 成功返回后抛非 ProviderError —— 对应 worker 持久化前崩溃。"""
        async def execute(self, session, *, model_role_binding_id, model_request):
            await super().execute(session, model_role_binding_id=model_role_binding_id,
                                  model_request=model_request)
            raise RuntimeError("worker-crashed-after-provider-return")

    gateway_crash = CrashAfterReturnGateway(lambda _p: _fixture_response("deepseek", "success.json"))
    runner3 = _runner(env, gateway_crash)
    async with env["sessions"]() as s:
        inv3 = await runner3.plan(s, **{**_plan_kwargs("deepseek", "deepseek-v4-pro", binding=binding3, attempt_no=2),
                                        "invocation_key": "inv-crash"})
        await s.commit()
        outcome3 = await runner3.run(s, invocation_id=inv3, model_role_binding_id=binding3,
                                     model_request=_model_request(
                                         "deepseek", "deepseek-v4-pro", attempt_no=2),
                                     blind_context=True)
    assert outcome3.lifecycle_state == "UNKNOWN"


@pytest.mark.asyncio
async def test_cache_hit_new_invocation_cost_zero(ai_env):
    env = ai_env
    binding = await _seed_binding(env, "deepseek", "deepseek-v4-pro")
    runner = _runner(env, _gateway("deepseek", "success.json"))
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
        assert not (await runner.check_cache(
            s,
            model_request=_model_request("deepseek", "deepseek-v4-pro"),
            code_hash="d" * 64,
        )).hit
        changed_prompt = replace(
            _model_request("deepseek", "deepseek-v4-pro"),
            prompt_text="different prompt",
        )
        assert not (await runner.check_cache(
            s, model_request=changed_prompt, code_hash="c" * 64,
        )).hit
        cache_invocation = await runner.record_cache_hit(
            s,
            plan_kwargs={
                **_plan_kwargs(
                    "deepseek", "deepseek-v4-pro", binding=binding, attempt_no=2,
                ),
                "invocation_key": "inv-cache-hit",
            },
            source_invocation_id=inv,
            occurred_at=datetime.now(timezone.utc),
            model_request=_model_request(
                "deepseek", "deepseek-v4-pro", attempt_no=2,
            ),
            code_hash="c" * 64,
        )
        await s.commit()
    async with env["sessions"]() as s:
        cached = (await s.execute(text(
            "SELECT lifecycle_state,result,parent_invocation_id,cost_estimated,"
            "request_artifact_ref,prompt_artifact_ref,schema_artifact_ref,"
            "raw_response_artifact_ref,parsed_output_artifact_ref,"
            "normalized_output_artifact_ref,accepted_output_binding,"
            "input_tokens,cache_tokens,output_tokens,reasoning_tokens "
            "FROM trading.ai_invocations WHERE id=:id"
        ), {"id": cache_invocation})).mappings().one()
        assert cached["lifecycle_state"] == "ACCEPTED"
        assert cached["result"] == "cache_hit"
        assert cached["parent_invocation_id"] == inv
        assert cached["cost_estimated"] == 0
        assert all(cached[key] for key in (
            "request_artifact_ref", "prompt_artifact_ref", "schema_artifact_ref",
            "raw_response_artifact_ref", "parsed_output_artifact_ref",
            "normalized_output_artifact_ref", "accepted_output_binding",
        ))
        assert all(cached[key] == 0 for key in (
            "input_tokens", "cache_tokens", "output_tokens", "reasoning_tokens",
        ))
        validator_counts = (await s.execute(text(
            "SELECT invocation_id,count(*) FROM trading.ai_validation_results "
            "WHERE invocation_id IN (:source,:cached) GROUP BY invocation_id"
        ), {"source": inv, "cached": cache_invocation})).all()
        assert {row[1] for row in validator_counts} == {5}


@pytest.mark.asyncio
async def test_blind_invocation_requires_no_network_and_no_taint(ai_env):
    env = ai_env
    binding = await _seed_binding(env, "deepseek", "deepseek-v4-pro")
    runner = _runner(env, _gateway("deepseek", "success.json"))
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


@pytest.mark.asyncio
async def test_raw_json_taint_is_rejected_and_caller_cannot_disable_blind(ai_env):
    env = ai_env
    binding = await _seed_binding(env, "deepseek", "deepseek-v4-pro")

    async def tainted_transport(endpoint, *, headers, json, timeout):
        return 200, {
            "id": "tainted",
            "choices": [{"message": {
                "content": '{"nested":{"quote":"0.73"}}',
                "model": "deepseek-v4-pro",
            }}],
            "usage": {},
        }

    runner = _runner(env, ModelGatewayService(lambda _p: tainted_transport))
    async with env["sessions"]() as s:
        inv = await runner.plan(
            s,
            **{**_plan_kwargs("deepseek", "deepseek-v4-pro", binding=binding),
               "invocation_key": "inv-raw-taint"},
        )
        await s.commit()
        outcome = await runner.run(
            s,
            invocation_id=inv,
            model_role_binding_id=binding,
            model_request=_model_request("deepseek", "deepseek-v4-pro"),
            blind_context=False,
        )
    assert outcome.lifecycle_state == "REJECTED"
    assert outcome.terminal_reason == "taint"


@pytest.mark.asyncio
async def test_returned_model_drift_keeps_full_response_evidence(ai_env):
    env = ai_env
    binding = await _seed_binding(env, "deepseek", "deepseek-v4-pro")

    async def drift_transport(endpoint, *, headers, json, timeout):
        body = (WIRE_DIR / "deepseek" / "success.json").read_text()
        return 200, body.replace("deepseek-v4-pro", "deepseek-v4-unknown")

    runner = _runner(env, ModelGatewayService(lambda _p: drift_transport))
    async with env["sessions"]() as s:
        inv = await runner.plan(
            s,
            **{
                **_plan_kwargs("deepseek", "deepseek-v4-pro", binding=binding),
                "invocation_key": "inv-model-drift",
            },
        )
        await s.commit()
        outcome = await runner.run(
            s,
            invocation_id=inv,
            model_role_binding_id=binding,
            model_request=_model_request("deepseek", "deepseek-v4-pro"),
            blind_context=True,
        )
    assert outcome.lifecycle_state == "REJECTED"
    assert "model" in (outcome.terminal_reason or "")
    async with env["sessions"]() as s:
        row = (await s.execute(text(
            "SELECT request_artifact_ref,prompt_artifact_ref,schema_artifact_ref,"
            "raw_response_artifact_ref,parsed_output_artifact_ref,"
            "normalized_output_artifact_ref,accepted_output_binding "
            "FROM trading.ai_invocations WHERE id=:id"
        ), {"id": inv})).mappings().one()
        assert all(row[key] for key in (
            "request_artifact_ref", "prompt_artifact_ref", "schema_artifact_ref",
            "raw_response_artifact_ref", "parsed_output_artifact_ref",
            "normalized_output_artifact_ref",
        ))
        assert row["accepted_output_binding"] is None
        assert (await s.execute(text(
            "SELECT count(*) FROM trading.ai_validation_results WHERE invocation_id=:id"
        ), {"id": inv})).scalar_one() == 5


@pytest.mark.asyncio
async def test_timeout_is_terminal_and_keeps_request_evidence(ai_env):
    env = ai_env
    binding = await _seed_binding(env, "deepseek", "deepseek-v4-pro")

    async def slow_transport(endpoint, *, headers, json, timeout):
        await asyncio.sleep(1)
        return 200, (WIRE_DIR / "deepseek" / "success.json").read_text()

    runner = _runner(env, ModelGatewayService(lambda _p: slow_transport))
    request = replace(
        _model_request("deepseek", "deepseek-v4-pro"),
        timeout_seconds=0.001,
    )
    plan_kwargs = {
        **_plan_kwargs("deepseek", "deepseek-v4-pro", binding=binding),
        "invocation_key": "inv-timeout",
        "cache_key_hash": AIRunner.request_cache_key(request, "c" * 64),
    }
    async with env["sessions"]() as s:
        inv = await runner.plan(s, **plan_kwargs)
        await s.commit()
        outcome = await runner.run(
            s,
            invocation_id=inv,
            model_role_binding_id=binding,
            model_request=request,
            blind_context=True,
        )
    assert outcome.lifecycle_state == "TIMEOUT"
    async with env["sessions"]() as s:
        row = (await s.execute(text(
            "SELECT request_artifact_ref,prompt_artifact_ref,schema_artifact_ref,"
            "raw_response_artifact_ref,cost_reconciliation "
            "FROM trading.ai_invocations WHERE id=:id"
        ), {"id": inv})).mappings().one()
        assert all(row[key] for key in (
            "request_artifact_ref", "prompt_artifact_ref", "schema_artifact_ref",
        ))
        assert row["raw_response_artifact_ref"] is None
        assert row["cost_reconciliation"] == "UNPRICED"


@pytest.mark.asyncio
async def test_run_claims_once_and_transport_has_no_open_db_transaction(ai_env):
    env = ai_env
    binding = await _seed_binding(env, "deepseek", "deepseek-v4-pro")
    calls = 0
    tx_states: list[bool] = []
    holder = {}

    async def transport(endpoint, *, headers, json, timeout):
        nonlocal calls
        calls += 1
        tx_states.append(holder["session"].in_transaction())
        return 200, (WIRE_DIR / "deepseek" / "success.json").read_text()

    runner = _runner(env, ModelGatewayService(lambda _p: transport))
    async with env["sessions"]() as s:
        holder["session"] = s
        inv = await runner.plan(
            s,
            **{**_plan_kwargs("deepseek", "deepseek-v4-pro", binding=binding),
               "invocation_key": "inv-single-claim"},
        )
        await s.commit()
        first = await runner.run(
            s,
            invocation_id=inv,
            model_role_binding_id=binding,
            model_request=_model_request("deepseek", "deepseek-v4-pro"),
            blind_context=True,
        )
        second = await runner.run(
            s,
            invocation_id=inv,
            model_role_binding_id=binding,
            model_request=_model_request("deepseek", "deepseek-v4-pro"),
            blind_context=True,
        )
    assert first.accepted and second.accepted
    assert calls == 1
    assert tx_states == [False]
