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
from app.logics.trading.evaluation import EvaluationLogic, _jsonable
from app.logics.trading.replay import ReplayLogic
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.evaluation import EvaluationRepository
from app.repositories.trading.settlement import SettlementRepository
from app.schemas.trading.evaluation import MetricRunInput

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


async def _insert_empty_metric_run(env, core, *, run_key, seed=1):
    """Create one real frozen Bernoulli observation and its metric artifact."""
    engine = create_engine(env["url"], poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            cohort = core.get("cohort")
            if cohort is None:
                cohort = c.execute(text(
                    "INSERT INTO trading.evaluation_cohorts "
                    "(cohort_key,status,objective_contract_id,strategy_version_id,"
                    " release_manifest_id,policy_hashes,seed_hash) "
                    "VALUES (:k,'CLOSED',:obj,:strategy,:release,'{}'::jsonb,:seed) "
                    "RETURNING id"
                ), {"k": f"cohort-{run_key}", "obj": core["obj"],
                    "strategy": core["strat"], "release": core["rel"],
                    "seed": "1" * 64}).scalar_one()
            opportunity = c.execute(text(
                "INSERT INTO trading.decision_opportunities "
                "(opportunity_key,cohort_id,chain_type,objective_contract_id,"
                " strategy_version_id,status,disposition,triggered_at) "
                "VALUES (:k,:cohort,'DECISION',:obj,:strategy,'ROUTED','completed',:at) "
                "RETURNING id"
            ), {"k": f"opportunity-{run_key}", "cohort": cohort,
                "obj": core["obj"], "strategy": core["strat"], "at": _T0}).scalar_one()
            episode = c.execute(text(
                "INSERT INTO trading.forecast_episodes "
                "(episode_key,decision_opportunity_id,component_version_id,strategy_version_id,"
                " objective_contract_id,trigger,cutoff_at,horizon,experiment_variant,status) "
                "VALUES (:k,:opportunity,1,:strategy,:obj,'SCHEDULED',:at,'resolution',"
                " 'champion','BLIND_COMMITTED') RETURNING id"
            ), {"k": canonical_hash({"episode": run_key}), "opportunity": opportunity,
                "strategy": core["strat"], "obj": core["obj"], "at": _T0}).scalar_one()
            cs = c.execute(text(
                "INSERT INTO trading.contract_specs "
                "(contract_key,version_no,snapshot_id,kc_resolution_states,token_ids,"
                " token_count,state_count,compiler_version,schema_version,status,content_hash) "
                "VALUES (:k,1,900001,'[\"YES\",\"NO\"]'::jsonb,'{}'::jsonb,2,2,'v1',1,'pass',:h) "
                "RETURNING id"
            ), {"k": f"cs-{run_key}", "h": canonical_hash(run_key)}).scalar_one()
            cluster = c.execute(text(
                "INSERT INTO trading.resolution_clusters "
                "(cluster_key,cluster_version,split,time_block_start,time_block_end,horizon,status) "
                "VALUES (:k,1,'forward_holdout',:start,:end,'resolution','RESOLVED') RETURNING id"
            ), {"k": f"cluster-{run_key}", "start": _T0 - timedelta(days=1),
                "end": _T0 + timedelta(days=1)}).scalar_one()
            market = c.execute(text(
                "INSERT INTO trading.pm_markets (gamma_market_id,condition_id) "
                "VALUES (:g,:condition) RETURNING id"
            ), {"g": f"market-{run_key}", "condition": f"condition-{run_key}"}).scalar_one()
            token = c.execute(text(
                "INSERT INTO trading.pm_tokens (token_id,market_id,outcome_index) "
                "VALUES (:token,:market,0) RETURNING id"
            ), {"token": f"token-{run_key}", "market": market}).scalar_one()
            token_version = c.execute(text(
                "INSERT INTO trading.pm_token_versions "
                "(token_id,version_no,outcome_index,observed_at,received_at) "
                "VALUES (:token,1,0,:at,:at) RETURNING id"
            ), {"token": token, "at": _T0}).scalar_one()
            payout = c.execute(text(
                "INSERT INTO trading.payout_functions "
                "(contract_spec_id,pm_token_id,token_version_id,outcome_index,function_ir,"
                " test_vectors,algorithm_hash,content_hash) "
                "VALUES (:cs,:token,:tv,0,'{\"YES\":\"1\",\"NO\":\"0\"}'::jsonb,"
                " '{}'::jsonb,:h,:h) RETURNING id"
            ), {"cs": cs, "token": token, "tv": token_version, "h": "e" * 64}).scalar_one()
            c.execute(text(
                "INSERT INTO trading.resolution_cluster_memberships "
                "(resolution_cluster_id,contract_spec_id,token_id) VALUES (:cluster,:cs,:token)"
            ), {"cluster": cluster, "cs": cs, "token": token})
            c.execute(text(
                "INSERT INTO trading.forecast_component_contract_specs "
                "(component_version_id,contract_spec_id,h_c,totality_test_hash) "
                "VALUES (1,:cs,'{\"w0\":\"YES\",\"w1\":\"NO\"}'::jsonb,:h)"
            ), {"cs": cs, "h": "f" * 64})
            manifest_id = c.execute(text(
                "INSERT INTO trading.forecast_input_manifests "
                "(episode_id,manifest_key,manifest_hash,evidence_bundle_hash,contract_spec_set_hash,"
                " world_schema_hash,prior_hash,taxonomy_hash,model_binding_hash,prompt_hash,code_hash,content) "
                "VALUES (:episode,:k,:h,:h,:h,:h,:h,:h,:h,:h,:h,'{}'::jsonb) RETURNING id"
            ), {"episode": episode, "k": f"input-{run_key}", "h": "0" * 64}).scalar_one()
            submission = c.execute(text(
                "INSERT INTO trading.forecast_submissions "
                "(episode_id,submission_key,status,q,u,forecast_input_manifest_id,"
                " contract_schema_prior_evidence_hash,algorithm_hash,committed_at) "
                "VALUES (:episode,:k,'BLIND_COMMITTED','{\"w0\":\"0.7\",\"w1\":\"0.3\"}'::jsonb,"
                " '[{\"w0\":\"0.7\",\"w1\":\"0.3\"}]'::jsonb,:m,:h,:alg,:at) RETURNING id"
            ), {"episode": episode, "k": f"submission-{run_key}", "m": manifest_id, "h": "0" * 64,
                "alg": "c" * 64, "at": _T0}).scalar_one()
            target = c.execute(text(
                "INSERT INTO trading.score_targets "
                "(target_key,target_type,contract_spec_id,resolution_cluster_id,horizon,"
                " target_weight,payout_function_id,canonical_side,payout_type) "
                "VALUES (:k,'bernoulli',:cs,:cluster,'resolution',1,:payout,'YES','binary') "
                "RETURNING id"
            ), {"k": f"target-{run_key}", "cs": cs, "cluster": cluster,
                "payout": payout}).scalar_one()
            c.execute(text(
                "INSERT INTO trading.score_target_memberships "
                "(score_target_id,token_id,member_weight) VALUES (:target,:token,1)"
            ), {"target": target, "token": token})
            label = c.execute(text(
                "INSERT INTO trading.resolution_labels "
                "(contract_spec_id,label_key,version_no,state,resolution_state,policy_code_hash) "
                "VALUES (:cs,:k,1,'final_admissible','YES',:h) RETURNING id"
            ), {"cs": cs, "k": f"label-{run_key}", "h": "a" * 64}).scalar_one()
            book_hash = canonical_hash({"book": run_key})
            locator = (
                f"cas/v1/sha256/{book_hash[:2]}/{book_hash[2:4]}/{book_hash}.raw"
            )
            book_artifact = c.execute(text(
                "INSERT INTO trading.artifact_objects "
                "(sha256,original_size,stored_size,mime,compression,storage_driver,storage_version,locator) "
                "VALUES (:h,1,1,'application/json','none','local','cas/v1',:loc) RETURNING id"
            ), {"h": book_hash, "loc": locator}).scalar_one()
            checkpoint = c.execute(text(
                "INSERT INTO trading.pm_book_checkpoints "
                "(token_id,connection_epoch_id,source_kind,book_hash,best_bid,best_ask,"
                " raw_artifact_id,completeness,validity,received_at) "
                "VALUES (:external,NULL,'rest_full',:h,0.60,0.70,:artifact,true,'VALID',:at) "
                "RETURNING id"
            ), {"external": f"token-{run_key}", "h": book_hash,
                "artifact": book_artifact, "at": _T0}).scalar_one()
            binding = c.execute(text(
                "INSERT INTO trading.pm_quote_bindings "
                "(token_id,checkpoint_id,checkpoint_received_at,best_bid,best_ask,price_convention,"
                " as_of,received_at,staleness_policy_ref,stale_at) "
                "VALUES (:external,:checkpoint,:at,0.60,0.70,'probability',:at,:at,:policy,:stale) "
                "RETURNING id"
            ), {"external": f"token-{run_key}", "checkpoint": checkpoint, "at": _T0,
                "policy": "b" * 64, "stale": _T0 + timedelta(hours=1)}).scalar_one()
            baseline_value = {str(token): "0.65"}
            observation_id = c.execute(text(
                "INSERT INTO trading.score_observations "
                "(observation_key,score_target_id,submission_id,label_version_id,"
                " baseline_quote_binding_ids,baseline_value,baseline_value_hash,"
                " baseline_checkpoint_received_at,baseline_quote,baseline_policy_hash,split,"
                " algorithm_hash,metric_id,score_value) "
                "VALUES (:k,:target,:submission,:label,:bindings,:value,:value_hash,:at,0.65,"
                " :policy,'forward_holdout',:algorithm,'bernoulli_brier',0.09) RETURNING id"
            ), {"k": f"obs-{run_key}", "target": target, "submission": submission,
                "label": label, "bindings": json.dumps([binding]),
                "value": json.dumps(baseline_value),
                "value_hash": canonical_hash(baseline_value), "at": _T0,
                "policy": "b" * 64, "algorithm": "c" * 64}).scalar_one()
            c.execute(text("SET LOCAL session_replication_role = origin"))
    finally:
        engine.dispose()
    input_ = MetricRunInput(
        run_key=run_key,
        cohort_id=cohort,
        observation_ids=[observation_id],
        observation_set_hash=canonical_hash(
            {
                "cohort_id": cohort,
                "split": "forward_holdout",
                "ordered_observation_ids": [observation_id],
                "label_versions": {"frozen": [label]},
                "time_blocks": {"resolution": _T0.date().isoformat()},
                "strategy_version_id": core["strat"],
                "release_manifest_id": core["rel"],
            }
        ),
        cohort_query_hash="a" * 64,
        strategy_version_id=core["strat"],
        release_manifest_id=core["rel"],
        label_versions={"frozen": [label]},
        split="forward_holdout",
        time_blocks={"resolution": _T0.date().isoformat()},
        code_hash="b" * 64,
        config_hash="c" * 64,
        seed=seed,
        n_market=0,
        n_episode=0,
        n_resolution_cluster=1,
        n_eff=Decimal("1"),
        results={},
        ci={},
        artifact_hash="0" * 64,
    )
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        created = await logic.run_metric(uow, input_=input_)
        assert created.ok, created.reason
        run_id = created.metric_run_id
        artifact_hash = (
            await uow.session.execute(
                text(
                    "SELECT artifact_hash FROM trading.metric_runs WHERE id = :run_id"
                ),
                {"run_id": run_id},
            )
        ).scalar_one()
    return run_id, artifact_hash


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
            observation_ids = []
            for obs_key, target, sub, decision, label, score in rows:
                observation_ids.append(c.execute(text(
                    "INSERT INTO trading.score_observations "
                    "(observation_key, score_target_id, submission_id, trade_decision_id, "
                    " label_version_id, baseline_quote_binding_ids, baseline_value, "
                    " baseline_value_hash, baseline_checkpoint_received_at, baseline_quote, "
                    " baseline_policy_hash, split, "
                    " algorithm_hash, metric_id, score_value) "
                    "VALUES (:k, :t, :s, :d, :lv, '[1]'::jsonb, '{\"1\":\"0.65\"}'::jsonb, "
                    " :bvh, :at, 0.65, :bh, :sp, :ah, 'bernoulli_brier', :sv) RETURNING id"
                ), {"k": obs_key, "t": target, "s": sub, "d": decision, "lv": label,
                    "bvh": canonical_hash({"1": "0.65"}), "at": _T0,
                    "bh": "b" * 64, "sp": split, "ah": "c" * 64,
                    "sv": score}).scalar_one())
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return label_ids, observation_ids
    finally:
        engine.dispose()


def _insert_review_metric_run(url, core, *, run_key, label_versions, observation_ids):
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            run_id = c.execute(text(
                "INSERT INTO trading.metric_runs "
                "(run_key,cohort_id,observation_ids,observation_set_hash,cohort_query_hash,"
                " strategy_version_id,release_manifest_id,label_versions,"
                " split,time_blocks,code_hash,config_hash,seed,n_market,n_episode,"
                " n_resolution_cluster,n_eff,results,ci,artifact_hash,status,completed_at) "
                "VALUES (:k,1,:oids,:osh,:c,:s,:r,:lv,'forward_holdout','{\"block\":\"frozen\"}'::jsonb,"
                " :ch,:cfg,42,0,0,1,1,:results,:ci,:ah,'COMPLETED',:at) RETURNING id"
            ), {
                "k": run_key, "oids": json.dumps(observation_ids), "osh": "d" * 64,
                "c": "a" * 64, "s": core["strat"], "r": core["rel"],
                "lv": json.dumps(label_versions), "ch": "b" * 64, "cfg": "c" * 64,
                "results": json.dumps({
                    "prediction": {}, "selection": {}, "edge": {},
                    "portfolio": {}, "execution": {},
                }),
                "ci": json.dumps({"prediction": {}}), "ah": canonical_hash(run_key),
                "at": _T0 + timedelta(hours=1),
            }).scalar_one()
            c.execute(text("SET LOCAL session_replication_role = origin"))
            return run_id
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
    core = _seed_core(env["url"], "r1")
    _, manifest = await _insert_empty_metric_run(env, core, run_key="metric-r1", seed=7)
    logic = ReplayLogic(AuditRepository(), EvaluationRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        first = await logic.replay_original(uow, run_key="replay-1", manifest_hash=manifest, seed=7)
    async with UnitOfWork(env["sessions"]) as uow:
        second = await logic.replay_original(uow, run_key="replay-2", manifest_hash=manifest, seed=7)
    async with UnitOfWork(env["sessions"]) as uow:
        retry = await logic.replay_original(
            uow, run_key="replay-1", manifest_hash=manifest, seed=7
        )
    assert first.ok and second.ok
    assert retry.ok and retry.idempotent
    assert first.output_artifact_hash == second.output_artifact_hash
    assert len(first.output_artifact_hash) == 64
    rows = _query(env["url"], "SELECT replay_kind, output_artifact_hash FROM trading.replay_runs ORDER BY run_key")
    assert rows[0][0] == "original" and rows[1][0] == "original"
    assert rows[0][1] == rows[1][1]
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_replay_recomputes_score_and_rejects_forged_source(replay_env):
    env = replay_env
    core = _seed_core(env["url"], "r-score")
    _, manifest = await _insert_empty_metric_run(
        env, core, run_key="metric-r-score", seed=11
    )
    engine = create_engine(env["url"], poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            c.execute(text(
                "UPDATE trading.score_observations SET score_value=0.99 "
                "WHERE observation_key='obs-metric-r-score'"
            ))
            c.execute(text("SET LOCAL session_replication_role = origin"))
    finally:
        engine.dispose()
    logic = ReplayLogic(AuditRepository(), EvaluationRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.replay_original(
            uow, run_key="replay-forged-score", manifest_hash=manifest, seed=11
        )
    assert result.ok is False
    assert result.reason == "replay_score_mismatch:obs-metric-r-score"
    assert _query(
        env["url"],
        "SELECT count(*) FROM trading.replay_runs WHERE run_key='replay-forged-score'",
    )[0][0] == 0


@pytest.mark.asyncio
async def test_replay_new_code_writes_new_run_without_overwrite(replay_env):
    env = replay_env
    core = _seed_core(env["url"], "r2")
    run_id, manifest = await _insert_empty_metric_run(env, core, run_key="metric-r2", seed=3)
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
    core = _seed_core(env["url"], "r3")
    _, manifest = await _insert_empty_metric_run(env, core, run_key="metric-r3", seed=9)
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
    core = _seed_core(env["url"], "r4")
    run_id, manifest = await _insert_empty_metric_run(env, core, run_key="metric-r4", seed=5)
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
    core = _seed_core(env["url"], "r5")
    manifest = canonical_hash({"kind": "metric", "cohort": "c5"})
    label_ids, observation_ids = _seed_observations_with_labels(env["url"])
    # 两个相同 observation 集、不同 run（review_key 含 metric_run_id → 不冲突）。
    run1 = _insert_review_metric_run(
        env["url"], core, run_key="run-r1", label_versions={"v": label_ids},
        observation_ids=observation_ids,
    )
    run2 = _insert_review_metric_run(
        env["url"], core, run_key="run-r2", label_versions={"v": label_ids},
        observation_ids=observation_ids,
    )
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
