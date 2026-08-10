"""Market stream / book Repository（WP-01B Checkpoint D）。

只拥有 SQL：epoch 状态机（CAS transition）、source batch/index、book checkpoint/levels、
book current（CAS）、quote binding。绝不 commit、不调用网络、不做业务判断。

- epoch transition 只做条件 UPDATE（``WHERE status=:expected``），非法 transition 由 DB guard 拒绝。
- 分区证据表写入必须落在已建分区内；received_at 缺省用 now()（当前日分区必然存在）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.artifact_store.contracts import ArtifactRef
from app.models.trading.market_stream import (
    BOOK_VALIDITY,
    CHECKPOINT_SOURCES,
    CURRENT_VALIDITY,
    EPOCH_STATUSES,
    PARSE_STATUSES,
    SOURCE_KINDS,
)

_sha_len = 64


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class MarketStreamRepository:
    """source/book SQL；不持有状态。"""

    # ---------------- artifact catalog ----------------

    async def register_artifact(self, session: AsyncSession, ref: ArtifactRef) -> int:
        """Register an immutable ArtifactRef and verify exact metadata on dedupe."""
        inserted = await session.execute(
            text(
                "INSERT INTO trading.artifact_objects "
                "(sha256, original_size, stored_size, mime, compression, storage_driver, "
                " storage_version, locator) "
                "VALUES (:sha, :orig, :stored, :mime, :codec, :driver, :ver, :loc) "
                "ON CONFLICT (sha256, compression, storage_driver, storage_version) "
                "DO NOTHING RETURNING id"
            ),
            {
                "sha": ref.sha256,
                "orig": ref.original_size,
                "stored": ref.stored_size,
                "mime": ref.mime,
                "codec": ref.compression,
                "driver": ref.storage_driver,
                "ver": ref.storage_version,
                "loc": ref.locator,
            },
        )
        artifact_id = inserted.scalar_one_or_none()
        if artifact_id is not None:
            return int(artifact_id)
        row = (
            await session.execute(
                text(
                    "SELECT id, original_size, stored_size, mime, locator "
                    "FROM trading.artifact_objects WHERE sha256=:sha AND compression=:codec "
                    "AND storage_driver=:driver AND storage_version=:ver FOR SHARE"
                ),
                {
                    "sha": ref.sha256,
                    "codec": ref.compression,
                    "driver": ref.storage_driver,
                    "ver": ref.storage_version,
                },
            )
        ).first()
        if row is None or (
            int(row.original_size) != ref.original_size
            or int(row.stored_size) != ref.stored_size
            or row.mime != ref.mime
            or row.locator != ref.locator
        ):
            raise RuntimeError("artifact_catalog_conflict")
        return int(row.id)

    async def load_artifact_ref(self, session: AsyncSession, artifact_id: int) -> ArtifactRef:
        row = (
            await session.execute(
                text(
                    "SELECT sha256, original_size, stored_size, mime, compression, "
                    "storage_driver, storage_version, locator "
                    "FROM trading.artifact_objects WHERE id=:i"
                ),
                {"i": artifact_id},
            )
        ).first()
        if row is None:
            raise RuntimeError("artifact_catalog_missing")
        return ArtifactRef(
            sha256=row.sha256,
            original_size=int(row.original_size),
            stored_size=int(row.stored_size),
            mime=row.mime,
            compression=row.compression,
            storage_driver=row.storage_driver,
            storage_version=row.storage_version,
            locator=row.locator,
        )

    # ---------------- epoch ----------------

    async def create_epoch(
        self,
        session: AsyncSession,
        *,
        shard_key: str,
        provider: str,
        started_at: datetime,
        config_release_id: int,
        owner: str | None = None,
        fencing_token: int | None = None,
    ) -> int:
        if provider not in SOURCE_KINDS:
            raise ValueError(f"unknown provider: {provider!r}")
        if isinstance(config_release_id, bool) or config_release_id <= 0:
            raise ValueError("config_release_id_invalid")
        result = await session.execute(
            text(
                "INSERT INTO trading.pm_connection_epochs "
                "(shard_key, provider, config_release_id, status, owner, fencing_token, started_at) "
                "VALUES (:sk, :p, :rel, 'CONNECTING', :o, :ft, :t) RETURNING id"
            ),
            {
                "sk": shard_key,
                "p": provider,
                "rel": config_release_id,
                "o": owner,
                "ft": fencing_token,
                "t": started_at,
            },
        )
        return result.scalar_one()

    async def get_epoch(self, session: AsyncSession, epoch_id: int) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, shard_key, provider, config_release_id, status, owner, "
                "       fencing_token, started_at, synced_at, live_at, stale_at, closed_at, "
                "       closed_reason "
                "FROM trading.pm_connection_epochs WHERE id=:e"
            ),
            {"e": epoch_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def active_epoch_for_shard(
        self, session: AsyncSession, shard_key: str, provider: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, status, fencing_token, owner FROM trading.pm_connection_epochs "
                "WHERE shard_key=:sk AND provider=:p "
                "AND status IN ('CONNECTING','SYNCING','LIVE') ORDER BY id DESC LIMIT 1"
            ),
            {"sk": shard_key, "p": provider},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def transition_epoch(
        self,
        session: AsyncSession,
        epoch_id: int,
        expected_status: str,
        new_status: str,
        *,
        at: datetime,
        closed_reason: str | None = None,
    ) -> bool:
        """条件 transition（CAS）；非法/重复由 DB guard 抛错或返回 False。"""
        if expected_status not in EPOCH_STATUSES or new_status not in EPOCH_STATUSES:
            raise ValueError(f"invalid epoch status: {expected_status}->{new_status}")
        column = {
            "SYNCING": "synced_at",
            "LIVE": "live_at",
            "STALE": "stale_at",
            "CLOSED": "closed_at",
        }.get(new_status)
        extra = f", {column}=:at" if column else ""
        if new_status == "CLOSED":
            extra += ", closed_reason=:reason"
        result = await session.execute(
            text(
                f"UPDATE trading.pm_connection_epochs SET status=:ns{extra} "
                "WHERE id=:e AND status=:es"
            ),
            {
                "ns": new_status,
                "es": expected_status,
                "at": at,
                "e": epoch_id,
                "reason": closed_reason,
            },
        )
        return result.rowcount == 1

    # ---------------- source batch / index ----------------

    async def claim_source_event(
        self,
        session: AsyncSession,
        *,
        key: str,
        owner_hash: str,
    ) -> bool:
        """Globally claim one source event across time partitions.

        Partition-local UNIQUE constraints cannot enforce ``epoch + receive_seq``
        globally because PostgreSQL requires the partition key in the constraint.
        The non-partitioned foundation claim is therefore the authority.
        """
        claimed = await session.execute(
            text(
                "INSERT INTO trading.idempotency_claims (scope, key, owner) "
                "VALUES ('pm_source_event', :k, :o) "
                "ON CONFLICT (scope, key) DO NOTHING RETURNING owner"
            ),
            {"k": key, "o": owner_hash},
        )
        owner = claimed.scalar_one_or_none()
        if owner is not None:
            return True
        existing = (
            await session.execute(
                text(
                    "SELECT owner FROM trading.idempotency_claims "
                    "WHERE scope='pm_source_event' AND key=:k FOR UPDATE"
                ),
                {"k": key},
            )
        ).scalar_one()
        if existing != owner_hash:
            raise RuntimeError("source_event_idempotency_conflict")
        return False

    async def insert_source_batch(
        self,
        session: AsyncSession,
        *,
        connection_epoch_id: int,
        batch_no: int,
        first_receive_seq: int,
        last_receive_seq: int,
        first_received_at: datetime,
        last_received_at: datetime,
        event_count: int,
        batch_hash: str,
        prev_batch_hash: str | None,
        raw_artifact_ref: str,
        raw_artifact_id: int,
        received_at: datetime,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.pm_source_event_batches "
                "(connection_epoch_id, batch_no, first_receive_seq, last_receive_seq, "
                " first_received_at, last_received_at, event_count, batch_hash, "
                " prev_batch_hash, raw_artifact_ref, raw_artifact_id, received_at) "
                "VALUES (:e, :bn, :fs, :ls, :ff, :ll, :n, :bh, :pb, :ar, :aid, :t) RETURNING id"
            ),
            {
                "e": connection_epoch_id,
                "bn": batch_no,
                "fs": first_receive_seq,
                "ls": last_receive_seq,
                "ff": first_received_at,
                "ll": last_received_at,
                "n": event_count,
                "bh": batch_hash,
                "pb": prev_batch_hash,
                "ar": raw_artifact_ref,
                "aid": raw_artifact_id,
                "t": received_at,
            },
        )
        return result.scalar_one()

    async def latest_batch_for_epoch(
        self, session: AsyncSession, connection_epoch_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, batch_no, last_receive_seq, batch_hash, received_at "
                "FROM trading.pm_source_event_batches WHERE connection_epoch_id=:e "
                "ORDER BY batch_no DESC LIMIT 1"
            ),
            {"e": connection_epoch_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def insert_source_events(
        self,
        session: AsyncSession,
        *,
        batch_id: int,
        received_at: datetime,
        events: Iterable[dict[str, Any]],
    ) -> int:
        """批量插入 source_event_index（batch_ordinal 单调）。"""
        rows = list(events)
        if not rows:
            return 0
        params: list[dict[str, Any]] = []
        for i, row in enumerate(rows):
            if row.get("source") not in SOURCE_KINDS:
                raise ValueError(f"unknown source: {row.get('source')!r}")
            if row.get("parse_status", "parsed") not in PARSE_STATUSES:
                raise ValueError(f"unknown parse_status: {row.get('parse_status')!r}")
            ordinal = row.get("batch_ordinal", i)
            params.append(
                {
                    "b": batch_id,
                    "t": received_at,
                    "src": row["source"],
                    "kind": row["kind"],
                    "e": row.get("connection_epoch_id"),
                    "seq": row.get("local_receive_seq"),
                    "pt": row.get("provider_time"),
                    "et": row.get("event_time"),
                    "ph": row["payload_hash"],
                    "ord": ordinal,
                    "ps": row.get("parse_status", "parsed"),
                    "pr": row.get("parse_reason"),
                    "cid": row.get("condition_id"),
                    "tid": row.get("token_id"),
                    "gmid": row.get("gamma_market_id"),
                    "attempt_id": row.get("attempt_id"),
                    "endpoint": row.get("endpoint"),
                    "method": row.get("method"),
                    "http_status": row.get("http_status"),
                    "latency_ms": row.get("latency_ms"),
                    "error_code": row.get("error_code"),
                    "request_hash": row.get("request_hash"),
                    "response_hash": row.get("response_hash"),
                    "retry_count": row.get("retry_count"),
                }
            )
        # SQLAlchemy/asyncpg executemany avoids one network round-trip per level.
        await session.execute(
            text(
                "INSERT INTO trading.pm_source_event_index "
                "(batch_id, received_at, source, kind, connection_epoch_id, "
                " local_receive_seq, provider_time, event_time, payload_hash, "
                " batch_ordinal, parse_status, parse_reason, condition_id, token_id, "
                " gamma_market_id, attempt_id, endpoint, method, http_status, latency_ms, "
                " error_code, request_hash, response_hash, retry_count) "
                "VALUES (:b, :t, :src, :kind, :e, :seq, :pt, :et, :ph, :ord, :ps, :pr, "
                "        :cid, :tid, :gmid, :attempt_id, :endpoint, :method, :http_status, "
                "        :latency_ms, :error_code, :request_hash, :response_hash, :retry_count)"
            ),
            params,
        )
        return len(rows)

    # ---------------- book checkpoint / levels ----------------

    async def insert_book_checkpoint(
        self,
        session: AsyncSession,
        *,
        token_id: str,
        connection_epoch_id: int | None,
        source_kind: str,
        book_hash: str,
        best_bid: Any | None,
        best_ask: Any | None,
        tick_size: Any | None,
        min_order_size: Any | None,
        provider_timestamp: int | None,
        artifact_ref: str | None,
        raw_artifact_id: int,
        completeness: bool,
        validity: str,
        received_at: datetime,
    ) -> int:
        if source_kind not in CHECKPOINT_SOURCES:
            raise ValueError(f"unknown checkpoint source: {source_kind!r}")
        if validity not in BOOK_VALIDITY:
            raise ValueError(f"unknown validity: {validity!r}")
        result = await session.execute(
            text(
                "INSERT INTO trading.pm_book_checkpoints "
                "(token_id, connection_epoch_id, source_kind, book_hash, best_bid, best_ask, "
                " tick_size, min_order_size, provider_timestamp, artifact_ref, completeness, "
                " raw_artifact_id, validity, received_at) "
                "VALUES (:tid, :e, :sk, :bh, :bb, :ba, :ts, :mos, :pt, :ar, :co, :aid, :v, :t) "
                "RETURNING id"
            ),
            {
                "tid": token_id,
                "e": connection_epoch_id,
                "sk": source_kind,
                "bh": book_hash,
                "bb": best_bid,
                "ba": best_ask,
                "ts": tick_size,
                "mos": min_order_size,
                "pt": provider_timestamp,
                "ar": artifact_ref,
                "co": completeness,
                "aid": raw_artifact_id,
                "v": validity,
                "t": received_at,
            },
        )
        return result.scalar_one()

    async def insert_book_levels(
        self,
        session: AsyncSession,
        *,
        checkpoint_id: int,
        received_at: datetime,
        levels: Iterable[dict[str, Any]],
    ) -> int:
        rows = list(levels)
        params: list[dict[str, Any]] = []
        for i, row in enumerate(rows):
            side = row["side"]
            if side not in ("bid", "ask"):
                raise ValueError(f"unknown side: {side!r}")
            params.append(
                {
                    "c": checkpoint_id,
                    "t": received_at,
                    "s": side,
                    "p": row["price"],
                    "sz": row["size"],
                    "o": row.get("ordinal", i),
                }
            )
        if params:
            await session.execute(
                text(
                    "INSERT INTO trading.pm_book_levels "
                    "(checkpoint_id, received_at, side, price, size, ordinal) "
                    "VALUES (:c, :t, :s, :p, :sz, :o)"
                ),
                params,
            )
        return len(rows)

    async def latest_valid_checkpoint(
        self, session: AsyncSession, token_id: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, token_id, connection_epoch_id, source_kind, book_hash, best_bid, "
                "       best_ask, tick_size, min_order_size, provider_timestamp, artifact_ref, "
                "       completeness, validity, received_at "
                "FROM trading.pm_book_checkpoints WHERE token_id=:tid "
                "AND validity='VALID' ORDER BY received_at DESC, id DESC LIMIT 1"
            ),
            {"tid": token_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    # ---------------- book current（CAS）----------------

    async def replace_book_current(
        self,
        session: AsyncSession,
        *,
        token_id: str,
        connection_epoch_id: int | None,
        checkpoint_id: int | None,
        checkpoint_received_at: datetime | None,
        best_bid: Any | None,
        best_ask: Any | None,
        tick_size: Any | None,
        min_order_size: Any | None,
        depth_hash: str | None,
        validity: str,
        observed_at: datetime,
        allow_syncing_epoch: bool = False,
    ) -> bool:
        if validity not in CURRENT_VALIDITY:
            raise ValueError(f"unknown validity: {validity!r}")
        result = await session.execute(
            text(
                "INSERT INTO trading.pm_book_current "
                "(token_id, connection_epoch_id, checkpoint_id, checkpoint_received_at, "
                " best_bid, best_ask, tick_size, "
                " min_order_size, depth_hash, validity, observed_at) "
                "SELECT :tid, :e, :cp, :cpt, :bb, :ba, :ts, :mos, :dh, :v, :ob "
                "FROM trading.pm_connection_epochs ep "
                "WHERE ep.id=:e AND (ep.status='LIVE' OR (:allow_sync AND ep.status='SYNCING')) "
                "ON CONFLICT (token_id) DO UPDATE SET "
                "  connection_epoch_id=EXCLUDED.connection_epoch_id, "
                "  checkpoint_id=EXCLUDED.checkpoint_id, "
                "  checkpoint_received_at=EXCLUDED.checkpoint_received_at, "
                "  best_bid=EXCLUDED.best_bid, "
                "  best_ask=EXCLUDED.best_ask, tick_size=EXCLUDED.tick_size, "
                "  min_order_size=EXCLUDED.min_order_size, depth_hash=EXCLUDED.depth_hash, "
                "  validity=EXCLUDED.validity, observed_at=EXCLUDED.observed_at "
                "WHERE trading.pm_book_current.observed_at <= EXCLUDED.observed_at "
                "RETURNING id"
            ),
            {
                "tid": token_id,
                "e": connection_epoch_id,
                "cp": checkpoint_id,
                "cpt": checkpoint_received_at,
                "bb": best_bid,
                "ba": best_ask,
                "ts": tick_size,
                "mos": min_order_size,
                "dh": depth_hash,
                "v": validity,
                "ob": observed_at,
                "allow_sync": allow_syncing_epoch,
            },
        )
        return result.first() is not None

    async def activate_epoch_books(
        self,
        session: AsyncSession,
        *,
        epoch_id: int,
        at: datetime,
    ) -> bool:
        """Atomically publish an epoch only after its full subscription barrier."""
        changed = await self.transition_epoch(session, epoch_id, "SYNCING", "LIVE", at=at)
        if not changed:
            return False
        await session.execute(
            text(
                "UPDATE trading.pm_book_current SET validity='VALID', observed_at=:at "
                "WHERE connection_epoch_id=:e AND validity='SYNCING'"
            ),
            {"e": epoch_id, "at": at},
        )
        return True

    async def stale_epoch_books(
        self,
        session: AsyncSession,
        *,
        epoch_id: int,
        at: datetime,
    ) -> int:
        """Invalidate every current quote produced by a disconnected epoch."""
        result = await session.execute(
            text(
                "UPDATE trading.pm_book_current SET validity='STALE', observed_at=:at "
                "WHERE connection_epoch_id=:e AND validity <> 'STALE'"
            ),
            {"e": epoch_id, "at": at},
        )
        return int(result.rowcount or 0)

    async def get_book_current(self, session: AsyncSession, token_id: str) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT token_id, connection_epoch_id, checkpoint_id, checkpoint_received_at, "
                "       best_bid, best_ask, "
                "       tick_size, min_order_size, depth_hash, validity, observed_at "
                "FROM trading.pm_book_current WHERE token_id=:tid"
            ),
            {"tid": token_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    # ---------------- quote binding ----------------

    async def insert_quote_binding(
        self,
        session: AsyncSession,
        *,
        token_id: str,
        checkpoint_id: int,
        checkpoint_received_at: datetime,
        best_bid: Any | None,
        best_ask: Any | None,
        price_convention: str,
        as_of: datetime,
        received_at: datetime,
        staleness_policy_ref: str,
        stale_at: datetime,
        decision_ref: str | None = None,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.pm_quote_bindings "
                "(token_id, checkpoint_id, checkpoint_received_at, best_bid, best_ask, price_convention, as_of, "
                " received_at, staleness_policy_ref, stale_at, decision_ref) "
                "VALUES (:tid, :cp, :cpt, :bb, :ba, :pc, :ao, :rc, :sr, :sa, :dr)"
            ),
            {
                "tid": token_id,
                "cp": checkpoint_id,
                "cpt": checkpoint_received_at,
                "bb": best_bid,
                "ba": best_ask,
                "pc": price_convention,
                "ao": as_of,
                "rc": received_at,
                "sr": staleness_policy_ref,
                "sa": stale_at,
                "dr": decision_ref,
            },
        )
