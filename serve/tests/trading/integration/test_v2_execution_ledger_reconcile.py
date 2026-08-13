"""
WP-05 execution ledger reconcile —— 真 PostgreSQL 集成（Checkpoint C）。

覆盖：每 asset ledger signed base units=0、position/cash/provider diff 任一非 0 → hard
stop/alert、reconcile 后 diff=0（COMPLETED）。
"""

from __future__ import annotations

import os
import hashlib
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

from app.logics.trading.execution import PrivateExecutionLogic
from app.logics.trading.reconciliation import ReconciliationLogic
from app.logics.trading.portfolio import PortfolioLogic
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository
from app.repositories.trading.vault import VaultRepository
from app.schemas.trading.execution import (
    EnvelopeInput,
    ReconcileInput,
    SubmitOrderInput,
)
from app.services.vault import VaultService

from tests.trading.integration.test_v2_vault_accounts_funds import seed_control_chain
from tests.trading.integration.test_v2_private_order_reconciliation import (
    _seed_execution_chain,
)

SERVE_DIR = __import__("pathlib").Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
HEAD = "b1000072"

K1 = os.urandom(32)
KEYRING = {("k1", "v1"): K1}
H64 = "a" * 64
TOKEN_ID = "tok-real"


class _UoW:
    def __init__(self, session):
        self.session = session


class _FakeSignedOrder:
    def __init__(self):
        self.salt = 2001
        self.timestamp = 1700000000
        self.maker = "0x" + "22" * 20
        self.signer = "0x" + "22" * 20
        self.signature_type = 3
        self.signature = "0x" + "b" * 130
        self.token_id = TOKEN_ID
        self.maker_amount = 100
        self.taker_amount = 55
        self.side = "SELL"
        self.expiration = 0
        self.order_type = "GTC"
        self.post_only = False


class _FakeAck:
    def __init__(self):
        self.post_order_calls = 0

    def post_order(self, signed_order):
        self.post_order_calls += 1
        from app.schemas.polymarket.clob_private import OrderResponse

        return OrderResponse(order_id="ext-ledger-1", status="live", success=True)


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

    async with sessions() as session:
        svc = VaultService(
            VaultRepository(), KEYRING, env="test", runtime_identity="worker-ledger",
        )
        entry = await svc.create_entry(
            session, name="pm/signer/fixture-ledger", secret_kind="signer_private_key",
            runtime_identity="worker-ledger",
        )
        version = await svc.store_secret(
            session,
            entry_id=entry["id"],
            secret=b"fixture-ledger-signer",
            purpose="sign",
            identity="worker-ledger",
            account="fixture-acct-ledger",
            key_id="k1",
            key_version="v1",
        )
        account = await repo.insert_account(
            session, account_key="fixture-acct-ledger", provider="polymarket", chain_id=137,
            identity_type="FIXTURE_ONLY", funder_address="0x" + "a" * 40,
            maker_address="0x" + "a" * 40, signing_identity="0x" + "c" * 40,
            wallet_type="deposit_wallet", signature_type="3",
            signer_secret_entry_id=entry["id"], signer_secret_version_id=version["id"],
            l2_secret_entry_id=None, l2_secret_version_id=None,
            release_manifest_id=chain["release_manifest_id"],
            capital_permission_manifest_id=chain["capital_permission_manifest_id"],
            network_mode="fixture",
        )
        snapshot = await repo.insert_balance_snapshot(
            session, account_id=account["id"], asset_key="USD", spender=None,
            balance=1000, allowance=1000, provider_reserved=0,
            observed_at=datetime.now(timezone.utc),
            request_hash="d" * 64, fencing_token=1, completeness="COMPLETE",
        )
        await repo.create_funds(
            session, account_id=account["id"], asset_key="USD",
            confirmed=1000, provider_reserved=0, local_reserved=0, available=1000,
            source_snapshot_id=snapshot["id"], reconcile_watermark=1,
        )
        await repo.insert_lease(
            session,
            account_id=account["id"],
            lease_role="EXECUTION",
            owner="worker-ledger",
            lease_until=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        await session.commit()
        account_id = account["id"]

    try:
        yield {"sessions": sessions, "repo": repo, "account_id": account_id, "chain": chain}
    finally:
        await async_engine.dispose()


async def _setup_submitted_filled(env):
    """seed → reserve → envelope → submit ACK → fill FILLED；返回 {order_id, intent_id, envelope_id}。"""
    async with env["sessions"]() as session:
        chain = await _seed_execution_chain(session, env["chain"], leg_role="reduce", quantity=10, account_id=env["account_id"])
        await session.commit()
    intent_id = chain["intent_id"]
    token_asset = f"tok:{chain['contract_spec_id']}:{chain['token_id']}"
    env["token_asset_key"] = token_asset
    logic_p = PortfolioLogic(execution=env["repo"])
    async with env["sessions"]() as session:
        snapshot = await env["repo"].insert_balance_snapshot(
            session, account_id=env["account_id"], asset_key=token_asset, spender=None,
            balance=1000, allowance=1000, provider_reserved=0,
            observed_at=datetime.now(timezone.utc),
            request_hash=hashlib.sha256(token_asset.encode()).hexdigest(),
            fencing_token=1, completeness="COMPLETE",
        )
        await env["repo"].create_funds(
            session, account_id=env["account_id"], asset_key=token_asset,
            confirmed=1000, provider_reserved=0, local_reserved=0, available=1000,
            source_snapshot_id=snapshot["id"], reconcile_watermark=1,
        )
        await logic_p.reserve_funds(
            _UoW(session), reservation_key="res-ledger", intent_id=intent_id,
            account_id=env["account_id"], asset_key=token_asset, amount=Decimal("10"),
            idempotency_key="ik-ledger",
        )
        await session.commit()
    logic = PrivateExecutionLogic(execution=env["repo"], ledger=LedgerRepository(),
                                  audit=AuditRepository())
    async with env["sessions"]() as session:
        pf1, pf2 = await logic.authoritative_preflight_hashes(
            _UoW(session), intent_id=intent_id, account_id=env["account_id"],
            release_manifest_id=env["chain"]["release_manifest_id"],
            execution_spec_version_id=env["chain"]["execution_spec_version_id"],
            capital_permission_manifest_id=env["chain"]["capital_permission_manifest_id"],
            fencing_token=1,
        )
        envelope_input = EnvelopeInput(
            envelope_key="env-ledger", intent_id=intent_id, account_id=env["account_id"],
            release_manifest_id=env["chain"]["release_manifest_id"],
            execution_spec_version_id=env["chain"]["execution_spec_version_id"],
            capital_permission_manifest_id=env["chain"]["capital_permission_manifest_id"],
            authority="FAKE_CONFORMANCE", idempotency_key="env-ik-ledger", fencing_token=1,
            intent_hash=H64, preflight_hash1=pf1, preflight_hash2=pf2,
        )
        envelope = await logic.create_envelope(
            _UoW(session), input_=envelope_input, owner="worker-ledger"
        )
        await session.commit()
        envelope_id = envelope["id"]
    submit = SubmitOrderInput(
        envelope_id=envelope_id, account_id=env["account_id"], fencing_token=1,
        token_id=TOKEN_ID, side="SELL", price="0.5", size="10",
    )
    from app.services.polymarket.clob_trading_driver import ACK, SubmitOutcome

    async with env["sessions"]() as session:
        prepared = await logic.prepare_submit(
            _UoW(session), input_=submit, owner="worker-ledger", signed_order=_FakeSignedOrder(),
            body_hash="b" * 64, expected_order_hash="c" * 64, sdk_manifest_hash="d" * 64,
        )
        await session.commit()
    outcome = SubmitOutcome(ACK, order_id="ext-ledger-1", http_status=200)
    async with env["sessions"]() as session:
        result = await logic.apply_submit_outcome(
            _UoW(session), prepared=prepared, outcome=outcome,
            response_hash="b" * 64, http_status=200, error_reason=None,
        )
        await session.commit()
        assert result.status == "ACK"
    async with env["sessions"]() as session:
        fill = await logic.apply_fill(
            _UoW(session), order_id=prepared.order_id, account_id=env["account_id"],
            envelope_id=envelope_id, intent_id=intent_id, owner="worker-ledger", fencing_token=1,
            external_trade_id="trd-ledger-1", side="SELL", price="0.50", size="10", fee="1",
            trade_time=datetime.now(timezone.utc), trade_status="CONFIRMED",
        )
        await session.commit()
        assert fill.order_status == "FILLED"
    return {"order_id": prepared.order_id, "intent_id": intent_id, "envelope_id": envelope_id}


@pytest.mark.asyncio
async def test_per_asset_ledger_signed_zero(env):
    await _setup_submitted_filled(env)
    repo = env["repo"]
    async with env["sessions"]() as session:
        sums = await repo.per_asset_ledger_sums(session, account_id=env["account_id"])
        assert sums, "expected postings"
        for row in sums:
            assert Decimal(str(row["signed_sum"])) == 0, row


@pytest.mark.asyncio
async def test_position_diff_triggers_hard_stop_alert(env):
    await _setup_submitted_filled(env)
    logic_r = ReconciliationLogic(execution=env["repo"], ledger=LedgerRepository(),
                                  audit=AuditRepository())
    async with env["sessions"]() as session:
        rec = await logic_r.start_reconcile(
            _UoW(session), input_=ReconcileInput(
                reconciliation_key="rec-ledger-fail", account_id=env["account_id"],
                fencing_token=1, trigger_reason="position_diff", ws_watermark=1, rest_cursor=None,
            ), owner="worker-ledger",
        )
        await session.commit()
        rec_id = rec["id"]
    # 本地 position=0（已 reduce 平仓），但 provider 报 10 → diff != 0 → FAILED + alert
    async with env["sessions"]() as session:
        result = await logic_r.complete_reconcile(
            _UoW(session), reconciliation_id=rec_id, account_id=env["account_id"],
            owner="worker-ledger", fencing_token=1,
            remote_orders=[], remote_trades=[{"external_trade_id": "trd-ledger-1"}],
            remote_positions=[{"token_id": TOKEN_ID, "size": "10"}],
            remote_funds=[{"asset_key": "USD", "confirmed": "1000", "provider_reserved": "0",
                           "local_reserved": "0"},
                          {"asset_key": env["token_asset_key"], "confirmed": "990",
                           "provider_reserved": "0", "local_reserved": "0"}],
            unknown_queries={},
        )
        await session.commit()
        assert result.status == "FAILED"
        assert any(d["kind"] == "position" for d in result.differences)
        # hard stop alert 已写入
        alerts = (await session.execute(
            text("SELECT code FROM trading.alert_events WHERE code='reconcile_differences_nonzero'")
        )).scalars().all()
        assert alerts


@pytest.mark.asyncio
async def test_reconcile_completed_after_diff_zero(env):
    await _setup_submitted_filled(env)
    logic_r = ReconciliationLogic(execution=env["repo"], ledger=LedgerRepository(),
                                  audit=AuditRepository())
    async with env["sessions"]() as session:
        rec = await logic_r.start_reconcile(
            _UoW(session), input_=ReconcileInput(
                reconciliation_key="rec-ledger-ok", account_id=env["account_id"],
                fencing_token=1, trigger_reason="ws_disconnect", ws_watermark=1, rest_cursor=None,
            ), owner="worker-ledger",
        )
        await session.commit()
        rec_id = rec["id"]
    async with env["sessions"]() as session:
        result = await logic_r.complete_reconcile(
            _UoW(session), reconciliation_id=rec_id, account_id=env["account_id"],
            owner="worker-ledger", fencing_token=1,
            remote_orders=[], remote_trades=[{"external_trade_id": "trd-ledger-1"}],
            remote_positions=[{"token_id": TOKEN_ID, "size": "0"}],
            remote_funds=[{"asset_key": "USD", "confirmed": "1000", "provider_reserved": "0",
                           "local_reserved": "0"},
                          {"asset_key": env["token_asset_key"], "confirmed": "990",
                           "provider_reserved": "0", "local_reserved": "0"}],
            unknown_queries={},
        )
        await session.commit()
        assert result.status == "COMPLETED", result.differences
        assert result.differences == []
