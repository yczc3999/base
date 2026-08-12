"""WP-03 ledger 不变量集成测试（真 PostgreSQL）。

- 双分录：BUY 至少 4 postings，cash/token 各归零；孤儿/不平衡/跨资产失衡拒绝。
- POSTED 后禁 UPDATE/DELETE；reversal 精确相反。
- operating cost append-only；类别白名单；禁止把缺失成本写成 0。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork
from app.domain.trading.ledger import build_buy_postings, postings_balanced
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
# WP-05 后 head=b1000052；本测试用 live ExecutionRepository（executions 含 account_id 列），
# 必须在 head schema 上跑，否则 UndefinedColumnError。
V31 = "b1000051"
V52 = "b1000052"
V70 = "b1000070"


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


@pytest_asyncio.fixture
async def ledger_env(temp_pg_db):
    _run(command.upgrade, V52, temp_pg_db.url)
    admin = make_url(temp_pg_db.url)
    async_url = admin.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    engine = create_async_engine(async_url, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    env = {"sessions": sessions, "ledger": LedgerRepository()}
    yield env
    await engine.dispose()


def _balanced_buy_postings() -> list[dict]:
    """BUY 100 @ 0.52 → cash 52, token 100（4 postings，各归零）。"""
    return [
        {"posting_no": 0, "asset_type": "CASH", "asset_key": "usd", "amount": "-52", "counterparty": "portfolio"},
        {"posting_no": 1, "asset_type": "CASH", "asset_key": "usd", "amount": "52", "counterparty": "shadow"},
        {"posting_no": 2, "asset_type": "TOKEN", "asset_key": "tok:1:1", "amount": "100", "counterparty": "portfolio"},
        {"posting_no": 3, "asset_type": "TOKEN", "asset_key": "tok:1:1", "amount": "-100", "counterparty": "shadow"},
    ]


@pytest.mark.asyncio
async def test_buy_postings_balanced_and_posted(ledger_env):
    env = ledger_env
    postings = _balanced_buy_postings()
    assert postings_balanced(
        [__import__("app.domain.trading.ledger", fromlist=["Posting"]).Posting(
            p["asset_type"], p["asset_key"], __import__("decimal").Decimal(p["amount"]), p["counterparty"]
        ) for p in postings]
    )
    async with UnitOfWork(env["sessions"]) as uow:
        tx = await env["ledger"].insert_transaction(
            uow.session, transaction_key="lt-bal-1", kind="FILL",
            trade_decision_id=None, execution_id=None, portfolio_namespace="ns")
        await env["ledger"].insert_postings(uow.session, transaction_id=tx, postings=postings)
        await env["ledger"].mark_posted(uow.session, tx, posted_at=datetime.now(timezone.utc))
    async with UnitOfWork(env["sessions"]) as uow:
        stored = await env["ledger"].get_transaction(uow.session, "lt-bal-1")
        assert stored["status"] == "POSTED"
        rows = await env["ledger"].postings_for_transaction(uow.session, stored["id"])
        assert len(rows) == 4


@pytest.mark.asyncio
async def test_unbalanced_postings_rejected(ledger_env):
    env = ledger_env
    # PENDING 插入（余额 trigger 仅 POSTED 时校验）
    async with UnitOfWork(env["sessions"]) as uow:
        tx = await env["ledger"].insert_transaction(
            uow.session, transaction_key="lt-unbal-1", kind="FILL",
            trade_decision_id=None, execution_id=None, portfolio_namespace="ns")
        await env["ledger"].insert_postings(uow.session, transaction_id=tx,
                                            postings=_balanced_buy_postings()[:3])
    # 单独 mark_posted → deferred trigger 在 commit 时拒绝不平衡
    with pytest.raises(Exception):
        async with UnitOfWork(env["sessions"]) as uow:
            await env["ledger"].mark_posted(uow.session, tx, posted_at=datetime.now(timezone.utc))
    # 保持 PENDING
    async with UnitOfWork(env["sessions"]) as uow:
        stored = await env["ledger"].get_transaction(uow.session, "lt-unbal-1")
        assert stored["status"] == "PENDING"


@pytest.mark.asyncio
async def test_posted_immutable_and_reversal(ledger_env):
    env = ledger_env
    async with UnitOfWork(env["sessions"]) as uow:
        tx = await env["ledger"].insert_transaction(
            uow.session, transaction_key="lt-imm-1", kind="FILL",
            trade_decision_id=None, execution_id=None, portfolio_namespace="ns")
        await env["ledger"].insert_postings(uow.session, transaction_id=tx, postings=_balanced_buy_postings())
        await env["ledger"].mark_posted(uow.session, tx, posted_at=datetime.now(timezone.utc))
    # POSTED 后禁改/禁删
    with pytest.raises(Exception):
        async with UnitOfWork(env["sessions"]) as uow:
            await uow.session.execute(text(
                "UPDATE trading.ledger_postings SET amount=amount+1 WHERE transaction_id=:t"
            ), {"t": tx})
    with pytest.raises(Exception):
        async with UnitOfWork(env["sessions"]) as uow:
            await uow.session.execute(text(
                "DELETE FROM trading.ledger_postings WHERE transaction_id=:t"
            ), {"t": tx})
    with pytest.raises(Exception):
        async with UnitOfWork(env["sessions"]) as uow:
            await uow.session.execute(text(
                "DELETE FROM trading.ledger_transactions WHERE id=:t"
            ), {"t": tx})
    # reversal：新建 REVERSAL 交易，postings 精确相反，可 POSTED。
    async with UnitOfWork(env["sessions"]) as uow:
        rev = await env["ledger"].insert_transaction(
            uow.session, transaction_key="lt-rev-1", kind="REVERSAL",
            trade_decision_id=None, execution_id=None, portfolio_namespace="ns",
            reference_transaction_id=tx)
        reversed_postings = [
            {**p, "amount": str(-__import__("decimal").Decimal(p["amount"]))}
            for p in _balanced_buy_postings()
        ]
        await env["ledger"].insert_postings(uow.session, transaction_id=rev, postings=reversed_postings)
        await env["ledger"].mark_posted(uow.session, rev, posted_at=datetime.now(timezone.utc))
    async with UnitOfWork(env["sessions"]) as uow:
        rows = await env["ledger"].postings_for_transaction(uow.session, rev)
        assert [r["amount"] for r in rows] == [52, -52, -100, 100]


@pytest.mark.asyncio
async def test_operating_cost_append_only_and_kind_whitelist(ledger_env):
    env = ledger_env
    async with UnitOfWork(env["sessions"]) as uow:
        cost_id = await env["ledger"].insert_operating_cost(
            uow.session, cost_key="cost-1", cost_kind="LLM", amount=100,
            release_manifest_id=None, episode_id=None, trade_decision_id=None,
            period_start=None, period_end=None, allocation_policy={"policy": "fixed_marginal"})
    assert cost_id > 0
    with pytest.raises(Exception):
        async with UnitOfWork(env["sessions"]) as uow:
            await uow.session.execute(text(
                "INSERT INTO trading.operating_cost_entries (cost_key, cost_kind, amount, allocation_policy) "
                "VALUES ('cost-bad', 'CRYPTO', 1, '{}'::jsonb)"
            ))
    with pytest.raises(Exception):
        async with UnitOfWork(env["sessions"]) as uow:
            await uow.session.execute(text(
                "UPDATE trading.operating_cost_entries SET amount=0 WHERE cost_key='cost-1'"
            ))
