"""
WP-05 execution reservations / fencing —— 真 PostgreSQL 集成（Checkpoint B）。

覆盖：并发 reservation 原子性（两个并发不越过 funds）、lease 获取/续期/过期接管、
fencing token 单调、stale owner 迟到 ack/heartbeat 只记录 STALE_FENCE_REJECTED。
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.logics.trading.execution import ExecutionLeaseLogic, LeaseError, StaleFenceError
from app.logics.trading.portfolio import PortfolioLogic
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.vault import VaultRepository
from app.services.vault import VaultService

from tests.trading.integration.test_v2_vault_accounts_funds import (
    seed_control_chain,
    seed_intent_chain,
)

SERVE_DIR = __import__("pathlib").Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
HEAD = "b1000050"

K1 = os.urandom(32)
KEYRING = {("k1", "v1"): K1}


class _UoW:
    def __init__(self, session):
        self.session = session


@pytest_asyncio.fixture
async def env(temp_pg_db):
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    engine = create_engine(temp_pg_db.url, poolclass=NullPool)
    conn = engine.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, "head")
    finally:
        conn.close()
        engine.dispose()

    async_url = make_url(temp_pg_db.url).set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    async_engine = create_async_engine(async_url, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)

    async with sessions() as session:
        chain = await seed_control_chain(session)
        await session.commit()

    repo = ExecutionRepository()
    vault_repo = VaultRepository()

    async with sessions() as session:
        svc = VaultService(vault_repo, KEYRING, env="test")
        entry = await svc.create_entry(
            session, name="pm/signer/fixture-2", secret_kind="signer_private_key",
            runtime_identity="worker-a",
        )
        account = await repo.insert_account(
            session, account_key="fixture-acct-2", provider="polymarket", chain_id=137,
            identity_type="FIXTURE_ONLY", funder_address="0x" + "a" * 40,
            maker_address="0x" + "b" * 40, signing_identity="0x" + "c" * 40,
            wallet_type="deposit_wallet", signature_type="3",
            signer_secret_entry_id=entry["id"], signer_secret_version_id=1,
            l2_secret_entry_id=None, l2_secret_version_id=None,
            release_manifest_id=chain["release_manifest_id"],
            capital_permission_manifest_id=chain["capital_permission_manifest_id"],
            network_mode="fixture",
        )
        snapshot = await repo.insert_balance_snapshot(
            session, account_id=account["id"], asset_key="USD", spender=None,
            balance=100, allowance=100, provider_reserved=0,
            observed_at=datetime.now(timezone.utc),
            request_hash="c" * 64, fencing_token=1, completeness="COMPLETE",
        )
        await repo.create_funds(
            session, account_id=account["id"], asset_key="USD",
            confirmed=100, provider_reserved=0, local_reserved=0, available=100,
            source_snapshot_id=snapshot["id"], reconcile_watermark=1,
        )
        await session.commit()
        account_id = account["id"]

    try:
        yield {
            "sessions": sessions, "repo": repo, "account_id": account_id,
            "chain": chain,
        }
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_reservations_atomicity(env):
    sessions = env["sessions"]
    repo = env["repo"]
    account_id = env["account_id"]
    logic = PortfolioLogic(execution=repo)
    async with sessions() as session:
        intent = await seed_intent_chain(session, env["chain"])
        await session.commit()
    intent_id = intent["intent_id"]

    async def _reserve(label, ik):
        async with sessions() as session:
            try:
                res = await logic.reserve_funds(
                    _UoW(session), reservation_key=f"conc-{label}", intent_id=intent_id,
                    account_id=account_id, asset_key="USD", amount=Decimal("70"),
                    idempotency_key=ik,
                )
                await session.commit()
                return "ok"
            except RuntimeError as exc:
                await session.rollback()
                return str(exc)

    results = await asyncio.gather(_reserve("a", "ik-a"), _reserve("b", "ik-b"))
    assert sorted(results) == ["funds_insufficient", "ok"]
    async with sessions() as session:
        funds = await repo.get_funds(session, account_id=account_id, asset_key="USD")
    assert funds["local_reserved"] == Decimal("70")
    assert funds["available"] == Decimal("30")
    # 不越过 funds
    assert funds["available"] == funds["confirmed"] - funds["provider_reserved"] - funds["local_reserved"]


@pytest.mark.asyncio
async def test_lease_fencing_lifecycle(env):
    sessions = env["sessions"]
    repo = env["repo"]
    account_id = env["account_id"]
    logic = ExecutionLeaseLogic(execution=repo)

    async with sessions() as session:
        lease_a = await logic.acquire_lease(
            _UoW(session), account_id=account_id, lease_role="EXECUTION",
            owner="leader-A", ttl_s=60,
        )
        await session.commit()
        assert lease_a["fencing_token"] == 1

    # 未过期、他人持有 → busy
    async with sessions() as session:
        with pytest.raises(LeaseError, match="lease_busy"):
            await logic.acquire_lease(
                _UoW(session), account_id=account_id, lease_role="EXECUTION",
                owner="leader-B", ttl_s=60,
            )
            await session.commit()
        await session.rollback()

    # 过期 → B 接管，token 单调 +1
    async with sessions() as session:
        await session.execute(
            text(
                "UPDATE trading.execution_leases SET lease_until=:t "
                "WHERE account_id=:a AND lease_role='EXECUTION'"
            ),
            {"t": datetime.now(timezone.utc) - timedelta(seconds=1), "a": account_id},
        )
        await session.commit()
    async with sessions() as session:
        lease_b = await logic.acquire_lease(
            _UoW(session), account_id=account_id, lease_role="EXECUTION",
            owner="leader-B", ttl_s=60,
        )
        await session.commit()
        assert lease_b["fencing_token"] == 2

    # 旧 owner A 的 fence 校验 → STALE_FENCE_REJECTED；新 owner B 通过
    async with sessions() as session:
        with pytest.raises(StaleFenceError, match="stale_fence_rejected"):
            await logic.assert_fence(
                _UoW(session), account_id=account_id, lease_role="EXECUTION", token=1,
            )
        await logic.assert_fence(
            _UoW(session), account_id=account_id, lease_role="EXECUTION", token=2,
        )
        await session.rollback()


@pytest.mark.asyncio
async def test_late_ack_only_stale_fence_rejected(env):
    sessions = env["sessions"]
    repo = env["repo"]
    account_id = env["account_id"]
    logic = ExecutionLeaseLogic(execution=repo)

    async with sessions() as session:
        await logic.acquire_lease(
            _UoW(session), account_id=account_id, lease_role="HEARTBEAT",
            owner="leader-A", ttl_s=60,
        )
        await session.commit()
    async with sessions() as session:
        await session.execute(
            text(
                "UPDATE trading.execution_leases SET lease_until=:t "
                "WHERE account_id=:a AND lease_role='HEARTBEAT'"
            ),
            {"t": datetime.now(timezone.utc) - timedelta(seconds=1), "a": account_id},
        )
        await session.commit()
    async with sessions() as session:
        await logic.acquire_lease(
            _UoW(session), account_id=account_id, lease_role="HEARTBEAT",
            owner="leader-B", ttl_s=60,
        )
        await session.commit()

    # 迟到 ack/heartbeat：旧 owner A 续期必须 stale，且 current 状态不变
    async with sessions() as session:
        with pytest.raises(StaleFenceError, match="stale_fence_rejected"):
            await logic.renew_lease(
                _UoW(session), account_id=account_id, lease_role="HEARTBEAT",
                owner="leader-A", fencing_token=1, ttl_s=60,
            )
            await session.commit()
        await session.rollback()
    async with sessions() as session:
        lease = await repo.get_lease(
            session, account_id=account_id, lease_role="HEARTBEAT"
        )
        assert lease["owner"] == "leader-B"
        assert lease["fencing_token"] == 2
