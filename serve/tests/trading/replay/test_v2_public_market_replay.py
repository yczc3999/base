"""
WP-01B 公共行情重放 —— 真 PostgreSQL（不访问公网）。

覆盖（任务 §6.5）：固定 artifact/event 流重放两次得到相同 versions/current/checkpoint hash；
DB 重启（TRUNCATE 保留 schema 后从同一固定 artifacts 重放）后相同。

- Universe：同一 fixed Gamma wire 两轮 ingest → market_current.content_hash 与
  market_versions.normalized_hash 逐行一致（TRUNCATE 模拟进程重启）。
- Book：从 CAS 读取固定 official WS raw frame，两次通过真实 BookWsIngestor 重放；
  清空并重建 runtime/repository 后，source/checkpoint/levels/current/outbox 完全一致。
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork
from app.logics.trading.market_data import FreshnessPolicy
from app.logics.trading.universe import UniverseLogic, UniversePolicy
from app.outbox.repository import OutboxRepository
from app.repositories.trading.market import MarketRepository
from app.repositories.trading.market_stream import MarketStreamRepository
from app.schemas.polymarket.common import PolymarketError, REASON_WS_DISCONNECT
from app.schemas.polymarket.market_ws import MarketWsUnknown, parse_market_ws_frame
from app.services.artifact_store.contracts import ArtifactRef, build_locator
from app.services.polymarket.base import WirePolicy
from app.services.polymarket.gamma_driver import GammaDriver
from app.services.polymarket.market_ws_driver import MarketWsMessage
from runtimes.trading.market_ingest import BookWsIngestor, UniverseIngestor
from tests.trading.fixtures.poly_fixtures import create_test_release_manifest


class FakeArtifactStore:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def put_bytes(self, data: bytes, mime: str, compression: str = "none") -> ArtifactRef:
        sha = hashlib.sha256(data).hexdigest()
        self.data.setdefault(sha, data)
        return ArtifactRef(
            sha256=sha, original_size=len(data), stored_size=len(data), mime=mime,
            compression="none", storage_driver="local", locator=build_locator(sha, "none"),
        )

    def get_bytes(self, ref: ArtifactRef) -> bytes:
        return self.data[ref.sha256]


class ArtifactReplayWsDriver:
    """Reads fixed raw frames from CAS and exposes the real MarketWsMessage API."""

    def __init__(self, artifacts, refs, received_at, assets_ids):
        self.artifacts = artifacts
        self.refs = list(refs)
        self.received_at = list(received_at)
        self.assets_ids = list(assets_ids)
        self.index = 0
        self.closed = False

    async def connect(self) -> None:
        return None

    async def next_frame(self) -> MarketWsMessage:
        if self.index >= len(self.refs):
            raise PolymarketError(REASON_WS_DISCONNECT)
        raw = self.artifacts.get_bytes(self.refs[self.index]).decode("utf-8")
        frame = parse_market_ws_frame(raw)
        assert not isinstance(frame, MarketWsUnknown), frame.parse_error
        self.index += 1
        return MarketWsMessage(
            receive_seq=self.index,
            received_at=self.received_at[self.index - 1],
            raw_text=raw,
            frame=frame,
        )

    async def aclose(self) -> None:
        self.closed = True


def _event(eid, slug):
    return {"id": eid, "slug": slug, "title": f"T {slug}",
            "startDate": "2024-01-01T00:00:00Z", "endDate": "2024-12-31T00:00:00Z",
            "active": True, "closed": False, "archived": False}


def _market(mid, condition, yes, no):
    return {"id": mid, "question": f"Q {mid}", "conditionId": condition,
            "clobTokenIds": json.dumps([yes, no]), "outcomes": json.dumps(["Yes", "No"]),
            "outcomePrices": json.dumps(["0.55", "0.45"]), "active": True, "closed": False,
            "archived": False, "acceptingOrders": True, "enableOrderBook": True, "negRisk": False}


def _gamma_driver():
    events_pages = [
        {"events": [_event("evt-1", "e1"), _event("evt-2", "e2")], "next_cursor": "e:1"},
        {"events": [_event("evt-3", "e3")], "next_cursor": ""},
    ]
    markets_pages = [
        {"markets": [_market("mkt-1", "c1", "ty1", "tn1"), _market("mkt-2", "c2", "ty2", "tn2")],
         "next_cursor": "m:1"},
        {"markets": [_market("mkt-3", "c3", "ty3", "tn3")], "next_cursor": ""},
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/events/keyset":
            if request.url.params.get("closed") == "true":
                return httpx.Response(200, json={"events": [], "next_cursor": ""})
            idx = 0 if not request.url.params.get("after_cursor") else 1
            return httpx.Response(200, json=events_pages[idx])
        if request.url.path == "/markets/keyset":
            if request.url.params.get("closed") == "true":
                return httpx.Response(200, json={"markets": [], "next_cursor": ""})
            idx = 0 if not request.url.params.get("after_cursor") else 1
            return httpx.Response(200, json=markets_pages[idx])
        return httpx.Response(404, json={})

    policy = WirePolicy(max_retries=0, rate_per_second=100000, rate_burst=100000)
    return GammaDriver("https://gamma.fake", policy=policy, transport=httpx.MockTransport(handler))


@pytest.fixture
def replay_env(migrated_pg_db):
    admin = make_url(migrated_pg_db.url)
    async_url = admin.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    engine = create_async_engine(async_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    env = {
        "sessions": sessions,
        "artifacts": FakeArtifactStore(),
        "market_repo": MarketRepository(),
        "stream_repo": MarketStreamRepository(),
        "outbox_repo": OutboxRepository(),
        "url": migrated_pg_db.url,
    }
    yield env
    engine.sync_engine.dispose()


def _query(engine, sql, params=None):
    with engine.connect() as c:
        return c.execute(text(sql), params or {}).fetchall()


def _restart(engine):
    """模拟进程重启：清 projection/evidence，保留 schema、release 与 CAS catalog。"""
    with engine.connect() as c:
        c.execute(
            text(
                "TRUNCATE trading.pm_universe_frames, trading.pm_events, "
                "trading.pm_markets, trading.pm_source_event_index, "
                "trading.pm_source_event_batches, trading.pm_book_levels, "
                "trading.pm_book_current, trading.pm_book_checkpoints, "
                "trading.pm_connection_epochs, trading.transactional_outbox, "
                "trading.idempotency_claims RESTART IDENTITY CASCADE"
            )
        )
        c.commit()


def _universe_ingestor(env, release_id: int):
    return UniverseIngestor(
        gamma=_gamma_driver(),
        artifacts=env["artifacts"],
        uow_factory=lambda: UnitOfWork(env["sessions"]),
        market_repo=env["market_repo"],
        stream_repo=env["stream_repo"],
        universe=UniverseLogic(env["market_repo"], UniversePolicy()),
        outbox_repo=env["outbox_repo"],
        config_release_id=release_id,
    )


async def _release_id(env, key: str) -> int:
    uow = UnitOfWork(env["sessions"])
    async with uow:
        return await create_test_release_manifest(uow.session, key=key)


# ---------------- Universe 重放 ----------------

@pytest.mark.asyncio
async def test_universe_replay_same_versions_and_current_hashes(replay_env):
    release_id = await _release_id(replay_env, "universe-replay")
    await _universe_ingestor(replay_env, release_id).run_once()
    eng = create_engine(replay_env["url"], poolclass=NullPool)
    try:
        current1 = _query(eng, "SELECT gamma_market_id, content_hash, eligible, mapping_state "
                               "FROM trading.pm_market_current ORDER BY gamma_market_id")
        # 用稳定 gamma_market_id 对齐版本（内部 market_id 是 surrogate，重启后 sequence 不归零）
        versions1 = _query(
            eng,
            "SELECT m.gamma_market_id, v.version_no, v.normalized_hash "
            "FROM trading.pm_market_versions v "
            "JOIN trading.pm_markets m ON m.id=v.market_id "
            "ORDER BY m.gamma_market_id, v.version_no",
        )
    finally:
        eng.dispose()

    # 模拟 DB 重启：清空后从同一 fixed artifacts 重放
    eng = create_engine(replay_env["url"], poolclass=NullPool)
    try:
        _restart(eng)
    finally:
        eng.dispose()
    await _universe_ingestor(replay_env, release_id).run_once()

    eng = create_engine(replay_env["url"], poolclass=NullPool)
    try:
        current2 = _query(eng, "SELECT gamma_market_id, content_hash, eligible, mapping_state "
                               "FROM trading.pm_market_current ORDER BY gamma_market_id")
        versions2 = _query(
            eng,
            "SELECT m.gamma_market_id, v.version_no, v.normalized_hash "
            "FROM trading.pm_market_versions v "
            "JOIN trading.pm_markets m ON m.id=v.market_id "
            "ORDER BY m.gamma_market_id, v.version_no",
        )
        assert current1 == current2, "current projection 重放不一致"
        assert versions1 == versions2, "market versions 重放不一致"
    finally:
        eng.dispose()


# ---------------- Book raw-artifact replay ----------------


def _book_wire(token: str, *, timestamp: int) -> bytes:
    return json.dumps(
        {
            "event_type": "book",
            "market": "condition-replay",
            "asset_id": token,
            "timestamp": timestamp,
            "hash": hashlib.sha256(f"book-{timestamp}".encode()).hexdigest(),
            "bids": [
                {"price": "0.50", "size": "100"},
                {"price": "0.49", "size": "200"},
            ],
            "asks": [
                {"price": "0.52", "size": "300"},
                {"price": "0.53", "size": "400"},
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _change_wire(token: str, *, timestamp: int) -> bytes:
    return json.dumps(
        {
            "event_type": "price_change",
            "market": "condition-replay",
            "timestamp": timestamp,
            "price_changes": [
                {
                    "asset_id": token,
                    "price": "0.50",
                    "size": "0",
                    "side": "BUY",
                    "hash": hashlib.sha256(f"delete-{timestamp}".encode()).hexdigest(),
                    "best_bid": "0.49",
                    "best_ask": "0.52",
                },
                {
                    "asset_id": token,
                    "price": "0.51",
                    "size": "250",
                    "side": "BUY",
                    "hash": hashlib.sha256(f"insert-{timestamp}".encode()).hexdigest(),
                    "best_bid": "0.51",
                    "best_ask": "0.52",
                },
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _book_projection(engine):
    """Return only deterministic evidence/projection fields (never wall-clock stale_at)."""
    return {
        "source": _query(
            engine,
            "SELECT i.kind, i.local_receive_seq, i.payload_hash, b.raw_artifact_ref, "
            "a.sha256 FROM trading.pm_source_event_index i "
            "JOIN trading.pm_source_event_batches b "
            "ON b.id=i.batch_id AND b.received_at=i.received_at "
            "JOIN trading.artifact_objects a ON a.id=b.raw_artifact_id "
            "ORDER BY i.local_receive_seq",
        ),
        "checkpoints": _query(
            engine,
            "SELECT c.source_kind, c.book_hash, c.best_bid, c.best_ask, c.tick_size, "
            "c.min_order_size, c.completeness, c.validity, c.artifact_ref, a.sha256 "
            "FROM trading.pm_book_checkpoints c "
            "JOIN trading.artifact_objects a ON a.id=c.raw_artifact_id "
            "ORDER BY c.received_at, c.id",
        ),
        "levels": _query(
            engine,
            "SELECT c.source_kind, l.side, l.price, l.size, l.ordinal "
            "FROM trading.pm_book_levels l JOIN trading.pm_book_checkpoints c "
            "ON c.id=l.checkpoint_id AND c.received_at=l.received_at "
            "ORDER BY c.received_at, c.id, l.ordinal",
        ),
        "current": _query(
            engine,
            "SELECT token_id, best_bid, best_ask, depth_hash, validity "
            "FROM trading.pm_book_current ORDER BY token_id",
        ),
        "outbox": _query(
            engine,
            "SELECT event_id, topic, aggregate_id, idempotency_key, payload "
            "FROM trading.transactional_outbox WHERE topic='market.book' ORDER BY event_id",
        ),
    }


def _book_ingestor(env, refs, received_at, release_id):
    # Every call constructs a fresh Driver, Repository, OutboxRepository and Runtime.
    driver = ArtifactReplayWsDriver(
        env["artifacts"], refs, received_at, ["t-replay"]
    )
    ingestor = BookWsIngestor(
        ws_driver_factory=lambda: driver,
        artifacts=env["artifacts"],
        uow_factory=lambda: UnitOfWork(env["sessions"]),
        stream_repo=MarketStreamRepository(),
        outbox_repo=OutboxRepository(),
        freshness_policy=FreshnessPolicy(quote_ttl_s=30),
        shard_key="replay-shard",
        config_release_id=release_id,
    )
    return ingestor, driver


@pytest.mark.asyncio
async def test_book_raw_artifact_replay_is_deterministic_across_runtime_restart(replay_env):
    """Both passes read the same CAS wire and execute the actual BookWsIngestor."""

    release_id = await _release_id(replay_env, "book-replay")
    raw = [
        _book_wire("t-replay", timestamp=1782753357001),
        _change_wire("t-replay", timestamp=1782753357002),
    ]
    refs = [
        replay_env["artifacts"].put_bytes(item, "application/json") for item in raw
    ]
    base = datetime.now(timezone.utc) - timedelta(seconds=2)
    received_at = [base, base + timedelta(milliseconds=10)]

    first_runtime, first_driver = _book_ingestor(
        replay_env, refs, received_at, release_id
    )
    await first_runtime.run_epoch(started_at=base - timedelta(seconds=1))
    assert first_driver.closed is True and first_driver.index == 2

    engine = create_engine(replay_env["url"], poolclass=NullPool)
    try:
        first = _book_projection(engine)
        assert len(first["source"]) == 2
        assert len(first["checkpoints"]) == 2
        assert len(first["outbox"]) == 2
        assert first["current"][0][1:3] == (Decimal("0.51"), Decimal("0.52"))
        assert first["current"][0][4] == "STALE"
        assert all(row[3] == row[4] for row in first["source"])
        assert all(row[8] == row[9] for row in first["checkpoints"])

        _restart(engine)
        assert _query(engine, "SELECT count(*) FROM trading.pm_book_current")[0][0] == 0
        # CAS catalog deliberately survives the restart and remains the replay source.
        assert _query(engine, "SELECT count(*) FROM trading.artifact_objects")[0][0] == 2
    finally:
        engine.dispose()

    second_runtime, second_driver = _book_ingestor(
        replay_env, refs, received_at, release_id
    )
    await second_runtime.run_epoch(started_at=base - timedelta(seconds=1))
    assert second_driver.closed is True and second_driver.index == 2

    engine = create_engine(replay_env["url"], poolclass=NullPool)
    try:
        second = _book_projection(engine)
        assert second == first
        assert _query(engine, "SELECT count(*) FROM trading.artifact_objects")[0][0] == 2
    finally:
        engine.dispose()
