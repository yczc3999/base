"""WP-01B universe ingest -- real PostgreSQL and scripted Gamma keyset wire.

The tests exercise the production lease/fence runtime.  No live network call is
made: httpx MockTransport returns official ``events|markets + next_cursor``
responses while the real GammaDriver still creates receipts and raw artifacts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork
from app.logics.trading.universe import UniverseLogic, UniversePolicy
from app.outbox.repository import OutboxRepository
from app.repositories.trading.market import MarketRepository
from app.repositories.trading.market_stream import MarketStreamRepository
from app.services.artifact_store.contracts import ArtifactRef, build_locator
from app.services.polymarket.base import WirePolicy
from app.services.polymarket.gamma_driver import GammaDriver
from runtimes.trading.market_ingest import UniverseIngestor
from tests.trading.fixtures.poly_fixtures import create_test_release_manifest


class FakeArtifactStore:
    """Immutable in-memory CAS with the ArtifactStore methods used by ingest."""

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


class GammaScript:
    """Cursor-addressed open/closed Gamma pages plus an optional blocking call."""

    def __init__(
        self,
        pages: dict[tuple[str, bool], list[dict]],
        *,
        block_on: tuple[str, bool, str | None] | None = None,
    ) -> None:
        self.pages = pages
        self.block_on = block_on
        self.calls: list[tuple[str, bool, str | None]] = []
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()

    def _page(self, kind: str, closed: bool, cursor: str | None) -> dict:
        chain = self.pages[(kind, closed)]
        if cursor is None:
            return chain[0]
        for index, page in enumerate(chain[:-1]):
            if page["next_cursor"] == cursor:
                return chain[index + 1]
        raise AssertionError(f"unexpected cursor for {kind}/{closed}: {cursor!r}")

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/events/keyset":
            kind = "events"
        elif request.url.path == "/markets/keyset":
            kind = "markets"
        else:  # pragma: no cover - fixture contract violation
            return httpx.Response(404, json={})
        closed = request.url.params.get("closed") == "true"
        cursor = request.url.params.get("after_cursor")
        call = (kind, closed, cursor)
        self.calls.append(call)
        if call == self.block_on:
            self.blocked.set()
            await self.release.wait()
        return httpx.Response(200, json=self._page(kind, closed, cursor))

    def driver(self) -> GammaDriver:
        policy = WirePolicy(
            max_retries=0,
            rate_per_second=100000,
            rate_burst=100000,
        )
        return GammaDriver(
            "https://gamma.fake",
            policy=policy,
            transport=httpx.MockTransport(self.handler),
        )


def _market(
    market_id: str,
    *,
    question: str | None = None,
    prices: tuple[str, str] = ("0.55", "0.45"),
    active: bool = True,
    closed: bool = False,
    accepting_orders: bool = True,
) -> dict:
    suffix = market_id.replace("mkt-", "")
    return {
        "id": market_id,
        "question": question or f"Question {market_id}",
        "description": f"Description {market_id}",
        "rules": f"Rules {market_id}",
        "resolutionSource": "https://resolution.example/source",
        "slug": f"slug-{suffix}",
        "conditionId": f"condition-{suffix}",
        "clobTokenIds": json.dumps([f"yes-{suffix}", f"no-{suffix}"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(list(prices)),
        "startDate": "2026-01-01T00:00:00Z",
        "endDate": "2026-12-31T23:59:59Z",
        "active": active,
        "closed": closed,
        "archived": False,
        "acceptingOrders": accepting_orders,
        "enableOrderBook": True,
        "negRisk": False,
    }


def _event(
    event_id: str,
    market: dict,
    *,
    active: bool = True,
    closed: bool = False,
) -> dict:
    return {
        "id": event_id,
        "slug": f"slug-{event_id}",
        "title": f"Title {event_id}",
        "description": f"Description {event_id}",
        "startDate": "2026-01-01T00:00:00Z",
        "endDate": "2026-12-31T23:59:59Z",
        "active": active,
        "closed": closed,
        "archived": False,
        "markets": [market],
    }


def _page(kind: str, items: list[dict], cursor: str | None) -> dict:
    return {kind: items, "next_cursor": "" if cursor is None else cursor}


def _catalog_script() -> GammaScript:
    open_a = _market("mkt-open-a")
    open_inactive = _market("mkt-open-inactive", active=False)
    closed = _market("mkt-closed", closed=True, accepting_orders=False)
    return GammaScript(
        {
            ("events", False): [
                _page("events", [_event("evt-open-a", open_a)], "events-open:1"),
                _page(
                    "events",
                    [_event("evt-open-inactive", open_inactive, active=False)],
                    None,
                ),
            ],
            ("events", True): [
                _page("events", [_event("evt-closed", closed, closed=True)], None)
            ],
            ("markets", False): [
                _page("markets", [open_a], "markets-open:1"),
                _page("markets", [open_inactive], None),
            ],
            ("markets", True): [_page("markets", [closed], None)],
        }
    )


def _version_script(
    *,
    question: str,
    prices: tuple[str, str],
    include_gone: bool,
) -> GammaScript:
    live = _market("mkt-live", question=question, prices=prices)
    gone = _market("mkt-gone")
    event_tail = [_event("evt-gone", gone)] if include_gone else []
    market_tail = [gone] if include_gone else []
    return GammaScript(
        {
            ("events", False): [
                _page("events", [_event("evt-live", live)], "events-open:stable"),
                _page("events", event_tail, None),
            ],
            ("events", True): [_page("events", [], None)],
            ("markets", False): [
                _page("markets", [live], "markets-open:stable"),
                _page("markets", market_tail, None),
            ],
            ("markets", True): [_page("markets", [], None)],
        }
    )


@pytest.fixture
def ingest_env(migrated_pg_db):
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
        "market_repo": MarketRepository(),
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


def _ingestor(
    env,
    script: GammaScript,
    release_id: int,
    *,
    owner: str,
    policy: UniversePolicy | None = None,
) -> UniverseIngestor:
    policy = policy or UniversePolicy()
    return UniverseIngestor(
        gamma=script.driver(),
        artifacts=env["artifacts"],
        uow_factory=lambda: UnitOfWork(env["sessions"]),
        market_repo=env["market_repo"],
        stream_repo=env["stream_repo"],
        universe=UniverseLogic(env["market_repo"], policy),
        outbox_repo=env["outbox_repo"],
        config_release_id=release_id,
        policy=policy,
        owner=owner,
    )


def _query(engine, sql: str, params=None):
    with engine.connect() as connection:
        return connection.execute(text(sql), params or {}).fetchall()


def _payload(store: FakeArtifactStore, sha: str) -> dict | list:
    return json.loads(store.data[sha])


@pytest.mark.asyncio
async def test_complete_open_closed_scan_has_exact_lineage_and_observability(ingest_env):
    release_id = await _release_id(ingest_env, "universe-complete")
    script = _catalog_script()
    result = await _ingestor(
        ingest_env, script, release_id, owner="catalog-owner"
    ).run_once()

    assert result.status == "COMPLETE"
    assert result.pages == 6
    assert result.total_events == 3
    assert result.total_markets == 3
    assert script.calls == [
        ("events", False, None),
        ("events", False, "events-open:1"),
        ("events", True, None),
        ("markets", False, None),
        ("markets", False, "markets-open:1"),
        ("markets", True, None),
    ]

    engine = create_engine(ingest_env["url"], poolclass=NullPool)
    try:
        frame = _query(
            engine,
            "SELECT status, page_count, total_events, total_markets, artifact_ref, "
            "content_hash FROM trading.pm_universe_frames WHERE id=:frame",
            {"frame": result.frame_id},
        )[0]
        assert frame[:4] == ("COMPLETE", 6, 3, 3)
        assert frame[4] == frame[5]
        manifest = _payload(ingest_env["artifacts"], frame[4])
        assert len(manifest) == 6

        # Every durable page resolves to an immutable catalog object, and the
        # canonical manifest pins the same page raw hashes in global order.
        assert _query(
            engine,
            "SELECT count(*) FROM trading.pm_universe_frame_pages p "
            "JOIN trading.artifact_objects a ON a.id=p.raw_artifact_id "
            "WHERE p.frame_id=:frame AND p.raw_artifact_ref=a.sha256 "
            "AND p.raw_artifact_hash=a.sha256",
            {"frame": result.frame_id},
        )[0][0] == 6
        page_rows = _query(
            engine,
            "SELECT endpoint, raw_artifact_ref FROM trading.pm_universe_frame_pages "
            "WHERE frame_id=:frame ORDER BY page_no",
            {"frame": result.frame_id},
        )
        assert [row[0] for row in page_rows] == [entry["endpoint"] for entry in manifest]
        assert [row[1] for row in page_rows] == [entry["raw_sha256"] for entry in manifest]

        # One HTTP receipt is indexed in one source batch for every page.  Its
        # response hash and batch artifact both point to that exact raw page.
        assert _query(
            engine,
            "SELECT count(*) FROM trading.pm_source_event_batches b "
            "JOIN trading.pm_source_event_index i "
            "ON i.batch_id=b.id AND i.received_at=b.received_at "
            "JOIN trading.pm_universe_frame_pages p "
            "ON p.frame_id=:frame AND p.raw_artifact_id=b.raw_artifact_id "
            "WHERE i.source='gamma' AND i.kind='request_attempt' "
            "AND i.method='GET' AND i.http_status=200 AND i.error_code IS NULL "
            "AND i.response_hash=p.raw_artifact_hash",
            {"frame": result.frame_id},
        )[0][0] == 6
        chain = _query(
            engine,
            "SELECT batch_no, batch_hash, prev_batch_hash "
            "FROM trading.pm_source_event_batches ORDER BY batch_no",
        )
        assert [row[0] for row in chain] == list(range(6))
        assert chain[0][2] is None
        assert all(chain[index][2] == chain[index - 1][1] for index in range(1, 6))

        # Event-embedded market membership is retained, while each master,
        # version and lifecycle row points to the precise page containing it.
        expected_mapping = {
            "mkt-open-a": "evt-open-a",
            "mkt-open-inactive": "evt-open-inactive",
            "mkt-closed": "evt-closed",
        }
        mappings = _query(
            engine,
            "SELECT m.gamma_market_id, m.gamma_event_id, m.raw_artifact_ref, "
            "v.raw_artifact_ref, l.raw_artifact_ref "
            "FROM trading.pm_markets m "
            "JOIN trading.pm_market_versions v ON v.market_id=m.id AND v.version_no=1 "
            "JOIN trading.pm_market_lifecycle_events l "
            "ON l.market_id=m.id AND l.event_type='created' ORDER BY m.gamma_market_id",
        )
        assert {row[0]: row[1] for row in mappings} == expected_mapping
        for market_id, _event_id, master_ref, version_ref, lifecycle_ref in mappings:
            assert master_ref == version_ref == lifecycle_ref
            raw_page = _payload(ingest_env["artifacts"], master_ref)
            assert market_id in {item["id"] for item in raw_page["markets"]}

        events = _query(
            engine,
            "SELECT gamma_event_id, raw_artifact_ref FROM trading.pm_events "
            "ORDER BY gamma_event_id",
        )
        for event_id, raw_ref in events:
            raw_page = _payload(ingest_env["artifacts"], raw_ref)
            assert event_id in {item["id"] for item in raw_page["events"]}

        assert _query(engine, "SELECT count(*) FROM trading.pm_tokens")[0][0] == 6
        assert _query(engine, "SELECT count(*) FROM trading.pm_token_versions")[0][0] == 6
        assert _query(
            engine,
            "SELECT min(version_no), max(version_no) FROM trading.pm_token_versions",
        )[0] == (1, 1)
        assert _query(
            engine,
            "SELECT gamma_market_id, current_version_no, eligible "
            "FROM trading.pm_market_current ORDER BY gamma_market_id",
        ) == [
            ("mkt-closed", 1, False),
            ("mkt-open-a", 1, True),
            ("mkt-open-inactive", 1, False),
        ]
        assert _query(
            engine,
            "SELECT status, config_release_id, closed_reason "
            "FROM trading.pm_connection_epochs WHERE provider='gamma'",
        ) == [("CLOSED", release_id, "frame_complete")]
        assert _query(
            engine,
            "SELECT release_manifest_id FROM trading.transactional_outbox "
            "WHERE topic='universe.frame'",
        ) == [(release_id,)]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_same_cursor_content_change_versions_tokens_and_revokes_absent(ingest_env):
    release_id = await _release_id(ingest_env, "universe-version")
    first_script = _version_script(
        question="Will version one happen?",
        prices=("0.55", "0.45"),
        include_gone=True,
    )
    first = await _ingestor(
        ingest_env, first_script, release_id, owner="version-owner-1"
    ).run_once()

    second_script = _version_script(
        question="Will version two happen?",
        prices=("0.60", "0.40"),
        include_gone=False,
    )
    second = await _ingestor(
        ingest_env, second_script, release_id, owner="version-owner-2"
    ).run_once()

    # A third byte-identical pass proves effect=0 after the real update.
    third_script = _version_script(
        question="Will version two happen?",
        prices=("0.60", "0.40"),
        include_gone=False,
    )
    third = await _ingestor(
        ingest_env, third_script, release_id, owner="version-owner-3"
    ).run_once()

    cursor_shape = [
        ("events", False, None),
        ("events", False, "events-open:stable"),
        ("events", True, None),
        ("markets", False, None),
        ("markets", False, "markets-open:stable"),
        ("markets", True, None),
    ]
    assert first_script.calls == second_script.calls == third_script.calls == cursor_shape

    engine = create_engine(ingest_env["url"], poolclass=NullPool)
    try:
        hashes = _query(
            engine,
            "SELECT id, content_hash FROM trading.pm_universe_frames ORDER BY id",
        )
        assert [row[0] for row in hashes] == [first.frame_id, second.frame_id, third.frame_id]
        assert hashes[0][1] != hashes[1][1], "raw content changed under identical cursors"
        assert hashes[1][1] == hashes[2][1], "byte-identical frame hash must be stable"

        assert _query(
            engine,
            "SELECT m.gamma_market_id, count(v.id), max(v.version_no) "
            "FROM trading.pm_markets m JOIN trading.pm_market_versions v ON v.market_id=m.id "
            "GROUP BY m.gamma_market_id ORDER BY m.gamma_market_id",
        ) == [("mkt-gone", 1, 1), ("mkt-live", 2, 2)]
        assert _query(
            engine,
            "SELECT gamma_market_id, current_version_no, eligible "
            "FROM trading.pm_market_current ORDER BY gamma_market_id",
        ) == [("mkt-gone", 1, False), ("mkt-live", 2, True)]

        # Both live token price hints changed once, so each has exactly two
        # immutable versions; the absent market's tokens remain at version one.
        assert _query(
            engine,
            "SELECT t.token_id, count(v.id), max(v.version_no) "
            "FROM trading.pm_tokens t JOIN trading.pm_token_versions v ON v.token_id=t.id "
            "GROUP BY t.token_id ORDER BY t.token_id",
        ) == [
            ("no-gone", 1, 1),
            ("no-live", 2, 2),
            ("yes-gone", 1, 1),
            ("yes-live", 2, 2),
        ]
        assert _query(
            engine,
            "SELECT m.gamma_market_id, l.event_type, count(*) "
            "FROM trading.pm_market_lifecycle_events l "
            "JOIN trading.pm_markets m ON m.id=l.market_id "
            "GROUP BY m.gamma_market_id, l.event_type "
            "ORDER BY m.gamma_market_id, l.event_type",
        ) == [
            ("mkt-gone", "created", 1),
            ("mkt-live", "created", 1),
            ("mkt-live", "updated", 1),
        ]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_live_lease_is_busy_then_expired_owner_resumes_without_refetch(ingest_env):
    release_id = await _release_id(ingest_env, "universe-resume")
    complete = _catalog_script().pages
    first_script = GammaScript(
        complete,
        block_on=("events", False, "events-open:1"),
    )
    short_lease = UniversePolicy(frame_lease_s=2)
    first_task = asyncio.create_task(
        _ingestor(
            ingest_env,
            first_script,
            release_id,
            owner="crashed-owner",
            policy=short_lease,
        ).run_once()
    )

    # The second request begins only after page 0 (raw artifact + receipt/index)
    # committed.  Cancellation therefore models a crash after a durable cursor.
    await asyncio.wait_for(first_script.blocked.wait(), timeout=5)
    engine = create_engine(ingest_env["url"], poolclass=NullPool)
    try:
        open_before = _query(
            engine,
            "SELECT id, owner, fencing_token, status, page_count, lease_expires_at "
            "FROM trading.pm_universe_frames",
        )[0]
        frame_id = open_before[0]
        assert open_before[1:5] == ("crashed-owner", 1, "OPEN", 1)
        first_page = _query(
            engine,
            "SELECT cursor_input, cursor_output, raw_artifact_ref "
            "FROM trading.pm_universe_frame_pages WHERE frame_id=:frame",
            {"frame": frame_id},
        )[0]
        assert first_page[:2] == (None, "events-open:1")
        assert _query(
            engine,
            "SELECT count(*) FROM trading.pm_source_event_index i "
            "JOIN trading.pm_source_event_batches b "
            "ON b.id=i.batch_id AND b.received_at=i.received_at "
            "WHERE b.raw_artifact_ref=:raw AND i.kind='request_attempt'",
            {"raw": first_page[2]},
        )[0][0] == 1
    finally:
        engine.dispose()

    # A concurrent owner sees a live lease, performs no network call, creates no
    # second frame and cannot mark the in-progress frame failed.
    busy_script = _catalog_script()
    with pytest.raises(RuntimeError, match="universe_frame_busy"):
        await _ingestor(
            ingest_env,
            busy_script,
            release_id,
            owner="busy-owner",
            policy=short_lease,
        ).run_once()
    assert busy_script.calls == []

    engine = create_engine(ingest_env["url"], poolclass=NullPool)
    try:
        assert _query(
            engine,
            "SELECT id, owner, fencing_token, status, page_count "
            "FROM trading.pm_universe_frames",
        ) == [(frame_id, "crashed-owner", 1, "OPEN", 1)]
    finally:
        engine.dispose()

    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_task

    # Wait for the database-authoritative wall-clock lease to expire.  A new
    # owner then takes over the same frame with fence+1 and starts at cursor 1.
    delay = max(
        0.0,
        (open_before[5] - datetime.now(timezone.utc)).total_seconds() + 0.15,
    )
    await asyncio.sleep(delay)
    resume_script = _catalog_script()
    resumed = await _ingestor(
        ingest_env,
        resume_script,
        release_id,
        owner="resume-owner",
        policy=short_lease,
    ).run_once()

    assert resumed.frame_id == frame_id
    assert resumed.status == "COMPLETE"
    assert resume_script.calls[0] == ("events", False, "events-open:1")
    assert ("events", False, None) not in resume_script.calls
    assert resume_script.calls == [
        ("events", False, "events-open:1"),
        ("events", True, None),
        ("markets", False, None),
        ("markets", False, "markets-open:1"),
        ("markets", True, None),
    ]

    engine = create_engine(ingest_env["url"], poolclass=NullPool)
    try:
        assert _query(
            engine,
            "SELECT owner, fencing_token, status, page_count "
            "FROM trading.pm_universe_frames WHERE id=:frame",
            {"frame": frame_id},
        ) == [("resume-owner", 2, "COMPLETE", 6)]
        assert _query(
            engine,
            "SELECT cursor_input, cursor_output, raw_artifact_ref "
            "FROM trading.pm_universe_frame_pages WHERE frame_id=:frame AND page_no=0",
            {"frame": frame_id},
        ) == [first_page]
        assert _query(
            engine,
            "SELECT count(*) FROM trading.pm_source_event_index "
            "WHERE source='gamma' AND kind='request_attempt'",
        )[0][0] == 6
        assert _query(
            engine,
            "SELECT count(*) FROM trading.pm_universe_frames",
        )[0][0] == 1
    finally:
        engine.dispose()
