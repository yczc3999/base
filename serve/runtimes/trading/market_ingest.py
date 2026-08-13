"""Polymarket public market ingest runtime (WP-01B).

The runtime has two fail-closed pipelines:

* ``UniverseIngestor`` owns a leased/fenced Gamma frame.  Every keyset page and
  request receipt is durable before the cursor advances; a new process resumes
  from the last durable cursor and only a four-chain COMPLETE frame may publish
  market current state.
* ``BookWsIngestor`` owns one Market WS epoch.  Raw frames/receipts are durable
  before derived checkpoints.  All subscribed tokens must receive a full book
  before the epoch becomes LIVE; disconnect atomically makes its current books
  STALE and a reconnect always uses a new epoch.

Provider calls are outside database transactions.  Business facts, their source
index and outbox notifications are committed in one UnitOfWork.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable

from app.db.uow import UnitOfWork
from app.logics.trading.market_data import BookState, FreshnessPolicy, apply_delta, snapshot_book
from app.logics.trading.universe import UniverseLogic, UniversePolicy
from app.outbox.contracts import create_envelope
from app.outbox.repository import OutboxRepository
from app.repositories.trading.market import MarketRepository
from app.repositories.trading.market_stream import MarketStreamRepository
from app.schemas.polymarket.common import PolymarketError, RequestReceipt
from app.schemas.polymarket.gamma import (
    GammaEvent,
    GammaMarket,
    parse_gamma_keyset_page,
)
from app.schemas.polymarket.market_ws import (
    MarketWsBook,
    MarketWsFrameBase,
    MarketWsPriceChange,
    MarketWsTickSizeChange,
)
from app.services.polymarket.base import parse_json_bytes
from app.services.polymarket.gamma_driver import GammaDriver
from app.services.polymarket.market_ws_driver import MarketWsDriver

logger = logging.getLogger(__name__)

ARTIFACT_MIME_JSON = "application/json"
OUTBOX_TOPIC_UNIVERSE_FRAME = "universe.frame"
OUTBOX_TOPIC_UNIVERSE_REFRESH = "universe.refresh"
OUTBOX_TOPIC_BOOK = "market.book"
OUTBOX_TOPIC_MARKET_CONFIG_REFRESH = "market.config.refresh"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _provider_timestamp(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _receipt_payload(receipts: Iterable[RequestReceipt]) -> list[dict[str, Any]]:
    return [asdict(receipt) for receipt in receipts]


def _receipt_event(
    receipt: RequestReceipt,
    *,
    source: str,
    connection_epoch_id: int,
) -> dict[str, Any]:
    payload_hash = receipt.response_hash or receipt.request_hash
    receipt_hash = _sha256(_json_bytes(asdict(receipt)))
    return {
        "_claim_key": f"receipt:{receipt.attempt_id}",
        "_owner_hash": receipt_hash,
        "source": source,
        "kind": "request_attempt",
        "connection_epoch_id": connection_epoch_id,
        "local_receive_seq": None,
        "payload_hash": payload_hash,
        "parse_status": "invalid" if receipt.error_code else "parsed",
        "parse_reason": receipt.error_code,
        "attempt_id": receipt.attempt_id,
        "endpoint": receipt.endpoint,
        "method": receipt.method,
        "http_status": receipt.http_status,
        "latency_ms": receipt.latency_ms,
        "error_code": receipt.error_code,
        "request_hash": receipt.request_hash,
        "response_hash": receipt.response_hash,
        "retry_count": receipt.retry_count,
    }


async def _write_source_batch(
    *,
    session: Any,
    stream: MarketStreamRepository,
    connection_epoch_id: int,
    received_at: datetime,
    raw_artifact_id: int,
    raw_artifact_ref: str,
    raw_hash: str,
    rows: list[dict[str, Any]],
) -> int:
    """Claim globally-idempotent source rows and append one immutable batch."""
    claimed_rows: list[dict[str, Any]] = []
    for row in rows:
        clean = dict(row)
        key = str(clean.pop("_claim_key"))
        owner_hash = str(clean.pop("_owner_hash"))
        if await stream.claim_source_event(session, key=key, owner_hash=owner_hash):
            claimed_rows.append(clean)
    if not claimed_rows:
        return 0

    latest = await stream.latest_batch_for_epoch(session, connection_epoch_id)
    batch_no = 0 if latest is None else int(latest["batch_no"]) + 1
    fallback_seq = 1 if latest is None else int(latest["last_receive_seq"]) + 1
    explicit = [
        int(row["local_receive_seq"])
        for row in claimed_rows
        if row.get("local_receive_seq") is not None
    ]
    first_seq = min(explicit) if explicit else fallback_seq
    last_seq = max(explicit) if explicit else fallback_seq
    batch_id = await stream.insert_source_batch(
        session,
        connection_epoch_id=connection_epoch_id,
        batch_no=batch_no,
        first_receive_seq=first_seq,
        last_receive_seq=last_seq,
        first_received_at=received_at,
        last_received_at=received_at,
        event_count=len(claimed_rows),
        batch_hash=raw_hash,
        prev_batch_hash=None if latest is None else latest["batch_hash"],
        raw_artifact_ref=raw_artifact_ref,
        raw_artifact_id=raw_artifact_id,
        received_at=received_at,
    )
    await stream.insert_source_events(
        session,
        batch_id=batch_id,
        received_at=received_at,
        events=claimed_rows,
    )
    return len(claimed_rows)


@dataclass(frozen=True)
class FrameRunResult:
    frame_id: int
    status: str
    pages: int
    total_events: int
    total_markets: int
    error_reason: str | None
    outbox_event_id: str | None
    # frame 完整归属（成功路径）：供 pipeline 构造 HydratedUniverseFrameInput 做 cohort 登记。
    content_hash: str | None
    artifact_id: int | None
    artifact_ref: str | None
    markets: tuple  # AppliedMarket 元组（db id + 规范化 content）


@dataclass(frozen=True)
class _GammaEndpoint:
    name: str
    kind: str
    closed: bool
    limit: int


class UniverseIngestor:
    """Crash-resumable, leased Gamma universe scanner."""

    def __init__(
        self,
        *,
        gamma: GammaDriver,
        artifacts: Any,
        uow_factory: Callable[[], UnitOfWork],
        market_repo: MarketRepository,
        stream_repo: MarketStreamRepository,
        universe: UniverseLogic,
        outbox_repo: OutboxRepository,
        config_release_id: int,
        policy: UniversePolicy | None = None,
        owner: str | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if isinstance(config_release_id, bool) or config_release_id <= 0:
            raise ValueError("config_release_id_invalid")
        self._gamma = gamma
        self._artifacts = artifacts
        self._uow_factory = uow_factory
        self._market = market_repo
        self._stream = stream_repo
        self._universe = universe
        self._outbox = outbox_repo
        self._config_release_id = config_release_id
        self._policy = policy or universe.policy
        self._owner = owner or f"universe-{uuid.uuid4().hex}"
        self._clock = clock

    @property
    def _endpoints(self) -> tuple[_GammaEndpoint, ...]:
        return (
            _GammaEndpoint("events_open", "events", False, self._policy.event_page_limit),
            _GammaEndpoint("events_closed", "events", True, self._policy.event_page_limit),
            _GammaEndpoint("markets_open", "markets", False, self._policy.market_page_limit),
            _GammaEndpoint("markets_closed", "markets", True, self._policy.market_page_limit),
        )

    async def run_once(self, *, received_at: datetime | None = None) -> FrameRunResult:
        started_at = received_at or self._clock()
        lease_expires_at = started_at + timedelta(seconds=self._policy.frame_lease_s)
        acquire_uow = self._uow_factory()
        async with acquire_uow:
            lease = await self._market.acquire_frame(
                acquire_uow.session,
                owner=self._owner,
                started_at=started_at,
                lease_expires_at=lease_expires_at,
            )
        frame_id = int(lease["id"])
        fencing_token = int(lease["fencing_token"])
        epoch_id = await self._ensure_gamma_epoch(frame_id, started_at)

        try:
            for endpoint in self._endpoints:
                await self._resume_endpoint(
                    frame_id=frame_id,
                    fencing_token=fencing_token,
                    epoch_id=epoch_id,
                    endpoint=endpoint,
                )

            pages, page_refs = await self._load_pages(frame_id)
            (
                events,
                markets,
                event_artifact_refs,
                market_artifact_refs,
                market_event_ids,
            ) = self._reconstruct_frame(pages, page_refs)
            manifest_bytes = self._frame_manifest_bytes(pages)
            manifest_ref = self._artifacts.put_bytes(manifest_bytes, ARTIFACT_MIME_JSON)
            content_hash = _sha256(manifest_bytes)
            completed_at = self._clock()
            total_events = sum(
                int(page["item_count"])
                for page in pages
                if page["endpoint"].startswith("events_")
            )
            total_markets = sum(
                int(page["item_count"])
                for page in pages
                if page["endpoint"].startswith("markets_")
            )

            outbox_event_id: str | None = None
            commit_uow = self._uow_factory()
            async with commit_uow:
                if not await self._market.renew_frame_lease(
                    commit_uow.session,
                    frame_id=frame_id,
                    owner=self._owner,
                    fencing_token=fencing_token,
                    now=completed_at,
                    lease_expires_at=completed_at
                    + timedelta(seconds=self._policy.frame_lease_s),
                ):
                    raise RuntimeError("universe_frame_fencing_conflict")
                artifact_id = await self._stream.register_artifact(
                    commit_uow.session, manifest_ref
                )
                status = await self._universe.finalize_frame(
                    commit_uow,
                    frame_id=frame_id,
                    events_terminal=True,
                    markets_terminal=True,
                    total_events=total_events,
                    total_markets=total_markets,
                    content_hash=content_hash,
                    artifact_id=artifact_id,
                    artifact_ref=manifest_ref.sha256,
                    owner=self._owner,
                    fencing_token=fencing_token,
                    completed_at=completed_at,
                )
                diff_result = await self._universe.apply_frame_diff(
                    commit_uow,
                    events=events,
                    markets=markets,
                    observed_at=completed_at,
                    received_at=completed_at,
                    raw_artifact_ref=manifest_ref.sha256,
                    event_artifact_refs=event_artifact_refs,
                    market_artifact_refs=market_artifact_refs,
                    market_event_ids=market_event_ids,
                )
                env = create_envelope(
                    topic=OUTBOX_TOPIC_UNIVERSE_FRAME,
                    schema_version=1,
                    aggregate_type="universe_frame",
                    aggregate_id=str(frame_id),
                    idempotency_key=f"universe-frame-{frame_id}",
                    release_manifest_id=self._config_release_id,
                    priority=96,
                    payload={
                        "frame_id": frame_id,
                        "status": status,
                        "total_events": total_events,
                        "total_markets": total_markets,
                        "content_hash": content_hash,
                    },
                )
                await self._outbox.enqueue(commit_uow.session, env)
                outbox_event_id = env.event_id
                epoch = await self._stream.get_epoch(commit_uow.session, epoch_id)
                if epoch and epoch["status"] == "SYNCING":
                    await self._stream.transition_epoch(
                        commit_uow.session, epoch_id, "SYNCING", "LIVE", at=completed_at
                    )
                epoch = await self._stream.get_epoch(commit_uow.session, epoch_id)
                if epoch and epoch["status"] == "LIVE":
                    await self._stream.transition_epoch(
                        commit_uow.session,
                        epoch_id,
                        "LIVE",
                        "CLOSED",
                        at=completed_at,
                        closed_reason="frame_complete",
                    )

            return FrameRunResult(
                frame_id=frame_id,
                status=status,
                pages=len(pages),
                total_events=total_events,
                total_markets=total_markets,
                error_reason=None,
                outbox_event_id=outbox_event_id,
                content_hash=content_hash,
                artifact_id=artifact_id,
                artifact_ref=manifest_ref.sha256,
                markets=tuple(diff_result.markets),
            )
        except asyncio.CancelledError:
            # Leave the OPEN frame and durable cursor pages for lease-based takeover.
            raise
        except Exception as exc:
            receipts = tuple(getattr(exc, "receipts", ()) or ())
            if receipts:
                await self._persist_receipts_only(epoch_id, receipts, self._clock())
            reason = self._failure_reason(exc)
            logger.warning("universe frame %s failed: %s", frame_id, reason)
            failed_at = self._clock()
            pages_uow = self._uow_factory()
            async with pages_uow:
                pages = await self._market.list_pages(pages_uow.session, frame_id)
            total_events = sum(
                int(page["item_count"])
                for page in pages
                if page["endpoint"].startswith("events_")
            )
            total_markets = sum(
                int(page["item_count"])
                for page in pages
                if page["endpoint"].startswith("markets_")
            )
            fail_uow = self._uow_factory()
            async with fail_uow:
                ok = await self._market.finalize_frame(
                    fail_uow.session,
                    frame_id=frame_id,
                    status="FAILED",
                    total_events=total_events,
                    total_markets=total_markets,
                    content_hash=None,
                    artifact_id=None,
                    artifact_ref=None,
                    error_reason=reason,
                    owner=self._owner,
                    fencing_token=fencing_token,
                    completed_at=failed_at,
                )
                if not ok:
                    raise RuntimeError("universe_frame_fencing_conflict") from exc
                epoch = await self._stream.get_epoch(fail_uow.session, epoch_id)
                if epoch and epoch["status"] in {"CONNECTING", "SYNCING", "LIVE", "STALE"}:
                    await self._stream.transition_epoch(
                        fail_uow.session,
                        epoch_id,
                        epoch["status"],
                        "CLOSED",
                        at=failed_at,
                        closed_reason=reason,
                    )
            return FrameRunResult(
                frame_id=frame_id,
                status="FAILED",
                pages=len(pages),
                total_events=total_events,
                total_markets=total_markets,
                error_reason=reason,
                outbox_event_id=None,
                content_hash=None,
                artifact_id=None,
                artifact_ref=None,
                markets=(),
            )

    async def sync_tag_catalog(self) -> dict[str, Any]:
        """从 ``GET /tags`` 全量同步官方目录。失败不猜名字，调用方决定是否继续 sense。"""
        observed_at = self._clock()
        upserted = 0
        pages = 0
        offset = 0
        limit = self._policy.tag_page_limit
        last_count = 0
        while pages < self._policy.tag_catalog_max_pages:
            result = await self._gamma.list_tags(limit=limit, offset=offset)
            page = result.typed
            last_count = len(page.items)
            if last_count == 0:
                break
            uow = self._uow_factory()
            async with uow:
                for tag in page.items:
                    written = await self._universe.persist_tag(
                        uow,
                        tag,
                        observed_at=observed_at,
                        seen_in_catalog=True,
                    )
                    if written is not None:
                        upserted += 1
            pages += 1
            if last_count < limit:
                break
            offset += limit
        truncated = (
            pages >= self._policy.tag_catalog_max_pages and last_count == limit
        )
        return {
            "stage": "tags",
            "ok": True,
            "upserted": upserted,
            "pages": pages,
            "truncated": truncated,
        }

    async def _ensure_gamma_epoch(self, frame_id: int, at: datetime) -> int:
        shard = f"universe-frame-{frame_id}"
        uow = self._uow_factory()
        async with uow:
            active = await self._stream.active_epoch_for_shard(uow.session, shard, "gamma")
            if active is None:
                epoch_id = await self._stream.create_epoch(
                    uow.session,
                    shard_key=shard,
                    provider="gamma",
                    started_at=at,
                    config_release_id=self._config_release_id,
                    owner=self._owner,
                )
                await self._stream.transition_epoch(
                    uow.session, epoch_id, "CONNECTING", "SYNCING", at=at
                )
                return epoch_id
            epoch = await self._stream.get_epoch(uow.session, int(active["id"]))
            if epoch is None or epoch["config_release_id"] != self._config_release_id:
                raise RuntimeError("universe_epoch_release_conflict")
            if epoch["status"] == "CONNECTING":
                await self._stream.transition_epoch(
                    uow.session, int(epoch["id"]), "CONNECTING", "SYNCING", at=at
                )
            return int(epoch["id"])

    async def _resume_endpoint(
        self,
        *,
        frame_id: int,
        fencing_token: int,
        epoch_id: int,
        endpoint: _GammaEndpoint,
    ) -> None:
        state_uow = self._uow_factory()
        async with state_uow:
            pages = await self._market.list_pages(state_uow.session, frame_id)
        endpoint_pages = [p for p in pages if p["endpoint"] == endpoint.name]
        endpoint_pages.sort(key=lambda page: int(page["page_no"]))
        if endpoint_pages and endpoint_pages[-1]["cursor_output"] is None:
            return
        if len(endpoint_pages) >= self._policy.max_pages_per_endpoint:
            raise RuntimeError("frame_page_overflow")

        cursor = endpoint_pages[-1]["cursor_output"] if endpoint_pages else None
        seen_cursors = {
            value
            for page in endpoint_pages
            for value in (page["cursor_input"], page["cursor_output"])
            if value is not None
        }
        next_page_no = 0 if not pages else max(int(page["page_no"]) for page in pages) + 1
        while True:
            try:
                if endpoint.kind == "events":
                    result = await self._gamma.keyset_events(
                        cursor=cursor, limit=endpoint.limit, closed=endpoint.closed
                    )
                else:
                    result = await self._gamma.keyset_markets(
                        cursor=cursor, limit=endpoint.limit, closed=endpoint.closed
                    )
            except Exception:
                raise
            next_cursor = result.typed.next_cursor
            if next_cursor is not None and next_cursor in seen_cursors:
                raise RuntimeError("frame_cursor_chain_break")
            page_received_at = self._clock()
            await self._persist_gamma_page(
                frame_id=frame_id,
                fencing_token=fencing_token,
                epoch_id=epoch_id,
                page_no=next_page_no,
                endpoint=endpoint.name,
                cursor_input=cursor,
                cursor_output=next_cursor,
                item_count=len(result.typed.items),
                raw=result.raw,
                receipts=tuple(result.receipts),
                received_at=page_received_at,
            )
            next_page_no += 1
            if next_cursor is None:
                return
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            endpoint_pages.append({"page_no": next_page_no, "cursor_output": next_cursor})
            if len(endpoint_pages) >= self._policy.max_pages_per_endpoint:
                raise RuntimeError("frame_page_overflow")

    async def _persist_gamma_page(
        self,
        *,
        frame_id: int,
        fencing_token: int,
        epoch_id: int,
        page_no: int,
        endpoint: str,
        cursor_input: str | None,
        cursor_output: str | None,
        item_count: int,
        raw: bytes,
        receipts: tuple[RequestReceipt, ...],
        received_at: datetime,
    ) -> None:
        ref = self._artifacts.put_bytes(raw, ARTIFACT_MIME_JSON)
        uow = self._uow_factory()
        async with uow:
            if not await self._market.renew_frame_lease(
                uow.session,
                frame_id=frame_id,
                owner=self._owner,
                fencing_token=fencing_token,
                now=received_at,
                lease_expires_at=received_at
                + timedelta(seconds=self._policy.frame_lease_s),
            ):
                raise RuntimeError("universe_frame_fencing_conflict")
            artifact_id = await self._stream.register_artifact(uow.session, ref)
            await self._universe.record_page(
                uow,
                frame_id=frame_id,
                endpoint=endpoint,
                page_no=page_no,
                cursor_input=cursor_input,
                cursor_output=cursor_output,
                item_count=item_count,
                raw_artifact_id=artifact_id,
                raw_artifact_ref=ref.sha256,
                raw_artifact_hash=_sha256(raw),
                received_at=received_at,
                owner=self._owner,
                fencing_token=fencing_token,
            )
            receipt_rows = [
                _receipt_event(receipt, source="gamma", connection_epoch_id=epoch_id)
                for receipt in receipts
            ]
            await _write_source_batch(
                session=uow.session,
                stream=self._stream,
                connection_epoch_id=epoch_id,
                received_at=received_at,
                raw_artifact_id=artifact_id,
                raw_artifact_ref=ref.sha256,
                raw_hash=_sha256(raw),
                rows=receipt_rows,
            )

    async def _persist_receipts_only(
        self,
        epoch_id: int,
        receipts: tuple[RequestReceipt, ...],
        received_at: datetime,
    ) -> None:
        raw = _json_bytes({"receipts": _receipt_payload(receipts)})
        ref = self._artifacts.put_bytes(raw, ARTIFACT_MIME_JSON)
        uow = self._uow_factory()
        async with uow:
            artifact_id = await self._stream.register_artifact(uow.session, ref)
            rows = [
                _receipt_event(receipt, source="gamma", connection_epoch_id=epoch_id)
                for receipt in receipts
            ]
            await _write_source_batch(
                session=uow.session,
                stream=self._stream,
                connection_epoch_id=epoch_id,
                received_at=received_at,
                raw_artifact_id=artifact_id,
                raw_artifact_ref=ref.sha256,
                raw_hash=_sha256(raw),
                rows=rows,
            )

    async def _load_pages(
        self, frame_id: int
    ) -> tuple[list[dict[str, Any]], dict[int, Any]]:
        uow = self._uow_factory()
        async with uow:
            pages = await self._market.list_pages(uow.session, frame_id)
            refs = {
                int(page["id"]): await self._stream.load_artifact_ref(
                    uow.session, int(page["raw_artifact_id"])
                )
                for page in pages
            }
        return pages, refs

    def _reconstruct_frame(
        self,
        pages: list[dict[str, Any]],
        refs: dict[int, Any],
    ) -> tuple[
        list[GammaEvent],
        list[GammaMarket],
        dict[str, str],
        dict[str, str],
        dict[str, str],
    ]:
        events_by_id: dict[str, GammaEvent] = {}
        markets_by_id: dict[str, GammaMarket] = {}
        event_refs: dict[str, str] = {}
        market_refs: dict[str, str] = {}
        market_event_ids: dict[str, str] = {}
        for page in sorted(pages, key=lambda row: int(row["page_no"])):
            raw = self._artifacts.get_bytes(refs[int(page["id"])])
            payload = parse_json_bytes(raw)
            kind = "events" if page["endpoint"].startswith("events_") else "markets"
            items, cursor = parse_gamma_keyset_page(payload, items_key=kind)
            if cursor != page["cursor_output"]:
                raise RuntimeError("frame_artifact_cursor_mismatch")
            if len(items) != int(page["item_count"]):
                raise RuntimeError("frame_artifact_count_mismatch")
            if kind == "events":
                for item in items:
                    event = GammaEvent.model_validate(item)
                    events_by_id[event.id] = event
                    event_refs[event.id] = page["raw_artifact_ref"]
                    for embedded in event.markets:
                        previous = market_event_ids.get(embedded.id)
                        if previous is not None and previous != event.id:
                            raise RuntimeError("frame_market_event_mapping_conflict")
                        market_event_ids[embedded.id] = event.id
                        markets_by_id.setdefault(embedded.id, embedded)
                        market_refs.setdefault(embedded.id, page["raw_artifact_ref"])
            else:
                for item in items:
                    market = GammaMarket.model_validate(item)
                    markets_by_id[market.id] = market
                    market_refs[market.id] = page["raw_artifact_ref"]
        return (
            list(events_by_id.values()),
            list(markets_by_id.values()),
            event_refs,
            market_refs,
            market_event_ids,
        )

    @staticmethod
    def _frame_manifest_bytes(pages: list[dict[str, Any]]) -> bytes:
        entries = [
            {
                "endpoint": page["endpoint"],
                "page_no": int(page["page_no"]),
                "cursor_input": page["cursor_input"],
                "cursor_output": page["cursor_output"],
                "item_count": int(page["item_count"]),
                "raw_sha256": page["raw_artifact_hash"],
            }
            for page in sorted(pages, key=lambda row: int(row["page_no"]))
        ]
        return _json_bytes(entries)

    @staticmethod
    def _failure_reason(exc: Exception) -> str:
        if isinstance(exc, PolymarketError):
            return exc.reason_code
        message = str(exc)
        known = {
            "frame_cursor_not_terminated",
            "frame_cursor_chain_break",
            "frame_page_overflow",
            "frame_artifact_cursor_mismatch",
            "frame_artifact_count_mismatch",
            "frame_market_event_mapping_conflict",
        }
        return message if message in known else "universe_pass_failed"


class BookWsIngestor:
    """One-shard Market WS epoch with durable raw evidence and a full-book barrier."""

    def __init__(
        self,
        *,
        ws_driver_factory: Callable[[], MarketWsDriver],
        artifacts: Any,
        uow_factory: Callable[[], UnitOfWork],
        stream_repo: MarketStreamRepository,
        outbox_repo: OutboxRepository,
        freshness_policy: FreshnessPolicy,
        shard_key: str,
        config_release_id: int,
    ) -> None:
        if isinstance(config_release_id, bool) or config_release_id <= 0:
            raise ValueError("config_release_id_invalid")
        self._ws_factory = ws_driver_factory
        self._artifacts = artifacts
        self._uow_factory = uow_factory
        self._stream = stream_repo
        self._outbox = outbox_repo
        self._freshness = freshness_policy
        self._shard_key = shard_key
        self._config_release_id = config_release_id

    async def run_epoch(self, *, started_at: datetime | None = None) -> int:
        now = started_at or _utc_now()
        uow = self._uow_factory()
        async with uow:
            epoch_id = await self._stream.create_epoch(
                uow.session,
                shard_key=self._shard_key,
                provider="market_ws",
                started_at=now,
                config_release_id=self._config_release_id,
            )
        await self._transition(epoch_id, "CONNECTING", "SYNCING", now)

        driver = self._ws_factory()
        books: dict[str, BookState] = {}
        initial_seen: set[str] = set()
        persisted_receipts: set[str] = set()
        try:
            await driver.connect()
            expected_tokens = frozenset(driver.assets_ids)
            await self._persist_new_driver_receipts(
                epoch_id, driver, persisted_receipts, _utc_now()
            )
            while True:
                msg = await driver.next_frame()
                receipts = self._new_receipts(driver, persisted_receipts)
                frame = msg.frame
                updates: list[tuple[str, BookState, bool]] = []
                if isinstance(frame, MarketWsBook) and frame.asset_id in expected_tokens:
                    assert frame.asset_id is not None  # raw parser requires it
                    state = snapshot_book(
                        token_id=frame.asset_id,
                        bids=[(level.price, level.size) for level in frame.bids],
                        asks=[(level.price, level.size) for level in frame.asks],
                        tick_size=None,
                        min_order_size=None,
                        epoch_id=epoch_id,
                        observed_at=msg.received_at,
                    )
                    is_initial = frame.asset_id not in initial_seen
                    initial_seen.add(frame.asset_id)
                    books[frame.asset_id] = state
                    updates.append((frame.asset_id, state, is_initial))
                elif isinstance(frame, MarketWsPriceChange):
                    grouped: dict[str, list[Any]] = {}
                    for change in frame.price_changes:
                        grouped.setdefault(change.asset_id, []).append(change)
                    for token_id, changes in grouped.items():
                        state = books.get(token_id)
                        if state is None or token_id not in initial_seen:
                            continue
                        updated = apply_delta(
                            state,
                            changes=[
                                (change.side, change.price, change.size) for change in changes
                            ],
                            epoch_id=epoch_id,
                            received_at=msg.received_at,
                        )
                        books[token_id] = updated
                        updates.append((token_id, updated, False))
                elif isinstance(frame, MarketWsTickSizeChange):
                    state = books.get(frame.asset_id or "")
                    if state is not None and frame.new_tick_size is not None:
                        updated = replace(state, tick_size=frame.new_tick_size)
                        books[state.token_id] = updated
                        updates.append((state.token_id, updated, False))

                await self._commit_ws_message(
                    epoch_id=epoch_id,
                    msg=msg,
                    updates=updates,
                    receipts=receipts,
                    publish_epoch=initial_seen >= expected_tokens,
                )
                persisted_receipts.update(receipt.attempt_id for receipt in receipts)
        except asyncio.CancelledError:
            await self._mark_stale(epoch_id)
            raise
        except Exception as exc:
            receipts = tuple(getattr(exc, "receipts", ()) or ())
            new_receipts = tuple(
                receipt for receipt in receipts if receipt.attempt_id not in persisted_receipts
            )
            if new_receipts:
                await self._persist_ws_receipts(
                    epoch_id, new_receipts, persisted_receipts, _utc_now()
                )
            logger.warning(
                "ws epoch %s ended: %s",
                epoch_id,
                getattr(exc, "reason_code", None) or str(exc) or type(exc).__name__,
            )
            await self._mark_stale(epoch_id)
        finally:
            await driver.aclose()
        return epoch_id

    def _new_receipts(
        self,
        driver: Any,
        persisted: set[str],
    ) -> tuple[RequestReceipt, ...]:
        receipts = tuple(getattr(driver, "receipts", ()) or ())
        return tuple(receipt for receipt in receipts if receipt.attempt_id not in persisted)

    async def _persist_new_driver_receipts(
        self,
        epoch_id: int,
        driver: Any,
        persisted: set[str],
        received_at: datetime,
    ) -> None:
        receipts = self._new_receipts(driver, persisted)
        if receipts:
            await self._persist_ws_receipts(epoch_id, receipts, persisted, received_at)

    async def _persist_ws_receipts(
        self,
        epoch_id: int,
        receipts: tuple[RequestReceipt, ...],
        persisted: set[str],
        received_at: datetime,
    ) -> None:
        raw = _json_bytes({"receipts": _receipt_payload(receipts)})
        ref = self._artifacts.put_bytes(raw, ARTIFACT_MIME_JSON)
        uow = self._uow_factory()
        async with uow:
            artifact_id = await self._stream.register_artifact(uow.session, ref)
            rows = [
                _receipt_event(receipt, source="market_ws", connection_epoch_id=epoch_id)
                for receipt in receipts
            ]
            await _write_source_batch(
                session=uow.session,
                stream=self._stream,
                connection_epoch_id=epoch_id,
                received_at=received_at,
                raw_artifact_id=artifact_id,
                raw_artifact_ref=ref.sha256,
                raw_hash=_sha256(raw),
                rows=rows,
            )
        persisted.update(receipt.attempt_id for receipt in receipts)

    async def _transition(
        self,
        epoch_id: int,
        expected: str,
        new: str,
        at: datetime,
    ) -> None:
        uow = self._uow_factory()
        async with uow:
            changed = await self._stream.transition_epoch(
                uow.session, epoch_id, expected, new, at=at
            )
            if not changed:
                raise RuntimeError("book_epoch_transition_conflict")

    async def _mark_stale(self, epoch_id: int) -> None:
        uow = self._uow_factory()
        async with uow:
            epoch = await self._stream.get_epoch(uow.session, epoch_id)
            if epoch is None or epoch["status"] not in {"SYNCING", "LIVE"}:
                return
            at = _utc_now()
            await self._stream.stale_epoch_books(uow.session, epoch_id=epoch_id, at=at)
            changed = await self._stream.transition_epoch(
                uow.session, epoch_id, epoch["status"], "STALE", at=at
            )
            if not changed:
                raise RuntimeError("book_epoch_stale_conflict")

    async def _commit_ws_message(
        self,
        *,
        epoch_id: int,
        msg: Any,
        updates: list[tuple[str, BookState, bool]],
        receipts: tuple[RequestReceipt, ...],
        publish_epoch: bool,
    ) -> None:
        raw = msg.raw_text.encode("utf-8")
        ref = self._artifacts.put_bytes(raw, ARTIFACT_MIME_JSON)
        payload_hash = _sha256(raw)
        frame: MarketWsFrameBase = msg.frame
        uow = self._uow_factory()
        async with uow:
            artifact_id = await self._stream.register_artifact(uow.session, ref)
            rows: list[dict[str, Any]] = []
            frame_claimed = await self._stream.claim_source_event(
                uow.session,
                key=f"market-ws:{epoch_id}:{msg.receive_seq}",
                owner_hash=payload_hash,
            )
            if frame_claimed:
                token_hint = (
                    updates[0][0]
                    if len(updates) == 1
                    else getattr(frame, "asset_id", None)
                )
                rows.append(
                    {
                        "_claim_key": f"preclaimed:market-ws:{epoch_id}:{msg.receive_seq}",
                        "_owner_hash": payload_hash,
                        "_preclaimed": True,
                        "source": "market_ws",
                        "kind": frame.event_type,
                        "connection_epoch_id": epoch_id,
                        "local_receive_seq": msg.receive_seq,
                        "provider_time": _provider_timestamp(getattr(frame, "timestamp", None)),
                        "payload_hash": payload_hash,
                        "parse_status": "unknown" if frame.event_type == "unknown" else "parsed",
                        "parse_reason": getattr(frame, "parse_error", None),
                        "condition_id": getattr(frame, "market", None),
                        "token_id": token_hint,
                    }
                )
            for receipt in receipts:
                rows.append(
                    _receipt_event(
                        receipt, source="market_ws", connection_epoch_id=epoch_id
                    )
                )

            # The frame claim above is intentionally in the same transaction.  Avoid
            # claiming it twice in the generic batch writer.
            prepared: list[dict[str, Any]] = []
            for row in rows:
                if row.pop("_preclaimed", False):
                    row.pop("_claim_key", None)
                    row.pop("_owner_hash", None)
                    row["_claim_key"] = f"batch-frame:{epoch_id}:{msg.receive_seq}"
                    row["_owner_hash"] = payload_hash
                    # claim_source_event would conflict because a different key is used;
                    # keep a marker consumed by the local writer branch below.
                    row["_already_claimed"] = True
                prepared.append(row)

            claimed_rows: list[dict[str, Any]] = []
            for row in prepared:
                clean = dict(row)
                already = bool(clean.pop("_already_claimed", False))
                key = clean.pop("_claim_key")
                owner_hash = clean.pop("_owner_hash")
                if already or await self._stream.claim_source_event(
                    uow.session, key=key, owner_hash=owner_hash
                ):
                    claimed_rows.append(clean)
            if claimed_rows:
                latest = await self._stream.latest_batch_for_epoch(uow.session, epoch_id)
                batch_no = 0 if latest is None else int(latest["batch_no"]) + 1
                batch_id = await self._stream.insert_source_batch(
                    uow.session,
                    connection_epoch_id=epoch_id,
                    batch_no=batch_no,
                    first_receive_seq=msg.receive_seq,
                    last_receive_seq=msg.receive_seq,
                    first_received_at=msg.received_at,
                    last_received_at=msg.received_at,
                    event_count=len(claimed_rows),
                    batch_hash=payload_hash,
                    prev_batch_hash=None if latest is None else latest["batch_hash"],
                    raw_artifact_ref=ref.sha256,
                    raw_artifact_id=artifact_id,
                    received_at=msg.received_at,
                )
                await self._stream.insert_source_events(
                    uow.session,
                    batch_id=batch_id,
                    received_at=msg.received_at,
                    events=claimed_rows,
                )

            if not frame_claimed:
                return
            epoch = await self._stream.get_epoch(uow.session, epoch_id)
            epoch_status = epoch["status"] if epoch else "STALE"
            for token_id, state, is_initial in updates:
                complete = state.best_bid is not None and state.best_ask is not None
                validity = "CROSSED" if state.crossed else "VALID"
                checkpoint_id = await self._stream.insert_book_checkpoint(
                    uow.session,
                    token_id=token_id,
                    connection_epoch_id=epoch_id,
                    source_kind="ws_initial" if is_initial else "ws_delta_aggregate",
                    book_hash=state.depth_hash(),
                    best_bid=state.best_bid,
                    best_ask=state.best_ask,
                    tick_size=state.tick_size,
                    min_order_size=state.min_order_size,
                    provider_timestamp=_provider_timestamp(getattr(frame, "timestamp", None)),
                    artifact_ref=ref.sha256,
                    raw_artifact_id=artifact_id,
                    completeness=complete,
                    validity=validity,
                    received_at=msg.received_at,
                )
                levels = [
                    {"side": "bid", "price": price, "size": size}
                    for price, size in state.bids.items()
                ] + [
                    {"side": "ask", "price": price, "size": size}
                    for price, size in state.asks.items()
                ]
                await self._stream.insert_book_levels(
                    uow.session,
                    checkpoint_id=checkpoint_id,
                    received_at=msg.received_at,
                    levels=levels,
                )
                current_validity = (
                    "CROSSED"
                    if state.crossed
                    else "STALE"
                    if not complete
                    else "VALID"
                    if epoch_status == "LIVE"
                    else "SYNCING"
                )
                updated = await self._stream.replace_book_current(
                    uow.session,
                    token_id=token_id,
                    connection_epoch_id=epoch_id,
                    checkpoint_id=checkpoint_id,
                    checkpoint_received_at=msg.received_at,
                    best_bid=state.best_bid,
                    best_ask=state.best_ask,
                    tick_size=state.tick_size,
                    min_order_size=state.min_order_size,
                    depth_hash=state.depth_hash(),
                    validity=current_validity,
                    observed_at=msg.received_at,
                    allow_syncing_epoch=epoch_status == "SYNCING",
                )
                if not updated:
                    raise RuntimeError("book_epoch_not_current")
                env = create_envelope(
                    topic=OUTBOX_TOPIC_BOOK,
                    schema_version=1,
                    aggregate_type="book",
                    aggregate_id=token_id,
                    idempotency_key=f"market-book-{epoch_id}-{msg.receive_seq}-{token_id}",
                    release_manifest_id=self._config_release_id,
                    priority=112,
                    payload={
                        "asset_id": token_id,
                        "epoch_id": epoch_id,
                        "checkpoint_id": checkpoint_id,
                        "checkpoint_received_at": msg.received_at.isoformat(),
                        "depth_hash": state.depth_hash(),
                        "validity": current_validity,
                    },
                )
                await self._outbox.enqueue(uow.session, env)

            if isinstance(frame, MarketWsTickSizeChange) and frame.asset_id:
                env = create_envelope(
                    topic=OUTBOX_TOPIC_MARKET_CONFIG_REFRESH,
                    schema_version=1,
                    aggregate_type="asset",
                    aggregate_id=frame.asset_id,
                    idempotency_key=f"market-config-refresh-{epoch_id}-{msg.receive_seq}",
                    release_manifest_id=self._config_release_id,
                    priority=104,
                    payload={"asset_id": frame.asset_id, "reason": "tick_size_change"},
                )
                await self._outbox.enqueue(uow.session, env)
            if frame.event_type in {"new_market", "market_resolved"}:
                aggregate_id = str(
                    getattr(frame, "market", None) or getattr(frame, "id", None) or msg.receive_seq
                )
                env = create_envelope(
                    topic=OUTBOX_TOPIC_UNIVERSE_REFRESH,
                    schema_version=1,
                    aggregate_type="market",
                    aggregate_id=aggregate_id,
                    idempotency_key=f"universe-refresh-{epoch_id}-{msg.receive_seq}",
                    release_manifest_id=self._config_release_id,
                    priority=120,
                    payload={"condition_id": aggregate_id, "reason": frame.event_type},
                )
                await self._outbox.enqueue(uow.session, env)

            if publish_epoch and epoch_status == "SYNCING":
                activated = await self._stream.activate_epoch_books(
                    uow.session, epoch_id=epoch_id, at=msg.received_at
                )
                if not activated:
                    raise RuntimeError("book_epoch_activation_conflict")
