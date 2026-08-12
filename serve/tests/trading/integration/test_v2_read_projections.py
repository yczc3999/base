"""WP-04 read projections —— 真 PostgreSQL 集成验收（Checkpoint D）。

覆盖（任务 §7.10 / §5.5）：
1. rebuild 两次投影 hash 精确相同（幂等；重复/乱序 event effect=0）；
2. 清空投影后重建，排序内容 hash 全等；
3. keyset 分页无重/漏（跨页并集=全集，无重复 id）；非法 filter/sort 拒绝；
4. 投影 lag 只降级页面：投影表缺失时读原事实表仍成功。
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
from app.logics.trading.decision import DecisionLogic
from app.logics.trading.execution import ShadowExecutionLogic
from app.logics.trading.projection import ProjectionLogic
from app.repositories.trading.cohort import CohortRepository
from app.repositories.trading.decision import DecisionRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.forecast import ForecastRepository
from app.repositories.trading.ledger import LedgerRepository
from app.repositories.trading.projection import ProjectionRepository
from app.repositories.trading.semantics import SemanticsRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.schemas.trading.decision import (
    ActionCandidateInput,
    ActionSetInput,
    MarketRelativeInput,
    PortfolioGateInput,
    UnderwritingInput,
)
from app.schemas.trading.execution import ShadowFillInput
from tests.trading.integration.test_v2_decision_shadow_workflow import (
    FIXED,
    _build_blind_committed_episode,
    _quote_map,
    _seed,
)

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
# WP-05 后 head=b1000052；本测试用 live ORM（executions 含 account_id 等新列），
# 必须在 head schema 上跑，否则 UndefinedColumnError。
HEAD = "b1000071"

# 与 replay 集成测试一致：book checkpoints 落在已建分区（当前日）。
FIXED = datetime(2026, 8, 11, 3, 4, 5, tzinfo=timezone.utc)

PROJECTION_TABLES = [
    "ops_health_current", "pipeline_funnel_hourly", "account_risk_current",
    "provider_cost_daily", "latest_chain_summary",
]


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
async def proj_env(temp_pg_db):
    _run(command.upgrade, HEAD, temp_pg_db.url)
    url = make_url(temp_pg_db.url).set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    engine = create_async_engine(url, pool_size=4, max_overflow=0)
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


async def _run_decision_chain(env: dict, ctx: dict, episode: int, spec_ids: list[int]) -> int:
    """create → reveal → market_relative → G7A → G7B → terminalize(ACTION) → shadow_fill。"""
    logic = DecisionLogic(env["decision"], env["wf"])
    spec_id = spec_ids[0]
    # trade_decision 时间动态化到 now() 之后：executions.created_at=now() 要求 quote binding
    # stale_at 相对真实时间未来（_seed_book_time 用 now+9min）。本测试无冻结 hash 断言，安全。
    trigger_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    quote_reveal_at = trigger_at + timedelta(seconds=1)
    decided_at = trigger_at + timedelta(seconds=2)

    async with UnitOfWork(env["sessions"]) as uow:
        created = await logic.create_decision(
            uow, episode_id=episode, trigger_at=trigger_at, experiment_variant="champion"
        )
    assert created.ok, created.reason

    async with UnitOfWork(env["sessions"]) as uow:
        revealed = await logic.reveal(
            uow, trade_decision_id=created.trade_decision_id,
            quote_reveal_at=quote_reveal_at, quotes=_quote_map(ctx),
        )
    assert revealed.ok, revealed.reason

    async with UnitOfWork(env["sessions"]) as uow:
        await uow.session.execute(
            text(
                "INSERT INTO trading.operating_cost_entries "
                "(cost_key,cost_kind,amount,release_manifest_id,episode_id,trade_decision_id,"
                " allocation_policy) VALUES (:k,'INFRASTRUCTURE',0,:r,:e,:d,"
                " '{\"kind\":\"fixed_marginal\",\"evidence\":\"observed_zero\","
                " \"provider\":\"perf-infra\"}'::jsonb)"
            ),
            {"k": f"proj-cost-{created.trade_decision_id}", "r": ctx["release"],
             "e": episode, "d": created.trade_decision_id},
        )
        mr = await logic.market_relative(
            uow, trade_decision_id=created.trade_decision_id,
            input_=MarketRelativeInput(decision_mode="BLIND_ONLY"),
        )
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
        )
    assert g7a.ok, g7a.reason

    async with UnitOfWork(env["sessions"]) as uow:
        g7b = await logic.run_g7b(
            uow, trade_decision_id=created.trade_decision_id,
            portfolio=PortfolioGateInput(portfolio_namespace="shadow-champion"),
        )
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
            decided_at=decided_at,
        )
    assert terminal.ok, terminal.reason

    exec_logic = ShadowExecutionLogic(env["execution"], env["ledger"])
    async with UnitOfWork(env["sessions"]) as uow:
        action_sets = (
            await uow.session.execute(
                text("SELECT id FROM trading.action_sets WHERE trade_decision_id=:d"),
                {"d": created.trade_decision_id},
            )
        ).scalars().all()
        legs = await env["decision"].action_set_legs(uow.session, action_sets[0])
        intent_id = (
            await uow.session.execute(
                text(
                    "SELECT id FROM trading.economic_action_intents "
                    "WHERE trade_decision_id=:d AND status='COMMITTED'"
                ),
                {"d": created.trade_decision_id},
            )
        ).scalar_one()
        fill = await exec_logic.shadow_fill(
            uow,
            fill=ShadowFillInput(
                execution_key=f"exec-{created.trade_decision_id}",
                economic_action_intent_id=intent_id,
                action_set_leg_id=legs[0]["id"],
            ),
        )
    assert fill.ok, fill.reason
    assert fill.status == "FILLED"
    return created.trade_decision_id


async def _seed_extra_positions(env: dict, *, spec_id: int, token_id: int,
                                market_id: int, component_id: int, count: int) -> None:
    async with UnitOfWork(env["sessions"]) as uow:
        await uow.session.execute(
            text(
                "INSERT INTO trading.positions "
                "(portfolio_namespace, contract_spec_id, token_id, market_id, component_id, "
                " quantity, cost_basis) "
                "SELECT 'shadow-int-' || lpad(g::text,4,'0'), :spec, :token, :market, :component, "
                " g, 0 FROM generate_series(1,:n) AS g"
            ),
            {"spec": spec_id, "token": token_id, "market": market_id,
             "component": component_id, "n": count},
        )


async def _build_and_rebuild(env: dict, *, extra_positions: int = 0) -> dict:
    """建决策链 + 额外 shadow positions，然后 rebuild_all。返回 env/ctx/spec/component 信息。"""
    # checkpoint received 动态化：stale_at(=received+300s) 相对真实 now() 未来。
    ctx = await _seed(env, book_received_at=datetime.now(timezone.utc) + timedelta(minutes=9))
    episode, spec_ids = await _build_blind_committed_episode(env, ctx)
    await _run_decision_chain(env, ctx, episode, spec_ids)
    async with UnitOfWork(env["sessions"]) as uow:
        component_id = (
            await uow.session.execute(
                text(
                    "SELECT component_id FROM trading.forecast_component_versions "
                    "WHERE id=(SELECT component_version_id FROM trading.forecast_episodes "
                    "WHERE id=:episode)"
                ),
                {"episode": episode},
            )
        ).scalar_one()
    if extra_positions:
        await _seed_extra_positions(
            env, spec_id=spec_ids[0], token_id=ctx["yes_token"],
            market_id=ctx["market"], component_id=component_id, count=extra_positions,
        )
    logic = ProjectionLogic(ProjectionRepository())
    results = await logic.rebuild_all(lambda: UnitOfWork(env["sessions"]))
    return {"ctx": ctx, "episode": episode, "spec_ids": spec_ids, "logic": logic, "results": results}


async def _projection_snapshot(env: dict) -> dict[str, dict]:
    """读 5 张投影表，返回 {table: {"count": n, "hashes": 排序后的 projection_hash}}。

    id/created_at 在重建间变化（TRUNCATE RESTART IDENTITY + now()）；hash 是内容标识，
    重建两次必须全等。
    """
    snap: dict[str, dict] = {}
    async with UnitOfWork(env["sessions"]) as uow:
        for table in PROJECTION_TABLES:
            rows = (await uow.session.execute(
                text(
                    "SELECT projection_hash FROM trading.%s "
                    "ORDER BY projection_hash" % table
                )
            )).fetchall()
            snap[table] = {
                "count": len(rows),
                "hashes": [row[0] for row in rows],
            }
    return snap


@pytest.mark.asyncio
async def test_rebuild_twice_hash_identical(proj_env):
    env = proj_env
    info = await _build_and_rebuild(env, extra_positions=25)
    first = await _projection_snapshot(env)
    # 第二次 rebuild（重复/乱序 event effect=0：重建是 delete+insert，聚合与顺序无关）。
    duplicate = await info["logic"].rebuild_all(lambda: UnitOfWork(env["sessions"]))
    second = await _projection_snapshot(env)
    assert first == second
    assert duplicate == {table: 0 for table in PROJECTION_TABLES}
    for table in PROJECTION_TABLES:
        assert first[table]["count"] > 0, f"{table} empty"


@pytest.mark.asyncio
async def test_out_of_order_projection_generation_is_noop(proj_env):
    env = proj_env
    await _build_and_rebuild(env, extra_positions=5)
    repo = ProjectionRepository()
    async with UnitOfWork(env["sessions"]) as uow:
        watermark = await repo.high_watermark(uow.session, "account_risk_current")
        before = await repo.count_rows(uow.session, "account_risk_current")
        effect = await repo.replace_risk_current(
            uow.session, [], watermark=watermark - 1
        )
        after = await repo.count_rows(uow.session, "account_risk_current")
    assert effect == 0
    assert after == before


@pytest.mark.asyncio
async def test_clear_rebuild_content_hash_equal(proj_env):
    env = proj_env
    info = await _build_and_rebuild(env, extra_positions=25)
    before = await _projection_snapshot(env)
    # 清空全部投影后重建 → 排序内容 hash 全等。
    repo = ProjectionRepository()
    async with UnitOfWork(env["sessions"]) as uow:
        for table in PROJECTION_TABLES:
            await repo.clear_table(uow.session, table)
    await info["logic"].rebuild_all(lambda: UnitOfWork(env["sessions"]))
    after = await _projection_snapshot(env)
    assert before == after


@pytest.mark.asyncio
async def test_keyset_pagination_no_dup_no_miss(proj_env):
    env = proj_env
    info = await _build_and_rebuild(env, extra_positions=25)
    logic = info["logic"]
    seen_ids: list[int] = []
    next_cursor = None
    pages = 0
    while True:
        async with UnitOfWork(env["sessions"]) as uow:
            page = await logic.list(
                uow, "risk_current",
                cursor=next_cursor,
                limit=10,
            )
        rows = page["rows"]
        for row in rows:
            assert row["id"] not in seen_ids, "duplicate id across pages"
            seen_ids.append(row["id"])
        pages += 1
        if not page["has_more"]:
            break
        next_cursor = page["next_cursor"]
    # 25 个 shadow-int-* + 决策链产生的 1 个 shadow-proj-read 位置 = 26 行。
    assert pages >= 3, "expected multiple keyset pages"
    assert len(seen_ids) == 26, f"expected 26 risk rows, got {len(seen_ids)}"


@pytest.mark.asyncio
async def test_keyset_cursor_is_bound_to_filter_and_snapshot(proj_env):
    env = proj_env
    info = await _build_and_rebuild(env, extra_positions=25)
    logic = info["logic"]
    async with UnitOfWork(env["sessions"]) as uow:
        first = await logic.list(uow, "risk_current", limit=5)
    cursor = first["next_cursor"]
    assert set(cursor) == {"sort_time", "id", "filter_hash", "as_of"}
    async with UnitOfWork(env["sessions"]) as uow:
        with pytest.raises(ValueError, match="cursor_filter_mismatch"):
            await logic.list(
                uow,
                "risk_current",
                cursor=cursor,
                filters={"portfolio_namespace": "shadow-int-0001"},
                limit=5,
            )
        with pytest.raises(ValueError, match="cursor_required"):
            await logic.list(
                uow,
                "risk_current",
                after_id=cursor["id"],
                after_as_of=cursor["sort_time"],
                limit=5,
            )


@pytest.mark.asyncio
async def test_invalid_filter_sort_rejected(proj_env):
    env = proj_env
    info = await _build_and_rebuild(env)
    logic = info["logic"]
    async with UnitOfWork(env["sessions"]) as uow:
        with pytest.raises(ValueError, match="unsupported filters"):
            await logic.list(uow, "health_current", filters={"bogus": 1})
        with pytest.raises(ValueError, match="unsupported sort"):
            await logic.list(uow, "health_current", sorts=["bogus"])
        with pytest.raises(ValueError, match="unknown projection"):
            await logic.list(uow, "does_not_exist")
        # repository 层对 filter 值也做 allowlist
        repo = ProjectionRepository()
        with pytest.raises(ValueError, match="unsupported status"):
            await repo.list_health_current(uow.session, status="bogus")
        with pytest.raises(ValueError, match="unsupported stage"):
            await repo.list_funnel_hourly(uow.session, stage="bogus")


@pytest.mark.asyncio
async def test_projection_lag_degrades_only_page(proj_env):
    env = proj_env
    await _build_and_rebuild(env)
    # 投影表缺失（lag/未建）→ 只降级该页面。
    async with UnitOfWork(env["sessions"]) as uow:
        await uow.session.execute(text("DROP TABLE trading.account_risk_current"))
    # 读缺失投影失败（独立事务；该错误终止本事务，业务查询在后续事务中仍可用）。
    async with UnitOfWork(env["sessions"]) as uow:
        with pytest.raises(Exception, match="does not exist"):
            await uow.session.execute(text("SELECT 1 FROM trading.account_risk_current"))
    # 业务事实查询仍可用。
    async with UnitOfWork(env["sessions"]) as uow:
        count = (
            await uow.session.execute(text("SELECT count(*) FROM trading.trade_decisions"))
        ).scalar_one()
        assert count >= 1
        facts = (
            await uow.session.execute(text("SELECT count(*) FROM trading.executions"))
        ).scalar_one()
        assert facts >= 1
