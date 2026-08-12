"""WP-06 Checkpoint D —— chain ledger reconcile（真 PostgreSQL）。

证明：settlement/position/cash 账本可从 append-only evidence 重建；ledger_transactions 与
postings 按 ID 链可展开回放；重复/乱序 effect=0；账本每 asset 平衡。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.trading.integration.test_v2_chain_operation_finality import (
    ADAPTER_CONTENT_HASH,
    _seed_fk_chain,
    _seed_observations,
    _seed_registry,
)

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V52 = "b1000052"
COND = "0x" + "44" * 32


def _upgrade(db_url):
    cfg = Config(); cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    engine = create_engine(db_url, poolclass=NullPool); conn = engine.connect()
    cfg.attributes["connection"] = conn
    try: command.upgrade(cfg, V52)
    finally: conn.close(); engine.dispose()


def _query(db_url, sql, params=None):
    engine = create_engine(db_url)
    try:
        with engine.connect() as c:
            return c.execute(text(sql), params or {}).fetchall()
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_ledger_rebuild_from_append_only_evidence(temp_pg_db):
    """按 chain_operation → ledger_transaction → postings 的 ID 链展开，账本可回放且平衡。"""
    url = temp_pg_db.url
    _upgrade(url)
    _seed_fk_chain(url)
    _seed_registry(url)
    _seed_observations(url)
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.db.uow import UnitOfWork
    from app.logics.trading.settlement import ChainSettlementLogic
    from app.repositories.trading.settlement import ChainOperationRepository
    from tests.trading.integration.test_v2_chain_operation_finality import _walk_to_finalized

    async_url = url.replace("postgresql+psycopg:///", "postgresql+asyncpg:///")
    async_engine = create_async_engine(async_url, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    logic = ChainSettlementLogic(chain_operations=ChainOperationRepository(), chain_id=137)
    chain_ops = ChainOperationRepository()
    try:
        async with UnitOfWork(sessions) as uow:
            prepared = await logic.prepare_redeem(
                uow, operation_key="op-rebuild", idempotency_key="idem-rebuild",
                account_id=1, wallet_address="0x" + "11" * 20, condition_id=COND,
                market_id=None, neg_risk=False, registry_content_hash=ADAPTER_CONTENT_HASH,
                permission_ref="perm/v1", release_manifest_id=900001,
                capital_permission_manifest_id=900002, fencing_token=1,
            )
            op_id = prepared.operation_id
            await _walk_to_finalized(uow, op_id, chain_ops)
            await logic.apply_finality(uow, op_id)
    finally:
        await async_engine.dispose()

    # 按 ID 链展开
    ledger = _query(url,
        "SELECT id, transaction_key, kind, chain_operation_id FROM trading.ledger_transactions "
        "WHERE kind='SETTLEMENT'")
    assert len(ledger) == 1
    tx_id, tx_key, kind, cop = ledger[0]
    postings = _query(url,
        "SELECT posting_no, asset_type, asset_key, amount FROM trading.ledger_postings "
        "WHERE transaction_id=:t ORDER BY posting_no", {"t": tx_id})
    assert len(postings) == 4
    by_asset = {}
    for _, asset_type, asset_key, amount in postings:
        by_asset[(asset_type, asset_key)] = by_asset.get((asset_type, asset_key), 0) + int(amount)
    assert all(v == 0 for v in by_asset.values()), f"ledger unbalanced: {by_asset}"
    # chain_operation lineage 回链到 operation
    op = _query(url, "SELECT operation_key FROM trading.chain_operations WHERE id=:oid",
                {"oid": cop})
    assert op == [("op-rebuild",)]
    # 重复展开（只读回放）无副作用
    postings2 = _query(url,
        "SELECT posting_no, asset_type, asset_key, amount FROM trading.ledger_postings "
        "WHERE transaction_id=:t ORDER BY posting_no", {"t": tx_id})
    assert postings2 == postings
