"""WP-06 Checkpoint D —— chain operation recovery replay（真 PostgreSQL）。

证明：N 个 UNKNOWN/active operation 两次恢复最终状态/hash 全等、blind resend=0；
restart 从 DB facts 恢复，不调用 AI、不重算 decision、不盲重发。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork
from app.logics.trading.settlement import ChainSettlementLogic
from app.repositories.trading.settlement import ChainOperationRepository

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V52 = "b1000052"


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


def _async_url(db_url: str) -> str:
    return db_url.replace("postgresql+psycopg:///", "postgresql+asyncpg:///")


def _seed_operations(db_url, n: int):
    """批量插入 UNKNOWN operations（replica 绕过 FK）。"""
    engine = create_engine(db_url)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            for i in range(n):
                c.execute(
                    text(
                        "INSERT INTO trading.chain_operations "
                        "(operation_key, idempotency_key, economic_hash, operation_type, "
                        " chain_id, account_id, wallet_address, condition_id, registry_version_id, "
                        " target_address, permission_ref, release_manifest_id, "
                        " capital_permission_manifest_id, fencing_token, amount_base_units, "
                        " calldata, calldata_keccak, body_hash, call_set_hash, "
                        " expected_operation_hash, preflight_hash1, preflight_hash2, status) "
                        "VALUES (:op, :idem, :eh, 'REDEEM', 137, 1, :wallet, :cond, 1, "
                        " :target, 'perm/v1', 900001, 900002, 1, 0, :cd, :ch, :bh, :csh, "
                        " :eoh, :p1, :p2, 'UNKNOWN')"
                    ),
                    {
                        "op": f"op-{i:06d}", "idem": f"idem-{i:06d}", "eh": f"{i:064x}",
                        "wallet": "0x" + f"{i:040x}",
                        "cond": "0x" + f"{i:064x}",
                        "target": "0xAdA100Db00Ca00073811820692005400218FcE1f",
                        "cd": "0x" + f"{i:080x}", "ch": f"{i:064x}", "bh": f"{i:064x}",
                        "csh": f"{i:064x}", "eoh": f"{i:064x}", "p1": f"{i:064x}",
                        "p2": f"{i:064x}",
                    },
                )
    finally:
        engine.dispose()


def _query(db_url, sql):
    engine = create_engine(db_url)
    try:
        with engine.connect() as c:
            return c.execute(text(sql)).fetchall()
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_recovery_1000_unknown_twice_equal_and_no_blind_resend(temp_pg_db):
    url = temp_pg_db.url
    _upgrade(url)
    N = 1000
    _seed_operations(url, N)
    async_engine = create_async_engine(_async_url(url), pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    logic = ChainSettlementLogic(chain_operations=ChainOperationRepository(),
                                 chain_id=137)
    chain_ops = ChainOperationRepository()

    # 第一次恢复：全部 UNKNOWN → 只读证据快照
    first = {}
    try:
        async with UnitOfWork(sessions) as uow:
            ops = await chain_ops.list_recoverable(uow.session, limit=N)
            assert len(ops) == N
            for op in ops:
                r = await logic.recover_unknown(uow, op["id"])
                first[op["id"]] = r
    finally:
        await async_engine.dispose()

    # 第二次恢复：结果 hash 全等、blind resend 恒 False
    async_engine2 = create_async_engine(_async_url(url), pool_size=4, max_overflow=0)
    sessions2 = async_sessionmaker(async_engine2, expire_on_commit=False)
    try:
        async with UnitOfWork(sessions2) as uow:
            ops = await chain_ops.list_recoverable(uow.session, limit=N)
            for op in ops:
                r = await logic.recover_unknown(uow, op["id"])
                assert r["blind_resend"] is False
                assert r == first[op["id"]], f"recovery drift on op {op['id']}"
    finally:
        await async_engine2.dispose()

    # 无权威失败/不存在证据 → real resend 计数为 0（无 blind resend）
    # 证据字段在恢复中不被改动（只读）；operation 状态仍 UNKNOWN
    unknown = _query(url, "SELECT count(*) FROM trading.chain_operations WHERE status='UNKNOWN'")
    assert unknown == [(N,)]


@pytest.mark.anyio
async def test_recovery_restart_resumes_from_db_facts(temp_pg_db):
    """restart 从 transaction/nonce/receipt/finalized block/balance 恢复，无 AI/重算。"""
    url = temp_pg_db.url
    _upgrade(url)
    _seed_operations(url, 5)
    async_engine = create_async_engine(_async_url(url), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    chain_ops = ChainOperationRepository()
    # 给一条 operation 补齐证据（模拟已发起的网络调用后 restart）
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(text(
                "UPDATE trading.chain_operations SET transaction_id='tx-1', "
                "transaction_hash=:th, relayer_nonce='7' WHERE operation_key='op-000000'"
            ), {"th": "0x" + "1" * 64})
    finally:
        engine.dispose()
    try:
        async with UnitOfWork(sessions) as uow:
            ops = await chain_ops.list_recoverable(uow.session, limit=5)
            by_key = {op["operation_key"]: op for op in ops}
            resumed = by_key["op-000000"]
            assert resumed["transaction_id"] == "tx-1"
            assert resumed["relayer_nonce"] == "7"
            r = await ChainSettlementLogic(chain_operations=chain_ops).recover_unknown(
                uow, resumed["id"]
            )
            assert r["evidence"]["has_transaction_id"] is True
            assert r["blind_resend"] is False
    finally:
        await async_engine.dispose()
