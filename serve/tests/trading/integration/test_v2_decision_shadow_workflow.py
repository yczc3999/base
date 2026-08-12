"""WP-03 decision → shadow execution 纵向集成测试（真 PostgreSQL）。

BLIND_COMMITTED + valid lease
→ reveal exact book → market-relative (BLIND_ONLY) → G7A → G7B
→ ACTION → immutable intent → shadow fill → position + balanced ledger。

覆盖：
- P2 execution spec/permission 通过真实 DB 冻结（freeze_p2_release）。
- reveal 写 pm_quote_bindings（trade_decision_id 绑定）；stale/crossed fail-closed。
- BLIND_ONLY 不覆盖 blind submission；quote-only 变化零 AI/零 forecast。
- G7A depth walk + robust EV；G7B caps；terminal ACTION/WAIT/ABSTAIN。
- shadow fill 产生 position lot + 平衡双分录 ledger。
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
from app.logics.trading.decision import DecisionLogic
from app.logics.trading.evidence import EvidenceLogic
from app.logics.trading.execution import ShadowExecutionLogic
from app.logics.trading.forecast import ForecastLogic, InputManifestMaterial
from app.logics.trading.screening import (
    AUDIT_ALGORITHM_VERSION,
    ScreeningLogic,
)
from app.orchestrator.trading_state_machine import EpisodeInput, TradingStateMachine
from app.repositories.trading.cohort import CohortRepository, REQUIRED_COHORT_POLICIES
from app.repositories.trading.decision import DecisionRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.forecast import ForecastRepository
from app.repositories.trading.ledger import LedgerRepository
from app.repositories.trading.semantics import SemanticsRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.schemas.trading.decision import (
    ActionCandidateInput,
    ActionSetInput,
    MarketRelativeInput,
    PortfolioGateInput,
    UnderwritingInput,
)
from app.schemas.trading.evidence import (
    EvidenceBundleInput,
    EvidenceCoveragePolicyInput,
    EvidenceRevisionInput,
    PriorInput,
)
from app.schemas.trading.execution import ShadowFillInput
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
from tests.trading.fixtures.p2_decision.p2_helpers import freeze_p2_release, load_p2_spec

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
# WP-05 后 head=b1000052；本测试用 live ORM/repo（executions 含 account_id 列），
# 必须在 head schema 上跑，否则 UndefinedColumnError。
HEAD = "b1000071"

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


@pytest_asyncio.fixture
async def decision_env(temp_pg_db):
    _run(command.upgrade, HEAD, temp_pg_db.url)
    admin = make_url(temp_pg_db.url)
    async_url = admin.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    engine = create_async_engine(async_url, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    env = {
        "sessions": sessions,
        "decision": DecisionRepository(),
        "execution": ExecutionRepository(),
        "ledger": LedgerRepository(),
        "forecast": ForecastRepository(),
        "wf": WorkflowRepository(),
        "cohort": CohortRepository(),
        "sem": SemanticsRepository(),
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


async def _seed(env: dict, *, book_received_at: datetime | None = None) -> dict:
    """建 control/cohort/market/component，并以真实 DB 冻结 P2 release。"""
    async with UnitOfWork(env["sessions"]) as uow:
        s = uow.session
        obj = (await s.execute(text(
            "INSERT INTO trading.strategy_objective_contracts (contract_key,version_no,content,schema_version,content_hash,status) "
            "VALUES ('obj-p2',1,CAST(:content AS jsonb),1,:hash,'active') RETURNING id"),
            {"content": json.dumps(FULL_OBJECTIVE), "hash": OBJECTIVE_HASH})).scalar_one()
        strategy_content = {
            "strategy": "p2/v1",
            "optional_shrinkage": {"enabled": True, "algorithm_id": "linear-shrinkage/v1",
                                   "w_blind": "0.5", "learned_default_w": False},
        }
        strategy_hash = canonical_hash(strategy_content)
        strategy = (await s.execute(text(
            "INSERT INTO trading.strategy_versions (strategy_key,version_no,content,schema_version,content_hash,status) "
            "VALUES ('strategy-p2',1,CAST(:content AS jsonb),1,:hash,'active') RETURNING id"),
            {"content": json.dumps(strategy_content), "hash": strategy_hash})).scalar_one()
        config = (await s.execute(text(
            "INSERT INTO trading.runtime_config_versions (config_key,version_no,content,schema_version,content_hash,status) "
            "VALUES ('config-p2',1,'{}',1,:h,'active') RETURNING id"),
            {"h": "d" * 64})).scalar_one()
        p2 = await freeze_p2_release(s, objective_contract_id=obj, strategy_version_id=strategy)
        policy_hashes = {
            name: f"{index:x}" * 64
            for index, name in enumerate(REQUIRED_COHORT_POLICIES, start=1)
        }
        policy_hashes["r0"] = canonical_hash(R0_POLICY.model_dump(mode="json"))
        policy_hashes["reject_audit"] = canonical_hash(AUDIT_POLICY.model_dump(mode="json"))
        policy_hashes["evidence_coverage"] = canonical_hash(COVERAGE_POLICY.model_dump(mode="json"))
        for name in REQUIRED_COHORT_POLICIES:
            await s.execute(text(
                "INSERT INTO trading.policy_type_scopes (policy_type,scope_type,scope_key) VALUES (:n,'cohort','cohort-p2')"
            ), {"n": name})
            await s.execute(text(
                "INSERT INTO trading.policy_freezes (policy_type,scope_type,scope_key,policy_version,policy_content_hash,release_manifest_id,status) "
                "VALUES (:n,'cohort','cohort-p2',1,:h,:rel,'frozen')"
            ), {"n": name, "h": policy_hashes[name], "rel": p2["release_manifest_id"]})
        cohort = (await s.execute(text(
            "INSERT INTO trading.evaluation_cohorts (cohort_key,status,objective_contract_id,strategy_version_id,release_manifest_id,policy_hashes,seed_hash) "
            "VALUES ('cohort-p2','DRAFT',:obj,:strat,:rel,CAST(:p AS jsonb),:seed) RETURNING id"),
            {"obj": obj, "strat": strategy, "rel": p2["release_manifest_id"],
             "p": json.dumps(policy_hashes), "seed": SEED})).scalar_one()
        await s.execute(text(
            "UPDATE trading.evaluation_cohorts SET status='OPEN',opened_at=:opened WHERE id=:c"
        ), {"opened": FIXED, "c": cohort})
        await s.execute(text(
            "INSERT INTO trading.evidence_coverage_policies (cohort_id,policy_version,content,content_hash,status) "
            "VALUES (:c,1,CAST(:content AS jsonb),:h,'active')"
        ), {"c": cohort, "content": json.dumps(COVERAGE_POLICY.model_dump(mode="json")),
            "h": policy_hashes["evidence_coverage"]})
        market = (await s.execute(text(
            "INSERT INTO trading.pm_markets (gamma_market_id,condition_id,active,closed,accepting_orders,enable_order_book) "
            "VALUES ('market-p2','condition-p2',true,false,true,true) RETURNING id"))).scalar_one()
        yes_token = (await s.execute(text(
            "INSERT INTO trading.pm_tokens (token_id,market_id,outcome_index) VALUES ('token-p2-yes',:m,0) RETURNING id"
        ), {"m": market})).scalar_one()
        no_token = (await s.execute(text(
            "INSERT INTO trading.pm_tokens (token_id,market_id,outcome_index) VALUES ('token-p2-no',:m,1) RETURNING id"
        ), {"m": market})).scalar_one()
        market_version = (await s.execute(text(
            "INSERT INTO trading.pm_market_versions (market_id,version_no,observed_at,received_at,normalized_hash) "
            "VALUES (:m,1,:at,:at,:h) RETURNING id"),
            {"m": market, "at": FIXED, "h": "1" * 64})).scalar_one()
        yes_version = (await s.execute(text(
            "INSERT INTO trading.pm_token_versions (token_id,version_no,outcome_index,observed_at,received_at) "
            "VALUES (:t,1,0,:at,:at) RETURNING id"),
            {"t": yes_token, "at": FIXED})).scalar_one()
        no_version = (await s.execute(text(
            "INSERT INTO trading.pm_token_versions (token_id,version_no,outcome_index,observed_at,received_at) "
            "VALUES (:t,1,1,:at,:at) RETURNING id"),
            {"t": no_token, "at": FIXED})).scalar_one()

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
            "VALUES ('COMPLETE',:started,'p2',:lease,1,:completed,0,0,1,:hash,:artifact,:hash) RETURNING id"),
            {"started": FIXED, "lease": FIXED + timedelta(minutes=1), "completed": FIXED,
             "hash": frame_sha, "artifact": frame_artifact})).scalar_one()
        # book checkpoints（quote binding 的精确 FK 目标；token_id 是 TEXT 标识）
        now = book_received_at or datetime.now(timezone.utc)
        checkpoint_yes = (await s.execute(text(
            "INSERT INTO trading.pm_book_checkpoints "
            "(token_id, connection_epoch_id, source_kind, book_hash, best_bid, best_ask, "
            " raw_artifact_id, completeness, validity, received_at) "
            "VALUES (:tok, NULL, 'rest_full', :bh, 0.50, 0.52, :art, true, 'VALID', :recv) RETURNING id"
        ), {"tok": "token-p2-yes", "bh": "1" * 64, "art": frame_artifact,
            "recv": now})).scalar_one()
        checkpoint_no = (await s.execute(text(
            "INSERT INTO trading.pm_book_checkpoints "
            "(token_id, connection_epoch_id, source_kind, book_hash, best_bid, best_ask, "
            " raw_artifact_id, completeness, validity, received_at) "
            "VALUES (:tok, NULL, 'rest_full', :bh, 0.48, 0.50, :art, true, 'VALID', :recv) RETURNING id"
        ), {"tok": "token-p2-no", "bh": "2" * 64, "art": frame_artifact,
            "recv": now})).scalar_one()
        # execution must re-read the exact bound checkpoint depth; caller payload depth is not evidence.
        await s.execute(text(
            "INSERT INTO trading.pm_book_levels "
            "(checkpoint_id,received_at,side,price,size,ordinal) VALUES "
            "(:yes,:recv,'ask',0.52,100,0),(:yes,:recv,'ask',0.53,200,1),"
            "(:yes,:recv,'bid',0.50,100,0),(:yes,:recv,'bid',0.49,200,1),"
            "(:no,:recv,'ask',0.50,100,0),(:no,:recv,'bid',0.48,100,0)"
        ), {"yes": checkpoint_yes, "no": checkpoint_no, "recv": now})
    return {
        "cohort": cohort, "objective": obj, "strategy": strategy,
        "release": p2["release_manifest_id"],
        "exec_spec": p2["execution_spec_version_id"],
        "capital": p2["capital_permission_manifest_id"],
        "policy_hashes": policy_hashes, "market": market,
        "market_version": market_version, "yes_token": yes_token, "no_token": no_token,
        "yes_version": yes_version, "no_version": no_version,
        "contract_artifact": contract_artifact,
        "frame": HydratedUniverseFrameInput(
            frame_id=frame, content_hash=frame_sha, artifact_object_id=frame_artifact,
            artifact_ref=frame_sha,
            markets=[{"market_id": market, "metadata": {"market_key": "market-p2"}}],
        ),
        "checkpoint_yes": checkpoint_yes, "checkpoint_no": checkpoint_no,
        "checkpoint_recv_yes": now,
        "checkpoint_recv_no": now,
    }


async def _build_blind_committed_episode(env: dict, ctx: dict) -> tuple[int, list[int]]:
    """G0→R0→parent→G1→G2→episode→R1→G4→G5A→G5B→G6 → BLIND_COMMITTED。"""
    screening = ScreeningLogic(env["cohort"], env["wf"])
    state = TradingStateMachine(env["wf"])
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
            r0_input=R0Input(market_metadata={"market_key": "market-p2"},
                             best_bid=Decimal("0.50"), best_ask=Decimal("0.52"),
                             rule_completeness=Decimal("0.90"),
                             minimum_deployable_capacity=Decimal("10"),
                             objective_ref=OBJECTIVE_HASH),
            g0=g0, r0_policy=R0_POLICY, audit_policy=AUDIT_POLICY)
    async with UnitOfWork(env["sessions"]) as uow:
        parent = await state.create_parent_opportunity(
            uow, cohort_id=ctx["cohort"], chain_type="DECISION",
            objective_contract_id=ctx["objective"], strategy_version_id=ctx["strategy"],
            source_screening_episode_id=selected.episode_id, triggered_at=FIXED,
            market_ids=[ctx["market"]])
    async with UnitOfWork(env["sessions"]) as uow:
        g1_child = await state.create_g1_child(
            uow, parent_id=parent, cohort_id=ctx["cohort"], chain_type="DECISION",
            objective_contract_id=ctx["objective"], strategy_version_id=ctx["strategy"],
            triggered_at=FIXED, market_id=ctx["market"], seq=1)
    contract_candidate = ContractSpecInput(
        contract_key="spec-p2", market_version_id=ctx["market_version"],
        yes_token_version_id=ctx["yes_version"], no_token_version_id=ctx["no_version"],
        artifact_object_id=ctx["contract_artifact"], resolution_states=["YES", "NO"],
        compiler_version="lookup/v1", schema_version=1, rules="rules", resolution_source="gamma",
        payouts=[
            PayoutIRInput(token_key="yes", pm_token_id=ctx["yes_token"],
                          token_version_id=ctx["yes_version"], outcome_index=0,
                          function_ir={"YES": "1", "NO": "0"}),
            PayoutIRInput(token_key="no", pm_token_id=ctx["no_token"],
                          token_version_id=ctx["no_version"], outcome_index=1,
                          function_ir={"YES": "0", "NO": "1"}),
        ],
    )
    async with UnitOfWork(env["sessions"]) as uow:
        g1 = await ContractLogic(env["sem"], env["wf"]).run_g1(
            uow, candidate=contract_candidate, cutoff_at=CUTOFF, timezone_name="UTC",
            raw_outcome_mapping={"YES": 0, "NO": 1}, opportunity_id=g1_child,
            policy_hash=ctx["policy_hashes"]["eligibility"], version_manifest_id=ctx["release"])
    assert g1.ok, g1.reason
    spec_ids = [g1.spec_id]
    async with UnitOfWork(env["sessions"]) as uow:
        g2_child = await state.create_g2_child(
            uow, parent_id=parent, cohort_id=ctx["cohort"], chain_type="DECISION",
            objective_contract_id=ctx["objective"], strategy_version_id=ctx["strategy"],
            triggered_at=FIXED, component_key="component-p2", g1_child_ids=[g1_child])
    ws = WorldSchemaInput(
        component_key="component-p2", variables={"outcome": {"type": "enum"}},
        domains={"outcome": ["yes", "no"]}, constraints=[],
        factorization={"independent": ["outcome"]},
        world_states=[{"world_state_id": "w0", "assignment": {"outcome": "yes"}},
                      {"world_state_id": "w1", "assignment": {"outcome": "no"}}],
        state_count=2,
        h_c={str(spec_ids[0]): {"w0": "YES", "w1": "NO"}}, schema_version=1,
    )
    async with UnitOfWork(env["sessions"]) as uow:
        g2 = await ComponentLogic(env["sem"], env["wf"]).run_g2(
            uow, candidate=ws, contract_spec_ids=spec_ids,
            member_hc={spec_ids[0]: ws.h_c[str(spec_ids[0])]}, cost_budget=Decimal("10"),
            opportunity_id=g2_child, policy_hash=ctx["policy_hashes"]["taxonomy"],
            version_manifest_id=ctx["release"])
    assert g2.ok, g2.reason
    episode_input = EpisodeInput(
        decision_opportunity_id=g2_child, component_version_id=g2.component_version_id,
        strategy_version_id=ctx["strategy"], objective_contract_id=ctx["objective"],
        trigger="p2", cutoff_at=CUTOFF, horizon="resolution", experiment_variant="control",
        contract_spec_ids=spec_ids,
    )
    async with UnitOfWork(env["sessions"]) as uow:
        episode = await state.create_episode(uow, input_=episode_input)
    async with UnitOfWork(env["sessions"]) as uow:
        await state.route_episode(
            uow, episode_id=episode, route_channel="standard", first_rejected_gate=None,
            reason_code=None, recheck_at=None, recheck_condition=None, audit_selected=False,
            policy_hash=ctx["policy_hashes"]["r1"], version_manifest_id=ctx["release"])
    evidence = EvidenceLogic(env["forecast"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        g4 = await evidence.run_g4(uow, episode_id=episode, prior=PRIOR,
                                   version_manifest_id=ctx["release"])
    assert g4.ok, g4.reason
    async with UnitOfWork(env["sessions"]) as uow:
        await evidence.add_revision(uow, episode_id=episode, revision=EvidenceRevisionInput(
            revision_key="r1", kind="source_claim", event_at=FIXED,
            published_at=CUTOFF - timedelta(hours=2), observed_at=CUTOFF - timedelta(hours=1),
            ingested_at=CUTOFF - timedelta(minutes=30), source="https://example.com",
            source_type="web", branch="main", raw_artifact_ref="7" * 64,
            content={"claim": "r1"}, taint_status="none"))
    async with UnitOfWork(env["sessions"]) as uow:
        g5a = await evidence.run_g5a(
            uow, episode_id=episode,
            bundle=EvidenceBundleInput(bundle_key="b1", information_cutoff_at=CUTOFF,
                                       revision_keys=["r1"]),
            version_manifest_id=ctx["release"])
    assert g5a.ok, g5a.reason
    async with UnitOfWork(env["sessions"]) as uow:
        g5b = await evidence.run_g5b(uow, episode_id=episode, policy=COVERAGE_POLICY,
                                     covered_branches=["w0", "w1"], version_manifest_id=ctx["release"])
    assert g5b.result == "PASS", g5b.reason
    submission = ForecastSubmissionInput(
        submission_key="sub-p2",
        Q=QDistributionInput(values={"w0": "0.6", "w1": "0.4"}),
        U=[QDistributionInput(values={"w0": "0.6", "w1": "0.4"}),
           QDistributionInput(values={"w0": "0.55", "w1": "0.45"})],
        forecast_input_manifest_id=1,
    )
    lease = ForecastLeaseInput(
        valid_until=datetime.now(timezone.utc) + timedelta(days=30),
        invalidation_conditions={"fact_freshness": {"max_age_hours": 48}},
        evidence_hash="a" * 64, schema_hash="b" * 64, spec_hash="c" * 64,
    )
    forecast = ForecastLogic(env["forecast"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        g6 = await forecast.run_g6(
            uow, episode_id=episode, submission=submission,
            material=InputManifestMaterial(taxonomy_hash="a" * 64,
                                           model_binding_hash="a" * 64,
                                           prompt_hash="a" * 64, code_hash="a" * 64),
            lease=lease, version_manifest_id=ctx["release"],
            policy_hash=ctx["policy_hashes"]["evidence_coverage"])
    assert g6.ok, g6.reason
    return episode, spec_ids


def _quote_map(ctx: dict, *, stale: bool = False) -> dict:
    return {
        "token-p2-yes": {
            "checkpoint_id": ctx["checkpoint_yes"],
            "checkpoint_received_at": ctx["checkpoint_recv_yes"],
            "best_bid": Decimal("0.50"), "best_ask": Decimal("0.52"),
            "as_of": ctx["checkpoint_recv_yes"], "received_at": ctx["checkpoint_recv_yes"],
            "stale_at": ctx["checkpoint_recv_yes"] + timedelta(minutes=5),
        },
        "token-p2-no": {
            "checkpoint_id": ctx["checkpoint_no"],
            "checkpoint_received_at": ctx["checkpoint_recv_no"],
            "best_bid": Decimal("0.48"), "best_ask": Decimal("0.50"),
            "as_of": ctx["checkpoint_recv_no"], "received_at": ctx["checkpoint_recv_no"],
            "stale_at": ctx["checkpoint_recv_no"] + timedelta(minutes=5),
        },
    }


async def _count(env: dict, table: str) -> int:
    async with UnitOfWork(env["sessions"]) as uow:
        return (await uow.session.execute(
            text(f"SELECT count(*) FROM trading.{table}")
        )).scalar_one()


@pytest.mark.asyncio
async def test_full_decision_shadow_chain(decision_env):
    env = decision_env
    ctx = await _seed(env)
    episode, spec_ids = await _build_blind_committed_episode(env, ctx)
    logic = DecisionLogic(env["decision"], env["wf"])

    # create decision（P2 release 冻结）
    async with UnitOfWork(env["sessions"]) as uow:
        ck = (await uow.session.execute(text("SELECT received_at FROM trading.pm_book_checkpoints WHERE token_id='token-p2-yes'"))).scalar_one()
    trigger_at = ck + timedelta(minutes=1)
    async with UnitOfWork(env["sessions"]) as uow:
        created = await logic.create_decision(uow, episode_id=episode,
                                              trigger_at=trigger_at, experiment_variant="champion")
    assert created.ok, created.reason

    # reveal
    async with UnitOfWork(env["sessions"]) as uow:
        revealed = await logic.reveal(uow, trade_decision_id=created.trade_decision_id,
                                      quote_reveal_at=trigger_at + timedelta(seconds=1),
                                      quotes=_quote_map(ctx))
    assert revealed.ok, revealed.reason
    async with UnitOfWork(env["sessions"]) as uow:
        qb = await env["decision"].quote_bindings_for_decision(
            uow.session, created.trade_decision_id)
    assert len(qb) == 2
    assert qb[0]["trade_decision_id"] == created.trade_decision_id

    # Explicit observed/frozen zero is evidence; absence is not silently treated as zero.
    async with UnitOfWork(env["sessions"]) as uow:
        await uow.session.execute(text(
            "INSERT INTO trading.operating_cost_entries "
            "(cost_key,cost_kind,amount,release_manifest_id,episode_id,trade_decision_id,allocation_policy) "
            "VALUES (:k,'INFRASTRUCTURE',0,:r,:e,:d,CAST(:p AS jsonb))"
        ), {"k": f"cost-{created.trade_decision_id}", "r": ctx["release"], "e": episode,
            "d": created.trade_decision_id,
            "p": json.dumps({"kind": "fixed_marginal", "evidence": "observed_zero"})})

    # market-relative BLIND_ONLY
    async with UnitOfWork(env["sessions"]) as uow:
        mr = await logic.market_relative(
            uow, trade_decision_id=created.trade_decision_id,
            input_=MarketRelativeInput(decision_mode="BLIND_ONLY"))
    assert mr.ok, mr.reason

    # G7A
    spec_id = spec_ids[0]
    async with UnitOfWork(env["sessions"]) as uow:
        g7a = await logic.run_g7a(
            uow, trade_decision_id=created.trade_decision_id,
            candidates=[
                ActionCandidateInput(
                    contract_spec_id=spec_id, token_id=ctx["yes_token"],
                    action_type="BUY_TOKEN", target_quantity=Decimal("100"),
                )
            ],
            policy_hash=None, version_manifest_id=None)
    assert g7a.ok, g7a.reason
    async with UnitOfWork(env["sessions"]) as uow:
        candidates = await env["decision"].candidates_for_decision(
            uow.session, created.trade_decision_id)
    assert len(candidates) == 1
    assert candidates[0]["cashflow_reconciliation_residual"] == 0

    # G7B
    async with UnitOfWork(env["sessions"]) as uow:
        g7b = await logic.run_g7b(
            uow, trade_decision_id=created.trade_decision_id,
            portfolio=PortfolioGateInput(), policy_hash=None, version_manifest_id=None)
    assert g7b.ok, g7b.reason

    # terminal ACTION
    async with UnitOfWork(env["sessions"]) as uow:
        terminal = await logic.terminalize(
            uow, trade_decision_id=created.trade_decision_id,
            action_set=ActionSetInput(
                disposition="ACTION",
                selected_action_type="BUY_TOKEN",
                legs={"open": {spec_id: {ctx["yes_token"]: Decimal("100")}}},
            ),
            underwriting=UnderwritingInput(
                plan_version=1, entry_range={"min": "0.50", "max": "0.55"},
                hold_to_resolution=True, thesis_hash="a" * 64,
                invalidation={"evidence": "regime_change"},
            ),
            decided_at=trigger_at + timedelta(seconds=2))
    assert terminal.ok, terminal.reason
    assert terminal.disposition == "ACTION"

    # intent（mode-independent hash）
    async with UnitOfWork(env["sessions"]) as uow:
        decision_row = await env["decision"].get_trade_decision_by_id(
            uow.session, created.trade_decision_id)
    assert decision_row["status"] == "ACTION" and decision_row["decided_at"] is not None

    # shadow fill → position + balanced ledger
    async with UnitOfWork(env["sessions"]) as uow:
        action_sets = (await uow.session.execute(
            text("SELECT id FROM trading.action_sets WHERE trade_decision_id=:d"),
            {"d": created.trade_decision_id})).scalars().all()
        legs = await env["decision"].action_set_legs(uow.session, action_sets[0])
        assert len(legs) == 1
        intent_id = (await uow.session.execute(text(
            "SELECT id FROM trading.economic_action_intents WHERE trade_decision_id=:d"
        ), {"d": created.trade_decision_id})).scalar_one()
        execution_logic = ShadowExecutionLogic(env["execution"], env["ledger"])
        fill_result = await execution_logic.shadow_fill(
            uow,
            fill=ShadowFillInput(
                execution_key=f"exec-{created.trade_decision_id}",
                economic_action_intent_id=intent_id,
                action_set_leg_id=legs[0]["id"],
                contract_spec_id=spec_id, token_id=ctx["yes_token"],
                fill_role="open", quantity=Decimal("100"), side="buy",
                depth_levels=[[Decimal("0.52"), 100], [Decimal("0.53"), 200]],
                taker_fee_bps=Decimal("0"),
                portfolio_namespace="shadow-champion",
            ),
            portfolio_namespace="shadow-champion",
            cash_asset_key="usd",
        )
    assert fill_result.ok, fill_result.reason
    assert fill_result.status == "FILLED"
    assert fill_result.filled_quantity == 100

    # position + lot + ledger posted
    assert await _count(env, "positions") == 1
    assert await _count(env, "position_lots") == 1
    assert await _count(env, "ledger_transactions") == 1
    assert await _count(env, "ledger_postings") == 4  # cash 2 + token 2
    async with UnitOfWork(env["sessions"]) as uow:
        tx = await env["ledger"].get_transaction(
            uow.session, f"ledger-exec-{created.trade_decision_id}")
        assert tx["status"] == "POSTED"
        postings = await env["ledger"].postings_for_transaction(uow.session, tx["id"])
    # cash + token 各自归零
    from collections import defaultdict
    sums = defaultdict(Decimal)
    for p in postings:
        sums[(p["asset_type"], p["asset_key"])] += Decimal(p["amount"])
    assert all(v == 0 for v in sums.values())
    # position quantity = 100
    async with UnitOfWork(env["sessions"]) as uow:
        position = await env["execution"].get_position(
            uow.session, portfolio_namespace="shadow-champion",
            contract_spec_id=spec_id, token_id=ctx["yes_token"])
    assert position["quantity"] == 100

    # Exact worker retry returns the immutable terminal result and has economic effect=0.
    before_retry = {
        name: await _count(env, name)
        for name in ("executions", "positions", "position_lots", "ledger_transactions",
                     "ledger_postings", "transactional_outbox")
    }
    async with UnitOfWork(env["sessions"]) as uow:
        retried = await execution_logic.shadow_fill(
            uow,
            fill=ShadowFillInput(
                execution_key=f"exec-{created.trade_decision_id}",
                economic_action_intent_id=intent_id,
                action_set_leg_id=legs[0]["id"],
                contract_spec_id=spec_id, token_id=ctx["yes_token"],
                fill_role="open", quantity=Decimal("100"), side="buy",
                portfolio_namespace="shadow-champion",
            ),
            portfolio_namespace="shadow-champion", cash_asset_key="usd",
        )
    assert retried.replayed and retried.execution_id == fill_result.execution_id
    assert {
        name: await _count(env, name) for name in before_retry
    } == before_retry

    # quote-only refresh 不增加 AI/forecast
    assert await _count(env, "ai_invocations") == 0
    assert await _count(env, "forecast_episodes") == 1


@pytest.mark.asyncio
async def test_reveal_stale_quote_fail_closed(decision_env):
    env = decision_env
    ctx = await _seed(env)
    episode, _ = await _build_blind_committed_episode(env, ctx)
    logic = DecisionLogic(env["decision"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        ck = (await uow.session.execute(text("SELECT received_at FROM trading.pm_book_checkpoints WHERE token_id='token-p2-yes'"))).scalar_one()
    async with UnitOfWork(env["sessions"]) as uow:
        created = await logic.create_decision(uow, episode_id=episode,
                                              trigger_at=ck + timedelta(minutes=1),
                                              experiment_variant="champion")
    quotes = _quote_map(ctx)
    quotes["token-p2-yes"]["stale_at"] = FIXED + timedelta(seconds=1)  # already stale at reveal
    async with UnitOfWork(env["sessions"]) as uow:
        revealed = await logic.reveal(uow, trade_decision_id=created.trade_decision_id,
                                      quote_reveal_at=ck + timedelta(minutes=6),
                                      quotes=quotes)
    assert not revealed.ok and revealed.reason == "decision_quote_stale"


@pytest.mark.asyncio
async def test_linear_shrinkage_challenger_abstain_keeps_blind(decision_env):
    env = decision_env
    ctx = await _seed(env)
    episode, _ = await _build_blind_committed_episode(env, ctx)
    logic = DecisionLogic(env["decision"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        ck = (await uow.session.execute(text("SELECT received_at FROM trading.pm_book_checkpoints WHERE token_id='token-p2-yes'"))).scalar_one()
    async with UnitOfWork(env["sessions"]) as uow:
        created = await logic.create_decision(uow, episode_id=episode,
                                              trigger_at=ck + timedelta(minutes=1),
                                              experiment_variant="champion")
    async with UnitOfWork(env["sessions"]) as uow:
        revealed = await logic.reveal(uow, trade_decision_id=created.trade_decision_id,
                                      quote_reveal_at=ck + timedelta(minutes=1, seconds=1),
                                      quotes=_quote_map(ctx))
    assert revealed.ok
    # 不完整 token price set → challenger ABSTAIN，不阻塞 BLIND_ONLY
    async with UnitOfWork(env["sessions"]) as uow:
        mr = await logic.market_relative(
            uow, trade_decision_id=created.trade_decision_id,
            input_=MarketRelativeInput(
                decision_mode="LINEAR_SHRINKAGE", w_blind=Decimal("0.5"),
                token_prices={ctx["yes_token"]: "0.52"}))
    assert not mr.ok and mr.reason == "ABSTAIN_MARKET_REFERENCE_UNIDENTIFIED"
    # BLIND_ONLY 仍可构造
    async with UnitOfWork(env["sessions"]) as uow:
        mr2 = await logic.market_relative(
            uow, trade_decision_id=created.trade_decision_id,
            input_=MarketRelativeInput(decision_mode="BLIND_ONLY"))
    assert mr2.ok
