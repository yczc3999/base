"""
WP-01C v2_0013 cohort/episode migration —— 真 PostgreSQL 集成验收（Checkpoint B/C §8.1）。

覆盖：12 新表 + policy 可逆强化（roundtrip）、cohort 状态机 guard、membership REST
confirmation NULL→COMPLETE、episode spec-set deferred trigger 全等、append-only、未知对象
downgrade preflight。
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command, util
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V12 = "b1000012"
V13 = "b1000013"

STREAM_TABLES = [
    "evaluation_cohorts", "universe_memberships", "screening_episodes", "audit_samples",
    "decision_opportunities", "decision_opportunity_markets", "episode_memberships",
    "forecast_episodes", "episode_contract_specs", "information_snapshots",
    "information_snapshot_items", "gate_decisions",
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


def _version(db_url):
    return _query(db_url, "SELECT version_num FROM public.alembic_version")


def _trading_tables(db_url):
    return [r[0] for r in _query(
        db_url, "SELECT tablename FROM pg_tables WHERE schemaname='trading' ORDER BY 1"
    )]


def test_literal_empty_roundtrip_and_policy_reinforcement_reversible(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V13, url)
    assert _version(url) == [(V13,)]
    assert set(STREAM_TABLES) <= set(_trading_tables(url))
    pts = _query(
        url,
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid='trading.policy_type_scopes'::regclass",
    )
    assert any("uq_policy_type_scopes_type" == row[0] for row in pts)
    assert any("ck_policy_type_scopes_scope_type_known" == row[0] for row in pts)
    pf_names = {
        row[0] for row in _query(
            url,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='trading' AND table_name='policy_freezes'",
        )
    }
    assert {"policy_type", "scope_type", "scope_key", "policy_version", "frozen_at"} <= pf_names
    assert any("uq_policy_freezes_type_scope_version" == row[0] for row in _query(
        url,
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid='trading.policy_freezes'::regclass",
    ))

    _run(command.downgrade, V12, url)
    assert _version(url) == [(V12,)]
    assert set(STREAM_TABLES).isdisjoint(set(_trading_tables(url)))
    pts2 = _query(
        url,
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid='trading.policy_type_scopes'::regclass",
    )
    assert any("uq_policy_type_scopes_triple" == row[0] for row in pts2)
    assert not any("uq_policy_type_scopes_type" == row[0] for row in pts2)
    pf2 = _query(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='trading' AND table_name='policy_freezes'",
    )
    assert "policy_type" not in {row[0] for row in pf2}

    _run(command.upgrade, V13, url)
    assert _version(url) == [(V13,)]


def test_existing_b1000012_policy_rows_upgrade_and_downgrade_without_loss(temp_pg_db):
    """Populated existing head is backfilled deterministically, not rejected or overwritten."""
    url = temp_pg_db.url
    _run(command.upgrade, V12, url)
    env = _seed_core(url)
    _execute(
        url,
        "INSERT INTO trading.policy_type_scopes "
        "(policy_type, scope_type, scope_key) VALUES ('legacy-policy', 'cohort', 'legacy')",
    )
    _execute(
        url,
        "INSERT INTO trading.policy_freezes "
        "(policy_content_hash, release_manifest_id, status) "
        "VALUES (:h, :r, 'frozen')",
        {"h": "8" * 64, "r": env["rel"]},
    )
    freeze_id = _query(
        url, "SELECT id FROM trading.policy_freezes ORDER BY id DESC LIMIT 1"
    )[0][0]

    _run(command.upgrade, V13, url)
    upgraded = _query(
        url,
        "SELECT policy_content_hash, release_manifest_id, status, policy_type, "
        "scope_type, scope_key, policy_version, frozen_at IS NOT NULL "
        "FROM trading.policy_freezes WHERE id=:id",
        {"id": freeze_id},
    )[0]
    assert upgraded == (
        "8" * 64, env["rel"], "frozen", f"__legacy_freeze__:{freeze_id}",
        "promotion", f"legacy:{freeze_id}", 0, True,
    )

    _run(command.downgrade, V12, url)
    assert _query(
        url,
        "SELECT policy_content_hash, release_manifest_id, status "
        "FROM trading.policy_freezes WHERE id=:id",
        {"id": freeze_id},
    ) == [("8" * 64, env["rel"], "frozen")]
    assert _query(
        url,
        "SELECT policy_type, scope_type, scope_key FROM trading.policy_type_scopes "
        "ORDER BY policy_type",
    ) == [("legacy-policy", "cohort", "legacy")]


def _seed_core(url):
    """插入 control/strategy/release + cohort 所需的对象。"""
    # strategy_objective_contracts
    _execute(url, "INSERT INTO trading.strategy_objective_contracts (contract_key, version_no, content, schema_version, content_hash, status) VALUES ('obj-1', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "a" * 64})
    obj_id = _query(url, "SELECT id FROM trading.strategy_objective_contracts WHERE contract_key='obj-1'")[0][0]
    _execute(url, "INSERT INTO trading.strategy_versions (strategy_key, version_no, content, schema_version, content_hash, status) VALUES ('strat-1', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "b" * 64})
    strat_id = _query(url, "SELECT id FROM trading.strategy_versions WHERE strategy_key='strat-1'")[0][0]
    _execute(url, "INSERT INTO trading.capital_permission_manifests (name, mode, capability, limits, evaluation_capital, authorized_capital, content_hash, status) VALUES ('perm-1', 'shadow', '{}'::jsonb, '{}'::jsonb, 0, 0, :h, 'active')", {"h": "c" * 64})
    _execute(url, "INSERT INTO trading.runtime_config_versions (config_key, version_no, content, schema_version, content_hash, status) VALUES ('cfg-1', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "d" * 64})
    cfg_id = _query(url, "SELECT id FROM trading.runtime_config_versions WHERE config_key='cfg-1'")[0][0]
    _execute(url, "INSERT INTO trading.execution_spec_versions (spec_key, version_no, content, schema_version, content_hash, status) VALUES ('exec-1', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "e" * 64})
    exec_id = _query(url, "SELECT id FROM trading.execution_spec_versions WHERE spec_key='exec-1'")[0][0]
    _execute(url, "INSERT INTO trading.release_manifests (release_name, config_version_id, strategy_version_id, execution_spec_version_id, capital_permission_manifest_id, git_sha, image_digest, db_revision, total_hash, status) VALUES ('rel-1', :cfg, :strat, :exec, :perm, 'abc', 'img', 'b1000013', :h, 'active')", {"cfg": cfg_id, "strat": strat_id, "exec": exec_id, "perm": 1, "h": "f" * 64})
    rel_id = _query(url, "SELECT id FROM trading.release_manifests WHERE release_name='rel-1'")[0][0]
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
                "(policy_type, scope_type, scope_key) VALUES (:p, 'cohort', 'cohort-1')",
                {"p": policy_type},
            ),
            (
                "INSERT INTO trading.policy_freezes "
                "(policy_type, scope_type, scope_key, policy_version, policy_content_hash, "
                " release_manifest_id, status) "
                "VALUES (:p, 'cohort', 'cohort-1', 1, :h, :r, 'frozen')",
                {"p": policy_type, "h": policy_hash, "r": env["rel"]},
            ),
        ])
    _exec_multi(url, statements)
    _execute(url, "INSERT INTO trading.evaluation_cohorts (cohort_key, status, objective_contract_id, strategy_version_id, release_manifest_id, policy_hashes, seed_hash) VALUES ('cohort-1', 'DRAFT', :obj, :strat, :rel, CAST(:pol AS jsonb), :seed)", {"obj": env["obj"], "strat": env["strat"], "rel": env["rel"], "pol": json.dumps(policy_hashes), "seed": "5" * 64})
    return _query(url, "SELECT id FROM trading.evaluation_cohorts WHERE cohort_key='cohort-1'")[0][0]


def _open_cohort(url, cohort):
    _execute(
        url,
        "UPDATE trading.evaluation_cohorts "
        "SET status='OPEN', opened_at=now() WHERE id=:c",
        {"c": cohort},
    )


def test_policy_scope_binding_and_open_requires_exact_freezes(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V13, url)
    env = _seed_core(url)
    _execute(
        url,
        "INSERT INTO trading.policy_type_scopes "
        "(policy_type, scope_type, scope_key) VALUES ('scope-test', 'cohort', 'x')",
    )
    with pytest.raises(Exception, match="fk_policy_freezes_policy_scope"):
        _execute(
            url,
            "INSERT INTO trading.policy_freezes "
            "(policy_type, scope_type, scope_key, policy_version, policy_content_hash, "
            " release_manifest_id) VALUES "
            "('scope-test', 'strategy', 'x', 1, :h, :r)",
            {"h": "a" * 64, "r": env["rel"]},
        )

    policy_hashes = {
        policy_type: f"{index:x}" * 64
        for index, policy_type in enumerate(REQUIRED_POLICIES, start=1)
    }
    _execute(
        url,
        "INSERT INTO trading.evaluation_cohorts "
        "(cohort_key, status, objective_contract_id, strategy_version_id, "
        " release_manifest_id, policy_hashes, seed_hash) "
        "VALUES ('missing-freezes', 'DRAFT', :obj, :strat, :rel, CAST(:p AS jsonb), :s)",
        {"obj": env["obj"], "strat": env["strat"], "rel": env["rel"],
         "p": json.dumps(policy_hashes), "s": "5" * 64},
    )
    cohort = _query(
        url,
        "SELECT id FROM trading.evaluation_cohorts WHERE cohort_key='missing-freezes'",
    )[0][0]
    with pytest.raises(Exception, match="v2_cohort_policy_freezes_incomplete"):
        _open_cohort(url, cohort)


def _seed_market(url):
    _execute(url, "INSERT INTO trading.pm_markets (gamma_market_id, condition_id, active) VALUES ('m-c', 'cond-c', true)")
    return _query(url, "SELECT id FROM trading.pm_markets WHERE gamma_market_id='m-c'")[0][0]


def _seed_complete_frame(url, suffix="9"):
    sha = suffix * 64
    _execute(
        url,
        "INSERT INTO trading.artifact_objects "
        "(sha256, original_size, stored_size, mime, compression, storage_driver, "
        " storage_version, locator) "
        "VALUES (:sha, 1, 1, 'application/json', 'none', 'local', 'cas/v1', :loc)",
        {"sha": sha, "loc": f"cas/v1/sha256/{sha[:2]}/{sha[2:4]}/{sha}.raw"},
    )
    artifact_id = _query(
        url, "SELECT id FROM trading.artifact_objects WHERE sha256=:sha", {"sha": sha}
    )[0][0]
    _execute(
        url,
        "INSERT INTO trading.pm_universe_frames "
        "(status, started_at, owner, lease_expires_at, fencing_token, completed_at, "
        " page_count, total_events, total_markets, content_hash, artifact_id) "
        "VALUES ('COMPLETE', now(), 'test', now() + interval '60 seconds', 1, now(), "
        "0, 0, 0, :h, :a)",
        {"h": sha, "a": artifact_id},
    )
    return _query(
        url, "SELECT id FROM trading.pm_universe_frames ORDER BY id DESC LIMIT 1"
    )[0][0]


def _seed_confirmed_screening(url, cohort, market, objective):
    frame = _seed_complete_frame(url)
    _execute(
        url,
        "INSERT INTO trading.universe_memberships "
        "(cohort_id, market_id, first_seen_source, first_observed_at, first_ingested_at, "
        " metadata_hash) VALUES (:c, :m, 'WS_HINT', now(), now(), :h)",
        {"c": cohort, "m": market, "h": "0" * 64},
    )
    _execute(
        url,
        "UPDATE trading.universe_memberships "
        "SET confirmed_frame_id=:f, confirmed_at=now() "
        "WHERE cohort_id=:c AND market_id=:m",
        {"c": cohort, "m": market, "f": frame},
    )
    _execute(
        url,
        "INSERT INTO trading.screening_episodes "
        "(cohort_id, market_id, episode_no, objective_contract_id, input_snapshot, "
        " input_hash, result, audit_assigned) "
        "VALUES (:c, :m, 1, :o, '{}'::jsonb, :h, 'SELECT', false)",
        {"c": cohort, "m": market, "o": objective, "h": "7" * 64},
    )
    screening = _query(
        url, "SELECT id FROM trading.screening_episodes ORDER BY id DESC LIMIT 1"
    )[0][0]
    return {"frame": frame, "screening": screening}


def _seed_snapshot(url):
    """建 snapshot 供 contract_specs FK（market + tokens + versions + artifact）。"""
    _execute(url, "INSERT INTO trading.pm_markets (gamma_market_id, condition_id, active) VALUES ('m-s', 'cond-s', true)")
    mid = _query(url, "SELECT id FROM trading.pm_markets WHERE gamma_market_id='m-s'")[0][0]
    _execute(url, "INSERT INTO trading.pm_tokens (token_id, market_id, outcome_index) VALUES ('t-s0', :m, 0), ('t-s1', :m, 1)", {"m": mid})
    t0 = _query(url, "SELECT id FROM trading.pm_tokens WHERE token_id='t-s0'")[0][0]
    t1 = _query(url, "SELECT id FROM trading.pm_tokens WHERE token_id='t-s1'")[0][0]
    _execute(url, "INSERT INTO trading.pm_market_versions (market_id, version_no, observed_at, received_at, normalized_hash) VALUES (:m, 1, now(), now(), 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')", {"m": mid})
    mv = _query(url, "SELECT id FROM trading.pm_market_versions WHERE market_id=:m", {"m": mid})[0][0]
    _execute(url, "INSERT INTO trading.pm_token_versions (token_id, version_no, outcome_index, observed_at, received_at) VALUES (:t, 1, 0, now(), now()), (:t1, 1, 1, now(), now())", {"t": t0, "t1": t1})
    tv0 = _query(url, "SELECT id FROM trading.pm_token_versions WHERE token_id=:t", {"t": t0})[0][0]
    tv1 = _query(url, "SELECT id FROM trading.pm_token_versions WHERE token_id=:t", {"t": t1})[0][0]
    sha = "c" * 64
    _execute(url, "INSERT INTO trading.artifact_objects (sha256, original_size, stored_size, mime, compression, storage_driver, storage_version, locator) VALUES (:sha, 10, 10, 'application/json', 'none', 'local', 'cas/v1', :loc)", {"sha": sha, "loc": f"cas/v1/sha256/{sha[:2]}/{sha[2:4]}/{sha}.raw"})
    art = _query(url, "SELECT id FROM trading.artifact_objects WHERE sha256=:sha", {"sha": sha})[0][0]
    _execute(url, "INSERT INTO trading.contract_snapshots (market_version_id, yes_token_version_id, no_token_version_id, artifact_object_id, rules, resolution_source, clarification, content_hash) VALUES (:mv, :tv0, :tv1, :art, 'r', 'g', 'c', :h)", {"mv": mv, "tv0": tv0, "tv1": tv1, "art": art, "h": "b" * 64})
    snapshot = _query(
        url, "SELECT id FROM trading.contract_snapshots ORDER BY id DESC LIMIT 1"
    )[0][0]
    return {
        "snapshot": snapshot,
        "market": mid,
        "t0": t0,
        "t1": t1,
        "tv0": tv0,
        "tv1": tv1,
    }


def _next_id(url, table):
    return _query(
        url,
        f"SELECT nextval(pg_get_serial_sequence('trading.{table}', 'id'))",
    )[0][0]


def _seed_active_component(url, prefix):
    """Create two exact PASS specs and one complete active component atomically."""
    snap = _seed_snapshot(url)
    _execute(
        url,
        "INSERT INTO trading.forecast_components (component_key) VALUES (:k)",
        {"k": f"comp-{prefix}"},
    )
    component = _query(
        url, "SELECT id FROM trading.forecast_components WHERE component_key=:k",
        {"k": f"comp-{prefix}"},
    )[0][0]
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
                    "id": spec,
                    "key": f"{prefix}{index}",
                    "snap": snap["snapshot"],
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
                {"s": spec, "t": snap["t0"], "tv": snap["tv0"],
                 "ah": "a" * 64, "ch": f"{index + 2}" * 64},
            ),
            (
                "INSERT INTO trading.payout_functions "
                "(contract_spec_id, pm_token_id, token_version_id, outcome_index, "
                " function_ir, test_vectors, algorithm_hash, content_hash) VALUES "
                "(:s, :t, :tv, 1, '{\"YES\":\"0\",\"NO\":\"1\"}'::jsonb, "
                " '{}'::jsonb, :ah, :ch)",
                {"s": spec, "t": snap["t1"], "tv": snap["tv1"],
                 "ah": "b" * 64, "ch": f"{index + 4}" * 64},
            ),
        ])
    _exec_multi(url, statements)

    world_schema = _next_id(url, "world_schema_versions")
    component_version = _next_id(url, "forecast_component_versions")
    member_hc = {"w0": "YES"}
    schema_hc = {str(spec): member_hc for spec in specs}
    resolution_map = {
        f"{index}" * 64: member_hc for index, _spec_id in enumerate(specs, start=1)
    }
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
             "resolution": json.dumps(resolution_map),
             "hc": json.dumps(schema_hc), "h": "c" * 64},
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
                {"cv": component_version, "s": spec, "hc": json.dumps(member_hc),
                 "th": f"{index + 5}" * 64},
            )
            for index, spec in enumerate(specs)
        ],
    ])
    return {"component": component, "component_version": component_version,
            "specs": specs, "market": snap["market"]}


def _seed_open_opportunity(url, env, cohort, market, key):
    screening = _seed_confirmed_screening(url, cohort, market, env["obj"])["screening"]
    _execute(
        url,
        "INSERT INTO trading.gate_decisions "
        "(gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, "
        " result, committed_at) "
        "VALUES ('G0', 'screening', :s, :ih, :ph, :vm, 'PASS', now()), "
        "('R0', 'screening', :s, :ih2, :ph2, :vm, 'SELECT', now())",
        {"s": screening, "ih": "1" * 64, "ph": "2" * 64,
         "ih2": "3" * 64, "ph2": "4" * 64, "vm": env["rel"]},
    )
    _execute(
        url,
        "INSERT INTO trading.decision_opportunities "
        "(opportunity_key, cohort_id, chain_type, objective_contract_id, "
        " strategy_version_id, source_screening_episode_id, triggered_at) "
        "VALUES (:k, :c, 'DECISION', :obj, :strat, :s, now())",
        {"k": key, "c": cohort, "obj": env["obj"], "strat": env["strat"],
         "s": screening},
    )
    opportunity = _query(
        url, "SELECT id FROM trading.decision_opportunities WHERE opportunity_key=:k",
        {"k": key},
    )[0][0]
    _execute(
        url,
        "INSERT INTO trading.gate_decisions "
        "(gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, "
        " result, committed_at) "
        "VALUES ('G1', 'opportunity', :o, :ih, :ph, :vm, 'PASS', now()), "
        "('G2', 'opportunity', :o, :ih2, :ph2, :vm, 'PASS', now())",
        {"o": opportunity, "ih": "5" * 64, "ph": "6" * 64,
         "ih2": "7" * 64, "ph2": "8" * 64, "vm": env["rel"]},
    )
    return {"opportunity": opportunity, "screening": screening}


def test_cohort_lifecycle_guard_and_membership_confirm(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V13, url)
    env = _seed_core(url)
    cohort = _seed_cohort(url, env)
    market = _seed_market(url)
    now = datetime.now(timezone.utc)

    # cohort DRAFT→OPEN 合法；OPEN cohort 才能接纳 membership。
    _open_cohort(url, cohort)

    # membership：WS hint 先建；REST confirmation 只能 NULL→COMPLETE frame。
    _execute(url, "INSERT INTO trading.universe_memberships (cohort_id, market_id, first_seen_source, first_observed_at, first_ingested_at, metadata_hash) VALUES (:c, :m, 'WS_HINT', now(), now(), :mh)", {"c": cohort, "m": market, "mh": "0" * 64})
    _execute(
        url,
        "INSERT INTO trading.pm_universe_frames "
        "(status, started_at, owner, lease_expires_at, fencing_token) "
        "VALUES ('OPEN', now(), 'test-open', now() + interval '60 seconds', 1)",
    )
    open_frame = _query(
        url, "SELECT id FROM trading.pm_universe_frames ORDER BY id DESC LIMIT 1"
    )[0][0]
    with pytest.raises(Exception, match="v2_membership_frame_not_complete"):
        _execute(
            url,
            "UPDATE trading.universe_memberships "
            "SET confirmed_frame_id=:f, confirmed_at=now() "
            "WHERE cohort_id=:c AND market_id=:m",
            {"c": cohort, "m": market, "f": open_frame},
        )
    frame = _seed_complete_frame(url)
    # NULL→值 允许（确认）；第二次改 confirmed_frame_id → guard 拒绝
    _execute(url, "UPDATE trading.universe_memberships SET confirmed_frame_id=:f, confirmed_at=now() WHERE cohort_id=:c AND market_id=:m", {"c": cohort, "m": market, "f": frame})
    with pytest.raises(Exception, match="v2_membership_immutable"):
        _execute(url, "UPDATE trading.universe_memberships SET confirmed_frame_id=:f WHERE cohort_id=:c AND market_id=:m", {"c": cohort, "m": market, "f": frame})
    # first-seen 不改写
    with pytest.raises(Exception, match="v2_membership_immutable"):
        _execute(url, "UPDATE trading.universe_memberships SET first_seen_source='REST_FRAME' WHERE cohort_id=:c AND market_id=:m", {"c": cohort, "m": market})

    # OPEN→CLOSED 合法；终态及 identity 均冻结。
    _execute(url, "UPDATE trading.evaluation_cohorts SET status='CLOSED', closed_at=now() WHERE id=:c", {"c": cohort})
    with pytest.raises(Exception, match="v2_cohort_immutable"):
        _execute(url, "UPDATE trading.evaluation_cohorts SET status='OPEN' WHERE id=:c", {"c": cohort})
    with pytest.raises(Exception, match="v2_cohort_(identity_)?immutable"):
        _execute(url, "UPDATE trading.evaluation_cohorts SET seed_hash=:s WHERE id=:c", {"c": cohort, "s": "a" * 64})
    with pytest.raises(Exception, match="v2_cohort_immutable"):
        _execute(url, "DELETE FROM trading.evaluation_cohorts WHERE id=:c", {"c": cohort})


def test_episode_spec_set_deferred_trigger_equality(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V13, url)
    component = _seed_active_component(url, "sp")
    env = _seed_core(url)
    cohort = _seed_cohort(url, env)
    _open_cohort(url, cohort)
    opp = _seed_open_opportunity(
        url, env, cohort, component["market"], "opp-1"
    )["opportunity"]
    ep = _next_id(url, "forecast_episodes")

    # episode 本身也挂 deferred guard；必须与完整 spec set 同事务提交。
    _exec_multi(url, [
        (
            "INSERT INTO trading.forecast_episodes "
            "(id, episode_key, decision_opportunity_id, component_version_id, "
            " strategy_version_id, objective_contract_id, trigger, cutoff_at, horizon, "
            " experiment_variant) VALUES "
            "(:id, :k, :opp, :cv, :strat, :obj, 'frame', now(), 'res', 'control')",
            {"id": ep, "k": "e" * 64, "opp": opp,
             "cv": component["component_version"], "strat": env["strat"],
             "obj": env["obj"]},
        ),
        *[
            (
                "INSERT INTO trading.episode_contract_specs "
                "(episode_id, contract_spec_id) VALUES (:e, :s)",
                {"e": ep, "s": spec},
            )
            for spec in component["specs"]
        ],
    ])
    assert _query(url, "SELECT count(*) FROM trading.episode_contract_specs WHERE episode_id=:e", {"e": ep})[0][0] == 2
    _execute(
        url,
        "INSERT INTO trading.gate_decisions "
        "(gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, "
        " result, committed_at) "
        "VALUES ('R1', 'episode', :e, :ih, :ph, :vm, 'standard', now())",
        {"e": ep, "ih": "a" * 64, "ph": "b" * 64, "vm": env["rel"]},
    )
    with pytest.raises(Exception, match="ck_episode_memberships_wp01c_no_eligibility"):
        _execute(
            url,
            "INSERT INTO trading.episode_memberships "
            "(episode_id, route_channel, processing_disposition, action_eligible, "
            " qualification_eligible, capital_evidence_eligible) "
            "VALUES (:e, 'standard', 'completed', true, false, false)",
            {"e": ep},
        )
    _exec_multi(url, [
        (
            "INSERT INTO trading.episode_memberships "
            "(episode_id, route_channel, processing_disposition, action_eligible, "
            " qualification_eligible, capital_evidence_eligible) "
            "VALUES (:e, 'standard', 'completed', false, false, false)",
            {"e": ep},
        ),
        (
            "UPDATE trading.forecast_episodes SET status='ROUTED' WHERE id=:e",
            {"e": ep},
        ),
    ])
    with pytest.raises(Exception, match="v2_episode_immutable"):
        _execute(
            url,
            "UPDATE trading.forecast_episodes SET drop_reason='late-change' WHERE id=:e",
            {"e": ep},
        )


def _spec(url, key):
    return _query(url, "SELECT id FROM trading.contract_specs WHERE contract_key=:k", {"k": key})[0][0]


def _exec_multi(db_url, statements):
    engine = create_engine(db_url)
    try:
        with engine.connect() as c:
            for stmt in statements:
                c.execute(text(stmt[0]), stmt[1])
            c.commit()
    finally:
        engine.dispose()


def test_episode_spec_set_mismatch_fails_commit(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V13, url)
    component = _seed_active_component(url, "sb")
    env = _seed_core(url)
    cohort = _seed_cohort(url, env)
    _open_cohort(url, cohort)
    opp = _seed_open_opportunity(
        url, env, cohort, component["market"], "opp-b"
    )["opportunity"]
    ep = _next_id(url, "forecast_episodes")
    # 只插入 1 个 episode spec（component 有 2 个）→ commit 报 missing。
    with pytest.raises(Exception, match="v2_episode_spec_(missing|extra)"):
        _exec_multi(url, [
            (
                "INSERT INTO trading.forecast_episodes "
                "(id, episode_key, decision_opportunity_id, component_version_id, "
                " strategy_version_id, objective_contract_id, trigger, cutoff_at, horizon, "
                " experiment_variant) VALUES "
                "(:id, :k, :opp, :cv, :strat, :obj, 'frame', now(), 'res', 'control')",
                {"id": ep, "k": "f" * 64, "opp": opp,
                 "cv": component["component_version"], "strat": env["strat"],
                 "obj": env["obj"]},
            ),
            (
                "INSERT INTO trading.episode_contract_specs "
                "(episode_id, contract_spec_id) VALUES (:e, :s)",
                {"e": ep, "s": component["specs"][0]},
            ),
        ])


def test_gate_and_opportunity_append_only(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V13, url)
    env = _seed_core(url)
    cohort = _seed_cohort(url, env)
    _open_cohort(url, cohort)
    market = _seed_market(url)
    screening = _seed_confirmed_screening(
        url, cohort, market, env["obj"]
    )["screening"]
    with pytest.raises(Exception, match="v2_gate_order_g0_r0"):
        _execute(
            url,
            "INSERT INTO trading.gate_decisions "
            "(gate, target_kind, target_id, input_hash, policy_hash, "
            " version_manifest_id, result, committed_at) "
            "VALUES ('R0', 'screening', :s, :ih, :ph, :vm, 'SELECT', now())",
            {"s": screening, "ih": "3" * 64, "ph": "4" * 64,
             "vm": env["rel"]},
        )
    _execute(
        url,
        "INSERT INTO trading.gate_decisions "
        "(gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, "
        " result, committed_at) "
        "VALUES ('G0', 'screening', :s, :ih, :ph, :vm, 'PASS', now())",
        {"s": screening, "ih": "1" * 64, "ph": "2" * 64, "vm": env["rel"]},
    )
    with pytest.raises(Exception, match="v2_immutable_row:gate_decisions"):
        _execute(
            url,
            "UPDATE trading.gate_decisions SET result='PREDICTION_RESEARCH_ONLY', "
            "reason_code='changed' WHERE gate='G0' AND target_id=:s",
            {"s": screening},
        )
    _execute(
        url,
        "INSERT INTO trading.gate_decisions "
        "(gate, target_kind, target_id, input_hash, policy_hash, version_manifest_id, "
        " result, committed_at) "
        "VALUES ('R0', 'screening', :s, :ih, :ph, :vm, 'SELECT', now())",
        {"s": screening, "ih": "3" * 64, "ph": "4" * 64, "vm": env["rel"]},
    )
    _execute(url, "INSERT INTO trading.decision_opportunities (opportunity_key, cohort_id, chain_type, objective_contract_id, strategy_version_id, source_screening_episode_id, triggered_at) VALUES ('opp-1', :c, 'DECISION', :obj, :strat, :screening, now())", {"c": cohort, "obj": env["obj"], "strat": env["strat"], "screening": screening})
    opp = _query(url, "SELECT id FROM trading.decision_opportunities WHERE opportunity_key='opp-1'")[0][0]
    with pytest.raises(Exception, match="v2_information_snapshot_forbidden_key"):
        _execute(
            url,
            "INSERT INTO trading.information_snapshots "
            "(snapshot_key, opportunity_id, gate, content, content_hash) "
            "VALUES ('forbidden', :o, 'G1', "
            "'{\"nested\":{\"price\":\"0.5\"}}'::jsonb, :h)",
            {"o": opp, "h": "9" * 64},
        )
    # opportunity OPEN→PRE_COMMIT_TERMINAL 合法
    _execute(url, "UPDATE trading.decision_opportunities SET status='PRE_COMMIT_TERMINAL', terminal_reason='g1_fail', disposition='rejected' WHERE id=:o", {"o": opp})
    with pytest.raises(Exception, match="v2_opportunity_immutable"):
        _execute(url, "UPDATE trading.decision_opportunities SET status='ROUTED' WHERE id=:o", {"o": opp})


def test_downgrade_fail_closed_on_unknown_object(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V13, url)
    before = set(_trading_tables(url))
    _execute(url, "CREATE TABLE trading.unknown_intruder (id integer)")
    with pytest.raises(
        Exception,
        match="v2_wp01c_unknown_object|v2_wp01b_unknown_object|v2_trading_schema_not_empty",
    ):
        _run(command.downgrade, "b1000001", url)
    assert _query(url, "SELECT to_regclass('trading.unknown_intruder') IS NOT NULL") == [(True,)]
    assert set(_trading_tables(url)) == before | {"unknown_intruder"}
    assert _version(url) == [(V13,)]
