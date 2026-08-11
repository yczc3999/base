"""WP-04 label 审计 + 五层评价 集成测试（真 PostgreSQL，Checkpoint C）。

用 ``temp_pg_db`` + alembic upgrade b1000040；seed 最小 contract spec / payout function /
token / forecast submission / score target / quote checkpoint / component membership 链
（``session_replication_role=replica`` 绕过深 FK 链）。通过真实 SettlementLogic /
EvaluationLogic 走业务路径（不直接批量 INSERT 冒充）。

至少证明（任务 §7）：
1. label 五态 + 冲突 + wrong payout + wrong token mapping + 证据缺失 + final update/delete
   全 fail closed（DB guard + Logic）；
2. final_excluded/disputed 不进 proper-loss；
3. bernoulli golden 数值精确；未来 baseline、token 双计被拒；
5. split crossing、holdout tamper 拒绝；
6. champion/challenger exact paired，唯一变化字段以外任何 hash 差异使实验无效；
7. full forecast-set 与 selected action-set 分开产出；
8. capital promotion 恒拒；合格 strategy promotion 只对未来 shadow assignment 生效。
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
from app.domain.trading.hashing import canonical_bytes, canonical_hash
from app.domain.trading.evaluation_policy import evaluation_policy_hash
from app.logics.trading.evaluation import (
    EvaluationLogic,
    _promotion_policy_hash,
    metric_evidence_manifest,
)
from app.logics.trading.settlement import SettlementLogic
from app.repositories.trading.evaluation import EvaluationRepository
from app.repositories.trading.settlement import SettlementRepository
from app.schemas.trading.evaluation import (
    MetricRunInput,
    PromotionDecisionInput,
    ScoreObservationInput,
)
from app.schemas.trading.settlement import LabelRevisionInput
from tests.trading.fixtures.p3_learning.p3_helpers import frozen_scenario

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V40 = "b1000040"

# 分区 lookahead 覆盖当前 UTC 日 + 7 天；用 now() 派生保证 partition 存在。
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


# ---------------- seed helpers（真实 DB，replica 绕过深 FK 链） ----------------

def _seed_core(url, suffix="e1"):
    _execute(
        url,
        "INSERT INTO trading.strategy_objective_contracts "
        "(contract_key, version_no, content, schema_version, content_hash, status) "
        "VALUES (:k, 1, '{}'::jsonb, 1, :h, 'active')",
        {"k": f"obj-{suffix}", "h": "a" * 64},
    )
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
    _execute(
        url,
        "INSERT INTO trading.policy_type_scopes (policy_type, scope_type, scope_key) "
        "VALUES ('p3_baseline', 'promotion', 'p3-evaluation-v1') "
        "ON CONFLICT (policy_type) DO NOTHING",
    )
    _execute(
        url,
        "INSERT INTO trading.policy_freezes "
        "(policy_type, scope_type, scope_key, policy_version, policy_content_hash, "
        " release_manifest_id, frozen_at, status) VALUES "
        "('p3_baseline', 'promotion', 'p3-evaluation-v1', 1, :hash, :release, :at, 'frozen') "
        "ON CONFLICT (policy_type, scope_type, scope_key, policy_version) DO NOTHING",
        {"hash": evaluation_policy_hash("baseline_convention"), "release": rel,
         "at": _T0 - timedelta(days=1)},
    )
    return {"obj": obj, "strat": strat, "rel": rel, "exec": exec_id, "cap": cap}


def _seed_contract(url, key="cs-bern", states=("YES", "NO"), payout_overrides=None):
    """contract_spec + pm_tokens + payout_functions（replica 绕过 snapshot FK）。

    ``payout_overrides``：``{state_index: {state: payout_value}}``，用于 mean-only 等
    非 0/1 兑付。
    """
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            cs = c.execute(text(
                "INSERT INTO trading.contract_specs "
                "(contract_key, version_no, snapshot_id, kc_resolution_states, token_ids, "
                " token_count, state_count, compiler_version, schema_version, status, content_hash) "
                "VALUES (:k, 1, 900001, :states, '{}'::jsonb, :tc, :sc, 'v1', 1, 'pass', :h) RETURNING id"
            ), {"k": key, "states": json.dumps(list(states)), "tc": len(states), "sc": len(states), "h": "e" * 64}).scalar_one()
            tokens: dict[str, int] = {}
            ext_tokens: dict[str, str] = {}
            market_ids: list[int] = []
            token_version_ids: list[int] = []
            for idx, st in enumerate(states):
                mid = c.execute(text(
                    "INSERT INTO trading.pm_markets (gamma_market_id, condition_id) "
                    "VALUES (:mk, :ck) RETURNING id"
                ), {"mk": f"{key}-m{idx}", "ck": f"c-{key}-{idx}"}).scalar_one()
                tok = c.execute(text(
                    "INSERT INTO trading.pm_tokens (token_id, market_id, outcome_index) "
                    "VALUES (:tk, :m, :oi) RETURNING id"
                ), {"tk": f"{key}-t{idx}", "m": mid, "oi": idx}).scalar_one()
                tv = c.execute(text(
                    "INSERT INTO trading.pm_token_versions (token_id, version_no, outcome_index, observed_at, received_at) "
                    "VALUES (:tok, 1, :oi, :at, :at) RETURNING id"
                ), {"tok": tok, "oi": idx, "at": _T0}).scalar_one()
                market_ids.append(mid)
                token_version_ids.append(tv)
                overrides = (payout_overrides or {}).get(idx)
                if overrides is not None:
                    ir = {s: str(v) for s, v in overrides.items()}
                else:
                    ir = {s: ("1" if i == idx else "0") for i, s in enumerate(states)}
                c.execute(text(
                    "INSERT INTO trading.payout_functions "
                    "(contract_spec_id, pm_token_id, token_version_id, outcome_index, function_ir, "
                    " test_vectors, algorithm_hash, content_hash) "
                    "VALUES (:cs, :tok, :tv, :oi, :ir, '{}'::jsonb, :ah, :h)"
                ), {"cs": cs, "tok": tok, "tv": tv, "oi": idx, "ir": json.dumps(ir),
                    "ah": "a" * 64, "h": "d" * 64})
                tokens[st] = tok
                ext_tokens[st] = f"{key}-t{idx}"
            # A contract snapshot always records distinct YES/NO token versions,
            # including for a scalar/mean-only evaluation target.  The auxiliary
            # book token is snapshot structure only and is intentionally not a
            # member of the scalar payout function/score target.
            if len(token_version_ids) == 1:
                aux_token = c.execute(text(
                    "INSERT INTO trading.pm_tokens (token_id, market_id, outcome_index) "
                    "VALUES (:tk, :m, 1) RETURNING id"
                ), {"tk": f"{key}-snapshot-aux", "m": market_ids[0]}).scalar_one()
                aux_version = c.execute(text(
                    "INSERT INTO trading.pm_token_versions "
                    "(token_id, version_no, outcome_index, observed_at, received_at) "
                    "VALUES (:tok, 1, 1, :at, :at) RETURNING id"
                ), {"tok": aux_token, "at": _T0}).scalar_one()
                token_version_ids.append(aux_version)
            market_version_id = c.execute(text(
                "INSERT INTO trading.pm_market_versions "
                "(market_id, version_no, resolution_source, observed_at, received_at, normalized_hash) "
                "VALUES (:m, 1, 'gamma', :at, :at, :h) RETURNING id"
            ), {"m": market_ids[0], "at": _T0, "h": "6" * 64}).scalar_one()
            artifact_id = c.execute(text(
                "INSERT INTO trading.artifact_objects "
                "(sha256, original_size, stored_size, mime, compression, storage_driver, "
                " storage_version, locator) VALUES (:h, 1, 1, 'application/json', 'none', "
                " 'local', 'cas/v1', :loc) RETURNING id"
            ), {"h": "5" * 64,
                "loc": f"cas/v1/sha256/55/55/{'5' * 64}.raw"}).scalar_one()
            snapshot_id = c.execute(text(
                "INSERT INTO trading.contract_snapshots "
                "(market_version_id, yes_token_version_id, no_token_version_id, "
                " artifact_object_id, resolution_source, content_hash) "
                "VALUES (:mv, :yes, :no, :art, 'gamma', :h) RETURNING id"
            ), {"mv": market_version_id, "yes": token_version_ids[0],
                "no": token_version_ids[-1], "art": artifact_id, "h": "4" * 64}).scalar_one()
            c.execute(text("UPDATE trading.contract_specs SET snapshot_id=:snap WHERE id=:cs"),
                      {"snap": snapshot_id, "cs": cs})
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return cs, tokens, ext_tokens
    finally:
        engine.dispose()


def _seed_component(url, contract_spec_id, h_c, key="comp-bern"):
    """forecast_components / world_schema_versions / forecast_component_versions /
    forecast_component_contract_specs（h_c 由 component 提供）。"""
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            comp = c.execute(text(
                "INSERT INTO trading.forecast_components (component_key, cost_budget) VALUES (:k, 1) RETURNING id"
            ), {"k": key}).scalar_one()
            world_states = [{"world_state_id": ws, "assignment": {}} for ws in h_c]
            ws = c.execute(text(
                "INSERT INTO trading.world_schema_versions "
                "(component_id, version_no, variables, domains, constraints, factorization, "
                " world_states, state_count, resolution_map, h_c, content_hash, schema_version, status) "
                "VALUES (:comp, 1, '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, '{}'::jsonb, "
                " :ws, :sc, '{}'::jsonb, :hc, :h, 1, 'active') RETURNING id"
            ), {"comp": comp, "ws": json.dumps(world_states), "sc": len(world_states),
                "hc": json.dumps({str(contract_spec_id): h_c}), "h": "9" * 64}).scalar_one()
            cv = c.execute(text(
                "INSERT INTO trading.forecast_component_versions "
                "(component_id, version_no, world_schema_version_id, content_hash, status) "
                "VALUES (:comp, 1, :ws, :h, 'active') RETURNING id"
            ), {"comp": comp, "ws": ws, "h": "8" * 64}).scalar_one()
            c.execute(text(
                "INSERT INTO trading.forecast_component_contract_specs "
                "(component_version_id, contract_spec_id, h_c, totality_test_hash) "
                "VALUES (:cv, :cs, :hc, :th)"
            ), {"cv": cv, "cs": contract_spec_id, "hc": json.dumps(h_c), "th": "f" * 64})
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return cv
    finally:
        engine.dispose()


def _seed_submission(url, q, *, contract_spec_id, committed_at=_T0, alg="c" * 64):
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            core = c.execute(text(
                "SELECT r.id AS release_id, r.strategy_version_id, o.id AS objective_id "
                "FROM trading.release_manifests r "
                "CROSS JOIN LATERAL (SELECT id FROM trading.strategy_objective_contracts "
                "                    ORDER BY id DESC LIMIT 1) o "
                "ORDER BY r.id DESC LIMIT 1"
            )).mappings().one()
            component_version_id = c.execute(text(
                "SELECT component_version_id "
                "FROM trading.forecast_component_contract_specs "
                "WHERE contract_spec_id=:cs ORDER BY component_version_id DESC LIMIT 1"
            ), {"cs": contract_spec_id}).scalar_one()
            baseline_hash = evaluation_policy_hash("baseline_convention")
            cohort = c.execute(text(
                "INSERT INTO trading.evaluation_cohorts "
                "(cohort_key, status, objective_contract_id, strategy_version_id, "
                " release_manifest_id, policy_hashes, seed_hash) "
                "VALUES (:key, 'DRAFT', :objective, :strategy, :release, :policies, :seed) "
                "RETURNING id"
            ), {
                "key": f"evaluation-{contract_spec_id}",
                "objective": core["objective_id"],
                "strategy": core["strategy_version_id"],
                "release": core["release_id"],
                "policies": json.dumps({"baseline_convention": baseline_hash}),
                "seed": canonical_hash({"contract_spec_id": contract_spec_id}),
            }).scalar_one()
            opportunity = c.execute(text(
                "INSERT INTO trading.decision_opportunities "
                "(opportunity_key, cohort_id, chain_type, objective_contract_id, "
                " strategy_version_id, status, disposition, triggered_at) "
                "VALUES (:key, :cohort, 'RESEARCH_EVAL', :objective, :strategy, "
                " 'ROUTED', 'completed', :at) RETURNING id"
            ), {
                "key": f"evaluation-opportunity-{contract_spec_id}",
                "cohort": cohort,
                "objective": core["objective_id"],
                "strategy": core["strategy_version_id"],
                "at": committed_at,
            }).scalar_one()
            episode = c.execute(text(
                "INSERT INTO trading.forecast_episodes "
                "(episode_key, decision_opportunity_id, component_version_id, "
                " strategy_version_id, objective_contract_id, trigger, cutoff_at, horizon, "
                " experiment_variant, status, cognition_status, forecast_committed_at) "
                "VALUES (:key, :opportunity, :component, :strategy, :objective, "
                " 'evaluation', :cutoff, 'resolution', 'champion', "
                " 'BLIND_COMMITTED', 'COMMITTED', :committed) RETURNING id"
            ), {
                "key": canonical_hash({"episode_contract_spec_id": contract_spec_id}),
                "opportunity": opportunity,
                "component": component_version_id,
                "strategy": core["strategy_version_id"],
                "objective": core["objective_id"],
                "cutoff": committed_at,
                "committed": committed_at,
            }).scalar_one()
            c.execute(text(
                "INSERT INTO trading.episode_contract_specs (episode_id, contract_spec_id) "
                "VALUES (:episode, :cs)"
            ), {"episode": episode, "cs": contract_spec_id})
            manifest = c.execute(text(
                "INSERT INTO trading.forecast_input_manifests "
                "(episode_id, manifest_key, manifest_hash, evidence_bundle_hash, "
                " contract_spec_set_hash, world_schema_hash, prior_hash, taxonomy_hash, "
                " model_binding_hash, prompt_hash, code_hash, content) "
                "VALUES (:episode, :k, :h, :h, :h, :h, :h, :h, :h, :h, :h, '{}'::jsonb) RETURNING id"
            ), {"episode": episode, "k": f"m-{episode}", "h": "0" * 64}).scalar_one()
            sid = c.execute(text(
                "INSERT INTO trading.forecast_submissions "
                "(episode_id, submission_key, status, q, u, forecast_input_manifest_id, "
                " contract_schema_prior_evidence_hash, algorithm_hash, committed_at) "
                "VALUES (:episode, :k, 'BLIND_COMMITTED', :q, :u, :m, :h, :alg, :at) RETURNING id"
            ), {"episode": episode, "k": f"sub-{episode}", "q": json.dumps(q), "u": json.dumps([q]),
                "m": manifest, "h": "0" * 64, "alg": alg, "at": committed_at}).scalar_one()
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return sid
    finally:
        engine.dispose()


def _seed_target(url, *, target_key, target_type, contract_spec_id, canonical_side=None,
                 members=None, token_ids=None, member_weights=None,
                 payout_function_id=None):
    # 不走 replica：score_target / membership 的 FK 均已存在，且 deferred 权重/双计 trigger
    # 必须在 replica 之外生效（replica 会禁用 constraint trigger）。
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as c:
            payout_rows = c.execute(text(
                "SELECT id, pm_token_id, outcome_index FROM trading.payout_functions "
                "WHERE contract_spec_id=:cs ORDER BY outcome_index"
            ), {"cs": contract_spec_id}).fetchall()
            all_tokens = [int(row[1]) for row in payout_rows]
            existing = c.execute(text(
                "SELECT c.id FROM trading.resolution_clusters c "
                "JOIN trading.resolution_cluster_memberships m "
                "  ON m.resolution_cluster_id=c.id "
                "WHERE m.contract_spec_id=:cs LIMIT 1"
            ), {"cs": contract_spec_id}).scalar_one_or_none()
            if existing is None:
                existing = c.execute(text(
                    "INSERT INTO trading.resolution_clusters "
                    "(cluster_key, cluster_version, split, time_block_start, time_block_end, "
                    " horizon, status) VALUES (:k, 1, 'train', :start, :end, 'resolution', "
                    " 'OPEN') RETURNING id"
                ), {"k": f"cluster-{contract_spec_id}", "start": _T0 - timedelta(days=1),
                    "end": _T0 + timedelta(days=1)}).scalar_one()
                for token in all_tokens:
                    c.execute(text(
                        "INSERT INTO trading.resolution_cluster_memberships "
                        "(resolution_cluster_id, contract_spec_id, token_id) "
                        "VALUES (:cluster, :cs, :token)"
                    ), {"cluster": existing, "cs": contract_spec_id, "token": token})
                # The membership guard is deferred so a transaction can build the
                # full vector atomically.  Validate it while the cluster is still
                # OPEN; freezing first would make the deferred check observe the
                # post-freeze state and correctly reject the insert.
                c.execute(text(
                    "SET CONSTRAINTS trading.trg_resolution_cluster_memberships_outcome IMMEDIATE"
                ))
                c.execute(text(
                    "UPDATE trading.resolution_clusters SET status='FROZEN' WHERE id=:cluster"
                ), {"cluster": existing})
            if payout_function_id is None and target_type in ("bernoulli", "mean_only"):
                if target_type == "bernoulli" and canonical_side == "NO" and len(payout_rows) > 1:
                    payout_function_id = int(payout_rows[1][0])
                else:
                    payout_function_id = int(payout_rows[0][0])
            payout_type = {
                "bernoulli": "binary", "multiclass": "multiclass", "mean_only": "scalar"
            }[target_type]
            tid = c.execute(text(
                "INSERT INTO trading.score_targets "
                "(target_key, target_type, contract_spec_id, resolution_cluster_id, horizon, "
                " target_weight, payout_function_id, canonical_side, members, payout_type) "
                "VALUES (:k, :tt, :cs, :cluster, 'resolution', 1, :pf, :csd, :m, :pt) RETURNING id"
            ), {"k": target_key, "tt": target_type, "cs": contract_spec_id, "pf": payout_function_id,
                "cluster": existing, "pt": payout_type, "csd": canonical_side,
                "m": json.dumps(members) if members else None}).scalar_one()
            if token_ids:
                weights = (
                    [Decimal(str(value)) for value in member_weights]
                    if member_weights is not None
                    else [Decimal("1") / Decimal(len(token_ids))] * len(token_ids)
                )
                if len(weights) != len(token_ids):
                    raise ValueError("target_member_weights_length_mismatch")
                for tok, weight in zip(token_ids, weights, strict=True):
                    c.execute(text(
                        "INSERT INTO trading.score_target_memberships (score_target_id, token_id, member_weight) "
                        "VALUES (:t, :tok, :w)"
                    ), {"t": tid, "tok": tok, "w": weight})
        return tid
    finally:
        engine.dispose()


def _seed_artifact(url, sha):
    locator = f"cas/v1/sha256/{sha[:2]}/{sha[2:4]}/{sha}.raw"
    return _insert_id(
        url,
        "INSERT INTO trading.artifact_objects "
        "(sha256, original_size, stored_size, mime, compression, storage_driver, storage_version, locator) "
        "VALUES (:sha, 1, 1, 'application/json', 'none', 'local', 'cas/v1', :loc) RETURNING id",
        {"sha": sha, "loc": locator},
    )


def _seed_quote(url, ext_token, midpoint, received_at):
    artifact = _seed_artifact(url, canonical_hash({"quote": ext_token, "at": received_at}))
    checkpoint_id = _insert_id(
        url,
        "INSERT INTO trading.pm_book_checkpoints "
        "(token_id, connection_epoch_id, source_kind, book_hash, best_bid, best_ask, "
        " raw_artifact_id, completeness, validity, received_at) "
        "VALUES (:tok, NULL, 'rest_full', :bh, :bid, :ask, :art, true, 'VALID', :at) RETURNING id",
        {"tok": ext_token, "bh": canonical_hash({"book": ext_token, "at": received_at}),
         "bid": midpoint - Decimal("0.01"), "ask": midpoint + Decimal("0.01"),
         "art": artifact, "at": received_at},
    )
    return _insert_id(
        url,
        "INSERT INTO trading.pm_quote_bindings "
        "(token_id, checkpoint_id, checkpoint_received_at, best_bid, best_ask, "
        " price_convention, as_of, received_at, staleness_policy_ref, stale_at) "
        "VALUES (:token, :checkpoint, :at, :bid, :ask, 'midpoint', :at, :at, "
        " 'p3-baseline-v1', :stale) RETURNING id",
        {"token": ext_token, "checkpoint": checkpoint_id, "at": received_at,
         "bid": midpoint - Decimal("0.01"), "ask": midpoint + Decimal("0.01"),
         "stale": received_at + timedelta(minutes=15)},
    )


def _baseline_kwargs(url, target_id):
    rows = _query(
        url,
        "SELECT b.id, tm.token_id, (b.best_bid+b.best_ask)/2 AS mid, "
        "       b.checkpoint_received_at, st.target_type "
        "FROM trading.score_targets st "
        "JOIN trading.score_target_memberships tm ON tm.score_target_id=st.id "
        "JOIN trading.pm_tokens t ON t.id=tm.token_id "
        "JOIN trading.pm_quote_bindings b ON b.token_id=t.token_id "
        "WHERE st.id=:target ORDER BY tm.token_id, b.id DESC",
        {"target": target_id},
    )
    latest: dict[int, tuple] = {}
    for row in rows:
        latest.setdefault(int(row[1]), row)
    ordered = [latest[token] for token in sorted(latest)]
    value = {str(row[1]): str(Decimal(str(row[2])).normalize()) for row in ordered}
    target_type = ordered[0][4]
    scalar = None if target_type == "multiclass" else Decimal(value[str(ordered[0][1])])
    return {
        "baseline_quote": scalar,
        "baseline_quote_binding_ids": [int(row[0]) for row in ordered],
        "baseline_value": value,
        "baseline_value_hash": canonical_hash(value),
        "baseline_checkpoint_received_at": max(row[3] for row in ordered),
        "baseline_policy_hash": evaluation_policy_hash("baseline_convention"),
    }


def _full_cashflow(tokens: dict[str, int]) -> dict[str, str]:
    return {str(tokens["YES"]): "1", str(tokens["NO"]): "0"}


def _seed_trade_decision(url, core, submission_id, decision_key):
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            decision_id = c.execute(text(
                "INSERT INTO trading.trade_decisions "
                "(decision_key, episode_id, forecast_submission_id, forecast_lease_id, "
                " objective_contract_id, strategy_version_id, release_manifest_id, "
                " execution_spec_version_id, capital_permission_manifest_id, "
                " experiment_variant, trigger_at, input_hash) "
                "VALUES (:k, 1, :sub, 1, :obj, :strat, :rel, :exec, :cap, 'champion', :at, :h) RETURNING id"
            ), {"k": decision_key, "sub": submission_id, "obj": core["obj"], "strat": core["strat"],
                "rel": core["rel"], "exec": core["exec"], "cap": core["cap"],
                "at": _T0, "h": "a" * 64}).scalar_one()
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return decision_id
    finally:
        engine.dispose()


async def _seed_label_chain(env, *, contract_spec_id, label_key, raw_outcome,
                            token_cashflow, resolution_state, resolution_source="gamma",
                            artifact_id=None, desired="final_admissible",
                            exclusion_reason=None, conflict_set=None):
    """通过 SettlementLogic.audit_label_revision 走 pending→provisional→desired 业务路径。"""
    if artifact_id is None:
        sha = canonical_hash(raw_outcome or {})
        artifact_id = _seed_artifact(env["url"], sha)
    class EvidenceStore:
        def get_bytes(self, ref, verify=True):
            payload = canonical_bytes(raw_outcome or {})
            assert canonical_hash(raw_outcome or {}) == ref.sha256
            return payload

    logic = SettlementLogic(SettlementRepository(), artifact_store=EvidenceStore())
    ids: list[int] = []
    async with UnitOfWork(env["sessions"]) as uow:
        v1 = await logic.audit_label_revision(
            uow, input_=LabelRevisionInput(
                contract_spec_id=contract_spec_id, label_key=label_key,
                state="pending", policy_code_hash=evaluation_policy_hash("label_policy"),
            )
        )
        assert v1.ok, v1.reason
        ids.append(v1.label_id)
    async with UnitOfWork(env["sessions"]) as uow:
        v2 = await logic.audit_label_revision(
            uow, input_=LabelRevisionInput(
                contract_spec_id=contract_spec_id, label_key=label_key,
                state="provisional", policy_code_hash=evaluation_policy_hash("label_policy"),
                supersedes_id=ids[0],
            )
        )
        assert v2.ok, v2.reason
        ids.append(v2.label_id)
    async with UnitOfWork(env["sessions"]) as uow:
        v3 = await logic.audit_label_revision(
            uow, input_=LabelRevisionInput(
                contract_spec_id=contract_spec_id, label_key=label_key,
                state=desired, resolution_state=resolution_state,
                resolution_source=resolution_source, evidence_artifact_id=artifact_id,
                raw_outcome=raw_outcome, token_cashflow=token_cashflow,
                policy_code_hash=evaluation_policy_hash("label_policy"), supersedes_id=ids[1],
                auditor_identity="pytest", exclusion_reason=exclusion_reason,
                conflict_set=conflict_set,
            )
        )
        assert v3.ok, v3.reason
        ids.append(v3.label_id)
    return ids[-1], artifact_id


@pytest_asyncio.fixture
async def eval_env(temp_pg_db):
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


# ---------------- 1) label 五态 / 冲突 / 证据 / immutable ----------------

@pytest.mark.asyncio
async def test_label_five_states_and_conflict_fail_closed(eval_env):
    env = eval_env
    _seed_core(env["url"], "l1")
    cs, tokens, ext = _seed_contract(env["url"], "cs-l1")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    raw = {"resolution": "YES", "source": "platform_resolution_event"}
    label_id, artifact_id = await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-a",
        raw_outcome=raw, token_cashflow=_full_cashflow(tokens), resolution_state="YES",
    )
    row = _query(env["url"], "SELECT state, resolution_state, version_no FROM trading.resolution_labels WHERE id=:id", {"id": label_id})
    assert row == [("final_admissible", "YES", 3)]
    assert _query(env["url"], "SELECT evidence_artifact_id FROM trading.resolution_labels WHERE id=:id", {"id": label_id}) == [(artifact_id,)]


@pytest.mark.asyncio
async def test_label_conflict_forces_disputed(eval_env):
    env = eval_env
    _seed_core(env["url"], "l2")
    cs, tokens, ext = _seed_contract(env["url"], "cs-l2")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    raw = {"resolution": "YES"}
    cashflow = {str(tokens["YES"]): "0.00", str(tokens["NO"]): "1"}
    label_id, _ = await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-conf",
        raw_outcome=raw, token_cashflow=cashflow, resolution_state="YES",
    )
    state, conflict = _query(env["url"], "SELECT state, conflict_set::text FROM trading.resolution_labels WHERE id=:id", {"id": label_id})[0]
    assert state == "disputed"
    assert "token_mapping" in conflict


@pytest.mark.asyncio
async def test_wrong_payout_and_wrong_token_mapping_force_disputed(eval_env):
    env = eval_env
    _seed_core(env["url"], "l3")
    cs, tokens, ext = _seed_contract(env["url"], "cs-l3")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    # wrong payout：token_cashflow 匹配冻结 IR，但 raw_outcome.actual_cashflow 冲突。
    raw = {"resolution": "YES", "actual_cashflow": {str(tokens["YES"]): "0"}}
    label_id, _ = await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-pay",
        raw_outcome=raw, token_cashflow=_full_cashflow(tokens), resolution_state="YES",
    )
    state, conflict = _query(env["url"], "SELECT state, conflict_set::text FROM trading.resolution_labels WHERE id=:id", {"id": label_id})[0]
    assert state == "disputed"
    assert "cashflow" in conflict

    # wrong token mapping：cashflow token 集与冻结 payout 集不符。
    raw2 = {"resolution": "YES"}
    label_id2, _ = await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-map",
        raw_outcome=raw2, token_cashflow={str(tokens["NO"]): "1"}, resolution_state="YES",
    )
    state2, conflict2 = _query(env["url"], "SELECT state, conflict_set::text FROM trading.resolution_labels WHERE id=:id", {"id": label_id2})[0]
    assert state2 == "disputed"
    assert "token_mapping" in conflict2


@pytest.mark.asyncio
async def test_missing_evidence_keeps_pending(eval_env):
    env = eval_env
    _seed_core(env["url"], "l4")
    cs, tokens, ext = _seed_contract(env["url"], "cs-l4")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    logic = SettlementLogic(SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        v1 = await logic.audit_label_revision(
            uow, input_=LabelRevisionInput(
                contract_spec_id=cs, label_key="lk-ev",
                state="pending", policy_code_hash=evaluation_policy_hash("label_policy"),
            )
        )
        assert v1.ok, v1.reason
    async with UnitOfWork(env["sessions"]) as uow:
        v2 = await logic.audit_label_revision(
            uow, input_=LabelRevisionInput(
                contract_spec_id=cs, label_key="lk-ev",
                state="provisional", policy_code_hash=evaluation_policy_hash("label_policy"),
                supersedes_id=v1.label_id,
            )
        )
        assert v2.ok, v2.reason
    async with UnitOfWork(env["sessions"]) as uow:
        v3 = await logic.audit_label_revision(
            uow, input_=LabelRevisionInput(
                contract_spec_id=cs, label_key="lk-ev",
                state="final_admissible", resolution_state="YES",
                policy_code_hash=evaluation_policy_hash("label_policy"),
                supersedes_id=v2.label_id, auditor_identity="pytest",
            )
        )
        assert v3.ok is False
        assert v3.reason == "label_evidence_missing"
    count = _query(env["url"], "SELECT count(*) FROM trading.resolution_labels WHERE contract_spec_id=:cs AND label_key='lk-ev'", {"cs": cs})
    assert count == [(2,)]
    current = _query(env["url"], "SELECT state FROM trading.resolution_labels WHERE id=:id", {"id": v2.label_id})
    assert current == [("provisional",)]


@pytest.mark.asyncio
async def test_label_final_update_delete_fail_closed(eval_env):
    env = eval_env
    _seed_core(env["url"], "l5")
    cs, tokens, ext = _seed_contract(env["url"], "cs-l5")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    label_id, _ = await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-imm",
        raw_outcome={"r": "YES"}, token_cashflow=_full_cashflow(tokens), resolution_state="YES",
    )
    with pytest.raises(Exception, match="v2_immutable_row:resolution_labels"):
        _execute(env["url"], "UPDATE trading.resolution_labels SET state='pending' WHERE id=:id", {"id": label_id})
    with pytest.raises(Exception, match="v2_immutable_row:resolution_labels"):
        _execute(env["url"], "DELETE FROM trading.resolution_labels WHERE id=:id", {"id": label_id})


# ---------------- 2) proper-loss 准入 ----------------

@pytest.mark.asyncio
async def test_final_excluded_and_disputed_not_in_proper_loss(eval_env):
    env = eval_env
    _seed_core(env["url"], "l6")
    cs, tokens, ext = _seed_contract(env["url"], "cs-l6")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    _seed_submission(env["url"], {"w0": "0.7", "w1": "0.3"}, contract_spec_id=cs)
    tid = _seed_target(env["url"], target_key="t6", target_type="bernoulli",
                       contract_spec_id=cs, canonical_side="YES", token_ids=[tokens["YES"]])
    _seed_quote(env["url"], ext["YES"], Decimal("0.65"), _T0)
    disputed_id, _ = await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-dis",
        raw_outcome={"r": "YES"}, token_cashflow={str(tokens["NO"]): "1"}, resolution_state="YES",
    )
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.score_observation(
            uow, input_=ScoreObservationInput(
                observation_key="o-dis", score_target_id=tid, submission_id=1,
                label_version_id=disputed_id, **_baseline_kwargs(env["url"], tid), split="train", algorithm_hash="c" * 64,
                metric_id="bernoulli_brier", score_value=Decimal("0.09"),
            )
        )
    assert result.ok is False
    assert result.reason == "score_label_not_admissible"


# ---------------- 3) golden 数值 / 未来 baseline / token 双计 ----------------

@pytest.mark.asyncio
async def test_golden_bernoulli_metric(eval_env):
    env = eval_env
    _seed_core(env["url"], "g1")
    scenario = frozen_scenario("bernoulli")
    cs, tokens, ext = _seed_contract(env["url"], "cs-g1")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    sid = _seed_submission(env["url"], {"w0": "0.7", "w1": "0.3"}, contract_spec_id=cs)
    tid = _seed_target(env["url"], target_key="t-g1", target_type="bernoulli",
                       contract_spec_id=cs, canonical_side="YES", token_ids=[tokens["YES"]])
    _seed_quote(env["url"], ext["YES"], Decimal(scenario["market_baseline"]["baseline_price_yes"]), _T0)
    raw = {"resolution": "YES"}
    label_id, _ = await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-g1",
        raw_outcome=raw, token_cashflow=_full_cashflow(tokens), resolution_state="YES",
    )
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        brier = await logic.score_observation(
            uow, input_=ScoreObservationInput(
                observation_key="o-g1-brier", score_target_id=tid, submission_id=sid,
                label_version_id=label_id, **_baseline_kwargs(env["url"], tid), split="train", algorithm_hash="c" * 64,
                metric_id="bernoulli_brier", score_value=Decimal(scenario["golden"]["brier"]),
            )
        )
        logloss = await logic.score_observation(
            uow, input_=ScoreObservationInput(
                observation_key="o-g1-ll", score_target_id=tid, submission_id=sid,
                label_version_id=label_id, **_baseline_kwargs(env["url"], tid), split="train", algorithm_hash="c" * 64,
                metric_id="bernoulli_log_loss", score_value=Decimal(scenario["golden"]["log_loss"]),
            )
        )
    assert brier.ok, brier.reason
    assert logloss.ok, logloss.reason
    rows = _query(env["url"], "SELECT metric_id, score_value FROM trading.score_observations WHERE observation_key IN ('o-g1-brier','o-g1-ll') ORDER BY metric_id")
    assert Decimal(str(rows[0][1])) == Decimal(scenario["golden"]["brier"])
    assert Decimal(str(rows[1][1])) == Decimal(scenario["golden"]["log_loss"])


@pytest.mark.asyncio
async def test_golden_multiclass_metric(eval_env):
    env = eval_env
    _seed_core(env["url"], "gmc")
    scenario = frozen_scenario("multiclass")
    # pm_tokens outcome_index 约束为 binary（0/1），所以用 2-state 版本；log loss 只依赖
    # 获胜概率 0.6，与 fixture golden 一致。
    cs, tokens, ext = _seed_contract(env["url"], "cs-gmc", states=("A", "B"))
    _seed_component(env["url"], cs, {"w0": "A", "w1": "B"}, key="comp-gmc")
    sid = _seed_submission(env["url"], {"w0": "0.6", "w1": "0.4"}, contract_spec_id=cs)
    tid = _seed_target(env["url"], target_key="t-gmc", target_type="multiclass",
                       contract_spec_id=cs, members=["A", "B"],
                       token_ids=[tokens["A"], tokens["B"]])
    _seed_quote(env["url"], ext["A"], Decimal("0.60"), _T0)
    _seed_quote(env["url"], ext["B"], Decimal("0.40"), _T0)
    raw = {"resolution": "A"}
    label_id, _ = await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-gmc",
        raw_outcome=raw, token_cashflow={str(tokens["A"]): "1", str(tokens["B"]): "0"},
        resolution_state="A",
    )
    # 2-state multiclass brier = (0.6-1)^2 + (0.4-0)^2 = 0.16 + 0.16 = 0.32。
    golden_brier = Decimal("0.32")
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        brier = await logic.score_observation(
            uow, input_=ScoreObservationInput(
                observation_key="o-gmc-brier", score_target_id=tid, submission_id=sid,
                label_version_id=label_id, **_baseline_kwargs(env["url"], tid), split="train", algorithm_hash="c" * 64,
                metric_id="multiclass_brier", score_value=golden_brier,
            )
        )
        logloss = await logic.score_observation(
            uow, input_=ScoreObservationInput(
                observation_key="o-gmc-ll", score_target_id=tid, submission_id=sid,
                label_version_id=label_id, **_baseline_kwargs(env["url"], tid), split="train", algorithm_hash="c" * 64,
                metric_id="multiclass_log_loss", score_value=Decimal(scenario["golden"]["multiclass_log_loss"]),
            )
        )
    assert brier.ok, brier.reason
    assert logloss.ok, logloss.reason
    rows = _query(env["url"], "SELECT metric_id, score_value FROM trading.score_observations WHERE observation_key IN ('o-gmc-brier','o-gmc-ll') ORDER BY metric_id")
    assert Decimal(str(rows[0][1])) == golden_brier
    assert Decimal(str(rows[1][1])) == Decimal(scenario["golden"]["multiclass_log_loss"])


@pytest.mark.asyncio
async def test_golden_mean_only_metric(eval_env):
    env = eval_env
    _seed_core(env["url"], "gmo")
    scenario = frozen_scenario("mean_only")
    # mean-only：单 state，兑付 0.9；predicted = 0.8 * 0.9 = 0.72。
    cs, tokens, ext = _seed_contract(env["url"], "cs-gmo", states=("MEAN",),
                                     payout_overrides={0: {"MEAN": "0.9"}})
    _seed_component(env["url"], cs, {"w0": "MEAN"}, key="comp-gmo")
    sid = _seed_submission(env["url"], {"w0": "0.8"}, contract_spec_id=cs)
    tid = _seed_target(env["url"], target_key="t-gmo", target_type="mean_only",
                       contract_spec_id=cs, token_ids=[tokens["MEAN"]])
    _seed_quote(env["url"], ext["MEAN"], Decimal("0.60"), _T0)
    raw = {"resolution": "MEAN", "actual_mean": "0.60"}
    label_id, _ = await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-gmo",
        raw_outcome=raw, token_cashflow={str(tokens["MEAN"]): "0.9"},
        resolution_state="MEAN",
    )
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        mse = await logic.score_observation(
            uow, input_=ScoreObservationInput(
                observation_key="o-gmo-mse", score_target_id=tid, submission_id=sid,
                label_version_id=label_id, **_baseline_kwargs(env["url"], tid), split="train", algorithm_hash="c" * 64,
                metric_id="mean_squared_payout_loss",
                score_value=Decimal(scenario["golden"]["mean_squared_payout_loss"]),
            )
        )
    assert mse.ok, mse.reason
    row = _query(env["url"], "SELECT score_value FROM trading.score_observations WHERE observation_key='o-gmo-mse'")
    assert Decimal(str(row[0][0])) == Decimal(scenario["golden"]["mean_squared_payout_loss"])


@pytest.mark.asyncio
async def test_cluster_weight_inflation_rejected(eval_env):
    env = eval_env
    _seed_core(env["url"], "gwi")
    cs, tokens, ext = _seed_contract(env["url"], "cs-gwi")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    # 两笔 membership 权重和 >1 → 权重放大被拒（deferred trigger）。
    with pytest.raises(Exception, match="v2_target_membership_weight_not_one"):
        _seed_target(
            env["url"], target_key="t-gwi", target_type="bernoulli",
            contract_spec_id=cs, canonical_side="YES",
            token_ids=[tokens["YES"], tokens["NO"]], member_weights=[1, 1],
        )


@pytest.mark.asyncio
async def test_future_baseline_rejected(eval_env):
    env = eval_env
    _seed_core(env["url"], "g2")
    cs, tokens, ext = _seed_contract(env["url"], "cs-g2")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    sid = _seed_submission(env["url"], {"w0": "0.7", "w1": "0.3"}, contract_spec_id=cs)
    tid = _seed_target(env["url"], target_key="t-g2", target_type="bernoulli",
                       contract_spec_id=cs, canonical_side="YES", token_ids=[tokens["YES"]])
    # 只存在未来 quote（received_at > committed_at）→ 显式 excluded。
    _seed_quote(env["url"], ext["YES"], Decimal("0.65"), _T0 + timedelta(hours=1))
    label_id, _ = await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-g2",
        raw_outcome={"r": "YES"}, token_cashflow=_full_cashflow(tokens), resolution_state="YES",
    )
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.score_observation(
            uow, input_=ScoreObservationInput(
                observation_key="o-g2", score_target_id=tid, submission_id=sid,
                label_version_id=label_id, **_baseline_kwargs(env["url"], tid), split="train", algorithm_hash="c" * 64,
                metric_id="bernoulli_brier", score_value=Decimal("0.09"),
            )
        )
    assert result.ok is False
    assert result.reason == "score_baseline_missing"
    assert result.state == "excluded"


@pytest.mark.asyncio
async def test_run_metric_persists_full_canonical_evidence_manifest(eval_env):
    env = eval_env
    core = _seed_core(env["url"], "metric-real")
    cs, tokens, ext = _seed_contract(env["url"], "cs-metric-real")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    sid = _seed_submission(
        env["url"], {"w0": "0.7", "w1": "0.3"}, contract_spec_id=cs
    )
    target_id = _seed_target(
        env["url"], target_key="t-metric-real", target_type="bernoulli",
        contract_spec_id=cs, canonical_side="YES", token_ids=[tokens["YES"]],
    )
    _seed_quote(env["url"], ext["YES"], Decimal("0.65"), _T0)
    label_id, _ = await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-metric-real",
        raw_outcome={"resolution": "YES"}, token_cashflow=_full_cashflow(tokens),
        resolution_state="YES",
    )
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        score = await logic.score_observation(
            uow,
            input_=ScoreObservationInput(
                observation_key="o-metric-real", score_target_id=target_id,
                submission_id=sid, label_version_id=label_id,
                **_baseline_kwargs(env["url"], target_id), split="train",
                algorithm_hash="c" * 64, metric_id="bernoulli_brier",
                score_value=Decimal("0.09"),
            ),
        )
    assert score.ok and score.observation_id is not None
    cohort_id = _query(
        env["url"],
        "SELECT dop.cohort_id FROM trading.forecast_submissions fs "
        "JOIN trading.forecast_episodes fe ON fe.id=fs.episode_id "
        "JOIN trading.decision_opportunities dop ON dop.id=fe.decision_opportunity_id "
        "WHERE fs.id=:submission",
        {"submission": sid},
    )[0][0]
    observation_ids = [score.observation_id]
    label_versions = {"final": [label_id]}
    time_blocks = {"resolution": _T0.date().isoformat()}
    observation_set_hash = canonical_hash({
        "cohort_id": cohort_id,
        "split": "train",
        "ordered_observation_ids": observation_ids,
        "label_versions": label_versions,
        "time_blocks": time_blocks,
        "strategy_version_id": core["strat"],
        "release_manifest_id": core["rel"],
    })
    metric_input = MetricRunInput(
        run_key="run-metric-real", cohort_id=cohort_id,
        observation_ids=observation_ids, observation_set_hash=observation_set_hash,
        cohort_query_hash="a" * 64, strategy_version_id=core["strat"],
        release_manifest_id=core["rel"], label_versions=label_versions,
        split="train", time_blocks=time_blocks, code_hash="b" * 64,
        config_hash="c" * 64, seed=42, n_market=1, n_episode=1,
        n_resolution_cluster=1, n_eff=Decimal("1"), results={}, ci={},
        artifact_hash="0" * 64,
    )
    async with UnitOfWork(env["sessions"]) as uow:
        run = await logic.run_metric(uow, input_=metric_input)
    assert run.ok, run.reason
    stored = _query(
        env["url"],
        "SELECT observation_ids, observation_set_hash, n_market, n_episode, "
        "n_resolution_cluster, n_eff, results, ci, artifact_hash, status "
        "FROM trading.metric_runs WHERE id=:id",
        {"id": run.metric_run_id},
    )[0]
    manifest = metric_evidence_manifest(
        metric_input,
        observation_ids=[int(value) for value in stored[0]],
        observation_set_hash=stored[1],
        n_market=stored[2], n_episode=stored[3],
        n_resolution_cluster=stored[4], n_eff=Decimal(str(stored[5])),
        results=stored[6], ci=stored[7],
    )
    assert stored[8] == canonical_hash(manifest)
    assert stored[9] == "COMPLETED"


@pytest.mark.asyncio
async def test_token_double_count_rejected(eval_env):
    env = eval_env
    _seed_core(env["url"], "g3")
    cs, tokens, ext = _seed_contract(env["url"], "cs-g3")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    _seed_target(env["url"], target_key="t-g3a", target_type="bernoulli",
                 contract_spec_id=cs, canonical_side="YES", token_ids=[tokens["YES"]])
    with pytest.raises(Exception, match="v2_target_membership_token_double_counted"):
        _seed_target(env["url"], target_key="t-g3b", target_type="bernoulli",
                     contract_spec_id=cs, canonical_side="NO", token_ids=[tokens["YES"]])


# ---------------- 5) split crossing / holdout tamper ----------------

@pytest.mark.asyncio
async def test_cluster_split_crossing_rejected(eval_env):
    env = eval_env
    _seed_core(env["url"], "s1")
    cs, tokens, ext = _seed_contract(env["url"], "cs-s1")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    logic = SettlementLogic(SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        c1 = await logic.create_cluster(
            uow, split="train", time_block_start=_T0, time_block_end=_T0 + timedelta(days=1),
            horizon="resolution", contract_spec_ids=[cs], token_ids=[tokens["YES"], tokens["NO"]],
        )
        assert c1.ok, c1.reason
    async with UnitOfWork(env["sessions"]) as uow:
        c2 = await logic.create_cluster(
            uow, split="validation", time_block_start=_T0 + timedelta(days=1),
            time_block_end=_T0 + timedelta(days=2), horizon="resolution",
            contract_spec_ids=[cs], token_ids=[tokens["YES"], tokens["NO"]],
        )
        assert c2.ok is False
        assert c2.reason == f"cluster_contract_already_active:{cs}"


@pytest.mark.asyncio
async def test_holdout_resolution_after_assignment_is_not_tamper(eval_env):
    env = eval_env
    _seed_core(env["url"], "s2")
    cs, tokens, ext = _seed_contract(env["url"], "cs-s2")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    # Build the assignment while OPEN, force the deferred membership guard, then
    # freeze it in the same transaction.  This is the production lifecycle.
    engine = create_engine(env["url"], poolclass=NullPool)
    try:
        with engine.begin() as c:
            cluster_id = c.execute(text(
                "INSERT INTO trading.resolution_clusters "
                "(cluster_key, cluster_version, split, time_block_start, "
                " time_block_end, horizon, status) "
                "VALUES (:k, 1, 'forward_holdout', :tbs, :tbe, 'resolution', 'OPEN') "
                "RETURNING id"
            ), {"k": "ho-1", "tbs": _T0,
                "tbe": _T0 + timedelta(days=1)}).scalar_one()
            c.execute(text(
                "INSERT INTO trading.resolution_cluster_memberships "
                "(resolution_cluster_id, contract_spec_id, token_id) "
                "VALUES (:c, :cs, :t1), (:c, :cs, :t2)"
            ), {"c": cluster_id, "cs": cs,
                "t1": tokens["YES"], "t2": tokens["NO"]})
            c.execute(text(
                "SET CONSTRAINTS trading.trg_resolution_cluster_memberships_outcome IMMEDIATE"
            ))
            c.execute(text(
                "UPDATE trading.resolution_clusters SET status='FROZEN' WHERE id=:id"
            ), {"id": cluster_id})
    finally:
        engine.dispose()
    # FROZEN pre-outcome assignment之后正常结算，不是 holdout tamper。
    await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-ho",
        raw_outcome={"r": "YES"}, token_cashflow=_full_cashflow(tokens), resolution_state="YES",
    )
    logic = SettlementLogic(SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        integrity = await logic.check_split_integrity(uow)
        assert integrity.ok is True, integrity.reason


# ---------------- 6) champion / challenger ----------------

@pytest.mark.asyncio
async def test_champion_challenger_exact_paired(eval_env):
    env = eval_env
    core = _seed_core(env["url"], "x1")
    experiment = _insert_id(
        env["url"],
        "INSERT INTO trading.experiments "
        "(experiment_key, hypothesis, hypothesis_hash, primary_metric, guardrails, "
        " unique_change_field, champion_input_manifest_hash, challenger_input_manifest_hash, "
        " sample_policy, stopping_rule, seed, time_block_start, time_block_end, status) "
        "VALUES (:k, 'h', :hh, 'bernoulli_brier', '{}'::jsonb, 'strategy_version_id', "
        " :ch, :cl, '{}'::jsonb, '{}'::jsonb, 42, :tbs, :tbe, 'PLANNED') RETURNING id",
        {"k": "exp-x1", "hh": "a" * 64, "ch": "a" * 64, "cl": "b" * 64,
         "tbs": _T0, "tbe": _T0 + timedelta(days=1)},
    )
    _execute(env["url"], "INSERT INTO trading.experiment_variants (experiment_id, variant_key, variant_type, input_manifest_hash, strategy_version_id, release_manifest_id) VALUES (:e, 'champ', 'champion', :h, :s, :r)", {"e": experiment, "h": "a" * 64, "s": core["strat"], "r": core["rel"]})
    _execute(env["url"], "INSERT INTO trading.experiment_variants (experiment_id, variant_key, variant_type, input_manifest_hash, strategy_version_id, release_manifest_id) VALUES (:e, 'chal', 'challenger', :h, :s, :r)", {"e": experiment, "h": "b" * 64, "s": core["strat"], "r": core["rel"]})
    _execute(env["url"], "INSERT INTO trading.challenger_variants (experiment_id, variant_key, challenger_type, changed_fields, policy_hash) VALUES (:e, 'chal', 'strategy', :cf, :ph)", {"e": experiment, "cf": json.dumps({"strategy_version_id": "x", "objective_id": "y"}), "ph": "c" * 64})
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        result = await logic.champion_challenger_pair(uow, experiment_key="exp-x1")
    assert result.ok is True
    assert result.status == "INVALIDATED"
    assert result.reason == "experiment_multiple_factors_changed"


# ---------------- 7) full vs selected ----------------

@pytest.mark.asyncio
async def test_full_forecast_and_selected_action_sets_separate(eval_env):
    env = eval_env
    core = _seed_core(env["url"], "r1")
    cs, tokens, ext = _seed_contract(env["url"], "cs-r1")
    _seed_component(env["url"], cs, {"w0": "YES", "w1": "NO"})
    sid = _seed_submission(env["url"], {"w0": "0.7", "w1": "0.3"}, contract_spec_id=cs)
    tid = _seed_target(env["url"], target_key="t-r1", target_type="bernoulli",
                       contract_spec_id=cs, canonical_side="YES", token_ids=[tokens["YES"]])
    _seed_quote(env["url"], ext["YES"], Decimal("0.65"), _T0)
    decision_id = _seed_trade_decision(env["url"], core, sid, canonical_hash({"d": "r1"}))
    yes_id, _ = await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-r0",
        raw_outcome={"r": "YES", "seq": 0},
        token_cashflow=_full_cashflow(tokens), resolution_state="YES",
    )
    no_id, _ = await _seed_label_chain(
        env, contract_spec_id=cs, label_key="lk-r1",
        raw_outcome={"r": "NO", "seq": 1},
        token_cashflow={str(tokens["YES"]): "0", str(tokens["NO"]): "1"},
        resolution_state="NO",
    )
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        s1 = await logic.score_observation(
            uow, input_=ScoreObservationInput(
                observation_key="o-r1", score_target_id=tid, submission_id=sid,
                trade_decision_id=decision_id, label_version_id=yes_id,
                **_baseline_kwargs(env["url"], tid),
                split="train", algorithm_hash="c" * 64,
                metric_id="bernoulli_brier", score_value=Decimal("0.09"),
            )
        )
        s2 = await logic.score_observation(
            uow, input_=ScoreObservationInput(
                observation_key="o-r2", score_target_id=tid, submission_id=sid,
                label_version_id=no_id, **_baseline_kwargs(env["url"], tid), split="train", algorithm_hash="c" * 64,
                metric_id="bernoulli_brier", score_value=Decimal("0.49"),
            )
        )
        assert s1.ok and s2.ok
        guard = await logic.score_observation_guardrails(uow, metric_id="bernoulli_brier")
    assert set(guard.keys()) == {"full_forecast_set", "selected_action_set"}
    assert guard["full_forecast_set"]["count"] == 2
    assert guard["selected_action_set"]["count"] == 1


# ---------------- 8) promotion ----------------

@pytest.mark.asyncio
async def test_capital_promotion_always_rejected_and_strategy_future_shadow(eval_env):
    env = eval_env
    core = _seed_core(env["url"], "p1")
    # Promotion consumes an immutable completed-run prerequisite.  Seed that
    # prerequisite under replica mode; metric evidence itself is exercised by
    # the metric/replay tests and remains guarded in production.
    passing_results = {
        layer: {"hard_guardrail_pass": True}
        for layer in ("prediction", "selection", "edge", "execution")
    }
    passing_results["portfolio"] = {
        "hard_guardrail_pass": True,
        "not_evaluable": False,
        "system_net": "1",
    }
    engine = create_engine(env["url"], poolclass=NullPool)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            run_id = c.execute(text(
                "INSERT INTO trading.metric_runs "
                "(run_key, cohort_id, observation_ids, observation_set_hash, "
                " cohort_query_hash, strategy_version_id, release_manifest_id, label_versions, "
                " split, time_blocks, code_hash, config_hash, seed, n_market, n_episode, "
                " n_resolution_cluster, n_eff, results, ci, artifact_hash, status, completed_at) "
                "VALUES ('run-p1', 900001, '[900001]'::jsonb, :osh, :cqh, :sv, :rm, "
                " '{}'::jsonb, 'forward_holdout', :time_blocks, :ch, :cgh, 1, 1, 2, "
                " 2, 2.0, :results, '{}'::jsonb, :ah, 'COMPLETED', :completed) RETURNING id"
            ), {"osh": "e" * 64, "cqh": "a" * 64, "sv": core["strat"],
                "rm": core["rel"], "ch": "b" * 64, "cgh": "c" * 64,
                "time_blocks": json.dumps({"resolution": _T0.date().isoformat()}),
                "results": json.dumps(passing_results), "ah": "d" * 64,
                "completed": _T0}).scalar_one()
            experiment = c.execute(text(
                "INSERT INTO trading.experiments "
                "(experiment_key,hypothesis,hypothesis_hash,primary_metric,guardrails,"
                " unique_change_field,champion_input_manifest_hash,"
                " challenger_input_manifest_hash,sample_policy,stopping_rule,seed,"
                " status,time_block_start,time_block_end) VALUES "
                "('promo-exp','paired','a'::text || repeat('a',63),'bernoulli_brier',"
                " '{}'::jsonb,'strategy_version_id',:from_ref,:to_ref,"
                " '{\"split\":\"forward_holdout\",\"min_n_eff\":\"2\"}'::jsonb,"
                " '{\"min_n_eff\":\"2\"}'::jsonb,1,'COMPLETED',:start,:end) "
                "RETURNING id"
            ), {"from_ref": "d" * 64, "to_ref": "e" * 64,
                "start": _T0 - timedelta(days=1), "end": _T0 + timedelta(days=1)}).scalar_one()
            c.execute(text(
                "INSERT INTO trading.experiment_variants "
                "(experiment_id,variant_key,variant_type,input_manifest_hash,"
                " strategy_version_id,release_manifest_id) VALUES "
                "(:e,'champ','champion',:from_ref,:sv,:rm),"
                "(:e,'chal','challenger',:to_ref,:sv,:rm)"
            ), {"e": experiment, "from_ref": "d" * 64, "to_ref": "e" * 64,
                "sv": core["strat"], "rm": core["rel"]})
            c.execute(text(
                "INSERT INTO trading.challenger_variants "
                "(experiment_id,variant_key,challenger_type,changed_fields,policy_hash,status) "
                "VALUES (:e,'chal','strategy','{\"strategy_version_id\":\"next\"}'::jsonb,"
                " :policy,'ACTIVE')"
            ), {"e": experiment, "policy": "9" * 64})
            promotion_hash = _promotion_policy_hash()
            c.execute(text(
                "INSERT INTO trading.gate_decisions "
                "(gate, target_kind, target_id, input_hash, policy_hash, "
                " version_manifest_id, result, reason_code, committed_at) "
                "VALUES ('G8', 'metric_run', :run, :input, :policy, :release, "
                " 'PASS', NULL, :committed)"
            ), {"run": run_id, "input": "f" * 64, "policy": promotion_hash,
                "release": core["rel"], "committed": _T0})
            c.execute(text("SET LOCAL session_replication_role = origin"))
    finally:
        engine.dispose()
    logic = EvaluationLogic(EvaluationRepository(), SettlementRepository())
    async with UnitOfWork(env["sessions"]) as uow:
        cap = await logic.promote(
            uow, input_=PromotionDecisionInput(
                promotion_key="pk-cap", metric_run_id=run_id, promotion_type="capital",
                from_ref="a" * 64, to_ref="b" * 64, evidence_manifest_hash="c" * 64,
                status="APPROVED", capital_amount=Decimal("0"),
            )
        )
        assert cap.status == "REJECTED"
        assert cap.reason == "capital_promotion_fail_closed"
    future = _T0 + timedelta(days=30)
    async with UnitOfWork(env["sessions"]) as uow:
        strat = await logic.promote(
            uow, input_=PromotionDecisionInput(
                promotion_key="pk-strat", metric_run_id=run_id, promotion_type="strategy",
                from_ref="d" * 64, to_ref="e" * 64,
                evidence_manifest_hash="d" * 64,
                status="APPROVED", future_effective_at=future,
            )
        )
    assert strat.status == "APPROVED", strat.reason
    rows = _query(env["url"], "SELECT status, future_effective_at IS NOT NULL FROM trading.promotion_decisions WHERE promotion_key='pk-strat'")
    assert rows == [("APPROVED", True)]
    cap_row = _query(env["url"], "SELECT authorized_capital FROM trading.capital_permission_manifests WHERE id=:id", {"id": core["cap"]})
    assert cap_row == [(0,)]
