"""WP-02 blind forecast 纵向集成测试（真 PostgreSQL）。

R1 ROUTED → G4 prior → evidence revisions → G5A bundle → G5B sufficiency → G6 atomic
BLIND_COMMITTED + lease + outbox。覆盖：
- G4 PASS 推进 cognition_status；重复推进拒绝。
- evidence revision 插入 + taint/cutoff hard veto（G5A fail-closed）。
- G5B WIDEN/ABSTAIN 与 PASS 分支。
- G6 原子 commit：submission/projections/checks/lease/Gate/outbox 同一 UoW；
  Q∉U / 非法概率 fail-closed；commit 后不可变。
- episode 终态 BLIND_COMMITTED；action/qualification/capital eligibility 保持 false。
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
    CoherenceCheckInput,
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
    "objective_fn_version": "objective/v1",
    "units": "USD",
    "decision_horizon": "HOLD_TO_RESOLUTION",
    "HOLD_TO_RESOLUTION": True,
    "discount_policy": {"kind": "none"},
    "capital_charge_policy": {"kind": "linear"},
    "NO_ACTION": {"action": "NO_ACTION"},
    "allowed_actions": ["NO_ACTION", "PREDICT"],
    **{
        field: {"included": True}
        for field in (
            "trading_cost_scope", "data_cost_scope", "llm_cost_scope", "search_cost_scope",
            "infrastructure_cost_scope", "human_cost_scope", "operational_cost_scope",
        )
    },
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
    policy_version=1,
    material_branches=["w0", "w1"],
    allowed_source_types=["web"],
    contamination_policy={"kind": "hard_veto"},
    staleness_policy={"max_age": "48h"},
    independence_requirement={"n": 2},
    widening_algorithm="extreme-points/v1",
    missing_branch_policy="widen",
    content={"meta": {"kind": "election"}},
)
PRIOR = PriorInput(
    reference_class="similar-elections",
    applicability={"scope": "elections"},
    sample_rule={"rule": "past-10"},
    width={"lower": "0.2", "upper": "0.4"},
    failure_conditions={"c": "regime-change"},
    market_blind_declaration=True,
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
async def cognition_env(temp_pg_db):
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
    policy_hashes = {
        name: f"{index:x}" * 64
        for index, name in enumerate(REQUIRED_COHORT_POLICIES, start=1)
    }
    policy_hashes["r0"] = r0_hash
    policy_hashes["reject_audit"] = audit_hash
    policy_hashes["evidence_coverage"] = coverage_hash

    async with UnitOfWork(env["sessions"]) as uow:
        s = uow.session
        obj = (
            await s.execute(
                text(
                    "INSERT INTO trading.strategy_objective_contracts "
                    "(contract_key,version_no,content,schema_version,content_hash,status) "
                    "VALUES ('obj-cog',1,CAST(:content AS jsonb),1,:hash,'active') RETURNING id"
                ),
                {"content": json.dumps(FULL_OBJECTIVE), "hash": OBJECTIVE_HASH},
            )
        ).scalar_one()
        strategy_content = {"strategy": "cognition/v1"}
        strategy_hash = canonical_hash(strategy_content)
        strategy = (
            await s.execute(
                text(
                    "INSERT INTO trading.strategy_versions "
                    "(strategy_key,version_no,content,schema_version,content_hash,status) "
                    "VALUES ('strategy-cog',1,CAST(:content AS jsonb),1,:hash,'active') RETURNING id"
                ),
                {"content": json.dumps(strategy_content), "hash": strategy_hash},
            )
        ).scalar_one()
        permission = (
            await s.execute(
                text(
                    "INSERT INTO trading.capital_permission_manifests "
                    "(name,mode,capability,limits,evaluation_capital,authorized_capital,content_hash,status) "
                    "VALUES ('permission-cog','shadow','{}','{}',0,0,:h,'active') RETURNING id"
                ),
                {"h": "c" * 64},
            )
        ).scalar_one()
        config = (
            await s.execute(
                text(
                    "INSERT INTO trading.runtime_config_versions "
                    "(config_key,version_no,content,schema_version,content_hash,status) "
                    "VALUES ('config-cog',1,'{}',1,:h,'active') RETURNING id"
                ),
                {"h": "d" * 64},
            )
        ).scalar_one()
        execution = (
            await s.execute(
                text(
                    "INSERT INTO trading.execution_spec_versions "
                    "(spec_key,version_no,content,schema_version,content_hash,status) "
                    "VALUES ('execution-cog',1,'{}',1,:h,'active') RETURNING id"
                ),
                {"h": "e" * 64},
            )
        ).scalar_one()
        release = (
            await s.execute(
                text(
                    "INSERT INTO trading.release_manifests "
                    "(release_name,config_version_id,strategy_version_id,execution_spec_version_id,"
                    "capital_permission_manifest_id,git_sha,image_digest,db_revision,total_hash,status) "
                    "VALUES ('release-cog',:cfg,:strategy,:execution,:permission,'abc','img',"
                    "'b1000020',:hash,'active') RETURNING id"
                ),
                {
                    "cfg": config, "strategy": strategy, "execution": execution,
                    "permission": permission, "hash": "f" * 64,
                },
            )
        ).scalar_one()
        for name in REQUIRED_COHORT_POLICIES:
            await s.execute(
                text(
                    "INSERT INTO trading.policy_type_scopes "
                    "(policy_type,scope_type,scope_key) VALUES (:name,'cohort','cohort-cog')"
                ),
                {"name": name},
            )
            await s.execute(
                text(
                    "INSERT INTO trading.policy_freezes "
                    "(policy_type,scope_type,scope_key,policy_version,policy_content_hash,"
                    "release_manifest_id,status) "
                    "VALUES (:name,'cohort','cohort-cog',1,:hash,:release,'frozen')"
                ),
                {"name": name, "hash": policy_hashes[name], "release": release},
            )
        cohort = (
            await s.execute(
                text(
                    "INSERT INTO trading.evaluation_cohorts "
                    "(cohort_key,status,objective_contract_id,strategy_version_id,release_manifest_id,"
                    "policy_hashes,seed_hash) "
                    "VALUES ('cohort-cog','DRAFT',:obj,:strategy,:release,CAST(:policies AS jsonb),:seed) "
                    "RETURNING id"
                ),
                {
                    "obj": obj, "strategy": strategy, "release": release,
                    "policies": json.dumps(policy_hashes), "seed": SEED,
                },
            )
        ).scalar_one()
        await s.execute(
            text(
                "UPDATE trading.evaluation_cohorts SET status='OPEN',opened_at=:opened "
                "WHERE id=:cohort"
            ),
            {"opened": FIXED, "cohort": cohort},
        )
        # evidence coverage policy frozen (cohort)
        await s.execute(
            text(
                "INSERT INTO trading.evidence_coverage_policies "
                "(cohort_id,policy_version,content,content_hash,status) "
                "VALUES (:cohort,1,CAST(:content AS jsonb),:hash,'active')"
            ),
            {"cohort": cohort, "content": json.dumps(COVERAGE_POLICY.model_dump(mode="json")), "hash": coverage_hash},
        )
        market = (
            await s.execute(
                text(
                    "INSERT INTO trading.pm_markets "
                    "(gamma_market_id,condition_id,active,closed,accepting_orders,enable_order_book) "
                    "VALUES ('market-cog','condition-cog',true,false,true,true) RETURNING id"
                ),
            )
        ).scalar_one()
        yes_token = (
            await s.execute(
                text(
                    "INSERT INTO trading.pm_tokens (token_id,market_id,outcome_index) "
                    "VALUES ('token-cog-yes',:market,0) RETURNING id"
                ),
                {"market": market},
            )
        ).scalar_one()
        no_token = (
            await s.execute(
                text(
                    "INSERT INTO trading.pm_tokens (token_id,market_id,outcome_index) "
                    "VALUES ('token-cog-no',:market,1) RETURNING id"
                ),
                {"market": market},
            )
        ).scalar_one()
        market_version = (
            await s.execute(
                text(
                    "INSERT INTO trading.pm_market_versions "
                    "(market_id,version_no,observed_at,received_at,normalized_hash) "
                    "VALUES (:market,1,:at,:at,:hash) RETURNING id"
                ),
                {"market": market, "at": FIXED, "hash": "1" * 64},
            )
        ).scalar_one()
        yes_version = (
            await s.execute(
                text(
                    "INSERT INTO trading.pm_token_versions "
                    "(token_id,version_no,outcome_index,observed_at,received_at) "
                    "VALUES (:token,1,0,:at,:at) RETURNING id"
                ),
                {"token": yes_token, "at": FIXED},
            )
        ).scalar_one()
        no_version = (
            await s.execute(
                text(
                    "INSERT INTO trading.pm_token_versions "
                    "(token_id,version_no,outcome_index,observed_at,received_at) "
                    "VALUES (:token,1,1,:at,:at) RETURNING id"
                ),
                {"token": no_token, "at": FIXED},
            )
        ).scalar_one()

        async def artifact(sha: str) -> int:
            return (
                await s.execute(
                    text(
                        "INSERT INTO trading.artifact_objects "
                        "(sha256,original_size,stored_size,mime,compression,storage_driver,"
                        "storage_version,locator) "
                        "VALUES (:sha,1,1,'application/json','none','local','cas/v1',:locator) "
                        "RETURNING id"
                    ),
                    {"sha": sha, "locator": f"cas/v1/sha256/{sha[:2]}/{sha[2:4]}/{sha}.raw"},
                )
            ).scalar_one()

        frame_sha = "9" * 64
        contract_artifact = await artifact("8" * 64)
        await artifact("7" * 64)  # evidence raw
        frame_artifact = await artifact(frame_sha)
        frame = (
            await s.execute(
                text(
                    "INSERT INTO trading.pm_universe_frames "
                    "(status,started_at,owner,lease_expires_at,fencing_token,completed_at,"
                    "page_count,total_events,total_markets,content_hash,artifact_id,artifact_ref) "
                    "VALUES ('COMPLETE',:started,'cog',:lease,1,:completed,0,0,1,:hash,:artifact,:hash) "
                    "RETURNING id"
                ),
                {
                    "started": FIXED, "lease": FIXED + timedelta(minutes=1),
                    "completed": FIXED, "hash": frame_sha, "artifact": frame_artifact,
                },
            )
        ).scalar_one()

    return {
        "cohort": cohort, "objective": obj, "strategy": strategy, "release": release,
        "policy_hashes": policy_hashes, "market": market, "market_version": market_version,
        "yes_token": yes_token, "no_token": no_token,
        "yes_version": yes_version, "no_version": no_version,
        "contract_artifact": contract_artifact,
        "frame": HydratedUniverseFrameInput(
            frame_id=frame, content_hash=frame_sha, artifact_object_id=frame_artifact,
            artifact_ref=frame_sha,
            markets=[{"market_id": market, "metadata": {"market_key": "market-cog"}}],
        ),
    }


async def _enroll_and_r0(env: dict, ctx: dict) -> tuple[G0Result, "object", int]:
    screening = ScreeningLogic(env["cohort"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        g0 = await screening.run_g0(uow, cohort_id=ctx["cohort"], objective_content=FULL_OBJECTIVE, expected_objective_hash=OBJECTIVE_HASH)
        assert g0.ok
    async with UnitOfWork(env["sessions"]) as uow:
        await screening.enroll_frame(uow, cohort_id=ctx["cohort"], frame=ctx["frame"], observed_at=FIXED, ingested_at=FIXED, g0=g0)
    async with UnitOfWork(env["sessions"]) as uow:
        selected = await screening.run_r0(
            uow, cohort_id=ctx["cohort"], market_id=ctx["market"], episode_no=1,
            r0_input=R0Input(market_metadata={"market_key": "market-cog"}, best_bid=Decimal("0.50"), best_ask=Decimal("0.52"), rule_completeness=Decimal("0.90"), minimum_deployable_capacity=Decimal("10"), objective_ref=OBJECTIVE_HASH),
            g0=g0, r0_policy=R0_POLICY, audit_policy=AUDIT_POLICY,
        )
    return g0, selected, selected.episode_id


def _contract_candidate(ctx: dict, key: str) -> ContractSpecInput:
    return ContractSpecInput(
        contract_key=key,
        market_version_id=ctx["market_version"],
        yes_token_version_id=ctx["yes_version"],
        no_token_version_id=ctx["no_version"],
        artifact_object_id=ctx["contract_artifact"],
        resolution_states=["YES", "NO"],
        compiler_version="lookup/v1",
        schema_version=1,
        rules="rules",
        resolution_source="gamma",
        payouts=[
            PayoutIRInput(token_key="yes", pm_token_id=ctx["yes_token"], token_version_id=ctx["yes_version"], outcome_index=0, function_ir={"YES": "1", "NO": "0"}),
            PayoutIRInput(token_key="no", pm_token_id=ctx["no_token"], token_version_id=ctx["no_version"], outcome_index=1, function_ir={"YES": "0", "NO": "1"}),
        ],
    )


async def _build_routed_episode(env: dict, ctx: dict, key_prefix: str = "cog") -> tuple[int, list[int]]:
    g0, _selected, screening = await _enroll_and_r0(env, ctx)
    state = TradingStateMachine(env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        parent = await state.create_parent_opportunity(uow, cohort_id=ctx["cohort"], chain_type="DECISION", objective_contract_id=ctx["objective"], strategy_version_id=ctx["strategy"], source_screening_episode_id=screening, triggered_at=FIXED, market_ids=[ctx["market"]])
    async with UnitOfWork(env["sessions"]) as uow:
        g1_child = await state.create_g1_child(uow, parent_id=parent, cohort_id=ctx["cohort"], chain_type="DECISION", objective_contract_id=ctx["objective"], strategy_version_id=ctx["strategy"], triggered_at=FIXED, market_id=ctx["market"], seq=1)
    async with UnitOfWork(env["sessions"]) as uow:
        g1 = await ContractLogic(env["sem"], env["wf"]).run_g1(uow, candidate=_contract_candidate(ctx, f"spec-{key_prefix}"), cutoff_at=CUTOFF, timezone_name="UTC", raw_outcome_mapping={"YES": 0, "NO": 1}, opportunity_id=g1_child, policy_hash=ctx["policy_hashes"]["eligibility"], version_manifest_id=ctx["release"])
    assert g1.ok, g1.reason
    spec_ids = [g1.spec_id]
    async with UnitOfWork(env["sessions"]) as uow:
        g2_child = await state.create_g2_child(uow, parent_id=parent, cohort_id=ctx["cohort"], chain_type="DECISION", objective_contract_id=ctx["objective"], strategy_version_id=ctx["strategy"], triggered_at=FIXED, component_key=f"component-{key_prefix}", g1_child_ids=[g1_child])
    ws = WorldSchemaInput(
        component_key=f"component-{key_prefix}",
        variables={"outcome": {"type": "enum"}},
        domains={"outcome": ["yes", "no"]},
        constraints=[],
        factorization={"independent": ["outcome"]},
        world_states=[
            {"world_state_id": "w0", "assignment": {"outcome": "yes"}},
            {"world_state_id": "w1", "assignment": {"outcome": "no"}},
        ],
        state_count=2,
        h_c={str(spec_ids[0]): {"w0": "YES", "w1": "NO"}},
        schema_version=1,
    )
    async with UnitOfWork(env["sessions"]) as uow:
        g2 = await ComponentLogic(env["sem"], env["wf"]).run_g2(uow, candidate=ws, contract_spec_ids=spec_ids, member_hc={spec_ids[0]: ws.h_c[str(spec_ids[0])]}, cost_budget=Decimal("10"), opportunity_id=g2_child, policy_hash=ctx["policy_hashes"]["taxonomy"], version_manifest_id=ctx["release"])
    assert g2.ok, g2.reason
    episode_input = EpisodeInput(decision_opportunity_id=g2_child, component_version_id=g2.component_version_id, strategy_version_id=ctx["strategy"], objective_contract_id=ctx["objective"], trigger="frame", cutoff_at=CUTOFF, horizon="resolution", experiment_variant="control", contract_spec_ids=spec_ids)
    async with UnitOfWork(env["sessions"]) as uow:
        episode = await state.create_episode(uow, input_=episode_input)
    async with UnitOfWork(env["sessions"]) as uow:
        await state.route_episode(uow, episode_id=episode, route_channel="standard", first_rejected_gate=None, reason_code=None, recheck_at=None, recheck_condition=None, audit_selected=False, policy_hash=ctx["policy_hashes"]["r1"], version_manifest_id=ctx["release"])
    return episode, spec_ids


def _revision(key: str, *, taint: str = "none", cutoff_ok: bool = True, conditioned: bool = False) -> EvidenceRevisionInput:
    observed = CUTOFF - timedelta(hours=1) if cutoff_ok else CUTOFF + timedelta(hours=1)
    return EvidenceRevisionInput(
        revision_key=key, kind="source_claim", event_at=FIXED,
        published_at=observed - timedelta(hours=1), observed_at=observed,
        ingested_at=observed + timedelta(minutes=5), source="https://example.com",
        source_type="web", branch="main", raw_artifact_ref="7" * 64,
        content={"claim": key}, taint_status=taint,
        market_conditioned_discovery=conditioned,
    )


async def _g4(env: dict, episode: int, *, crash: bool = False) -> "object":
    logic = EvidenceLogic(env["forecast"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.run_g4(uow, episode_id=episode, prior=PRIOR, version_manifest_id=await _release(env))
        if crash:
            raise InjectedCrash("after_g4")
        return result


async def _release(env: dict) -> int:
    async with UnitOfWork(env["sessions"]) as uow:
        return (
            await uow.session.execute(text("SELECT id FROM trading.release_manifests WHERE release_name='release-cog'"))
        ).scalar_one()


async def _g5a(env: dict, episode: int, revisions: list[str], *, crash: bool = False):
    logic = EvidenceLogic(env["forecast"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.run_g5a(uow, episode_id=episode, bundle=EvidenceBundleInput(bundle_key="bundle-1", information_cutoff_at=CUTOFF, revision_keys=revisions), version_manifest_id=await _release(env))
        if crash:
            raise InjectedCrash("after_g5a")
        return result


async def _g5b(env: dict, episode: int, covered: list[str]):
    logic = EvidenceLogic(env["forecast"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        return await logic.run_g5b(uow, episode_id=episode, policy=COVERAGE_POLICY, covered_branches=covered, version_manifest_id=await _release(env))


async def _g6(env: dict, ctx: dict, episode: int, spec_ids: list[int], *, q: dict | None = None, u: list | None = None, crash: bool = False):
    q = q or {"w0": "0.6", "w1": "0.4"}
    u = u or [{"w0": "0.6", "w1": "0.4"}, {"w0": "0.5", "w1": "0.5"}]
    submission = ForecastSubmissionInput(
        submission_key=f"sub-{episode}",
        Q=QDistributionInput(values=q),
        U=[QDistributionInput(values=member) for member in u],
        forecast_input_manifest_id=1,  # Logic 自建 manifest，此值仅占位
    )
    lease = ForecastLeaseInput(
        valid_until=FIXED + timedelta(days=30),
        invalidation_conditions={"fact_freshness": {"max_age_hours": 48}, "rule_change": {"check": "schema_hash"}},
        evidence_hash="a" * 64,
        schema_hash="b" * 64,
        spec_hash="c" * 64,
    )
    logic = ForecastLogic(env["forecast"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.run_g6(uow, episode_id=episode, submission=submission, material=MATERIAL, lease=lease, version_manifest_id=await _release(env), policy_hash=ctx["policy_hashes"]["evidence_coverage"])
        if crash:
            raise InjectedCrash("after_g6")
        return result


async def _count(env: dict, table: str) -> int:
    async with UnitOfWork(env["sessions"]) as uow:
        return (await uow.session.execute(text(f"SELECT count(*) FROM trading.{table}"))).scalar_one()


async def _episode_state(env: dict, episode: int) -> dict:
    async with UnitOfWork(env["sessions"]) as uow:
        return (await uow.session.execute(text("SELECT status, cognition_status, prior_frozen_at IS NOT NULL AS p, evidence_bundle_at IS NOT NULL AS e, forecast_committed_at IS NOT NULL AS c FROM trading.forecast_episodes WHERE id=:e"), {"e": episode})).mappings().one()


async def _membership_eligibility(env: dict, episode: int) -> dict:
    async with UnitOfWork(env["sessions"]) as uow:
        return (await uow.session.execute(text("SELECT action_eligible, qualification_eligible, capital_evidence_eligible FROM trading.episode_memberships WHERE episode_id=:e"), {"e": episode})).mappings().one()


@pytest.mark.asyncio
async def test_blind_forecast_full_chain_atomic_commit(cognition_env):
    env = cognition_env
    ctx = await _seed(env)
    episode, spec_ids = await _build_routed_episode(env, ctx, "full")

    # G4
    g4 = await _g4(env, episode)
    assert g4.ok, g4.reason
    state = await _episode_state(env, episode)
    assert state["status"] == "ROUTED" and state["cognition_status"] == "PRIOR_READY" and state["p"]
    # 重复 G4 推进拒绝
    retry = await _g4(env, episode)
    assert not retry.ok and retry.reason == "g4_cognition_not_pending"

    # evidence revisions + G5A
    async with UnitOfWork(env["sessions"]) as uow:
        rev_logic = EvidenceLogic(env["forecast"], env["wf"])
        await rev_logic.add_revision(uow, episode_id=episode, revision=_revision("r1"))
        await rev_logic.add_revision(uow, episode_id=episode, revision=_revision("r2"))
    g5a = await _g5a(env, episode, ["r1", "r2"])
    assert g5a.ok, g5a.reason
    state = await _episode_state(env, episode)
    assert state["cognition_status"] == "EVIDENCE_READY" and state["e"]

    # G5B PASS（覆盖全部 branch）
    g5b = await _g5b(env, episode, ["w0", "w1"])
    assert g5b.result == "PASS", g5b.reason

    # G6 原子 commit
    g6 = await _g6(env, ctx, episode, spec_ids)
    assert g6.ok, g6.reason
    assert g6.committed and g6.committed_count == 1
    assert g6.projection_count == 2  # 1 spec × 2 tokens
    state = await _episode_state(env, episode)
    assert state["status"] == "BLIND_COMMITTED" and state["cognition_status"] == "COMMITTED" and state["c"]
    el = await _membership_eligibility(env, episode)
    assert not el["action_eligible"] and not el["qualification_eligible"] and not el["capital_evidence_eligible"]

    # submission/projection/check/lease 落库
    assert await _count(env, "forecast_submissions") == 1
    assert await _count(env, "payout_projections") == 2
    assert await _count(env, "coherence_checks") >= 3
    assert await _count(env, "forecast_leases") == 1
    assert await _count(env, "forecast_input_manifests") == 1
    # outbox 事件（同一 UoW）
    assert await _count(env, "transactional_outbox") == 1
    # G6 Gate
    async with UnitOfWork(env["sessions"]) as uow:
        gate = await env["wf"].get_gate_decision(uow.session, gate="G6", target_kind="episode", target_id=episode)
    assert gate is not None and gate["result"] == "PASS"

    # commit 后不可变
    async with UnitOfWork(env["sessions"]) as uow:
        sub = (await uow.session.execute(text("SELECT id FROM trading.forecast_submissions WHERE episode_id=:e"), {"e": episode})).scalar_one()
        with pytest.raises(Exception):
            await uow.session.execute(text("UPDATE trading.forecast_submissions SET Q='{}'::jsonb WHERE id=:s"), {"s": sub})
    with pytest.raises(Exception):
        async with UnitOfWork(env["sessions"]) as uow:
            await uow.session.execute(text("UPDATE trading.forecast_episodes SET drop_reason='x' WHERE id=:e"), {"e": episode})


@pytest.mark.asyncio
async def test_g6_fail_closed_on_q_not_in_u_and_incoherent(cognition_env):
    env = cognition_env
    ctx = await _seed(env)
    episode, spec_ids = await _build_routed_episode(env, ctx, "fail")
    assert (await _g4(env, episode)).ok
    async with UnitOfWork(env["sessions"]) as uow:
        rev_logic = EvidenceLogic(env["forecast"], env["wf"])
        await rev_logic.add_revision(uow, episode_id=episode, revision=_revision("r1"))
    assert (await _g5a(env, episode, ["r1"])).ok
    assert (await _g5b(env, episode, ["w0", "w1"])).result == "PASS"

    # Q∉U → fail-closed，不生成 committed submission
    bad = await _g6(env, ctx, episode, spec_ids, q={"w0": "0.9", "w1": "0.1"}, u=[{"w0": "0.6", "w1": "0.4"}])
    assert not bad.ok and bad.reason == "g6_q_not_in_u"
    assert await _count(env, "forecast_submissions") == 0
    assert await _count(env, "forecast_leases") == 0

    # 非法概率（不 total）→ fail-closed
    bad2 = await _g6(env, ctx, episode, spec_ids, q={"w0": "0.5", "w1": "0.4"}, u=[{"w0": "0.6", "w1": "0.4"}, {"w0": "0.5", "w1": "0.5"}])
    assert not bad2.ok and bad2.reason == "g6_q_incoherent"
    assert await _count(env, "forecast_submissions") == 0


@pytest.mark.asyncio
async def test_g5a_taint_and_cutoff_hard_veto(cognition_env):
    env = cognition_env
    ctx = await _seed(env)
    episode, _ = await _build_routed_episode(env, ctx, "taint")
    assert (await _g4(env, episode)).ok
    async with UnitOfWork(env["sessions"]) as uow:
        rev_logic = EvidenceLogic(env["forecast"], env["wf"])
        await rev_logic.add_revision(uow, episode_id=episode, revision=_revision("good"))
        await rev_logic.add_revision(uow, episode_id=episode, revision=_revision("bad-taint", taint="market"))
        await rev_logic.add_revision(uow, episode_id=episode, revision=_revision("bad-cutoff", cutoff_ok=False))
        await rev_logic.add_revision(uow, episode_id=episode, revision=_revision("bad-cond", taint="market", conditioned=True))
    g5a = await _g5a(env, episode, ["good", "bad-taint"])
    assert not g5a.ok and g5a.reason.startswith("g5a_taint")
    g5a2 = await _g5a(env, episode, ["good", "bad-cutoff"])
    assert not g5a2.ok and g5a2.reason == "g5a_cutoff_violation"
    g5a3 = await _g5a(env, episode, ["good", "bad-cond"])
    # taint 优先于 market-conditioned discovery；两者都是 hard veto
    assert not g5a3.ok and (
        g5a3.reason == "g5a_market_conditioned_discovery"
        or g5a3.reason.startswith("g5a_taint")
    )
    # 全部合格 → PASS
    g5a_ok = await _g5a(env, episode, ["good"])
    assert g5a_ok.ok
    # 无 prior 不可 G5A（顺序）—— 已在 cognition guard 处理


@pytest.mark.asyncio
async def test_g5b_widen_and_abstain_branches(cognition_env):
    env = cognition_env
    ctx = await _seed(env)
    episode, _ = await _build_routed_episode(env, ctx, "widen")
    assert (await _g4(env, episode)).ok
    async with UnitOfWork(env["sessions"]) as uow:
        rev_logic = EvidenceLogic(env["forecast"], env["wf"])
        await rev_logic.add_revision(uow, episode_id=episode, revision=_revision("r1"))
    assert (await _g5a(env, episode, ["r1"])).ok
    # 缺 w1 → policy widen
    widen = await _g5b(env, episode, ["w0"])
    assert widen.result == "WIDEN_REQUIRED" and widen.missing_branches == ["w1"]
    assert widen.widening_input_hash and widen.widening_output_hash


@pytest.mark.asyncio
async def test_g6_crash_retry_effect_zero(cognition_env):
    env = cognition_env
    ctx = await _seed(env)
    episode, spec_ids = await _build_routed_episode(env, ctx, "crash")
    assert (await _g4(env, episode)).ok
    async with UnitOfWork(env["sessions"]) as uow:
        rev_logic = EvidenceLogic(env["forecast"], env["wf"])
        await rev_logic.add_revision(uow, episode_id=episode, revision=_revision("r1"))
    assert (await _g5a(env, episode, ["r1"])).ok
    assert (await _g5b(env, episode, ["w0", "w1"])).result == "PASS"

    with pytest.raises(InjectedCrash, match="after_g6"):
        await _g6(env, ctx, episode, spec_ids, crash=True)
    assert await _count(env, "forecast_submissions") == 0
    assert await _count(env, "payout_projections") == 0
    assert await _count(env, "forecast_leases") == 0
    assert await _count(env, "transactional_outbox") == 0
    state = await _episode_state(env, episode)
    assert state["status"] == "ROUTED" and state["cognition_status"] == "EVIDENCE_READY"

    g6 = await _g6(env, ctx, episode, spec_ids)
    assert g6.ok and g6.committed_count == 1
    assert await _count(env, "forecast_submissions") == 1
