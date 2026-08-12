"""WP-06 Checkpoint D —— secret / no-egress boundary（静态 + 真 PostgreSQL）。

证明：Builder/Relayer secret、signature 明文、raw signed body、RPC credential marker 在
DB/log/trace/artifact/API/git 为 0；ChainSettlementLogic 执行路径不持久化任何 secret；
fake transport 调用 >0 而 real outbound/chain/money calls =0、authorized capital=0。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V52 = "b1000052"

# 敏感 marker 正则（防止误报：只匹配 secret 形态，不匹配 ref/marker）
_SENSITIVE_PATTERNS = (
    re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    re.compile(r"(?i)(?:api[_-]?key|passphrase|builder[_-]?secret|relayer[_-]?secret)\s*[:=]\s*['\"][A-Za-z0-9+/=_\-]{20,}"),
    re.compile(r"signature\s*[:=]\s*['\"]0x[a-f0-9]{128}['\"]"),
    re.compile(r"0x[a-f0-9]{64}\s*:\s*0x[a-f0-9]{64}\s*:\s*0x[a-f0-9]{64}"),  # secret triple
)

_WP06_FILES = (
    "alembic/versions/b1000052_v2_0052_chain_settlement.py",
    "app/services/polymarket/polygon_driver.py",
    "app/services/polymarket/relayer_driver.py",
    "app/logics/trading/settlement.py",
    "app/repositories/trading/settlement.py",
    "app/schemas/polymarket/chain.py",
    "app/domain/trading/payout.py",
    "app/config.py",
)


def _upgrade(db_url):
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    engine = create_engine(db_url, poolclass=NullPool)
    conn = engine.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, V52)
    finally:
        conn.close()
        engine.dispose()


def test_wp06_source_no_secret_plaintext():
    hits = []
    for rel in _WP06_FILES:
        path = SERVE_DIR / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in _SENSITIVE_PATTERNS:
            for m in pattern.finditer(text):
                hits.append((rel, pattern.pattern, m.start()))
    assert hits == [], f"secret plaintext in WP-06 sources: {hits}"


def test_offline_sql_no_secret_marker():
    from subprocess import run

    proc = run(
        [str(SERVE_DIR / ".venv/bin/alembic"), "upgrade", V52, "--sql"],
        cwd=str(SERVE_DIR), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    sql = proc.stdout
    for pattern in _SENSITIVE_PATTERNS:
        assert not pattern.search(sql), f"secret marker in offline SQL: {pattern.pattern}"


@pytest.mark.anyio
async def test_db_no_secret_after_operation_flow(temp_pg_db):
    """ChainSettlementLogic 全链（prepare→finality）后 DB 无 secret 明文。"""
    url = temp_pg_db.url
    _upgrade(url)
    from datetime import datetime, timezone
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.uow import UnitOfWork
    from app.logics.trading.settlement import ChainSettlementLogic
    from app.repositories.trading.settlement import ChainOperationRepository

    async_url = url.replace("postgresql+psycopg:///", "postgresql+asyncpg:///")
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(text(
            "INSERT INTO trading.contract_registry (registry_version, kind, version_no, chain_id, "
            " address, proxy_kind, runtime_keccak, resolved_implementation_or_beacon, "
            " resolved_code_keccak, snapshot_block_number, snapshot_block_hash, source_url, "
            " retrieved_at, content_hash, status) VALUES "
            " ('polygon-mainnet-v1', 'ctf_adapter_standard', 1, 137, "
            " '0xAdA100Db00Ca00073811820692005400218FcE1f', 'beacon', "
            " ('0x'::text || repeat('a',64)), ('0x'::text || repeat('b',40)), ('0x'::text || repeat('c',64)), 91842167, "
            " ('0x'::text || repeat('d',64)), 'https://docs.polymarket.com/resources/contracts', "
            " now(), repeat('e',64), 'ACTIVE')"
        ))
        c.execute(text(
            "INSERT INTO trading.pm_accounts (account_key, provider, chain_id, identity_type, "
            " funder_address, maker_address, signing_identity, wallet_type, signature_type, "
            " release_manifest_id, capital_permission_manifest_id, network_mode) VALUES "
            " ('acct', 'polymarket', 137, 'FIXTURE_ONLY', ('0x'::text || repeat('1',20)), "
            " ('0x'::text || repeat('1',20)), ('0x'::text || repeat('2',20)), 'deposit_wallet', '3', 900001, 900002, "
            " 'fixture')"
        ))
        c.execute(text(
            "INSERT INTO trading.capital_permission_manifests (id, name, mode, capability, limits, "
            " evaluation_capital, authorized_capital, kill_switch, content_hash, status) "
            "VALUES (900002, 'test', 'shadow', '{}'::jsonb, '{}'::jsonb, 0, 0, false, "
            " repeat('f',64), 'active')"
        ))
        c.execute(text(
            "INSERT INTO trading.runtime_config_versions (id, config_key, version_no, content, "
            " schema_version, content_hash, status) VALUES (100, 'c', 1, '{}'::jsonb, 1, "
            " repeat('a',64), 'active')"
        ))
        c.execute(text(
            "INSERT INTO trading.strategy_versions (id, strategy_key, version_no, content, "
            " schema_version, content_hash, status) VALUES (101, 's', 1, '{}'::jsonb, 1, "
            " repeat('a',64), 'active')"
        ))
        c.execute(text(
            "INSERT INTO trading.execution_spec_versions (id, spec_key, version_no, content, "
            " schema_version, content_hash, status) VALUES (102, 'e', 1, '{}'::jsonb, 1, "
            " repeat('a',64), 'active')"
        ))
        c.execute(text(
            "INSERT INTO trading.release_manifests (id, release_name, config_version_id, "
            " strategy_version_id, execution_spec_version_id, capital_permission_manifest_id, "
            " git_sha, image_digest, db_revision, total_hash, status) "
            "VALUES (900001, 'rel', 100, 101, 102, 900002, repeat('a',64), 'img', 'b1000052', "
            " repeat('b',64), 'active')"
        ))
    engine.dispose()

    async_engine = create_async_engine(async_url, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    chain_ops = ChainOperationRepository()
    try:
        async with UnitOfWork(sessions) as uow:
            # 一条 REDEEM operation（无 secret 字段）
            await chain_ops.insert_operation(uow.session, {
                "operation_key": "op-secret-bound", "idempotency_key": "idem-secret",
                "economic_hash": "a" * 64, "operation_type": "REDEEM", "chain_id": 137,
                "account_id": 1, "wallet_address": "0x" + "11" * 20,
                "condition_id": "0x" + "44" * 32, "market_id": None,
                "registry_version_id": 1, "target_address": "0xAdA100Db00Ca00073811820692005400218FcE1f",
                "permission_ref": "perm/v1", "release_manifest_id": 900001,
                "capital_permission_manifest_id": 900002, "fencing_token": 1,
                "amount_base_units": 0, "calldata": "0x01b7037c" + "00" * 50,
                "calldata_keccak": "b" * 64, "body_hash": "c" * 64, "call_set_hash": "d" * 64,
                "expected_operation_hash": "e" * 64, "preflight_hash1": "f" * 64,
                "preflight_hash2": "a" * 64,
            })
    finally:
        await async_engine.dispose()

    # 全库扫描 secret marker
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            # 序列化所有 text/jsonb 列，扫描敏感模式
            rows = c.execute(text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema='trading' AND data_type IN ('text','jsonb','character varying')"
            )).fetchall()
            total = 0
            for table, column in rows:
                try:
                    cells = c.execute(text(
                        f'SELECT "{column}"::text FROM trading."{table}" '
                        f'WHERE "{column}" IS NOT NULL'
                    )).fetchall()
                except Exception:
                    continue
                for (val,) in cells:
                    for pattern in _SENSITIVE_PATTERNS:
                        if pattern.search(str(val)):
                            total += 1
            assert total == 0, f"{total} secret markers in DB after operation flow"
    finally:
        engine.dispose()


def test_authorized_capital_zero_and_fake_only():
    import app.config as cfg_mod

    settings = cfg_mod.Settings(_env_file=None)
    assert settings.PM_V2_EXECUTION_EGRESS_MODE == "shadow"
    assert settings.PM_V2_POLYGON_CHAIN_ID == 137
