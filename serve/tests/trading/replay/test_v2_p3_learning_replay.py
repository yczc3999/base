"""WP-04 P3 learning replay 集成测试（真 PostgreSQL，Checkpoint C）。

- 原样 replay 两次 hash 全等（同 manifest+code+seed）；新 code/variant 写新 run，不覆盖
  原事实；未来 label/quote taint=0（replay 前后未来 split 的 label/quote 行数不变）。
- crash 注入：在 replay 事务内 rollback → 无半条有效证据（metric_runs 无 RUNNING 半行、
  promotion_decisions 无新行、replay_runs 无输出 artifact 行）。
- top-loss/regret + 随机成功样本按 seed 入 review；root-cause taxonomy 拒绝未知值。
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
from app.logics.trading.replay import ReplayLogic
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.evaluation import EvaluationRepository

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V40 = "b1000040"

_T0 = datetime.now(timezone.utc).replace(microsecond=0)


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


def _execute(url, sql, params=None):
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text(sql), params or {})
    finally:
        engine.dispose()


def _insert_id(url, sql, params=None):
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as c:
            return c.execute(text(sql), params or {}).scalar_one()
    finally:
        engine.dispose()


def _query(url, sql, params=None):
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as c:
            return c.execute(text(sql), params or {}).fetchall()
    finally:
        engine.dispose()


def _seed_core(url, suffix="rp"):
    _execute(url, "INSERT INTO trading.strategy_objective_contracts (contract_key, version_no, content, schema_version, content_hash, status) VALUES (:k, 1, '{}'::jsonb, 1, :h, 'active')", {"k": f"obj-{suffix}", "h": "a" * 64})
    obj = _query(url, "SELECT id FROM trading.strategy_objective_contracts WHERE contract_key=:k", {"k": f"obj-{suffix}"})[0][0]
    _execute(url, "INSERT INTO trading.strategy_versions (strategy_key, version_no, content, schema_version, content_hash, status) VALUES (:k, 1, '{}'::jsonb, 1, :h, 'active')", {"k": f"strat-{suffix}", "h": "b" * 64})
    strat = _query(url, "SELECT id FROM trading.strategy_versions WHERE strategy_key=:k", {"k": f"strat-{suffix}"})[0][0]
    _execute(url, "INSERT INTO trading.capital_permission_manifests (name, mode, capability, limits, evaluation_capital, authorized_capital, content_hash, status) VALUES (:k, 'shadow', '{}'::jsonb, '{}'::jsonb, 0, 0, :h, 'active')", {"k": f"perm-{suffix}", "h": "c" * 64})
    cap = _query(url, "SELECT id FROM trading.capital_permission_manifests WHERE name=:k", {"k": f"perm-{suffix}"})[0][0]
    _execute(url, "INSERT INTO trading.runtime_config_versions (config_key, version_no, content, schema_version, content_hash, status) VALUES (:k, 1, '{}'::jsonb, 1, :h, 'active')", {"k": f"cfg-{suffix}", "h": "d" * 64})
    cfg = _query(url, "SELECT id FROM trading.runtime_config_versions WHERE config_key=:k", {"k": f"cfg-{suffix}"})[0][0]
    _execute(url, "INSERT INTO trading.execution_spec_versions (spec_key, version_no, content, schema_version, content_hash, status) VALUES (:k, 1, '{}'::jsonb, 1, :h, 'active')", {"k": f"exec-{suffix}", "h": "e" * 64})
    exec_id = _query(url, "SELECT id FROM trading.execution_spec_versions WHERE spec_key=:k", {"k": f"exec-{suffix}"})[0][0]
    _execute(url, "INSERT INTO trading.release_manifests (release_name, config_version_id, strategy_version_id, execution_spec_version_id, capital_permission_manifest_id, git_sha, image_digest, db_revision, total_hash, status) VALUES (:k, :cfg, :strat, :exec, :cap, 'abc', 'img', :rev, :h, 'active')", {"k": f"rel-{suffix}", "cfg": cfg, "strat": strat, "exec": exec_id, "cap": cap, "rev": V40, "h": "f" * 64})
    rel = _query(url, "SELECT id FROM trading.release_manifests WHERE release_name=:k", {"k": f"rel-{suffix}"})[0][0]
    return {"obj": obj, "strat": strat, "rel": rel, "exec": exec_id, "cap": cap}


def _insert_metric_run(url, core, *, run_key, label_versions=None, completed=True):
    run_id = _insert_id(
        url,
        "INSERT INTO trading.metric_runs "
        "(run_key, cohort_query_hash, strategy_version_id, release_manifest_id, label_versions, "
        " split, time_blocks, code_hash, config_hash, seed, n_market, n_episode, "
        " n_resolution_cluster, n_eff, results, ci, artifact_hash) VALUES "
        "(:k, :cqh, :sv, :rm, :lv, 'forward_holdout', '{}'::jsonb, "
        " :ch, :cgh, 1, 1, 1, 1, 1.0, '{}'::jsonb, '{}'::jsonb, :ah) RETURNING id",
        {"k": run_key, "cqh": "a" * 64, "sv": core["strat"], "rm": core["rel"],
         "lv": json.dumps(label_versions or {}),
         "ch": "b" * 64, "cgh": "c" * 64, "ah": "d" * 64},
    )
    if completed:
        _execute(url, "UPDATE trading.metric_runs SET status='COMPLETED', completed_at=now() WHERE id=:id", {"id": run_id})
    return run_id


def _seed_observations_with_labels(url, *, split="forward_holdout", count=3):
    """contract_spec + final_admissible labels + score_observations（replica 绕过状态机
    deferred / BEFORE trigger，只为了让 error_review_selection 有可读观察）。"""
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            cs = c.execute(text(
                "INSERT INTO trading.contract_specs "
                "(contract_key, version_no, snapshot_id, kc_resolution_states, token_ids, "
                " token_count, state_count, compiler_version, schema_version, status, content_hash) "
                "VALUES ('cs-rp', 1, 900001, '[\"YES\"]'::jsonb, '{}'::jsonb, 1, 1, 'v1', 1, 'pass', :h) RETURNING id"
            ), {"h": "e" * 64}).scalar_one()
            label_ids = []
            for i in range(count):
                label_id = c.execute(text(
                    "INSERT INTO trading.resolution_labels "
                    "(contract_spec_id, label_key, version_no, state, resolution_state, policy_code_hash) "
                    "VALUES (:cs, :k, 1, 'final_admissible', 'YES', :h) RETURNING id"
                ), {"cs": cs, "k": f"lk-rp-{i}", "h": "a" * 64}).scalar_one()
                label_ids.append(label_id)
            rows = [
                ("obs-rp-1", 1, 1, 5, label_ids[0], Decimal("0.01")),
                ("obs-rp-2", 1, 1, None, label_ids[1], Decimal("0.49")),
                ("obs-rp-3", 1, 1, None, label_ids[2], Decimal("0.25")),
            ]
            for obs_key, target, sub, decision, label, score in rows:
                c.execute(text(
                    "INSERT INTO trading.score_observations "
                    "(observation_key, score_target_id, submission_id, trade_decision_id, "
                    " label_version_id, baseline_quote, baseline_policy_hash, split, "
                    " algorithm_hash, metric_id, score_value) "
                    "VALUES (:k, :t, :s, :d, :lv, 0.65, :bh, :sp, :ah, 'bernoulli_brier', :sv)"
                ), {"k": obs_key, "t": target, "s": sub, "d": decision, "lv": label,
                    "bh": "b" * 64, "sp": split, "ah": "c" * 64, "sv": score})
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return label_ids
    finally:
        engine.dispose()


@pytest_asyncio.fixture
async def replay_env(temp_pg_db):
    _run(command.upgrade, V40, temp_pg_db.url)
    admin = make_url(temp_pg_db.url)
    async_url = admin.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    engine = create_async_engine(
        async_url, pool_size=4, max_overflow=0,
        json_serializer=lambda obj: json.dumps(obj),
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    env = {"sessions": sessions, "url": temp_pg_db.url, "engine": engine}
    yield env
    await engine.dispose()


@pytest.mark.asyncio
async def test_replay_original_twice_hash_equal(replay_env):
    env = replay_env
    _seed_core(env["url"], "r1")
    manifest = canonical_hash({"kind": "metric", "cohort": "c1"})
    _insert_metric_run(env["url"], {"strat": 1, "rel": 1}, run_key=manifest)
    logic = ReplayLogic(AuditRepository(), EvaluationRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        first = await logic.replay_original(uow, run_key="replay-1", manifest_hash=manifest, seed=7)
    async with UnitOfWork(env["sessions"]) as uow:
        second = await logic.replay_original(uow, run_key="replay-2", manifest_hash=manifest, seed=7)
    assert first.ok and second.ok
    assert first.output_artifact_hash == second.output_artifact_hash
    assert len(first.output_artifact_hash) == 64
    rows = _query(env["url"], "SELECT replay_kind, output_artifact_hash FROM trading.replay_runs ORDER BY run_key")
    assert rows[0][0] == "original" and rows[1][0] == "original"
    assert rows[0][1] == rows[1][1]


@pytest.mark.asyncio
async def test_replay_new_code_writes_new_run_without_overwrite(replay_env):
    env = replay_env
    _seed_core(env["url"], "r2")
    manifest = canonical_hash({"kind": "metric", "cohort": "c2"})
    run_id = _insert_metric_run(env["url"], {"strat": 1, "rel": 1}, run_key=manifest)
    logic = ReplayLogic(AuditRepository(), EvaluationRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        original = await logic.replay_original(uow, run_key="replay-orig", manifest_hash=manifest, seed=3)
    async with UnitOfWork(env["sessions"]) as uow:
        new_code = await logic.replay_new_code(
            uow, run_key="replay-new", manifest_hash=manifest,
            code_hash="f" * 64, seed=3,
        )
    assert original.ok and new_code.ok
    assert new_code.replay_kind == "new_code"
    assert new_code.output_artifact_hash != original.output_artifact_hash
    # 原 metric run 未被覆盖。
    row = _query(env["url"], "SELECT status, code_hash FROM trading.metric_runs WHERE id=:id", {"id": run_id})
    assert row == [("COMPLETED", "b" * 64)]


@pytest.mark.asyncio
async def test_replay_future_taint_zero(replay_env):
    env = replay_env
    _seed_core(env["url"], "r3")
    manifest = canonical_hash({"kind": "metric", "cohort": "c3"})
    _insert_metric_run(env["url"], {"strat": 1, "rel": 1}, run_key=manifest)
    # 未来 split 的 label / quote 计数（replay 不得写入）。
    labels_before = _query(env["url"], "SELECT count(*) FROM trading.resolution_labels")[0][0]
    quotes_before = _query(env["url"], "SELECT count(*) FROM trading.pm_book_checkpoints")[0][0]
    logic = ReplayLogic(AuditRepository(), EvaluationRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.replay_original(uow, run_key="replay-taint", manifest_hash=manifest, seed=9)
    assert result.ok
    assert _query(env["url"], "SELECT count(*) FROM trading.resolution_labels")[0][0] == labels_before
    assert _query(env["url"], "SELECT count(*) FROM trading.pm_book_checkpoints")[0][0] == quotes_before
    # replay 只写 replay_runs。
    assert _query(env["url"], "SELECT count(*) FROM trading.replay_runs")[0][0] == 1


@pytest.mark.asyncio
async def test_replay_crash_rollback_no_half_evidence(replay_env):
    env = replay_env
    _seed_core(env["url"], "r4")
    manifest = canonical_hash({"kind": "metric", "cohort": "c4"})
    run_id = _insert_metric_run(env["url"], {"strat": 1, "rel": 1}, run_key=manifest)
    prom_before = _query(env["url"], "SELECT count(*) FROM trading.promotion_decisions")[0][0]
    logic = ReplayLogic(AuditRepository(), EvaluationRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.replay_original(uow, run_key="replay-crash", manifest_hash=manifest, seed=5)
        assert result.ok
        # crash：同一 UoW 内回滚，不产生半条有效证据。
        await uow.rollback()
    assert _query(env["url"], "SELECT count(*) FROM trading.replay_runs WHERE run_key='replay-crash'")[0][0] == 0
    assert _query(env["url"], "SELECT status FROM trading.metric_runs WHERE id=:id", {"id": run_id}) == [("COMPLETED",)]
    assert _query(env["url"], "SELECT count(*) FROM trading.promotion_decisions")[0][0] == prom_before


@pytest.mark.asyncio
async def test_error_review_selection_deterministic_and_taxonomy(replay_env):
    env = replay_env
    _seed_core(env["url"], "r5")
    manifest = canonical_hash({"kind": "metric", "cohort": "c5"})
    label_ids = _seed_observations_with_labels(env["url"])
    # 两个相同 observation 集、不同 run（review_key 含 metric_run_id → 不冲突）。
    run1 = _insert_metric_run(env["url"], {"strat": 1, "rel": 1}, run_key="run-r1",
                              label_versions={"v": label_ids})
    run2 = _insert_metric_run(env["url"], {"strat": 1, "rel": 1}, run_key="run-r2",
                              label_versions={"v": label_ids})
    logic = ReplayLogic(AuditRepository(), EvaluationRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        first = await logic.error_review_selection(uow, metric_run_id=run1, seed=42)
        assert first.ok, first.reason
        assert first.count >= 1
    async with UnitOfWork(env["sessions"]) as uow:
        second = await logic.error_review_selection(uow, metric_run_id=run2, seed=42)
    assert second.count == first.count
    rows = _query(env["url"], "SELECT review_type, observation_key FROM trading.error_reviews ORDER BY id")
    assert len(rows) == first.count + second.count
    # 固定 seed 可复现：两次选择相同（相同 observation 集 + seed → 相同 (type, obs)）。
    half = first.count
    assert {(r[0], r[1]) for r in rows[:half]} == {(r[0], r[1]) for r in rows[half:]}
    # root-cause taxonomy 拒绝未知值。
    async with UnitOfWork(env["sessions"]) as uow:
        bad = await logic.error_review_selection(
            uow, metric_run_id=run1, seed=42,
            explicit_taxonomies={"obs-rp-1": "bogus"},
        )
    assert bad.ok is False
    assert bad.reason == "error_review_taxonomy_unknown:bogus"
