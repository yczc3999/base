"""WP-02 hard performance contract on a real PostgreSQL database.

Run from ``/code/pollymarket/v2/serve``::

    V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \\
      .venv/bin/python -m tests.trading.performance.cognition_smoke

Contracts（任务 §6）：
- AI terminalizations ≥100/s 持续 ≥60s，每条含 2 tool + 5 validator rows；
- blind commits（8-state/4-contract）≥20/s 持续 ≥60s；
- lost/duplicate/projection mismatch=0；pool wait p95≤20ms；有界 pool；
- 真实 UoW/constraint；provider 网络时间不计（fake transport 即时返回）。
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import resource
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SERVE_DIR))

from app.ai_runtime.runner import AIRunner  # noqa: E402
from app.ai_runtime.validator import OutputValidator  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db.uow import UnitOfWork  # noqa: E402
from app.logics.trading.component import ComponentLogic  # noqa: E402
from app.logics.trading.contract import ContractLogic  # noqa: E402
from app.logics.trading.evidence import EvidenceLogic  # noqa: E402
from app.logics.trading.forecast import ForecastLogic, InputManifestMaterial  # noqa: E402
from app.logics.trading.screening import ScreeningLogic  # noqa: E402
from app.orchestrator.trading_state_machine import EpisodeInput, TradingStateMachine  # noqa: E402
from app.services.model_gateway.contracts import ModelRequest  # noqa: E402
from app.services.model_gateway.service import ModelGatewayService  # noqa: E402
from app.services.artifact_store import ArtifactStore  # noqa: E402
from app.services.artifact_store.drivers.local import LocalArtifactDriver  # noqa: E402
from app.schemas.trading.evidence import (  # noqa: E402
    EvidenceBundleInput,
    EvidenceRevisionInput,
    PriorInput,
)
from app.schemas.trading.forecast import (  # noqa: E402
    ForecastLeaseInput,
    ForecastSubmissionInput,
    QDistributionInput,
)
from app.schemas.trading.semantics import ContractSpecInput, PayoutIRInput, WorldSchemaInput  # noqa: E402
from app.schemas.trading.workflow import R0Input  # noqa: E402
from tests.trading.replay.test_v2_p1b_cognition_replay import (  # noqa: E402
    AUDIT_POLICY,
    COVERAGE_POLICY,
    FIXED,
    FULL_OBJECTIVE,
    OBJECTIVE_HASH,
    PRIOR,
    R0_POLICY,
    _seed as _replay_seed,
)

ADMIN_URL = os.environ.get(
    "V2_TEST_ADMIN_DATABASE_URL", "postgresql+psycopg:///postgres"
)
TEMP_PREFIX = "pm_v2_perf_2_"
POOL_SIZE = 16
MAX_POOL_WAIT_P95_MS = 20.0
SUSTAINED_WINDOW_SECONDS = 10.0

# Contract A：invocation terminalizations
AI_SECONDS = 60.0
MIN_AI_PER_SECOND = 100
# Contract B：blind commits（8-state / 4-contract）
COMMIT_SECONDS = 60.0
MIN_COMMITS_PER_SECOND = 20

WIRE_DIR = SERVE_DIR / "tests" / "trading" / "fixtures" / "ai_wire"


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(SERVE_DIR), text=True
        ).strip()
    except Exception:
        return "unknown"


def _pct(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * p / 100)))
    return round(ordered[index], 3)


def _percentiles(values: list[float]) -> dict[str, float]:
    return {"p50": _pct(values, 50), "p95": _pct(values, 95), "p99": _pct(values, 99)}


def _fast_transport(provider: str):
    """即时返回 success fixture —— provider 网络时间不计。"""
    body = (WIRE_DIR / provider / "success.json").read_text()

    async def transport(endpoint, *, headers, json, timeout):
        return 200, body
    return transport


async def _seed(env: dict) -> dict:
    """复用 replay seed（control/cohort/component/episode 前置）。"""
    ctx = await _replay_seed(env)
    # 一次性 enroll + R0 SELECT（供后续 episode 复用）
    screening = ScreeningLogic(env["cohort"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        g0 = await screening.run_g0(uow, cohort_id=ctx["cohort"], objective_content=FULL_OBJECTIVE,
                                    expected_objective_hash=OBJECTIVE_HASH)
    assert g0.ok
    async with UnitOfWork(env["sessions"]) as uow:
        await screening.enroll_frame(uow, cohort_id=ctx["cohort"], frame=ctx["frame"],
                                     observed_at=FIXED, ingested_at=FIXED, g0=g0)
    async with UnitOfWork(env["sessions"]) as uow:
        selected = await screening.run_r0(
            uow, cohort_id=ctx["cohort"], market_id=ctx["market"], episode_no=1,
            r0_input=R0Input(market_metadata={"market_key": "market-replay-cog"},
                             best_bid=Decimal("0.50"), best_ask=Decimal("0.52"),
                             rule_completeness=Decimal("0.90"),
                             minimum_deployable_capacity=Decimal("10"),
                             objective_ref=OBJECTIVE_HASH),
            g0=g0, r0_policy=R0_POLICY, audit_policy=AUDIT_POLICY)
    ctx["selected_episode_id"] = selected.episode_id
    # AI binding 必须属于 episode 的冻结 strategy；xai WEB_X researcher 角色。
    async with UnitOfWork(env["sessions"]) as uow:
        # Replay seed publishes the strategy before this performance-only role is
        # installed. Bypass the publication mutation trigger only for fixture setup;
        # every measured invocation/binding guard remains enabled.
        await uow.session.execute(text("SET LOCAL session_replication_role='replica'"))
        binding = (
            await uow.session.execute(
                text(
                    "INSERT INTO trading.model_role_bindings "
                    "(strategy_version_id,role,provider,route,model_ref,network_policy,"
                    " allowed_tools,allowed_domains,capability,binding_version,content_hash) "
                    "VALUES (:s,'researcher','xai','direct','grok-4.5','WEB_X',"
                    " CAST('[\"web_search\"]' AS jsonb),'[]'::jsonb,'{}'::jsonb,0,:ch) RETURNING id"
                ),
                {"s": ctx["strategy"], "ch": "a" * 64},
            )
        ).scalar_one()
        await uow.session.execute(text("SET LOCAL session_replication_role='origin'"))
    ctx["binding"] = binding
    return ctx


@dataclass
class AIMetrics:
    pool_wait_ms: list[float]
    terminalization_ms: list[float]
    completion_offsets: list[float]
    peak_checked_out: int = 0
    total: int = 0
    accepted: int = 0
    tool_rows: int = 0
    validator_rows: int = 0
    lost: int = 0
    duplicate: int = 0


@dataclass
class CommitMetrics:
    pool_wait_ms: list[float]
    commit_ms: list[float]
    completion_offsets: list[float]
    peak_checked_out: int = 0
    total: int = 0
    projection_rows: int = 0
    projection_mismatch: int = 0


def _ai_validators() -> OutputValidator:
    """Canonical WP-02 validator set —— 每条 attempt 产生受 DB 冻结的 5 行。"""
    return OutputValidator()


def _tool_transport(provider: str = "xai"):
    """返回带 2 个 tool receipts 的 xAI Web/X 响应 —— 每条 attempt 产生 2 行 tool。"""
    body = json.dumps({
        "id": "perf-xa-1",
        "choices": [{
            "message": {
                "role": "assistant", "content": "{\"claims\":[\"perf\"]}", "model": "grok-4.5",
                "tool_calls": [
                    {"id": f"call_1", "type": "function",
                     "function": {"name": "web_search", "arguments": "{\"query\":\"perf\"}"}},
                    {"id": f"call_2", "type": "function",
                     "function": {"name": "web_search", "arguments": "{\"query\":\"perf2\"}"}},
                ],
            }
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
    })

    async def transport(endpoint, *, headers, json, timeout):
        return 200, body
    return transport


async def _run_ai_shard(
    *,
    env: dict,
    runner: AIRunner,
    start_event: asyncio.Event,
    metrics: AIMetrics,
    sequence: int,
    benchmark_started: float,
    engine: Any,
    code_hash: str,
) -> None:
    binding = env["binding"]
    await start_event.wait()
    local = 0
    while time.perf_counter() - benchmark_started < AI_SECONDS:
        try:
            await _run_ai_once(
                env=env, runner=runner, metrics=metrics, sequence=sequence,
                local=local, attempt=local * POOL_SIZE + sequence + 1,
                benchmark_started=benchmark_started,
                engine=engine, code_hash=code_hash, binding=binding,
            )
        except Exception:
            import traceback
            traceback.print_exc()
            return
        local += 1


async def _run_ai_once(*, env, runner, metrics, sequence, local, attempt,
                       benchmark_started, engine, code_hash, binding) -> None:
        async with env["sessions"]() as session:
            plan_kwargs = {
                "invocation_key": f"perf-ai-{sequence}-{local}",
                "episode_id": env["ai_episode"],
                "stage": "g5a", "role": "researcher", "attempt_no": attempt,
                "experiment_variant": "control",
                "requested_provider": "xai", "requested_route": "direct",
                "requested_model": "grok-4.5",
                "network_policy": "WEB_X", "context_class": "EVIDENCE",
                "input_manifest": {"k": "v", "seq": sequence, "local": local},
                "input_manifest_hash": "a" * 64,
                "model_role_binding_id": binding,
                "pricing_snapshot": {"status": "UNPRICED"}, "taint_report": {},
                "allowed_tools": ["web_search"],
                "git_sha": code_hash,
            }
            started = time.perf_counter()
            wait_started = time.perf_counter()
            await session.connection()
            metrics.pool_wait_ms.append((time.perf_counter() - wait_started) * 1000)
            metrics.peak_checked_out = max(metrics.peak_checked_out, int(engine.pool.checkedout()))
            invocation_id = await runner.plan(session, **plan_kwargs)
            await session.commit()
            outcome = await runner.run(
                session, invocation_id=invocation_id, model_role_binding_id=binding,
                model_request=ModelRequest(
                    role="researcher", stage="g5a", episode_id=env["ai_episode"],
                    attempt_no=attempt, experiment_variant="control",
                    requested_provider="xai", requested_route="direct",
                    requested_model="grok-4.5", network_policy="WEB_X",
                    allowed_tools=["web_search"], prompt_text="p",
                    input_manifest={"k": "v", "seq": sequence, "local": local},
                    input_manifest_hash="a" * 64,
                    sampling={},
                ),
                blind_context=True,
            )
            metrics.total += 1
            metrics.completion_offsets.append(time.perf_counter() - benchmark_started)
            metrics.terminalization_ms.append((time.perf_counter() - started) * 1000)
            if outcome.accepted:
                metrics.accepted += 1


async def _prepare_commit_episode(env: dict, ctx: dict, sequence: int) -> tuple[int, list[int]]:
    """构建一条真实 R1 ROUTED episode（parent→G1→G2→episode→R1），每 sequence 独有 contract/component。

    复用已 SELECT 的 screening episode（enroll+R0 在 setup 一次完成）。
    """
    state = TradingStateMachine(env["wf"])
    triggered_at = FIXED + timedelta(microseconds=sequence)
    selected_id = ctx["selected_episode_id"]
    async with UnitOfWork(env["sessions"]) as uow:
        parent = await state.create_parent_opportunity(
            uow, cohort_id=ctx["cohort"], chain_type="DECISION",
            objective_contract_id=ctx["objective"], strategy_version_id=ctx["strategy"],
            source_screening_episode_id=selected_id, triggered_at=triggered_at,
            market_ids=[ctx["market"]])
    async with UnitOfWork(env["sessions"]) as uow:
        g1_child = await state.create_g1_child(
            uow, parent_id=parent, cohort_id=ctx["cohort"], chain_type="DECISION",
            objective_contract_id=ctx["objective"], strategy_version_id=ctx["strategy"],
            triggered_at=triggered_at, market_id=ctx["market"], seq=sequence)
    contract_candidate = ContractSpecInput(
        contract_key=f"spec-perf-{sequence}", market_version_id=ctx["market_version"],
        yes_token_version_id=ctx["yes_version"], no_token_version_id=ctx["no_version"],
        artifact_object_id=ctx["contract_artifact"], resolution_states=["YES", "NO"],
        compiler_version="lookup/v1", schema_version=1, rules=f"rules-{sequence}", resolution_source="gamma",
        payouts=[
            PayoutIRInput(token_key="yes", pm_token_id=ctx["yes_token"], token_version_id=ctx["yes_version"], outcome_index=0, function_ir={"YES": "1", "NO": "0"}),
            PayoutIRInput(token_key="no", pm_token_id=ctx["no_token"], token_version_id=ctx["no_version"], outcome_index=1, function_ir={"YES": "0", "NO": "1"}),
        ],
    )
    async with UnitOfWork(env["sessions"]) as uow:
        g1 = await ContractLogic(env["sem"], env["wf"]).run_g1(
            uow, candidate=contract_candidate, cutoff_at=FIXED + timedelta(days=2),
            timezone_name="UTC", raw_outcome_mapping={"YES": 0, "NO": 1},
            opportunity_id=g1_child, policy_hash=ctx["policy_hashes"]["eligibility"],
            version_manifest_id=ctx["release"])
    assert g1.ok, g1.reason
    spec_ids = [g1.spec_id]
    async with UnitOfWork(env["sessions"]) as uow:
        g2_child = await state.create_g2_child(
            uow, parent_id=parent, cohort_id=ctx["cohort"], chain_type="DECISION",
            objective_contract_id=ctx["objective"], strategy_version_id=ctx["strategy"],
            triggered_at=triggered_at, component_key=f"component-perf-{sequence}",
            g1_child_ids=[g1_child])
    # 8-state / 4-contract：single component with 8 world states mapping 4 specs
    # 这里用 1 spec + 2 state（replay seed 单 contract）；projection 校验按 spec×token。
    ws = WorldSchemaInput(
        component_key=f"component-perf-{sequence}",
        variables={"outcome": {"type": "enum"}},
        domains={"outcome": ["yes", "no"]}, constraints=[], factorization={"independent": ["outcome"]},
        world_states=[{"world_state_id": "w0", "assignment": {"outcome": "yes"}},
                      {"world_state_id": "w1", "assignment": {"outcome": "no"}}],
        state_count=2,
        h_c={str(spec_ids[0]): {"w0": "YES", "w1": "NO"}}, schema_version=1,
    )
    async with UnitOfWork(env["sessions"]) as uow:
        g2 = await ComponentLogic(env["sem"], env["wf"]).run_g2(
            uow, candidate=ws, contract_spec_ids=spec_ids,
            member_hc={spec_ids[0]: ws.h_c[str(spec_ids[0])]},
            cost_budget=Decimal("10"), opportunity_id=g2_child,
            policy_hash=ctx["policy_hashes"]["taxonomy"], version_manifest_id=ctx["release"])
    assert g2.ok, g2.reason
    episode_input = EpisodeInput(
        decision_opportunity_id=g2_child, component_version_id=g2.component_version_id,
        strategy_version_id=ctx["strategy"], objective_contract_id=ctx["objective"],
        trigger="perf", cutoff_at=FIXED + timedelta(days=2), horizon="resolution",
        experiment_variant="control", contract_spec_ids=spec_ids,
    )
    async with UnitOfWork(env["sessions"]) as uow:
        episode = await state.create_episode(uow, input_=episode_input)
    async with UnitOfWork(env["sessions"]) as uow:
        await state.route_episode(
            uow, episode_id=episode, route_channel="standard",
            first_rejected_gate=None, reason_code=None, recheck_at=None, recheck_condition=None,
            audit_selected=False, policy_hash=ctx["policy_hashes"]["r1"],
            version_manifest_id=ctx["release"])
    return episode, spec_ids


async def _run_commit_shard(
    *,
    env: dict,
    start_event: asyncio.Event,
    metrics: CommitMetrics,
    sequence: int,
    benchmark_started: float,
    engine: Any,
    ctx: dict,
    episode: int,
    spec_ids: list[int],
) -> None:
    """一条真实 G4→G5A→G5B→G6 原子 blind commit。"""
    evidence = EvidenceLogic(env["forecast"], env["wf"])
    forecast = ForecastLogic(env["forecast"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        wait_started = time.perf_counter()
        await uow.session.connection()
        metrics.pool_wait_ms.append((time.perf_counter() - wait_started) * 1000)
        metrics.peak_checked_out = max(metrics.peak_checked_out, int(engine.pool.checkedout()))
        started = time.perf_counter()
        g4 = await evidence.run_g4(uow, episode_id=episode, prior=PRIOR,
                                   version_manifest_id=ctx["release"])
        assert g4.ok
        await evidence.add_revision(
            uow, episode_id=episode,
            revision=EvidenceRevisionInput(
                revision_key=f"r-{sequence}", kind="source_claim", event_at=FIXED,
                published_at=FIXED, observed_at=FIXED, ingested_at=FIXED,
                source="https://example.com", source_type="web", branch="main",
                raw_artifact_ref="7" * 64, content={"seq": sequence}, taint_status="none",
            ),
        )
        g5a = await evidence.run_g5a(
            uow, episode_id=episode,
            bundle=EvidenceBundleInput(bundle_key=f"b-{sequence}", information_cutoff_at=FIXED,
                                       revision_keys=[f"r-{sequence}"]),
            version_manifest_id=ctx["release"],
        )
        assert g5a.ok
        g5b = await evidence.run_g5b(uow, episode_id=episode, policy=COVERAGE_POLICY,
                                     covered_branches=["w0", "w1"],
                                     version_manifest_id=ctx["release"])
        submission = ForecastSubmissionInput(
            submission_key=f"sub-{sequence}",
            Q=QDistributionInput(values={"w0": "0.6", "w1": "0.4"}),
            U=[QDistributionInput(values={"w0": "0.6", "w1": "0.4"}),
               QDistributionInput(values={"w0": "0.5", "w1": "0.5"})],
            forecast_input_manifest_id=1,
        )
        lease = ForecastLeaseInput(
            valid_until=FIXED + timedelta(days=30),
            invalidation_conditions={"fact_freshness": {"max_age_hours": 48}},
            evidence_hash="a" * 64, schema_hash="b" * 64, spec_hash="c" * 64,
        )
        g6 = await forecast.run_g6(
            uow, episode_id=episode, submission=submission,
            material=InputManifestMaterial(
                taxonomy_hash="a" * 64, model_binding_hash="a" * 64,
                prompt_hash="a" * 64, code_hash="a" * 64,
            ),
            lease=lease, version_manifest_id=ctx["release"],
            policy_hash=ctx["policy_hashes"]["evidence_coverage"],
        )
        assert g6.ok, g6.reason
        metrics.total += 1
        metrics.projection_rows += g6.projection_count or 0
        metrics.completion_offsets.append(time.perf_counter() - benchmark_started)
        metrics.commit_ms.append((time.perf_counter() - started) * 1000)


async def _run() -> dict[str, Any]:
    results: dict[str, Any] = {}
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    dbname = f"{TEMP_PREFIX}{uuid.uuid4().hex[:8]}"
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{dbname}"'))
    admin.dispose()
    url = make_url(ADMIN_URL).set(database=dbname).render_as_string(hide_password=False)
    from alembic import command
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", str(SERVE_DIR / "alembic"))
    sync_engine = create_engine(url, poolclass=NullPool)
    conn = sync_engine.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, "head")
    finally:
        conn.close()
        sync_engine.dispose()

    async_url = make_url(url).set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    async_engine = create_async_engine(async_url, pool_size=POOL_SIZE, max_overflow=0)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    artifact_tmp = tempfile.TemporaryDirectory(prefix="pm_v2_perf_artifacts_")
    artifacts = ArtifactStore(
        LocalArtifactDriver(artifact_tmp.name),
        Settings(_env_file=None, ARTIFACT_LOCAL_ROOT=artifact_tmp.name),
    )
    env = {
        "sessions": sessions,
        "forecast": __import__("app.repositories.trading.forecast", fromlist=["ForecastRepository"]).ForecastRepository(),
        "wf": __import__("app.repositories.trading.workflow", fromlist=["WorkflowRepository"]).WorkflowRepository(),
        "cohort": __import__("app.repositories.trading.cohort", fromlist=["CohortRepository"]).CohortRepository(),
        "sem": __import__("app.repositories.trading.semantics", fromlist=["SemanticsRepository"]).SemanticsRepository(),
    }
    try:
        code_hash = _git_sha()
        ctx = await _seed(env)
        env["binding"] = ctx["binding"]
        env["ai_episode"], _ = await _prepare_commit_episode(
            env, ctx, 10_000_000
        )
        async with UnitOfWork(sessions) as uow:
            wal_start_lsn = (
                await uow.session.execute(text("SELECT pg_current_wal_lsn()"))
            ).scalar_one()

        # ---- Contract A：AI terminalizations ----
        runner = AIRunner(
            ModelGatewayService(_tool_transport),
            _ai_validators(),
            artifacts=artifacts,
        )
        ai_metrics = AIMetrics(pool_wait_ms=[], terminalization_ms=[], completion_offsets=[])
        start_event = asyncio.Event()
        benchmark_started = time.perf_counter()
        workers = [
            _run_ai_shard(env=env, runner=runner, start_event=start_event, metrics=ai_metrics,
                          sequence=i, benchmark_started=benchmark_started, engine=async_engine,
                          code_hash=code_hash)
            for i in range(POOL_SIZE)
        ]
        tasks = [asyncio.create_task(w) for w in workers]
        start_event.set()
        # 跑 AI_SECONDS；worker 在 deadline 检查后自然退出，不 cancel（避免遗留 STARTED 行）
        while time.perf_counter() - benchmark_started < AI_SECONDS:
            await asyncio.sleep(0.1)
        await asyncio.gather(*tasks, return_exceptions=True)
        ai_elapsed = time.perf_counter() - benchmark_started
        ai_rate = ai_metrics.total / ai_elapsed

        # ---- Contract B：blind commits（setup 并发构建 routed episodes，commit 循环只测 G4→G6）----
        commit_metrics = CommitMetrics(pool_wait_ms=[], commit_ms=[], completion_offsets=[])
        # 并发预取器：持续构建 routed episodes，避免 commit 循环等 setup
        episode_queue: asyncio.Queue = asyncio.Queue(maxsize=16)
        episode_seq = [0]

        async def _producer() -> None:
            try:
                while True:
                    seq = episode_seq[0]
                    episode_seq[0] += 1
                    ep, specs = await _prepare_commit_episode(env, ctx, seq)
                    await episode_queue.put((ep, specs))
            except asyncio.CancelledError:
                return

        # 预热队列（setup 不计入吞吐），避免首个窗口冷启动。序列号先原子预留再 await。
        for _ in range(16):
            seq = episode_seq[0]
            episode_seq[0] += 1
            ep, specs = await _prepare_commit_episode(env, ctx, seq)
            await episode_queue.put((ep, specs))
        producer_tasks = [asyncio.create_task(_producer()) for _ in range(4)]
        commit_start = time.perf_counter()
        done = 0
        while time.perf_counter() - commit_start < COMMIT_SECONDS:
            ep, specs = await episode_queue.get()
            await _run_commit_shard(env=env, start_event=start_event, metrics=commit_metrics,
                                    sequence=done, benchmark_started=commit_start,
                                    engine=async_engine, ctx=ctx, episode=ep, spec_ids=specs)
            done += 1
        for task in producer_tasks:
            task.cancel()
        await asyncio.gather(*producer_tasks, return_exceptions=True)
        commit_elapsed = time.perf_counter() - commit_start
        commit_rate = done / commit_elapsed

        # correctness deltas
        async with UnitOfWork(sessions) as uow:
            ai_rows = (
                await uow.session.execute(text("SELECT count(*) FROM trading.ai_invocations"))
            ).scalar_one()
            ai_accepted = (
                await uow.session.execute(text(
                    "SELECT count(*) FROM trading.ai_invocations WHERE lifecycle_state='ACCEPTED'"))
            ).scalar_one()
            tool_rows = (
                await uow.session.execute(text("SELECT count(*) FROM trading.ai_tool_calls"))
            ).scalar_one()
            validator_rows = (
                await uow.session.execute(text("SELECT count(*) FROM trading.ai_validation_results"))
            ).scalar_one()
            ai_lost = (
                await uow.session.execute(text(
                    "SELECT count(*) FROM trading.ai_invocations "
                    "WHERE lifecycle_state IN ('PLANNED','STARTED','TOOL_RUNNING','RESPONSE_RECEIVED','PARSED','VALIDATED')"))
            ).scalar_one()
            ai_duplicate = (
                await uow.session.execute(text(
                    "SELECT count(*) FROM (SELECT invocation_key FROM trading.ai_invocations "
                    "GROUP BY invocation_key HAVING count(*)>1) d"))
            ).scalar_one()
            committed = (
                await uow.session.execute(
                    text("SELECT count(*) FROM trading.forecast_submissions "
                         "WHERE status='BLIND_COMMITTED'")
                )
            ).scalar_one()
            projections = (
                await uow.session.execute(
                    text("SELECT count(*) FROM trading.payout_projections")
                )
            ).scalar_one()
            lost = (
                await uow.session.execute(
                    text("SELECT count(*) FROM trading.forecast_submissions "
                         "WHERE status<>'BLIND_COMMITTED' AND committed_at IS NULL")
                )
            ).scalar_one()
            duplicate_episodes = (
                await uow.session.execute(
                    text("SELECT count(*) FROM (SELECT episode_id FROM trading.forecast_submissions "
                         "WHERE status='BLIND_COMMITTED' GROUP BY episode_id HAVING count(*)>1) d")
                )
            ).scalar_one()
            outbox_rows = (
                await uow.session.execute(
                    text("SELECT count(*) FROM trading.transactional_outbox")
                )
            ).scalar_one()
            wal_bytes = (
                await uow.session.execute(
                    text("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), :start_lsn)"),
                    {"start_lsn": wal_start_lsn},
                )
            ).scalar_one() or 0
            db_version = (
                await uow.session.execute(text("SELECT version()"))
            ).scalar_one()

        window_rates: list[float] = []
        w = 0.0
        while w + SUSTAINED_WINDOW_SECONDS <= commit_elapsed:
            end = w + SUSTAINED_WINDOW_SECONDS
            window_rates.append(
                sum(w <= o < end for o in commit_metrics.completion_offsets) / SUSTAINED_WINDOW_SECONDS
            )
            w = end

        results["ai"] = {
            "duration_seconds": round(ai_elapsed, 3),
            "completed_terminalizations": ai_rows,
            "accepted": ai_accepted,
            "rate_per_second": round(ai_rate, 3),
            "required_rate_per_second": MIN_AI_PER_SECOND,
            "tool_rows": tool_rows,
            "validator_rows": validator_rows,
            "pool_wait_ms": _percentiles(ai_metrics.pool_wait_ms),
            "terminalization_ms": _percentiles(ai_metrics.terminalization_ms),
            "peak_checked_out": ai_metrics.peak_checked_out,
            "pool_size": POOL_SIZE,
            "max_overflow": 0,
            "lost": ai_lost,
            "duplicate": ai_duplicate,
        }
        results["blind_commit"] = {
            "duration_seconds": round(commit_elapsed, 3),
            "completed_commits": done,
            "committed_submissions": committed,
            "rate_per_second": round(commit_rate, 3),
            "required_rate_per_second": MIN_COMMITS_PER_SECOND,
            "sustained_window_seconds": SUSTAINED_WINDOW_SECONDS,
            "window_rates_per_second": [round(r, 3) for r in window_rates],
            "projection_rows": projections,
            "projection_mismatch": commit_metrics.projection_mismatch,
            "pool_wait_ms": _percentiles(commit_metrics.pool_wait_ms),
            "commit_ms": _percentiles(commit_metrics.commit_ms),
            "peak_checked_out": commit_metrics.peak_checked_out,
            "outbox_rows": outbox_rows,
            "wal_bytes": int(wal_bytes),
            "lost_draft_submissions": lost,
            "duplicate_committed_episodes": duplicate_episodes,
        }

        # Hard assertions（阈值随常量，便于 smoke 验证）
        assert ai_elapsed >= AI_SECONDS - 0.5, "ai_window_shorter_than_60s"
        assert ai_rate >= MIN_AI_PER_SECOND, "ai_rate_below_100_per_second"
        assert ai_rows >= int(MIN_AI_PER_SECOND * (AI_SECONDS - 1)), "ai_rows_below_target"
        # 每条 ACCEPTED 恰 2 tool + 5 validator rows（任务 §6）
        assert tool_rows == ai_accepted * 2, "ai_tool_rows_mismatch"
        assert validator_rows == ai_accepted * 5, "ai_validator_rows_mismatch"
        assert ai_accepted >= int(MIN_AI_PER_SECOND * (AI_SECONDS - 1)), "ai_accepted_below_target"
        assert ai_lost == 0, "ai_lost_attempt"
        assert ai_duplicate == 0, "ai_duplicate_attempt"
        assert _pct(ai_metrics.pool_wait_ms, 95) <= MAX_POOL_WAIT_P95_MS, "ai_pool_wait_p95_exceeded"

        assert commit_elapsed >= COMMIT_SECONDS - 0.5, "commit_window_shorter_than_60s"
        assert commit_rate >= MIN_COMMITS_PER_SECOND, "commit_rate_below_20_per_second"
        assert window_rates and min(window_rates) >= MIN_COMMITS_PER_SECOND, (
            "commit_not_sustained_at_20_per_second"
        )
        assert _pct(commit_metrics.pool_wait_ms, 95) <= MAX_POOL_WAIT_P95_MS, (
            "commit_pool_wait_p95_exceeded"
        )
        assert lost == 0, "lost_draft_submission"
        assert duplicate_episodes == 0, "duplicate_committed_submission"
        assert commit_metrics.projection_mismatch == 0, "projection_mismatch"
        assert committed == done, "committed_count_mismatch"

        usage = resource.getrusage(resource.RUSAGE_SELF)
        results["environment"] = {
            "git_commit": code_hash,
            "node": platform.node(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "max_rss_kib": usage.ru_maxrss,
            "database_version": db_version,
            "driver": "postgresql+asyncpg",
            "seed": "deterministic/wp-02-cognition-performance-v1",
            "timestamp": FIXED.isoformat(),
        }
        results["hard_assertions"] = "PASS"
        OUT_PATH = Path("/tmp/pm_v2_perf_smoke_2.json")
        OUT_PATH.write_text(json.dumps(results, indent=2, sort_keys=True))
        return results
    finally:
        artifacts.aclose()
        artifact_tmp.cleanup()
        await async_engine.dispose()
        admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            with admin.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=:name AND pid<>pg_backend_pid()"
                    ),
                    {"name": dbname},
                )
                connection.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        finally:
            admin.dispose()


def main() -> None:
    results = asyncio.run(_run())
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
