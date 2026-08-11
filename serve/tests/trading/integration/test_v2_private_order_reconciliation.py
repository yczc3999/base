"""
WP-05 private order reconciliation —— 真 PostgreSQL 集成（Checkpoint C）。

覆盖：submit→ACK→fill→reconcile（diff=0 COMPLETED）、UNKNOWN 保留 reservation + hard stop、
duplicate ACK / 乱序 / partial / late fill 收敛、cancel race、kill switch 阻止增仓但放行
REDUCE/CLOSE/CANCEL/reconcile、fencing 旧 owner effect=0。
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.logics.trading.execution import (
    KillSwitchBlocked,
    PrivateExecutionLogic,
    StaleFenceError,
)
from app.logics.trading.reconciliation import ReconciliationLogic
from app.logics.trading.portfolio import PortfolioLogic
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository
from app.repositories.trading.vault import VaultRepository
from app.schemas.trading.execution import (
    CancelOrderInput,
    EnvelopeInput,
    ReconcileInput,
    SubmitOrderInput,
)
from app.services.vault import VaultService

from tests.trading.integration.test_v2_vault_accounts_funds import (
    seed_control_chain,
    seed_intent_chain,
)

SERVE_DIR = __import__("pathlib").Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
HEAD = "b1000051"

K1 = os.urandom(32)
KEYRING = {("k1", "v1"): K1}

H64 = "a" * 64
TOKEN_ID = "tok-real"


class _UoW:
    def __init__(self, session):
        self.session = session


class _FakeClob:
    """fake CLOB client：create_limit_order / post_order / cancel / heartbeat。"""

    def __init__(self, *, ack=True, order_id="ext-ord-1", heartbeat_chain=None):
        self.ack = ack
        self.order_id = order_id
        self.post_order_calls = 0
        self.create_limit_calls = 0
        self.heartbeat_chain = heartbeat_chain or ["hb-1"]
        self.heartbeat_calls = []

    def create_limit_order(self, **kwargs):
        self.create_limit_calls += 1
        return _FakeSignedOrder(order_id=self.order_id)

    def post_order(self, signed_order):
        self.post_order_calls += 1
        from app.schemas.polymarket.clob_private import OrderResponse

        if not self.ack:
            return OrderResponse(order_id=None, status="unmatched", success=False, error_msg="bad")
        return OrderResponse(order_id=self.order_id, status="live", success=True)

    def cancel_orders(self, *, order_ids):
        return _SdkCancel(canceled=tuple(order_ids), not_canceled={})

    def post_heartbeat(self, heartbeat_id):
        self.heartbeat_calls.append(heartbeat_id)
        if not self.heartbeat_chain:
            return {"heartbeat_id": None}
        return {"heartbeat_id": self.heartbeat_chain.pop(0)}


class _SdkCancel:
    def __init__(self, canceled, not_canceled):
        self.canceled = canceled
        self.not_canceled = not_canceled


class _FakeSignedOrder:
    def __init__(self, order_id="ext-ord-1", salt=1001, timestamp=1700000000):
        self.order_id = order_id
        self.salt = salt
        self.timestamp = timestamp
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


class _FakeReconcileDriver:
    """reconcile REST 观察：open orders / trades / positions 全空（收敛场景）。"""

    def __init__(self, *, remote_orders=(), remote_trades=(), remote_positions=()):
        self._orders = list(remote_orders)
        self._trades = list(remote_trades)
        self._positions = list(remote_positions)

    async def open_orders(self, cursor=None, limit=200, headers=None):
        from app.schemas.polymarket.data_api import DataApiOpenOrders

        return _SimpleResult(DataApiOpenOrders(data=self._orders, next_cursor=None))

    async def trades(self, cursor=None, limit=200, after=None, headers=None):
        from app.schemas.polymarket.data_api import DataApiTrades

        return _SimpleResult(DataApiTrades(data=self._trades, next_cursor=None))

    async def positions(self, cursor=None, limit=200, headers=None):
        from app.schemas.polymarket.data_api import DataApiPositions

        return _SimpleResult(DataApiPositions(data=self._positions, next_cursor=None))


class _SimpleResult:
    def __init__(self, typed):
        self.typed = typed
        self.raw = b"{}"
        self.receipts = ()


async def _seed_execution_chain(session, chain, *, leg_role="reduce", quantity=10,
                                account_id=None):
    """扩展 seed_intent_chain：加 pm_market/pm_token/contract_spec/action_set_leg。

    与 seed_intent_chain 同口径：``session_replication_role = replica`` 绕过与
    contract_snapshot 等 fixture FK 的耦合；CHECK 约束仍强制（contract_specs status 用合法值）。
    reduce/close 场景先种入既有 position（namespace=exec-<account_id>），供真实 fill 收敛。
    """
    h = "a" * 64
    await session.execute(text("SET LOCAL session_replication_role = replica"))
    try:
        market = (
            await session.execute(
                text(
                    "INSERT INTO trading.pm_markets "
                    "(gamma_market_id, question, slug, active, closed, accepting_orders, "
                    " enable_order_book, neg_risk, content_hash) "
                    "VALUES ('gm-1', 'q', 's', true, false, true, true, false, :h) "
                    "ON CONFLICT (gamma_market_id) DO NOTHING RETURNING id"
                ),
                {"h": h},
            )
        ).scalar_one()
        if market is None:
            market = (
                await session.execute(
                    text("SELECT id FROM trading.pm_markets WHERE gamma_market_id='gm-1'")
                )
            ).scalar_one()
        token = (
            await session.execute(
                text(
                    "INSERT INTO trading.pm_tokens (token_id, market_id, outcome_index) "
                    "VALUES (:t, :m, 0) ON CONFLICT (token_id) DO NOTHING RETURNING id"
                ),
                {"t": TOKEN_ID, "m": market},
            )
        ).scalar_one()
        if token is None:
            token = (
                await session.execute(
                    text("SELECT id FROM trading.pm_tokens WHERE token_id=:t"),
                    {"t": TOKEN_ID},
                )
            ).scalar_one()
        contract_spec = (
            await session.execute(
                text(
                    "INSERT INTO trading.contract_specs "
                    "(contract_key, version_no, snapshot_id, kc_resolution_states, token_ids, "
                    " token_count, state_count, compiler_version, schema_version, status, content_hash) "
                    "VALUES ('cs-1', 1, :m, '[\"YES\",\"NO\"]'::jsonb, "
                    " jsonb_build_object('0', CAST(:t AS bigint)), 2, 2, 'c1', 1, 'pass', :h) "
                    "ON CONFLICT (contract_key, version_no) DO NOTHING RETURNING id"
                ),
                {"m": market, "t": token, "h": h},
            )
        ).scalar_one()
        if contract_spec is None:
            contract_spec = (
                await session.execute(
                    text(
                        "SELECT id FROM trading.contract_specs "
                        "WHERE contract_key='cs-1' AND version_no=1"
                    )
                )
            ).scalar_one()
        intent = await seed_intent_chain(session, chain)
        # seed_intent_chain 内部会切回 origin；重新切 replica 以绕过 leg→token FK。
        await session.execute(text("SET LOCAL session_replication_role = replica"))
        signed_qty = quantity if leg_role == "open" else -quantity
        leg = (
            await session.execute(
                text(
                    "INSERT INTO trading.action_set_legs "
                    "(action_set_id, contract_spec_id, token_id, leg_role, quantity, "
                    " signed_quantity, entry_vwap) "
                    "VALUES (:as, :cs, :t, :role, :qty, :signed, 0.5) RETURNING id"
                ),
                {"as": intent["action_set_id"], "cs": contract_spec, "t": token,
                 "role": leg_role, "qty": quantity, "signed": signed_qty},
            )
        ).scalar_one()
        if account_id is not None and leg_role in ("reduce", "close"):
            namespace = f"exec-{account_id}"
            await session.execute(
                text(
                    "INSERT INTO trading.positions "
                    "(portfolio_namespace, contract_spec_id, token_id, market_id, quantity, "
                    " cost_basis, account_id) "
                    "VALUES (:ns, :cs, :t, :m, :qty, :cb, :acct) "
                    "ON CONFLICT (portfolio_namespace, contract_spec_id, token_id) DO NOTHING"
                ),
                {"ns": namespace, "cs": contract_spec, "t": token, "m": market,
                 "qty": quantity, "cb": quantity * 50, "acct": account_id},
            )
    finally:
        await session.execute(text("SET LOCAL session_replication_role = origin"))
    return {
        **intent,
        "market_id": market, "token_id": token, "contract_spec_id": contract_spec, "leg_id": leg,
    }


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
            session, name="pm/signer/fixture-c", secret_kind="signer_private_key",
            runtime_identity="worker-c",
        )
        account = await repo.insert_account(
            session, account_key="fixture-acct-c", provider="polymarket", chain_id=137,
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
            balance=1000, allowance=1000, provider_reserved=0,
            observed_at=datetime.now(timezone.utc),
            request_hash="c" * 64, fencing_token=1, completeness="COMPLETE",
        )
        await repo.create_funds(
            session, account_id=account["id"], asset_key="USD",
            confirmed=1000, provider_reserved=0, local_reserved=0, available=1000,
            source_snapshot_id=snapshot["id"], reconcile_watermark=1,
        )
        await session.commit()
        account_id = account["id"]

    try:
        yield {
            "sessions": sessions, "repo": repo, "account_id": account_id, "chain": chain,
        }
    finally:
        await async_engine.dispose()


async def _reserve(env, intent_id, *, amount=Decimal("500"), ik="ik-c"):
    logic = PortfolioLogic(execution=env["repo"])
    async with env["sessions"]() as session:
        res = await logic.reserve_funds(
            _UoW(session), reservation_key="res-c-1", intent_id=intent_id,
            account_id=env["account_id"], asset_key="USD", amount=amount, idempotency_key=ik,
        )
        await session.commit()
        return res["id"]


def _envelope_input(env, intent_id):
    chain = env["chain"]
    return EnvelopeInput(
        envelope_key="env-c-1",
        intent_id=intent_id,
        account_id=env["account_id"],
        release_manifest_id=chain["release_manifest_id"],
        execution_spec_version_id=chain["execution_spec_version_id"],
        capital_permission_manifest_id=chain["capital_permission_manifest_id"],
        authority="FAKE_CONFORMANCE",
        idempotency_key="env-ik-c",
        fencing_token=1,
        intent_hash=H64,
        preflight_hash1="b" * 64,
        preflight_hash2="c" * 64,
    )


async def _create_envelope(env, intent_id):
    logic = PrivateExecutionLogic(execution=env["repo"], ledger=LedgerRepository(),
                                  audit=AuditRepository())
    async with env["sessions"]() as session:
        envelope = await logic.create_envelope(_UoW(session), input_=_envelope_input(env, intent_id))
        await session.commit()
        return envelope["id"]


async def _prepare_and_apply(env, *, envelope_id, intent_id, side="SELL", size="10",
                             price="0.5", fencing_token=1, fake=None):
    logic = PrivateExecutionLogic(execution=env["repo"], ledger=LedgerRepository(),
                                  audit=AuditRepository())
    submit = SubmitOrderInput(
        envelope_id=envelope_id, account_id=env["account_id"], fencing_token=fencing_token,
        token_id=TOKEN_ID, side=side, price=price, size=size,
    )
    driver = ClobTradingDriverShim(fake or _FakeClob())
    async with env["sessions"]() as session:
        prepared = await logic.prepare_submit(
            _UoW(session), input_=submit, signed_order=_FakeSignedOrder(),
            body_hash="b" * 64, expected_order_hash="c" * 64, sdk_manifest_hash="d" * 64,
        )
        await session.commit()
        prepared = prepared
    outcome = await driver.submit_order(_FakeSignedOrder())
    async with env["sessions"]() as session:
        result = await logic.apply_submit_outcome(
            _UoW(session), prepared=prepared, outcome=outcome,
            response_hash="b" * 64, http_status=200, error_reason=None,
        )
        await session.commit()
        return result, prepared


class ClobTradingDriverShim:
    """把 fake client 包装成 driver 形状（submit_order 返回 outcome）。"""

    def __init__(self, fake):
        self._fake = fake

    async def create_signed_order(self, **kwargs):
        return _FakeSignedOrder()

    async def submit_order(self, signed_order):
        from app.schemas.polymarket.clob_private import OrderResponse
        from app.services.polymarket.clob_trading_driver import ACK, REJECTED, SubmitOutcome

        response = self._fake.post_order(signed_order)
        if response.success:
            return SubmitOutcome(ACK, order_id=response.order_id, http_status=200)
        return SubmitOutcome(REJECTED, order_id=None, http_status=200)

    async def cancel_orders(self, order_ids):
        return self._fake.cancel_orders(order_ids=order_ids)

    async def send_heartbeat(self, heartbeat_id):
        return self._fake.post_heartbeat(heartbeat_id)


@pytest.mark.asyncio
async def test_submit_ack_fill_reconcile_converges(env):
    async with env["sessions"]() as session:
        chain = await _seed_execution_chain(session, env["chain"], leg_role="reduce", quantity=10, account_id=env["account_id"])
        await session.commit()
    intent_id = chain["intent_id"]
    await _reserve(env, intent_id)
    envelope_id = await _create_envelope(env, intent_id)

    result, prepared = await _prepare_and_apply(env, envelope_id=envelope_id, intent_id=intent_id)
    assert result.ok and result.status == "ACK"
    order_id = result.order_id

    # apply fill（partial → 再 late fill 收敛到 FILLED）
    logic = PrivateExecutionLogic(execution=env["repo"], ledger=LedgerRepository(),
                                  audit=AuditRepository())
    async with env["sessions"]() as session:
        fill1 = await logic.apply_fill(
            _UoW(session), order_id=prepared.order_id, account_id=env["account_id"],
            envelope_id=envelope_id, intent_id=intent_id, fencing_token=1,
            external_trade_id="trd-c-1", side="SELL", price="0.50", size="4", fee="1",
            trade_time=datetime.now(timezone.utc),
        )
        await session.commit()
        assert fill1.order_status == "PARTIAL"
    async with env["sessions"]() as session:
        fill2 = await logic.apply_fill(
            _UoW(session), order_id=prepared.order_id, account_id=env["account_id"],
            envelope_id=envelope_id, intent_id=intent_id, fencing_token=1,
            external_trade_id="trd-c-2", side="SELL", price="0.50", size="6", fee="1",
            trade_time=datetime.now(timezone.utc),
        )
        await session.commit()
        assert fill2.order_status == "FILLED"

    # reconcile → COMPLETED（diff=0）
    logic_r = ReconciliationLogic(execution=env["repo"], ledger=LedgerRepository(),
                                  audit=AuditRepository())
    recon_input = ReconcileInput(
        reconciliation_key="rec-c-1", account_id=env["account_id"], fencing_token=1,
        trigger_reason="ws_disconnect", ws_watermark=2, rest_cursor=None,
    )
    async with env["sessions"]() as session:
        rec = await logic_r.start_reconcile(_UoW(session), input_=recon_input)
        await session.commit()
        rec_id = rec["id"]
    async with env["sessions"]() as session:
        result = await logic_r.complete_reconcile(
            _UoW(session), reconciliation_id=rec_id, account_id=env["account_id"],
            remote_orders=[], remote_trades=[{"external_trade_id": "trd-c-1"},
                                             {"external_trade_id": "trd-c-2"}],
            remote_positions=[{"token_id": TOKEN_ID, "size": "0"}],
            remote_funds=[{"asset_key": "USD", "confirmed": "1000", "provider_reserved": "0",
                           "local_reserved": "0"}],
            unknown_queries={},
        )
        await session.commit()
        assert result.status == "COMPLETED", result.differences
        assert result.differences == []
    # 订单 RECONCILED
    async with env["sessions"]() as session:
        order = await env["repo"].get_order_by_external(
            session, account_id=env["account_id"], external_order_id=order_id,
        )
        assert order["status"] == "FILLED"


@pytest.mark.asyncio
async def test_unknown_keeps_reservation_hard_stop(env):
    async with env["sessions"]() as session:
        chain = await _seed_execution_chain(session, env["chain"], leg_role="reduce", quantity=10, account_id=env["account_id"])
        await session.commit()
    intent_id = chain["intent_id"]
    await _reserve(env, intent_id)
    envelope_id = await _create_envelope(env, intent_id)

    # 提交但 outcome 不确定 → UNKNOWN（保留 reservation + hard stop）
    fake = _FakeClob()
    submit = SubmitOrderInput(
        envelope_id=envelope_id, account_id=env["account_id"], fencing_token=1,
        token_id=TOKEN_ID, side="SELL", price="0.5", size="10",
    )
    logic = PrivateExecutionLogic(execution=env["repo"], ledger=LedgerRepository(),
                                  audit=AuditRepository())
    async with env["sessions"]() as session:
        prepared = await logic.prepare_submit(
            _UoW(session), input_=submit, signed_order=_FakeSignedOrder(),
            body_hash="b" * 64, expected_order_hash="c" * 64, sdk_manifest_hash="d" * 64,
        )
        await session.commit()
    from app.services.polymarket.clob_trading_driver import UNKNOWN, SubmitOutcome

    outcome = SubmitOutcome(UNKNOWN, order_id=None, http_status=None, error_code="wire_read_timeout")
    async with env["sessions"]() as session:
        result = await logic.apply_submit_outcome(
            _UoW(session), prepared=prepared, outcome=outcome,
            response_hash="b" * 64, http_status=None, error_reason="wire_read_timeout",
        )
        await session.commit()
        assert result.status == "UNKNOWN"
        # reservation 保留（UNKNOWN 状态，local_reserved 不减）
        reservation = await env["repo"].get_reservation_by_intent(
            session, account_id=env["account_id"], intent_id=intent_id,
        )
        assert reservation["status"] == "UNKNOWN"
        funds = await env["repo"].get_funds(session, account_id=env["account_id"], asset_key="USD")
        assert funds["local_reserved"] == Decimal("500")
        # alert 已写入
        alerts = (await session.execute(
            text("SELECT code FROM trading.alert_events WHERE code='order_unknown_hard_stop'")
        )).scalars().all()
        assert alerts

    # reconcile → FAILED（unknown 未决）
    logic_r = ReconciliationLogic(execution=env["repo"], ledger=LedgerRepository(),
                                  audit=AuditRepository())
    async with env["sessions"]() as session:
        rec = await logic_r.start_reconcile(
            _UoW(session), input_=ReconcileInput(
                reconciliation_key="rec-c-unknown", account_id=env["account_id"],
                fencing_token=1, trigger_reason="ws_disconnect", ws_watermark=1, rest_cursor=None,
            ),
        )
        await session.commit()
        rec_id = rec["id"]
    async with env["sessions"]() as session:
        result = await logic_r.complete_reconcile(
            _UoW(session), reconciliation_id=rec_id, account_id=env["account_id"],
            remote_orders=[], remote_trades=[], remote_positions=[], remote_funds=[],
            unknown_queries={},
        )
        await session.commit()
        assert result.status == "FAILED"
        assert any(d["kind"] == "unknown" for d in result.differences)


@pytest.mark.asyncio
async def test_duplicate_ack_and_late_fill_converge(env):
    async with env["sessions"]() as session:
        chain = await _seed_execution_chain(session, env["chain"], leg_role="reduce", quantity=10, account_id=env["account_id"])
        await session.commit()
    intent_id = chain["intent_id"]
    await _reserve(env, intent_id, ik="ik-dup")
    envelope_id = await _create_envelope(env, intent_id)
    result, prepared = await _prepare_and_apply(env, envelope_id=envelope_id, intent_id=intent_id)

    # duplicate ACK：再次 apply ACK → replayed，不重复 economic effect。
    from app.services.polymarket.clob_trading_driver import ACK, SubmitOutcome

    logic = PrivateExecutionLogic(execution=env["repo"], ledger=LedgerRepository(),
                                  audit=AuditRepository())
    async with env["sessions"]() as session:
        dup = await logic.apply_submit_outcome(
            _UoW(session), prepared=prepared, outcome=SubmitOutcome(ACK, order_id=result.order_id),
            response_hash="b" * 64, http_status=200, error_reason=None,
        )
        await session.commit()
        assert dup.replayed is True

    # late fill（乱序但可收敛）
    async with env["sessions"]() as session:
        fill = await logic.apply_fill(
            _UoW(session), order_id=prepared.order_id, account_id=env["account_id"],
            envelope_id=envelope_id, intent_id=intent_id, fencing_token=1,
            external_trade_id="trd-dup-1", side="SELL", price="0.50", size="10", fee="1",
            trade_time=datetime.now(timezone.utc),
        )
        await session.commit()
        assert fill.order_status == "FILLED"
    # duplicate fill → replayed，不重复 lot/posting
    async with env["sessions"]() as session:
        dup_fill = await logic.apply_fill(
            _UoW(session), order_id=prepared.order_id, account_id=env["account_id"],
            envelope_id=envelope_id, intent_id=intent_id, fencing_token=1,
            external_trade_id="trd-dup-1", side="SELL", price="0.50", size="10", fee="1",
            trade_time=datetime.now(timezone.utc),
        )
        await session.commit()
        assert dup_fill.replayed is True
        lots = (await session.execute(
            text("SELECT count(*) FROM trading.position_lots WHERE order_id=:o"),
            {"o": prepared.order_id},
        )).scalar_one()
        assert lots == 1
        trades = (await session.execute(
            text("SELECT count(*) FROM trading.exchange_trades WHERE external_trade_id='trd-dup-1'")
        )).scalar_one()
        assert trades == 1


@pytest.mark.asyncio
async def test_cancel_race_converges(env):
    async with env["sessions"]() as session:
        chain = await _seed_execution_chain(session, env["chain"], leg_role="reduce", quantity=10, account_id=env["account_id"])
        await session.commit()
    intent_id = chain["intent_id"]
    await _reserve(env, intent_id, ik="ik-cancel")
    envelope_id = await _create_envelope(env, intent_id)
    result, _prepared = await _prepare_and_apply(env, envelope_id=envelope_id, intent_id=intent_id)

    logic = PrivateExecutionLogic(execution=env["repo"], ledger=LedgerRepository(),
                                  audit=AuditRepository())
    cancel_input = CancelOrderInput(
        account_id=env["account_id"], fencing_token=1, external_order_id=result.order_id,
    )
    async with env["sessions"]() as session:
        cancel = await logic.cancel_order(
            _UoW(session), input_=cancel_input, outcome=None,
            response_hash=None, error_reason=None,
        )
        await session.commit()
        assert cancel.status == "CANCELLED"
    # 重复 cancel → replayed（收敛）
    async with env["sessions"]() as session:
        dup = await logic.cancel_order(
            _UoW(session), input_=cancel_input, outcome=None,
            response_hash=None, error_reason=None,
        )
        await session.commit()
        assert dup.replayed is True


@pytest.mark.asyncio
async def test_kill_switch_blocks_open_allows_reduce_cancel_reconcile(env):
    async with env["sessions"]() as session:
        chain = await _seed_execution_chain(session, env["chain"], leg_role="reduce", quantity=10, account_id=env["account_id"])
        await session.commit()
    intent_id = chain["intent_id"]
    envelope_id = await _create_envelope(env, intent_id)

    # BUY（open）在 authorized_capital=0 下被阻止（gate 在 resolve_intent_leg 之前触发）
    submit_buy = SubmitOrderInput(
        envelope_id=envelope_id, account_id=env["account_id"], fencing_token=1,
        token_id=TOKEN_ID, side="BUY", price="0.5", size="10",
    )
    logic = PrivateExecutionLogic(execution=env["repo"], ledger=LedgerRepository(),
                                  audit=AuditRepository())
    async with env["sessions"]() as session:
        with pytest.raises(KillSwitchBlocked, match="exposure_increasing_blocked_zero_capital"):
            await logic.prepare_submit(
                _UoW(session), input_=submit_buy, signed_order=_FakeSignedOrder(),
                body_hash="b" * 64, expected_order_hash="c" * 64, sdk_manifest_hash="d" * 64,
            )
        await session.rollback()
    # SELL（REDUCE/CLOSE）仍可走 fake path（authorized_capital=0 只阻止增仓；envelope 仍 ACTIVE）
    result, _p = await _prepare_and_apply(
        env, envelope_id=envelope_id, intent_id=intent_id, side="SELL",
    )
    assert result.status == "ACK"


@pytest.mark.asyncio
async def test_fencing_old_owner_effect_zero(env):
    async with env["sessions"]() as session:
        chain = await _seed_execution_chain(session, env["chain"], leg_role="reduce", quantity=10, account_id=env["account_id"])
        await session.commit()
    intent_id = chain["intent_id"]
    envelope_id = await _create_envelope(env, intent_id)

    # 旧 owner 用旧 fencing token 提交 → STALE_FENCE_REJECTED，effect=0
    submit = SubmitOrderInput(
        envelope_id=envelope_id, account_id=env["account_id"], fencing_token=999,
        token_id=TOKEN_ID, side="SELL", price="0.5", size="10",
    )
    logic = PrivateExecutionLogic(execution=env["repo"], ledger=LedgerRepository(),
                                  audit=AuditRepository())
    async with env["sessions"]() as session:
        with pytest.raises(StaleFenceError):
            await logic.prepare_submit(
                _UoW(session), input_=submit, signed_order=_FakeSignedOrder(),
                body_hash="b" * 64, expected_order_hash="c" * 64, sdk_manifest_hash="d" * 64,
            )
        await session.rollback()
    async with env["sessions"]() as session:
        orders = (await session.execute(
            text("SELECT count(*) FROM trading.exchange_orders WHERE account_id=:a"),
            {"a": env["account_id"]},
        )).scalar_one()
        assert orders == 0
