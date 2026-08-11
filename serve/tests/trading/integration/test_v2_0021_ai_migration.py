"""
WP-02 v2_0021 AI observability migration —— 真 PostgreSQL 集成验收（Checkpoint B §8.1）。

覆盖：3 分区表 + 分区子表 roundtrip、model_role_bindings typed/versioned 可逆强化
（legacy 反填 + 唯一键 + 约束）、artifact_lineage_edges.relation allowlist 扩展、downgrade
fail-closed。
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V20 = "b1000020"
V21 = "b1000021"

AI_TABLES = ["ai_invocations", "ai_tool_calls", "ai_validation_results"]


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


def test_literal_empty_roundtrip_and_reinforcement(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V21, url)
    assert _version(url) == [(V21,)]
    tables = set(_trading_tables(url))
    assert set(AI_TABLES) <= tables
    # 分区子表已建（父表 PARTITION BY RANGE）
    for table in AI_TABLES:
        rows = _query(url, "SELECT count(*) FROM pg_inherits i JOIN pg_class p ON p.oid=i.inhparent JOIN pg_namespace pn ON pn.oid=p.relnamespace WHERE pn.nspname='trading' AND p.relname=:t", {"t": table})
        assert rows[0][0] >= 1, f"{table} has no partitions"
    # model_role_bindings 强化
    cols = {row[0] for row in _query(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='trading' AND table_name='model_role_bindings'",
    )}
    assert {"provider", "route", "network_policy", "allowed_tools", "allowed_domains",
            "capability", "binding_version"} <= cols
    assert any("uq_model_role_bindings_strategy_role_version" == row[0] for row in _query(
        url, "SELECT conname FROM pg_constraint WHERE conrelid='trading.model_role_bindings'::regclass"
    ))
    # artifact_lineage_edges.relation 扩展
    rel_ck = _query(
        url,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='ck_artifact_lineage_edges_relation_known'",
    )
    assert rel_ck and "READS" in rel_ck[0][0] and "PROJECTS_TO" in rel_ck[0][0]

    _run(command.downgrade, V20, url)
    assert _version(url) == [(V20,)]
    assert set(AI_TABLES).isdisjoint(set(_trading_tables(url)))
    cols2 = {row[0] for row in _query(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='trading' AND table_name='model_role_bindings'",
    )}
    assert "binding_version" not in cols2 and "provider" not in cols2
    assert any("uq_model_role_bindings_strategy_role" == row[0] for row in _query(
        url, "SELECT conname FROM pg_constraint WHERE conrelid='trading.model_role_bindings'::regclass"
    ))
    rel_ck2 = _query(
        url,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='ck_artifact_lineage_edges_relation_known'",
    )
    assert rel_ck2 and "READS" not in rel_ck2[0][0]

    _run(command.upgrade, V21, url)
    assert _version(url) == [(V21,)]


def test_existing_model_role_bindings_upgrade_preserves_data(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V20, url)
    _execute(url, "INSERT INTO trading.strategy_versions (strategy_key, version_no, content, schema_version, content_hash, status) VALUES ('strat-legacy', 1, '{}'::jsonb, 1, :h, 'draft')", {"h": "b" * 64})
    strat = _query(url, "SELECT id FROM trading.strategy_versions WHERE strategy_key='strat-legacy'")[0][0]
    _execute(url, "INSERT INTO trading.model_role_bindings (strategy_version_id, role, model_ref, content_hash) VALUES (:s, 'planner_prior', 'deepseek-v4-pro', :h)", {"s": strat, "h": "a" * 64})
    binding_id = _query(url, "SELECT id FROM trading.model_role_bindings WHERE role='planner_prior'")[0][0]

    _run(command.upgrade, V21, url)
    upgraded = _query(
        url,
        "SELECT strategy_version_id, role, model_ref, content_hash, provider, route, "
        "network_policy, binding_version FROM trading.model_role_bindings WHERE id=:id",
        {"id": binding_id},
    )[0]
    assert upgraded == (strat, "planner_prior", "deepseek-v4-pro", "a" * 64,
                        "__legacy__", "__legacy__", "NONE", 0)

    _run(command.downgrade, V20, url)
    assert _query(
        url,
        "SELECT strategy_version_id, role, model_ref, content_hash "
        "FROM trading.model_role_bindings WHERE id=:id",
        {"id": binding_id},
    ) == [(strat, "planner_prior", "deepseek-v4-pro", "a" * 64)]


def test_ai_tables_append_only_and_partition_required(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V21, url)
    # 无 default partition：不存在的月份插入失败
    with pytest.raises(Exception, match="no partition of relation"):
        _execute(
            url,
            "INSERT INTO trading.ai_invocations "
            "(occurred_at, invocation_key, episode_id, stage, role, attempt_no, "
            " requested_provider, requested_route, requested_model, network_policy, "
            " context_class, taint_report, input_manifest, input_manifest_hash, "
            " pricing_snapshot) VALUES "
            "(date '1999-01-01', 'k', 1, 'g6', 'planner_prior', 1, "
            " 'deepseek', 'direct', 'deepseek-v4-pro', 'NONE', 'PRIOR', '{}'::jsonb, "
            " '{}'::jsonb, :h, '{}'::jsonb)",
            {"h": "a" * 64},
        )
    # 当前月份分区可插入；生命周期 guard 允许 PLANNED→ACCEPTED，但 terminal 后禁改/禁删
    now = datetime.now(timezone.utc)
    _execute(
        url,
        "INSERT INTO trading.ai_invocations "
        "(occurred_at, invocation_key, episode_id, stage, role, attempt_no, "
        " requested_provider, requested_route, requested_model, network_policy, "
        " context_class, taint_report, input_manifest, input_manifest_hash, "
        " pricing_snapshot, lifecycle_state) VALUES "
        "(:at, 'k', 1, 'g6', 'planner_prior', 1, "
        " 'deepseek', 'direct', 'deepseek-v4-pro', 'NONE', 'PRIOR', '{}'::jsonb, "
        " '{}'::jsonb, :h, '{}'::jsonb, 'PLANNED')",
        {"at": now, "h": "a" * 64},
    )
    _execute(
        url,
        "UPDATE trading.ai_invocations SET lifecycle_state='ACCEPTED', accepted_at=now() "
        "WHERE invocation_key='k'",
    )
    # terminal 行禁改 lifecycle / identity
    with pytest.raises(Exception, match="v2_ai_invocation_terminal_immutable"):
        _execute(
            url,
            "UPDATE trading.ai_invocations SET lifecycle_state='FAILED' "
            "WHERE invocation_key='k'",
        )
    with pytest.raises(Exception, match="v2_ai_invocation_identity_immutable"):
        _execute(
            url,
            "UPDATE trading.ai_invocations SET role='researcher' "
            "WHERE invocation_key='k'",
        )
    with pytest.raises(Exception, match="v2_ai_invocation_immutable"):
        _execute(
            url,
            "DELETE FROM trading.ai_invocations WHERE invocation_key='k'",
        )


def test_downgrade_fail_closed_on_unknown_object(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V21, url)
    before = set(_trading_tables(url))
    _execute(url, "CREATE TABLE trading.unknown_intruder_0021 (id integer)")
    with pytest.raises(
        Exception,
        match="v2_wp02_ai_unknown_object|v2_wp02_unknown_object|v2_wp01c_unknown_object|v2_trading_schema_not_empty",
    ):
        _run(command.downgrade, "b1000001", url)
    assert _query(url, "SELECT to_regclass('trading.unknown_intruder_0021') IS NOT NULL") == [(True,)]
    assert set(_trading_tables(url)) == before | {"unknown_intruder_0021"}
    assert _version(url) == [(V21,)]
