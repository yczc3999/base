"""WP-01C full-pipeline replay and crash/retry proof on real PostgreSQL."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.logics.trading.component import ComponentLogic
from app.logics.trading.contract import ContractLogic
from app.logics.trading.screening import (
    AUDIT_ALGORITHM_VERSION,
    G0Result,
    ScreeningLogic,
)
from app.orchestrator.trading_state_machine import EpisodeInput, TradingStateMachine
from app.repositories.trading.cohort import CohortRepository, REQUIRED_COHORT_POLICIES
from app.repositories.trading.semantics import SemanticsRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.schemas.trading.semantics import ContractSpecInput, PayoutIRInput, WorldSchemaInput
from app.schemas.trading.workflow import (
    HydratedUniverseFrameInput,
    R0Input,
    R0PolicyInput,
    RejectAuditPolicyInput,
)

FIXED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
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
            "trading_cost_scope",
            "data_cost_scope",
            "llm_cost_scope",
            "search_cost_scope",
            "infrastructure_cost_scope",
            "human_cost_scope",
            "operational_cost_scope",
        )
    },
    "robustness_policy": {"kind": "worst_case"},
    "hard_constraint_ordering": ["eligibility", "capital"],
}
OBJECTIVE_HASH = canonical_hash(FULL_OBJECTIVE)
R0_POLICY = R0PolicyInput(
    policy_version=1,
    minimum_rule_completeness=Decimal("0.75"),
    maximum_research_cost=Decimal("100"),
    require_two_sided_quote=True,
    defer_recheck_condition="book_or_rules_change",
    reject_recheck_condition="capacity_or_cost_change",
)
AUDIT_POLICY = RejectAuditPolicyInput(
    policy_version=1,
    algorithm_version=AUDIT_ALGORITHM_VERSION,
    salt="replay/reject-audit/v1",
    reject_probability=Decimal("1"),
    defer_probability=Decimal("1"),
)


class InjectedCrash(RuntimeError):
    pass


@pytest_asyncio.fixture
async def replay_env(migrated_pg_db):
    admin = make_url(migrated_pg_db.url)
    async_url = admin.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    engine = create_async_engine(async_url, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    env = {
        "sessions": sessions,
        "sem": SemanticsRepository(),
        "wf": WorkflowRepository(),
        "cohort": CohortRepository(),
        "url": migrated_pg_db.url,
    }
    yield env
    await engine.dispose()


async def _seed(env: dict) -> dict:
    r0_hash = canonical_hash(R0_POLICY.model_dump(mode="json"))
    audit_hash = canonical_hash(AUDIT_POLICY.model_dump(mode="json"))
    policy_hashes = {
        name: f"{index:x}" * 64
        for index, name in enumerate(REQUIRED_COHORT_POLICIES, start=1)
    }
    policy_hashes["r0"] = r0_hash
    policy_hashes["reject_audit"] = audit_hash

    async with UnitOfWork(env["sessions"]) as uow:
        s = uow.session
        obj = (
            await s.execute(
                text(
                    "INSERT INTO trading.strategy_objective_contracts "
                    "(contract_key,version_no,content,schema_version,content_hash,status) "
                    "VALUES ('obj-replay',1,CAST(:content AS jsonb),1,:hash,'active') RETURNING id"
                ),
                {"content": json.dumps(FULL_OBJECTIVE), "hash": OBJECTIVE_HASH},
            )
        ).scalar_one()
        strategy_content = {"strategy": "replay/v1"}
        strategy_hash = canonical_hash(strategy_content)
        strategy = (
            await s.execute(
                text(
                    "INSERT INTO trading.strategy_versions "
                    "(strategy_key,version_no,content,schema_version,content_hash,status) "
                    "VALUES ('strategy-replay',1,CAST(:content AS jsonb),1,:hash,'active') "
                    "RETURNING id"
                ),
                {"content": json.dumps(strategy_content), "hash": strategy_hash},
            )
        ).scalar_one()
        permission = (
            await s.execute(
                text(
                    "INSERT INTO trading.capital_permission_manifests "
                    "(name,mode,capability,limits,evaluation_capital,authorized_capital,content_hash,status) "
                    "VALUES ('permission-replay','shadow','{}','{}',0,0,:h,'active') RETURNING id"
                ),
                {"h": "c" * 64},
            )
        ).scalar_one()
        config = (
            await s.execute(
                text(
                    "INSERT INTO trading.runtime_config_versions "
                    "(config_key,version_no,content,schema_version,content_hash,status) "
                    "VALUES ('config-replay',1,'{}',1,:h,'active') RETURNING id"
                ),
                {"h": "d" * 64},
            )
        ).scalar_one()
        execution = (
            await s.execute(
                text(
                    "INSERT INTO trading.execution_spec_versions "
                    "(spec_key,version_no,content,schema_version,content_hash,status) "
                    "VALUES ('execution-replay',1,'{}',1,:h,'active') RETURNING id"
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
                    "VALUES ('release-replay',:cfg,:strategy,:execution,:permission,'abc','img',"
                    "'b1000013',:hash,'active') RETURNING id"
                ),
                {
                    "cfg": config,
                    "strategy": strategy,
                    "execution": execution,
                    "permission": permission,
                    "hash": "f" * 64,
                },
            )
        ).scalar_one()
        for name in REQUIRED_COHORT_POLICIES:
            await s.execute(
                text(
                    "INSERT INTO trading.policy_type_scopes "
                    "(policy_type,scope_type,scope_key) VALUES (:name,'cohort','cohort-replay')"
                ),
                {"name": name},
            )
            await s.execute(
                text(
                    "INSERT INTO trading.policy_freezes "
                    "(policy_type,scope_type,scope_key,policy_version,policy_content_hash,"
                    "release_manifest_id,status) "
                    "VALUES (:name,'cohort','cohort-replay',1,:hash,:release,'frozen')"
                ),
                {"name": name, "hash": policy_hashes[name], "release": release},
            )
        cohort = (
            await s.execute(
                text(
                    "INSERT INTO trading.evaluation_cohorts "
                    "(cohort_key,status,objective_contract_id,strategy_version_id,release_manifest_id,"
                    "policy_hashes,seed_hash) "
                    "VALUES ('cohort-replay','DRAFT',:obj,:strategy,:release,CAST(:policies AS jsonb),:seed) "
                    "RETURNING id"
                ),
                {
                    "obj": obj,
                    "strategy": strategy,
                    "release": release,
                    "policies": json.dumps(policy_hashes),
                    "seed": SEED,
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

        market_ids: list[int] = []
        for index in (1, 2):
            market_ids.append(
                (
                    await s.execute(
                        text(
                            "INSERT INTO trading.pm_markets "
                            "(gamma_market_id,condition_id,active,closed,accepting_orders,enable_order_book) "
                            "VALUES (:market,:condition,true,false,true,true) RETURNING id"
                        ),
                        {
                            "market": f"market-replay-{index}",
                            "condition": f"condition-replay-{index}",
                        },
                    )
                ).scalar_one()
            )
        market = market_ids[0]
        yes_token = (
            await s.execute(
                text(
                    "INSERT INTO trading.pm_tokens (token_id,market_id,outcome_index) "
                    "VALUES ('token-replay-yes',:market,0) RETURNING id"
                ),
                {"market": market},
            )
        ).scalar_one()
        no_token = (
            await s.execute(
                text(
                    "INSERT INTO trading.pm_tokens (token_id,market_id,outcome_index) "
                    "VALUES ('token-replay-no',:market,1) RETURNING id"
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
                    {
                        "sha": sha,
                        "locator": f"cas/v1/sha256/{sha[:2]}/{sha[2:4]}/{sha}.raw",
                    },
                )
            ).scalar_one()

        frame_sha = "9" * 64
        frame_artifact = await artifact(frame_sha)
        contract_artifact = await artifact("8" * 64)
        frame = (
            await s.execute(
                text(
                    "INSERT INTO trading.pm_universe_frames "
                    "(status,started_at,owner,lease_expires_at,fencing_token,completed_at,"
                    "page_count,total_events,total_markets,content_hash,artifact_id,artifact_ref) "
                    "VALUES ('COMPLETE',:started,'replay',:lease,1,:completed,0,0,2,:hash,:artifact,:hash) "
                    "RETURNING id"
                ),
                {
                    "started": FIXED,
                    "lease": FIXED + timedelta(minutes=1),
                    "completed": FIXED,
                    "hash": frame_sha,
                    "artifact": frame_artifact,
                },
            )
        ).scalar_one()

    return {
        "cohort": cohort,
        "objective": obj,
        "strategy": strategy,
        "release": release,
        "policy_hashes": policy_hashes,
        "markets": market_ids,
        "market_version": market_version,
        "yes_token": yes_token,
        "no_token": no_token,
        "yes_version": yes_version,
        "no_version": no_version,
        "contract_artifact": contract_artifact,
        "frame": HydratedUniverseFrameInput(
            frame_id=frame,
            content_hash=frame_sha,
            artifact_object_id=frame_artifact,
            artifact_ref=frame_sha,
            markets=[
                {"market_id": market_ids[0], "metadata": {"market_key": "market-replay-1"}},
                {"market_id": market_ids[1], "metadata": {"market_key": "market-replay-2"}},
            ],
        ),
    }


async def _enroll(env: dict, ctx: dict, g0: G0Result, *, crash: bool) -> int:
    async with UnitOfWork(env["sessions"]) as uow:
        count = await ScreeningLogic(env["cohort"], env["wf"]).enroll_frame(
            uow,
            cohort_id=ctx["cohort"],
            frame=ctx["frame"],
            observed_at=FIXED,
            ingested_at=FIXED,
            g0=g0,
        )
        if crash:
            raise InjectedCrash("after_membership")
        return count


async def _r0(env: dict, ctx: dict, g0: G0Result, *, crash: bool):
    logic = ScreeningLogic(env["cohort"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        selected = await logic.run_r0(
            uow,
            cohort_id=ctx["cohort"],
            market_id=ctx["markets"][0],
            episode_no=1,
            r0_input=R0Input(
                market_metadata={"market_key": "market-replay-1"},
                best_bid=Decimal("0.50"),
                best_ask=Decimal("0.52"),
                rule_completeness=Decimal("0.90"),
                minimum_deployable_capacity=Decimal("10"),
                objective_ref=OBJECTIVE_HASH,
            ),
            g0=g0,
            r0_policy=R0_POLICY,
            audit_policy=AUDIT_POLICY,
        )
        rejected = await logic.run_r0(
            uow,
            cohort_id=ctx["cohort"],
            market_id=ctx["markets"][1],
            episode_no=1,
            r0_input=R0Input(
                market_metadata={"market_key": "market-replay-2"},
                best_bid=Decimal("0.40"),
                best_ask=Decimal("0.42"),
                rule_completeness=Decimal("0.90"),
                minimum_deployable_capacity=Decimal("0"),
                objective_ref=OBJECTIVE_HASH,
            ),
            g0=g0,
            r0_policy=R0_POLICY,
            audit_policy=AUDIT_POLICY,
        )
        if crash:
            raise InjectedCrash("after_r0")
        return selected, rejected


def _contract_candidate(ctx: dict, key: str, *, rules: str | None = "rules"):
    return ContractSpecInput(
        contract_key=key,
        market_version_id=ctx["market_version"],
        yes_token_version_id=ctx["yes_version"],
        no_token_version_id=ctx["no_version"],
        artifact_object_id=ctx["contract_artifact"],
        resolution_states=["YES", "NO"],
        compiler_version="lookup/v1",
        schema_version=1,
        rules=rules,
        resolution_source="gamma",
        payouts=[
            PayoutIRInput(
                token_key="yes",
                pm_token_id=ctx["yes_token"],
                token_version_id=ctx["yes_version"],
                outcome_index=0,
                function_ir={"YES": "1", "NO": "0"},
            ),
            PayoutIRInput(
                token_key="no",
                pm_token_id=ctx["no_token"],
                token_version_id=ctx["no_version"],
                outcome_index=1,
                function_ir={"YES": "0", "NO": "1"},
            ),
        ],
    )


async def _g1(env: dict, ctx: dict, child: int, key: str, *, crash: bool = False):
    async with UnitOfWork(env["sessions"]) as uow:
        result = await ContractLogic(env["sem"], env["wf"]).run_g1(
            uow,
            candidate=_contract_candidate(ctx, key),
            cutoff_at=FIXED,
            timezone_name="UTC",
            raw_outcome_mapping={"YES": 0, "NO": 1},
            opportunity_id=child,
            policy_hash=ctx["policy_hashes"]["eligibility"],
            version_manifest_id=ctx["release"],
        )
        if crash:
            raise InjectedCrash("after_g1")
        return result


def _world_schema(spec_ids: list[int]) -> WorldSchemaInput:
    return WorldSchemaInput(
        component_key="component-replay",
        variables={"outcome": {"type": "enum"}},
        domains={"outcome": ["yes", "no"]},
        constraints=[],
        factorization={"independent": ["outcome"]},
        world_states=[
            {"world_state_id": "world-yes", "assignment": {"outcome": "yes"}},
            {"world_state_id": "world-no", "assignment": {"outcome": "no"}},
        ],
        state_count=2,
        h_c={
            str(spec_id): {"world-yes": "YES", "world-no": "NO"}
            for spec_id in spec_ids
        },
        schema_version=1,
    )


async def _g2(env: dict, ctx: dict, child: int, spec_ids: list[int], *, crash: bool):
    candidate = _world_schema(spec_ids)
    member_hc = {spec_id: candidate.h_c[str(spec_id)] for spec_id in spec_ids}
    async with UnitOfWork(env["sessions"]) as uow:
        result = await ComponentLogic(env["sem"], env["wf"]).run_g2(
            uow,
            candidate=candidate,
            contract_spec_ids=spec_ids,
            member_hc=member_hc,
            cost_budget=Decimal("10"),
            opportunity_id=child,
            policy_hash=ctx["policy_hashes"]["taxonomy"],
            version_manifest_id=ctx["release"],
        )
        if crash:
            raise InjectedCrash("after_g2")
        return result


async def _count(env: dict, table: str) -> int:
    async with UnitOfWork(env["sessions"]) as uow:
        return (
            await uow.session.execute(text(f"SELECT count(*) FROM trading.{table}"))
        ).scalar_one()


async def _run_chain(env: dict) -> dict:
    ctx = await _seed(env)
    screening = ScreeningLogic(env["cohort"], env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        g0 = await screening.run_g0(
            uow,
            cohort_id=ctx["cohort"],
            objective_content=FULL_OBJECTIVE,
            expected_objective_hash=OBJECTIVE_HASH,
        )
    assert g0.ok

    with pytest.raises(InjectedCrash, match="after_membership"):
        await _enroll(env, ctx, g0, crash=True)
    assert await _count(env, "universe_memberships") == 0
    assert await _enroll(env, ctx, g0, crash=False) == 2
    assert await _enroll(env, ctx, g0, crash=False) == 0

    with pytest.raises(InjectedCrash, match="after_r0"):
        await _r0(env, ctx, g0, crash=True)
    assert await _count(env, "screening_episodes") == 0
    selected, rejected = await _r0(env, ctx, g0, crash=False)
    retry_selected, retry_rejected = await _r0(env, ctx, g0, crash=False)
    assert selected.result == retry_selected.result == "SELECT"
    assert rejected.result == retry_rejected.result == "REJECT"
    assert rejected.audit_selected and rejected.audit_probability == Decimal("1.000000000000")

    state = TradingStateMachine(env["wf"])
    async with UnitOfWork(env["sessions"]) as uow:
        parent = await state.create_parent_opportunity(
            uow,
            cohort_id=ctx["cohort"],
            chain_type="DECISION",
            objective_contract_id=ctx["objective"],
            strategy_version_id=ctx["strategy"],
            source_screening_episode_id=selected.episode_id,
            triggered_at=FIXED,
            market_ids=[ctx["markets"][0]],
        )
        audit_parent = await state.create_parent_opportunity(
            uow,
            cohort_id=ctx["cohort"],
            chain_type="RESEARCH_EVAL",
            objective_contract_id=ctx["objective"],
            strategy_version_id=ctx["strategy"],
            source_screening_episode_id=rejected.episode_id,
            triggered_at=FIXED,
            market_ids=[ctx["markets"][1]],
            audit_tag="R0_REJECT_AUDIT",
        )
    assert parent != audit_parent

    async with UnitOfWork(env["sessions"]) as uow:
        g1_children = [
            await state.create_g1_child(
                uow,
                parent_id=parent,
                cohort_id=ctx["cohort"],
                chain_type="DECISION",
                objective_contract_id=ctx["objective"],
                strategy_version_id=ctx["strategy"],
                triggered_at=FIXED,
                market_id=ctx["markets"][0],
                seq=index,
            )
            for index in (1, 2)
        ]

    with pytest.raises(InjectedCrash, match="after_g1"):
        await _g1(env, ctx, g1_children[0], "spec-replay-1", crash=True)
    assert await _count(env, "contract_specs") == 0
    spec_results = [
        await _g1(env, ctx, g1_children[0], "spec-replay-1"),
        await _g1(env, ctx, g1_children[1], "spec-replay-2"),
    ]
    retry_spec = await _g1(env, ctx, g1_children[0], "spec-replay-1")
    assert retry_spec.spec_id == spec_results[0].spec_id
    spec_ids = [result.spec_id for result in spec_results]

    async with UnitOfWork(env["sessions"]) as uow:
        g2_child = await state.create_g2_child(
            uow,
            parent_id=parent,
            cohort_id=ctx["cohort"],
            chain_type="DECISION",
            objective_contract_id=ctx["objective"],
            strategy_version_id=ctx["strategy"],
            triggered_at=FIXED,
            component_key="component-replay",
            g1_child_ids=g1_children,
        )
    with pytest.raises(InjectedCrash, match="after_g2"):
        await _g2(env, ctx, g2_child, spec_ids, crash=True)
    assert await _count(env, "forecast_component_versions") == 0
    g2 = await _g2(env, ctx, g2_child, spec_ids, crash=False)
    retry_g2 = await _g2(env, ctx, g2_child, list(reversed(spec_ids)), crash=False)
    assert retry_g2.component_version_id == g2.component_version_id

    episode_input = EpisodeInput(
        decision_opportunity_id=g2_child,
        component_version_id=g2.component_version_id,
        strategy_version_id=ctx["strategy"],
        objective_contract_id=ctx["objective"],
        trigger="frame",
        cutoff_at=FIXED,
        horizon="resolution",
        experiment_variant="control",
        contract_spec_ids=list(reversed(spec_ids)),
    )
    async with UnitOfWork(env["sessions"]) as uow:
        episode = await state.create_episode(uow, input_=episode_input)
    async with UnitOfWork(env["sessions"]) as uow:
        assert await state.create_episode(uow, input_=episode_input) == episode
    for _ in range(2):
        async with UnitOfWork(env["sessions"]) as uow:
            await state.route_episode(
                uow,
                episode_id=episode,
                route_channel="standard",
                first_rejected_gate=None,
                reason_code=None,
                recheck_at=None,
                recheck_condition=None,
                audit_selected=False,
                policy_hash=ctx["policy_hashes"]["r1"],
                version_manifest_id=ctx["release"],
            )

    # A failed G1 sibling and failed G2 sibling are terminal without affecting the valid episode.
    async with UnitOfWork(env["sessions"]) as uow:
        failed_g1_child = await state.create_g1_child(
            uow,
            parent_id=parent,
            cohort_id=ctx["cohort"],
            chain_type="DECISION",
            objective_contract_id=ctx["objective"],
            strategy_version_id=ctx["strategy"],
            triggered_at=FIXED,
            market_id=ctx["markets"][0],
            seq=99,
        )
    async with UnitOfWork(env["sessions"]) as uow:
        failed_g1 = await ContractLogic(env["sem"], env["wf"]).run_g1(
            uow,
            candidate=_contract_candidate(ctx, "spec-replay-fail", rules=None),
            cutoff_at=FIXED,
            timezone_name="UTC",
            raw_outcome_mapping={"YES": 0, "NO": 1},
            opportunity_id=failed_g1_child,
            policy_hash=ctx["policy_hashes"]["eligibility"],
            version_manifest_id=ctx["release"],
        )
        assert not failed_g1.ok
        assert await state.terminal_g1_fail(
            uow, failed_g1_child, failed_g1.reason
        )

    async with UnitOfWork(env["sessions"]) as uow:
        failed_g2_child = await state.create_g2_child(
            uow,
            parent_id=parent,
            cohort_id=ctx["cohort"],
            chain_type="DECISION",
            objective_contract_id=ctx["objective"],
            strategy_version_id=ctx["strategy"],
            triggered_at=FIXED,
            component_key="component-empty",
            g1_child_ids=g1_children,
        )
    empty_schema = _world_schema(spec_ids)
    empty_schema.component_key = "component-empty"
    async with UnitOfWork(env["sessions"]) as uow:
        failed_g2 = await ComponentLogic(env["sem"], env["wf"]).run_g2(
            uow,
            candidate=empty_schema,
            contract_spec_ids=[],
            member_hc={},
            opportunity_id=failed_g2_child,
            policy_hash=ctx["policy_hashes"]["taxonomy"],
            version_manifest_id=ctx["release"],
        )
        assert not failed_g2.ok
        assert await state.terminal_g2_fail(
            uow, failed_g2_child, failed_g2.reason
        )

    return await _snapshot(env, g0.manifest_hash)


async def _snapshot(env: dict, g0_manifest_hash: str) -> dict:
    async with UnitOfWork(env["sessions"]) as uow:
        s = uow.session

        async def rows(sql: str):
            return [tuple(row) for row in (await s.execute(text(sql))).fetchall()]

        memberships = await rows(
            "SELECT m.gamma_market_id,u.metadata_hash,f.content_hash "
            "FROM trading.universe_memberships u JOIN trading.pm_markets m ON m.id=u.market_id "
            "JOIN trading.pm_universe_frames f ON f.id=u.confirmed_frame_id ORDER BY m.gamma_market_id"
        )
        screenings = await rows(
            "SELECT m.gamma_market_id,se.input_hash,se.result,se.reason_code,se.audit_assigned "
            "FROM trading.screening_episodes se JOIN trading.pm_markets m ON m.id=se.market_id "
            "ORDER BY m.gamma_market_id"
        )
        audits = [
            (row[0], row[1], row[2], str(row[3]), str(row[4]), row[5])
            for row in (
                await s.execute(
                    text(
                        "SELECT target,content_hash,stratum,u,inclusion_probability,selected "
                        "FROM trading.audit_samples ORDER BY content_hash"
                    )
                )
            ).fetchall()
        ]
        opportunities = await rows(
            "SELECT opportunity_key,chain_type,status,disposition,audit_tag,terminal_reason "
            "FROM trading.decision_opportunities ORDER BY opportunity_key"
        )
        specs = await rows(
            "SELECT contract_key,status,content_hash,g1_reason FROM trading.contract_specs "
            "ORDER BY contract_key"
        )
        components = await rows(
            "SELECT c.component_key,cv.content_hash FROM trading.forecast_component_versions cv "
            "JOIN trading.forecast_components c ON c.id=cv.component_id ORDER BY c.component_key"
        )
        episodes = await rows(
            "SELECT episode_key,status FROM trading.forecast_episodes ORDER BY episode_key"
        )
        memberships_r1 = await rows(
            "SELECT route_channel,processing_disposition,action_eligible,qualification_eligible,"
            "capital_evidence_eligible,audit_selected FROM trading.episode_memberships"
        )
        gates = await rows(
            "SELECT gate,target_kind,input_hash,policy_hash,result,reason_code "
            "FROM trading.gate_decisions ORDER BY gate,target_kind,input_hash"
        )
    assert memberships_r1 == [("standard", "completed", False, False, False, False)]
    return {
        "g0_manifest_hash": g0_manifest_hash,
        "memberships": memberships,
        "screenings": screenings,
        "audits": audits,
        "opportunities": opportunities,
        "specs": specs,
        "components": components,
        "episodes": episodes,
        "r1": memberships_r1,
        "gates": gates,
    }


def _restart(url: str) -> None:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE "
                    "trading.policy_freezes,trading.policy_type_scopes,"
                    "trading.evaluation_cohorts,trading.decision_opportunities,"
                    "trading.forecast_episodes,trading.screening_episodes,trading.audit_samples,"
                    "trading.contract_snapshots,trading.contract_specs,trading.forecast_components,"
                    "trading.strategy_objective_contracts,trading.strategy_versions,"
                    "trading.release_manifests,trading.runtime_config_versions,"
                    "trading.execution_spec_versions,trading.capital_permission_manifests,"
                    "trading.pm_universe_frames,trading.pm_markets,trading.artifact_objects CASCADE"
                )
            )
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_full_pipeline_replay_is_stable_and_crash_retry_idempotent(replay_env):
    first = await _run_chain(replay_env)
    _restart(replay_env["url"])
    second = await _run_chain(replay_env)
    assert first == second
