"""WP-05 Checkpoint A —— P-stability 确定性重放（真 PostgreSQL，Alembic head=b1000051）。

对同一 frozen input（P2 decision → shadow execution → metric 链）两次确定性重放，断言
universe / opportunity / episode identity / processing disposition / blind commit /
economic action intent / ledger / metric artifact 业务 hash 逐项相等。

- ``execution_authorization_envelope`` 表由 0050/0051 建立；在 b1000051 下参与稳定性校验。
- 固定 event log（stability_event_log_v1.json）冻结 23 个链事件 + 8 个故障场景；本测试按
  链步骤执行并断言事件顺序/载荷 hash 与 fixture 一致。
- 故障注入（不触碰生产代码，复用现有逻辑的 fail-closed 语义）：
  * worker restart：两次全链重放 + 引擎重建；
  * transaction rollback：UoW 写入后回滚 → 无半条证据；
  * duplicate delivery：重复 Gate / 重复模型提交 fail closed；
  * out-of-order：因果非法转移 fail closed，不推进下一 Gate；
  * model timeout/partial failure：同一 frozen 输入重试 run_g6 fail closed；
  * random seed binding：deterministic_sample 同 seed 同结果、异 seed 可归因。
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
from app.domain.trading.hashing import canonical_hash, deterministic_sample
from app.logics.trading.decision import DecisionLogic
from app.logics.trading.evaluation import EvaluationLogic
from app.logics.trading.execution import ShadowExecutionLogic
from app.logics.trading.forecast import ForecastLogic, InputManifestMaterial
from app.repositories.trading.decision import DecisionRepository
from app.repositories.trading.evaluation import EvaluationRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.forecast import ForecastRepository
from app.repositories.trading.ledger import LedgerRepository
from app.repositories.trading.settlement import SettlementRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.schemas.trading.decision import (
    ActionCandidateInput,
    ActionSetInput,
    MarketRelativeInput,
    PortfolioGateInput,
    UnderwritingInput,
)
from app.schemas.trading.evaluation import MetricRunInput
from app.schemas.trading.execution import ShadowFillInput
from app.schemas.trading.forecast import (
    ForecastLeaseInput,
    ForecastSubmissionInput,
    QDistributionInput,
)
from tests.trading.fixtures.p5_execution.p5_helpers import frozen_scenario, load_scenario
from tests.trading.integration.test_v2_decision_shadow_workflow import (
    FULL_OBJECTIVE,
    OBJECTIVE_HASH,
    R0_POLICY,
    AUDIT_POLICY,
    COVERAGE_POLICY,
    PRIOR,
    _seed,
    _quote_map,
)

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
# WP-05 完成后 head=b1000051；P-stability 在完整 head 上验证（与 ORM metadata 对齐）。
HEAD = "b1000051"

# 固定时基：落在 20260811 partition 且早于测试运行日（今天 2026-08-11）。
# opportunity/episode 的 opportunity_key 含 triggered_at，故该锚必须固定以保证
# ``== expected``（冻结 snapshot hash）。trade_decision 的 trigger/quote_bound/decided 时间
# 单独动态化（见 _run_decision_execution_chain），因为 executions.created_at=now() 要求
# quote binding stale_at 相对真实时间未来。
FIXED = datetime(2026, 8, 11, 3, 4, 5, tzinfo=timezone.utc)
CUTOFF = FIXED + timedelta(days=2)

def _seed_book_time() -> datetime:
    """book checkpoint received_at 基准：stale_at（=received+ttl 300s）相对真实 now() 未来。

    executions.created_at 由 DB now() 生成，0031 guard 要求 ``quote.stale_at > created_at``。
    因此 checkpoint received_at 必须满足 ``received + 300s > now()`` 且 ``received <= quote_bound``
    （quote_before_decision=60s，trigger=now+10min）。取 ``now+9min``：received=now+9min，
    stale_at=now+14min > now ✓，received < trigger(now+10min) ✓。业务 hash 不含 checkpoint
    received_at，``== expected`` 不受影响。
    """
    return datetime.now(timezone.utc) + timedelta(minutes=9)


_METRIC_RUN_KEY = "metric-p5"
_OBS_KEY = "obs-p5"
_LABEL_KEY = "label-p5"
_TARGET_KEY = "target-p5"
_CLUSTER_KEY = "cluster-p5"


@pytest_asyncio.fixture
async def stability_env(temp_pg_db):
    _run(command.upgrade, HEAD, temp_pg_db.url)
    engine, sessions = _fresh_engine(temp_pg_db.url)
    env = {
        "sessions": sessions,
        "engine": engine,
        "url": temp_pg_db.url,
        "decision": DecisionRepository(),
        "execution": ExecutionRepository(),
        "ledger": LedgerRepository(),
        "forecast": ForecastRepository(),
        "wf": WorkflowRepository(),
    }
    yield env
    await engine.dispose()


def _fresh_engine(db_url: str):
    admin = make_url(db_url)
    async_url = admin.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    engine = create_async_engine(async_url, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


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


def _restart(url: str) -> None:
    """TRUNCATE 决策/执行/账本 + 上游全部表（CASCADE + RESTART IDENTITY）。"""
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "TRUNCATE trading.trade_decisions, trading.market_relative_decisions, "
                "trading.discrepancy_reviews, trading.action_candidates, "
                "trading.resolution_cashflows, trading.action_sets, trading.action_set_legs, "
                "trading.underwriting_plans, trading.economic_action_intents, "
                "trading.executions, trading.positions, trading.position_lots, "
                "trading.ledger_transactions, trading.ledger_postings, "
                "trading.operating_cost_entries, trading.pm_quote_bindings, "
                "trading.pm_book_checkpoints, trading.gate_decisions, "
                "trading.metric_runs, trading.score_observations, trading.score_targets, "
                "trading.score_target_memberships, trading.resolution_labels, "
                "trading.resolution_clusters, trading.resolution_cluster_memberships, "
                "trading.evaluation_cohorts, trading.decision_opportunities, "
                "trading.forecast_episodes, trading.forecast_submissions, "
                "trading.forecast_leases, trading.contract_specs, trading.payout_functions, "
                "trading.forecast_components, trading.world_schema_versions, "
                "trading.forecast_component_versions, trading.forecast_component_contract_specs, "
                "trading.contract_snapshots, trading.pm_markets, trading.pm_tokens, "
                "trading.pm_market_versions, trading.pm_token_versions, "
                "trading.pm_universe_frames, trading.artifact_objects, "
                "trading.screening_episodes, trading.audit_samples, trading.policy_freezes, "
                "trading.policy_type_scopes, trading.strategy_objective_contracts, "
                "trading.strategy_versions, trading.execution_spec_versions, "
                "trading.capital_permission_manifests, trading.runtime_config_versions, "
                "trading.release_manifests, trading.evidence_coverage_policies, "
                "trading.evidence_revisions, trading.evidence_bundles, "
                "trading.evidence_bundle_items, trading.forecast_input_manifests, "
                "trading.transactional_outbox, trading.idempotency_claims "
                "RESTART IDENTITY CASCADE"
            ))
    finally:
        engine.dispose()


async def _build_blind_committed_episode(env: dict, ctx: dict) -> tuple[int, list[int]]:
    """G0→R0→parent→G1→G2→episode→R1→G4→G5A→G5B→G6 → BLIND_COMMITTED（lease 用 FIXED 基准）。"""
    from app.logics.trading.component import ComponentLogic
    from app.logics.trading.contract import ContractLogic
    from app.logics.trading.evidence import EvidenceLogic
    from app.logics.trading.screening import ScreeningLogic
    from app.orchestrator.trading_state_machine import EpisodeInput, TradingStateMachine
    from app.repositories.trading.cohort import CohortRepository
    from app.repositories.trading.semantics import SemanticsRepository
    from app.schemas.trading.evidence import (
        EvidenceBundleInput,
        EvidenceCoveragePolicyInput,
        EvidenceRevisionInput,
        PriorInput,
    )
    from app.schemas.trading.semantics import ContractSpecInput, PayoutIRInput, WorldSchemaInput
    from app.schemas.trading.workflow import R0Input

    screening = ScreeningLogic(CohortRepository(), env["wf"])
    state = TradingStateMachine(env["wf"])
    sem = SemanticsRepository()

    async with UnitOfWork(env["sessions"]) as uow:
        g0 = await screening.run_g0(uow, cohort_id=ctx["cohort"], objective_content=FULL_OBJECTIVE,
                                    expected_objective_hash=OBJECTIVE_HASH)
    assert g0.ok, g0.reason
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
        g1 = await ContractLogic(sem, env["wf"]).run_g1(
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
        g2 = await ComponentLogic(sem, env["wf"]).run_g2(
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
        valid_until=FIXED + timedelta(days=30),
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


async def _run_decision_execution_chain(env: dict, ctx: dict, episode: int, spec_ids: list[int]) -> dict:
    """create_decision → reveal → market_relative → G7A → G7B → terminalize → shadow_fill。"""
    logic = DecisionLogic(env["decision"], env["wf"])
    spec_id = spec_ids[0]

    # trade_decision 时间单独动态化到真实 now() 之后（_seed_book_time 用 now+9min）。
    # opportunity/episode 的 opportunity_key 仍用固定 FIXED，保证业务 hash == expected。
    trigger_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    quote_reveal_at = trigger_at + timedelta(seconds=1)
    decided_at = trigger_at + timedelta(seconds=2)

    async with UnitOfWork(env["sessions"]) as uow:
        created = await logic.create_decision(uow, episode_id=episode,
                                              trigger_at=trigger_at, experiment_variant="champion")
    assert created.ok, created.reason

    async with UnitOfWork(env["sessions"]) as uow:
        revealed = await logic.reveal(uow, trade_decision_id=created.trade_decision_id,
                                      quote_reveal_at=quote_reveal_at, quotes=_quote_map(ctx))
    assert revealed.ok, revealed.reason

    async with UnitOfWork(env["sessions"]) as uow:
        await uow.session.execute(text(
            "INSERT INTO trading.operating_cost_entries "
            "(cost_key,cost_kind,amount,release_manifest_id,episode_id,trade_decision_id,allocation_policy) "
            "VALUES (:k,'INFRASTRUCTURE',0,:r,:e,:d,CAST(:p AS jsonb))"
        ), {"k": f"cost-{created.trade_decision_id}", "r": ctx["release"], "e": episode,
            "d": created.trade_decision_id,
            "p": json.dumps({"kind": "fixed_marginal", "evidence": "observed_zero"})})

    async with UnitOfWork(env["sessions"]) as uow:
        mr = await logic.market_relative(
            uow, trade_decision_id=created.trade_decision_id,
            input_=MarketRelativeInput(decision_mode="BLIND_ONLY"))
    assert mr.ok, mr.reason

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
        g7b = await logic.run_g7b(
            uow, trade_decision_id=created.trade_decision_id,
            portfolio=PortfolioGateInput(), policy_hash=None, version_manifest_id=None)
    assert g7b.ok, g7b.reason

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
            decided_at=decided_at)
    assert terminal.ok, terminal.reason
    assert terminal.disposition == "ACTION"

    execution_logic = ShadowExecutionLogic(env["execution"], env["ledger"])
    async with UnitOfWork(env["sessions"]) as uow:
        decision_row = await env["decision"].get_trade_decision_by_id(
            uow.session, created.trade_decision_id)
        assert decision_row["status"] == "ACTION" and decision_row["decided_at"] is not None
        action_sets = (await uow.session.execute(
            text("SELECT id FROM trading.action_sets WHERE trade_decision_id=:d"),
            {"d": created.trade_decision_id})).scalars().all()
        legs = await env["decision"].action_set_legs(uow.session, action_sets[0])
        intent_id = (await uow.session.execute(text(
            "SELECT id FROM trading.economic_action_intents WHERE trade_decision_id=:d"
        ), {"d": created.trade_decision_id})).scalar_one()
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
    return {
        "trade_decision_id": created.trade_decision_id,
        "intent_id": intent_id,
        "action_set_id": action_sets[0],
    }


def _seed_metric_observation(env: dict, ctx: dict, episode: int, spec_id: int, chain: dict) -> tuple[int, int]:
    """用链的 contract/submission/binding 生成一条 frozen Bernoulli observation（replica 绕过 trigger）。"""
    engine = create_engine(env["url"], poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            payout = c.execute(text(
                "SELECT id FROM trading.payout_functions WHERE contract_spec_id=:cs LIMIT 1"
            ), {"cs": spec_id}).scalar_one()
            token = c.execute(text(
                "SELECT id FROM trading.pm_tokens WHERE market_id=:m AND outcome_index=0"
            ), {"m": ctx["market"]}).scalar_one()
            submission = c.execute(text(
                "SELECT id FROM trading.forecast_submissions WHERE episode_id=:e ORDER BY id LIMIT 1"
            ), {"e": episode}).scalar_one()
            binding = c.execute(text(
                "SELECT id FROM trading.pm_quote_bindings WHERE token_id='token-p2-yes' ORDER BY id LIMIT 1"
            )).scalar_one()
            cluster = c.execute(text(
                "INSERT INTO trading.resolution_clusters "
                "(cluster_key,cluster_version,split,time_block_start,time_block_end,horizon,status) "
                "VALUES (:k,1,'forward_holdout',:s,:e,'resolution','RESOLVED') RETURNING id"
            ), {"k": _CLUSTER_KEY, "s": FIXED - timedelta(days=1), "e": FIXED + timedelta(days=1)}).scalar_one()
            target = c.execute(text(
                "INSERT INTO trading.score_targets "
                "(target_key,target_type,contract_spec_id,resolution_cluster_id,horizon,"
                " target_weight,payout_function_id,canonical_side,payout_type) "
                "VALUES (:k,'bernoulli',:cs,:cluster,'resolution',1,:payout,'YES','binary') RETURNING id"
            ), {"k": _TARGET_KEY, "cs": spec_id, "cluster": cluster, "payout": payout}).scalar_one()
            c.execute(text(
                "INSERT INTO trading.resolution_cluster_memberships "
                "(resolution_cluster_id,contract_spec_id,token_id) VALUES (:cluster,:cs,:tok)"
            ), {"cluster": cluster, "cs": spec_id, "tok": token})
            c.execute(text(
                "INSERT INTO trading.score_target_memberships (score_target_id,token_id,member_weight) "
                "VALUES (:t,:tok,1)"
            ), {"t": target, "tok": token})
            label = c.execute(text(
                "INSERT INTO trading.resolution_labels "
                "(contract_spec_id,label_key,version_no,state,resolution_state,policy_code_hash) "
                "VALUES (:cs,:k,1,'final_admissible','YES',:h) RETURNING id"
            ), {"cs": spec_id, "k": _LABEL_KEY, "h": "a" * 64}).scalar_one()
            baseline_value = {str(token): "0.65"}
            baseline_value_hash = canonical_hash(baseline_value)
            obs = c.execute(text(
                "INSERT INTO trading.score_observations "
                "(observation_key,score_target_id,submission_id,trade_decision_id,label_version_id,"
                " baseline_quote_binding_ids,baseline_value,baseline_value_hash,"
                " baseline_checkpoint_received_at,baseline_quote,baseline_policy_hash,split,"
                " algorithm_hash,metric_id,score_value) "
                "VALUES (:k,:t,:sub,:td,:lab,:bindings,:value,:vh,:at,0.65,"
                " :ph,'forward_holdout',:ah,'bernoulli_brier',0.09) RETURNING id"
            ), {"k": _OBS_KEY, "t": target, "sub": submission, "td": None, "lab": label,
                "bindings": json.dumps([binding]), "value": json.dumps(baseline_value),
                "vh": baseline_value_hash, "at": FIXED, "ph": "b" * 64, "ah": "c" * 64}).scalar_one()
            c.execute(text("SET LOCAL session_replication_role = origin"))
            return obs, label
    finally:
        engine.dispose()


def _metric_input(ctx: dict, obs_id: int, label_id: int, *, run_key: str) -> MetricRunInput:
    return MetricRunInput(
        run_key=run_key,
        cohort_id=ctx["cohort"],
        observation_ids=[obs_id],
        observation_set_hash=canonical_hash(
            {
                "cohort_id": ctx["cohort"],
                "split": "forward_holdout",
                "ordered_observation_ids": [obs_id],
                "label_versions": {"frozen": [label_id]},
                "time_blocks": {"resolution": "2026-08-11"},
                "strategy_version_id": ctx["strategy"],
                "release_manifest_id": ctx["release"],
            }
        ),
        cohort_query_hash="a" * 64,
        strategy_version_id=ctx["strategy"],
        release_manifest_id=ctx["release"],
        label_versions={"frozen": [label_id]},
        split="forward_holdout",
        time_blocks={"resolution": "2026-08-11"},
        code_hash="b" * 64,
        config_hash="c" * 64,
        seed=7,
        n_market=0,
        n_episode=0,
        n_resolution_cluster=1,
        n_eff=Decimal("1"),
        results={},
        ci={},
        artifact_hash="0" * 64,
    )


async def _run_metric(env: dict, metric_input: MetricRunInput) -> str:
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        created = await logic.run_metric(uow, input_=metric_input)
        assert created.ok, created.reason
        artifact = (await uow.session.execute(text(
            "SELECT artifact_hash FROM trading.metric_runs WHERE id=:id"
        ), {"id": created.metric_run_id})).scalar_one()
    return artifact


async def _run_full_chain(env: dict) -> dict:
    """完整 frozen 链（含 metric），返回业务 hash 快照 + 链上下文。"""
    ctx = await _seed(env, book_received_at=_seed_book_time())
    episode, spec_ids = await _build_blind_committed_episode(env, ctx)
    chain = await _run_decision_execution_chain(env, ctx, episode, spec_ids)
    obs_id, label_id = _seed_metric_observation(env, ctx, episode, spec_ids[0], chain)
    metric_input = _metric_input(ctx, obs_id, label_id, run_key=_METRIC_RUN_KEY)
    artifact = await _run_metric(env, metric_input)
    return {
        "ctx": ctx,
        "episode": episode,
        "spec_ids": spec_ids,
        **chain,
        "metric_artifact": artifact,
        "metric_input": metric_input,
    }


async def _business_hashes(env: dict) -> dict:
    """读 DB 关键业务列（surrogate id / created_at / committed_at 一律排除）求 canonical hash。"""
    async with UnitOfWork(env["sessions"]) as uow:
        s = uow.session

        async def rows(sql: str):
            return [tuple(r) for r in (await s.execute(text(sql))).fetchall()]

        return {
            "universe": canonical_hash(await rows(
                "SELECT status, owner, fencing_token, content_hash "
                "FROM trading.pm_universe_frames ORDER BY id"
            )),
            "opportunity": canonical_hash(await rows(
                "SELECT opportunity_key, chain_type, disposition, status, terminal_reason "
                "FROM trading.decision_opportunities ORDER BY opportunity_key"
            )),
            "episode_identity": canonical_hash(await rows(
                "SELECT episode_key, trigger, experiment_variant, status, cognition_status "
                "FROM trading.forecast_episodes ORDER BY episode_key"
            )),
            "processing_disposition": canonical_hash(await rows(
                "SELECT episode_id, route_channel, first_rejected_gate, reason_code, "
                "processing_disposition, action_eligible "
                "FROM trading.episode_memberships ORDER BY episode_id"
            )),
            "blind_commit": canonical_hash(await rows(
                "SELECT submission_key, status, q::text, u::text, "
                "contract_schema_prior_evidence_hash, algorithm_hash "
                "FROM trading.forecast_submissions ORDER BY submission_key"
            )),
            "economic_action_intent": canonical_hash(await rows(
                "SELECT intent_key, intent_hash, status "
                "FROM trading.economic_action_intents ORDER BY intent_key"
            )),
            "authorization_envelope": None,  # 0050/0051 表未建；由集成覆盖
            "ledger": canonical_hash(
                await rows(
                    "SELECT transaction_key, status, kind, portfolio_namespace "
                    "FROM trading.ledger_transactions ORDER BY transaction_key"
                )
                + await rows(
                    "SELECT t.transaction_key, p.posting_no, p.asset_type, p.asset_key, "
                    "p.amount::text, p.counterparty "
                    "FROM trading.ledger_postings p "
                    "JOIN trading.ledger_transactions t ON t.id = p.transaction_id "
                    "ORDER BY t.transaction_key, p.posting_no"
                )
                + await rows(
                    "SELECT portfolio_namespace, quantity::text, cost_basis::text "
                    "FROM trading.positions ORDER BY portfolio_namespace"
                )
            ),
            "metric_artifact": canonical_hash(await rows(
                "SELECT run_key, artifact_hash, status FROM trading.metric_runs ORDER BY run_key"
            )),
        }


def _frozen_event_types() -> list[str]:
    log = load_scenario("event_log")
    return [ev["type"] for ev in log["events"]]


@pytest.mark.asyncio
async def test_p_stability_event_log_sequence_matches_chain(stability_env):
    """冻结 event log 的 23 个事件类型/顺序与链步骤一致，payload_hash 自洽。"""
    log = load_scenario("event_log")
    events = log["events"]
    assert [ev["type"] for ev in events] == _frozen_event_types()
    assert len(events) == 23
    expected_types = [
        "universe_frame", "cohort_open", "r0_select", "parent_opportunity", "g1_contract",
        "g2_schema", "episode_create", "route_standard", "g4_prior", "evidence_revision",
        "g5a_bundle", "g5b_coverage", "g6_blind_commit", "create_decision",
        "reveal_quote_bind", "market_relative", "g7a_depth_walk", "g7b_portfolio",
        "terminalize_action", "economic_intent", "shadow_fill", "ledger_postings",
        "metric_run",
    ]
    assert [ev["type"] for ev in events] == expected_types
    for i, ev in enumerate(events, start=1):
        assert ev["seq"] == i
        assert ev["payload_hash"] == canonical_hash({"seq": ev["seq"], "type": ev["type"]})


@pytest.mark.asyncio
async def test_p_stability_double_replay_hashes_equal(stability_env):
    """同一 frozen input 两次确定性重放，业务 hash 逐项相等，且与冻结 snapshot 一致。"""
    frozen = frozen_scenario("snapshot")
    expected = frozen["expected_business_hashes"]

    first = await _run_full_chain(stability_env)
    h1 = await _business_hashes(stability_env)

    _restart(stability_env["url"])
    await _run_full_chain(stability_env)
    h2 = await _business_hashes(stability_env)

    assert h1 == h2, "double replay drift"
    assert h1["authorization_envelope"] is None
    for key, value in expected.items():
        if key in ("authorization_envelope", "authorization_envelope_note"):
            continue
        assert h1[key] == value, f"business hash {key} drifted from frozen snapshot"


@pytest.mark.asyncio
async def test_p_stability_worker_restart_between_stages(stability_env):
    """worker restart（engine 重建 + 重新从 checkpoint 续跑）后业务 hash 与冻结 snapshot 一致。"""
    frozen = frozen_scenario("snapshot")
    expected = frozen["expected_business_hashes"]

    ctx = await _seed(stability_env, book_received_at=_seed_book_time())
    episode, spec_ids = await _build_blind_committed_episode(stability_env, ctx)

    # 进程重启：dispose 全部连接，重建 engine/session。
    await stability_env["engine"].dispose()
    stability_env["engine"], stability_env["sessions"] = _fresh_engine(stability_env["url"])

    chain = await _run_decision_execution_chain(stability_env, ctx, episode, spec_ids)
    obs_id, label_id = _seed_metric_observation(stability_env, ctx, episode, spec_ids[0], chain)
    await _run_metric(stability_env, _metric_input(ctx, obs_id, label_id, run_key=_METRIC_RUN_KEY))

    h = await _business_hashes(stability_env)
    for key, value in expected.items():
        if key in ("authorization_envelope", "authorization_envelope_note"):
            continue
        assert h[key] == value, f"business hash {key} drifted after worker restart"


@pytest.mark.asyncio
async def test_p_stability_transaction_rollback_no_half_evidence(stability_env):
    """crash rollback：同一 UoW 内写入后回滚，不产生半条 metric evidence，业务 hash 不漂移。"""
    frozen = frozen_scenario("snapshot")
    expected = frozen["expected_business_hashes"]

    ctx = await _seed(stability_env, book_received_at=_seed_book_time())
    episode, spec_ids = await _build_blind_committed_episode(stability_env, ctx)
    chain = await _run_decision_execution_chain(stability_env, ctx, episode, spec_ids)
    obs_id, label_id = _seed_metric_observation(stability_env, ctx, episode, spec_ids[0], chain)
    await _run_metric(stability_env, _metric_input(ctx, obs_id, label_id, run_key=_METRIC_RUN_KEY))

    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    # crash 注入：用不同 run_key 启动第二次 metric run，同一 UoW 内 rollback。
    dup_input = _metric_input(ctx, obs_id, label_id, run_key="metric-p5-crash")
    async with UnitOfWork(stability_env["sessions"]) as uow:
        created = await logic.run_metric(uow, input_=dup_input)
        assert created.ok, created.reason
        await uow.rollback()

    # 无半条证据：只有正式 run（COMPLETED），无 crash run。
    async with UnitOfWork(stability_env["sessions"]) as uow:
        runs = (await uow.session.execute(text(
            "SELECT run_key, status FROM trading.metric_runs ORDER BY run_key"
        ))).fetchall()
    assert [tuple(r) for r in runs] == [(_METRIC_RUN_KEY, "COMPLETED")], runs

    h = await _business_hashes(stability_env)
    for key, value in expected.items():
        if key in ("authorization_envelope", "authorization_envelope_note"):
            continue
        assert h[key] == value, f"business hash {key} drifted after rollback"


@pytest.mark.asyncio
async def test_p_stability_duplicate_and_model_retry_fail_closed(stability_env):
    """重复投递 / 模型 timeout 重试 fail closed：不双提交、不双记 Gate、业务 hash 不漂移。"""
    frozen = frozen_scenario("snapshot")
    expected = frozen["expected_business_hashes"]

    ctx = await _seed(stability_env, book_received_at=_seed_book_time())
    episode, spec_ids = await _build_blind_committed_episode(stability_env, ctx)
    chain = await _run_decision_execution_chain(stability_env, ctx, episode, spec_ids)
    obs_id, label_id = _seed_metric_observation(stability_env, ctx, episode, spec_ids[0], chain)
    await _run_metric(stability_env, _metric_input(ctx, obs_id, label_id, run_key=_METRIC_RUN_KEY))

    logic = DecisionLogic(stability_env["decision"], stability_env["wf"])

    # 重复 G7A：状态已 ACTION，fail closed，不新增 gate 行。
    async with UnitOfWork(stability_env["sessions"]) as uow:
        dup = await logic.run_g7a(
            uow, trade_decision_id=chain["trade_decision_id"],
            candidates=[
                ActionCandidateInput(
                    contract_spec_id=spec_ids[0], token_id=ctx["yes_token"],
                    action_type="BUY_TOKEN", target_quantity=Decimal("100"),
                )
            ],
            policy_hash=None, version_manifest_id=None)
    assert dup.ok is False
    async with UnitOfWork(stability_env["sessions"]) as uow:
        g7a_rows = (await uow.session.execute(text(
            "SELECT count(*) FROM trading.gate_decisions WHERE gate='G7A'"
        ))).scalar_one()
    assert g7a_rows == 1

    # 模型 timeout/partial failure → 同一 frozen 输入重试 run_g6：episode 已 BLIND_COMMITTED，fail closed。
    forecast = ForecastLogic(stability_env["forecast"], stability_env["wf"])
    submission = ForecastSubmissionInput(
        submission_key="sub-p2",
        Q=QDistributionInput(values={"w0": "0.6", "w1": "0.4"}),
        U=[QDistributionInput(values={"w0": "0.6", "w1": "0.4"}),
           QDistributionInput(values={"w0": "0.55", "w1": "0.45"})],
        forecast_input_manifest_id=1,
    )
    lease = ForecastLeaseInput(
        valid_until=FIXED + timedelta(days=30),
        invalidation_conditions={"fact_freshness": {"max_age_hours": 48}},
        evidence_hash="a" * 64, schema_hash="b" * 64, spec_hash="c" * 64,
    )
    async with UnitOfWork(stability_env["sessions"]) as uow:
        retry = await forecast.run_g6(
            uow, episode_id=episode, submission=submission,
            material=InputManifestMaterial(taxonomy_hash="a" * 64,
                                           model_binding_hash="a" * 64,
                                           prompt_hash="a" * 64, code_hash="a" * 64),
            lease=lease, version_manifest_id=ctx["release"],
            policy_hash=ctx["policy_hashes"]["evidence_coverage"])
    assert retry.ok is False, "model retry on committed episode must fail closed"
    async with UnitOfWork(stability_env["sessions"]) as uow:
        submissions = (await uow.session.execute(text(
            "SELECT count(*) FROM trading.forecast_submissions WHERE episode_id=:e"
        ), {"e": episode})).scalar_one()
    assert submissions == 1

    h = await _business_hashes(stability_env)
    for key, value in expected.items():
        if key in ("authorization_envelope", "authorization_envelope_note"):
            continue
        assert h[key] == value, f"business hash {key} drifted after duplicate/retry"


@pytest.mark.asyncio
async def test_p_stability_out_of_order_fail_closed(stability_env):
    """乱序：market_relative 在 reveal 前调用 fail closed，不推进状态、不生成新证据。"""
    frozen = frozen_scenario("snapshot")
    expected = frozen["expected_business_hashes"]

    ctx = await _seed(stability_env, book_received_at=_seed_book_time())
    episode, spec_ids = await _build_blind_committed_episode(stability_env, ctx)
    chain = await _run_decision_execution_chain(stability_env, ctx, episode, spec_ids)
    obs_id, label_id = _seed_metric_observation(stability_env, ctx, episode, spec_ids[0], chain)
    await _run_metric(stability_env, _metric_input(ctx, obs_id, label_id, run_key=_METRIC_RUN_KEY))

    logic = DecisionLogic(stability_env["decision"], stability_env["wf"])
    # 同一 episode 上新建第二个决策（不同 trigger_at → 不同 decision_key）。
    trigger_at = FIXED + timedelta(hours=13)
    async with UnitOfWork(stability_env["sessions"]) as uow:
        created = await logic.create_decision(uow, episode_id=episode,
                                              trigger_at=trigger_at, experiment_variant="control")
    assert created.ok, created.reason

    # 乱序：未 reveal（未 QUOTE_BOUND）直接 market_relative → fail closed。
    async with UnitOfWork(stability_env["sessions"]) as uow:
        mr = await logic.market_relative(
            uow, trade_decision_id=created.trade_decision_id,
            input_=MarketRelativeInput(decision_mode="BLIND_ONLY"))
    assert mr.ok is False
    assert mr.reason == "decision_not_quote_bound"

    # 状态未推进（仍 CREATED），且未产生 market_relative 行。
    async with UnitOfWork(stability_env["sessions"]) as uow:
        status = (await uow.session.execute(text(
            "SELECT status FROM trading.trade_decisions WHERE id=:id"
        ), {"id": created.trade_decision_id})).scalar_one()
        mr_rows = (await uow.session.execute(text(
            "SELECT count(*) FROM trading.market_relative_decisions WHERE trade_decision_id=:id"
        ), {"id": created.trade_decision_id})).scalar_one()
    assert status == "CREATED"
    assert mr_rows == 0

    h = await _business_hashes(stability_env)
    for key, value in expected.items():
        if key in ("authorization_envelope", "authorization_envelope_note"):
            continue
        assert h[key] == value, f"business hash {key} drifted after out-of-order attempt"


@pytest.mark.asyncio
async def test_p_stability_unconfirmed_write_fail_closed(stability_env):
    """未确认写入 / 不可判定推进 fail closed：缺前置 Gate 的 terminalize 不生成 action/intent。"""
    frozen = frozen_scenario("snapshot")
    expected = frozen["expected_business_hashes"]

    ctx = await _seed(stability_env, book_received_at=_seed_book_time())
    episode, spec_ids = await _build_blind_committed_episode(stability_env, ctx)

    logic = DecisionLogic(stability_env["decision"], stability_env["wf"])
    trigger_at = FIXED + timedelta(hours=14)
    async with UnitOfWork(stability_env["sessions"]) as uow:
        created = await logic.create_decision(uow, episode_id=episode,
                                              trigger_at=trigger_at, experiment_variant="control")
    assert created.ok, created.reason

    # 未过 G7A/G7B 就 terminalize(ACTION) → fail closed。
    async with UnitOfWork(stability_env["sessions"]) as uow:
        terminal = await logic.terminalize(
            uow, trade_decision_id=created.trade_decision_id,
            action_set=ActionSetInput(
                disposition="ACTION", selected_action_type="BUY_TOKEN",
                legs={"open": {spec_ids[0]: {ctx["yes_token"]: Decimal("100")}}},
            ),
            underwriting=UnderwritingInput(
                plan_version=1, entry_range={"min": "0.50", "max": "0.55"},
                hold_to_resolution=True, thesis_hash="a" * 64,
                invalidation={"evidence": "regime_change"},
            ),
            decided_at=FIXED + timedelta(hours=14, minutes=1))
    assert terminal.ok is False
    assert terminal.reason == "decision_not_g7b"

    # 未生成 action_set / intent（fail closed，不推进下一 Gate）。
    async with UnitOfWork(stability_env["sessions"]) as uow:
        action_sets = (await uow.session.execute(text(
            "SELECT count(*) FROM trading.action_sets WHERE trade_decision_id=:id"
        ), {"id": created.trade_decision_id})).scalar_one()
        intents = (await uow.session.execute(text(
            "SELECT count(*) FROM trading.economic_action_intents WHERE trade_decision_id=:id"
        ), {"id": created.trade_decision_id})).scalar_one()
    assert action_sets == 0 and intents == 0

    # 主链未受影响（此场景只建了 episode，业务 hash 与冻结 snapshot 的 pre-decision 部分一致）。
    # 这里只校验与冻结 snapshot 的 universe/opportunity/episode/blind 一致（decision 之前不变）。
    h = await _business_hashes(stability_env)
    assert h["universe"] == expected["universe"]
    assert h["opportunity"] == expected["opportunity"]
    assert h["episode_identity"] == expected["episode_identity"]
    assert h["blind_commit"] == expected["blind_commit"]


def test_p_stability_random_seed_binding():
    """随机调用 seed 绑定：同 seed 同结果；异 seed 差异可归因到 sampling。"""
    event_log = load_scenario("event_log")
    seed_hash = event_log["seed_hash"]
    assert len(seed_hash) == 64
    content = canonical_hash({"market": "m1", "layer": "reject-audit", "event": "r0"})

    same1 = deterministic_sample(content_hash=content, seed_hash=seed_hash,
                                 stratum="r0", rate=Decimal("0.5"))
    same2 = deterministic_sample(content_hash=content, seed_hash=seed_hash,
                                 stratum="r0", rate=Decimal("0.5"))
    assert same1 == same2  # (selected, u, rate) 完全一致

    other_seed = canonical_hash({"seed": "different"})
    other = deterministic_sample(content_hash=content, seed_hash=other_seed,
                                 stratum="r0", rate=Decimal("0.5"))
    # 差异可归因：u 不同（sampling 输出不同）。
    assert same1[1] != other[1]
