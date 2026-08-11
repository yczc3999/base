"""WP-04 promotion gate G8 集成测试（真 PostgreSQL，Checkpoint C）。

G8 绑定 ``(G8, metric_run, metric_run_id)``；deferred trigger ``v2_validate_gate_g8`` 强约束
target 存在且 COMPLETED、release==version_manifest。``review_promotion_g8`` 只 future-effective。

至少证明（任务 §7）：
- G8 gate：未 COMPLETED 的 run → 拒 ``v2_gate_g8_target_invalid``；COMPLETED +
  release==version_manifest → 过；g8_approved 可读。
- promotion 引用未污染 holdout 才可 APPROVED；train/validation-only、inadmissible label、
  篡改 holdout → REJECTED。
- G8 只 future-effective：approved 后历史 cohort/assignment/metric/forecast 零变化；
  strategy approval 只创建未来 shadow assignment，``authorized_capital`` 仍为 0。
- ``review_promotion_g8`` 拒绝错误 promotion_type / policy_hash。
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
from app.logics.trading.evaluation import EvaluationLogic, _promotion_policy_hash
from app.orchestrator.trading_state_machine import IllegalTransitionError, TradingStateMachine
from app.repositories.trading.evaluation import EvaluationRepository
from app.repositories.trading.settlement import SettlementRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.schemas.trading.evaluation import PromotionDecisionInput

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


def _seed_core(url, suffix="pg"):
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
    promotion_hash = _promotion_policy_hash()
    _execute(
        url,
        "INSERT INTO trading.policy_type_scopes (policy_type, scope_type, scope_key) "
        "VALUES (:type, 'promotion', :scope)",
        {"type": f"p3_promotion_{suffix}", "scope": f"p3-promotion-{suffix}"},
    )
    _execute(
        url,
        "INSERT INTO trading.policy_freezes "
        "(policy_type, scope_type, scope_key, policy_version, policy_content_hash, "
        " release_manifest_id, frozen_at, status) "
        "VALUES (:type, 'promotion', :scope, 1, :hash, :release, :at, 'frozen')",
        {"type": f"p3_promotion_{suffix}", "scope": f"p3-promotion-{suffix}",
         "hash": promotion_hash, "release": rel, "at": _T0 - timedelta(days=1)},
    )
    return {"obj": obj, "strat": strat, "rel": rel, "exec": exec_id, "cap": cap}


def _insert_metric_run(url, core, *, run_key, split="forward_holdout",
                       label_versions=None, completed=True, results=None):
    if results is None:
        results = {
            "prediction": {"hard_guardrail_pass": True},
            "selection": {"hard_guardrail_pass": True},
            "edge": {"hard_guardrail_pass": True},
            "portfolio": {
                "hard_guardrail_pass": True,
                "not_evaluable": False,
                "system_net": "1",
            },
            "execution": {"hard_guardrail_pass": True},
        }
    # Promotion tests consume an already frozen metric as their fixture.  Build
    # that immutable prerequisite under replica mode rather than fabricating a
    # partial observation graph that would weaken the metric-evidence trigger.
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            run_id = c.execute(
                text(
                    "INSERT INTO trading.metric_runs "
                    "(run_key, cohort_id, observation_ids, observation_set_hash, "
                    " cohort_query_hash, strategy_version_id, release_manifest_id, label_versions, "
                    " split, time_blocks, code_hash, config_hash, seed, n_market, n_episode, "
                    " n_resolution_cluster, n_eff, results, ci, artifact_hash, status, completed_at) "
                    "VALUES (:k, 900001, '[900001]'::jsonb, :osh, :cqh, :sv, :rm, :lv, "
                    " :split, :time_blocks, :ch, :cgh, 1, 1, 2, 2, 2.0, :results, "
                    " '{}'::jsonb, :ah, :status, :completed_at) RETURNING id"
                ),
                {"k": run_key, "osh": "e" * 64, "cqh": "a" * 64,
                 "sv": core["strat"], "rm": core["rel"],
                 "lv": json.dumps(label_versions or {}), "split": split,
                 "time_blocks": json.dumps({"resolution": _T0.date().isoformat()}),
                 "ch": "b" * 64, "cgh": "c" * 64, "ah": "d" * 64,
                 "results": json.dumps(results),
                 "status": "COMPLETED" if completed else "RUNNING",
                 "completed_at": _T0 if completed else None},
            ).scalar_one()
            experiment = c.execute(text(
                "INSERT INTO trading.experiments "
                "(experiment_key,hypothesis,hypothesis_hash,primary_metric,guardrails,"
                " unique_change_field,champion_input_manifest_hash,"
                " challenger_input_manifest_hash,sample_policy,stopping_rule,seed,status,"
                " time_block_start,time_block_end) VALUES "
                "(:key,'paired',:hh,'bernoulli_brier','{}'::jsonb,"
                " 'strategy_version_id',:from_ref,:to_ref,"
                " '{\"split\":\"forward_holdout\",\"min_n_eff\":\"2\"}'::jsonb,"
                " '{\"min_n_eff\":\"2\"}'::jsonb,1,'COMPLETED',:start,:end) "
                "RETURNING id"
            ), {"key": f"experiment-{run_key}", "hh": canonical_hash({"run": run_key}),
                "from_ref": "a" * 64, "to_ref": "b" * 64,
                "start": _T0 - timedelta(days=1),
                "end": _T0 + timedelta(days=1)}).scalar_one()
            c.execute(text(
                "INSERT INTO trading.experiment_variants "
                "(experiment_id,variant_key,variant_type,input_manifest_hash,"
                " strategy_version_id,release_manifest_id) VALUES "
                "(:e,'champ','champion',:from_ref,:sv,:rm),"
                "(:e,'chal','challenger',:to_ref,:sv,:rm)"
            ), {"e": experiment, "from_ref": "a" * 64, "to_ref": "b" * 64,
                "sv": core["strat"], "rm": core["rel"]})
            c.execute(text(
                "INSERT INTO trading.challenger_variants "
                "(experiment_id,variant_key,challenger_type,changed_fields,policy_hash,status) "
                "VALUES (:e,'chal','strategy',"
                "'{\"strategy_version_id\":\"next\"}'::jsonb,:policy,'ACTIVE')"
            ), {"e": experiment, "policy": "9" * 64})
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return run_id
    finally:
        engine.dispose()


def _seed_g8_pass(url, core, run_id):
    _execute(
        url,
        "INSERT INTO trading.gate_decisions "
        "(gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, "
        " result, reason_code, committed_at) "
        "VALUES ('G8', 'metric_run', :run, :input, :policy, :release, "
        " 'PASS', NULL, :committed)",
        {"run": run_id, "input": canonical_hash({"metric_run_id": run_id}),
         "policy": _promotion_policy_hash(), "release": core["rel"],
         "committed": _T0},
    )


def _seed_contract_spec(url, key):
    """replica 绕过 snapshot FK，插入最小 contract_spec。"""
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            cs = c.execute(text(
                "INSERT INTO trading.contract_specs "
                "(contract_key, version_no, snapshot_id, kc_resolution_states, token_ids, "
                " token_count, state_count, compiler_version, schema_version, status, content_hash) "
                "VALUES (:k, 1, 900001, '[\"YES\"]'::jsonb, '{}'::jsonb, 1, 1, 'v1', 1, 'pass', :h) RETURNING id"
            ), {"k": key, "h": "e" * 64}).scalar_one()
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return cs
    finally:
        engine.dispose()


def _insert_label(url, contract_spec_id, label_key, state):
    return _insert_id(
        url,
        "INSERT INTO trading.resolution_labels (contract_spec_id, label_key, version_no, state, policy_code_hash) "
        "VALUES (:cs, :k, 1, :st, :h) RETURNING id",
        {"cs": contract_spec_id, "k": label_key, "st": state, "h": "a" * 64},
    )


@pytest_asyncio.fixture
async def gate_env(temp_pg_db):
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


# ---------------- G8 gate 强约束 ----------------

@pytest.mark.asyncio
async def test_g8_rejects_non_completed_target(gate_env):
    env = gate_env
    core = _seed_core(env["url"], "g8a")
    run_id = _insert_metric_run(env["url"], core, run_key="run-g8a", completed=False)
    state = TradingStateMachine(WorkflowRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        with pytest.raises(IllegalTransitionError, match="g8_metric_run_not_completed"):
            await state.review_promotion_g8(
                uow, metric_run_id=run_id,
                promotion_type="strategy", from_ref="a" * 64, to_ref="b" * 64,
                evidence_manifest_hash="d" * 64, policy_hash=_promotion_policy_hash(),
                version_manifest_id=core["rel"], result="PASS", reason_code=None,
                committed_at=_T0,
            )


@pytest.mark.asyncio
async def test_g8_rejects_wrong_policy_hash_and_type(gate_env):
    env = gate_env
    core = _seed_core(env["url"], "g8b")
    run_id = _insert_metric_run(env["url"], core, run_key="run-g8b")
    state = TradingStateMachine(WorkflowRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        with pytest.raises(IllegalTransitionError, match="g8_policy_hash_mismatch"):
            await state.review_promotion_g8(
                uow, metric_run_id=run_id, promotion_type="strategy",
                from_ref="a" * 64, to_ref="b" * 64, evidence_manifest_hash="d" * 64,
                policy_hash="d" * 64, version_manifest_id=core["rel"],
                result="PASS", reason_code=None, committed_at=_T0,
            )
        with pytest.raises(IllegalTransitionError, match="g8_promotion_type_unknown"):
            await state.review_promotion_g8(
                uow, metric_run_id=run_id, promotion_type="wallet",
                from_ref="a" * 64, to_ref="b" * 64, evidence_manifest_hash="d" * 64,
                policy_hash="d" * 64, version_manifest_id=core["rel"],
                result="PASS", reason_code=None, committed_at=_T0,
            )


@pytest.mark.asyncio
async def test_g8_completed_target_with_release_passes(gate_env):
    env = gate_env
    core = _seed_core(env["url"], "g8c")
    run_id = _insert_metric_run(env["url"], core, run_key="run-g8c")
    state = TradingStateMachine(WorkflowRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        await state.review_promotion_g8(
            uow, metric_run_id=run_id, promotion_type="strategy",
            from_ref="a" * 64, to_ref="b" * 64,
            evidence_manifest_hash="d" * 64, policy_hash=_promotion_policy_hash(),
            version_manifest_id=core["rel"], result="PASS", reason_code=None,
            committed_at=_T0,
        )
        assert await state.g8_approved(uow, run_id) is True
    row = _query(env["url"], "SELECT gate, target_kind, target_id, result FROM trading.gate_decisions WHERE gate='G8' AND target_id=:id", {"id": run_id})
    assert row == [("G8", "metric_run", run_id, "PASS")]


@pytest.mark.asyncio
async def test_g8_rejects_wrong_release_binding(gate_env):
    env = gate_env
    core = _seed_core(env["url"], "g8d")
    core2 = _seed_core(env["url"], "g8d2")
    run_id = _insert_metric_run(env["url"], core, run_key="run-g8d")
    state = TradingStateMachine(WorkflowRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        with pytest.raises(IllegalTransitionError, match="g8_release_binding_mismatch"):
            await state.review_promotion_g8(
                uow, metric_run_id=run_id, promotion_type="strategy",
                from_ref="a" * 64, to_ref="b" * 64,
                evidence_manifest_hash="d" * 64, policy_hash=_promotion_policy_hash(),
                version_manifest_id=core2["rel"], result="PASS", reason_code=None,
                committed_at=_T0,
            )


@pytest.mark.asyncio
async def test_g8_rejects_wrong_metric_evidence_manifest(gate_env):
    env = gate_env
    core = _seed_core(env["url"], "g8-evidence")
    run_id = _insert_metric_run(env["url"], core, run_key="run-g8-evidence")
    state = TradingStateMachine(WorkflowRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        with pytest.raises(
            IllegalTransitionError, match="g8_evidence_manifest_mismatch"
        ):
            await state.review_promotion_g8(
                uow, metric_run_id=run_id, promotion_type="strategy",
                from_ref="a" * 64, to_ref="b" * 64,
                evidence_manifest_hash="c" * 64,
                policy_hash=_promotion_policy_hash(),
                version_manifest_id=core["rel"], result="PASS", reason_code=None,
                committed_at=_T0,
            )


# ---------------- promotion guardrails ----------------

@pytest.mark.asyncio
async def test_strategy_promotion_approved_on_uncontaminated_holdout(gate_env):
    env = gate_env
    core = _seed_core(env["url"], "p1")
    run_id = _insert_metric_run(env["url"], core, run_key="run-p1")
    _seed_g8_pass(env["url"], core, run_id)
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    future = _T0 + timedelta(days=30)
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.promote(
            uow, input_=PromotionDecisionInput(
                promotion_key="pk-ok", metric_run_id=run_id, promotion_type="strategy",
                from_ref="a" * 64, to_ref="b" * 64,
                evidence_manifest_hash="d" * 64,
                status="APPROVED", future_effective_at=future,
            )
        )
    assert result.status == "APPROVED", result.reason


@pytest.mark.asyncio
async def test_train_validation_only_result_rejected(gate_env):
    env = gate_env
    core = _seed_core(env["url"], "p2")
    run_id = _insert_metric_run(env["url"], core, run_key="run-p2", split="validation")
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.promote(
            uow, input_=PromotionDecisionInput(
                promotion_key="pk-tv", metric_run_id=run_id, promotion_type="strategy",
                from_ref="a" * 64, to_ref="b" * 64,
                evidence_manifest_hash="d" * 64,
                status="APPROVED",
                future_effective_at=_T0 + timedelta(days=30),
            )
        )
    assert result.status == "REJECTED"
    assert result.reason == "promotion_train_validation_only_result"


@pytest.mark.asyncio
async def test_inadmissible_label_rejected(gate_env):
    env = gate_env
    core = _seed_core(env["url"], "p3")
    cs = _seed_contract_spec(env["url"], "cs-p3")
    pending_label = _insert_label(env["url"], cs, "lk-p3", "pending")
    run_id = _insert_metric_run(env["url"], core, run_key="run-p3",
                                label_versions={"v": [pending_label]})
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.promote(
            uow, input_=PromotionDecisionInput(
                promotion_key="pk-ial", metric_run_id=run_id, promotion_type="strategy",
                from_ref="a" * 64, to_ref="b" * 64,
                evidence_manifest_hash="d" * 64,
                status="APPROVED",
                future_effective_at=_T0 + timedelta(days=30),
            )
        )
    assert result.status == "REJECTED"
    assert result.reason == "promotion_inadmissible_label"


@pytest.mark.asyncio
async def test_holdout_tampered_promotion_rejected(gate_env):
    env = gate_env
    core = _seed_core(env["url"], "p4")
    cs = _seed_contract_spec(env["url"], "cs-p4")
    # Build the holdout cluster through its legal OPEN→FROZEN lifecycle.
    cluster_id = _insert_id(env["url"], "INSERT INTO trading.resolution_clusters (cluster_key, cluster_version, split, time_block_start, time_block_end, horizon, status) VALUES ('ho-p4', 1, 'forward_holdout', :tbs, :tbe, 'resolution', 'OPEN') RETURNING id", {"tbs": _T0, "tbe": _T0 + timedelta(days=1)})
    tok = _insert_id(env["url"], "INSERT INTO trading.pm_markets (gamma_market_id, condition_id) VALUES ('m-p4', 'c-p4') RETURNING id")
    # 不需要 token 的真实 id；用 pg_tokens 兼容处理 —— 直接插 membership 用 contract 与
    # 虚拟 token（replica 绕过 FK）。
    engine = create_engine(env["url"], poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            tk = c.execute(text("INSERT INTO trading.pm_tokens (token_id, market_id, outcome_index) VALUES ('t-p4', :m, 0) RETURNING id"), {"m": tok}).scalar_one()
            c.execute(text("INSERT INTO trading.resolution_cluster_memberships (resolution_cluster_id, contract_spec_id, token_id) VALUES (:c, :cs, :tk)"), {"c": cluster_id, "cs": cs, "tk": tk})
            c.execute(text("SET LOCAL session_replication_role = origin"))
    finally:
        engine.dispose()
    _execute(
        env["url"],
        "UPDATE trading.resolution_clusters SET status='FROZEN' WHERE id=:id",
        {"id": cluster_id},
    )
    # final label on contract（cluster 已 FROZEN → 允许）。
    artifact_sha = canonical_hash({"r": "YES"})
    art = _insert_id(env["url"], "INSERT INTO trading.artifact_objects (sha256, original_size, stored_size, mime, compression, storage_driver, storage_version, locator) VALUES (:sha, 1, 1, 'application/json', 'none', 'local', 'cas/v1', :loc) RETURNING id", {"sha": artifact_sha, "loc": f"cas/v1/sha256/{artifact_sha[:2]}/{artifact_sha[2:4]}/{artifact_sha}.raw"})
    v1 = _insert_label(env["url"], cs, "lk-p4a", "pending")
    v2 = _insert_id(env["url"], "INSERT INTO trading.resolution_labels (contract_spec_id, label_key, version_no, state, policy_code_hash, supersedes_id) VALUES (:cs, 'lk-p4a', 2, 'provisional', :h, :sup) RETURNING id", {"cs": cs, "h": "b" * 64, "sup": v1})
    # Construct the intentionally corrupted historical label fixture under
    # replica mode; production terminal-label guards are covered separately.
    engine = create_engine(env["url"], poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            final_id = c.execute(text(
                "INSERT INTO trading.resolution_labels "
                "(contract_spec_id, label_key, version_no, state, resolution_state, "
                " resolution_source, evidence_artifact_id, raw_outcome, token_cashflow, "
                " policy_code_hash, supersedes_id, auditor_identity) "
                "VALUES (:cs, 'lk-p4a', 3, 'final_admissible', 'YES', 'gamma', "
                " :art, :ro, :tc, :h, :sup, 'fixture-auditor') RETURNING id"
            ), {"cs": cs, "art": art, "ro": json.dumps({"r": "YES"}),
                "tc": json.dumps({str(tk): "1"}), "h": "c" * 64,
                "sup": v2}).scalar_one()
            c.execute(text("SET LOCAL session_replication_role = origin"))
    finally:
        engine.dispose()
    # Simulate an assignment appended after the outcome became known (replica only to
    # construct the corrupt fixture; production append-only guards reject this rewrite).
    engine = create_engine(env["url"], poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            c.execute(text(
                "UPDATE trading.resolution_cluster_memberships SET added_at="
                "(SELECT created_at + interval '1 second' FROM trading.resolution_labels "
                " WHERE id=:label) WHERE resolution_cluster_id=:cluster"
            ), {"label": final_id, "cluster": cluster_id})
            c.execute(text("SET LOCAL session_replication_role = origin"))
    finally:
        engine.dispose()
    run_id = _insert_metric_run(env["url"], core, run_key="run-p4",
                                label_versions={"v": [final_id]})
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.promote(
            uow, input_=PromotionDecisionInput(
                promotion_key="pk-tam", metric_run_id=run_id, promotion_type="strategy",
                from_ref="a" * 64, to_ref="b" * 64,
                evidence_manifest_hash="d" * 64,
                status="APPROVED",
                future_effective_at=_T0 + timedelta(days=30),
            )
        )
    assert result.status == "REJECTED"
    assert result.reason == "promotion_holdout_tampered"


# ---------------- G8 只 future-effective ----------------

@pytest.mark.asyncio
async def test_g8_future_effective_no_history_writeback(gate_env):
    env = gate_env
    core = _seed_core(env["url"], "fe")
    run_id = _insert_metric_run(env["url"], core, run_key="run-fe")
    state = TradingStateMachine(WorkflowRepository())

    def count(table):
        return _query(env["url"], f"SELECT count(*) FROM trading.{table}")[0][0]

    before = {
        "cohort": count("evaluation_cohorts"),
        "gate_decisions": count("gate_decisions"),
        "metric_runs": count("metric_runs"),
        "forecast_episodes": count("forecast_episodes"),
        "promotion_decisions": count("promotion_decisions"),
    }
    async with UnitOfWork(env["sessions"]) as uow:
        await state.review_promotion_g8(
            uow, metric_run_id=run_id, promotion_type="strategy",
            from_ref="a" * 64, to_ref="b" * 64,
            evidence_manifest_hash="d" * 64, policy_hash=_promotion_policy_hash(),
            version_manifest_id=core["rel"], result="PASS", reason_code=None,
            committed_at=_T0,
        )
    # G8 只写 gate_decision；历史 cohort/assignment/metric/forecast 零变化。
    assert count("gate_decisions") == before["gate_decisions"] + 1
    assert count("evaluation_cohorts") == before["cohort"]
    assert count("metric_runs") == before["metric_runs"]
    assert count("forecast_episodes") == before["forecast_episodes"]
    assert count("promotion_decisions") == before["promotion_decisions"]
    # 新 strategy 只创建未来 shadow assignment；authorized_capital 仍为 0。
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.promote(
            uow, input_=PromotionDecisionInput(
                promotion_key="pk-fe", metric_run_id=run_id, promotion_type="strategy",
                from_ref="a" * 64, to_ref="b" * 64,
                evidence_manifest_hash="d" * 64,
                status="APPROVED", future_effective_at=_T0 + timedelta(days=30),
            )
        )
    assert result.status == "APPROVED", result.reason
    cap_row = _query(env["url"], "SELECT authorized_capital FROM trading.capital_permission_manifests WHERE id=:id", {"id": core["cap"]})
    assert cap_row == [(0,)]
