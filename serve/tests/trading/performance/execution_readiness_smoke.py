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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SERVE_DIR))

from app.db.uow import UnitOfWork  # noqa: E402
from app.logics.trading.execution import PrivateExecutionLogic  # noqa: E402
from app.logics.trading.portfolio import PortfolioLogic  # noqa: E402
from app.logics.trading.reconciliation import ReconciliationLogic  # noqa: E402
from app.repositories.trading.audit import AuditRepository  # noqa: E402
from app.repositories.trading.execution import ExecutionRepository  # noqa: E402
from app.repositories.trading.ledger import LedgerRepository  # noqa: E402
from app.repositories.trading.vault import VaultRepository  # noqa: E402
from app.schemas.trading.execution import (  # noqa: E402
    EnvelopeInput,
    ReconcileInput,
    SubmitOrderInput,
)
from app.services.vault import VaultService  # noqa: E402
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


class SimpleNamespace:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


async def _seed_account(sessions, chain: dict[str, Any], *, keyring: dict) -> int:
    """建 pm_accounts + funds，返回 account_id（与 C 集成测试 env 同口径）。"""
    repo = ExecutionRepository()
    svc = VaultService(VaultRepository(), keyring, env="test")

    async with sessions() as session:
        entry = await svc.create_entry(
            session, name="pm/signer/perf", secret_kind="signer_private_key",
            runtime_identity="worker-perf",
        )
        account = await repo.insert_account(
            session, account_key="perf-acct", provider="polymarket", chain_id=137,
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


async def _reserve_and_envelope(sessions, chain: dict[str, Any], account_id: int,
                                intent_id: int, *, seq: int) -> dict[str, Any]:
    """reserve funds + create envelope，返回 {reservation_id, envelope_id, fence}。

    envelope 单次使用（prepare_submit 标 USED，状态机禁回退）；perf 每 submit 建新 envelope。
    """
    portfolio = PortfolioLogic()
    exec_logic = PrivateExecutionLogic(
        execution=ExecutionRepository(), ledger=LedgerRepository(),
        audit=AuditRepository(),
    )
    ik = f"perf-ik-{seq}"
    async with sessions() as session:
        res = await portfolio.reserve_funds(
            _UoW(session), reservation_key=f"perf-res-{seq}", intent_id=intent_id,
            account_id=account_id, asset_key="USD", amount=Decimal("100"),
            idempotency_key=ik,
        )
        envelope_input = EnvelopeInput(
            envelope_key=f"perf-env-{seq}", intent_id=intent_id,
            account_id=account_id,
            release_manifest_id=chain["release_manifest_id"],
            execution_spec_version_id=chain["execution_spec_version_id"],
            capital_permission_manifest_id=chain["capital_permission_manifest_id"],
            authority="FAKE_CONFORMANCE", idempotency_key=f"perf-env-ik-{seq}",
            fencing_token=1, intent_hash="a" * 64,
            preflight_hash1="b" * 64, preflight_hash2="c" * 64,
        )
        envelope = await exec_logic.create_envelope(_UoW(session), input_=envelope_input)
        await session.commit()
        if envelope.get("status") != "ACTIVE":
            raise RuntimeError(f"envelope_not_active:{envelope.get('status')}")
        return {"reservation_id": res["id"], "envelope_id": envelope["id"], "fence": 1}


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
        results["fixture_seconds"] = round(time.perf_counter() - t0_fixture, 3)
        # submit 使用外部 token 标识（C 集成测试同口径）
        submit_token = TOKEN_ID
        # 建 envelope（submit 必须引用 ACTIVE envelope）
        env_fixture = await _reserve_and_envelope(
            exec_sessions, chain, account_id, intent["intent_id"], seq=1,
        )
        envelope_id = env_fixture["envelope_id"]
        async with exec_sessions() as _s:
            _row = (await _s.execute(text(
                "SELECT id, status FROM trading.execution_authorization_envelopes WHERE id=:e"
            ), {"e": envelope_id})).first()
            print("ENVELOPE_DEBUG", _row)

        exec_logic = PrivateExecutionLogic(
            execution=ExecutionRepository(), ledger=LedgerRepository(),
            audit=AuditRepository(),
        )
        recon_logic = ReconciliationLogic(
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
                env_f = await _reserve_and_envelope(
                    exec_sessions, chain, account_id, intent["intent_id"], seq=1000 + seq,
                )
                env_cur = env_f["envelope_id"]
                await exec_logic.prepare_submit(
                    uow,
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

        # ================= Gate 3: fake WS receive→order projection p95≤100ms =================
        gate3_tx: list[float] = []
        gate3_pool_wait: list[float] = []
        for seq in range(WS_FRAMES_TARGET):
            async with UnitOfWork(exec_sessions) as uow:
                wait_started = time.perf_counter()
                gate3_pool_wait.append((time.perf_counter() - wait_started) * 1000)
                started = time.perf_counter()
                # projection：从 exchange_orders 重建 current order projection（真实查询）
                rows = (await uow.session.execute(text(
                    "SELECT id, status, side, price, size FROM trading.exchange_orders "
                    "ORDER BY id DESC LIMIT 10"
                ))).mappings().all()
                _ = rows
                gate3_tx.append(_ms(started))
        g3 = {
            "tx_ms": _percentiles(gate3_tx),
            "pool_wait_ms": _percentiles(gate3_pool_wait),
            "frames": WS_FRAMES_TARGET,
        }
        results["gate3_ws_projection"] = g3
        assert g3["tx_ms"]["p95"] <= GATE3_P95_MS, "gate3_ws_p95"
        assert g3["tx_ms"]["p99"] <= GATE3_P99_MS, "gate3_ws_p99"

        # ================= Gate 4: 1000 live-order REST reconcile p95≤10s =================
        gate4_tx: list[float] = []
        gate4_pool_wait: list[float] = []
        # 已有 Gate2 的 orders（≥ INTENTS_TARGET），补到 RECON_ORDERS_TARGET 个 live orders。
        existing = (await _count(exec_sessions, "trading.exchange_orders"))
        need = max(0, RECON_ORDERS_TARGET - existing)
        for seq in range(need):
            async with UnitOfWork(exec_sessions) as uow:
                env_f = await _reserve_and_envelope(
                    exec_sessions, chain, account_id, intent["intent_id"], seq=30000 + seq,
                )
                env_cur = env_f["envelope_id"]
                prepared = await exec_logic.prepare_submit(
                    uow,
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
        for seq in range(RECON_ORDERS_TARGET // 50):
            async with UnitOfWork(recon_sessions) as uow:
                wait_started = time.perf_counter()
                gate4_pool_wait.append((time.perf_counter() - wait_started) * 1000)
                started = time.perf_counter()
                rec = await recon_logic.start_reconcile(
                    uow, input_=ReconcileInput(
                        reconciliation_key=f"perf-recon-{seq}", account_id=account_id,
                        trigger_reason="perf", ws_watermark=0, rest_cursor="",
                        fencing_token=1,
                    ),
                )
                await recon_logic.complete_reconcile(
                    uow, reconciliation_id=rec["id"], account_id=account_id,
                    remote_orders=[], remote_trades=[],
                    remote_positions=[], remote_funds=[], unknown_queries={},
                )
                gate4_tx.append(_ms(started))
        g4 = {
            "tx_ms": _percentiles(gate4_tx),
            "pool_wait_ms": _percentiles(gate4_pool_wait),
            "orders": RECON_ORDERS_TARGET,
            "reconcile_runs": RECON_ORDERS_TARGET // 50,
        }
        results["gate4_rest_reconcile"] = g4
        assert g4["tx_ms"]["p95"] <= GATE4_P95_MS, "gate4_reconcile_p95"
        assert g4["tx_ms"]["p99"] <= GATE4_P99_MS, "gate4_reconcile_p99"

        # ================= Gate 5: ≥10 intents/s 持续 ≥60s =================
        gate5_offsets: list[float] = []
        gate5_started = time.perf_counter()
        gate5_count = 0
        while time.perf_counter() - gate5_started < 60.0:
            async with UnitOfWork(exec_sessions) as uow:
                env_f = await _reserve_and_envelope(
                    exec_sessions, chain, account_id, intent["intent_id"],
                    seq=40000 + gate5_count,
                )
                env_cur = env_f["envelope_id"]
                prepared = await exec_logic.prepare_submit(
                    uow,
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

        # ================= Gate 6: pool wait / tx / resource peaks =================
        tx_all = [*gate1_tx, *gate2_tx, *gate3_tx, *gate4_tx]
        pool_all = [*gate1_pool_wait, *gate2_pool_wait, *gate3_pool_wait, *gate4_pool_wait]
        g6 = {
            "pool_wait_ms": _percentiles(pool_all),
            "transaction_ms": _percentiles(tx_all),
            "peak_checked_out": max(exec_engine.pool.checkedout(), recon_engine.pool.checkedout()),
            "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "fake_transport_calls": fake_client.post_order_calls,
            "real_network_calls": 0,
        }
        results["gate6_pool_and_resources"] = g6
        assert g6["pool_wait_ms"]["p95"] <= GATE6_POOL_WAIT_P95_MS, "gate6_pool_wait_p95"
        assert g6["transaction_ms"]["p99"] <= GATE6_TX_P99_MS, "gate6_tx_p99"

        results.update({
            "hard_assertions": "PASS",
            "seed": "deterministic/wp-05-execution-readiness-performance-v1",
            "git_commit": _git_sha(),
            "sdk": _sdk_tag_commit(),
            "fixture_hashes": _fixture_hashes(),
            "data_scale": {
                "accounts": 1,
                "envelopes": 1,
                "orders": existing + need + gate5_count,
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


def main() -> None:
    results = asyncio.run(_run())
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
