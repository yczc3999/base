"""WP-01B quantified PostgreSQL ingest smoke.

Acceptance run (about 80 seconds, PostgreSQL required)::

    V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
      .venv/bin/python -m tests.trading.performance.ingest_smoke

Fast engineering check (same rates and SLOs, shorter durations; not acceptance evidence)::

    V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' \
      .venv/bin/python -m tests.trading.performance.ingest_smoke --quick

The harness uses the real ``b1000010``/``b1000011`` schema, a published release
manifest, an exact artifact catalog, and a bounded SQLAlchemy pool.  It paces rather
than merely bulk-inserting: 1,000 events/s for 60 seconds, then 5,000 events/s for
10 seconds.  Results and every hard assertion are written to
``/tmp/pm_v2_perf_smoke.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SERVE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SERVE_DIR))

from app.db.uow import UnitOfWork  # noqa: E402
from app.repositories.trading.market import MarketRepository  # noqa: E402
from app.repositories.trading.market_stream import MarketStreamRepository  # noqa: E402
from app.services.artifact_store import ArtifactRef, ArtifactStore  # noqa: E402
from app.services.artifact_store.drivers.local import LocalArtifactDriver  # noqa: E402
from tests.trading.fixtures.poly_fixtures import create_test_release_manifest  # noqa: E402

ADMIN_URL = os.environ.get("V2_TEST_ADMIN_DATABASE_URL", "postgresql+psycopg:///postgres")
TEMP_PREFIX = "pm_v2_perf_"
OUTPUT_DEFAULT = "/tmp/pm_v2_perf_smoke.json"

POOL_SIZE = 4
MAX_OVERFLOW = 0
POOL_BUDGET = POOL_SIZE + MAX_OVERFLOW
POOL_TIMEOUT_S = 3
SOURCE_P99_LIMIT_MS = 250.0
BOOK_P99_LIMIT_MS = 750.0
# Flush every 100 ms.  This makes the latency distribution event-based: the oldest
# event in a batch includes its queueing time instead of pretending 1,000 events arrive
# atomically once per second.
SOURCE_BATCH_INTERVAL_S = 0.1
BOOK_TOKEN_COUNT = 100


@dataclass(frozen=True)
class Workload:
    mode: str
    markets: int
    sustained_duration_s: int
    burst_duration_s: int
    book_writes: int
    acceptance_qualified: bool


FULL = Workload(
    mode="full",
    markets=10_000,
    sustained_duration_s=60,
    burst_duration_s=10,
    book_writes=1_000,
    acceptance_qualified=True,
)
QUICK = Workload(
    mode="quick",
    markets=1_000,
    sustained_duration_s=3,
    burst_duration_s=3,
    book_writes=100,
    acceptance_qualified=False,
)


class PoolProbe:
    """Thread-safe live/peak checkout counter for the real workload pool."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0

    def checkout(self, *_args: object) -> None:
        with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)

    def checkin(self, *_args: object) -> None:
        with self._lock:
            self.current -= 1
            if self.current < 0:
                raise AssertionError("pool checkout counter became negative")

    def reset_peak(self) -> None:
        with self._lock:
            self.peak = self.current

    def snapshot(self) -> tuple[int, int]:
        with self._lock:
            return self.current, self.peak


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _nearest_rank(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile / 100 * len(ordered)) - 1)
    return round(ordered[index], 3)


def _percentiles(values: list[float]) -> dict[str, float | int]:
    return {
        "p50": _nearest_rank(values, 50),
        "p95": _nearest_rank(values, 95),
        "p99": _nearest_rank(values, 99),
        "count": len(values),
        "max": round(max(values), 3) if values else 0.0,
    }


async def _put_artifact(store: ArtifactStore, payload: bytes) -> ArtifactRef:
    # Local driver deliberately fsyncs.  Keep its blocking filesystem work off the loop.
    return await asyncio.to_thread(
        store.put_bytes,
        payload,
        "application/json",
        "none",
    )


async def _warm_pool(engine, width: int) -> None:
    """Open all configured slots concurrently so connection startup is not timed."""
    ready = asyncio.Event()
    entered = 0
    entered_lock = asyncio.Lock()

    async def holder() -> None:
        nonlocal entered
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            async with entered_lock:
                entered += 1
                if entered == width:
                    ready.set()
            await ready.wait()

    await asyncio.gather(*(holder() for _ in range(width)))


async def _create_live_epoch(
    sessions: async_sessionmaker,
    stream: MarketStreamRepository,
    *,
    release_id: int,
    shard_key: str,
) -> int:
    now = _utcnow()
    async with UnitOfWork(sessions) as uow:
        epoch_id = await stream.create_epoch(
            uow.session,
            shard_key=shard_key,
            provider="market_ws",
            started_at=now,
            config_release_id=release_id,
            owner="wp01b-perf",
            fencing_token=1,
        )
        assert await stream.transition_epoch(
            uow.session, epoch_id, "CONNECTING", "SYNCING", at=now
        )
        assert await stream.transition_epoch(
            uow.session, epoch_id, "SYNCING", "LIVE", at=now
        )
    return int(epoch_id)


async def _frame_ingest(
    sessions: async_sessionmaker,
    market: MarketRepository,
    stream: MarketStreamRepository,
    store: ArtifactStore,
    n_markets: int,
) -> dict[str, Any]:
    """Persist one COMPLETE frame plus market/version/token/current projections."""
    started_at = _utcnow()
    lease_expires_at = started_at + timedelta(minutes=30)
    owner = f"wp01b-perf-{uuid.uuid4().hex[:12]}"
    batch_commit_ms: list[float] = []
    wall_started = time.perf_counter()

    async with UnitOfWork(sessions) as uow:
        frame = await market.acquire_frame(
            uow.session,
            owner=owner,
            started_at=started_at,
            lease_expires_at=lease_expires_at,
        )
    frame_id = int(frame["id"])
    fencing_token = int(frame["fencing_token"])

    endpoints: tuple[tuple[str, list[str]], ...] = (
        ("events_open", []),
        ("events_closed", []),
        ("markets_open", [f"perf-mkt-{i:06d}" for i in range(n_markets)]),
        ("markets_closed", []),
    )
    page_refs: list[tuple[str, int, ArtifactRef]] = []
    for page_no, (endpoint, items) in enumerate(endpoints):
        payload = json.dumps(
            {"endpoint": endpoint, "items": items},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        ref = await _put_artifact(store, payload)
        received_at = _utcnow()
        async with UnitOfWork(sessions) as uow:
            artifact_id = await stream.register_artifact(uow.session, ref)
            inserted = await market.append_page(
                uow.session,
                frame_id=frame_id,
                page_no=page_no,
                endpoint=endpoint,
                cursor_input=None,
                cursor_output=None,
                item_count=len(items),
                raw_artifact_id=artifact_id,
                raw_artifact_ref=ref.sha256,
                raw_artifact_hash=ref.sha256,
                received_at=received_at,
                owner=owner,
                fencing_token=fencing_token,
            )
            assert inserted
        page_refs.append((endpoint, len(items), ref))

    market_artifact_ref = next(ref for endpoint, _, ref in page_refs if endpoint == "markets_open")
    observed_at = _utcnow()
    for batch_start in range(0, n_markets, 500):
        batch = range(batch_start, min(batch_start + 500, n_markets))
        commit_started = time.perf_counter()
        async with UnitOfWork(sessions) as uow:
            for i in batch:
                gamma_market_id = f"perf-mkt-{i:06d}"
                condition_id = f"perf-cond-{i:06d}"
                content_hash = _sha(gamma_market_id.encode())
                market_db_id = await market.upsert_market(
                    uow.session,
                    gamma_market_id=gamma_market_id,
                    gamma_event_id=None,
                    condition_id=condition_id,
                    question=f"Performance market {i}",
                    slug=f"performance-market-{i}",
                    ticker=None,
                    active=True,
                    closed=False,
                    archived=False,
                    accepting_orders=True,
                    enable_order_book=True,
                    neg_risk=False,
                    start_date=observed_at,
                    end_date=None,
                    closed_at=None,
                    volume=None,
                    liquidity=None,
                    spread=None,
                    best_bid=None,
                    best_ask=None,
                    last_trade_price=None,
                    content_hash=content_hash,
                    raw_artifact_ref=market_artifact_ref.sha256,
                )
                await market.append_market_version(
                    uow.session,
                    market_db_id=market_db_id,
                    question=f"Performance market {i}",
                    description=None,
                    rules=None,
                    resolution_source=None,
                    start_date=observed_at,
                    end_date=None,
                    active=True,
                    closed=False,
                    archived=False,
                    accepting_orders=True,
                    enable_order_book=True,
                    neg_risk=False,
                    observed_at=observed_at,
                    received_at=observed_at,
                    raw_artifact_ref=market_artifact_ref.sha256,
                    normalized_hash=content_hash,
                )
                for outcome_index, outcome_label in ((0, "Yes"), (1, "No")):
                    token_db_id = await market.upsert_token(
                        uow.session,
                        token_id=f"perf-{'yes' if outcome_index == 0 else 'no'}-{i:06d}",
                        market_db_id=market_db_id,
                        outcome_index=outcome_index,
                        outcome_label=outcome_label,
                        price_hint=None,
                    )
                    await market.append_token_version(
                        uow.session,
                        token_db_id=token_db_id,
                        outcome_index=outcome_index,
                        outcome_label=outcome_label,
                        price_hint=None,
                        observed_at=observed_at,
                        received_at=observed_at,
                    )
                assert await market.set_market_current(
                    uow.session,
                    market_db_id=market_db_id,
                    condition_id=condition_id,
                    gamma_market_id=gamma_market_id,
                    tokens_ok=True,
                    mapping_state="complete",
                    eligible=True,
                    current_version_no=1,
                    observed_at=observed_at,
                    content_hash=content_hash,
                )
        batch_commit_ms.append((time.perf_counter() - commit_started) * 1_000)

    manifest_payload = json.dumps(
        {
            "frame_id": frame_id,
            "pages": [
                {"endpoint": endpoint, "item_count": item_count, "sha256": ref.sha256}
                for endpoint, item_count, ref in page_refs
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest_ref = await _put_artifact(store, manifest_payload)
    completed_at = _utcnow()
    async with UnitOfWork(sessions) as uow:
        artifact_id = await stream.register_artifact(uow.session, manifest_ref)
        assert await market.finalize_frame(
            uow.session,
            frame_id=frame_id,
            status="COMPLETE",
            total_events=0,
            total_markets=n_markets,
            content_hash=manifest_ref.sha256,
            artifact_id=artifact_id,
            artifact_ref=manifest_ref.sha256,
            error_reason=None,
            owner=owner,
            fencing_token=fencing_token,
            completed_at=completed_at,
        )

    return {
        "frame_id": frame_id,
        "markets": n_markets,
        "tokens": n_markets * 2,
        "market_versions": n_markets,
        "token_versions": n_markets * 2,
        "pages": 4,
        "wall_s": round(time.perf_counter() - wall_started, 3),
        "batch_commit_ms": _percentiles(batch_commit_ms),
    }


def _source_batch_hash(batch_no: int, first_seq: int, count: int) -> str:
    return _sha(f"source-batch:{batch_no}:{first_seq}:{count}".encode())


async def _persist_source_batch(
    sessions: async_sessionmaker,
    stream: MarketStreamRepository,
    store: ArtifactStore,
    semaphore: asyncio.Semaphore,
    *,
    epoch_id: int,
    batch_no: int,
    first_seq: int,
    batch_rows: int,
    previous_hash: str | None,
    first_received_at: datetime,
    last_received_at: datetime,
    first_receipt_monotonic: float,
    receipt_step_s: float,
) -> list[float]:
    async with semaphore:
        batch_hash = _source_batch_hash(batch_no, first_seq, batch_rows)
        raw_payload = json.dumps(
            {
                "batch_no": batch_no,
                "first_receive_seq": first_seq,
                "last_receive_seq": first_seq + batch_rows - 1,
                "event_count": batch_rows,
                "batch_hash": batch_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        ref = await _put_artifact(store, raw_payload)
        events = [
            {
                "source": "market_ws",
                "kind": "price_change",
                "connection_epoch_id": epoch_id,
                "local_receive_seq": seq,
                "provider_time": None,
                "event_time": None,
                "payload_hash": _sha(f"source-event:{epoch_id}:{seq}".encode()),
                "batch_ordinal": seq - first_seq,
                "parse_status": "parsed",
                "parse_reason": None,
                "condition_id": f"perf-cond-{seq % BOOK_TOKEN_COUNT:06d}",
                "token_id": f"perf-yes-{seq % BOOK_TOKEN_COUNT:06d}",
                "gamma_market_id": f"perf-mkt-{seq % BOOK_TOKEN_COUNT:06d}",
            }
            for seq in range(first_seq, first_seq + batch_rows)
        ]
        async with UnitOfWork(sessions) as uow:
            artifact_id = await stream.register_artifact(uow.session, ref)
            source_batch_id = await stream.insert_source_batch(
                uow.session,
                connection_epoch_id=epoch_id,
                batch_no=batch_no,
                first_receive_seq=first_seq,
                last_receive_seq=first_seq + batch_rows - 1,
                first_received_at=first_received_at,
                last_received_at=last_received_at,
                event_count=batch_rows,
                batch_hash=batch_hash,
                prev_batch_hash=previous_hash,
                raw_artifact_ref=ref.sha256,
                raw_artifact_id=artifact_id,
                received_at=last_received_at,
            )
            inserted = await stream.insert_source_events(
                uow.session,
                batch_id=source_batch_id,
                received_at=last_received_at,
                events=events,
            )
            assert inserted == batch_rows
        durable_monotonic = time.perf_counter()
        return [
            (durable_monotonic - (first_receipt_monotonic + ordinal * receipt_step_s))
            * 1_000
            for ordinal in range(batch_rows)
        ]


async def _paced_source_phase(
    sessions: async_sessionmaker,
    stream: MarketStreamRepository,
    store: ArtifactStore,
    *,
    epoch_id: int,
    phase: str,
    target_rate_per_s: int,
    duration_s: int,
    first_batch_no: int,
    first_seq: int,
    previous_hash: str | None,
) -> tuple[dict[str, Any], int, int, str | None]:
    batch_rows = int(target_rate_per_s * SOURCE_BATCH_INTERVAL_S)
    if batch_rows <= 0 or not math.isclose(
        batch_rows / target_rate_per_s,
        SOURCE_BATCH_INTERVAL_S,
    ):
        raise AssertionError("source rate must form an exact 100 ms microbatch")
    batch_count = int(duration_s / SOURCE_BATCH_INTERVAL_S)
    if not math.isclose(batch_count * SOURCE_BATCH_INTERVAL_S, duration_s):
        raise AssertionError("source duration must form an exact number of microbatches")
    interval_s = SOURCE_BATCH_INTERVAL_S
    receipt_step_s = 1.0 / target_rate_per_s
    semaphore = asyncio.Semaphore(POOL_BUDGET)
    tasks: list[asyncio.Task[list[float]]] = []
    schedule_drift_ms: list[float] = []
    phase_started = time.perf_counter()

    next_batch_no = first_batch_no
    next_seq = first_seq
    chain_hash = previous_hash
    for index in range(batch_count):
        interval_started = phase_started + index * interval_s
        deadline = interval_started + interval_s
        delay = deadline - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        flush_monotonic = time.perf_counter()
        last_received_at = _utcnow()
        first_received_at = last_received_at - timedelta(
            seconds=interval_s - receipt_step_s
        )
        schedule_drift_ms.append(max(0.0, flush_monotonic - deadline) * 1_000)
        batch_hash = _source_batch_hash(next_batch_no, next_seq, batch_rows)
        tasks.append(
            asyncio.create_task(
                _persist_source_batch(
                    sessions,
                    stream,
                    store,
                    semaphore,
                    epoch_id=epoch_id,
                    batch_no=next_batch_no,
                    first_seq=next_seq,
                    batch_rows=batch_rows,
                    previous_hash=chain_hash,
                    first_received_at=first_received_at,
                    last_received_at=last_received_at,
                    first_receipt_monotonic=interval_started,
                    receipt_step_s=receipt_step_s,
                )
            )
        )
        chain_hash = batch_hash
        next_batch_no += 1
        next_seq += batch_rows

    nominal_end = phase_started + duration_s
    remaining = nominal_end - time.perf_counter()
    if remaining > 0:
        await asyncio.sleep(remaining)
    schedule_elapsed_s = time.perf_counter() - phase_started
    batch_durable_ms = await asyncio.gather(*tasks)
    durable_ms = [latency for batch in batch_durable_ms for latency in batch]
    wall_s = time.perf_counter() - phase_started
    event_count = batch_count * batch_rows
    actual_receipt_rate = event_count / schedule_elapsed_s
    durable_rate = event_count / wall_s

    # Pacing is a contract, not a label on an unpaced bulk insert.
    assert schedule_elapsed_s >= duration_s * 0.98, (
        phase,
        schedule_elapsed_s,
        duration_s,
    )
    assert schedule_elapsed_s <= duration_s + max(1.0, duration_s * 0.05), (
        phase,
        schedule_elapsed_s,
        duration_s,
    )
    assert actual_receipt_rate >= target_rate_per_s * 0.97, (
        phase,
        actual_receipt_rate,
        target_rate_per_s,
    )
    assert durable_rate >= target_rate_per_s * 0.97, (
        phase,
        durable_rate,
        target_rate_per_s,
    )

    return (
        {
            "phase": phase,
            "target_rate_per_s": target_rate_per_s,
            "duration_s": duration_s,
            "batch_interval_ms": round(SOURCE_BATCH_INTERVAL_S * 1_000),
            "batch_rows": batch_rows,
            "batches": batch_count,
            "events": event_count,
            "schedule_wall_s": round(schedule_elapsed_s, 3),
            "durable_wall_s": round(wall_s, 3),
            "actual_receipt_rate_per_s": round(actual_receipt_rate, 2),
            "actual_durable_rate_per_s": round(durable_rate, 2),
            "schedule_drift_ms": _percentiles(schedule_drift_ms),
            "event_receipt_to_durable_ms": _percentiles(durable_ms),
        },
        next_batch_no,
        next_seq,
        chain_hash,
    )


async def _book_current_writes(
    sessions: async_sessionmaker,
    stream: MarketStreamRepository,
    store: ArtifactStore,
    *,
    epoch_id: int,
    count: int,
) -> dict[str, Any]:
    durable_ms: list[float] = []
    wall_started = time.perf_counter()
    for index in range(count):
        receipt_started = time.perf_counter()
        received_at = _utcnow()
        token_id = f"perf-yes-{index % BOOK_TOKEN_COUNT:06d}"
        raw_payload = json.dumps(
            {
                "token_id": token_id,
                "best_bid": "0.49",
                "best_ask": "0.51",
                "sequence": index,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        ref = await _put_artifact(store, raw_payload)
        async with UnitOfWork(sessions) as uow:
            artifact_id = await stream.register_artifact(uow.session, ref)
            checkpoint_id = await stream.insert_book_checkpoint(
                uow.session,
                token_id=token_id,
                connection_epoch_id=epoch_id,
                source_kind="ws_delta_aggregate",
                book_hash=ref.sha256,
                best_bid="0.49",
                best_ask="0.51",
                tick_size="0.01",
                min_order_size="1",
                provider_timestamp=None,
                artifact_ref=ref.sha256,
                raw_artifact_id=artifact_id,
                completeness=True,
                validity="VALID",
                received_at=received_at,
            )
            assert await stream.insert_book_levels(
                uow.session,
                checkpoint_id=checkpoint_id,
                received_at=received_at,
                levels=(
                    {"side": "bid", "price": "0.49", "size": "100", "ordinal": 0},
                    {"side": "ask", "price": "0.51", "size": "100", "ordinal": 1},
                ),
            ) == 2
            assert await stream.replace_book_current(
                uow.session,
                token_id=token_id,
                connection_epoch_id=epoch_id,
                checkpoint_id=checkpoint_id,
                checkpoint_received_at=received_at,
                best_bid="0.49",
                best_ask="0.51",
                tick_size="0.01",
                min_order_size="1",
                depth_hash=ref.sha256,
                validity="VALID",
                observed_at=received_at,
            )
        durable_ms.append((time.perf_counter() - receipt_started) * 1_000)
    return {
        "writes": count,
        "tokens": min(count, BOOK_TOKEN_COUNT),
        "wall_s": round(time.perf_counter() - wall_started, 3),
        "receipt_to_current_durable_ms": _percentiles(durable_ms),
    }


async def _counts(
    sessions: async_sessionmaker,
    *,
    epoch_id: int,
    frame_market_count: int,
) -> dict[str, int]:
    async with UnitOfWork(sessions) as uow:
        row = (
            await uow.session.execute(
                text(
                    "SELECT "
                    " (SELECT count(*) FROM trading.pm_markets "
                    "   WHERE gamma_market_id LIKE 'perf-mkt-%') AS markets, "
                    " (SELECT count(*) FROM trading.pm_market_versions) AS market_versions, "
                    " (SELECT count(*) FROM trading.pm_tokens "
                    "   WHERE token_id LIKE 'perf-%') AS tokens, "
                    " (SELECT count(*) FROM trading.pm_token_versions) AS token_versions, "
                    " (SELECT count(*) FROM trading.pm_universe_frames "
                    "   WHERE status='COMPLETE' AND total_markets=:markets "
                    "     AND page_count=4 AND artifact_id IS NOT NULL) AS complete_frames, "
                    " (SELECT count(*) FROM trading.pm_universe_frame_pages p "
                    "   JOIN trading.artifact_objects a ON a.id=p.raw_artifact_id "
                    "   WHERE a.sha256=p.raw_artifact_ref "
                    "     AND a.sha256=p.raw_artifact_hash) AS exact_frame_pages, "
                    " (SELECT count(*) FROM trading.pm_source_event_batches "
                    "   WHERE connection_epoch_id=:epoch) AS source_batches, "
                    " (SELECT count(DISTINCT batch_no) FROM trading.pm_source_event_batches "
                    "   WHERE connection_epoch_id=:epoch) AS distinct_source_batches, "
                    " (SELECT count(*) FROM trading.pm_source_event_index "
                    "   WHERE connection_epoch_id=:epoch) AS source_events, "
                    " (SELECT count(DISTINCT (connection_epoch_id, local_receive_seq)) "
                    "   FROM trading.pm_source_event_index "
                    "   WHERE connection_epoch_id=:epoch) AS distinct_source_events, "
                    " (SELECT count(*) FROM trading.pm_source_event_batches b "
                    "   JOIN trading.artifact_objects a ON a.id=b.raw_artifact_id "
                    "   WHERE b.connection_epoch_id=:epoch AND a.sha256=b.raw_artifact_ref) "
                    "   AS exact_source_artifacts, "
                    " (SELECT count(DISTINCT raw_artifact_id) "
                    "   FROM trading.pm_source_event_batches "
                    "   WHERE connection_epoch_id=:epoch) AS distinct_source_artifacts, "
                    " (SELECT count(*) FROM trading.pm_book_checkpoints "
                    "   WHERE connection_epoch_id=:epoch) AS book_checkpoints, "
                    " (SELECT count(DISTINCT book_hash) FROM trading.pm_book_checkpoints "
                    "   WHERE connection_epoch_id=:epoch) AS distinct_book_checkpoints, "
                    " (SELECT count(*) FROM trading.pm_book_checkpoints c "
                    "   JOIN trading.artifact_objects a ON a.id=c.raw_artifact_id "
                    "   WHERE c.connection_epoch_id=:epoch AND a.sha256=c.artifact_ref "
                    "     AND a.sha256=c.book_hash) AS exact_book_artifacts, "
                    " (SELECT count(*) FROM trading.pm_book_levels l "
                    "   JOIN trading.pm_book_checkpoints c "
                    "     ON c.id=l.checkpoint_id AND c.received_at=l.received_at "
                    "   WHERE c.connection_epoch_id=:epoch) AS book_levels, "
                    " (SELECT count(*) FROM trading.pm_book_current "
                    "   WHERE connection_epoch_id=:epoch AND validity='VALID') AS book_current"
                ),
                {"epoch": epoch_id, "markets": frame_market_count},
            )
        ).mappings().one()
    return {key: int(value) for key, value in row.items()}


def _git_metadata() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(
            args,
            cwd=SERVE_DIR,
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()

    return {
        "commit": run("git", "rev-parse", "HEAD"),
        "dirty": bool(run("git", "status", "--porcelain")),
    }


def _validate(
    workload: Workload,
    *,
    sustained: dict[str, Any],
    burst: dict[str, Any],
    book: dict[str, Any],
    counts: dict[str, int],
    pool_peak: int,
) -> list[dict[str, Any]]:
    expected_events = (
        1_000 * workload.sustained_duration_s + 5_000 * workload.burst_duration_s
    )
    expected_batches = int(
        (workload.sustained_duration_s + workload.burst_duration_s)
        / SOURCE_BATCH_INTERVAL_S
    )
    expected_current = min(workload.book_writes, BOOK_TOKEN_COUNT)
    checks: list[dict[str, Any]] = []

    def exact(name: str, actual: Any, expected: Any) -> None:
        checks.append(
            {
                "name": name,
                "passed": actual == expected,
                "actual": actual,
                "limit_or_expected": expected,
            }
        )

    def at_most(name: str, actual: float | int, limit: float | int) -> None:
        checks.append(
            {
                "name": name,
                "passed": actual <= limit,
                "actual": actual,
                "limit_or_expected": limit,
            }
        )

    exact("frame_markets", counts["markets"], workload.markets)
    exact("frame_market_versions", counts["market_versions"], workload.markets)
    exact("frame_tokens", counts["tokens"], workload.markets * 2)
    exact("frame_token_versions", counts["token_versions"], workload.markets * 2)
    exact("frame_complete", counts["complete_frames"], 1)
    exact("frame_pages_exact", counts["exact_frame_pages"], 4)
    exact("source_batches_zero_loss", counts["source_batches"], expected_batches)
    exact(
        "source_batches_no_duplicates",
        counts["distinct_source_batches"],
        expected_batches,
    )
    exact("source_events_zero_loss", counts["source_events"], expected_events)
    exact(
        "source_events_no_duplicates",
        counts["distinct_source_events"],
        expected_events,
    )
    exact("source_artifact_exact", counts["exact_source_artifacts"], expected_batches)
    exact(
        "source_artifacts_no_alias",
        counts["distinct_source_artifacts"],
        expected_batches,
    )
    at_most(
        "sustained_p99_ms",
        sustained["event_receipt_to_durable_ms"]["p99"],
        SOURCE_P99_LIMIT_MS,
    )
    at_most(
        "burst_p99_ms",
        burst["event_receipt_to_durable_ms"]["p99"],
        SOURCE_P99_LIMIT_MS,
    )
    exact("book_checkpoints_zero_loss", counts["book_checkpoints"], workload.book_writes)
    exact(
        "book_checkpoints_no_duplicates",
        counts["distinct_book_checkpoints"],
        workload.book_writes,
    )
    exact("book_artifact_exact", counts["exact_book_artifacts"], workload.book_writes)
    exact("book_levels_zero_loss", counts["book_levels"], workload.book_writes * 2)
    exact("book_current_exact", counts["book_current"], expected_current)
    at_most(
        "book_current_p99_ms",
        book["receipt_to_current_durable_ms"]["p99"],
        BOOK_P99_LIMIT_MS,
    )
    at_most("pool_budget", pool_peak, POOL_BUDGET)
    return checks


async def _run(workload: Workload, output_path: Path) -> dict[str, Any]:
    dbname = f"{TEMP_PREFIX}{uuid.uuid4().hex[:12]}"
    admin_url = make_url(ADMIN_URL)
    created = False
    async_engine = None
    artifact_store = None
    results: dict[str, Any] = {}

    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{dbname}"'))
            created = True
        finally:
            admin.dispose()

        database_url = admin_url.set(database=dbname).render_as_string(hide_password=False)
        from alembic import command
        from alembic.config import Config

        migration_engine = create_engine(database_url)
        try:
            with migration_engine.connect() as connection:
                config = Config()
                config.set_main_option("script_location", str(SERVE_DIR / "alembic"))
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        finally:
            migration_engine.dispose()

        async_url = make_url(database_url).set(drivername="postgresql+asyncpg")
        async_engine = create_async_engine(
            async_url,
            pool_size=POOL_SIZE,
            max_overflow=MAX_OVERFLOW,
            pool_timeout=POOL_TIMEOUT_S,
            pool_recycle=1_800,
            pool_pre_ping=True,
            connect_args={
                "server_settings": {
                    "application_name": "pollymarket_v2_wp01b_perf",
                    "statement_timeout": "30000",
                }
            },
        )
        probe = PoolProbe()
        event.listen(async_engine.sync_engine, "checkout", probe.checkout)
        event.listen(async_engine.sync_engine, "checkin", probe.checkin)
        sessions = async_sessionmaker(async_engine, expire_on_commit=False)
        await _warm_pool(async_engine, POOL_BUDGET)

        with tempfile.TemporaryDirectory(prefix="pm-v2-perf-artifacts-") as artifact_root:
            artifact_store = ArtifactStore(LocalArtifactDriver(artifact_root))
            market = MarketRepository()
            stream = MarketStreamRepository()

            async with UnitOfWork(sessions) as uow:
                release_id = await create_test_release_manifest(
                    uow.session,
                    key=f"wp01b-perf-{dbname}",
                )

            frame = await _frame_ingest(
                sessions,
                market,
                stream,
                artifact_store,
                workload.markets,
            )
            epoch_id = await _create_live_epoch(
                sessions,
                stream,
                release_id=release_id,
                shard_key=f"perf-load-{dbname}",
            )

            # Prepare source statements and artifact directories outside measured phases.
            warm_epoch_id = await _create_live_epoch(
                sessions,
                stream,
                release_id=release_id,
                shard_key=f"perf-warm-{dbname}",
            )
            await _persist_source_batch(
                sessions,
                stream,
                artifact_store,
                asyncio.Semaphore(1),
                epoch_id=warm_epoch_id,
                batch_no=0,
                first_seq=0,
                batch_rows=10,
                previous_hash=None,
                first_received_at=_utcnow(),
                last_received_at=_utcnow(),
                first_receipt_monotonic=time.perf_counter(),
                receipt_step_s=0.0,
            )
            probe.reset_peak()

            sustained, next_batch, next_seq, chain_hash = await _paced_source_phase(
                sessions,
                stream,
                artifact_store,
                epoch_id=epoch_id,
                phase="sustained",
                target_rate_per_s=1_000,
                duration_s=workload.sustained_duration_s,
                first_batch_no=0,
                first_seq=0,
                previous_hash=None,
            )
            burst, _, _, _ = await _paced_source_phase(
                sessions,
                stream,
                artifact_store,
                epoch_id=epoch_id,
                phase="burst",
                target_rate_per_s=5_000,
                duration_s=workload.burst_duration_s,
                first_batch_no=next_batch,
                first_seq=next_seq,
                previous_hash=chain_hash,
            )
            book = await _book_current_writes(
                sessions,
                stream,
                artifact_store,
                epoch_id=epoch_id,
                count=workload.book_writes,
            )
            counts = await _counts(
                sessions,
                epoch_id=epoch_id,
                frame_market_count=workload.markets,
            )
            pool_current, pool_peak = probe.snapshot()
            checks = _validate(
                workload,
                sustained=sustained,
                burst=burst,
                book=book,
                counts=counts,
                pool_peak=pool_peak,
            )

            async with async_engine.connect() as connection:
                postgres_version = str(
                    (await connection.execute(text("SELECT version()"))).scalar_one()
                )

            results = {
                "status": "PASS" if all(item["passed"] for item in checks) else "FAIL",
                "mode": workload.mode,
                "acceptance_qualified": workload.acceptance_qualified,
                "frame_ingest": frame,
                "source_sustained": sustained,
                "source_burst": burst,
                "book_current": book,
                "counts": counts,
                "pool": {
                    "pool_size": POOL_SIZE,
                    "max_overflow": MAX_OVERFLOW,
                    "budget": POOL_BUDGET,
                    "observed_peak_checkout": pool_peak,
                    "checked_out_after_workload": pool_current,
                    "timeout_s": POOL_TIMEOUT_S,
                },
                "thresholds_ms": {
                    "source_receipt_to_durable_p99": SOURCE_P99_LIMIT_MS,
                    "book_receipt_to_current_durable_p99": BOOK_P99_LIMIT_MS,
                },
                "assertions": checks,
                "seed": {
                    "generator": "deterministic-v1",
                    "markets": workload.markets,
                    "tokens": workload.markets * 2,
                    "source_events": (
                        1_000 * workload.sustained_duration_s
                        + 5_000 * workload.burst_duration_s
                    ),
                    "book_writes": workload.book_writes,
                },
                "environment": {
                    "node": platform.node(),
                    "platform": platform.platform(),
                    "machine": platform.machine(),
                    "cpu_count": os.cpu_count(),
                    "python": platform.python_version(),
                    "postgresql": postgres_version,
                },
                "git": _git_metadata(),
                "completed_at": _utcnow().isoformat(),
            }
            output_path.write_text(
                json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    finally:
        if artifact_store is not None:
            artifact_store.aclose()
        if async_engine is not None:
            await async_engine.dispose()
        if created:
            admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
            try:
                with admin.connect() as connection:
                    connection.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname=:name AND pid<>pg_backend_pid()"
                        ),
                        {"name": dbname},
                    )
                    connection.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
            finally:
                admin.dispose()
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="run a shorter non-acceptance check at the same rates and SLOs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(OUTPUT_DEFAULT),
        help=f"result JSON path (default: {OUTPUT_DEFAULT})",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    workload = QUICK if args.quick else FULL
    results = asyncio.run(_run(workload, args.output))
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    if results.get("status") != "PASS":
        failed = [item["name"] for item in results.get("assertions", []) if not item["passed"]]
        raise SystemExit(f"WP-01B performance smoke failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
