"""
WP-03 v2_0030 decision shadow migration —— 真 PostgreSQL 集成验收（Checkpoint B）。

覆盖：9 张 decision 表 roundtrip、pm_quote_bindings.trade_decision_id 强化、
gate_decisions G7A/G7B 与 forecast_episodes REVEALED/DECIDED allowlist 扩展、
trade_decisions 生命周期 guard（CREATED→QUOTE_BOUND→G7A→G7B→ACTION|WAIT|ABSTAIN）、
action_set legs 一致性 deferred trigger、downgrade fail-closed。
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V21 = "b1000021"
V30 = "b1000030"

DECISION_TABLES = [
    "market_relative_decisions", "discrepancy_reviews", "trade_decisions",
    "action_candidates", "resolution_cashflows", "action_sets",
    "action_set_legs", "underwriting_plans", "economic_action_intents",
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


def _insert_id(db_url, sql, params=None):
    """Execute a single INSERT ... RETURNING id inside its own committed transaction."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as c:
            return c.execute(text(sql), params or {}).scalar_one()
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
    """Build strategy_objective_contracts / strategy_versions / execution_spec_versions /
    capital_permission_manifests / runtime_config_versions / release_manifests. Returns id dict."""
    _execute(url, "INSERT INTO trading.strategy_objective_contracts (contract_key, version_no, content, schema_version, content_hash, status) VALUES ('obj-30', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "a" * 64})
    obj_id = _query(url, "SELECT id FROM trading.strategy_objective_contracts WHERE contract_key='obj-30'")[0][0]
    _execute(url, "INSERT INTO trading.strategy_versions (strategy_key, version_no, content, schema_version, content_hash, status) VALUES ('strat-30', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "b" * 64})
    strat_id = _query(url, "SELECT id FROM trading.strategy_versions WHERE strategy_key='strat-30'")[0][0]
    _execute(url, "INSERT INTO trading.capital_permission_manifests (name, mode, capability, limits, evaluation_capital, authorized_capital, content_hash, status) VALUES ('perm-30', 'shadow', '{}'::jsonb, '{}'::jsonb, 0, 0, :h, 'active')", {"h": "c" * 64})
    cap_id = _query(url, "SELECT id FROM trading.capital_permission_manifests WHERE name='perm-30'")[0][0]
    _execute(url, "INSERT INTO trading.runtime_config_versions (config_key, version_no, content, schema_version, content_hash, status) VALUES ('cfg-30', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "d" * 64})
    cfg_id = _query(url, "SELECT id FROM trading.runtime_config_versions WHERE config_key='cfg-30'")[0][0]
    _execute(url, "INSERT INTO trading.execution_spec_versions (spec_key, version_no, content, schema_version, content_hash, status) VALUES ('exec-30', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "e" * 64})
    exec_id = _query(url, "SELECT id FROM trading.execution_spec_versions WHERE spec_key='exec-30'")[0][0]
    _execute(url, "INSERT INTO trading.release_manifests (release_name, config_version_id, strategy_version_id, execution_spec_version_id, capital_permission_manifest_id, git_sha, image_digest, db_revision, total_hash, status) VALUES ('rel-30', :cfg, :strat, :exec, :cap, 'abc', 'img', 'b1000030', :h, 'active')", {"cfg": cfg_id, "strat": strat_id, "exec": exec_id, "cap": cap_id, "h": "f" * 64})
    rel_id = _query(url, "SELECT id FROM trading.release_manifests WHERE release_name='rel-30'")[0][0]
    return {"obj": obj_id, "strat": strat_id, "rel": rel_id, "exec": exec_id, "cap": cap_id}


def _seed_decision_deps(url, env, *, episode_id=1):
    """Seed minimal episode/submission/lease rows under replica so trade_decisions
    FKs to forecast_episodes / forecast_submissions / forecast_leases resolve.
    The decision lifecycle guard remains real (origin mode)."""
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            c.execute(text(
                "INSERT INTO trading.evaluation_cohorts "
                "(id,cohort_key,status,objective_contract_id,strategy_version_id,release_manifest_id,policy_hashes,seed_hash) "
                "VALUES (900003,:ck,'OPEN',:obj,:strat,:rel,'{}'::jsonb,:h) ON CONFLICT (id) DO NOTHING"
            ), {"ck": f"cohort-{episode_id}", "obj": env["obj"], "strat": env["strat"], "rel": env["rel"], "h": "1" * 64})
            c.execute(text(
                "INSERT INTO trading.decision_opportunities "
                "(id,opportunity_key,cohort_id,chain_type,objective_contract_id,strategy_version_id,triggered_at) "
                "VALUES (:id,:k,900003,'DECISION',:obj,:strat,now()) ON CONFLICT (id) DO NOTHING"
            ), {"id": 900000 + episode_id, "k": f"opp-{episode_id}", "obj": env["obj"], "strat": env["strat"]})
            c.execute(
                text(
                    "INSERT INTO trading.forecast_episodes "
                    "(id, episode_key, decision_opportunity_id, component_version_id, "
                    " strategy_version_id, objective_contract_id, trigger, cutoff_at, horizon, "
                    " experiment_variant, status, cognition_status) VALUES "
                    "(:id, :key, :opp, 900002, :strat, :obj, 'contract', now(), "
                    " 'resolution', 'champion', 'BLIND_COMMITTED', 'COMMITTED')"
                ),
                {"id": episode_id, "opp": 900000 + episode_id, "key": f"{episode_id:064x}", "strat": env["strat"], "obj": env["obj"]},
            )
            sub_id = c.execute(
                text(
                    "INSERT INTO trading.forecast_submissions "
                    "(episode_id, submission_key, Q, U, forecast_input_manifest_id, "
                    " contract_schema_prior_evidence_hash, algorithm_hash, status, committed_at) VALUES "
                    "(:ep, :key, '{\"w0\":\"1\"}'::jsonb, '[{\"w0\":\"1\"}]'::jsonb, "
                    " 900004, :h, :h, 'BLIND_COMMITTED', now()) RETURNING id"
                ),
                {"ep": episode_id, "key": f"sub-{episode_id}", "h": "b" * 64},
            ).scalar_one()
            lease_id = c.execute(
                text(
                    "INSERT INTO trading.forecast_leases "
                    "(submission_id, valid_until, invalidation_conditions, evidence_hash, "
                    " schema_hash, spec_hash) VALUES "
                    "(:sub, now()+interval '1 day', '{}'::jsonb, :h, :h, :h) RETURNING id"
                ),
                {"sub": sub_id, "h": "c" * 64},
            ).scalar_one()
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return {"episode": episode_id, "sub": sub_id, "lease": lease_id}
    finally:
        engine.dispose()


def _insert_trade_decision(url, env, deps, *, status="CREATED", decision_key=None):
    key = decision_key or (uuid4().hex + uuid4().hex)
    sql = (
        "INSERT INTO trading.trade_decisions "
        "(decision_key, episode_id, forecast_submission_id, forecast_lease_id, "
        " objective_contract_id, strategy_version_id, release_manifest_id, "
        " execution_spec_version_id, capital_permission_manifest_id, "
        " experiment_variant, status, trigger_at, input_hash) VALUES "
        "(:key, :ep, :sub, :lease, :obj, :strat, :rel, :exec, :cap, "
        " 'champion', :status, :trigger_at, :hash) RETURNING id"
    )
    return _insert_id(
        url,
        sql,
        {
            "key": key,
            "ep": deps["episode"], "sub": deps["sub"], "lease": deps["lease"],
            "obj": env["obj"], "strat": env["strat"], "rel": env["rel"],
            "exec": env["exec"], "cap": env["cap"],
            "status": status,
            "trigger_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "hash": "a" * 64,
        },
    )


def _update_decision(url, decision_id, *, status=None, quote_bound_at=None,
                     decided_at=None, output_hash=None, episode_id=None):
    sets, params = [], {"id": decision_id}
    if status is not None:
        sets.append("status=:status"); params["status"] = status
    if quote_bound_at is not None:
        sets.append("quote_bound_at=:qb"); params["qb"] = quote_bound_at
    if decided_at is not None:
        sets.append("decided_at=:dd"); params["dd"] = decided_at
    if output_hash is not None:
        sets.append("output_hash=:oh"); params["oh"] = output_hash
    if episode_id is not None:
        sets.append("episode_id=:ep"); params["ep"] = episode_id
    _execute(url, f"UPDATE trading.trade_decisions SET {', '.join(sets)} WHERE id=:id", params)


def _seed_terminal_decision(url, env, deps, *, decision_key=None, status="ACTION"):
    """Seed a terminal identity under replica for tests of downstream table guards."""
    qb = datetime.now(timezone.utc)
    dd = qb + timedelta(minutes=1)
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            decision = c.execute(
                text(
                    "INSERT INTO trading.trade_decisions "
                    "(decision_key, episode_id, forecast_submission_id, forecast_lease_id, "
                    " objective_contract_id, strategy_version_id, release_manifest_id, "
                    " execution_spec_version_id, capital_permission_manifest_id, "
                    " experiment_variant, status, selected_action_type, trigger_at, quote_bound_at, "
                    " decided_at, input_hash, output_hash, reason_code) VALUES "
                    "(:key, :ep, :sub, :lease, :obj, :strat, :rel, :exec, :cap, "
                    " 'champion', :status, :selected, :trigger_at, :qb, :dd, :hash, :oh, :reason) RETURNING id"
                ),
                {
                    "key": decision_key or (uuid4().hex + uuid4().hex),
                    "ep": deps["episode"], "sub": deps["sub"], "lease": deps["lease"],
                    "obj": env["obj"], "strat": env["strat"], "rel": env["rel"],
                    "exec": env["exec"], "cap": env["cap"],
                    "trigger_at": qb - timedelta(hours=1), "qb": qb, "dd": dd,
                    "hash": "a" * 64, "oh": "d" * 64,
                    "status": status,
                    "selected": "BUY_TOKEN" if status == "ACTION" else None,
                    "reason": "fixture" if status == "ABSTAIN" else None,
                },
            ).scalar_one()
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return decision
    finally:
        engine.dispose()


def _seed_leg_fk_targets(url, *, episode_id=1):
    """Seed pm_markets/pm_tokens/contract_specs under replica so action_set_legs FKs resolve."""
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            mid = c.execute(text("INSERT INTO trading.pm_markets (gamma_market_id, condition_id) VALUES ('m-30', 'c-30') RETURNING id")).scalar_one()
            token = c.execute(text("INSERT INTO trading.pm_tokens (token_id, market_id, outcome_index) VALUES ('t-30', :m, 0) RETURNING id"), {"m": mid}).scalar_one()
            cspec = c.execute(text("INSERT INTO trading.contract_specs (contract_key, version_no, snapshot_id, kc_resolution_states, token_ids, token_count, state_count, compiler_version, schema_version, status, content_hash) VALUES ('cs-30', 1, 900001, '[\"YES\"]'::jsonb, '{}'::jsonb, 1, 1, 'v1', 1, 'pass', :h) RETURNING id"), {"h": "e" * 64}).scalar_one()
            c.execute(text("INSERT INTO trading.episode_contract_specs (episode_id,contract_spec_id) VALUES (:ep,:cs)"), {"ep": episode_id, "cs": cspec})
            c.execute(text("INSERT INTO trading.payout_functions (contract_spec_id,pm_token_id,token_version_id,outcome_index,function_ir,test_vectors,algorithm_hash,content_hash) VALUES (:cs,:t,900099,0,'{\"YES\":\"1\"}'::jsonb,'{}'::jsonb,:h,:h)"), {"cs": cspec, "t": token, "h": "9" * 64})
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return {"cs": cspec, "token": token}
    finally:
        engine.dispose()


def test_literal_empty_roundtrip_and_reinforcement(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V30, url)
    assert _version(url) == [(V30,)]
    assert set(DECISION_TABLES) <= set(_trading_tables(url))

    # pm_quote_bindings 强化
    quote_cols = {row[0] for row in _query(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='trading' AND table_name='pm_quote_bindings'",
    )}
    assert "trade_decision_id" in quote_cols

    # gate_decisions allowlist 扩展（G7A/G7B）
    gate_ck = _query(url, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_gate_decisions_gate_known'")
    assert gate_ck and "G7A" in gate_ck[0][0]

    # forecast_episodes status allowlist 扩展（REVEALED/DECIDED）
    ep_ck = _query(url, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_forecast_episodes_status_known'")
    assert ep_ck and "REVEALED" in ep_ck[0][0]

    _run(command.downgrade, V21, url)
    assert _version(url) == [(V21,)]
    assert set(DECISION_TABLES).isdisjoint(set(_trading_tables(url)))
    quote_cols2 = {row[0] for row in _query(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='trading' AND table_name='pm_quote_bindings'",
    )}
    assert "trade_decision_id" not in quote_cols2
    gate_ck2 = _query(url, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_gate_decisions_gate_known'")
    assert gate_ck2 and "G7A" not in gate_ck2[0][0] and "G6" in gate_ck2[0][0]
    ep_ck2 = _query(url, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_forecast_episodes_status_known'")
    assert ep_ck2 and "REVEALED" not in ep_ck2[0][0]

    _run(command.upgrade, V30, url)
    assert _version(url) == [(V30,)]


def test_trade_decision_lifecycle_guard(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V30, url)
    env = _seed_core(url)
    deps = _seed_decision_deps(url, env)

    # INSERT 必须 CREATED 起步
    d1 = _insert_trade_decision(url, env, deps)
    assert _query(url, "SELECT status FROM trading.trade_decisions WHERE id=:id", {"id": d1}) == [("CREATED",)]
    with pytest.raises(Exception, match="v2_trade_decision_initial_state_invalid"):
        _insert_trade_decision(url, env, deps, status="WAIT")

    # CREATED→QUOTE_BOUND（带 quote_bound_at）合法
    qb = datetime.now(timezone.utc)
    _update_decision(url, d1, status="QUOTE_BOUND", quote_bound_at=qb)
    assert _query(
        url, "SELECT status, quote_bound_at IS NOT NULL FROM trading.trade_decisions WHERE id=:id", {"id": d1}
    ) == [("QUOTE_BOUND", True)]

    # 直接 CREATED→G7A 被 guard 拒绝
    d2 = _insert_trade_decision(url, env, deps)
    with pytest.raises(Exception, match="v2_trade_decision_immutable"):
        _update_decision(url, d2, status="G7A")

    # 状态路径本身合法，但缺 exact quote/gates/action evidence 的 terminal 必须 fail-closed。
    dd = qb + timedelta(minutes=1)
    _update_decision(url, d2, status="QUOTE_BOUND", quote_bound_at=qb)
    _update_decision(url, d2, status="G7A")
    _update_decision(url, d2, status="G7B")
    with pytest.raises(Exception, match="v2_trade_decision_required_tokens_missing|v2_trade_decision_gate_evidence_missing"):
        _update_decision(url, d2, status="ACTION", decided_at=dd, output_hash="b" * 64)
    assert _query(url, "SELECT status FROM trading.trade_decisions WHERE id=:id", {"id": d2}) == [("G7B",)]

    # 终态后再改 status 被拒
    terminal = _seed_terminal_decision(url, env, deps)
    with pytest.raises(Exception, match="v2_trade_decision_terminal_immutable"):
        _update_decision(url, terminal, status="WAIT")

    # identity 不可改
    d3 = _insert_trade_decision(url, env, deps)
    with pytest.raises(Exception, match="v2_trade_decision_identity_(?:immutable|invalid)"):
        _update_decision(url, d3, episode_id=deps["episode"] + 100)


def test_action_set_legs_consistency(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V30, url)
    env = _seed_core(url)
    deps = _seed_decision_deps(url, env)
    decision = _seed_terminal_decision(url, env, deps)

    # ACTION 必须同事务带 ≥1 leg，否则 deferred trigger 在 commit 报错
    with pytest.raises(Exception, match="v2_action_set_action_requires_leg"):
        _exec_multi(url, [
            (
                "INSERT INTO trading.action_sets (action_set_key, trade_decision_id, disposition, action_set_hash) "
                "VALUES (:k, :d, 'ACTION', :h)",
                {"k": "as-act-noleg", "d": decision, "h": "a" * 64},
            ),
        ])

    targets = _seed_leg_fk_targets(url)
    # ACTION + 1 leg 同事务 → 提交成功
    _exec_multi(url, [
        (
            "INSERT INTO trading.action_sets (action_set_key, trade_decision_id, disposition, action_set_hash) "
            "VALUES (:k, :d, 'ACTION', :h)",
            {"k": "as-act-leg", "d": decision, "h": "b" * 64},
        ),
        (
            "INSERT INTO trading.action_set_legs (action_set_id, contract_spec_id, token_id, leg_role, quantity, signed_quantity, entry_vwap) "
            "SELECT id, :cs, :t, 'open', 10, 10, 0.5 FROM trading.action_sets WHERE action_set_key=:askey",
            {"cs": targets["cs"], "t": targets["token"], "askey": "as-act-leg"},
        ),
    ])

    # WAIT 必须带 wake_condition 或 recheck_at
    wait_decision = _seed_terminal_decision(url, env, deps, status="WAIT")
    with pytest.raises(Exception, match="ck_action_sets_wait_wake"):
        _execute(url, "INSERT INTO trading.action_sets (action_set_key, trade_decision_id, disposition, action_set_hash) VALUES ('as-wait-bad', :d, 'WAIT', :h)", {"d": wait_decision, "h": "c" * 64})
    _execute(url, "INSERT INTO trading.action_sets (action_set_key, trade_decision_id, disposition, wake_condition, action_set_hash) VALUES ('as-wait-ok', :d, 'WAIT', 'wake', :h)", {"d": wait_decision, "h": "c" * 64})

    # ABSTAIN 必须带 reason_code
    abstain_decision = _seed_terminal_decision(url, env, deps, status="ABSTAIN")
    with pytest.raises(Exception, match="ck_action_sets_abstain_reason"):
        _execute(url, "INSERT INTO trading.action_sets (action_set_key, trade_decision_id, disposition, action_set_hash) VALUES ('as-abstain-bad', :d, 'ABSTAIN', :h)", {"d": abstain_decision, "h": "d" * 64})
    _execute(url, "INSERT INTO trading.action_sets (action_set_key, trade_decision_id, disposition, reason_code, action_set_hash) VALUES ('as-abstain-ok', :d, 'ABSTAIN', 'r', :h)", {"d": abstain_decision, "h": "d" * 64})


def test_downgrade_fail_closed_on_unknown_object(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V30, url)
    before = set(_trading_tables(url))
    _execute(url, "CREATE TABLE trading.unknown_intruder_0030 (id integer)")
    with pytest.raises(
        Exception,
        match="v2_wp03_unknown_object|v2_wp02_ai_unknown_object|v2_wp02_unknown_object|v2_wp01c_unknown_object|v2_trading_schema_not_empty",
    ):
        _run(command.downgrade, "b1000001", url)
    assert _query(url, "SELECT to_regclass('trading.unknown_intruder_0030') IS NOT NULL") == [(True,)]
    assert set(_trading_tables(url)) == before | {"unknown_intruder_0030"}
    assert _version(url) == [(V30,)]
