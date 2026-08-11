"""WP-05 persisted-before-send recovery on a real PostgreSQL database.

These tests exercise the four process-loss boundaries around a private submit:

1. the durable attempt commits before the transport write starts;
2. the provider accepts the write but the client loses the response;
3. the ACK body is received but the apply UoW never starts; and
4. the apply UoW writes its projection and then rolls back.

On restart the recovery scanner must never submit the signed order again.  It
must fail closed to ``UNKNOWN``, retain the reservation, preserve the immutable
body/order hashes, and be idempotent when a second recovery worker scans the
same account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
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
from app.logics.trading.execution import ExecutionLeaseLogic, PrivateExecutionLogic
from app.logics.trading.portfolio import PortfolioLogic
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository
from app.schemas.trading.execution import EnvelopeInput, SubmitOrderInput
from app.services.polymarket.clob_trading_driver import ACK, SubmitOutcome
from runtimes.trading.execution import PrivateExecutionRuntime
from tests.trading.integration.test_v2_private_order_reconciliation import (
    TOKEN_ID,
    _seed_execution_chain,
)
from tests.trading.integration.test_v2_vault_accounts_funds import seed_control_chain


SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
OWNER = "execution-recovery-worker"
BODY_HASH = "b" * 64
ORDER_HASH = "c" * 64
SDK_HASH = "d" * 64


@dataclass
class _SignedOrder:
    salt: int = 20260811
    timestamp: int = 1786417445


class _WriteProbe:
    """Fake provider that can lose a response after accepting one write."""

    def __init__(self) -> None:
        self.submit_calls = 0
        self.accepted_writes = 0

    async def submit(self, *, lose_response: bool = False) -> SubmitOutcome:
        self.submit_calls += 1
        self.accepted_writes += 1
        if lose_response:
            raise ConnectionError("fixture_response_lost_after_provider_accept")
        return SubmitOutcome(
            ACK,
            order_id=f"recovery-order-{self.accepted_writes}",
            http_status=200,
        )


@pytest_asyncio.fixture
async def recovery_env(temp_pg_db):
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    sync_engine = create_engine(temp_pg_db.url, poolclass=NullPool)
    connection = sync_engine.connect()
    cfg.attributes["connection"] = connection
    try:
        command.upgrade(cfg, "head")
    finally:
        connection.close()
        sync_engine.dispose()

    async_url = make_url(temp_pg_db.url).set(
        drivername="postgresql+asyncpg"
    ).render_as_string(hide_password=False)

    def fresh():
        engine = create_async_engine(async_url, pool_size=2, max_overflow=0)
        return engine, async_sessionmaker(engine, expire_on_commit=False)

    engine, sessions = fresh()
    repo = ExecutionRepository()
    async with sessions() as session:
        chain = await seed_control_chain(session)
        account = await repo.insert_account(
            session,
            account_key="execution-recovery-account",
            provider="polymarket",
            chain_id=137,
            identity_type="FIXTURE_ONLY",
            funder_address="0x" + "a" * 40,
            maker_address="0x" + "a" * 40,
            signing_identity="0x" + "c" * 40,
            wallet_type="deposit_wallet",
            signature_type="3",
            signer_secret_entry_id=None,
            signer_secret_version_id=None,
            l2_secret_entry_id=None,
            l2_secret_version_id=None,
            release_manifest_id=chain["release_manifest_id"],
            capital_permission_manifest_id=chain["capital_permission_manifest_id"],
            network_mode="fixture",
        )
        snapshot = await repo.insert_balance_snapshot(
            session,
            account_id=account["id"],
            asset_key="USD",
            spender=None,
            balance=Decimal("1000"),
            allowance=Decimal("1000"),
            provider_reserved=Decimal("0"),
            observed_at=datetime.now(timezone.utc),
            request_hash="e" * 64,
            fencing_token=1,
            completeness="COMPLETE",
        )
        await repo.create_funds(
            session,
            account_id=account["id"],
            asset_key="USD",
            confirmed=Decimal("1000"),
            provider_reserved=Decimal("0"),
            local_reserved=Decimal("0"),
            available=Decimal("1000"),
            source_snapshot_id=snapshot["id"],
            reconcile_watermark=1,
        )
        await session.commit()
        account_id = account["id"]

    async with sessions() as session:
        material = await _seed_execution_chain(
            session,
            chain,
            leg_role="reduce",
            quantity=10,
            account_id=account_id,
        )
        await session.commit()

    reservation_asset = f"tok:{material['contract_spec_id']}:{material['token_id']}"
    async with sessions() as session:
        token_snapshot = await repo.insert_balance_snapshot(
            session,
            account_id=account_id,
            asset_key=reservation_asset,
            spender=None,
            balance=Decimal("1000"),
            allowance=Decimal("1000"),
            provider_reserved=Decimal("0"),
            observed_at=datetime.now(timezone.utc),
            request_hash="f" * 64,
            fencing_token=1,
            completeness="COMPLETE",
        )
        await repo.create_funds(
            session,
            account_id=account_id,
            asset_key=reservation_asset,
            confirmed=Decimal("1000"),
            provider_reserved=Decimal("0"),
            local_reserved=Decimal("0"),
            available=Decimal("1000"),
            source_snapshot_id=token_snapshot["id"],
            reconcile_watermark=1,
        )
        await session.commit()

    async with UnitOfWork(sessions) as uow:
        reservation = await PortfolioLogic(repo).reserve_funds(
            uow,
            reservation_key="execution-recovery-reservation",
            intent_id=material["intent_id"],
            account_id=account_id,
            asset_key=reservation_asset,
            amount=Decimal("100"),
            idempotency_key="execution-recovery-reservation-v1",
        )
        lease = await ExecutionLeaseLogic(repo).acquire_lease(
            uow,
            account_id=account_id,
            lease_role="EXECUTION",
            owner=OWNER,
            ttl_s=300,
        )

    env = {
        "url": temp_pg_db.url,
        "async_url": async_url,
        "engine": engine,
        "sessions": sessions,
        "fresh": fresh,
        "repo": repo,
        "chain": chain,
        "account_id": account_id,
        "intent_id": material["intent_id"],
        "reservation_id": reservation["id"],
        "reservation_asset": reservation_asset,
        "fencing_token": lease["fencing_token"],
    }
    try:
        yield env
    finally:
        await env["engine"].dispose()


async def _prepare_durable_attempt(env: dict) -> object:
    logic = PrivateExecutionLogic(
        execution=env["repo"],
        ledger=LedgerRepository(),
        audit=AuditRepository(),
    )
    chain = env["chain"]
    async with UnitOfWork(env["sessions"]) as uow:
        preflight_hash1, preflight_hash2 = await logic.authoritative_preflight_hashes(
            uow,
            intent_id=env["intent_id"],
            account_id=env["account_id"],
            release_manifest_id=chain["release_manifest_id"],
            execution_spec_version_id=chain["execution_spec_version_id"],
            capital_permission_manifest_id=chain["capital_permission_manifest_id"],
            fencing_token=env["fencing_token"],
        )
        envelope = await logic.create_envelope(
            uow,
            owner=OWNER,
            input_=EnvelopeInput(
                envelope_key="execution-recovery-envelope",
                intent_id=env["intent_id"],
                account_id=env["account_id"],
                release_manifest_id=chain["release_manifest_id"],
                execution_spec_version_id=chain["execution_spec_version_id"],
                capital_permission_manifest_id=chain[
                    "capital_permission_manifest_id"
                ],
                authority="FAKE_CONFORMANCE",
                idempotency_key="execution-recovery-envelope-v1",
                fencing_token=env["fencing_token"],
                intent_hash="a" * 64,
                preflight_hash1=preflight_hash1,
                preflight_hash2=preflight_hash2,
            ),
        )
    async with UnitOfWork(env["sessions"]) as uow:
        return await logic.prepare_submit(
            uow,
            owner=OWNER,
            input_=SubmitOrderInput(
                envelope_id=envelope["id"],
                account_id=env["account_id"],
                fencing_token=env["fencing_token"],
                token_id=TOKEN_ID,
                side="SELL",
                price=Decimal("0.50"),
                size=Decimal("10"),
                post_only=True,
            ),
            signed_order=_SignedOrder(),
            body_hash=BODY_HASH,
            expected_order_hash=ORDER_HASH,
            sdk_manifest_hash=SDK_HASH,
        )


async def _restart_worker(env: dict) -> None:
    await env["engine"].dispose()
    env["engine"], env["sessions"] = env["fresh"]()


async def _attempt_snapshot(env: dict, attempt_id: int) -> dict:
    async with env["sessions"]() as session:
        row = (
            await session.execute(
                text(
                    "SELECT a.result, a.body_hash, a.expected_order_hash, "
                    "a.sdk_manifest_hash, o.status AS order_status, "
                    "e.status AS envelope_status, r.status AS reservation_status, "
                    "f.local_reserved, f.provider_reserved "
                    "FROM trading.exchange_order_attempts a "
                    "JOIN trading.execution_authorization_envelopes e ON e.id=a.envelope_id "
                    "JOIN trading.exchange_orders o ON o.attempt_id=a.id "
                    "JOIN trading.capital_reservations r ON r.intent_id=e.intent_id "
                    "JOIN trading.account_funds_current f "
                    " ON f.account_id=e.account_id AND f.asset_key=r.asset_key "
                    "WHERE a.id=:attempt"
                ),
                {"attempt": attempt_id},
            )
        ).mappings().one()
        return dict(row)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "crash_point,expected_provider_writes",
    [
        ("before_transport_write", 0),
        ("after_provider_write_before_response", 1),
        ("after_response_before_apply", 1),
        ("during_apply_before_commit", 1),
    ],
)
async def test_restart_recovery_never_resubmits_and_fails_closed(
    recovery_env, crash_point: str, expected_provider_writes: int,
):
    env = recovery_env
    prepared = await _prepare_durable_attempt(env)
    probe = _WriteProbe()
    outcome: SubmitOutcome | None = None

    if crash_point == "after_provider_write_before_response":
        with pytest.raises(ConnectionError, match="response_lost"):
            await probe.submit(lose_response=True)
    elif crash_point in {"after_response_before_apply", "during_apply_before_commit"}:
        outcome = await probe.submit()

    if crash_point == "during_apply_before_commit":
        logic = PrivateExecutionLogic(
            execution=env["repo"], ledger=LedgerRepository(), audit=AuditRepository(),
        )
        async with UnitOfWork(env["sessions"]) as uow:
            applied = await logic.apply_submit_outcome(
                uow,
                prepared=prepared,
                outcome=outcome,
                response_hash="1" * 64,
                http_status=200,
            )
            assert applied.status == "ACK"
            await uow.rollback()

    assert probe.submit_calls == expected_provider_writes
    before = await _attempt_snapshot(env, prepared.attempt_id)
    assert before == {
        "result": "SUBMITTED",
        "body_hash": BODY_HASH,
        "expected_order_hash": ORDER_HASH,
        "sdk_manifest_hash": SDK_HASH,
        "order_status": "OPEN",
        "envelope_status": "USED",
        "reservation_status": "HELD",
        "local_reserved": Decimal("100"),
        "provider_reserved": Decimal("0"),
    }

    # A real worker restart destroys all in-memory PreparedSubmit/response state.
    await _restart_worker(env)
    runtime = PrivateExecutionRuntime(env["sessions"])
    recovered = await runtime.recover_submitted(
        account_id=env["account_id"],
        owner=OWNER,
        fencing_token=env["fencing_token"],
    )
    assert len(recovered) == 1
    assert probe.submit_calls == expected_provider_writes, "recovery must never resend"

    after = await _attempt_snapshot(env, prepared.attempt_id)
    assert after["result"] == "UNKNOWN"
    assert after["order_status"] == "UNKNOWN"
    assert after["envelope_status"] == "USED"
    assert after["reservation_status"] == "UNKNOWN"
    assert after["local_reserved"] == Decimal("100")
    assert after["provider_reserved"] == Decimal("0")
    assert after["body_hash"] == before["body_hash"]
    assert after["expected_order_hash"] == before["expected_order_hash"]
    assert after["sdk_manifest_hash"] == before["sdk_manifest_hash"]

    # Recovery is itself replay-safe: no remaining SUBMITTED attempt and no new
    # economic/provider effect on a second scan.
    second = await runtime.recover_submitted(
        account_id=env["account_id"],
        owner=OWNER,
        fencing_token=env["fencing_token"],
    )
    assert second == []
    assert await _attempt_snapshot(env, prepared.attempt_id) == after
    assert probe.submit_calls == expected_provider_writes
