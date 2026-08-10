"""WP-01B book resync / freshness -- real PostgreSQL + scripted official WS wire."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork
from app.logics.trading.market_data import (
    FreshnessPolicy,
    apply_delta,
    freshness,
    snapshot_book,
)
from app.outbox.repository import OutboxRepository
from app.repositories.trading.market_stream import MarketStreamRepository
from app.schemas.polymarket.common import PolymarketError, REASON_WS_DISCONNECT
from app.schemas.polymarket.market_ws import MarketWsUnknown, parse_market_ws_frame
from app.services.artifact_store.contracts import ArtifactRef, build_locator
from app.services.polymarket.market_ws_driver import MarketWsMessage
from runtimes.trading.market_ingest import BookWsIngestor
from tests.trading.fixtures.poly_fixtures import create_test_release_manifest


class FakeArtifactStore:
    """Small immutable CAS implementing the ArtifactStore methods used by ingest."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def put_bytes(
        self, data: bytes, mime: str, compression: str = "none"
    ) -> ArtifactRef:
        raw = bytes(data)
        sha = hashlib.sha256(raw).hexdigest()
        self.data.setdefault(sha, raw)
        return ArtifactRef(
            sha256=sha,
            original_size=len(raw),
            stored_size=len(raw),
            mime=mime,
            compression="none",
            storage_driver="local",
            locator=build_locator(sha, "none"),
        )

    def get_bytes(self, ref: ArtifactRef) -> bytes:
        return self.data[ref.sha256]


class ControlledWsDriver:
    """Delivers parsed official wire frames, then waits for an explicit disconnect.

    ``pause_before_index`` lets the test inspect committed state after frame N and
    before frame N+1.  ``waiting_disconnect`` is set only after every delivered
    frame has been committed and ingest asks for the next frame.
    """

    def __init__(self, messages, assets_ids, *, pause_before_index: int | None = None):
        self.messages = list(messages)
        self.assets_ids = list(assets_ids)
        self.pause_before_index = pause_before_index
        self.index = 0
        self.closed = False
        self.paused = asyncio.Event()
        self.resume = asyncio.Event()
        self.waiting_disconnect = asyncio.Event()
        self.disconnect = asyncio.Event()

    async def connect(self) -> None:
        return None

    async def next_frame(self) -> MarketWsMessage:
        if self.pause_before_index == self.index and not self.resume.is_set():
            self.paused.set()
            await self.resume.wait()
        if self.index < len(self.messages):
            message = self.messages[self.index]
            self.index += 1
            return message
        self.waiting_disconnect.set()
        await self.disconnect.wait()
        raise PolymarketError(REASON_WS_DISCONNECT)

    async def aclose(self) -> None:
        self.closed = True


def _wire_message(payload: dict, seq: int, received_at: datetime) -> MarketWsMessage:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    frame = parse_market_ws_frame(raw)
    assert not isinstance(frame, MarketWsUnknown), frame.parse_error
    return MarketWsMessage(
        receive_seq=seq,
        received_at=received_at,
        raw_text=raw,
        frame=frame,
    )


def _book_payload(token: str, bids, asks, *, timestamp: int) -> dict:
    return {
        "event_type": "book",
        "market": f"condition-{token}",
        "asset_id": token,
        "timestamp": timestamp,
        "hash": hashlib.sha256(f"book-{token}-{timestamp}".encode()).hexdigest(),
        "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
        "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
    }


def _price_change_payload(token: str, changes, *, timestamp: int) -> dict:
    return {
        "event_type": "price_change",
        "market": f"condition-{token}",
        "timestamp": timestamp,
        "price_changes": [
            {
                "asset_id": token,
                "price": str(price),
                "size": str(size),
                "side": "BUY" if side == "bid" else "SELL",
                "hash": hashlib.sha256(
                    f"change-{token}-{price}-{size}-{side}-{timestamp}".encode()
                ).hexdigest(),
                "best_bid": "0.51",
                "best_ask": "0.52",
            }
            for side, price, size in changes
        ],
    }


def _tick_payload(token: str, *, timestamp: int) -> dict:
    return {
        "event_type": "tick_size_change",
        "market": f"condition-{token}",
        "asset_id": token,
        "old_tick_size": "0.01",
        "new_tick_size": "0.005",
        "timestamp": timestamp,
    }


@pytest.fixture
def stream_env(migrated_pg_db):
    admin = make_url(migrated_pg_db.url)
    async_url = admin.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    engine = create_async_engine(async_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    env = {
        "engine": engine,
        "sessions": sessions,
        "artifacts": FakeArtifactStore(),
        "stream_repo": MarketStreamRepository(),
        "outbox_repo": OutboxRepository(),
        "url": migrated_pg_db.url,
    }
    yield env
    engine.sync_engine.dispose()


async def _release_id(env, key: str) -> int:
    uow = UnitOfWork(env["sessions"])
    async with uow:
        return await create_test_release_manifest(uow.session, key=key)


def _ingestor(env, driver: ControlledWsDriver, release_id: int, shard: str):
    return BookWsIngestor(
        ws_driver_factory=lambda: driver,
        artifacts=env["artifacts"],
        uow_factory=lambda: UnitOfWork(env["sessions"]),
        stream_repo=env["stream_repo"],
        outbox_repo=env["outbox_repo"],
        freshness_policy=FreshnessPolicy(quote_ttl_s=30),
        shard_key=shard,
        config_release_id=release_id,
    )


def _query(engine, sql, params=None):
    with engine.connect() as connection:
        return connection.execute(text(sql), params or {}).fetchall()


@pytest.mark.asyncio
async def test_ws_barrier_reconnect_fencing_and_durable_evidence(stream_env):
    """The full two-token barrier is atomic and reconnect cannot be poisoned by epoch 1."""

    release_id = await _release_id(stream_env, "book-resync")
    base = datetime.now(timezone.utc) - timedelta(seconds=2)
    first_messages = [
        _wire_message(
            _book_payload(
                "t1",
                [(Decimal("0.49"), 100), (Decimal("0.50"), 200)],
                [(Decimal("0.53"), 50), (Decimal("0.52"), 100)],
                timestamp=1782753357001,
            ),
            1,
            base,
        ),
        _wire_message(
            _book_payload(
                "t2", [(Decimal("0.40"), 80)], [(Decimal("0.42"), 70)],
                timestamp=1782753357002,
            ),
            2,
            base + timedelta(milliseconds=10),
        ),
        _wire_message(
            _price_change_payload(
                "t1",
                [
                    ("bid", Decimal("0.50"), Decimal("0")),
                    ("bid", Decimal("0.51"), Decimal("300")),
                ],
                timestamp=1782753357003,
            ),
            3,
            base + timedelta(milliseconds=20),
        ),
    ]
    first = ControlledWsDriver(
        first_messages, ["t1", "t2"], pause_before_index=1
    )
    first_task = asyncio.create_task(
        _ingestor(stream_env, first, release_id, "shard-a").run_epoch()
    )

    # The runtime asks for frame 2 only after frame 1 committed.  One of two
    # snapshots is not enough to publish this subscription.
    await asyncio.wait_for(first.paused.wait(), timeout=5)
    sync_engine = create_engine(stream_env["url"], poolclass=NullPool)
    try:
        syncing = _query(
            sync_engine,
            "SELECT id, status FROM trading.pm_connection_epochs "
            "WHERE shard_key='shard-a' ORDER BY id DESC LIMIT 1",
        )[0]
        first_epoch_id = syncing[0]
        assert syncing[1] == "SYNCING"
        assert _query(
            sync_engine,
            "SELECT token_id, validity FROM trading.pm_book_current ORDER BY token_id",
        ) == [("t1", "SYNCING")]

        first.resume.set()
        await asyncio.wait_for(first.waiting_disconnect.wait(), timeout=5)
        assert _query(
            sync_engine,
            "SELECT status FROM trading.pm_connection_epochs WHERE id=:e",
            {"e": first_epoch_id},
        )[0][0] == "LIVE"
        assert _query(
            sync_engine,
            "SELECT token_id, validity FROM trading.pm_book_current ORDER BY token_id",
        ) == [("t1", "VALID"), ("t2", "VALID")]
        assert _query(
            sync_engine,
            "SELECT best_bid, best_ask FROM trading.pm_book_current WHERE token_id='t1'",
        )[0] == (Decimal("0.51"), Decimal("0.52"))
    finally:
        sync_engine.dispose()

    first.disconnect.set()
    assert await asyncio.wait_for(first_task, timeout=5) == first_epoch_id
    assert first.closed is True

    sync_engine = create_engine(stream_env["url"], poolclass=NullPool)
    try:
        assert _query(
            sync_engine,
            "SELECT status FROM trading.pm_connection_epochs WHERE id=:e",
            {"e": first_epoch_id},
        )[0][0] == "STALE"
        assert _query(
            sync_engine,
            "SELECT DISTINCT validity FROM trading.pm_book_current "
            "WHERE connection_epoch_id=:e",
            {"e": first_epoch_id},
        ) == [("STALE",)]
        old_checkpoint = _query(
            sync_engine,
            "SELECT id, received_at, best_bid, best_ask, tick_size, min_order_size, book_hash "
            "FROM trading.pm_book_checkpoints WHERE connection_epoch_id=:e "
            "AND token_id='t1' ORDER BY received_at DESC, id DESC LIMIT 1",
            {"e": first_epoch_id},
        )[0]
    finally:
        sync_engine.dispose()

    # STALE is terminal evidence but no longer occupies the active shard slot.
    # Epoch 2 evidence is received after epoch 1's disconnect marker; otherwise
    # the monotonic current CAS correctly rejects it as older evidence.
    second_base = datetime.now(timezone.utc)
    second_messages = [
        _wire_message(
            _book_payload("t1", [(Decimal("0.60"), 10)], [(Decimal("0.62"), 10)],
                          timestamp=1782753358001),
            1,
            second_base,
        ),
        _wire_message(
            _book_payload("t2", [(Decimal("0.30"), 10)], [(Decimal("0.32"), 10)],
                          timestamp=1782753358002),
            2,
            second_base + timedelta(milliseconds=10),
        ),
    ]
    second = ControlledWsDriver(second_messages, ["t1", "t2"])
    second_task = asyncio.create_task(
        _ingestor(stream_env, second, release_id, "shard-a").run_epoch()
    )
    await asyncio.wait_for(second.waiting_disconnect.wait(), timeout=5)

    sync_engine = create_engine(stream_env["url"], poolclass=NullPool)
    try:
        second_epoch_id = _query(
            sync_engine,
            "SELECT id FROM trading.pm_connection_epochs "
            "WHERE shard_key='shard-a' AND status='LIVE'",
        )[0][0]
        assert second_epoch_id != first_epoch_id
        assert _query(
            sync_engine,
            "SELECT connection_epoch_id, best_bid, validity FROM trading.pm_book_current "
            "WHERE token_id='t1'",
        )[0] == (second_epoch_id, Decimal("0.60"), "VALID")
    finally:
        sync_engine.dispose()

    # Even a newer observed_at cannot let a stale epoch replace epoch 2 current.
    uow = UnitOfWork(stream_env["sessions"])
    async with uow:
        overwritten = await stream_env["stream_repo"].replace_book_current(
            uow.session,
            token_id="t1",
            connection_epoch_id=first_epoch_id,
            checkpoint_id=old_checkpoint[0],
            checkpoint_received_at=old_checkpoint[1],
            best_bid=old_checkpoint[2],
            best_ask=old_checkpoint[3],
            tick_size=old_checkpoint[4],
            min_order_size=old_checkpoint[5],
            depth_hash=old_checkpoint[6],
            validity="VALID",
            observed_at=datetime.now(timezone.utc) + timedelta(seconds=10),
        )
        assert overwritten is False

    sync_engine = create_engine(stream_env["url"], poolclass=NullPool)
    try:
        assert _query(
            sync_engine,
            "SELECT connection_epoch_id, best_bid, validity FROM trading.pm_book_current "
            "WHERE token_id='t1'",
        )[0] == (second_epoch_id, Decimal("0.60"), "VALID")
    finally:
        sync_engine.dispose()

    second.disconnect.set()
    assert await asyncio.wait_for(second_task, timeout=5) == second_epoch_id

    sync_engine = create_engine(stream_env["url"], poolclass=NullPool)
    try:
        assert _query(
            sync_engine,
            "SELECT connection_epoch_id, validity FROM trading.pm_book_current "
            "WHERE token_id='t1'",
        )[0] == (second_epoch_id, "STALE")

        # Five frames produced five exact raw artifacts/batches/index rows and five
        # derived checkpoints/outbox events.  Every SQL evidence link resolves.
        assert _query(sync_engine, "SELECT count(*) FROM trading.pm_source_event_batches")[0][0] == 5
        assert _query(sync_engine, "SELECT count(*) FROM trading.pm_source_event_index")[0][0] == 5
        assert _query(sync_engine, "SELECT count(*) FROM trading.pm_book_checkpoints")[0][0] == 5
        assert _query(
            sync_engine,
            "SELECT count(*) FROM trading.transactional_outbox WHERE topic='market.book'",
        )[0][0] == 5
        assert _query(
            sync_engine,
            "SELECT count(*) FROM trading.pm_source_event_batches b "
            "JOIN trading.artifact_objects a ON a.id=b.raw_artifact_id "
            "WHERE b.raw_artifact_ref=a.sha256",
        )[0][0] == 5
        assert _query(
            sync_engine,
            "SELECT count(*) FROM trading.pm_book_checkpoints c "
            "JOIN trading.artifact_objects a ON a.id=c.raw_artifact_id "
            "WHERE c.artifact_ref=a.sha256",
        )[0][0] == 5
        assert _query(
            sync_engine,
            "SELECT array_agg(DISTINCT kind ORDER BY kind) "
            "FROM trading.pm_source_event_index",
        )[0][0] == ["book", "price_change"]
    finally:
        sync_engine.dispose()


@pytest.mark.asyncio
async def test_official_tick_size_change_updates_checkpoint(stream_env):
    release_id = await _release_id(stream_env, "book-tick")
    now = datetime.now(timezone.utc) - timedelta(seconds=1)
    messages = [
        _wire_message(
            _book_payload("tick-token", [(Decimal("0.50"), 1)], [(Decimal("0.52"), 1)],
                          timestamp=1782753359001),
            1,
            now,
        ),
        _wire_message(_tick_payload("tick-token", timestamp=1782753359002), 2,
                      now + timedelta(milliseconds=10)),
    ]
    driver = ControlledWsDriver(messages, ["tick-token"])
    task = asyncio.create_task(
        _ingestor(stream_env, driver, release_id, "shard-tick").run_epoch()
    )
    await asyncio.wait_for(driver.waiting_disconnect.wait(), timeout=5)
    driver.disconnect.set()
    await asyncio.wait_for(task, timeout=5)

    engine = create_engine(stream_env["url"], poolclass=NullPool)
    try:
        row = _query(
            engine,
            "SELECT tick_size, min_order_size FROM trading.pm_book_checkpoints "
            "WHERE token_id='tick-token' AND source_kind='ws_delta_aggregate'",
        )[0]
        assert row == (Decimal("0.005"), None)
    finally:
        engine.dispose()


def test_freshness_hard_stops():
    policy = FreshnessPolicy(quote_ttl_s=30)
    now = datetime.now(timezone.utc)
    live_checkpoint = {"validity": "VALID", "completeness": True, "received_at": now}

    assert freshness(policy, now, epoch_status="CONNECTING", checkpoint=live_checkpoint,
                     best_bid=Decimal("0.5"), best_ask=Decimal("0.52")).reason == "quote_epoch_not_live"
    assert freshness(policy, now, epoch_status="LIVE", checkpoint=None,
                     best_bid=Decimal("0.5"), best_ask=Decimal("0.52")).reason == "quote_snapshot_incomplete"
    assert freshness(policy, now, epoch_status="LIVE",
                     checkpoint=dict(live_checkpoint, validity="STALE"),
                     best_bid=Decimal("0.5"), best_ask=Decimal("0.52")).reason == "quote_stale"
    assert freshness(policy, now, epoch_status="LIVE",
                     checkpoint=dict(live_checkpoint, completeness=False),
                     best_bid=Decimal("0.5"), best_ask=Decimal("0.52")).reason == "quote_snapshot_incomplete"
    assert freshness(policy, now, epoch_status="LIVE", checkpoint=live_checkpoint,
                     best_bid=None, best_ask=Decimal("0.52")).reason == "quote_side_missing"
    assert freshness(policy, now, epoch_status="LIVE", checkpoint=live_checkpoint,
                     best_bid=Decimal("0.53"), best_ask=Decimal("0.52")).reason == "quote_book_crossed"
    old = dict(live_checkpoint, received_at=now - timedelta(seconds=100))
    assert freshness(policy, now, epoch_status="LIVE", checkpoint=old,
                     best_bid=Decimal("0.5"), best_ask=Decimal("0.52")).reason == "quote_too_old"
    live = freshness(policy, now, epoch_status="LIVE", checkpoint=live_checkpoint,
                     best_bid=Decimal("0.5"), best_ask=Decimal("0.52"))
    assert live.live is True and live.hard_stop is None


def test_old_epoch_delta_rejected():
    now = datetime.now(timezone.utc)
    state = snapshot_book(
        token_id="t",
        bids=[(Decimal("0.5"), Decimal("1"))],
        asks=[(Decimal("0.52"), Decimal("1"))],
        tick_size=None,
        min_order_size=None,
        epoch_id=7,
        observed_at=now,
    )
    with pytest.raises(ValueError, match="delta_epoch_mismatch"):
        apply_delta(
            state,
            changes=[("bid", Decimal("0.5"), Decimal("0"))],
            epoch_id=999,
            received_at=now,
        )


def test_crossed_state_never_becomes_live():
    now = datetime.now(timezone.utc)
    state = snapshot_book(
        token_id="t",
        bids=[(Decimal("0.53"), Decimal("1"))],
        asks=[(Decimal("0.52"), Decimal("1"))],
        tick_size=None,
        min_order_size=None,
        epoch_id=1,
        observed_at=now,
    )
    assert state.crossed is True
    decision = freshness(
        FreshnessPolicy(),
        now,
        epoch_status="LIVE",
        checkpoint={"validity": "VALID", "completeness": True, "received_at": now},
        best_bid=state.best_bid,
        best_ask=state.best_ask,
    )
    assert decision.reason == "quote_book_crossed"
