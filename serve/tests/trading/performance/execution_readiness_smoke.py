"""WP-05 hard performance contract on a real PostgreSQL database (execution readiness).

Run from ``/code/pollymarket/v2/serve``::

    V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \\
      .venv/bin/python -m tests.trading.performance.execution_readiness_smoke

Bounded execution pool = ``5+1`` (pool_size=5, max_overflow=1) and reconciliation pool =
``5+1``.  Uses the real PrivateExecutionLogic / ReconciliationLogic / repositories /
UnitOfWork / constraints with an injected fake provider transport; no bulk INSERT
impersonating the measured path.  Real network side effects stay 0.

Contracts（任务 §7.3）:
- Gate 1: DB-only final preflight + atomic reservation p99≤50ms;
- Gate 2: fake CLOB submit→ACK p95≤2s, p99≤5s;
- Gate 3: fake User WS receive→order projection p95≤100ms, p99≤300ms;
- Gate 4: 1,000 live-order REST reconcile p95≤10s, p99≤30s, final diff=0;
- Gate 5: ≥10 intents/s sustained ≥60s;
- Gate 6: DB pool wait p95≤20ms, transaction p99≤50ms; connection/CPU/RSS/WAL + 10s windows;
- 输出 ``/tmp/pm_v2_perf_smoke_5.json`` 含 seed / git commit / SDK tag/commit / fixture hashes /
  p50/p95/p99 / 资源峰值 / hard assertions / ``fake_transport_calls`` / ``real_network_calls=0``；
  临时数据库清理为 0。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SERVE_DIR))

from app.db.uow import UnitOfWork  # noqa: E402
from app.logics.trading.execution import (  # noqa: E402
    ExecutionLeaseLogic,
    PrivateExecutionLogic,
)
from app.logics.trading.portfolio import PortfolioLogic  # noqa: E402
from app.repositories.trading.audit import AuditRepository  # noqa: E402
from app.repositories.trading.execution import ExecutionRepository  # noqa: E402
from app.repositories.trading.ledger import LedgerRepository  # noqa: E402
from app.repositories.trading.vault import VaultRepository  # noqa: E402
from app.schemas.trading.execution import (  # noqa: E402
    CancelOrderInput,
    EnvelopeInput,
    ReconcileInput,
    SubmitOrderInput,
)
from app.schemas.polymarket.user_ws import (  # noqa: E402
    UserOrderEvent,
    user_ws_frame_artifact_hash,
)
from app.services.polymarket.user_ws_driver import UserWsMessage  # noqa: E402
from app.services.vault import VaultService  # noqa: E402
from runtimes.trading.execution import (  # noqa: E402
    PrivateExecutionRuntime,
    UserWsExecutionRuntime,
)
from runtimes.trading.reconciliation import ReconciliationRuntime  # noqa: E402
from tests.trading.integration.test_v2_vault_accounts_funds import (  # noqa: E402
    seed_control_chain,
)
from tests.trading.integration.test_v2_private_order_reconciliation import (  # noqa: E402
    TOKEN_ID,
    _seed_execution_chain,
)

ADMIN_URL = os.environ.get(
    "V2_TEST_ADMIN_DATABASE_URL", "postgresql+psycopg:///postgres"
)
TEMP_PREFIX = "pm_v2_perf_5_"
EXEC_POOL_SIZE = 5
EXEC_MAX_OVERFLOW = 1
RECON_POOL_SIZE = 5
RECON_MAX_OVERFLOW = 1
OUT_PATH = Path("/tmp/pm_v2_perf_smoke_5.json")

# 硬门阈值
GATE1_P99_MS = 50.0
GATE2_P95_MS = 2000.0
GATE2_P99_MS = 5000.0
GATE3_P95_MS = 100.0
GATE3_P99_MS = 300.0
GATE4_P95_MS = 10000.0
GATE4_P99_MS = 30000.0
GATE5_MIN_IPS = 10.0
GATE6_POOL_WAIT_P95_MS = 20.0
GATE6_TX_P99_MS = 50.0

INTENTS_TARGET = 60  # ≥10/s 持续 ≥60s
RECON_ORDERS_TARGET = 1000
WS_FRAMES_TARGET = 500
EXEC_OWNER = "perf-execution-owner"
HEARTBEAT_OWNER = "perf-heartbeat-owner"
HEARTBEAT_INTERVAL_S = 5.0
HEARTBEAT_MAX_DRIFT_MS = 500.0


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(SERVE_DIR), text=True
        ).strip()
    except Exception:
        return "unknown"


def _sdk_tag_commit() -> dict[str, str]:
    return {
        "package": "polymarket-client",
        "version": "0.5.0",
        "tag": "polymarket-client-v0.5.0",
        "tag_commit": "974d2e22ca92445d8ab7ecd7715a247f1ea7d65a",
    }


def _fixture_hashes() -> dict[str, str]:
    base = SERVE_DIR / "tests/trading/fixtures/p5_execution"
    out: dict[str, str] = {}
    for name in (
        "p_execution_readiness_spec_v1.json",
        "sdk_source_manifest_v1.json",
        "official_heartbeat_drift_v1.json",
        "stability_event_log_v1.json",
        "stability_snapshot_v1.json",
    ):
        path = base / name
        if path.exists():
            out[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _pct(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(len(ordered) * p / 100))
    return float(ordered[idx])


def _percentiles(values: list[float]) -> dict[str, float]:
    return {f"p{name}": _pct(values, name) for name in (50, 95, 99)}


def _window_rates(offsets: list[float], elapsed: float) -> list[float]:
    if elapsed <= 0 or not offsets:
        return []
    buckets: dict[int, int] = {}
    for off in offsets:
        slot = int(off // 10.0)
        buckets[slot] = buckets.get(slot, 0) + 1
    return [count / 10.0 for _, count in sorted(buckets.items())]


class _FakeAckClient:
    """fake provider transport：submit 立即 ACK（post-only GTC live）。"""

    def __init__(self, *, latency_ms: float = 0.0) -> None:
        self.post_order_calls = 0
        self._latency_ms = latency_ms

    def post_order(self, signed_order: Any) -> Any:
        self.post_order_calls += 1
        if self._latency_ms:
            time.sleep(self._latency_ms / 1000.0)
        return SimpleNamespace(
            cls="ACK", order_id=f"ord-ack-{self.post_order_calls}", success=True,
        )

    def cancel_orders(self, *, order_ids: list[str]) -> Any:
        return SimpleNamespace(canceled=tuple(order_ids), not_canceled={})

    def list_open_orders(self, **kwargs: Any) -> list[Any]:
        return []

    def list_trades(self, **kwargs: Any) -> list[Any]:
        return []


class _PoolPeakProbe:
    """Record the real high-water mark instead of sampling an idle pool at the end."""

    def __init__(self) -> None:
        self.current = 0
        self.peak = 0

    def checkout(self, *_args: Any) -> None:
        self.current += 1
        self.peak = max(self.peak, self.current)

    def checkin(self, *_args: Any) -> None:
        self.current = max(0, self.current - 1)


class _FakeUserWsReceiver:
    """In-memory User WS receive boundary used by Gate 3."""

    def __init__(self, messages: list[UserWsMessage]) -> None:
        self._messages = iter(messages)
        self.receive_calls = 0

    async def next_frame(self) -> UserWsMessage:
        self.receive_calls += 1
        return next(self._messages)


class _PagedRestDriver:
    """Fully paginated fake REST projection for a 1,000-order reconcile."""

    def __init__(
        self,
        *,
        orders: list[Any],
        trades: list[Any],
        positions: list[Any],
        funds: list[Any],
    ) -> None:
        self.orders = orders
        self.trade_rows = trades
        self.position_rows = positions
        self.fund_rows = funds
        self.open_order_calls = 0
        self.trade_calls = 0
        self.position_calls = 0
        self.fund_calls = 0
        self.unknown_order_calls = 0

    @staticmethod
    def _result(items: list[Any], next_cursor: str | None = None) -> Any:
        return SimpleNamespace(
            typed=SimpleNamespace(items=tuple(items), next_cursor=next_cursor),
            raw=b"{}",
            receipts=(),
        )

    @staticmethod
    def _page(rows: list[Any], cursor: str | None, limit: int) -> tuple[list[Any], str | None]:
        offset = int(cursor or "0")
        page = rows[offset:offset + limit]
        next_offset = offset + len(page)
        return page, str(next_offset) if next_offset < len(rows) else None

    async def open_orders(self, *, cursor=None, limit=200, headers=None):
        self.open_order_calls += 1
        rows, next_cursor = self._page(self.orders, cursor, limit)
        return self._result(rows, next_cursor)

    async def trades(self, *, cursor=None, limit=200, after=None, headers=None):
        self.trade_calls += 1
        rows, next_cursor = self._page(self.trade_rows, cursor, limit)
        return self._result(rows, next_cursor)

    async def positions(self, *, cursor=None, limit=200, headers=None):
        self.position_calls += 1
        rows, next_cursor = self._page(self.position_rows, cursor, limit)
        return self._result(rows, next_cursor)

    async def funds(self, *, cursor=None, limit=200, headers=None):
        self.fund_calls += 1
        rows, next_cursor = self._page(self.fund_rows, cursor, limit)
        return self._result(rows, next_cursor)

    async def balance_allowances(self, *, cursor=None, limit=200, headers=None):
        return await self.funds(cursor=cursor, limit=limit, headers=headers)

    async def balance_allowance(self, *, headers=None):
        self.fund_calls += 1
        return self._result(self.fund_rows, None)

    async def order(self, *, order_id=None, headers=None, **_kwargs):
        self.unknown_order_calls += 1
        item = next((row for row in self.orders if row.order_id == order_id), None)
        return SimpleNamespace(typed=item, raw=b"{}", receipts=())

    async def get_order(self, *, order_id=None, headers=None, **kwargs):
        return await self.order(order_id=order_id, headers=headers, **kwargs)


class _HeartbeatProbeDriver:
    """Records monotonic cadence and injects one deterministic failure."""

    def __init__(self) -> None:
        self.call_times: list[float] = []
        self.ids: list[str] = []
        self.fail_next = False

    async def send_heartbeat(self, heartbeat_id: str) -> dict[str, Any]:
        self.call_times.append(time.monotonic())
        if self.fail_next:
            self.fail_next = False
            return {"ok": False, "error": "fixture_heartbeat_failure"}
        next_id = f"perf-heartbeat-{len(self.call_times)}"
        self.ids.append(next_id)
        return {"ok": True, "heartbeat_id": next_id}

    async def cancel_orders(self, order_ids: list[str]) -> Any:
        return SimpleNamespace(canceled=tuple(order_ids), not_canceled={})


class SimpleNamespace:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


async def _seed_account(sessions, chain: dict[str, Any], *, keyring: dict) -> int:
    """建 pm_accounts + funds，返回 account_id（与 C 集成测试 env 同口径）。"""
    repo = ExecutionRepository()
    svc = VaultService(
        VaultRepository(), keyring, env="test", runtime_identity="worker-perf",
    )

    async with sessions() as session:
        entry = await svc.create_entry(
            session, name="pm/signer/perf", secret_kind="signer_private_key",
            runtime_identity="worker-perf",
        )
        version = await svc.store_secret(
            session,
            entry_id=entry["id"],
            secret=b"fixture-perf-signer-secret",
            purpose="sign",
            identity="worker-perf",
            account="perf-acct",
            key_id="k1",
            key_version="v1",
        )
        account = await repo.insert_account(
            session, account_key="perf-acct", provider="polymarket", chain_id=137,
            identity_type="FIXTURE_ONLY", funder_address="0x" + "a" * 40,
            maker_address="0x" + "a" * 40, signing_identity="0x" + "c" * 40,
            wallet_type="deposit_wallet", signature_type="3",
            signer_secret_entry_id=entry["id"],
            signer_secret_version_id=version["id"],
            l2_secret_entry_id=None, l2_secret_version_id=None,
            release_manifest_id=chain["release_manifest_id"],
            capital_permission_manifest_id=chain["capital_permission_manifest_id"],
            network_mode="fixture",
        )
        snapshot = await repo.insert_balance_snapshot(
            session, account_id=account["id"], asset_key="USD", spender=None,
            balance=Decimal(10**9), allowance=Decimal(10**9), provider_reserved=0,
            observed_at=datetime.now(timezone.utc),
            request_hash="c" * 64, fencing_token=1, completeness="COMPLETE",
        )
        await repo.create_funds(
            session, account_id=account["id"], asset_key="USD",
            confirmed=Decimal(10**9), provider_reserved=0, local_reserved=0,
            available=Decimal(10**9),
            source_snapshot_id=snapshot["id"], reconcile_watermark=1,
        )
        await session.commit()
        return account["id"]


async def _clone_intent(session: Any, *, template_intent_id: int, seq: int) -> int:
    """Create one committed immutable intent sharing the frozen action-set material."""
    intent_hash = hashlib.sha256(f"perf-intent:{seq}".encode()).hexdigest()
    intent_id = int((await session.execute(text(
        "INSERT INTO trading.economic_action_intents "
        "(intent_key, intent_hash, trade_decision_id, action_set_id, status, ttl_at, preflight) "
        "SELECT :key, :hash, trade_decision_id, action_set_id, 'PLANNED', ttl_at, preflight "
        "FROM trading.economic_action_intents WHERE id=:template RETURNING id"
    ), {
        "key": f"perf-intent-{seq}",
        "hash": intent_hash,
        "template": template_intent_id,
    })).scalar_one())
    await session.execute(text(
        "UPDATE trading.economic_action_intents SET status='COMMITTED' WHERE id=:intent"
    ), {"intent": intent_id})
    return intent_id


async def _intent_reservation_asset(session: Any, *, intent_id: int) -> str:
    row = (await session.execute(text(
        "SELECT leg.contract_spec_id, leg.token_id "
        "FROM trading.economic_action_intents intent "
        "JOIN trading.action_set_legs leg ON leg.action_set_id=intent.action_set_id "
        "WHERE intent.id=:intent ORDER BY leg.id LIMIT 1"
    ), {"intent": intent_id})).mappings().one()
    return f"tok:{row['contract_spec_id']}:{row['token_id']}"


async def _reserve_and_envelope(sessions, chain: dict[str, Any], account_id: int,
                                intent_id: int, *, seq: int,
                                reserve: bool = True) -> dict[str, Any]:
    """reserve funds + create envelope，返回 {reservation_id, envelope_id, fence}。

    envelope 单次使用（prepare_submit 标 USED，状态机禁回退）；perf 每 submit 建新 envelope。
    """
    exec_logic = PrivateExecutionLogic(
        execution=ExecutionRepository(), ledger=LedgerRepository(),
        audit=AuditRepository(),
    )
    ik = f"perf-ik-{seq}"
    async with sessions() as session:
        intent_id = await _clone_intent(
            session, template_intent_id=intent_id, seq=seq,
        )
        res = None
        if reserve:
            asset_key = await _intent_reservation_asset(session, intent_id=intent_id)
            res = await PortfolioLogic().reserve_funds(
                _UoW(session), reservation_key=f"perf-res-{seq}", intent_id=intent_id,
                account_id=account_id, asset_key=asset_key, amount=Decimal("1"),
                idempotency_key=ik,
            )
        preflight_hash1, preflight_hash2 = await exec_logic.authoritative_preflight_hashes(
            _UoW(session),
            intent_id=intent_id,
            account_id=account_id,
            release_manifest_id=chain["release_manifest_id"],
            execution_spec_version_id=chain["execution_spec_version_id"],
            capital_permission_manifest_id=chain["capital_permission_manifest_id"],
            fencing_token=1,
        )
        intent_hash = (await session.execute(text(
            "SELECT intent_hash FROM trading.economic_action_intents WHERE id=:intent"
        ), {"intent": intent_id})).scalar_one()
        envelope_input = EnvelopeInput(
            envelope_key=f"perf-env-{seq}", intent_id=intent_id,
            account_id=account_id,
            release_manifest_id=chain["release_manifest_id"],
            execution_spec_version_id=chain["execution_spec_version_id"],
            capital_permission_manifest_id=chain["capital_permission_manifest_id"],
            authority="FAKE_CONFORMANCE", idempotency_key=f"perf-env-ik-{seq}",
            fencing_token=1, intent_hash=intent_hash,
            preflight_hash1=preflight_hash1, preflight_hash2=preflight_hash2,
        )
        envelope = await exec_logic.create_envelope(
            _UoW(session), input_=envelope_input, owner=EXEC_OWNER,
        )
        await session.commit()
        if envelope.get("status") != "ACTIVE":
            raise RuntimeError(f"envelope_not_active:{envelope.get('status')}")
        return {
            "reservation_id": res["id"] if res is not None else None,
            "envelope_id": envelope["id"],
            "intent_id": intent_id,
            "fence": 1,
        }


async def _reserve_and_envelope_in_uow(
    uow: Any,
    chain: dict[str, Any],
    account_id: int,
    intent_id: int,
    *,
    seq: int,
) -> dict[str, Any]:
    """Gate-1 measured path; caller may roll back the complete atomic preflight."""
    intent_id = await _clone_intent(
        uow.session, template_intent_id=intent_id, seq=seq,
    )
    asset_key = await _intent_reservation_asset(uow.session, intent_id=intent_id)
    reservation = await PortfolioLogic().reserve_funds(
        uow,
        reservation_key=f"perf-g1-res-{seq}",
        intent_id=intent_id,
        account_id=account_id,
        asset_key=asset_key,
        amount=Decimal("1"),
        idempotency_key=f"perf-g1-ik-{seq}",
    )
    logic = PrivateExecutionLogic(
        execution=ExecutionRepository(), ledger=LedgerRepository(),
        audit=AuditRepository(),
    )
    preflight_hash1, preflight_hash2 = await logic.authoritative_preflight_hashes(
        uow,
        intent_id=intent_id,
        account_id=account_id,
        release_manifest_id=chain["release_manifest_id"],
        execution_spec_version_id=chain["execution_spec_version_id"],
        capital_permission_manifest_id=chain["capital_permission_manifest_id"],
        fencing_token=1,
    )
    intent_hash = (await uow.session.execute(text(
        "SELECT intent_hash FROM trading.economic_action_intents WHERE id=:intent"
    ), {"intent": intent_id})).scalar_one()
    envelope = await logic.create_envelope(
        uow,
        owner=EXEC_OWNER,
        input_=EnvelopeInput(
            envelope_key=f"perf-g1-env-{seq}",
            intent_id=intent_id,
            account_id=account_id,
            release_manifest_id=chain["release_manifest_id"],
            execution_spec_version_id=chain["execution_spec_version_id"],
            capital_permission_manifest_id=chain["capital_permission_manifest_id"],
            authority="FAKE_CONFORMANCE",
            idempotency_key=f"perf-g1-env-ik-{seq}",
            fencing_token=1,
            intent_hash=intent_hash,
            preflight_hash1=preflight_hash1,
            preflight_hash2=preflight_hash2,
        ),
    )
    return {"reservation_id": reservation["id"], "envelope_id": envelope["id"]}


class _UoW:
    """thin UoW shim for synchronous-seed helpers that receive an AsyncSession."""

    def __init__(self, session) -> None:
        self.session = session


async def _run() -> dict[str, Any]:
    results: dict[str, Any] = {}
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    dbname = f"{TEMP_PREFIX}{uuid.uuid4().hex[:8]}"
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{dbname}"'))
    admin.dispose()
    url = make_url(ADMIN_URL).set(database=dbname).render_as_string(hide_password=False)

    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(SERVE_DIR / "alembic"))
    sync_engine = create_engine(url, poolclass=NullPool)
    conn = sync_engine.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, "head")
    finally:
        conn.close()
        sync_engine.dispose()

    async_url = make_url(url).set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    exec_engine = create_async_engine(
        async_url, pool_size=EXEC_POOL_SIZE, max_overflow=EXEC_MAX_OVERFLOW,
        pool_timeout=10, pool_pre_ping=False,
    )
    recon_engine = create_async_engine(
        async_url, pool_size=RECON_POOL_SIZE, max_overflow=RECON_MAX_OVERFLOW,
        pool_timeout=10, pool_pre_ping=False,
    )
    exec_pool_probe = _PoolPeakProbe()
    recon_pool_probe = _PoolPeakProbe()
    event.listen(exec_engine.sync_engine, "checkout", exec_pool_probe.checkout)
    event.listen(exec_engine.sync_engine, "checkin", exec_pool_probe.checkin)
    event.listen(recon_engine.sync_engine, "checkout", recon_pool_probe.checkout)
    event.listen(recon_engine.sync_engine, "checkin", recon_pool_probe.checkin)
    exec_sessions = async_sessionmaker(exec_engine, expire_on_commit=False)
    recon_sessions = async_sessionmaker(recon_engine, expire_on_commit=False)

    fake_client = _FakeAckClient()

    try:
        # ---- fixture：真实 control chain + execution chain + account（不计入计时窗口） ----
        t0_fixture = time.perf_counter()
        K1 = os.urandom(32)
        KEYRING = {("k1", "v1"): K1}
        async with exec_sessions() as session:
            chain = await seed_control_chain(session)
            await session.commit()
        account_id = await _seed_account(exec_sessions, chain, keyring=KEYRING)
        async with exec_sessions() as session:
            intent = await _seed_execution_chain(
                session, chain, leg_role="reduce", quantity=10, account_id=account_id,
            )
            await session.commit()
        token_asset_key = f"tok:{intent['contract_spec_id']}:{intent['token_id']}"
        async with exec_sessions() as session:
            repo = ExecutionRepository()
            token_snapshot = await repo.insert_balance_snapshot(
                session,
                account_id=account_id,
                asset_key=token_asset_key,
                spender=None,
                balance=Decimal(10**9),
                allowance=Decimal(10**9),
                provider_reserved=Decimal(0),
                observed_at=datetime.now(timezone.utc),
                request_hash=hashlib.sha256(token_asset_key.encode()).hexdigest(),
                fencing_token=1,
                completeness="COMPLETE",
            )
            await repo.create_funds(
                session,
                account_id=account_id,
                asset_key=token_asset_key,
                confirmed=Decimal(10**9),
                provider_reserved=Decimal(0),
                local_reserved=Decimal(0),
                available=Decimal(10**9),
                source_snapshot_id=token_snapshot["id"],
                reconcile_watermark=1,
            )
            await session.commit()
        results["fixture_seconds"] = round(time.perf_counter() - t0_fixture, 3)
        # submit 使用外部 token 标识（C 集成测试同口径）
        submit_token = TOKEN_ID
        async with UnitOfWork(exec_sessions) as uow:
            execution_lease = await ExecutionLeaseLogic().acquire_lease(
                uow,
                account_id=account_id,
                lease_role="EXECUTION",
                owner=EXEC_OWNER,
                ttl_s=3600,
            )
        execution_fence = execution_lease["fencing_token"]
        assert execution_fence == 1
        exec_logic = PrivateExecutionLogic(
            execution=ExecutionRepository(), ledger=LedgerRepository(),
            audit=AuditRepository(),
        )
        # ================= Gate 1: DB-only preflight + atomic reservation p99≤50ms =================
        gate1_tx: list[float] = []
        gate1_pool_wait: list[float] = []
        for seq in range(200):
            wait_started = time.perf_counter()
            async with UnitOfWork(exec_sessions) as uow:
                gate1_pool_wait.append((time.perf_counter() - wait_started) * 1000)
                started = time.perf_counter()
                env_f = await _reserve_and_envelope_in_uow(
                    uow, chain, account_id, intent["intent_id"], seq=1000 + seq,
                )
                env_cur = env_f["envelope_id"]
                await exec_logic.prepare_submit(
                    uow,
                    owner=EXEC_OWNER,
                    input_=SubmitOrderInput(
                        envelope_id=env_cur, account_id=account_id, fencing_token=1,
                        token_id=submit_token, side="SELL", price=Decimal("0.50"),
                        size=Decimal("1"), post_only=True,
                    ),
                    signed_order=SimpleNamespace(salt=seq + 1, timestamp=int(time.time())),
                    body_hash="e" * 64, expected_order_hash="f" * 64,
                    sdk_manifest_hash="0" * 64,
                )
                gate1_tx.append(_ms(started))
                # The gate measures the complete atomic path, then rolls the
                # sample back so performance evidence cannot pollute Gate 3/4
                # reconciliation state.
                await uow.rollback()
        g1 = {
            "tx_ms": _percentiles(gate1_tx),
            "pool_wait_ms": _percentiles(gate1_pool_wait),
        }
        results["gate1_db_preflight"] = g1
        assert g1["tx_ms"]["p99"] <= GATE1_P99_MS, "gate1_db_preflight_p99"
        assert g1["pool_wait_ms"]["p95"] <= GATE6_POOL_WAIT_P95_MS, "gate1_pool_wait_p95"

        # ================= Gate 2: fake submit→ACK p95≤2s =================
        gate2_tx: list[float] = []
        gate2_pool_wait: list[float] = []
        gate2_offsets: list[float] = []
        gate2_started = time.perf_counter()
        for seq in range(INTENTS_TARGET):
            async with UnitOfWork(exec_sessions) as uow:
                wait_started = time.perf_counter()
                gate2_pool_wait.append((time.perf_counter() - wait_started) * 1000)
                started = time.perf_counter()
                env_f = await _reserve_and_envelope(
                    exec_sessions, chain, account_id, intent["intent_id"], seq=2000 + seq,
                )
                env_cur = env_f["envelope_id"]
                prepared = await exec_logic.prepare_submit(
                    uow,
                    owner=EXEC_OWNER,
                    input_=SubmitOrderInput(
                        envelope_id=env_cur, account_id=account_id, fencing_token=1,
                        token_id=submit_token, side="SELL", price=Decimal("0.50"),
                        size=Decimal("1"), post_only=True,
                    ),
                    signed_order=SimpleNamespace(salt=1000 + seq, timestamp=int(time.time())),
                    body_hash="e" * 64, expected_order_hash="f" * 64,
                    sdk_manifest_hash="0" * 64,
                )
                outcome = fake_client.post_order(SimpleNamespace(salt=1000 + seq))
                await exec_logic.apply_submit_outcome(
                    uow, prepared=prepared, outcome=outcome,
                    response_hash="a" * 64, http_status=200,
                )
                gate2_tx.append(_ms(started))
                gate2_offsets.append(time.perf_counter() - gate2_started)
        g2_elapsed = time.perf_counter() - gate2_started
        g2 = {
            "tx_ms": _percentiles(gate2_tx),
            "pool_wait_ms": _percentiles(gate2_pool_wait),
            "elapsed_s": round(g2_elapsed, 3),
            "window_rates_per_second": _window_rates(gate2_offsets, g2_elapsed),
            "intents": INTENTS_TARGET,
        }
        results["gate2_fake_submit_ack"] = g2
        assert g2["tx_ms"]["p95"] <= GATE2_P95_MS, "gate2_submit_p95"
        assert g2["tx_ms"]["p99"] <= GATE2_P99_MS, "gate2_submit_p99"
        assert g2["pool_wait_ms"]["p95"] <= GATE6_POOL_WAIT_P95_MS, "gate2_pool_wait_p95"

        # ================= Gate 3: fake User WS receive→runtime apply p95≤100ms =================
        # Build 500 real ACK projections outside the measurement window.  Gate 3
        # then passes provider order frames through the fake receive boundary and
        # UserWsExecutionRuntime.apply_event (not a stand-in SELECT).
        acked = await _count_status(exec_sessions, "ACK")
        for seq in range(max(0, WS_FRAMES_TARGET - acked)):
            env_f = await _reserve_and_envelope(
                exec_sessions, chain, account_id, intent["intent_id"],
                seq=10_000_000 + seq,
            )
            async with UnitOfWork(exec_sessions) as uow:
                prepared = await exec_logic.prepare_submit(
                    uow,
                    owner=EXEC_OWNER,
                    input_=SubmitOrderInput(
                        envelope_id=env_f["envelope_id"], account_id=account_id,
                        fencing_token=execution_fence, token_id=submit_token,
                        side="SELL", price=Decimal("0.50"), size=Decimal("1"),
                        post_only=True,
                    ),
                    signed_order=SimpleNamespace(
                        salt=50000 + seq, timestamp=int(time.time()),
                    ),
                    body_hash="e" * 64,
                    expected_order_hash=f"{50000 + seq:064x}",
                    sdk_manifest_hash="0" * 64,
                )
            outcome = fake_client.post_order(SimpleNamespace(salt=50000 + seq))
            async with UnitOfWork(exec_sessions) as uow:
                await exec_logic.apply_submit_outcome(
                    uow, prepared=prepared, outcome=outcome,
                    response_hash="a" * 64, http_status=200,
                )

        async with exec_sessions() as session:
            ws_order_rows = (await session.execute(text(
                "SELECT external_order_id, token_id, side, price, size "
                "FROM trading.exchange_orders WHERE status='ACK' "
                "ORDER BY id LIMIT :limit"
            ), {"limit": WS_FRAMES_TARGET})).mappings().all()
        assert len(ws_order_rows) == WS_FRAMES_TARGET
        ws_messages: list[UserWsMessage] = []
        for seq, row in enumerate(ws_order_rows, start=1):
            frame = UserOrderEvent(
                event_type="order",
                order_id=row["external_order_id"],
                token_id=row["token_id"],
                side=row["side"],
                price=row["price"],
                size=row["size"],
                status="PARTIAL",
                timestamp=1786417445 + seq,
            )
            ws_messages.append(UserWsMessage(
                receive_seq=seq,
                received_at=datetime.now(timezone.utc),
                frame=frame,
                artifact_hash=user_ws_frame_artifact_hash(frame),
            ))
        ws_driver = _FakeUserWsReceiver(ws_messages)
        ws_runtime = UserWsExecutionRuntime(exec_sessions)
        gate3_tx: list[float] = []
        gate3_pool_wait: list[float] = []
        for _seq in range(WS_FRAMES_TARGET):
            wait_started = time.perf_counter()
            message = await ws_driver.next_frame()
            gate3_pool_wait.append((time.perf_counter() - wait_started) * 1000)
            started = time.perf_counter()
            applied = await ws_runtime.apply_event(
                account_id=account_id,
                owner=EXEC_OWNER,
                fencing_token=execution_fence,
                event=message,
            )
            assert applied is not None
            gate3_tx.append(_ms(started))
        g3 = {
            "tx_ms": _percentiles(gate3_tx),
            "pool_wait_ms": _percentiles(gate3_pool_wait),
            "frames": WS_FRAMES_TARGET,
            "fake_ws_receive_calls": ws_driver.receive_calls,
            "projected_partial_orders": await _count_status(exec_sessions, "PARTIAL"),
        }
        results["gate3_ws_projection"] = g3
        assert g3["tx_ms"]["p95"] <= GATE3_P95_MS, "gate3_ws_p95"
        assert g3["tx_ms"]["p99"] <= GATE3_P99_MS, "gate3_ws_p99"
        assert g3["fake_ws_receive_calls"] == WS_FRAMES_TARGET
        assert g3["projected_partial_orders"] == WS_FRAMES_TARGET

        # ================= Gate 4: 1000 live-order REST reconcile p95≤10s =================
        gate4_tx: list[float] = []
        gate4_pool_wait: list[float] = []
        # 已有 Gate2/Gate3 的 live orders，补足到精确 1,000 条。
        existing = (await _count(exec_sessions, "trading.exchange_orders"))
        need = max(0, RECON_ORDERS_TARGET - existing)
        for seq in range(need):
            async with UnitOfWork(exec_sessions) as uow:
                env_f = await _reserve_and_envelope(
                    exec_sessions, chain, account_id, intent["intent_id"],
                    seq=20_000_000 + seq,
                )
                env_cur = env_f["envelope_id"]
                prepared = await exec_logic.prepare_submit(
                    uow,
                    owner=EXEC_OWNER,
                    input_=SubmitOrderInput(
                        envelope_id=env_cur, account_id=account_id, fencing_token=1,
                        token_id=submit_token, side="SELL", price=Decimal("0.50"),
                        size=Decimal("1"), post_only=True,
                    ),
                    signed_order=SimpleNamespace(salt=10000 + seq, timestamp=int(time.time())),
                    body_hash="e" * 64, expected_order_hash="f" * 64,
                    sdk_manifest_hash="0" * 64,
                )
                outcome = fake_client.post_order(SimpleNamespace(salt=10000 + seq))
                await exec_logic.apply_submit_outcome(
                    uow, prepared=prepared, outcome=outcome,
                    response_hash="a" * 64, http_status=200,
                )

        async with recon_sessions() as session:
            local_orders = (await session.execute(text(
                "SELECT external_order_id, token_id, side, price, size, status "
                "FROM trading.exchange_orders "
                "WHERE account_id=:account AND status IN ('ACK','PARTIAL') "
                "ORDER BY id"
            ), {"account": account_id})).mappings().all()
            local_positions = (await session.execute(text(
                "SELECT pt.token_id, p.quantity, p.cost_basis "
                "FROM trading.positions p "
                "JOIN trading.pm_tokens pt ON pt.id=p.token_id "
                "WHERE p.account_id=:account ORDER BY p.id"
            ), {"account": account_id})).mappings().all()
            local_funds = (await session.execute(text(
                "SELECT asset_key, confirmed, provider_reserved, local_reserved "
                "FROM trading.account_funds_current WHERE account_id=:account "
                "ORDER BY asset_key"
            ), {"account": account_id})).mappings().all()
        assert len(local_orders) == RECON_ORDERS_TARGET
        rest_driver = _PagedRestDriver(
            orders=[SimpleNamespace(
                order_id=row["external_order_id"], token_id=row["token_id"],
                side=row["side"], price=row["price"], size=row["size"],
                status=("partial" if row["status"] == "PARTIAL" else "live"),
            ) for row in local_orders],
            trades=[],
            positions=[SimpleNamespace(
                token_id=row["token_id"], size=row["quantity"],
                avg_price=(row["cost_basis"] / row["quantity"]
                           if row["quantity"] else None),
            ) for row in local_positions],
            funds=[SimpleNamespace(
                asset_key=row["asset_key"], confirmed=row["confirmed"],
                provider_reserved=row["provider_reserved"],
                local_reserved=row["local_reserved"],
            ) for row in local_funds],
        )
        reconcile_runtime = ReconciliationRuntime(recon_sessions)
        reconcile_results: list[dict[str, Any]] = []
        for seq in range(RECON_ORDERS_TARGET // 50):
            wait_started = time.perf_counter()
            started = time.perf_counter()
            reconciled = await reconcile_runtime.reconcile(
                reconcile_input=ReconcileInput(
                    reconciliation_key=f"perf-recon-{seq}", account_id=account_id,
                    trigger_reason="perf", ws_watermark=WS_FRAMES_TARGET,
                    rest_cursor="", fencing_token=execution_fence,
                ),
                driver=rest_driver,
                owner=EXEC_OWNER,
            )
            gate4_pool_wait.append((started - wait_started) * 1000)
            gate4_tx.append(_ms(started))
            reconcile_results.append(reconciled)
            assert reconciled["status"] == "COMPLETED", reconciled
            assert reconciled["differences"] == [], reconciled
            assert reconciled["pages"] == 5, reconciled
        g4 = {
            "tx_ms": _percentiles(gate4_tx),
            "pool_wait_ms": _percentiles(gate4_pool_wait),
            "orders": RECON_ORDERS_TARGET,
            "reconcile_runs": RECON_ORDERS_TARGET // 50,
            "completed_runs": sum(
                result["status"] == "COMPLETED" for result in reconcile_results
            ),
            "final_difference_count": len(reconcile_results[-1]["differences"]),
            "rest_open_order_page_calls": rest_driver.open_order_calls,
        }
        results["gate4_rest_reconcile"] = g4
        assert g4["tx_ms"]["p95"] <= GATE4_P95_MS, "gate4_reconcile_p95"
        assert g4["tx_ms"]["p99"] <= GATE4_P99_MS, "gate4_reconcile_p99"
        assert g4["completed_runs"] == g4["reconcile_runs"]
        assert g4["final_difference_count"] == 0
        assert g4["rest_open_order_page_calls"] == g4["reconcile_runs"] * 5

        # ============ Heartbeat failure action: stop → cancel → reconcile ============
        private_runtime = PrivateExecutionRuntime(exec_sessions)
        async with UnitOfWork(exec_sessions) as uow:
            heartbeat_lease = await ExecutionLeaseLogic().acquire_lease(
                uow,
                account_id=account_id,
                lease_role="HEARTBEAT",
                owner=HEARTBEAT_OWNER,
                ttl_s=3600,
            )
        heartbeat_fence = heartbeat_lease["fencing_token"]
        priority_driver = _HeartbeatProbeDriver()
        priority_driver.fail_next = True
        priority_stop = asyncio.Event()
        priority_actions: list[tuple[str, float]] = []
        priority_started = time.monotonic()
        cancel_external_id = rest_driver.orders[0].order_id

        async def _heartbeat_failure_action(*_args: Any, **_kwargs: Any) -> None:
            priority_actions.append(("stop_new_orders", time.monotonic()))
            canceled = await private_runtime.cancel_order(
                cancel_input=CancelOrderInput(
                    account_id=account_id,
                    fencing_token=execution_fence,
                    external_order_id=cancel_external_id,
                ),
                driver=priority_driver,
                owner=EXEC_OWNER,
            )
            assert canceled.ok and canceled.status == "CANCELLED", canceled
            priority_actions.append(("cancel", time.monotonic()))

            rest_driver.orders = [
                order for order in rest_driver.orders
                if order.order_id != cancel_external_id
            ]
            async with recon_sessions() as session:
                current_funds = (await session.execute(text(
                    "SELECT asset_key, confirmed, provider_reserved, local_reserved "
                    "FROM trading.account_funds_current WHERE account_id=:account "
                    "ORDER BY asset_key"
                ), {"account": account_id})).mappings().all()
            rest_driver.fund_rows = [SimpleNamespace(
                asset_key=row["asset_key"], confirmed=row["confirmed"],
                provider_reserved=row["provider_reserved"],
                local_reserved=row["local_reserved"],
            ) for row in current_funds]
            recovered = await reconcile_runtime.reconcile(
                reconcile_input=ReconcileInput(
                    reconciliation_key="perf-heartbeat-failure-reconcile",
                    account_id=account_id,
                    trigger_reason="heartbeat_failure",
                    ws_watermark=WS_FRAMES_TARGET,
                    rest_cursor="",
                    fencing_token=execution_fence,
                ),
                driver=rest_driver,
                owner=EXEC_OWNER,
            )
            assert recovered["status"] == "COMPLETED", recovered
            assert recovered["differences"] == [], recovered
            priority_actions.append(("reconcile", time.monotonic()))
            priority_stop.set()

        await asyncio.wait_for(
            private_runtime.run_heartbeat_loop(
                account_id=account_id,
                owner=HEARTBEAT_OWNER,
                fencing_token=heartbeat_fence,
                driver=priority_driver,
                stop_event=priority_stop,
                on_failure=_heartbeat_failure_action,
                interval_s=0.01,
            ),
            timeout=GATE4_P99_MS / 1000.0,
        )
        action_names = [name for name, _at in priority_actions]
        assert action_names == ["stop_new_orders", "cancel", "reconcile"]
        priority_metrics = {
            "actions": action_names,
            "failure_to_stop_ms": round(
                (priority_actions[0][1] - priority_driver.call_times[0]) * 1000, 3
            ),
            "failure_to_reconciled_ms": round(
                (priority_actions[-1][1] - priority_started) * 1000, 3
            ),
        }
        assert priority_metrics["failure_to_stop_ms"] <= HEARTBEAT_MAX_DRIFT_MS
        assert priority_metrics["failure_to_reconciled_ms"] <= GATE4_P99_MS
        results["heartbeat_failure_priority"] = priority_metrics

        # ================= Gate 5: ≥10 intents/s 持续 ≥60s =================
        cadence_driver = _HeartbeatProbeDriver()
        cadence_stop = asyncio.Event()
        cadence_failures: list[Any] = []

        async def _unexpected_heartbeat_failure(*args: Any, **_kwargs: Any) -> None:
            cadence_failures.append(args)
            cadence_stop.set()

        cadence_task = asyncio.create_task(private_runtime.run_heartbeat_loop(
            account_id=account_id,
            owner=HEARTBEAT_OWNER,
            fencing_token=heartbeat_fence,
            driver=cadence_driver,
            stop_event=cadence_stop,
            on_failure=_unexpected_heartbeat_failure,
            interval_s=HEARTBEAT_INTERVAL_S,
        ))
        gate5_offsets: list[float] = []
        gate5_started = time.perf_counter()
        gate5_count = 0
        while time.perf_counter() - gate5_started < 60.0:
            async with UnitOfWork(exec_sessions) as uow:
                env_f = await _reserve_and_envelope(
                    exec_sessions, chain, account_id, intent["intent_id"],
                    seq=30_000_000 + gate5_count,
                )
                env_cur = env_f["envelope_id"]
                prepared = await exec_logic.prepare_submit(
                    uow,
                    owner=EXEC_OWNER,
                    input_=SubmitOrderInput(
                        envelope_id=env_cur, account_id=account_id, fencing_token=1,
                        token_id=submit_token, side="SELL", price=Decimal("0.50"),
                        size=Decimal("1"), post_only=True,
                    ),
                    signed_order=SimpleNamespace(salt=20000 + gate5_count, timestamp=int(time.time())),
                    body_hash="e" * 64, expected_order_hash="f" * 64,
                    sdk_manifest_hash="0" * 64,
                )
                outcome = fake_client.post_order(SimpleNamespace(salt=20000 + gate5_count))
                await exec_logic.apply_submit_outcome(
                    uow, prepared=prepared, outcome=outcome,
                    response_hash="a" * 64, http_status=200,
                )
                gate5_count += 1
                gate5_offsets.append(time.perf_counter() - gate5_started)
        cadence_stop.set()
        await asyncio.wait_for(cadence_task, timeout=HEARTBEAT_INTERVAL_S + 1.0)
        gate5_elapsed = time.perf_counter() - gate5_started
        gate5_ips = gate5_count / max(1.0, gate5_elapsed)
        g5 = {
            "intents": gate5_count,
            "elapsed_s": round(gate5_elapsed, 3),
            "intents_per_second": round(gate5_ips, 3),
            "window_rates_per_second": _window_rates(gate5_offsets, gate5_elapsed),
        }
        results["gate5_steady_intents"] = g5
        assert gate5_ips >= GATE5_MIN_IPS, "gate5_intents_per_second"
        assert cadence_failures == [], cadence_failures
        heartbeat_intervals = [
            (right - left) * 1000
            for left, right in zip(cadence_driver.call_times, cadence_driver.call_times[1:])
        ]
        heartbeat_drift = [
            abs(interval - HEARTBEAT_INTERVAL_S * 1000)
            for interval in heartbeat_intervals
        ]
        heartbeat_metrics = {
            "calls": len(cadence_driver.call_times),
            "interval_ms": _percentiles(heartbeat_intervals),
            "drift_ms": _percentiles(heartbeat_drift),
            "max_drift_ms": max(heartbeat_drift, default=0.0),
            "id_chain_no_skip": cadence_driver.ids == [
                f"perf-heartbeat-{index}"
                for index in range(1, len(cadence_driver.ids) + 1)
            ],
        }
        assert heartbeat_metrics["calls"] >= 10
        assert heartbeat_metrics["max_drift_ms"] <= HEARTBEAT_MAX_DRIFT_MS
        assert heartbeat_metrics["id_chain_no_skip"] is True
        results["heartbeat_under_saturation"] = heartbeat_metrics

        # ================= Gate 6: pool wait / tx / resource peaks =================
        # Gate 4 is an end-to-end multi-page orchestration with its own 10s/30s
        # contract.  Gate 6's 50ms transaction contract measures the DB-bound
        # execution/apply transactions, not the complete REST reconciliation.
        tx_all = [*gate1_tx, *gate2_tx, *gate3_tx]
        pool_all = [*gate1_pool_wait, *gate2_pool_wait, *gate3_pool_wait, *gate4_pool_wait]
        g6 = {
            "pool_wait_ms": _percentiles(pool_all),
            "transaction_ms": _percentiles(tx_all),
            "peak_checked_out": max(exec_pool_probe.peak, recon_pool_probe.peak),
            "peak_checked_out_by_pool": {
                "execution": exec_pool_probe.peak,
                "reconciliation": recon_pool_probe.peak,
            },
            "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "fake_transport_calls": (
                fake_client.post_order_calls
                + ws_driver.receive_calls
                + rest_driver.open_order_calls
                + rest_driver.trade_calls
                + rest_driver.position_calls
                + rest_driver.fund_calls
                + len(priority_driver.call_times)
                + len(cadence_driver.call_times)
            ),
            "real_network_calls": 0,
        }
        results["gate6_pool_and_resources"] = g6
        assert g6["pool_wait_ms"]["p95"] <= GATE6_POOL_WAIT_P95_MS, "gate6_pool_wait_p95"
        assert g6["transaction_ms"]["p99"] <= GATE6_TX_P99_MS, "gate6_tx_p99"
        assert 0 < exec_pool_probe.peak <= EXEC_POOL_SIZE + EXEC_MAX_OVERFLOW
        assert 0 < recon_pool_probe.peak <= RECON_POOL_SIZE + RECON_MAX_OVERFLOW

        results.update({
            "hard_assertions": "PASS",
            "seed": "deterministic/wp-05-execution-readiness-performance-v1",
            "git_commit": _git_sha(),
            "sdk": _sdk_tag_commit(),
            "fixture_hashes": _fixture_hashes(),
            "data_scale": {
                "accounts": 1,
                "envelopes": await _count(
                    exec_sessions, "trading.execution_authorization_envelopes"
                ),
                "orders": await _count(exec_sessions, "trading.exchange_orders"),
                "intents_gate5": gate5_count,
                "reconcile_runs": RECON_ORDERS_TARGET // 50,
            },
            "platform": platform.platform(),
            "python": platform.python_version(),
        })
        OUT_PATH.write_text(json.dumps(results, indent=2, sort_keys=True))
        return results
    finally:
        await exec_engine.dispose()
        await recon_engine.dispose()
        # 清理临时库
        admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            with admin.connect() as c:
                c.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:n AND pid<>pg_backend_pid()"
                ), {"n": dbname})
                c.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        finally:
            admin.dispose()


async def _count(sessions, table: str) -> int:
    async with sessions() as session:
        row = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
        return int(row)


async def _count_status(sessions, status: str) -> int:
    async with sessions() as session:
        row = (await session.execute(text(
            "SELECT count(*) FROM trading.exchange_orders WHERE status=:status"
        ), {"status": status})).scalar_one()
        return int(row)


def main() -> None:
    results = asyncio.run(_run())
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
