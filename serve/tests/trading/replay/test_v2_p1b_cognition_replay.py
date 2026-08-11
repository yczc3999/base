"""WP-02 P1B cognition replay —— 冻结事实两次离线重放 hash 全等（真 PostgreSQL）。

流程：G4 prior → G5A bundle → G5B → G6 atomic BLIND_COMMITTED；两次完整运行后
对比 prior/bundle/input/Q/U/projection/submission/lease/Gate/outbox 全部 hash 与关键值。
崩溃重试 effect=0（G6 crash 后重跑不重复 commit）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.logics.trading.component import ComponentLogic
from app.logics.trading.contract import ContractLogic
from app.logics.trading.evidence import EvidenceLogic
from app.logics.trading.forecast import ForecastLogic, InputManifestMaterial
from app.logics.trading.screening import (
    AUDIT_ALGORITHM_VERSION,
    G0Result,
    ScreeningLogic,
)
from app.orchestrator.trading_state_machine import EpisodeInput, TradingStateMachine
from app.repositories.trading.cohort import CohortRepository, REQUIRED_COHORT_POLICIES
from app.repositories.trading.forecast import ForecastRepository
from app.repositories.trading.semantics import SemanticsRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.schemas.trading.evidence import (
    EvidenceBundleInput,
    EvidenceCoveragePolicyInput,
    EvidenceRevisionInput,
    PriorInput,
)
from app.schemas.trading.forecast import (
    ForecastLeaseInput,
    ForecastSubmissionInput,
    QDistributionInput,
)
from app.schemas.trading.semantics import ContractSpecInput, PayoutIRInput, WorldSchemaInput
from app.schemas.trading.workflow import (
    HydratedUniverseFrameInput,
    R0Input,
    R0PolicyInput,
    RejectAuditPolicyInput,
)

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V20 = "b1000020"

FIXED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
CUTOFF = FIXED + timedelta(days=2)
SEED = "5" * 64

FULL_OBJECTIVE = {
    "objective_fn_version": "objective/v1", "units": "USD",
    "decision_horizon": "HOLD_TO_RESOLUTION", "HOLD_TO_RESOLUTION": True,
    "discount_policy": {"kind": "none"}, "capital_charge_policy": {"kind": "linear"},
    "NO_ACTION": {"action": "NO_ACTION"}, "allowed_actions": ["NO_ACTION", "PREDICT"],
    **{field: {"included": True} for field in (
        "trading_cost_scope", "data_cost_scope", "llm_cost_scope", "search_cost_scope",
        "infrastructure_cost_scope", "human_cost_scope", "operational_cost_scope")},
    "robustness_policy": {"kind": "worst_case"},
    "hard_constraint_ordering": ["eligibility", "capital"],
}
OBJECTIVE_HASH = canonical_hash(FULL_OBJECTIVE)
R0_POLICY = R0PolicyInput(
    policy_version=1, minimum_rule_completeness=Decimal("0.75"),
    maximum_research_cost=Decimal("100"), require_two_sided_quote=True,
    defer_recheck_condition="book_or_rules_change",
    reject_recheck_condition="capacity_or_cost_change",
)
AUDIT_POLICY = RejectAuditPolicyInput(
    policy_version=1, algorithm_version=AUDIT_ALGORITHM_VERSION,
    salt="replay/reject-audit/v1", reject_probability=Decimal("1"),
    defer_probability=Decimal("1"),
)
COVERAGE_POLICY = EvidenceCoveragePolicyInput(
    policy_version=1, material_branches=["w0", "w1"], allowed_source_types=["web"],
    contamination_policy={"kind": "hard_veto"}, staleness_policy={"max_age": "48h"},
    independence_requirement={"n": 2}, widening_algorithm="extreme-points/v1",
    missing_branch_policy="widen", content={"meta": {"kind": "election"}},
)
PRIOR = PriorInput(
    reference_class="similar-elections", applicability={"scope": "elections"},
    sample_rule={"rule": "past-10"}, width={"lower": "0.2", "upper": "0.4"},
    failure_conditions={"c": "regime-change"}, market_blind_declaration=True,
)
MATERIAL = InputManifestMaterial(
    taxonomy_hash=canonical_hash({"taxonomy": "taxonomy/v1"}),
    model_binding_hash=canonical_hash({"binding": "deepseek-planner/v1"}),
    prompt_hash=canonical_hash({"prompt": "planner_prior/v1"}),
    code_hash=canonical_hash({"code": "serve@wp02"}),
)


class InjectedCrash(RuntimeError):
    pass


@pytest_asyncio.fixture
async def replay_env(temp_pg_db):
    _run(command.upgrade, V20, temp_pg_db.url)
    admin = make_url(temp_pg_db.url)
    async_url = admin.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    engine = create_async_engine(async_url, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    env = {
        "sessions": sessions,
        "forecast": ForecastRepository(),
        "wf": WorkflowRepository(),
        "cohort": CohortRepository(),
        "sem": SemanticsRepository(),
        "url": temp_pg_db.url,
    }
    yield env
    await engine.dispose()


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


async def _seed(env: dict) -> dict:
    r0_hash = canonical_hash(R0_POLICY.model_dump(mode="json"))
    audit_hash = canonical_hash(AUDIT_POLICY.model_dump(mode="json"))
    coverage_hash = canonical_hash(COVERAGE_POLICY.model_dump(mode="json"))
    policy_hashes = {name: f"{index:x}" * 64 for index, name in enumerate(REQUIRED_COHORT_POLICIES, start=1)}
    policy_hashes["r0"] = r0_hash
    policy_hashes["reject_audit"] = audit_hash
    policy_hashes["evidence_coverage"] = coverage_hash
    async with UnitOfWork(env["sessions"]) as uow:
        s = uow.session
        obj = (await s.execute(text(
            "INSERT INTO trading.strategy_objective_contracts (contract_key,version_no,content,schema_version,content_hash,status) "
            "VALUES ('obj-replay-cog',1,CAST(:content AS jsonb),1,:hash,'active') RETURNING id"),
            {"content": json.dumps(FULL_OBJECTIVE), "hash": OBJECTIVE_HASH})).scalar_one()
        strategy_content = {"strategy": "cognition/v1"}
        strategy_hash = canonical_hash(strategy_content)
        strategy = (await s.execute(text(
            "INSERT INTO trading.strategy_versions (strategy_key,version_no,content,schema_version,content_hash,status) "
            "VALUES ('strategy-replay-cog',1,CAST(:content AS jsonb),1,:hash,'active') RETURNING id"),
            {"content": json.dumps(strategy_content), "hash": strategy_hash})).scalar_one()
        permission = (await s.execute(text(
            "INSERT INTO trading.capital_permission_manifests (name,mode,capability,limits,evaluation_capital,authorized_capital,content_hash,status) "
            "VALUES ('permission-replay-cog','shadow','{}','{}',0,0,:h,'active') RETURNING id"),
            {"h": "c" * 64})).scalar_one()
        config = (await s.execute(text(
            "INSERT INTO trading.runtime_config_versions (config_key,version_no,content,schema_version,content_hash,status) "
            "VALUES ('config-replay-cog',1,'{}',1,:h,'active') RETURNING id"),
            {"h": "d" * 64})).scalar_one()
        execution = (await s.execute(text(
            "INSERT INTO trading.execution_spec_versions (spec_key,version_no,content,schema_version,content_hash,status) "
            "VALUES ('execution-replay-cog',1,'{}',1,:h,'active') RETURNING id"),
            {"h": "e" * 64})).scalar_one()
        release = (await s.execute(text(
            "INSERT INTO trading.release_manifests (release_name,config_version_id,strategy_version_id,execution_spec_version_id,"
            "capital_permission_manifest_id,git_sha,image_digest,db_revision,total_hash,status) "
            "VALUES ('release-replay-cog',:cfg,:strategy,:execution,:permission,'abc','img','b1000020',:hash,'active') RETURNING id"),
            {"cfg": config, "strategy": strategy, "execution": execution, "permission": permission, "hash": "f" * 64})).scalar_one()
        for name in REQUIRED_COHORT_POLICIES:
            await s.execute(text("INSERT INTO trading.policy_type_scopes (policy_type,scope_type,scope_key) VALUES (:name,'cohort','cohort-replay-cog')"), {"name": name})
            await s.execute(text("INSERT INTO trading.policy_freezes (policy_type,scope_type,scope_key,policy_version,policy_content_hash,release_manifest_id,status) VALUES (:name,'cohort','cohort-replay-cog',1,:hash,:release,'frozen')"), {"name": name, "hash": policy_hashes[name], "release": release})
        cohort = (await s.execute(text(
            "INSERT INTO trading.evaluation_cohorts (cohort_key,status,objective_contract_id,strategy_version_id,release_manifest_id,policy_hashes,seed_hash) "
            "VALUES ('cohort-replay-cog','DRAFT',:obj,:strategy,:release,CAST(:policies AS jsonb),:seed) RETURNING id"),
            {"obj": obj, "strategy": strategy, "release": release, "policies": json.dumps(policy_hashes), "seed": SEED})).scalar_one()
        await s.execute(text("UPDATE trading.evaluation_cohorts SET status='OPEN',opened_at=:opened WHERE id=:cohort"), {"opened": FIXED, "cohort": cohort})
        await s.execute(text(
            "INSERT INTO trading.evidence_coverage_policies (cohort_id,policy_version,content,content_hash,status) "
            "VALUES (:cohort,1,CAST(:content AS jsonb),:hash,'active')"),
            {"cohort": cohort, "content": json.dumps(COVERAGE_POLICY.model_dump(mode="json")), "hash": coverage_hash})
        market = (await s.execute(text(
            "INSERT INTO trading.pm_markets (gamma_market_id,condition_id,active,closed,accepting_orders,enable_order_book) "
            "VALUES ('market-replay-cog','condition-replay-cog',true,false,true,true) RETURNING id"))).scalar_one()
        yes_token = (await s.execute(text("INSERT INTO trading.pm_tokens (token_id,market_id,outcome_index) VALUES ('token-replay-cog-yes',:market,0) RETURNING id"), {"market": market})).scalar_one()
        no_token = (await s.execute(text("INSERT INTO trading.pm_tokens (token_id,market_id,outcome_index) VALUES ('token-replay-cog-no',:market,1) RETURNING id"), {"market": market})).scalar_one()
        market_version = (await s.execute(text(
            "INSERT INTO trading.pm_market_versions (market_id,version_no,observed_at,received_at,normalized_hash) "
            "VALUES (:market,1,:at,:at,:hash) RETURNING id"),
            {"market": market, "at": FIXED, "hash": "1" * 64})).scalar_one()
        yes_version = (await s.execute(text(
            "INSERT INTO trading.pm_token_versions (token_id,version_no,outcome_index,observed_at,received_at) VALUES (:token,1,0,:at,:at) RETURNING id"),
            {"token": yes_token, "at": FIXED})).scalar_one()
        no_version = (await s.execute(text(
            "INSERT INTO trading.pm_token_versions (token_id,version_no,outcome_index,observed_at,received_at) VALUES (:token,1,1,:at,:at) RETURNING id"),
            {"token": no_token, "at": FIXED})).scalar_one()

        async def artifact(sha: str) -> int:
            return (await s.execute(text(
                "INSERT INTO trading.artifact_objects (sha256,original_size,stored_size,mime,compression,storage_driver,storage_version,locator) "
                "VALUES (:sha,1,1,'application/json','none','local','cas/v1',:locator) RETURNING id"),
                {"sha": sha, "locator": f"cas/v1/sha256/{sha[:2]}/{sha[2:4]}/{sha}.raw"})).scalar_one()

        frame_sha = "9" * 64
        contract_artifact = await artifact("8" * 64)
        await artifact("7" * 64)
        frame_artifact = await artifact(frame_sha)
        frame = (await s.execute(text(
            "INSERT INTO trading.pm_universe_frames (status,started_at,owner,lease_expires_at,fencing_token,completed_at,page_count,total_events,total_markets,content_hash,artifact_id,artifact_ref) "
            "VALUES ('COMPLETE',:started,'replay-cog',:lease,1,:completed,0,0,1,:hash,:artifact,:hash) RETURNING id"),
            {"started": FIXED, "lease": FIXED + timedelta(minutes=1), "completed": FIXED, "hash": frame_sha, "artifact": frame_artifact})).scalar_one()
    return {
        "cohort": cohort, "objective": obj, "strategy": strategy, "release": release,
        "policy_hashes": policy_hashes, "market": market, "market_version": market_version,
        "yes_token": yes_token, "no_token": no_token, "yes_version": yes_version, "no_version": no_version,
        "contract_artifact": contract_artifact,
        "frame": HydratedUniverseFrameInput(frame_id=frame, content_hash=frame_sha, artifact_object_id=frame_artifact, artifact_ref=frame_sha, markets=[{"market_id": market, "metadata": {"market_key": "market-replay-cog"}}]),
    }


async def _run_chain(env: dict) -> dict:
    ctx = await _seed(env)
    screening = ScreeningLogic(env["cohort"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        g0 = await screening.run_g0(uow, cohort_id=ctx["cohort"], objective_content=FULL_OBJECTIVE, expected_objective_hash=OBJECTIVE_HASH)
    assert g0.ok
    async with UnitOfWork(env["sessions"]) as uow:
        await screening.enroll_frame(uow, cohort_id=ctx["cohort"], frame=ctx["frame"], observed_at=FIXED, ingested_at=FIXED, g0=g0)
    async with UnitOfWork(env["sessions"]) as uow:
        selected = await screening.run_r0(uow, cohort_id=ctx["cohort"], market_id=ctx["market"], episode_no=1,
            r0_input=R0Input(market_metadata={"market_key": "market-replay-cog"}, best_bid=Decimal("0.50"), best_ask=Decimal("0.52"), rule_completeness=Decimal("0.90"), minimum_deployable_capacity=Decimal("10"), objective_ref=OBJECTIVE_HASH),
            g0=g0, r0_policy=R0_POLICY, audit_policy=AUDIT_POLICY)
    state = TradingStateMachine(env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        parent = await state.create_parent_opportunity(uow, cohort_id=ctx["cohort"], chain_type="DECISION", objective_contract_id=ctx["objective"], strategy_version_id=ctx["strategy"], source_screening_episode_id=selected.episode_id, triggered_at=FIXED, market_ids=[ctx["market"]])
    async with UnitOfWork(env["sessions"]) as uow:
        g1_child = await state.create_g1_child(uow, parent_id=parent, cohort_id=ctx["cohort"], chain_type="DECISION", objective_contract_id=ctx["objective"], strategy_version_id=ctx["strategy"], triggered_at=FIXED, market_id=ctx["market"], seq=1)
    contract_candidate = ContractSpecInput(
        contract_key="spec-replay-cog", market_version_id=ctx["market_version"],
        yes_token_version_id=ctx["yes_version"], no_token_version_id=ctx["no_version"],
        artifact_object_id=ctx["contract_artifact"], resolution_states=["YES", "NO"],
        compiler_version="lookup/v1", schema_version=1, rules="rules", resolution_source="gamma",
        payouts=[
            PayoutIRInput(token_key="yes", pm_token_id=ctx["yes_token"], token_version_id=ctx["yes_version"], outcome_index=0, function_ir={"YES": "1", "NO": "0"}),
            PayoutIRInput(token_key="no", pm_token_id=ctx["no_token"], token_version_id=ctx["no_version"], outcome_index=1, function_ir={"YES": "0", "NO": "1"}),
        ],
    )
    async with UnitOfWork(env["sessions"]) as uow:
        g1 = await ContractLogic(env["sem"], env["wf"]).run_g1(uow, candidate=contract_candidate, cutoff_at=CUTOFF, timezone_name="UTC", raw_outcome_mapping={"YES": 0, "NO": 1}, opportunity_id=g1_child, policy_hash=ctx["policy_hashes"]["eligibility"], version_manifest_id=ctx["release"])
    assert g1.ok, g1.reason
    spec_ids = [g1.spec_id]
    async with UnitOfWork(env["sessions"]) as uow:
        g2_child = await state.create_g2_child(uow, parent_id=parent, cohort_id=ctx["cohort"], chain_type="DECISION", objective_contract_id=ctx["objective"], strategy_version_id=ctx["strategy"], triggered_at=FIXED, component_key="component-replay-cog", g1_child_ids=[g1_child])
    ws = WorldSchemaInput(component_key="component-replay-cog", variables={"outcome": {"type": "enum"}}, domains={"outcome": ["yes", "no"]}, constraints=[], factorization={"independent": ["outcome"]},
        world_states=[{"world_state_id": "w0", "assignment": {"outcome": "yes"}}, {"world_state_id": "w1", "assignment": {"outcome": "no"}}], state_count=2,
        h_c={str(spec_ids[0]): {"w0": "YES", "w1": "NO"}}, schema_version=1)
    async with UnitOfWork(env["sessions"]) as uow:
        g2 = await ComponentLogic(env["sem"], env["wf"]).run_g2(uow, candidate=ws, contract_spec_ids=spec_ids, member_hc={spec_ids[0]: ws.h_c[str(spec_ids[0])]}, cost_budget=Decimal("10"), opportunity_id=g2_child, policy_hash=ctx["policy_hashes"]["taxonomy"], version_manifest_id=ctx["release"])
    assert g2.ok, g2.reason
    episode_input = EpisodeInput(decision_opportunity_id=g2_child, component_version_id=g2.component_version_id, strategy_version_id=ctx["strategy"], objective_contract_id=ctx["objective"], trigger="frame", cutoff_at=CUTOFF, horizon="resolution", experiment_variant="control", contract_spec_ids=spec_ids)
    async with UnitOfWork(env["sessions"]) as uow:
        episode = await state.create_episode(uow, input_=episode_input)
    async with UnitOfWork(env["sessions"]) as uow:
        await state.route_episode(uow, episode_id=episode, route_channel="standard", first_rejected_gate=None, reason_code=None, recheck_at=None, recheck_condition=None, audit_selected=False, policy_hash=ctx["policy_hashes"]["r1"], version_manifest_id=ctx["release"])

    evidence = EvidenceLogic(env["forecast"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        g4 = await evidence.run_g4(uow, episode_id=episode, prior=PRIOR, version_manifest_id=ctx["release"])
    assert g4.ok, g4.reason
    async with UnitOfWork(env["sessions"]) as uow:
        await evidence.add_revision(uow, episode_id=episode, revision=EvidenceRevisionInput(
            revision_key="r1", kind="source_claim", event_at=FIXED,
            published_at=CUTOFF - timedelta(hours=2), observed_at=CUTOFF - timedelta(hours=1),
            ingested_at=CUTOFF - timedelta(minutes=30), source="https://example.com", source_type="web",
            branch="main", raw_artifact_ref="7" * 64, content={"claim": "r1"}, taint_status="none"))
        await evidence.add_revision(uow, episode_id=episode, revision=EvidenceRevisionInput(
            revision_key="r2", kind="observation", event_at=FIXED,
            published_at=CUTOFF - timedelta(hours=2), observed_at=CUTOFF - timedelta(hours=1),
            ingested_at=CUTOFF - timedelta(minutes=29), source="https://example.com/data", source_type="api",
            branch="main", raw_artifact_ref="7" * 64, content={"value": "x"}, taint_status="none"))
    async with UnitOfWork(env["sessions"]) as uow:
        g5a = await evidence.run_g5a(uow, episode_id=episode, bundle=EvidenceBundleInput(bundle_key="bundle-1", information_cutoff_at=CUTOFF, revision_keys=["r1", "r2"]), version_manifest_id=ctx["release"])
    assert g5a.ok, g5a.reason
    async with UnitOfWork(env["sessions"]) as uow:
        g5b = await evidence.run_g5b(uow, episode_id=episode, policy=COVERAGE_POLICY, covered_branches=["w0", "w1"], version_manifest_id=ctx["release"])
    assert g5b.result == "PASS", g5b.reason

    submission = ForecastSubmissionInput(
        submission_key="sub-replay-cog",
        Q=QDistributionInput(values={"w0": "0.6", "w1": "0.4"}),
        U=[QDistributionInput(values={"w0": "0.6", "w1": "0.4"}), QDistributionInput(values={"w0": "0.5", "w1": "0.5"})],
        forecast_input_manifest_id=1,
    )
    lease = ForecastLeaseInput(valid_until=FIXED + timedelta(days=30),
        invalidation_conditions={"fact_freshness": {"max_age_hours": 48}, "rule_change": {"check": "schema_hash"}},
        evidence_hash="a" * 64, schema_hash="b" * 64, spec_hash="c" * 64)
    forecast = ForecastLogic(env["forecast"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        g6 = await forecast.run_g6(uow, episode_id=episode, submission=submission, material=MATERIAL, lease=lease, version_manifest_id=ctx["release"], policy_hash=ctx["policy_hashes"]["evidence_coverage"])
    assert g6.ok, g6.reason
    assert g6.committed and g6.committed_count == 1
    return await _snapshot(env)


async def _snapshot(env: dict) -> dict:
    async with UnitOfWork(env["sessions"]) as uow:
        s = uow.session

        async def rows(sql: str):
            return [tuple(row) for row in (await s.execute(text(sql))).fetchall()]

        priors = await rows(
            "SELECT version_no, content_hash, status FROM trading.priors"
        )
        bundles = await rows(
            "SELECT bundle_key, information_cutoff_at, bundle_hash, status "
            "FROM trading.evidence_bundles"
        )
        bundle_items = await rows(
            "SELECT b.bundle_key, r.revision_key, i.item_no, i.eligible "
            "FROM trading.evidence_bundle_items i "
            "JOIN trading.evidence_bundles b ON b.id=i.bundle_id "
            "JOIN trading.evidence_revisions r ON r.id=i.revision_id ORDER BY b.bundle_key, i.item_no"
        )
        manifests = await rows(
            "SELECT manifest_key, manifest_hash, evidence_bundle_hash, "
            "contract_spec_set_hash, world_schema_hash, prior_hash, taxonomy_hash, "
            "model_binding_hash, prompt_hash, code_hash FROM trading.forecast_input_manifests"
        )
        submissions = await rows(
            "SELECT submission_key, status, Q::text, U::text, "
            "contract_schema_prior_evidence_hash, algorithm_hash, committed_at IS NOT NULL "
            "FROM trading.forecast_submissions"
        )
        projections = await rows(
            "SELECT contract_spec_id, pm_token_id, mu::text, v::text, "
            "u_lower::text, u_upper::text, p_blind::text, algorithm_hash, h_c_hash, g_hash "
            "FROM trading.payout_projections ORDER BY contract_spec_id, pm_token_id"
        )
        checks = await rows(
            "SELECT check_name, passed, severity, reason_code "
            "FROM trading.coherence_checks ORDER BY check_name"
        )
        leases = await rows(
            "SELECT valid_until, invalidation_conditions::text, "
            "evidence_hash, schema_hash, spec_hash FROM trading.forecast_leases"
        )
        gates = await rows(
            "SELECT gate, target_kind, input_hash, policy_hash, result, reason_code "
            "FROM trading.gate_decisions WHERE gate IN ('G4','G5A','G5B','G6') "
            "ORDER BY gate"
        )
        episodes = await rows(
            "SELECT status, cognition_status FROM trading.forecast_episodes"
        )
        eligibility = await rows(
            "SELECT action_eligible, qualification_eligible, capital_evidence_eligible "
            "FROM trading.episode_memberships"
        )
        outbox = await rows(
            "SELECT topic, schema_version, aggregate_type, idempotency_key, "
            "priority, payload::text, release_manifest_id FROM trading.transactional_outbox"
        )
    assert eligibility == [(False, False, False)]
    assert episodes == [("BLIND_COMMITTED", "COMMITTED")]
    return {
        "priors": priors, "bundles": bundles, "bundle_items": bundle_items,
        "manifests": manifests, "submissions": submissions, "projections": projections,
        "checks": checks, "leases": leases, "gates": gates, "episodes": episodes,
        "eligibility": eligibility, "outbox": outbox,
    }


def _restart(url: str) -> None:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "TRUNCATE trading.policy_freezes, trading.policy_type_scopes, "
                "trading.evaluation_cohorts, trading.decision_opportunities, "
                "trading.forecast_episodes, trading.screening_episodes, trading.audit_samples, "
                "trading.contract_snapshots, trading.contract_specs, trading.forecast_components, "
                "trading.strategy_objective_contracts, trading.strategy_versions, "
                "trading.release_manifests, trading.runtime_config_versions, "
                "trading.execution_spec_versions, trading.capital_permission_manifests, "
                "trading.pm_universe_frames, trading.pm_markets, trading.artifact_objects, "
                "trading.priors, trading.evidence_revisions, trading.evidence_bundles, "
                "trading.evidence_bundle_items, trading.forecast_input_manifests, "
                "trading.forecast_submissions, trading.payout_projections, "
                "trading.coherence_checks, trading.forecast_leases, "
                "trading.evidence_coverage_policies, trading.transactional_outbox RESTART IDENTITY CASCADE"
            ))
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_p1b_cognition_replay_is_stable(replay_env):
    first = await _run_chain(replay_env)
    _restart(replay_env["url"])
    second = await _run_chain(replay_env)
    assert first == second
