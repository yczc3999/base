"""WP-03 P2 decision → shadow execution replay —— 确定性全链两次重放 hash 全等（真 PostgreSQL）。

流程：create_decision → reveal（exact quote binding）→ market_relative（BLIND_ONLY）
→ G7A（depth walk + robust EV）→ G7B（caps）→ terminalize(ACTION) → intent → shadow_fill
→ position + balanced ledger。

两次完整运行（`_restart` TRUNCATE 决策/执行/账本/上游表后第二次）对比
decision/market-relative/candidate/set/leg/intent/execution/position/ledger/quote-binding
的全部 hash 与关键值；quote-only 全链零 AI、零新 forecast episode。

注意（相对 workflow 集成测试的确定性差异）：
- ``_build_blind_committed_episode`` 的 lease 改为 ``FIXED`` 基准（workflow 版用 ``now()``，
  会把 ``valid_until`` 混进 ``trade_decisions.input_hash``，破坏重放全等）。
- ``_restart`` 额外 TRUNCATE ``gate_decisions`` 与 ``discrepancy_reviews``（decision 链 G7A/G7B
  写 gate、reveal 写 discrepancy，各自 ``(gate,target_id,target_kind)`` / ``(decision,review_key)``
  唯一；不重置则第二次运行唯一键冲突）。
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
from tests.trading.integration.test_v2_decision_shadow_workflow import (
    FIXED,
    CUTOFF,
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
# WP-05 后 head=b1000052；本测试用 live ORM（executions 含 account_id 等新列），
# 必须在 head schema 上跑，否则 UndefinedColumnError。
HEAD = "b1000070"
# Stable timestamp inside the migration's current-day stream partition and after P2 freeze.
FIXED = datetime(2026, 8, 11, 3, 4, 5, tzinfo=timezone.utc)
CUTOFF = FIXED + timedelta(days=2)


@pytest_asyncio.fixture
async def replay_env(temp_pg_db):
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


async def _build_blind_committed_episode(env: dict, ctx: dict) -> tuple[int, list[int]]:
    """G0→R0→parent→G1→G2→episode→R1→G4→G5A→G5B→G6 → BLIND_COMMITTED。

    与 workflow 集成测试等价，仅 lease ``valid_until`` 改为 ``FIXED`` 基准，
    保证 ``trade_decisions.input_hash`` 跨运行确定（workflow 版 ``now()`` 会注入随机性）。
    """
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


async def _run_chain(env: dict) -> dict:
    """完整 decision → shadow execution 链一次运行，返回可对比 snapshot。"""
    # checkpoint received 与 trade_decision 时间动态化：executions.created_at=now() 要求
    # quote binding stale_at(=received+300s) 相对真实 now() 未来。业务 hash（input/output/
    # action_set/intent/ledger）不含时间戳，保持稳定；仅 decision_key（canonical_hash 含
    # trigger_at）随输入时间漂移，故 _snapshot 排除它（时间变化是输入的一部分，非业务漂移）。
    ctx = await _seed(env, book_received_at=datetime.now(timezone.utc) + timedelta(minutes=9))
    episode, spec_ids = await _build_blind_committed_episode(env, ctx)
    logic = DecisionLogic(env["decision"], env["wf"])
    spec_id = spec_ids[0]

    # 确定性时间线：trigger 在真实 now() 之后（checkpoint received 为 now+9min）。
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
    return await _snapshot(env)


async def _snapshot(env: dict) -> dict:
    """读 decision/execution/ledger/quote 关键列；surrogate id 一律排除或 RESTART IDENTITY 保证一致。"""
    async with UnitOfWork(env["sessions"]) as uow:
        s = uow.session

        async def rows(sql: str):
            return [tuple(row) for row in (await s.execute(text(sql))).fetchall()]

        return {
            "trade_decisions": await rows(
                # decision_key/input_hash 均含 trigger_at（时间敏感，动态时间线下两次重放漂移，
                # 属输入时间变化而非业务不稳定）；保留 status/output_hash/selected_action_type。
                "SELECT status, output_hash, selected_action_type "
                "FROM trading.trade_decisions ORDER BY status, output_hash"
            ),
            "market_relative": await rows(
                "SELECT decision_mode, q_decision::text, u_decision::text, u_blind_hash, "
                "u_decision_hash, input_manifest_hash, output_manifest_hash "
                "FROM trading.market_relative_decisions ORDER BY trade_decision_id"
            ),
            "action_candidates": await rows(
                "SELECT contract_spec_id, token_id, action_type, robust_ev, net_edge "
                "FROM trading.action_candidates "
                "ORDER BY contract_spec_id, token_id, action_type"
            ),
            "action_sets": await rows(
                "SELECT action_set_hash, disposition FROM trading.action_sets "
                "ORDER BY action_set_hash"
            ),
            "action_set_legs": await rows(
                "SELECT leg_role, quantity, signed_quantity FROM trading.action_set_legs "
                "ORDER BY leg_role, quantity"
            ),
            "intents": await rows(
                "SELECT intent_hash FROM trading.economic_action_intents ORDER BY intent_hash"
            ),
            "executions": await rows(
                "SELECT status, filled_quantity, vwap FROM trading.executions "
                "ORDER BY execution_key"
            ),
            "positions": await rows(
                "SELECT quantity, cost_basis FROM trading.positions "
                "ORDER BY portfolio_namespace, contract_spec_id, token_id"
            ),
            "ledger_transactions": await rows(
                "SELECT transaction_key, kind, status FROM trading.ledger_transactions "
                "ORDER BY transaction_key"
            ),
            "ledger_postings": await rows(
                "SELECT p.asset_type, p.asset_key, p.amount, p.counterparty "
                "FROM trading.ledger_postings p "
                "JOIN trading.ledger_transactions t ON t.id = p.transaction_id "
                "ORDER BY t.transaction_key, p.asset_type, p.asset_key, p.posting_no"
            ),
            "operating_cost_entries": await rows(
                "SELECT cost_key, cost_kind, amount FROM trading.operating_cost_entries "
                "ORDER BY cost_key"
            ),
            "quote_bindings": await rows(
                "SELECT token_id, best_bid, best_ask, trade_decision_id "
                "FROM trading.pm_quote_bindings ORDER BY token_id"
            ),
        }


def _restart(url: str) -> None:
    """TRUNCATE 决策/执行/账本 + 上游全部表（CASCADE + RESTART IDENTITY）。

    在任务清单基础上额外覆盖 ``gate_decisions`` 与 ``discrepancy_reviews``
    （decision 链实际写入、带唯一键；不重置则二次运行冲突）。
    """
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


async def _count(env: dict, table: str) -> int:
    async with UnitOfWork(env["sessions"]) as uow:
        return (await uow.session.execute(
            text(f"SELECT count(*) FROM trading.{table}")
        )).scalar_one()


@pytest.mark.asyncio
async def test_p2_decision_replay_is_stable(replay_env):
    first = await _run_chain(replay_env)
    _restart(replay_env["url"])
    second = await _run_chain(replay_env)
    assert first == second
    # quote-only 全链零 AI、零新 forecast episode
    assert await _count(replay_env, "ai_invocations") == 0
    assert await _count(replay_env, "forecast_episodes") == 1
