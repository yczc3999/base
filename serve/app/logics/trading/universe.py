"""Universe Logic（WP-01B Checkpoint D）。

frame 是否完整、market version/disposition 如何追加、current projection 是否 eligible。

- cursor 单调链：每页 ``cursor_input`` 必须等于同 endpoint 上一页的 ``cursor_output``（首页可为 None）。
- frame 终态：events/markets 的 open+closed 四条物理 keyset 链都必须终止
  （``next_cursor=None``）才 COMPLETE，
  否则 FAILED；failed frame 不更新 current（任务 §5.4）。
- version append-only：normalized content 变化才追加新 version（幂等重放 effect=0）。
- eligible 判定 fail-closed：active/accepting_orders/enable_order_book 必须显式 true、
  closed/archived 不得 true、token 映射必须完整 YES/NO（任务 §4.2）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.db.uow import UnitOfWork
from app.repositories.trading.market import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    MarketRepository,
)
from app.schemas.polymarket.gamma import (
    GammaEvent,
    GammaMarket,
    assess_binary_market,
)

FRAME_FAILED_CURSOR_BREAK = "frame_cursor_chain_break"
FRAME_FAILED_NOT_TERMINATED = "frame_cursor_not_terminated"
FRAME_FAILED_PAGE_OVERFLOW = "frame_page_overflow"


@dataclass(frozen=True)
class UniversePolicy:
    """universe 扫描策略（显式 policy fixture，任务 §2.9）。"""

    event_page_limit: int = 500
    market_page_limit: int = 100
    max_pages_per_endpoint: int = 200
    frame_lease_s: int = 300

    def __post_init__(self) -> None:
        if isinstance(self.event_page_limit, bool) or not 1 <= self.event_page_limit <= 500:
            raise ValueError("event_page_limit_out_of_range")
        if isinstance(self.market_page_limit, bool) or not 1 <= self.market_page_limit <= 100:
            raise ValueError("market_page_limit_out_of_range")
        if isinstance(self.max_pages_per_endpoint, bool) or self.max_pages_per_endpoint <= 0:
            raise ValueError("max_pages_per_endpoint_invalid")
        if isinstance(self.frame_lease_s, bool) or self.frame_lease_s <= 0:
            raise ValueError("frame_lease_s_invalid")


@dataclass(frozen=True)
class ApplyDiffResult:
    events_upserted: int
    markets_new: int
    markets_updated: int
    versions_appended: int
    tokens_upserted: int
    eligible_count: int
    lifecycle_events: int


def market_normalized_content(market: GammaMarket) -> dict[str, Any]:
    """market version 的规范化 content（用于 normalized_hash 与 diff 判定）。"""

    def _iso(dt: datetime | None) -> str | None:
        return dt.astimezone(timezone.utc).isoformat() if dt is not None else None

    return {
        "condition_id": market.condition_id,
        "question": market.question,
        "description": getattr(market, "description", None),
        "rules": getattr(market, "rules", None),
        "resolution_source": getattr(market, "resolution_source", None),
        "active": market.active,
        "closed": market.closed,
        "archived": market.archived,
        "accepting_orders": market.accepting_orders,
        "enable_order_book": market.enable_order_book,
        "neg_risk": market.neg_risk,
        "start_date": _iso(market.start_date),
        "end_date": _iso(market.end_date),
        "clob_token_ids": market.clob_token_ids,
        "outcomes": market.outcomes,
        "outcome_prices": [str(p) for p in market.outcome_prices],
    }


def canonical_hash(content: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def market_is_eligible(
    market: GammaMarket, *, tokens_ok: bool, mapping_state: str
) -> bool:
    """fail-closed：任何未知字段都不算通过（任务 §4.2）。"""
    if not tokens_ok or mapping_state != "complete":
        return False
    if market.active is not True:
        return False
    if market.closed is True or market.archived is True:
        return False
    if market.accepting_orders is not True:
        return False
    if market.enable_order_book is not True:
        return False
    return True


class UniverseLogic:
    """frame + market master 编排；所有写经 UoW，绝不 commit。"""

    def __init__(self, market_repo: MarketRepository, policy: UniversePolicy | None = None) -> None:
        self._market = market_repo
        self._policy = policy or UniversePolicy()

    @property
    def policy(self) -> UniversePolicy:
        return self._policy

    async def begin_frame(
        self,
        uow: UnitOfWork,
        *,
        owner: str,
        started_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = started_at or datetime.now(timezone.utc)
        return await self._market.acquire_frame(
            uow.session,
            owner=owner,
            started_at=now,
            lease_expires_at=now + timedelta(seconds=self._policy.frame_lease_s),
        )

    async def record_page(
        self,
        uow: UnitOfWork,
        *,
        frame_id: int,
        endpoint: str,
        page_no: int,
        cursor_input: str | None,
        cursor_output: str | None,
        item_count: int,
        raw_artifact_id: int,
        raw_artifact_ref: str,
        raw_artifact_hash: str,
        received_at: datetime,
        owner: str,
        fencing_token: int,
    ) -> None:
        """记录一页；先验证 cursor 单调链（前一页 output == 本页 input）。"""
        prev = await self._market.latest_page_for_endpoint(uow.session, frame_id, endpoint)
        if prev is not None:
            if prev["cursor_output"] != cursor_input:
                raise ValueError(FRAME_FAILED_CURSOR_BREAK)
        else:
            if cursor_input is not None:
                # 首页 input 必须为 None（从首页开始）
                raise ValueError(FRAME_FAILED_CURSOR_BREAK)
        await self._market.append_page(
            uow.session,
            frame_id=frame_id,
            page_no=page_no,
            endpoint=endpoint,
            cursor_input=cursor_input,
            cursor_output=cursor_output,
            item_count=item_count,
            raw_artifact_id=raw_artifact_id,
            raw_artifact_ref=raw_artifact_ref,
            raw_artifact_hash=raw_artifact_hash,
            received_at=received_at,
            owner=owner,
            fencing_token=fencing_token,
        )

    async def finalize_frame(
        self,
        uow: UnitOfWork,
        *,
        frame_id: int,
        events_terminal: bool,
        markets_terminal: bool,
        total_events: int,
        total_markets: int,
        content_hash: str | None = None,
        artifact_id: int | None = None,
        artifact_ref: str | None = None,
        error_reason: str | None = None,
        owner: str,
        fencing_token: int,
        completed_at: datetime,
    ) -> str:
        """open+closed 四条物理链均终止才 COMPLETE。

        ``events_terminal`` / ``markets_terminal`` 是 repository 已核验同类两条
        cursor 链后的分组结果；数据库 finalize guard 仍会复核四条完整链。
        """
        status = STATUS_COMPLETE if (events_terminal and markets_terminal) else STATUS_FAILED
        if status == STATUS_FAILED and error_reason is None:
            error_reason = FRAME_FAILED_NOT_TERMINATED
        ok = await self._market.finalize_frame(
            uow.session,
            frame_id=frame_id,
            status=status,
            total_events=total_events,
            total_markets=total_markets,
            content_hash=content_hash,
            artifact_id=artifact_id,
            artifact_ref=artifact_ref,
            error_reason=error_reason,
            owner=owner,
            fencing_token=fencing_token,
            completed_at=completed_at,
        )
        if not ok:
            raise RuntimeError("frame already finalized")
        return status

    async def apply_frame_diff(
        self,
        uow: UnitOfWork,
        *,
        events: list[GammaEvent],
        markets: list[GammaMarket],
        observed_at: datetime,
        received_at: datetime,
        raw_artifact_ref: str | None,
        event_artifact_refs: dict[str, str] | None = None,
        market_artifact_refs: dict[str, str] | None = None,
        market_event_ids: dict[str, str] | None = None,
    ) -> ApplyDiffResult:
        """把一帧解析后的 events/markets 应用到 master + version + token + current。

        idempotent：相同 normalized content 重放不追加 version、不产生 lifecycle 事件
        （effect=0）。``pm_market_current`` 用 observed_at CAS（旧帧不覆盖）。
        """
        session = uow.session
        events_upserted = 0
        markets_new = 0
        markets_updated = 0
        versions_appended = 0
        tokens_upserted = 0
        eligible_count = 0
        lifecycle_events = 0
        event_artifact_refs = event_artifact_refs or {}
        market_artifact_refs = market_artifact_refs or {}
        market_event_ids = market_event_ids or {}

        for event in events:
            await self._market.upsert_event(
                session,
                gamma_event_id=event.id,
                slug=event.slug,
                title=event.title,
                description=event.description,
                start_date=event.start_date,
                end_date=event.end_date,
                active=event.active,
                closed=event.closed,
                archived=event.archived,
                volume=event.volume,
                liquidity=event.liquidity,
                content_hash=canonical_hash(
                    {"title": event.title, "slug": event.slug, "active": event.active, "closed": event.closed}
                ),
                raw_artifact_ref=event_artifact_refs.get(event.id, raw_artifact_ref),
            )
            events_upserted += 1

        for market in markets:
            existing = await self._market.get_market_by_gamma_id(session, market.id)
            is_new = existing is None
            content = market_normalized_content(market)
            content_hash = canonical_hash(content)
            db_id = await self._market.upsert_market(
                session,
                gamma_market_id=market.id,
                gamma_event_id=market_event_ids.get(market.id),
                condition_id=market.condition_id,
                question=market.question,
                slug=market.slug,
                ticker=market.ticker,
                active=market.active,
                closed=market.closed,
                archived=market.archived,
                accepting_orders=market.accepting_orders,
                enable_order_book=market.enable_order_book,
                neg_risk=market.neg_risk,
                start_date=market.start_date,
                end_date=market.end_date,
                closed_at=market.closed_time,
                volume=market.volume,
                liquidity=market.liquidity,
                spread=market.spread,
                best_bid=market.best_bid,
                best_ask=market.best_ask,
                last_trade_price=market.last_trade_price,
                content_hash=content_hash,
                raw_artifact_ref=market_artifact_refs.get(market.id, raw_artifact_ref),
            )
            if is_new:
                markets_new += 1
            else:
                markets_updated += 1

            # version：content 变化才 append
            latest_hash = await self._market.latest_market_version_hash(session, db_id)
            if latest_hash != content_hash:
                await self._market.append_market_version(
                    session,
                    market_db_id=db_id,
                    question=market.question,
                    description=getattr(market, "description", None),
                    rules=getattr(market, "rules", None),
                    resolution_source=getattr(market, "resolution_source", None),
                    start_date=market.start_date,
                    end_date=market.end_date,
                    active=market.active,
                    closed=market.closed,
                    archived=market.archived,
                    accepting_orders=market.accepting_orders,
                    enable_order_book=market.enable_order_book,
                    neg_risk=market.neg_risk,
                    observed_at=observed_at,
                    received_at=received_at,
                    raw_artifact_ref=market_artifact_refs.get(market.id, raw_artifact_ref),
                    normalized_hash=content_hash,
                )
                versions_appended += 1

            # token：按 index 绑定（0=YES, 1=NO）；price hint 取自 outcomePrices
            assessment = assess_binary_market(
                market.outcomes, market.clob_token_ids, market.outcome_prices, neg_risk=market.neg_risk
            )
            tokens_ok = assessment.complete
            for idx, token_id in enumerate(market.clob_token_ids):
                label = market.outcomes[idx] if idx < len(market.outcomes) else None
                hint = market.outcome_prices[idx] if idx < len(market.outcome_prices) else None
                token_db_id = await self._market.upsert_token(
                    session,
                    token_id=token_id,
                    market_db_id=db_id,
                    outcome_index=idx,
                    outcome_label=label,
                    price_hint=hint,
                )
                latest_token = await self._market.latest_token_version(session, token_db_id)
                if (
                    latest_token is None
                    or latest_token["outcome_index"] != idx
                    or latest_token["outcome_label"] != label
                    or latest_token["price_hint"] != hint
                ):
                    await self._market.append_token_version(
                        session,
                        token_db_id=token_db_id,
                        outcome_index=idx,
                        outcome_label=label,
                        price_hint=hint,
                        observed_at=observed_at,
                        received_at=received_at,
                    )
                tokens_ok = tokens_ok and idx in (0, 1)
                tokens_upserted += 1

            mapping_state = "complete" if assessment.complete else (
                "conflict" if assessment.reason and assessment.reason != "mapping_incomplete" else "incomplete"
            )
            eligible = market_is_eligible(market, tokens_ok=tokens_ok, mapping_state=mapping_state)
            current_version_no = await self._market.latest_market_version_no(session, db_id)

            await self._market.set_market_current(
                session,
                market_db_id=db_id,
                condition_id=market.condition_id,
                gamma_market_id=market.id,
                tokens_ok=tokens_ok,
                mapping_state=mapping_state,
                eligible=eligible,
                current_version_no=current_version_no,
                observed_at=observed_at,
                content_hash=content_hash,
            )
            if eligible:
                eligible_count += 1

            # lifecycle：created/updated/closed（payload 相同去重）
            if is_new:
                event_type = "created"
            elif existing is not None and existing["content_hash"] != content_hash:
                event_type = "closed" if (market.closed is True) else "updated"
            else:
                event_type = None
            if event_type is not None:
                inserted = await self._market.insert_lifecycle_event(
                    session,
                    market_db_id=db_id,
                    event_type=event_type,
                    provider_event_time=market.closed_time if event_type == "closed" else None,
                    observed_at=observed_at,
                    received_at=received_at,
                    payload_hash=content_hash,
                    raw_artifact_ref=market_artifact_refs.get(market.id, raw_artifact_ref),
                )
                if inserted:
                    lifecycle_events += 1

        # ``closed=false`` 的 COMPLETE frame 会让刚关闭的市场从结果集中消失；
        # 缺失不能证明结算，但必须立刻撤销旧 eligibility（V1 事故的直接防线）。
        await self._market.invalidate_absent_markets(
            session,
            present_gamma_market_ids=[market.id for market in markets],
            observed_at=observed_at,
        )

        return ApplyDiffResult(
            events_upserted=events_upserted,
            markets_new=markets_new,
            markets_updated=markets_updated,
            versions_appended=versions_appended,
            tokens_upserted=tokens_upserted,
            eligible_count=eligible_count,
            lifecycle_events=lifecycle_events,
        )
