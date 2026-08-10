"""
WP-01B v2_0011 market stream / book evidence migration —— 真 PostgreSQL 集成验收（Checkpoint C §5.3）。

前置：``V2_TEST_ADMIN_DATABASE_URL`` 存在，否则整模块 skip。在独立 ``pm_v2_test_*``
临时库执行，从不在管理/业务库跑 downgrade。

覆盖：
1. literal-empty roundtrip：b1000010 → b1000011 → b1000010；7 张 stream 表 + 32 个日分区；
2. 分区语义：4 张证据表真实 RANGE 分区、无 default partition、写入落正确分区、
   缺分区（未来第 8 天）写入失败；
3. epoch 状态机 guard：CONNECTING→SYNCING→LIVE→STALE|CLOSED 合法，跳转/重复/CLOSED 后改拒绝；
4. 复合 FK：source_event_index → batch、book_levels → checkpoint；
5. append-only guard：source/book/quote binding 禁 UPDATE/DELETE；
6. book_current ``observed_at`` CAS 单调；
7. quote binding 非零价 + 不 crossed 约束；
8. 未知对象 downgrade fail-closed。
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V10 = "b1000010"
V11 = "b1000011"
V2 = "b1000002"

STREAM_TABLES = [
    "pm_connection_epochs",
    "pm_source_event_batches",
    "pm_source_event_index",
    "pm_book_checkpoints",
    "pm_book_levels",
    "pm_book_current",
    "pm_quote_bindings",
]
PARTITIONED_PARENTS = [
    "pm_source_event_batches",
    "pm_source_event_index",
    "pm_book_checkpoints",
    "pm_book_levels",
]

_sha = lambda c: c * 64  # noqa: E731


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


def _seed_artifact(db_url, marker="a"):
    sha = hashlib.sha256(marker.encode()).hexdigest()
    locator = f"cas/v1/sha256/{sha[:2]}/{sha[2:4]}/{sha}.raw"
    _execute(
        db_url,
        "INSERT INTO trading.artifact_objects "
        "(sha256, original_size, stored_size, mime, compression, storage_driver, "
        " storage_version, locator) "
        "VALUES (:sha, 2, 2, 'application/json', 'none', 'local', 'cas/v1', :locator) "
        "ON CONFLICT DO NOTHING",
        {"sha": sha, "locator": locator},
    )
    return _query(
        db_url, "SELECT id FROM trading.artifact_objects WHERE sha256=:sha LIMIT 1", {"sha": sha}
    )[0][0]


def _seed_release(db_url):
    version_tables = (
        ("runtime_config_versions", "config_key", "cfg-test"),
        ("strategy_versions", "strategy_key", "strategy-test"),
        ("execution_spec_versions", "spec_key", "execution-test"),
    )
    ids = []
    for table, key_column, key in version_tables:
        _execute(
            db_url,
            f"INSERT INTO trading.{table} "
            f"({key_column}, version_no, content, schema_version, content_hash, status) "
            "VALUES (:key, 1, '{}'::jsonb, 1, :hash, 'draft') ON CONFLICT DO NOTHING",
            {"key": key, "hash": _sha(key[0])},
        )
        ids.append(_query(
            db_url, f"SELECT id FROM trading.{table} WHERE {key_column}=:key", {"key": key}
        )[0][0])
    _execute(
        db_url,
        "INSERT INTO trading.capital_permission_manifests "
        "(name, mode, capability, limits, evaluation_capital, authorized_capital, "
        " content_hash, status) VALUES "
        "('capital-test', 'shadow', '{}'::jsonb, '{}'::jsonb, 0, 0, :hash, 'draft')",
        {"hash": _sha("p")},
    )
    capital_id = _query(
        db_url, "SELECT id FROM trading.capital_permission_manifests WHERE name='capital-test'"
    )[0][0]
    _execute(
        db_url,
        "INSERT INTO trading.release_manifests "
        "(release_name, config_version_id, strategy_version_id, execution_spec_version_id, "
        " capital_permission_manifest_id, git_sha, image_digest, db_revision, total_hash, status) "
        "VALUES ('release-test', :cfg, :strategy, :execution, :capital, :sha, "
        " 'sha256:test', 'b1000011', :total, 'draft')",
        {
            "cfg": ids[0], "strategy": ids[1], "execution": ids[2], "capital": capital_id,
            "sha": _sha("g"), "total": _sha("t"),
        },
    )
    return _query(
        db_url, "SELECT id FROM trading.release_manifests WHERE release_name='release-test'"
    )[0][0]


def _version(db_url):
    return _query(db_url, "SELECT version_num FROM public.alembic_version")


def _trading_tables(db_url):
    return [r[0] for r in _query(
        db_url, "SELECT tablename FROM pg_tables WHERE schemaname='trading' ORDER BY 1"
    )]


def _seed_epoch(db_url, *, shard_key="shard-1"):
    release_id = _seed_release(db_url)
    _execute(
        db_url,
        "INSERT INTO trading.pm_connection_epochs "
        "(shard_key, provider, config_release_id, started_at) "
        "VALUES (:shard, 'market_ws', :release, :t)",
        {"shard": shard_key, "release": release_id, "t": datetime.now(timezone.utc)},
    )
    return _query(
        db_url, "SELECT id FROM trading.pm_connection_epochs ORDER BY id DESC LIMIT 1"
    )[0][0]


def _seed_checkpoint(db_url, token_id, marker, received_at, epoch_id=None):
    epoch_id = epoch_id or _seed_epoch(db_url, shard_key=f"shard-{marker}")
    artifact_id = _seed_artifact(db_url, marker)
    _execute(
        db_url,
        "INSERT INTO trading.pm_book_checkpoints "
        "(token_id, connection_epoch_id, source_kind, book_hash, best_bid, best_ask, "
        " raw_artifact_id, completeness, received_at) "
        "VALUES (:token, :epoch, 'rest_full', :hash, 0.50, 0.52, :artifact, true, :at)",
        {
            "token": token_id, "epoch": epoch_id, "hash": _sha(marker),
            "artifact": artifact_id, "at": received_at,
        },
    )
    return _query(
        db_url,
        "SELECT id FROM trading.pm_book_checkpoints "
        "WHERE token_id=:token AND received_at=:at",
        {"token": token_id, "at": received_at},
    )[0][0]


# ---------------- 1. literal-empty roundtrip ----------------

def test_literal_empty_roundtrip(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V11, url)
    assert _version(url) == [(V11,)]
    tables = set(_trading_tables(url))
    assert set(STREAM_TABLES) <= tables
    # 4 张分区父表 × (当前日 + 未来 7 日) = 32 个子分区；无 default partition
    partitions = [t for t in tables if any(
        t.startswith(parent + "_") for parent in PARTITIONED_PARENTS
    )]
    assert len(partitions) == 32, f"expected 32 daily partitions, got {len(partitions)}"

    _run(command.downgrade, V10, url)
    assert _version(url) == [(V10,)]
    assert set(STREAM_TABLES).isdisjoint(set(_trading_tables(url)))

    _run(command.upgrade, V11, url)
    assert _version(url) == [(V11,)]


def test_no_default_partition_and_ranges_real(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V11, url)
    for parent in PARTITIONED_PARENTS:
        rows = _query(
            url,
            "SELECT p.partstrat, c.relname FROM pg_partitioned_table p "
            "JOIN pg_class c ON c.oid=p.partrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='trading' AND c.relname=:t",
            {"t": parent},
        )
        assert rows and rows[0][0] == "r", f"{parent}: 必须是 RANGE 分区: {rows}"
        # 无 default partition
        children = _query(
            url,
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_inherits i ON i.inhrelid=c.oid "
            "JOIN pg_class p ON p.oid=i.inhparent "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='trading' AND p.relname=:t AND c.relname NOT LIKE '%_default%'",
            {"t": parent},
        )
        assert len(children) == 8, f"{parent}: 预期 8 个日分区, got {len(children)}"


def test_missing_future_partition_fails_fail_closed(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V11, url)
    epoch_id = _seed_epoch(url)
    # 未来第 9 天（超出预建 8 天窗口）→ 无分区 → INSERT 失败（fail-closed，无 default partition）
    far_future = datetime.now(timezone.utc) + timedelta(days=9)
    with pytest.raises(Exception, match="no partition of relation"):
        _execute(
            url,
            "INSERT INTO trading.pm_source_event_batches "
            "(connection_epoch_id, batch_no, first_receive_seq, last_receive_seq, "
            " first_received_at, last_received_at, event_count, batch_hash, "
            " prev_batch_hash, raw_artifact_id, raw_artifact_ref, received_at) "
            "VALUES (:e, 0, 1, 1, :t, :t, 1, :h, NULL, :aid, :a, :t)",
            {
                "e": epoch_id, "t": far_future, "h": _sha("a"),
                "aid": _seed_artifact(url, "b"), "a": _sha("b"),
            },
        )


# ---------------- 2. epoch 状态机 ----------------

def test_epoch_status_machine_guarded(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V11, url)
    eid = _seed_epoch(url)

    def status():
        return _query(url, "SELECT status FROM trading.pm_connection_epochs WHERE id=:e", {"e": eid})[0][0]

    assert status() == "CONNECTING"
    with pytest.raises(Exception, match="v2_epoch_identity_immutable"):
        _execute(
            url,
            "UPDATE trading.pm_connection_epochs SET config_release_id=config_release_id + 1, "
            "status='SYNCING', synced_at=now() WHERE id=:e",
            {"e": eid},
        )
    with pytest.raises(Exception, match="config_release_id|null value"):
        _execute(
            url,
            "INSERT INTO trading.pm_connection_epochs (shard_key, provider, started_at) "
            "VALUES ('no-release', 'market_ws', now())",
        )
    # 合法链
    _execute(url, "UPDATE trading.pm_connection_epochs SET status='SYNCING', synced_at=now() WHERE id=:e", {"e": eid})
    _execute(url, "UPDATE trading.pm_connection_epochs SET status='LIVE', live_at=now() WHERE id=:e", {"e": eid})
    _execute(url, "UPDATE trading.pm_connection_epochs SET status='STALE', stale_at=now() WHERE id=:e", {"e": eid})
    # STALE 不可复活；重连必须创建新 epoch，且 STALE 不占 active unique 槽。
    with pytest.raises(Exception, match="v2_epoch_transition_invalid"):
        _execute(url, "UPDATE trading.pm_connection_epochs SET status='SYNCING' WHERE id=:e", {"e": eid})
    replacement = _seed_epoch(url)
    assert replacement != eid
    _execute(url, "UPDATE trading.pm_connection_epochs SET status='CLOSED', closed_at=now() WHERE id=:e", {"e": eid})
    assert status() == "CLOSED"
    # 终态后不可变
    with pytest.raises(Exception, match="v2_epoch_transition_invalid|v2_epoch_noop_update"):
        _execute(url, "UPDATE trading.pm_connection_epochs SET status='LIVE' WHERE id=:e", {"e": eid})
    with pytest.raises(Exception, match="v2_epoch_immutable"):
        _execute(url, "DELETE FROM trading.pm_connection_epochs WHERE id=:e", {"e": eid})


def test_epoch_shard_single_active(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V11, url)
    release_id = _seed_release(url)
    _execute(
        url,
        "INSERT INTO trading.pm_connection_epochs "
        "(shard_key, provider, config_release_id, started_at) "
        "VALUES ('s1', 'market_ws', :release, now())",
        {"release": release_id},
    )
    with pytest.raises(Exception, match="uq_pm_connection_epochs_active_shard"):
        _execute(
            url,
            "INSERT INTO trading.pm_connection_epochs "
            "(shard_key, provider, config_release_id, started_at) "
            "VALUES ('s1', 'market_ws', :release, now())",
            {"release": release_id},
        )
    # 关闭后可再建新 epoch
    _execute(
        url,
        "UPDATE trading.pm_connection_epochs SET status='CLOSED', closed_at=now() "
        "WHERE shard_key='s1'",
    )
    _execute(
        url,
        "INSERT INTO trading.pm_connection_epochs "
        "(shard_key, provider, config_release_id, started_at) "
        "VALUES ('s1', 'market_ws', :release, now())",
        {"release": release_id},
    )


# ---------------- 3. 复合 FK + append-only + CAS + quote 约束 ----------------

def test_composite_fk_and_append_only(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V11, url)
    eid = _seed_epoch(url)
    now = datetime.now(timezone.utc)

    _execute(
        url,
        "INSERT INTO trading.pm_source_event_batches "
        "(connection_epoch_id, batch_no, first_receive_seq, last_receive_seq, "
        " first_received_at, last_received_at, event_count, batch_hash, "
        " prev_batch_hash, raw_artifact_id, raw_artifact_ref, received_at) "
        "VALUES (:e, 0, 1, 2, :t, :t, 2, :h, NULL, :aid, :a, :t)",
        {
            "e": eid, "t": now, "h": _sha("a"),
            "aid": _seed_artifact(url, "b"), "a": _sha("b"),
        },
    )
    batch = _query(
        url,
        "SELECT id FROM trading.pm_source_event_batches WHERE connection_epoch_id=:e",
        {"e": eid},
    )[0][0]
    _execute(
        url,
        "INSERT INTO trading.pm_source_event_index "
        "(batch_id, received_at, source, kind, connection_epoch_id, local_receive_seq, "
        " payload_hash, batch_ordinal, token_id) "
        "VALUES (:b, :t, 'market_ws', 'book', :e, 1, :h, 0, 'tok-1')",
        {"b": batch, "t": now, "e": eid, "h": _sha("c")},
    )
    with pytest.raises(Exception, match="v2_immutable_row:pm_source_event_index"):
        _execute(
            url, "UPDATE trading.pm_source_event_index SET kind='price_change' WHERE batch_id=:b",
            {"b": batch},
        )
    with pytest.raises(Exception, match="fk_pm_source_event_index_batch"):
        _execute(
            url,
            "INSERT INTO trading.pm_source_event_index "
            "(batch_id, received_at, source, kind, payload_hash, batch_ordinal) "
            "VALUES (999999, :t, 'gamma', 'page', :h, 0)",
            {"t": now, "h": _sha("d")},
        )

    _execute(
        url,
        "INSERT INTO trading.pm_book_checkpoints "
        "(token_id, connection_epoch_id, source_kind, book_hash, best_bid, best_ask, "
        " tick_size, min_order_size, raw_artifact_id, completeness, received_at) "
        "VALUES ('tok-1', :e, 'rest_full', :h, 0.50, 0.52, 0.01, 1, :a, true, :t)",
        {"e": eid, "t": now, "h": _sha("e"), "a": _seed_artifact(url, "e")},
    )
    cp = _query(
        url,
        "SELECT id FROM trading.pm_book_checkpoints WHERE token_id='tok-1'",
    )[0][0]
    _execute(
        url,
        "INSERT INTO trading.pm_book_levels (checkpoint_id, received_at, side, price, size, ordinal) "
        "VALUES (:c, :t, 'bid', 0.50, 100, 0)",
        {"c": cp, "t": now},
    )
    with pytest.raises(Exception, match="v2_immutable_row:pm_book_levels"):
        _execute(
            url, "DELETE FROM trading.pm_book_levels WHERE checkpoint_id=:c", {"c": cp}
        )


def test_event_index_does_not_claim_partition_local_global_uniqueness(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V11, url)
    assert _query(
        url,
        "SELECT count(*) FROM pg_constraint WHERE connamespace='trading'::regnamespace "
        "AND conname='uq_pm_source_event_index_epoch_seq'",
    ) == [(0,)]
    assert _query(
        url,
        "SELECT indexdef LIKE 'CREATE INDEX %' FROM pg_indexes "
        "WHERE schemaname='trading' AND indexname='ix_pm_source_event_index_epoch_seq'",
    ) == [(True,)]
    with pytest.raises(Exception, match="ck_pm_source_event_index_http_status_range"):
        # CHECK 在 FK 前也能独立验证 attempt receipt 范围。
        _execute(
            url,
            "INSERT INTO trading.pm_source_event_index "
            "(batch_id, received_at, source, kind, payload_hash, http_status) "
            "VALUES (999999, now(), 'gamma', 'attempt', :h, 99)",
            {"h": _sha("r")},
        )


def test_book_current_cas_and_quote_binding_constraints(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V11, url)
    now = datetime.now(timezone.utc)
    eid = _seed_epoch(url)
    cp1 = _seed_checkpoint(url, "tok-1", "i", now, eid)
    cp2 = _seed_checkpoint(url, "tok-2", "j", now, eid)
    cp3 = _seed_checkpoint(url, "tok-3", "k", now, eid)

    _execute(
        url,
        "INSERT INTO trading.pm_book_current "
        "(token_id, connection_epoch_id, checkpoint_id, checkpoint_received_at, "
        " best_bid, best_ask, validity, observed_at) "
        "VALUES ('tok-1', :e, :cp, :received, 0.50, 0.52, 'VALID', :observed)",
        {
            "e": eid, "cp": cp1, "received": now,
            "observed": now - timedelta(seconds=10),
        },
    )
    _execute(
        url,
        "UPDATE trading.pm_book_current SET best_ask=0.53, observed_at=:t WHERE token_id='tok-1'",
        {"t": now},
    )
    with pytest.raises(Exception, match="v2_book_current_stale_overwrite"):
        _execute(
            url,
            "UPDATE trading.pm_book_current SET best_ask=0.51, observed_at=:t "
            "WHERE token_id='tok-1'",
            {"t": now - timedelta(seconds=100)},
        )

    # quote binding：非零价 + 不 crossed
    _execute(
        url,
        "INSERT INTO trading.pm_quote_bindings "
        "(token_id, checkpoint_id, checkpoint_received_at, best_bid, best_ask, "
        " price_convention, as_of, received_at, "
        " staleness_policy_ref, stale_at) "
        "VALUES ('tok-1', :cp, :t, 0.50, 0.52, 'usd-cents', :t, :t, 'policy-1', :t2)",
        {"cp": cp1, "t": now, "t2": now + timedelta(seconds=60)},
    )
    with pytest.raises(Exception, match="ck_pm_quote_bindings_not_crossed"):
        _execute(
            url,
            "INSERT INTO trading.pm_quote_bindings "
            "(token_id, checkpoint_id, checkpoint_received_at, best_bid, best_ask, "
            " price_convention, as_of, received_at, "
            " staleness_policy_ref, stale_at) "
            "VALUES ('tok-2', :cp, :t, 0.53, 0.52, 'usd-cents', :t, :t, 'policy-1', :t2)",
            {"cp": cp2, "t": now, "t2": now + timedelta(seconds=60)},
        )
    with pytest.raises(Exception, match="ck_pm_quote_bindings_price_positive"):
        _execute(
            url,
            "INSERT INTO trading.pm_quote_bindings "
            "(token_id, checkpoint_id, checkpoint_received_at, best_bid, best_ask, "
            " price_convention, as_of, received_at, "
            " staleness_policy_ref, stale_at) "
            "VALUES ('tok-3', :cp, :t, 0, 0.01, 'usd-cents', :t, :t, 'policy-1', :t2)",
            {"cp": cp3, "t": now, "t2": now + timedelta(seconds=60)},
        )
    with pytest.raises(Exception, match="v2_immutable_row:pm_quote_bindings"):
        _execute(
            url,
            "UPDATE trading.pm_quote_bindings SET best_ask=0.54 WHERE token_id='tok-1'",
        )

    with pytest.raises(Exception, match="fk_pm_quote_bindings_checkpoint"):
        _execute(
            url,
            "INSERT INTO trading.pm_quote_bindings "
            "(token_id, checkpoint_id, checkpoint_received_at, best_bid, best_ask, "
            " price_convention, as_of, received_at, staleness_policy_ref, stale_at) "
            "VALUES ('ghost', 999999, :t, 0.40, 0.60, 'usd-cents', :t, :t, "
            " 'policy-1', :t2)",
            {"t": now, "t2": now + timedelta(seconds=60)},
        )


def test_downgrade_fail_closed_on_unknown_schema_object(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V11, url)
    before = set(_trading_tables(url))
    _execute(url, "CREATE TABLE trading.unknown_intruder (id integer)")
    with pytest.raises(Exception, match="v2_wp01b_unknown_object"):
        _run(command.downgrade, V2, url)
    assert _query(url, "SELECT to_regclass('trading.unknown_intruder') IS NOT NULL") == [(True,)]
    assert set(_trading_tables(url)) == before | {"unknown_intruder"}
    assert _version(url) == [(V11,)]
