"""
WP-02 v2_0020 cognition migration —— 真 PostgreSQL 集成验收（Checkpoint A §8.1）。

覆盖：11 新表 roundtrip、forecast_episodes 强化（cognition 列 + BLIND_COMMITTED +
lifecycle guard 推进）、gate_decisions/information_snapshots allowlist 扩展、episode spec-set
BLIND_COMMITTED 也要求 R1、submission immutable guard、downgrade 恢复 b1000013 guard。
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V13 = "b1000013"
V20 = "b1000020"

COGNITION_TABLES = [
    "priors", "evidence_coverage_policies", "evidence_revisions", "evidence_bundles",
    "evidence_bundle_items", "forecast_input_manifests", "forecast_submissions",
    "payout_projections", "coherence_checks", "forecast_challenges", "forecast_leases",
]

REQUIRED_POLICIES = (
    "eligibility", "taxonomy", "horizon", "r0", "r1", "evidence_coverage",
    "shrinkage", "baseline_scoring", "split_inference", "reject_audit",
)


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


def _query(db_url, sql, params=None):
    engine = create_engine(db_url)
    try:
        with engine.connect() as c:
            return c.execute(text(sql), params or {}).fetchall()
    finally:
        engine.dispose()


def _execute(db_url, sql, params=None):
    engine = create_engine(db_url)
    try:
        with engine.connect() as c:
            c.execute(text(sql), params or {})
            c.commit()
    finally:
        engine.dispose()


def _exec_multi(db_url, statements):
    engine = create_engine(db_url)
    try:
        with engine.connect() as c:
            for stmt in statements:
                c.execute(text(stmt[0]), stmt[1])
            c.commit()
    finally:
        engine.dispose()


def _version(db_url):
    return _query(db_url, "SELECT version_num FROM public.alembic_version")


def _trading_tables(db_url):
    return [r[0] for r in _query(
        db_url, "SELECT tablename FROM pg_tables WHERE schemaname='trading' ORDER BY 1"
    )]


def _seed_core(url):
    _execute(url, "INSERT INTO trading.strategy_objective_contracts (contract_key, version_no, content, schema_version, content_hash, status) VALUES ('obj-2', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "a" * 64})
    obj_id = _query(url, "SELECT id FROM trading.strategy_objective_contracts WHERE contract_key='obj-2'")[0][0]
    _execute(url, "INSERT INTO trading.strategy_versions (strategy_key, version_no, content, schema_version, content_hash, status) VALUES ('strat-2', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "b" * 64})
    strat_id = _query(url, "SELECT id FROM trading.strategy_versions WHERE strategy_key='strat-2'")[0][0]
    _execute(url, "INSERT INTO trading.capital_permission_manifests (name, mode, capability, limits, evaluation_capital, authorized_capital, content_hash, status) VALUES ('perm-2', 'shadow', '{}'::jsonb, '{}'::jsonb, 0, 0, :h, 'active')", {"h": "c" * 64})
    _execute(url, "INSERT INTO trading.runtime_config_versions (config_key, version_no, content, schema_version, content_hash, status) VALUES ('cfg-2', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "d" * 64})
    cfg_id = _query(url, "SELECT id FROM trading.runtime_config_versions WHERE config_key='cfg-2'")[0][0]
    _execute(url, "INSERT INTO trading.execution_spec_versions (spec_key, version_no, content, schema_version, content_hash, status) VALUES ('exec-2', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "e" * 64})
    exec_id = _query(url, "SELECT id FROM trading.execution_spec_versions WHERE spec_key='exec-2'")[0][0]
    _execute(url, "INSERT INTO trading.release_manifests (release_name, config_version_id, strategy_version_id, execution_spec_version_id, capital_permission_manifest_id, git_sha, image_digest, db_revision, total_hash, status) VALUES ('rel-2', :cfg, :strat, :exec, :perm, 'abc', 'img', 'b1000020', :h, 'active')", {"cfg": cfg_id, "strat": strat_id, "exec": exec_id, "perm": 1, "h": "f" * 64})
    rel_id = _query(url, "SELECT id FROM trading.release_manifests WHERE release_name='rel-2'")[0][0]
    return {"obj": obj_id, "strat": strat_id, "rel": rel_id}


def _seed_cohort(url, env):
    policy_hashes = {
        policy_type: f"{index:x}" * 64
        for index, policy_type in enumerate(REQUIRED_POLICIES, start=1)
    }
    statements = []
    for policy_type, policy_hash in policy_hashes.items():
        statements.extend([
            (
                "INSERT INTO trading.policy_type_scopes "
                "(policy_type, scope_type, scope_key) VALUES (:p, 'cohort', 'cohort-2')",
                {"p": policy_type},
            ),
            (
                "INSERT INTO trading.policy_freezes "
                "(policy_type, scope_type, scope_key, policy_version, policy_content_hash, "
                " release_manifest_id, status) "
                "VALUES (:p, 'cohort', 'cohort-2', 1, :h, :r, 'frozen')",
                {"p": policy_type, "h": policy_hash, "r": env["rel"]},
            ),
        ])
    _exec_multi(url, statements)
    _execute(url, "INSERT INTO trading.evaluation_cohorts (cohort_key, status, objective_contract_id, strategy_version_id, release_manifest_id, policy_hashes, seed_hash) VALUES ('cohort-2', 'DRAFT', :obj, :strat, :rel, CAST(:pol AS jsonb), :seed)", {"obj": env["obj"], "strat": env["strat"], "rel": env["rel"], "pol": json.dumps(policy_hashes), "seed": "5" * 64})
    return _query(url, "SELECT id FROM trading.evaluation_cohorts WHERE cohort_key='cohort-2'")[0][0]


def _open_cohort(url, cohort):
    _execute(url, "UPDATE trading.evaluation_cohorts SET status='OPEN', opened_at=now() WHERE id=:c", {"c": cohort})


def _next_id(url, table):
    return _query(url, f"SELECT nextval(pg_get_serial_sequence('trading.{table}', 'id'))")[0][0]


def _seed_snapshot(url):
    _execute(url, "INSERT INTO trading.pm_markets (gamma_market_id, condition_id, active) VALUES ('m-2', 'cond-2', true)")
    mid = _query(url, "SELECT id FROM trading.pm_markets WHERE gamma_market_id='m-2'")[0][0]
    _execute(url, "INSERT INTO trading.pm_tokens (token_id, market_id, outcome_index) VALUES ('t-20', :m, 0), ('t-21', :m, 1)", {"m": mid})
    t0 = _query(url, "SELECT id FROM trading.pm_tokens WHERE token_id='t-20'")[0][0]
    t1 = _query(url, "SELECT id FROM trading.pm_tokens WHERE token_id='t-21'")[0][0]
    _execute(url, "INSERT INTO trading.pm_market_versions (market_id, version_no, observed_at, received_at, normalized_hash) VALUES (:m, 1, now(), now(), :hash)", {"m": mid, "hash": "a" * 64})
    mv = _query(url, "SELECT id FROM trading.pm_market_versions WHERE market_id=:m", {"m": mid})[0][0]
    _execute(url, "INSERT INTO trading.pm_token_versions (token_id, version_no, outcome_index, observed_at, received_at) VALUES (:t, 1, 0, now(), now()), (:t1, 1, 1, now(), now())", {"t": t0, "t1": t1})
    tv0 = _query(url, "SELECT id FROM trading.pm_token_versions WHERE token_id=:t", {"t": t0})[0][0]
    tv1 = _query(url, "SELECT id FROM trading.pm_token_versions WHERE token_id=:t", {"t": t1})[0][0]
    sha = "c" * 64
    _execute(url, "INSERT INTO trading.artifact_objects (sha256, original_size, stored_size, mime, compression, storage_driver, storage_version, locator) VALUES (:sha, 10, 10, 'application/json', 'none', 'local', 'cas/v1', :loc)", {"sha": sha, "loc": f"cas/v1/sha256/{sha[:2]}/{sha[2:4]}/{sha}.raw"})
    art = _query(url, "SELECT id FROM trading.artifact_objects WHERE sha256=:sha", {"sha": sha})[0][0]
    _execute(url, "INSERT INTO trading.contract_snapshots (market_version_id, yes_token_version_id, no_token_version_id, artifact_object_id, rules, resolution_source, clarification, content_hash) VALUES (:mv, :tv0, :tv1, :art, 'r', 'g', 'c', :h)", {"mv": mv, "tv0": tv0, "tv1": tv1, "art": art, "h": "b" * 64})
    snapshot = _query(url, "SELECT id FROM trading.contract_snapshots ORDER BY id DESC LIMIT 1")[0][0]
    return {"snapshot": snapshot, "market": mid, "t0": t0, "t1": t1, "tv0": tv0, "tv1": tv1}


def _seed_active_component(url, prefix):
    snap = _seed_snapshot(url)
    _execute(url, "INSERT INTO trading.forecast_components (component_key) VALUES (:k)", {"k": f"comp-{prefix}"})
    component = _query(url, "SELECT id FROM trading.forecast_components WHERE component_key=:k", {"k": f"comp-{prefix}"})[0][0]
    specs = [_next_id(url, "contract_specs"), _next_id(url, "contract_specs")]
    statements = []
    for index, spec in enumerate(specs, start=1):
        statements.extend([
            (
                "INSERT INTO trading.contract_specs "
                "(id, contract_key, version_no, snapshot_id, kc_resolution_states, "
                " token_ids, token_count, state_count, compiler_version, schema_version, "
                " status, content_hash) VALUES "
                "(:id, :key, 1, :snap, '[\"YES\",\"NO\"]'::jsonb, CAST(:tokens AS jsonb), "
                " 2, 2, 'lookup/v1', 1, 'pass', :hash)",
                {
                    "id": spec, "key": f"{prefix}{index}", "snap": snap["snapshot"],
                    "tokens": json.dumps({"0": str(snap["t0"]), "1": str(snap["t1"])}),
                    "hash": f"{index}" * 64,
                },
            ),
            (
                "INSERT INTO trading.payout_functions "
                "(contract_spec_id, pm_token_id, token_version_id, outcome_index, "
                " function_ir, test_vectors, algorithm_hash, content_hash) VALUES "
                "(:s, :t, :tv, 0, '{\"YES\":\"1\",\"NO\":\"0\"}'::jsonb, "
                " '{}'::jsonb, :ah, :ch)",
                {"s": spec, "t": snap["t0"], "tv": snap["tv0"], "ah": "a" * 64, "ch": f"{index + 2}" * 64},
            ),
            (
                "INSERT INTO trading.payout_functions "
                "(contract_spec_id, pm_token_id, token_version_id, outcome_index, "
                " function_ir, test_vectors, algorithm_hash, content_hash) VALUES "
                "(:s, :t, :tv, 1, '{\"YES\":\"0\",\"NO\":\"1\"}'::jsonb, "
                " '{}'::jsonb, :ah, :ch)",
                {"s": spec, "t": snap["t1"], "tv": snap["tv1"], "ah": "b" * 64, "ch": f"{index + 4}" * 64},
            ),
        ])
    _exec_multi(url, statements)
    world_schema = _next_id(url, "world_schema_versions")
    component_version = _next_id(url, "forecast_component_versions")
    member_hc = {"w0": "YES"}
    schema_hc = {str(spec): member_hc for spec in specs}
    resolution_map = {f"{index}" * 64: member_hc for index, _ in enumerate(specs, start=1)}
    _exec_multi(url, [
        (
            "INSERT INTO trading.world_schema_versions "
            "(id, component_id, version_no, variables, domains, constraints, factorization, "
            " world_states, state_count, resolution_map, h_c, status, content_hash, schema_version) "
            "VALUES (:id, :c, 1, '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, '{}'::jsonb, "
            " CAST(:states AS jsonb), 1, CAST(:resolution AS jsonb), CAST(:hc AS jsonb), "
            " 'active', :h, 1)",
            {"id": world_schema, "c": component,
             "states": json.dumps([{"world_state_id": "w0", "assignment": {"x": "0"}}]),
             "resolution": json.dumps(resolution_map), "hc": json.dumps(schema_hc), "h": "c" * 64},
        ),
        (
            "INSERT INTO trading.forecast_component_versions "
            "(id, component_id, version_no, world_schema_version_id, status, content_hash) "
            "VALUES (:id, :c, 1, :ws, 'active', :h)",
            {"id": component_version, "c": component, "ws": world_schema, "h": "d" * 64},
        ),
        *[
            (
                "INSERT INTO trading.forecast_component_contract_specs "
                "(component_version_id, contract_spec_id, h_c, totality_test_hash) "
                "VALUES (:cv, :s, CAST(:hc AS jsonb), :th)",
                {"cv": component_version, "s": spec, "hc": json.dumps(member_hc), "th": f"{index + 5}" * 64},
            )
            for index, spec in enumerate(specs)
        ],
    ])
    return {"component": component, "component_version": component_version, "specs": specs, "market": snap["market"]}


def _seed_confirmed_screening(url, cohort, market, objective):
    frame_sha = "9" * 64
    _execute(url, "INSERT INTO trading.artifact_objects (sha256, original_size, stored_size, mime, compression, storage_driver, storage_version, locator) VALUES (:sha, 1, 1, 'application/json', 'none', 'local', 'cas/v1', :loc)", {"sha": frame_sha, "loc": f"cas/v1/sha256/{frame_sha[:2]}/{frame_sha[2:4]}/{frame_sha}.raw"})
    _execute(url, "INSERT INTO trading.pm_universe_frames (status, started_at, owner, lease_expires_at, fencing_token, completed_at, page_count, total_events, total_markets, content_hash, artifact_id) VALUES ('COMPLETE', now(), 'test', now() + interval '60 seconds', 1, now(), 0, 0, 0, :h, :a)", {"h": frame_sha, "a": _query(url, "SELECT id FROM trading.artifact_objects WHERE sha256=:s", {"s": frame_sha})[0][0]})
    frame = _query(url, "SELECT id FROM trading.pm_universe_frames ORDER BY id DESC LIMIT 1")[0][0]
    _execute(url, "INSERT INTO trading.universe_memberships (cohort_id, market_id, first_seen_source, first_observed_at, first_ingested_at, metadata_hash) VALUES (:c, :m, 'WS_HINT', now(), now(), :h)", {"c": cohort, "m": market, "h": "0" * 64})
    _execute(url, "UPDATE trading.universe_memberships SET confirmed_frame_id=:f, confirmed_at=now() WHERE cohort_id=:c AND market_id=:m", {"c": cohort, "m": market, "f": frame})
    _execute(url, "INSERT INTO trading.screening_episodes (cohort_id, market_id, episode_no, objective_contract_id, input_snapshot, input_hash, result, audit_assigned) VALUES (:c, :m, 1, :o, '{}'::jsonb, :h, 'SELECT', false)", {"c": cohort, "m": market, "o": objective, "h": "7" * 64})
    screening = _query(url, "SELECT id FROM trading.screening_episodes ORDER BY id DESC LIMIT 1")[0][0]
    return {"frame": frame, "screening": screening}


def _seed_routed_episode(url, env, cohort, component, key):
    """Build G0→R0→G1→G2→R1→(routed) full chain so G4/G5A/G5B/G6 can attach."""
    screening = _seed_confirmed_screening(url, cohort, component["market"], env["obj"])["screening"]
    _execute(url, "INSERT INTO trading.gate_decisions (gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, result, committed_at) VALUES ('G0', 'screening', :s, :ih, :ph, :vm, 'PASS', now()), ('R0', 'screening', :s, :ih2, :ph2, :vm, 'SELECT', now())", {"s": screening, "ih": "1" * 64, "ph": "2" * 64, "ih2": "3" * 64, "ph2": "4" * 64, "vm": env["rel"]})
    _execute(url, "INSERT INTO trading.decision_opportunities (opportunity_key, cohort_id, chain_type, objective_contract_id, strategy_version_id, source_screening_episode_id, triggered_at) VALUES (:k, :c, 'DECISION', :obj, :strat, :s, now())", {"k": f"{key}-parent", "c": cohort, "obj": env["obj"], "strat": env["strat"], "s": screening})
    parent = _query(url, "SELECT id FROM trading.decision_opportunities WHERE opportunity_key=:k", {"k": f"{key}-parent"})[0][0]
    _execute(url, "INSERT INTO trading.gate_decisions (gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, result, committed_at) VALUES ('G1', 'opportunity', :o, :ih, :ph, :vm, 'PASS', now()), ('G2', 'opportunity', :o, :ih2, :ph2, :vm, 'PASS', now())", {"o": parent, "ih": "5" * 64, "ph": "6" * 64, "ih2": "7" * 64, "ph2": "8" * 64, "vm": env["rel"]})
    ep = _next_id(url, "forecast_episodes")
    _exec_multi(url, [
        (
            "INSERT INTO trading.forecast_episodes "
            "(id, episode_key, decision_opportunity_id, component_version_id, "
            " strategy_version_id, objective_contract_id, trigger, cutoff_at, horizon, "
            " experiment_variant) VALUES "
            "(:id, :k, :opp, :cv, :strat, :obj, 'frame', now(), 'res', 'control')",
            {"id": ep, "k": "e" * 64, "opp": parent, "cv": component["component_version"],
             "strat": env["strat"], "obj": env["obj"]},
        ),
        *[
            ("INSERT INTO trading.episode_contract_specs (episode_id, contract_spec_id) VALUES (:e, :s)", {"e": ep, "s": spec})
            for spec in component["specs"]
        ],
    ])
    _execute(url, "INSERT INTO trading.gate_decisions (gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, result, committed_at) VALUES ('R1', 'episode', :e, :ih, :ph, :vm, 'standard', now())", {"e": ep, "ih": "a" * 64, "ph": "b" * 64, "vm": env["rel"]})
    _exec_multi(url, [
        (
            "INSERT INTO trading.episode_memberships "
            "(episode_id, route_channel, processing_disposition, action_eligible, "
            " qualification_eligible, capital_evidence_eligible) "
            "VALUES (:e, 'standard', 'completed', false, false, false)",
            {"e": ep},
        ),
        ("UPDATE trading.forecast_episodes SET status='ROUTED' WHERE id=:e", {"e": ep}),
    ])
    return ep


def test_literal_empty_roundtrip_and_reinforcement(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V20, url)
    assert _version(url) == [(V20,)]
    assert set(COGNITION_TABLES) <= set(_trading_tables(url))
    cols = {row[0] for row in _query(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='trading' AND table_name='forecast_episodes'",
    )}
    assert {"cognition_status", "prior_frozen_at", "evidence_bundle_at", "forecast_committed_at"} <= cols
    gate_ck = {row[0] for row in _query(
        url,
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid='trading.gate_decisions'::regclass",
    )}
    assert "ck_gate_decisions_gate_known" in gate_ck

    _run(command.downgrade, V13, url)
    assert _version(url) == [(V13,)]
    assert set(COGNITION_TABLES).isdisjoint(set(_trading_tables(url)))
    cols2 = {row[0] for row in _query(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='trading' AND table_name='forecast_episodes'",
    )}
    assert "cognition_status" not in cols2
    gate_ck2 = {row[0] for row in _query(
        url,
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid='trading.gate_decisions'::regclass",
    )}
    assert "ck_gate_decisions_gate_known" in gate_ck2

    _run(command.upgrade, V20, url)
    assert _version(url) == [(V20,)]


def test_cognition_gate_ordering_and_allowlist(temp_pg_db):
    """G4/G5A/G5B/G6 绑 episode；顺序由 gate guard 强制（R1→G4→G5A→G5B→G6）。"""
    url = temp_pg_db.url
    _run(command.upgrade, V20, url)
    env = _seed_core(url)
    cohort = _seed_cohort(url, env)
    _open_cohort(url, cohort)
    component = _seed_active_component(url, "ra")
    ep = _seed_routed_episode(url, env, cohort, component, "r1")
    # G5A 无 G4 PASS 时拒绝（顺序）
    with pytest.raises(Exception, match="v2_gate_order_g4_g5a"):
        _execute(url, "INSERT INTO trading.gate_decisions (gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, result, committed_at) VALUES ('G5A', 'episode', :e, :ih, :ph, :vm, 'PASS', now())", {"e": ep, "ih": "2" * 64, "ph": "2" * 64, "vm": env["rel"]})
    # G4 可写（R1 已存在）；随后 G5A/G5B 可写；G6 需 G5B PASS
    _execute(url, "INSERT INTO trading.gate_decisions (gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, result, committed_at) VALUES ('G4', 'episode', :e, :ih, :ph, :vm, 'PASS', now())", {"e": ep, "ih": "1" * 64, "ph": "1" * 64, "vm": env["rel"]})
    _execute(url, "INSERT INTO trading.gate_decisions (gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, result, committed_at) VALUES ('G5A', 'episode', :e, :ih, :ph, :vm, 'PASS', now())", {"e": ep, "ih": "3" * 64, "ph": "3" * 64, "vm": env["rel"]})
    with pytest.raises(Exception, match="v2_gate_order_g5b_g6"):
        _execute(url, "INSERT INTO trading.gate_decisions (gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, result, committed_at) VALUES ('G6', 'episode', :e, :ih, :ph, :vm, 'PASS', now())", {"e": ep, "ih": "9" * 64, "ph": "9" * 64, "vm": env["rel"]})
    _execute(url, "INSERT INTO trading.gate_decisions (gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, result, committed_at) VALUES ('G5B', 'episode', :e, :ih, :ph, :vm, 'PASS', now())", {"e": ep, "ih": "4" * 64, "ph": "4" * 64, "vm": env["rel"]})
    _execute(url, "INSERT INTO trading.gate_decisions (gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, result, committed_at) VALUES ('G6', 'episode', :e, :ih, :ph, :vm, 'PASS', now())", {"e": ep, "ih": "5" * 64, "ph": "5" * 64, "vm": env["rel"]})
    # G6 FAIL 必须带 reason
    with pytest.raises(Exception, match="v2_gate_failure_reason_required"):
        _execute(url, "INSERT INTO trading.gate_decisions (gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, result, committed_at) VALUES ('G6', 'episode', :e, :ih, :ph, :vm, 'FAIL', now())", {"e": ep, "ih": "6" * 64, "ph": "6" * 64, "vm": env["rel"]})


def test_episode_cognition_lifecycle_and_submission_immutable(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V20, url)
    env = _seed_core(url)
    cohort = _seed_cohort(url, env)
    _open_cohort(url, cohort)
    component = _seed_active_component(url, "rb")
    ep = _seed_routed_episode(url, env, cohort, component, "r2")

    # PENDING→PRIOR_READY（G4）
    _execute(url, "UPDATE trading.forecast_episodes SET cognition_status='PRIOR_READY', prior_frozen_at=now() WHERE id=:e", {"e": ep})
    with pytest.raises(Exception, match="v2_episode_immutable"):
        _execute(url, "UPDATE trading.forecast_episodes SET cognition_status='PRIOR_READY', prior_frozen_at=now() WHERE id=:e", {"e": ep})
    # PRIOR_READY→EVIDENCE_READY（G5A/G5B）
    _execute(url, "UPDATE trading.forecast_episodes SET cognition_status='EVIDENCE_READY', evidence_bundle_at=now() WHERE id=:e", {"e": ep})
    # EVIDENCE_READY→BLIND_COMMITTED（G6）+ 时间戳
    _execute(url, "UPDATE trading.forecast_episodes SET status='BLIND_COMMITTED', cognition_status='COMMITTED', forecast_committed_at=now() WHERE id=:e", {"e": ep})
    with pytest.raises(Exception, match="v2_episode_immutable"):
        _execute(url, "UPDATE trading.forecast_episodes SET drop_reason='late' WHERE id=:e", {"e": ep})

    # submission DRAFT→BLIND_COMMITTED 原子；commit 后禁改/禁删
    manifest = _next_id(url, "forecast_input_manifests")
    _exec_multi(url, [
        (
            "INSERT INTO trading.forecast_input_manifests "
            "(id, episode_id, manifest_key, manifest_hash, evidence_bundle_hash, "
            " contract_spec_set_hash, world_schema_hash, prior_hash, taxonomy_hash, "
            " model_binding_hash, prompt_hash, code_hash, content) VALUES "
            "(:id, :e, 'mk', :h, :h, :h, :h, :h, :h, :h, :h, :h, '{}'::jsonb)",
            {"id": manifest, "e": ep, "h": "a" * 64},
        ),
        (
            "INSERT INTO trading.forecast_submissions "
            "(episode_id, submission_key, Q, U, forecast_input_manifest_id, "
            " contract_schema_prior_evidence_hash, algorithm_hash) VALUES "
            "(:e, 'sub-1', '{\"w0\":\"1\"}'::jsonb, "
            " '[{\"w0\":\"1\"}]'::jsonb, :m, :h, :h)",
            {"e": ep, "m": manifest, "h": "b" * 64},
        ),
    ])
    sub = _query(url, "SELECT id FROM trading.forecast_submissions WHERE submission_key='sub-1'")[0][0]
    _execute(url, "UPDATE trading.forecast_submissions SET status='BLIND_COMMITTED', committed_at=now() WHERE id=:s", {"s": sub})
    with pytest.raises(Exception, match="v2_submission_immutable"):
        _execute(url, "UPDATE trading.forecast_submissions SET status='BLIND_COMMITTED' WHERE id=:s", {"s": sub})
    with pytest.raises(Exception, match="v2_submission_immutable"):
        _execute(url, "UPDATE trading.forecast_submissions SET Q='{\"w0\":\"0\"}'::jsonb WHERE id=:s", {"s": sub})
    with pytest.raises(Exception, match="v2_submission_immutable"):
        _execute(url, "DELETE FROM trading.forecast_submissions WHERE id=:s", {"s": sub})


def test_downgrade_fail_closed_on_unknown_object(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V20, url)
    before = set(_trading_tables(url))
    _execute(url, "CREATE TABLE trading.unknown_intruder_0020 (id integer)")
    with pytest.raises(
        Exception,
        match="v2_wp02_unknown_object|v2_wp01c_unknown_object|v2_wp01b_unknown_object|v2_trading_schema_not_empty",
    ):
        _run(command.downgrade, "b1000001", url)
    assert _query(url, "SELECT to_regclass('trading.unknown_intruder_0020') IS NOT NULL") == [(True,)]
    assert set(_trading_tables(url)) == before | {"unknown_intruder_0020"}
    assert _version(url) == [(V20,)]
