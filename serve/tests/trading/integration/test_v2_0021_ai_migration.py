"""
WP-02 v2_0021 AI observability migration —— 真 PostgreSQL 集成验收（Checkpoint B §8.1）。

覆盖：3 分区表 + 分区子表 roundtrip、model_role_bindings typed/versioned 可逆强化
（legacy 反填 + 唯一键 + 约束）、artifact_lineage_edges.relation allowlist 扩展、downgrade
fail-closed。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

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


def _seed_ai_identity(
    db_url,
    *,
    episode_ids=(1,),
    role="planner_prior",
    provider="deepseek",
    route="direct",
    model="deepseek-v4-pro",
    network="NONE",
    tools="[]",
    domains="[]",
    variant="champion",
):
    """Seed an exact strategy/binding plus isolated episode rows for this migration boundary."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as c:
            # Recreate the strategy in the committed transaction.
            strategy = c.execute(
                text(
                    "INSERT INTO trading.strategy_versions "
                    "(strategy_key,version_no,content,schema_version,content_hash,status) "
                    "VALUES (:key,1,'{}'::jsonb,1,:hash,'draft') RETURNING id"
                ),
                {"key": f"ai-contract-{uuid4().hex}", "hash": "b" * 64},
            ).scalar_one()
            binding = c.execute(
                text(
                    "INSERT INTO trading.model_role_bindings "
                    "(strategy_version_id,role,provider,route,model_ref,network_policy,"
                    "allowed_tools,allowed_domains,capability,binding_version,content_hash) "
                    "VALUES (:strategy,:role,:provider,:route,:model,:network,"
                    "CAST(:tools AS jsonb),CAST(:domains AS jsonb),'{}'::jsonb,0,:hash) "
                    "RETURNING id"
                ),
                {
                    "strategy": strategy,
                    "role": role,
                    "provider": provider,
                    "route": route,
                    "model": model,
                    "network": network,
                    "tools": tools,
                    "domains": domains,
                    "hash": "a" * 64,
                },
            ).scalar_one()
            # Upstream workflow has its own integration coverage. Disable FK triggers only
            # while creating the referenced episode fixture; AI FKs/guards remain real.
            c.execute(text("SET LOCAL session_replication_role = replica"))
            for episode_id in episode_ids:
                c.execute(
                    text(
                        "INSERT INTO trading.forecast_episodes "
                        "(id,episode_key,decision_opportunity_id,component_version_id,"
                        "strategy_version_id,objective_contract_id,trigger,cutoff_at,horizon,"
                        "experiment_variant,status,cognition_status) VALUES "
                        "(:id,:key,900001,900002,:strategy,900003,'contract',now(),"
                        "'resolution',:variant,'ROUTED','PENDING')"
                    ),
                    {
                        "id": episode_id,
                        "key": f"{episode_id:064x}",
                        "strategy": strategy,
                        "variant": variant,
                    },
                )
            c.execute(text("SET LOCAL session_replication_role = origin"))
        return strategy, binding
    finally:
        engine.dispose()


def _register_artifacts(db_url, hashes):
    engine = create_engine(db_url)
    try:
        with engine.begin() as c:
            for sha in set(hashes):
                c.execute(
                    text(
                        "INSERT INTO trading.artifact_objects "
                        "(sha256,original_size,stored_size,mime,compression,storage_driver,"
                        "storage_version,locator) VALUES (:sha,0,0,'application/json','none',"
                        "'local','cas/v1',:locator) ON CONFLICT DO NOTHING"
                    ),
                    {
                        "sha": sha,
                        "locator": f"cas/v1/sha256/{sha[:2]}/{sha[2:4]}/{sha}.raw",
                    },
                )
    finally:
        engine.dispose()


def _seed_required_validators(db_url, invocation_key, *, hard_failure=False):
    invocation_id, occurred_at = _query(
        db_url,
        "SELECT id,occurred_at FROM trading.ai_invocations WHERE invocation_key=:key",
        {"key": invocation_key},
    )[0]
    rows = [
        ("json_parser", "hard", not hard_failure, "7" * 64),
        ("normalized_output", "hard", True, "8" * 64),
        ("secret_quarantine", "hard", True, "9" * 64),
        ("blind_taint", "hard", True, "a" * 64),
        ("probability_rollup", "soft", True, "b" * 64),
    ]
    _register_artifacts(db_url, [row[3] for row in rows])
    engine = create_engine(db_url)
    try:
        with engine.begin() as c:
            for name, severity, passed, details_hash in rows:
                c.execute(
                    text(
                        "INSERT INTO trading.ai_validation_results "
                        "(occurred_at,invocation_id,invocation_occurred_at,validator_name,"
                        "validator_version,passed,severity,details_artifact_hash) VALUES "
                        "(:at,:id,:at,:name,'v1',:passed,:severity,:hash)"
                    ),
                    {
                        "at": occurred_at,
                        "id": invocation_id,
                        "name": name,
                        "passed": passed,
                        "severity": severity,
                        "hash": details_hash,
                    },
                )
    finally:
        engine.dispose()


def _insert_planned_invocation(
    db_url,
    *,
    occurred_at,
    invocation_key,
    episode_id=1,
    stage="g6",
    role="planner_prior",
    attempt_no=1,
    input_manifest="{}",
    model_role_binding_id=1,
    strategy_version_id=None,
    network_policy="NONE",
    allowed_tools="[]",
    allowed_domains="[]",
    provider="deepseek",
    route="direct",
    model="deepseek-v4-pro",
    cache_key_hash="c" * 64,
):
    _execute(
        db_url,
        "INSERT INTO trading.ai_invocations "
        "(occurred_at, invocation_key, episode_id, stage, role, attempt_no, "
        " requested_provider, requested_route, requested_model, network_policy, "
        " allowed_tools, allowed_domains, context_class, taint_report, input_manifest, input_manifest_hash, "
        " model_role_binding_id, strategy_version_id, cache_key_hash, pricing_snapshot, "
        " lifecycle_state, queued_at) VALUES "
        "(:at, :key, :episode, :stage, :role, :attempt, "
        " :provider, :route, :model, :network, CAST(:tools AS jsonb), CAST(:domains AS jsonb), "
        " 'PRIOR', '{}'::jsonb, CAST(:manifest AS jsonb), :h, :binding, :strategy, :cache_key, "
        " '{}'::jsonb, 'PLANNED', :at)",
        {
            "at": occurred_at,
            "key": invocation_key,
            "episode": episode_id,
            "stage": stage,
            "role": role,
            "attempt": attempt_no,
            "manifest": input_manifest,
            "h": "a" * 64,
            "binding": model_role_binding_id,
            "strategy": strategy_version_id,
            "network": network_policy,
            "tools": allowed_tools,
            "domains": allowed_domains,
            "provider": provider,
            "route": route,
            "model": model,
            "cache_key": cache_key_hash,
        },
    )


def _accept_invocation(
    db_url,
    invocation_key,
    *,
    seed_artifacts=True,
    seed_validators=True,
    tool_count=0,
    search_count=0,
):
    hashes = {f"h{i}": f"{i}" * 64 for i in range(1, 7)}
    if seed_artifacts:
        _register_artifacts(db_url, hashes.values())
    if seed_validators:
        _seed_required_validators(db_url, invocation_key)
    _execute(
        db_url,
        "UPDATE trading.ai_invocations SET "
        " lifecycle_state='ACCEPTED', result='accepted', accepted_at=now(), "
        " returned_provider='deepseek', returned_route='direct', "
        " returned_model='deepseek-v4-pro', prompt_version='planner_prior/v1', "
        " schema_version='planner_prior/v1', request_artifact_ref=:h1, "
        " prompt_artifact_ref=:h2, schema_artifact_ref=:h3, "
        " raw_response_artifact_ref=:h4, parsed_output_artifact_ref=:h5, "
        " normalized_output_artifact_ref=:h6, accepted_output_binding=:binding_ref, "
        " cache_key_hash=:cache_key, "
        " started_at=now(), response_at=now(), parsed_at=now(), "
        " validated_at=now(), completed_at=now(), tool_count=:tool_count, "
        " search_count=:search_count "
        "WHERE invocation_key=:key",
        {
            **hashes,
            "binding_ref": hashes["h6"],
            "cache_key": "c" * 64,
            "key": invocation_key,
            "tool_count": tool_count,
            "search_count": search_count,
        },
    )


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
    invocation_columns = {row[0] for row in _query(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='trading' AND table_name='ai_invocations'",
    )}
    assert "request_artifact_ref" in invocation_columns
    assert "cache_key_hash" in invocation_columns
    for child in ("ai_tool_calls", "ai_validation_results"):
        child_columns = {row[0] for row in _query(
            url,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='trading' AND table_name=:table",
            {"table": child},
        )}
        assert "invocation_occurred_at" in child_columns
    ai_constraints = {
        row[0]: row[1] for row in _query(
            url,
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid IN ('trading.ai_invocations'::regclass, "
            "'trading.ai_tool_calls'::regclass, 'trading.ai_validation_results'::regclass)",
        )
    }
    assert "request_artifact_ref" in ai_constraints["ck_ai_invocations_accepted_shape"]
    assert "fk_ai_invocations_episode" in ai_constraints
    assert "fk_ai_invocations_model_role_binding" in ai_constraints
    assert "invocation_occurred_at" in ai_constraints["fk_ai_tool_calls_invocation"]
    assert "invocation_occurred_at" in ai_constraints["fk_ai_validation_results_invocation"]
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
    strategy, binding = _seed_ai_identity(url)
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
    # 当前月份分区可插入；ACCEPTED 必须一次封齐证据 shape，terminal 后整行禁改/禁删。
    now = datetime.now(timezone.utc)
    _insert_planned_invocation(
        url, occurred_at=now, invocation_key="k",
        model_role_binding_id=binding, strategy_version_id=strategy,
    )
    with pytest.raises(Exception, match="v2_ai_invocation_artifact_missing|accepted_shape"):
        _execute(
            url,
            "UPDATE trading.ai_invocations SET lifecycle_state='ACCEPTED', "
            "result='accepted', accepted_at=now() WHERE invocation_key='k'",
        )
    assert _query(
        url,
        "SELECT lifecycle_state FROM trading.ai_invocations WHERE invocation_key='k'",
    ) == [("PLANNED",)]
    _accept_invocation(url, "k")

    # terminal 行任何列都不可回填/篡改，即使 lifecycle_state 不变。
    with pytest.raises(Exception, match="v2_ai_invocation_terminal_immutable"):
        _execute(
            url,
            "UPDATE trading.ai_invocations SET cost_estimated=cost_estimated+1 "
            "WHERE invocation_key='k'",
        )
    with pytest.raises(Exception, match="v2_ai_invocation_terminal_immutable"):
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


def test_ai_attempt_global_claim_and_failed_insert_rolls_back_claim(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V21, url)
    now = datetime.now(timezone.utc)
    strategy, binding = _seed_ai_identity(url, episode_ids=(41, 42, 43))

    _insert_planned_invocation(
        url,
        occurred_at=now,
        invocation_key="attempt-owner-a",
        episode_id=41,
        stage="g4",
        attempt_no=3,
        model_role_binding_id=binding,
        strategy_version_id=strategy,
    )


    # occurred_at 不属于逻辑 identity；跨时间/分区仍由非分区 claim 全局挡重。
    with pytest.raises(Exception, match="v2_ai_invocation_attempt_duplicate"):
        _insert_planned_invocation(
            url,
            occurred_at=now + timedelta(microseconds=1),
            invocation_key="attempt-owner-b",
            episode_id=41,
            stage="g4",
            attempt_no=3,
            model_role_binding_id=binding,
            strategy_version_id=strategy,
        )
    assert _query(
        url,
        "SELECT count(*) FROM trading.ai_invocations "
        "WHERE episode_id=41 AND stage='g4' AND role='planner_prior' "
        "AND experiment_variant='champion' AND attempt_no=3",
    ) == [(1,)]
    assert _query(
        url,
        "SELECT owner FROM trading.idempotency_claims "
        "WHERE scope='ai_invocation_attempt'",
    ) == [("attempt-owner-a",)]

    # 两个真实连接同时认领同一逻辑 attempt：唯一索引串行化，只允许一个提交。
    barrier = Barrier(2)

    def concurrent_claim(owner, at):
        barrier.wait(timeout=5)
        try:
            _insert_planned_invocation(
                url,
                occurred_at=at,
                invocation_key=owner,
                episode_id=43,
                stage="g5a",
                attempt_no=1,
                model_role_binding_id=binding,
                strategy_version_id=strategy,
            )
            return None
        except Exception as exc:  # 两个 worker 中必须恰有一个命中全局 claim。
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent_results = [
            future.result(timeout=10)
            for future in (
                pool.submit(concurrent_claim, "concurrent-a", now + timedelta(seconds=1)),
                pool.submit(
                    concurrent_claim,
                    "concurrent-b",
                    now + timedelta(seconds=1, microseconds=1),
                ),
            )
        ]
    assert sum(result is None for result in concurrent_results) == 1
    assert sum(
        result is not None and "v2_ai_invocation_attempt_duplicate" in result
        for result in concurrent_results
    ) == 1
    assert _query(
        url,
        "SELECT count(*) FROM trading.ai_invocations "
        "WHERE episode_id=43 AND stage='g5a' AND role='planner_prior' "
        "AND experiment_variant='champion' AND attempt_no=1",
    ) == [(1,)]

    # claim 与 invocation INSERT 同事务：后置 table CHECK 失败不能遗留幽灵 claim。
    with pytest.raises(Exception, match="ck_ai_invocations_input_manifest_object"):
        _insert_planned_invocation(
            url,
            occurred_at=now + timedelta(microseconds=2),
            invocation_key="bad-manifest",
            episode_id=42,
            stage="g4",
            attempt_no=1,
            input_manifest="[]",
            model_role_binding_id=binding,
            strategy_version_id=strategy,
        )
    assert _query(
        url,
        "SELECT count(*) FROM trading.idempotency_claims "
        "WHERE scope='ai_invocation_attempt' AND owner='bad-manifest'",
    ) == [(0,)]
    _insert_planned_invocation(
        url,
        occurred_at=now + timedelta(microseconds=3),
        invocation_key="good-after-rollback",
        episode_id=42,
        stage="g4",
        attempt_no=1,
        model_role_binding_id=binding,
        strategy_version_id=strategy,
    )

    # revision rollback removes only its own claim scope；re-upgrade can claim the same identity.
    _run(command.downgrade, V20, url)
    assert _query(
        url,
        "SELECT count(*) FROM trading.idempotency_claims "
        "WHERE scope='ai_invocation_attempt'",
    ) == [(0,)]
    _run(command.upgrade, V21, url)
    _execute(
        url,
        "INSERT INTO trading.model_role_bindings "
        "(strategy_version_id,role,provider,route,model_ref,network_policy,allowed_tools,"
        "allowed_domains,capability,binding_version,content_hash) VALUES "
        "(:strategy,'planner_prior','deepseek','direct','deepseek-v4-pro','NONE',"
        "'[]'::jsonb,'[]'::jsonb,'{}'::jsonb,1,:hash)",
        {"strategy": strategy, "hash": "d" * 64},
    )
    binding_after_roundtrip = _query(
        url,
        "SELECT id FROM trading.model_role_bindings WHERE strategy_version_id=:strategy "
        "AND role='planner_prior' AND binding_version=1",
        {"strategy": strategy},
    )[0][0]
    _insert_planned_invocation(
        url,
        occurred_at=now,
        invocation_key="attempt-owner-after-roundtrip",
        episode_id=41,
        stage="g4",
        attempt_no=3,
        model_role_binding_id=binding_after_roundtrip,
        strategy_version_id=strategy,
    )


def test_ai_identity_and_accepted_evidence_fail_closed(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V21, url)
    now = datetime.now(timezone.utc)
    strategy, binding = _seed_ai_identity(url, episode_ids=(11, 12, 13))

    with pytest.raises(Exception, match="v2_ai_invocation_binding_role_mismatch"):
        _insert_planned_invocation(
            url,
            occurred_at=now,
            invocation_key="wrong-role",
            episode_id=11,
            role="researcher",
            model_role_binding_id=binding,
            strategy_version_id=strategy,
        )

    other_strategy, other_binding = _seed_ai_identity(url, episode_ids=(21,))
    with pytest.raises(Exception, match="v2_ai_invocation_binding_strategy_mismatch"):
        _insert_planned_invocation(
            url,
            occurred_at=now,
            invocation_key="wrong-strategy",
            episode_id=11,
            attempt_no=2,
            model_role_binding_id=other_binding,
            strategy_version_id=other_strategy,
        )

    _insert_planned_invocation(
        url,
        occurred_at=now,
        invocation_key="missing-validators",
        episode_id=12,
        model_role_binding_id=binding,
        strategy_version_id=strategy,
    )
    with pytest.raises(Exception, match="v2_ai_invocation_required_validators_missing"):
        _accept_invocation(url, "missing-validators", seed_validators=False)
    assert _query(
        url,
        "SELECT lifecycle_state FROM trading.ai_invocations "
        "WHERE invocation_key='missing-validators'",
    ) == [("PLANNED",)]
    _accept_invocation(url, "missing-validators", seed_artifacts=False)

    # Child facts cannot be appended after the terminal evidence set is frozen.
    invocation_id, invocation_at = _query(
        url,
        "SELECT id,occurred_at FROM trading.ai_invocations "
        "WHERE invocation_key='missing-validators'",
    )[0]
    with pytest.raises(Exception, match="v2_ai_child_parent_terminal"):
        _execute(
            url,
            "INSERT INTO trading.ai_validation_results "
            "(occurred_at,invocation_id,invocation_occurred_at,validator_name,"
            "validator_version,passed,severity) VALUES "
            "(:at,:id,:at,'late-validator','v1',true,'soft')",
            {"at": invocation_at, "id": invocation_id},
        )

    _insert_planned_invocation(
        url,
        occurred_at=now + timedelta(microseconds=1),
        invocation_key="missing-tool-receipt",
        episode_id=13,
        model_role_binding_id=binding,
        strategy_version_id=strategy,
    )
    with pytest.raises(Exception, match="v2_ai_invocation_tool_count_mismatch"):
        _accept_invocation(
            url, "missing-tool-receipt", tool_count=1, search_count=1
        )


def test_ai_children_require_composite_parent_and_are_append_only(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V21, url)
    now = datetime.now(timezone.utc)
    strategy, binding = _seed_ai_identity(url)
    _insert_planned_invocation(
        url, occurred_at=now, invocation_key="parent",
        model_role_binding_id=binding, strategy_version_id=strategy,
    )
    invocation_id, invocation_at = _query(
        url,
        "SELECT id, occurred_at FROM trading.ai_invocations WHERE invocation_key='parent'",
    )[0]

    _execute(
        url,
        "INSERT INTO trading.ai_tool_calls "
        "(occurred_at, invocation_id, invocation_occurred_at, ordinal, tool_type, "
        " arguments, started_at, completed_at, status) VALUES "
        "(:at, :id, :parent_at, 0, 'web_search', '{}'::jsonb, :at, :at, 'COMPLETED')",
        {"at": now, "id": invocation_id, "parent_at": invocation_at},
    )
    _execute(
        url,
        "INSERT INTO trading.ai_validation_results "
        "(occurred_at, invocation_id, invocation_occurred_at, validator_name, "
        " passed, severity) VALUES (:at, :id, :parent_at, 'schema', true, 'hard')",
        {"at": now, "id": invocation_id, "parent_at": invocation_at},
    )
    with pytest.raises(Exception, match="fk_ai_tool_calls_invocation"):
        _execute(
            url,
            "INSERT INTO trading.ai_tool_calls "
            "(occurred_at, invocation_id, invocation_occurred_at, ordinal, tool_type, "
            " arguments, started_at, completed_at, status) VALUES "
            "(:at, 999999, :parent_at, 1, 'web_search', '{}'::jsonb, "
            " :at, :at, 'COMPLETED')",
            {"at": now, "parent_at": invocation_at},
        )
    with pytest.raises(Exception, match="fk_ai_validation_results_invocation"):
        _execute(
            url,
            "INSERT INTO trading.ai_validation_results "
            "(occurred_at, invocation_id, invocation_occurred_at, validator_name, "
            " passed, severity) VALUES "
            "(:at, :id, :wrong_at, 'orphan', true, 'hard')",
            {"at": now, "id": invocation_id, "wrong_at": invocation_at + timedelta(seconds=1)},
        )
    with pytest.raises(Exception, match="v2_immutable_row:ai_tool_calls"):
        _execute(url, "UPDATE trading.ai_tool_calls SET ordinal=2 WHERE invocation_id=:id", {"id": invocation_id})
    with pytest.raises(Exception, match="v2_immutable_row:ai_validation_results"):
        _execute(url, "DELETE FROM trading.ai_validation_results WHERE invocation_id=:id", {"id": invocation_id})


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
