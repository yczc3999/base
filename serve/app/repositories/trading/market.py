"""Market master Repository（WP-01B Checkpoint D）。

只拥有 SQL：frame/page、event/market/token upsert、version append、lifecycle、current CAS。
绝不 commit、不调用网络、不做业务判断（实施合同 §6）。

- frame：create / finalize（OPEN→COMPLETE|FAILED 由 DB guard 强制）；page append + cursor 链检查。
- event/market/token：按 provider ID / condition / token 唯一 upsert；version 只在
  normalized content 变化时 append（幂等重放 effect=0）。
- ``pm_market_current``：INSERT..ON CONFLICT + ``observed_at`` CAS（旧帧不覆盖，DB trigger 兜底）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trading.market import (
    MAPPING_STATES,
    LIFECYCLE_TYPES,
    PAGE_ENDPOINTS,
)

STATUS_OPEN = "OPEN"
STATUS_COMPLETE = "COMPLETE"
STATUS_FAILED = "FAILED"


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class MarketRepository:
    """market master SQL；不持有状态。"""

    # ---------------- frame ----------------

    async def acquire_frame(
        self,
        session: AsyncSession,
        *,
        owner: str,
        started_at: datetime,
        lease_expires_at: datetime,
    ) -> dict[str, Any]:
        """Acquire the singleton OPEN frame using a transaction-scoped lock.

        A live lease owned by another runner is never stolen.  An expired lease
        is taken over with a strictly larger fencing token.  A retry by the same
        owner only extends its lease and keeps the token stable.
        """
        if not owner:
            raise ValueError("universe_frame_owner_empty")
        if lease_expires_at <= started_at:
            raise ValueError("universe_frame_lease_invalid")
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('trading.pm_universe_frame', 0))")
        )
        current = (
            await session.execute(
                text(
                    "SELECT id, owner, lease_expires_at, fencing_token "
                    "FROM trading.pm_universe_frames WHERE status='OPEN' "
                    "ORDER BY id DESC LIMIT 1 FOR UPDATE"
                )
            )
        ).mappings().first()
        if current is None:
            result = await session.execute(
                text(
                    "INSERT INTO trading.pm_universe_frames "
                    "(status, started_at, owner, lease_expires_at, fencing_token) "
                    "SELECT 'OPEN', :started, :owner, :lease, "
                    "       COALESCE(MAX(fencing_token), 0) + 1 "
                    "FROM trading.pm_universe_frames RETURNING id, owner, "
                    "lease_expires_at, fencing_token"
                ),
                {"started": started_at, "owner": owner, "lease": lease_expires_at},
            )
            return dict(result.mappings().one())

        if current["owner"] == owner:
            result = await session.execute(
                text(
                    "UPDATE trading.pm_universe_frames SET lease_expires_at=:lease "
                    "WHERE id=:id AND status='OPEN' AND owner=:owner "
                    "AND fencing_token=:fence AND lease_expires_at > :now "
                    "RETURNING id, owner, lease_expires_at, fencing_token"
                ),
                {
                    "id": current["id"],
                    "owner": owner,
                    "fence": current["fencing_token"],
                    "now": started_at,
                    "lease": lease_expires_at,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise RuntimeError("universe_frame_lease_expired")
            return dict(row)

        if current["lease_expires_at"] > started_at:
            raise RuntimeError("universe_frame_busy")
        result = await session.execute(
            text(
                "UPDATE trading.pm_universe_frames "
                "SET owner=:owner, lease_expires_at=:lease, fencing_token=fencing_token+1 "
                "WHERE id=:id AND status='OPEN' AND lease_expires_at <= :now "
                "RETURNING id, owner, lease_expires_at, fencing_token"
            ),
            {
                "id": current["id"],
                "owner": owner,
                "now": started_at,
                "lease": lease_expires_at,
            },
        )
        row = result.mappings().first()
        if row is None:
            raise RuntimeError("universe_frame_takeover_conflict")
        return dict(row)

    async def renew_frame_lease(
        self,
        session: AsyncSession,
        *,
        frame_id: int,
        owner: str,
        fencing_token: int,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.pm_universe_frames SET lease_expires_at=:lease "
                "WHERE id=:id AND status='OPEN' AND owner=:owner "
                "AND fencing_token=:fence AND lease_expires_at > :now"
            ),
            {
                "id": frame_id,
                "owner": owner,
                "fence": fencing_token,
                "now": now,
                "lease": lease_expires_at,
            },
        )
        return result.rowcount == 1

    async def get_frame(self, session: AsyncSession, frame_id: int) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, status, started_at, owner, lease_expires_at, fencing_token, "
                "       completed_at, page_count, total_events, total_markets, content_hash, "
                "       artifact_id, artifact_ref, error_reason "
                "FROM trading.pm_universe_frames WHERE id=:f"
            ),
            {"f": frame_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def latest_page_for_endpoint(
        self, session: AsyncSession, frame_id: int, endpoint: str
    ) -> dict[str, Any] | None:
        """该 frame+endpoint 的最后一页（按 page_no 降序），用于 cursor 链校验。"""
        result = await session.execute(
            text(
                "SELECT id, page_no, cursor_input, cursor_output, item_count, raw_artifact_id, "
                "       raw_artifact_ref, raw_artifact_hash, received_at "
                "FROM trading.pm_universe_frame_pages "
                "WHERE frame_id=:f AND endpoint=:e ORDER BY page_no DESC LIMIT 1"
            ),
            {"f": frame_id, "e": endpoint},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def append_page(
        self,
        session: AsyncSession,
        *,
        frame_id: int,
        page_no: int,
        endpoint: str,
        cursor_input: str | None,
        cursor_output: str | None,
        item_count: int,
        raw_artifact_id: int,
        raw_artifact_ref: str,
        raw_artifact_hash: str,
        received_at: datetime,
        owner: str,
        fencing_token: int,
    ) -> bool:
        if endpoint not in PAGE_ENDPOINTS:
            raise ValueError(f"unknown endpoint: {endpoint!r}")
        inserted = await session.execute(
            text(
                "INSERT INTO trading.pm_universe_frame_pages "
                "(frame_id, page_no, endpoint, cursor_input, cursor_output, item_count, "
                " raw_artifact_id, raw_artifact_ref, raw_artifact_hash, received_at) "
                "SELECT :f, :p, :e, :ci, :co, :n, :aid, :a, :ah, :t "
                "FROM trading.pm_universe_frames frame "
                "WHERE frame.id=:f AND frame.status='OPEN' AND frame.owner=:owner "
                "AND frame.fencing_token=:fence AND frame.lease_expires_at > :t "
                "ON CONFLICT (frame_id, page_no) DO NOTHING RETURNING id"
            ),
            {
                "f": frame_id,
                "p": page_no,
                "e": endpoint,
                "ci": cursor_input,
                "co": cursor_output,
                "n": item_count,
                "aid": raw_artifact_id,
                "a": raw_artifact_ref,
                "ah": raw_artifact_hash,
                "t": received_at,
                "owner": owner,
                "fence": fencing_token,
            },
        )
        page_id = inserted.scalar_one_or_none()
        if page_id is None:
            existing = (
                await session.execute(
                    text(
                        "SELECT endpoint, cursor_input, cursor_output, item_count, "
                        "raw_artifact_id, raw_artifact_hash FROM "
                        "trading.pm_universe_frame_pages WHERE frame_id=:f AND page_no=:p"
                    ),
                    {"f": frame_id, "p": page_no},
                )
            ).mappings().first()
            expected = {
                "endpoint": endpoint,
                "cursor_input": cursor_input,
                "cursor_output": cursor_output,
                "item_count": item_count,
                "raw_artifact_id": raw_artifact_id,
                "raw_artifact_hash": raw_artifact_hash,
            }
            if existing is None or any(existing[key] != value for key, value in expected.items()):
                raise RuntimeError("universe_frame_page_conflict")
            return False
        updated = await session.execute(
            text(
                "UPDATE trading.pm_universe_frames SET page_count=page_count+1 "
                "WHERE id=:f AND status='OPEN' AND owner=:owner AND fencing_token=:fence"
            ),
            {"f": frame_id, "owner": owner, "fence": fencing_token},
        )
        if updated.rowcount != 1:
            raise RuntimeError("universe_frame_fencing_conflict")
        return True

    async def finalize_frame(
        self,
        session: AsyncSession,
        *,
        frame_id: int,
        status: str,
        total_events: int,
        total_markets: int,
        content_hash: str | None,
        artifact_id: int | None,
        artifact_ref: str | None,
        error_reason: str | None,
        owner: str,
        fencing_token: int,
        completed_at: datetime,
    ) -> bool:
        """OPEN → COMPLETE|FAILED（DB guard 只允许该 transition）。"""
        if status not in (STATUS_COMPLETE, STATUS_FAILED):
            raise ValueError(f"finalize status must be COMPLETE|FAILED, got {status!r}")
        result = await session.execute(
            text(
                "UPDATE trading.pm_universe_frames "
                "SET status=:s, completed_at=:at, total_events=:te, total_markets=:tm, "
                "    content_hash=:ch, artifact_id=:aid, artifact_ref=:ar, error_reason=:er "
                "WHERE id=:f AND status='OPEN' AND owner=:owner AND fencing_token=:fence "
                "AND lease_expires_at > :at"
            ),
            {
                "f": frame_id,
                "s": status,
                "te": total_events,
                "tm": total_markets,
                "ch": content_hash,
                "aid": artifact_id,
                "ar": artifact_ref,
                "er": error_reason,
                "owner": owner,
                "fence": fencing_token,
                "at": completed_at,
            },
        )
        return result.rowcount == 1

    async def list_pages(
        self, session: AsyncSession, frame_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT id, page_no, endpoint, cursor_input, cursor_output, item_count, "
                "       raw_artifact_id, raw_artifact_ref, raw_artifact_hash, received_at "
                "FROM trading.pm_universe_frame_pages WHERE frame_id=:f "
                "ORDER BY page_no"
            ),
            {"f": frame_id},
        )
        return _rows(result)

    # ---------------- event / market / token ----------------

    async def upsert_event(
        self,
        session: AsyncSession,
        *,
        gamma_event_id: str,
        slug: str | None,
        title: str | None,
        description: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        active: bool | None,
        closed: bool | None,
        archived: bool | None,
        volume: Any | None,
        liquidity: Any | None,
        content_hash: str,
        raw_artifact_ref: str | None,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.pm_events "
                "(gamma_event_id, slug, title, description, start_date, end_date, active, "
                " closed, archived, volume, liquidity, content_hash, raw_artifact_ref) "
                "VALUES (:id, :slug, :title, :desc, :sd, :ed, :a, :c, :arc, :vol, :liq, :ch, :ar) "
                "ON CONFLICT (gamma_event_id) DO UPDATE SET "
                "  slug=EXCLUDED.slug, title=EXCLUDED.title, description=EXCLUDED.description, "
                "  start_date=EXCLUDED.start_date, end_date=EXCLUDED.end_date, "
                "  active=EXCLUDED.active, closed=EXCLUDED.closed, archived=EXCLUDED.archived, "
                "  volume=EXCLUDED.volume, liquidity=EXCLUDED.liquidity, "
                "  content_hash=EXCLUDED.content_hash, raw_artifact_ref=EXCLUDED.raw_artifact_ref "
                "RETURNING id"
            ),
            {
                "id": gamma_event_id,
                "slug": slug,
                "title": title,
                "desc": description,
                "sd": start_date,
                "ed": end_date,
                "a": active,
                "c": closed,
                "arc": archived,
                "vol": volume,
                "liq": liquidity,
                "ch": content_hash,
                "ar": raw_artifact_ref,
            },
        )
        return result.scalar_one()

    async def upsert_market(
        self,
        session: AsyncSession,
        *,
        gamma_market_id: str,
        gamma_event_id: str | None,
        condition_id: str | None,
        question: str | None,
        slug: str | None,
        ticker: str | None,
        active: bool | None,
        closed: bool | None,
        archived: bool | None,
        accepting_orders: bool | None,
        enable_order_book: bool | None,
        neg_risk: bool | None,
        start_date: datetime | None,
        end_date: datetime | None,
        closed_at: datetime | None,
        volume: Any | None,
        liquidity: Any | None,
        spread: Any | None,
        best_bid: Any | None,
        best_ask: Any | None,
        last_trade_price: Any | None,
        content_hash: str,
        raw_artifact_ref: str | None,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.pm_markets "
                "(gamma_market_id, gamma_event_id, condition_id, question, slug, ticker, "
                " active, closed, archived, accepting_orders, enable_order_book, neg_risk, "
                " start_date, end_date, closed_at, volume, liquidity, spread, best_bid, "
                " best_ask, last_trade_price, content_hash, raw_artifact_ref) "
                "VALUES (:mid, :eid, :cid, :q, :slug, :tick, :a, :c, :arc, :ao, :eo, :neg, "
                "        :sd, :ed, :cd, :vol, :liq, :sp, :bb, :ba, :ltp, :ch, :ar) "
                "ON CONFLICT (gamma_market_id) DO UPDATE SET "
                "  gamma_event_id=EXCLUDED.gamma_event_id, condition_id=EXCLUDED.condition_id, "
                "  question=EXCLUDED.question, slug=EXCLUDED.slug, ticker=EXCLUDED.ticker, "
                "  active=EXCLUDED.active, closed=EXCLUDED.closed, archived=EXCLUDED.archived, "
                "  accepting_orders=EXCLUDED.accepting_orders, "
                "  enable_order_book=EXCLUDED.enable_order_book, neg_risk=EXCLUDED.neg_risk, "
                "  start_date=EXCLUDED.start_date, end_date=EXCLUDED.end_date, "
                "  closed_at=EXCLUDED.closed_at, volume=EXCLUDED.volume, "
                "  liquidity=EXCLUDED.liquidity, spread=EXCLUDED.spread, "
                "  best_bid=EXCLUDED.best_bid, best_ask=EXCLUDED.best_ask, "
                "  last_trade_price=EXCLUDED.last_trade_price, "
                "  content_hash=EXCLUDED.content_hash, raw_artifact_ref=EXCLUDED.raw_artifact_ref "
                "RETURNING id"
            ),
            {
                "mid": gamma_market_id,
                "eid": gamma_event_id,
                "cid": condition_id,
                "q": question,
                "slug": slug,
                "tick": ticker,
                "a": active,
                "c": closed,
                "arc": archived,
                "ao": accepting_orders,
                "eo": enable_order_book,
                "neg": neg_risk,
                "sd": start_date,
                "ed": end_date,
                "cd": closed_at,
                "vol": volume,
                "liq": liquidity,
                "sp": spread,
                "bb": best_bid,
                "ba": best_ask,
                "ltp": last_trade_price,
                "ch": content_hash,
                "ar": raw_artifact_ref,
            },
        )
        return result.scalar_one()

    async def get_market_by_gamma_id(
        self, session: AsyncSession, gamma_market_id: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, gamma_market_id, condition_id, content_hash FROM trading.pm_markets "
                "WHERE gamma_market_id=:m"
            ),
            {"m": gamma_market_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def latest_market_version_hash(
        self, session: AsyncSession, market_db_id: int
    ) -> str | None:
        result = await session.execute(
            text(
                "SELECT normalized_hash FROM trading.pm_market_versions "
                "WHERE market_id=:m ORDER BY version_no DESC LIMIT 1"
            ),
            {"m": market_db_id},
        )
        row = result.first()
        return row[0] if row else None

    async def latest_market_version_no(
        self, session: AsyncSession, market_db_id: int
    ) -> int:
        """Return the immutable version currently backing ``pm_market_current``."""
        result = await session.execute(
            text(
                "SELECT COALESCE(MAX(version_no), 0) FROM trading.pm_market_versions "
                "WHERE market_id=:m"
            ),
            {"m": market_db_id},
        )
        return int(result.scalar_one())

    async def append_market_version(
        self,
        session: AsyncSession,
        *,
        market_db_id: int,
        question: str | None,
        description: str | None,
        rules: str | None,
        resolution_source: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        active: bool | None,
        closed: bool | None,
        archived: bool | None,
        accepting_orders: bool | None,
        enable_order_book: bool | None,
        neg_risk: bool | None,
        observed_at: datetime,
        received_at: datetime,
        raw_artifact_ref: str | None,
        normalized_hash: str,
    ) -> int:
        """version_no = MAX+1；normalized_hash 去重由调用方（Logic）检查。"""
        result = await session.execute(
            text(
                "INSERT INTO trading.pm_market_versions "
                "(market_id, version_no, question, description, rules, resolution_source, "
                " start_date, end_date, active, closed, archived, accepting_orders, "
                " enable_order_book, neg_risk, observed_at, received_at, raw_artifact_ref, "
                " normalized_hash) "
                "VALUES (:m, "
                "        (SELECT COALESCE(MAX(version_no),0)+1 FROM trading.pm_market_versions "
                "         WHERE market_id=:m), "
                "        :q, :d, :r, :rs, :sd, :ed, :a, :c, :arc, :ao, :eo, :neg, "
                "        :ob, :rc, :ar, :nh) "
                "RETURNING id"
            ),
            {
                "m": market_db_id,
                "q": question,
                "d": description,
                "r": rules,
                "rs": resolution_source,
                "sd": start_date,
                "ed": end_date,
                "a": active,
                "c": closed,
                "arc": archived,
                "ao": accepting_orders,
                "eo": enable_order_book,
                "neg": neg_risk,
                "ob": observed_at,
                "rc": received_at,
                "ar": raw_artifact_ref,
                "nh": normalized_hash,
            },
        )
        return result.scalar_one()

    async def upsert_token(
        self,
        session: AsyncSession,
        *,
        token_id: str,
        market_db_id: int,
        outcome_index: int,
        outcome_label: str | None,
        price_hint: Any | None,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.pm_tokens "
                "(token_id, market_id, outcome_index, outcome_label, price_hint) "
                "VALUES (:tid, :m, :oi, :ol, :ph) "
                "ON CONFLICT (token_id) DO UPDATE SET "
                "  market_id=EXCLUDED.market_id, outcome_index=EXCLUDED.outcome_index, "
                "  outcome_label=EXCLUDED.outcome_label, price_hint=EXCLUDED.price_hint "
                "RETURNING id"
            ),
            {
                "tid": token_id,
                "m": market_db_id,
                "oi": outcome_index,
                "ol": outcome_label,
                "ph": price_hint,
            },
        )
        return result.scalar_one()

    async def tokens_for_market(
        self, session: AsyncSession, market_db_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT id, token_id, outcome_index, outcome_label, price_hint "
                "FROM trading.pm_tokens WHERE market_id=:m ORDER BY outcome_index"
            ),
            {"m": market_db_id},
        )
        return _rows(result)

    async def append_token_version(
        self,
        session: AsyncSession,
        *,
        token_db_id: int,
        outcome_index: int,
        outcome_label: str | None,
        price_hint: Any | None,
        observed_at: datetime,
        received_at: datetime,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.pm_token_versions "
                "(token_id, version_no, outcome_index, outcome_label, price_hint, "
                " observed_at, received_at) "
                "VALUES (:t, "
                "        (SELECT COALESCE(MAX(version_no),0)+1 FROM trading.pm_token_versions "
                "         WHERE token_id=:t), "
                "        :oi, :ol, :ph, :ob, :rc)"
            ),
            {
                "t": token_db_id,
                "oi": outcome_index,
                "ol": outcome_label,
                "ph": price_hint,
                "ob": observed_at,
                "rc": received_at,
            },
        )

    async def latest_token_version(
        self, session: AsyncSession, token_db_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT version_no, outcome_index, outcome_label, price_hint "
                "FROM trading.pm_token_versions WHERE token_id=:t "
                "ORDER BY version_no DESC LIMIT 1"
            ),
            {"t": token_db_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def insert_lifecycle_event(
        self,
        session: AsyncSession,
        *,
        market_db_id: int,
        event_type: str,
        provider_event_time: datetime | None,
        observed_at: datetime,
        received_at: datetime,
        payload_hash: str,
        raw_artifact_ref: str | None,
    ) -> bool:
        """内容相同时刻去重（unique）；返回是否新插入。"""
        if event_type not in LIFECYCLE_TYPES:
            raise ValueError(f"unknown lifecycle type: {event_type!r}")
        result = await session.execute(
            text(
                "INSERT INTO trading.pm_market_lifecycle_events "
                "(market_id, event_type, provider_event_time, observed_at, received_at, "
                " payload_hash, raw_artifact_ref) "
                "VALUES (:m, :et, :pt, :ob, :rc, :ph, :ar) "
                "ON CONFLICT (market_id, event_type, observed_at, payload_hash) DO NOTHING"
            ),
            {
                "m": market_db_id,
                "et": event_type,
                "pt": provider_event_time,
                "ob": observed_at,
                "rc": received_at,
                "ph": payload_hash,
                "ar": raw_artifact_ref,
            },
        )
        return result.rowcount == 1

    # ---------------- current projection（CAS）----------------

    async def set_market_current(
        self,
        session: AsyncSession,
        *,
        market_db_id: int,
        condition_id: str | None,
        gamma_market_id: str,
        tokens_ok: bool,
        mapping_state: str,
        eligible: bool,
        current_version_no: int,
        observed_at: datetime,
        content_hash: str | None,
    ) -> bool:
        """INSERT..ON CONFLICT + observed_at CAS：旧帧/乱序不覆盖（返回 False 被拒）。"""
        if mapping_state not in MAPPING_STATES:
            raise ValueError(f"unknown mapping_state: {mapping_state!r}")
        result = await session.execute(
            text(
                "INSERT INTO trading.pm_market_current "
                "(market_id, condition_id, gamma_market_id, tokens_ok, mapping_state, eligible, "
                " current_version_no, observed_at, content_hash) "
                "VALUES (:m, :cid, :gmid, :tok, :ms, :el, :v, :ob, :ch) "
                "ON CONFLICT (market_id) DO UPDATE SET "
                "  condition_id=EXCLUDED.condition_id, gamma_market_id=EXCLUDED.gamma_market_id, "
                "  tokens_ok=EXCLUDED.tokens_ok, mapping_state=EXCLUDED.mapping_state, "
                "  eligible=EXCLUDED.eligible, current_version_no=EXCLUDED.current_version_no, "
                "  observed_at=EXCLUDED.observed_at, content_hash=EXCLUDED.content_hash "
                "WHERE trading.pm_market_current.observed_at <= EXCLUDED.observed_at "
                "RETURNING id"
            ),
            {
                "m": market_db_id,
                "cid": condition_id,
                "gmid": gamma_market_id,
                "tok": tokens_ok,
                "ms": mapping_state,
                "el": eligible,
                "v": current_version_no,
                "ob": observed_at,
                "ch": content_hash,
            },
        )
        return result.first() is not None

    async def get_market_current(
        self, session: AsyncSession, market_db_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT market_id, condition_id, gamma_market_id, tokens_ok, mapping_state, "
                "       eligible, current_version_no, observed_at, content_hash "
                "FROM trading.pm_market_current WHERE market_id=:m"
            ),
            {"m": market_db_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def invalidate_absent_markets(
        self,
        session: AsyncSession,
        *,
        present_gamma_market_ids: list[str],
        observed_at: datetime,
    ) -> int:
        """Fail closed when an item disappears from a COMPLETE active-universe frame.

        Gamma ``closed=false`` removes a market once it closes.  Absence is not a
        resolution label, but it *is* sufficient to revoke eligibility until a detail
        refresh proves the new lifecycle state.  This prevents a previously eligible,
        now-closed market from remaining tradeable forever.
        """
        result = await session.execute(
            text(
                "UPDATE trading.pm_market_current SET eligible=false, observed_at=:ob "
                "WHERE eligible "
                "AND observed_at <= :ob "
                "AND gamma_market_id NOT IN ("
                "  SELECT jsonb_array_elements_text(CAST(:present AS jsonb))"
                ")"
            ),
            {
                "ob": observed_at,
                "present": json.dumps(sorted(set(present_gamma_market_ids))),
            },
        )
        return int(result.rowcount or 0)
