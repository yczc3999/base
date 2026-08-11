"""
WP-03 v2_0031 shadow ledger migration —— 真 PostgreSQL 集成验收（Checkpoint C）。

覆盖：6 张表 roundtrip、ledger balance deferred trigger（POSTED 前每 asset 组平衡且
≥2 组）、execution lifecycle guard（PENDING→PARTIAL|FILLED|REJECTED|FAILED）、
POSTED ledger 行 append-only immutable、downgrade fail-closed。
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
V30 = "b1000030"
V31 = "b1000031"

SHADOW_TABLES = [
    "executions", "positions", "position_lots",
    "ledger_transactions", "ledger_postings", "operating_cost_entries",
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
    _execute(url, "INSERT INTO trading.strategy_objective_contracts (contract_key, version_no, content, schema_version, content_hash, status) VALUES ('obj-31', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "a" * 64})
    obj_id = _query(url, "SELECT id FROM trading.strategy_objective_contracts WHERE contract_key='obj-31'")[0][0]
    _execute(url, "INSERT INTO trading.strategy_versions (strategy_key, version_no, content, schema_version, content_hash, status) VALUES ('strat-31', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "b" * 64})
    strat_id = _query(url, "SELECT id FROM trading.strategy_versions WHERE strategy_key='strat-31'")[0][0]
    _execute(url, "INSERT INTO trading.capital_permission_manifests (name, mode, capability, limits, evaluation_capital, authorized_capital, content_hash, status) VALUES ('perm-31', 'shadow', '{}'::jsonb, '{}'::jsonb, 0, 0, :h, 'active')", {"h": "c" * 64})
    cap_id = _query(url, "SELECT id FROM trading.capital_permission_manifests WHERE name='perm-31'")[0][0]
    _execute(url, "INSERT INTO trading.runtime_config_versions (config_key, version_no, content, schema_version, content_hash, status) VALUES ('cfg-31', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "d" * 64})
    cfg_id = _query(url, "SELECT id FROM trading.runtime_config_versions WHERE config_key='cfg-31'")[0][0]
    _execute(url, "INSERT INTO trading.execution_spec_versions (spec_key, version_no, content, schema_version, content_hash, status) VALUES ('exec-31', 1, '{}'::jsonb, 1, :h, 'active')", {"h": "e" * 64})
    exec_id = _query(url, "SELECT id FROM trading.execution_spec_versions WHERE spec_key='exec-31'")[0][0]
    _execute(url, "INSERT INTO trading.release_manifests (release_name, config_version_id, strategy_version_id, execution_spec_version_id, capital_permission_manifest_id, git_sha, image_digest, db_revision, total_hash, status) VALUES ('rel-31', :cfg, :strat, :exec, :cap, 'abc', 'img', 'b1000031', :h, 'active')", {"cfg": cfg_id, "strat": strat_id, "exec": exec_id, "cap": cap_id, "h": "f" * 64})
    rel_id = _query(url, "SELECT id FROM trading.release_manifests WHERE release_name='rel-31'")[0][0]
    return {"obj": obj_id, "strat": strat_id, "rel": rel_id, "exec": exec_id, "cap": cap_id}


def _seed_decision_deps(url, env, *, episode_id=1):
    """Seed minimal episode/submission/lease rows under replica so trade_decisions FKs resolve."""
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            c.execute(
                text(
                    "INSERT INTO trading.forecast_episodes "
                    "(id, episode_key, decision_opportunity_id, component_version_id, "
                    " strategy_version_id, objective_contract_id, trigger, cutoff_at, horizon, "
                    " experiment_variant, status, cognition_status) VALUES "
                    "(:id, :key, 900001, 900002, :strat, :obj, 'contract', now(), "
                    " 'resolution', 'champion', 'ROUTED', 'PENDING')"
                ),
                {"id": episode_id, "key": f"{episode_id:064x}", "strat": env["strat"], "obj": env["obj"]},
            )
            sub_id = c.execute(
                text(
                    "INSERT INTO trading.forecast_submissions "
                    "(episode_id, submission_key, Q, U, forecast_input_manifest_id, "
                    " contract_schema_prior_evidence_hash, algorithm_hash) VALUES "
                    "(:ep, :key, '{\"w0\":\"1\"}'::jsonb, '[{\"w0\":\"1\"}]'::jsonb, "
                    " 900004, :h, :h) RETURNING id"
                ),
                {"ep": episode_id, "key": f"sub-{episode_id}", "h": "b" * 64},
            ).scalar_one()
            lease_id = c.execute(
                text(
                    "INSERT INTO trading.forecast_leases "
                    "(submission_id, valid_until, invalidation_conditions, evidence_hash, "
                    " schema_hash, spec_hash) VALUES "
                    "(:sub, now(), '{}'::jsonb, :h, :h, :h) RETURNING id"
                ),
                {"sub": sub_id, "h": "c" * 64},
            ).scalar_one()
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return {"episode": episode_id, "sub": sub_id, "lease": lease_id}
    finally:
        engine.dispose()


def _seed_trade_decision(url, env, deps):
    """Insert a CREATED trade_decision and return its id."""
    return _insert_id(
        url,
        "INSERT INTO trading.trade_decisions "
        "(decision_key, episode_id, forecast_submission_id, forecast_lease_id, "
        " objective_contract_id, strategy_version_id, release_manifest_id, "
        " execution_spec_version_id, capital_permission_manifest_id, "
        " experiment_variant, status, trigger_at, input_hash) VALUES "
        "(:key, :ep, :sub, :lease, :obj, :strat, :rel, :exec, :cap, "
        " 'champion', 'CREATED', :trigger_at, :hash) RETURNING id",
        {
            "key": uuid4().hex + uuid4().hex,
            "ep": deps["episode"], "sub": deps["sub"], "lease": deps["lease"],
            "obj": env["obj"], "strat": env["strat"], "rel": env["rel"],
            "exec": env["exec"], "cap": env["cap"],
            "trigger_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "hash": "a" * 64,
        },
    )


def _seed_execution_deps(url, decision_id):
    """Seed pm_markets/pm_tokens/contract_specs/action_sets/action_set_legs/
    economic_action_intents under replica so executions FKs resolve."""
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            mid = c.execute(text("INSERT INTO trading.pm_markets (gamma_market_id, condition_id) VALUES ('m-31', 'c-31') RETURNING id")).scalar_one()
            token = c.execute(text("INSERT INTO trading.pm_tokens (token_id, market_id, outcome_index) VALUES ('t-31', :m, 0) RETURNING id"), {"m": mid}).scalar_one()
            cspec = c.execute(text("INSERT INTO trading.contract_specs (contract_key, version_no, snapshot_id, kc_resolution_states, token_ids, token_count, state_count, compiler_version, schema_version, status, content_hash) VALUES ('cs-31', 1, 900001, '[\"YES\"]'::jsonb, '{}'::jsonb, 1, 1, 'v1', 1, 'pass', :h) RETURNING id"), {"h": "e" * 64}).scalar_one()
            aset = c.execute(text("INSERT INTO trading.action_sets (action_set_key, trade_decision_id, disposition, action_set_hash) VALUES ('as-31', :d, 'ACTION', :h) RETURNING id"), {"d": decision_id, "h": "f" * 64}).scalar_one()
            leg = c.execute(text("INSERT INTO trading.action_set_legs (action_set_id, contract_spec_id, token_id, leg_role, quantity, signed_quantity, entry_vwap) VALUES (:as, :cs, :t, 'open', 10, 10, 0.5) RETURNING id"), {"as": aset, "cs": cspec, "t": token}).scalar_one()
            intent = c.execute(text("INSERT INTO trading.economic_action_intents (intent_key, intent_hash, trade_decision_id, action_set_id, status, preflight) VALUES ('intent-31', :h, :d, :as, 'PLANNED', '{}'::jsonb) RETURNING id"), {"h": "a" * 64, "d": decision_id, "as": aset}).scalar_one()
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return {"intent": intent, "leg": leg, "cs": cspec, "token": token}
    finally:
        engine.dispose()


def _insert_execution(url, deps, *, status="PENDING", key=None):
    sql = (
        "INSERT INTO trading.executions "
        "(execution_key, economic_action_intent_id, action_set_leg_id, contract_spec_id, "
        " token_id, fill_role, quantity, portfolio_namespace, status) VALUES "
        "(:k, :intent, :leg, :cs, :t, 'open', 10, 'ns', :status) RETURNING id"
    )
    return _insert_id(
        url,
        sql,
        {
            "k": key or uuid4().hex + uuid4().hex,
            "intent": deps["intent"], "leg": deps["leg"],
            "cs": deps["cs"], "t": deps["token"],
            "status": status,
        },
    )


def test_literal_empty_roundtrip(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V31, url)
    assert _version(url) == [(V31,)]
    assert set(SHADOW_TABLES) <= set(_trading_tables(url))

    _run(command.downgrade, V30, url)
    assert _version(url) == [(V30,)]
    assert set(SHADOW_TABLES).isdisjoint(set(_trading_tables(url)))

    _run(command.upgrade, V31, url)
    assert _version(url) == [(V31,)]


def test_ledger_balance_deferred_trigger(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V31, url)

    # 平衡双资产组：POSTED 提交成功
    tx1 = _insert_id(url, "INSERT INTO trading.ledger_transactions (transaction_key, kind, portfolio_namespace) VALUES ('lt-1', 'FILL', 'ns') RETURNING id", {})
    _exec_multi(url, [
        ("INSERT INTO trading.ledger_postings (transaction_id, posting_no, asset_type, asset_key, amount, counterparty) VALUES (:tx, 0, 'CASH', 'usdc', -50, 'broker')", {"tx": tx1}),
        ("INSERT INTO trading.ledger_postings (transaction_id, posting_no, asset_type, asset_key, amount, counterparty) VALUES (:tx, 1, 'CASH', 'usdc', 50, 'self')", {"tx": tx1}),
        ("INSERT INTO trading.ledger_postings (transaction_id, posting_no, asset_type, asset_key, amount, counterparty) VALUES (:tx, 2, 'TOKEN', 't-1', 100, 'broker')", {"tx": tx1}),
        ("INSERT INTO trading.ledger_postings (transaction_id, posting_no, asset_type, asset_key, amount, counterparty) VALUES (:tx, 3, 'TOKEN', 't-1', -100, 'self')", {"tx": tx1}),
        ("UPDATE trading.ledger_transactions SET status='POSTED', posted_at=now() WHERE id=:tx", {"tx": tx1}),
    ])
    assert _query(url, "SELECT status FROM trading.ledger_transactions WHERE id=:id", {"id": tx1}) == [("POSTED",)]

    # 不平衡（CASH 只有一条）→ POSTED 报错
    tx2 = _insert_id(url, "INSERT INTO trading.ledger_transactions (transaction_key, kind, portfolio_namespace) VALUES ('lt-2', 'FILL', 'ns') RETURNING id", {})
    with pytest.raises(Exception, match="v2_ledger_unbalanced|v2_ledger_requires_two_asset_groups"):
        _exec_multi(url, [
            ("INSERT INTO trading.ledger_postings (transaction_id, posting_no, asset_type, asset_key, amount, counterparty) VALUES (:tx, 0, 'CASH', 'usdc', -50, 'broker')", {"tx": tx2}),
            ("UPDATE trading.ledger_transactions SET status='POSTED', posted_at=now() WHERE id=:tx", {"tx": tx2}),
        ])
    assert _query(url, "SELECT status FROM trading.ledger_transactions WHERE id=:id", {"id": tx2}) == [("PENDING",)]

    # 单资产组（平衡但缺第二组）→ POSTED 报错
    tx3 = _insert_id(url, "INSERT INTO trading.ledger_transactions (transaction_key, kind, portfolio_namespace) VALUES ('lt-3', 'FILL', 'ns') RETURNING id", {})
    with pytest.raises(Exception, match="v2_ledger_requires_two_asset_groups"):
        _exec_multi(url, [
            ("INSERT INTO trading.ledger_postings (transaction_id, posting_no, asset_type, asset_key, amount, counterparty) VALUES (:tx, 0, 'CASH', 'usdc', -50, 'broker')", {"tx": tx3}),
            ("INSERT INTO trading.ledger_postings (transaction_id, posting_no, asset_type, asset_key, amount, counterparty) VALUES (:tx, 1, 'CASH', 'usdc', 50, 'self')", {"tx": tx3}),
            ("UPDATE trading.ledger_transactions SET status='POSTED', posted_at=now() WHERE id=:tx", {"tx": tx3}),
        ])
    assert _query(url, "SELECT status FROM trading.ledger_transactions WHERE id=:id", {"id": tx3}) == [("PENDING",)]


def test_execution_lifecycle_guard(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V31, url)
    env = _seed_core(url)
    deps = _seed_decision_deps(url, env)
    decision = _seed_trade_decision(url, env, deps)
    edeps = _seed_execution_deps(url, decision)

    # INSERT 必须 PENDING（filled_quantity=0, vwap NULL）
    e1 = _insert_execution(url, edeps)
    assert _query(url, "SELECT status FROM trading.executions WHERE id=:id", {"id": e1}) == [("PENDING",)]
    with pytest.raises(Exception, match="v2_execution_initial_state_invalid"):
        _insert_execution(url, edeps, status="FILLED")

    # PENDING→PARTIAL|FILLED|REJECTED|FAILED 合法
    _execute(url, "UPDATE trading.executions SET status='PARTIAL', filled_quantity=5, unfilled_reason='partial' WHERE id=:id", {"id": e1})
    assert _query(url, "SELECT status FROM trading.executions WHERE id=:id", {"id": e1}) == [("PARTIAL",)]

    e2 = _insert_execution(url, edeps)
    _execute(url, "UPDATE trading.executions SET status='FILLED', filled_quantity=10 WHERE id=:id", {"id": e2})

    e3 = _insert_execution(url, edeps)
    _execute(url, "UPDATE trading.executions SET status='REJECTED', filled_quantity=0, unfilled_reason='rejected' WHERE id=:id", {"id": e3})

    e4 = _insert_execution(url, edeps)
    _execute(url, "UPDATE trading.executions SET status='FAILED', filled_quantity=0, unfilled_reason='failed' WHERE id=:id", {"id": e4})

    # PENDING 改非法外值被 guard 拒绝
    e5 = _insert_execution(url, edeps)
    with pytest.raises(Exception, match="v2_execution_immutable"):
        _execute(url, "UPDATE trading.executions SET status='GARBAGE' WHERE id=:id", {"id": e5})
    assert _query(url, "SELECT status FROM trading.executions WHERE id=:id", {"id": e5}) == [("PENDING",)]

    # 终态后再改 status 被拒
    with pytest.raises(Exception, match="v2_execution_terminal_immutable"):
        _execute(url, "UPDATE trading.executions SET status='PARTIAL' WHERE id=:id", {"id": e2})

    # identity 不可改
    with pytest.raises(Exception, match="v2_execution_identity_immutable"):
        _execute(url, "UPDATE trading.executions SET execution_key=:k WHERE id=:id", {"k": uuid4().hex + uuid4().hex, "id": e3})


def test_posted_ledger_immutable(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V31, url)

    tx = _insert_id(url, "INSERT INTO trading.ledger_transactions (transaction_key, kind, portfolio_namespace) VALUES ('lt-posted', 'FILL', 'ns') RETURNING id", {})
    _exec_multi(url, [
        ("INSERT INTO trading.ledger_postings (transaction_id, posting_no, asset_type, asset_key, amount, counterparty) VALUES (:tx, 0, 'CASH', 'usdc', -50, 'broker')", {"tx": tx}),
        ("INSERT INTO trading.ledger_postings (transaction_id, posting_no, asset_type, asset_key, amount, counterparty) VALUES (:tx, 1, 'CASH', 'usdc', 50, 'self')", {"tx": tx}),
        ("INSERT INTO trading.ledger_postings (transaction_id, posting_no, asset_type, asset_key, amount, counterparty) VALUES (:tx, 2, 'TOKEN', 't-1', 100, 'broker')", {"tx": tx}),
        ("INSERT INTO trading.ledger_postings (transaction_id, posting_no, asset_type, asset_key, amount, counterparty) VALUES (:tx, 3, 'TOKEN', 't-1', -100, 'self')", {"tx": tx}),
        ("UPDATE trading.ledger_transactions SET status='POSTED', posted_at=now() WHERE id=:tx", {"tx": tx}),
    ])

    # ledger_postings 是 append-only：UPDATE/DELETE 被 immutable guard 拒绝。
    with pytest.raises(Exception, match="v2_immutable_row:ledger_postings"):
        _execute(url, "UPDATE trading.ledger_postings SET amount=amount+1 WHERE transaction_id=:id", {"id": tx})
    with pytest.raises(Exception, match="v2_immutable_row:ledger_postings"):
        _execute(url, "DELETE FROM trading.ledger_postings WHERE transaction_id=:id", {"id": tx})
    # ledger_transactions 有生命周期 guard：POSTED 后禁改/禁删。
    with pytest.raises(Exception, match="v2_ledger_transaction_identity_immutable"):
        _execute(url, "UPDATE trading.ledger_transactions SET kind='REVERSAL' WHERE id=:id", {"id": tx})
    with pytest.raises(Exception, match="v2_ledger_transaction_immutable"):
        _execute(url, "UPDATE trading.ledger_transactions SET status='PENDING' WHERE id=:id", {"id": tx})
    with pytest.raises(Exception, match="v2_ledger_transaction_immutable"):
        _execute(url, "DELETE FROM trading.ledger_transactions WHERE id=:id", {"id": tx})


def test_downgrade_fail_closed_on_unknown_object(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V31, url)
    before = set(_trading_tables(url))
    _execute(url, "CREATE TABLE trading.unknown_intruder_0031 (id integer)")
    with pytest.raises(
        Exception,
        match="v2_wp03_ledger_unknown_object|v2_wp03_unknown_object|v2_trading_schema_not_empty",
    ):
        _run(command.downgrade, "b1000001", url)
    assert _query(url, "SELECT to_regclass('trading.unknown_intruder_0031') IS NOT NULL") == [(True,)]
    assert set(_trading_tables(url)) == before | {"unknown_intruder_0031"}
    assert _version(url) == [(V31,)]
