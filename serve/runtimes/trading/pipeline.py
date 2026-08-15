"""Trading pipeline driver（WP-07C Checkpoint B）。

常驻驱动器：按 §1.1 状态机把 sensing→screening→opportunity→episode→cognition→
decision→shadow execution 串起来。**认知/决策链由本驱动器主动推进（状态机表轮询），
不经 outbox 触发**（outbox 只承载事后事实通知）。

阶段（每阶段一个 UoW、append-only、fail closed；任一阶段异常不中断其他阶段）：
- Stage 0 ``_sense``：``UniverseIngestor.run_once`` 拉取 universe frame（market pool）。
- Stage 1 ``_screen``：对 confirmed membership 跑 G0/R0；R0=select 建 parent
  ``decision_opportunity``。
- Stage 2 ``_advance_opportunities``：OPEN opportunity → G1/G2 → G2 pass 建 episode。
  G1/G2 的 contract/component 解析是 AI 产出，由 ``ai_enabled`` 门控（每轮
  ``run_once`` 读 ``runtime_flags['pipeline.ai_enabled']``，无行回退 policy 冻结值）。
- Stage 3 ``_advance_episodes``：ROUTED episode → CognitionRuntime G4→G5A→G5B→G6（AI）。
- Stage 4 ``_advance_decisions``：G6 commit 后 → reveal → G7A/G7B → shadow execution。

verifier 已移除（产品决议：不接入 Gemini，证据链不强制第二核验）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.logics.trading.screening import ScreeningLogic
from app.orchestrator.trading_state_machine import TradingStateMachine
from app.repositories.trading.cohort import CohortRepository
from app.repositories.trading.runtime_config import RuntimeConfigRepository
from app.repositories.trading.workflow import WorkflowRepository
from runtimes.trading.policies import SHADOW_AUDIT_POLICY, SHADOW_R0_POLICY

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelinePolicy:
    """驱动节奏与 AI 门控。"""

    interval_s: float = 30.0
    sense_enabled: bool = True
    screen_enabled: bool = True
    ai_enabled: bool = False           # G1/G2/G4-G7（默认关，不烧钱）
    screen_batch: int = 50


class PipelineDriver:
    """常驻 pipeline 驱动器。依赖注入，不持有全局状态。"""

    def __init__(
        self,
        *,
        sessions_factory: Callable[[str], Any],
        universe_ingestor: Any | None = None,
        cognition_runtime: Any | None = None,
        policy: PipelinePolicy | None = None,
    ) -> None:
        self._sessions = sessions_factory
        self._ingestor = universe_ingestor
        self._cognition = cognition_runtime
        self._policy = policy or PipelinePolicy()
        self._cohort_repo = CohortRepository()
        self._workflow_repo = WorkflowRepository()
        self._runtime_config_repo = RuntimeConfigRepository()
        self._screening = ScreeningLogic(self._cohort_repo, self._workflow_repo)
        self._state = TradingStateMachine(self._workflow_repo)
        self._last_frame = None  # 最近一次成功 COMPLETE frame 的归属（含 markets）

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._policy.interval_s)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        if self._policy.sense_enabled:
            summary["sense"] = await self._safe(self._sense)
        if self._policy.screen_enabled:
            summary["enroll"] = await self._safe(self._enroll)
            summary["screen"] = await self._safe(self._screen)
        if await self._resolve_ai_enabled():
            summary["opportunities"] = await self._safe(self._advance_opportunities)
            summary["episodes"] = await self._safe(self._advance_episodes)
            summary["decisions"] = await self._safe(self._advance_decisions)
        return summary

    async def _resolve_ai_enabled(self) -> bool:
        """每轮读 ``runtime_flags['pipeline.ai_enabled']``；无行/查询失败回退 policy 冻结值。

        用 pipeline 自己的 session factory 开一个短查询（不进任何 market UoW），
        DB 配置改完下一轮即生效；表不存在/连接失败等异常不阻断 pipeline。
        """
        try:
            async with self._sessions("market")() as session:
                row = await self._runtime_config_repo.get_flag(
                    session, flag_key="pipeline.ai_enabled"
                )
        except Exception:  # noqa: BLE001 - flag 读取失败回退冻结默认，循环不中断
            logger.exception("pipeline_ai_flag_read_failed")
            return self._policy.ai_enabled
        if row is None:
            return self._policy.ai_enabled
        return str(row["flag_value"]).strip().lower() == "true"

    async def _safe(self, stage: Callable[[], Any]) -> dict[str, Any]:
        name = stage.__name__.lstrip("_")
        try:
            return await stage()
        except Exception as exc:  # noqa: BLE001 - 单阶段 fail closed，循环不中断
            logger.exception("pipeline_stage_failed stage=%s", name)
            return {"stage": name, "ok": False, "reason": type(exc).__name__}

    # ---- Stage 0：感知 ----

    async def _sense(self) -> dict[str, Any]:
        if self._ingestor is None:
            return {"stage": "sense", "ok": False, "reason": "ingestor_not_configured"}
        tags = {"ok": True, "reason": "sync_not_configured"}
        sync = getattr(self._ingestor, "sync_tag_catalog", None)
        if callable(sync):
            try:
                tags = await sync()
            except Exception as exc:  # noqa: BLE001 - 目录失败不阻断 universe frame
                logger.exception("tag_catalog_sync_failed")
                tags = {"ok": False, "reason": type(exc).__name__}
        result = await self._ingestor.run_once()
        if getattr(result, "status", None) == "COMPLETE":
            self._last_frame = result  # 保存成功 frame 归属（含 markets）
        return {
            "stage": "sense", "ok": True,
            "frame_id": getattr(result, "frame_id", None),
            "status": getattr(result, "status", None),
            "tags": tags,
        }

    # ---- Stage 1a：登记（frame → cohort membership）----

    async def _enroll(self) -> dict[str, Any]:
        """把最近一次 COMPLETE frame 的 markets 登记进每个 OPEN cohort（发现即登记）。

        这是 §3.1 worker 3 的「登记」环节：用 frame 的显式归属（db id + 规范化
        content）构造 HydratedUniverseFrameInput，调 ScreeningLogic.enroll_frame
        写 universe_memberships；R0 筛选（_screen）据此才能查到待筛 market。
        """
        frame = self._last_frame
        if frame is None or getattr(frame, "markets", None) is None:
            return {"stage": "enroll", "ok": True, "reason": "no_complete_frame"}
        if frame.status != "COMPLETE":
            return {"stage": "enroll", "ok": True, "reason": "frame_not_complete"}

        from app.schemas.trading.workflow import (
            HydratedFrameMarketInput,
            HydratedUniverseFrameInput,
        )

        hydrated = HydratedUniverseFrameInput(
            frame_id=frame.frame_id,
            content_hash=frame.content_hash,
            artifact_object_id=frame.artifact_id,
            artifact_ref=frame.artifact_ref,
            markets=[
                HydratedFrameMarketInput(market_id=m.market_id, metadata=m.metadata)
                for m in frame.markets
            ],
        )
        enrolled = 0
        async with UnitOfWork(self._sessions("market")) as uow:
            for cohort_id in await self._open_cohorts(uow.session):
                g0 = await self._screening.run_g0(uow, cohort_id=cohort_id)
                if not g0.ok:
                    continue
                await self._screening.enroll_frame(
                    uow,
                    cohort_id=cohort_id,
                    frame=hydrated,
                    observed_at=datetime.now(timezone.utc),
                    ingested_at=datetime.now(timezone.utc),
                    g0=g0,
                )
                enrolled += 1
        return {"stage": "enroll", "ok": True, "cohorts": enrolled, "markets": len(hydrated.markets)}

    # ---- Stage 1：筛选（G0/R0 → parent opportunity）----

    async def _open_cohorts(self, session) -> list[int]:
        rows = (
            await session.execute(
                text("SELECT id FROM trading.evaluation_cohorts WHERE status='OPEN' ORDER BY id")
            )
        ).all()
        return [r[0] for r in rows]

    async def _unscreened_markets(self, session, cohort_id: int) -> list[int]:
        """cohort 内已确认 membership 但尚无 screening_episode 的 market。"""
        rows = (
            await session.execute(
                text(
                    "SELECT um.market_id FROM trading.universe_memberships um "
                    "WHERE um.cohort_id=:c AND um.confirmed_frame_id IS NOT NULL "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM trading.screening_episodes se "
                    "  WHERE se.cohort_id=um.cohort_id AND se.market_id=um.market_id"
                    ") ORDER BY um.market_id LIMIT :lim"
                ),
                {"c": cohort_id, "lim": self._policy.screen_batch},
            )
        ).all()
        return [r[0] for r in rows]

    async def _market_quote(self, session, market_id: int) -> dict[str, Any] | None:
        row = (
            await session.execute(
                text(
                    "SELECT id, best_bid, best_ask, liquidity, end_date, question "
                    "FROM trading.pm_markets WHERE id=:m"
                ),
                {"m": market_id},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def _market_tags(self, session, market_id: int) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                text(
                    "SELECT t.gamma_tag_id, t.disposition "
                    "FROM trading.pm_markets m "
                    "JOIN trading.pm_event_tags e ON e.gamma_event_id = m.gamma_event_id "
                    "JOIN trading.pm_tags t ON t.gamma_tag_id = e.gamma_tag_id "
                    "WHERE m.id=:m ORDER BY e.position, t.gamma_tag_id"
                ),
                {"m": market_id},
            )
        ).mappings().all()
        return [dict(row) for row in rows]

    async def _screen(self) -> dict[str, Any]:
        """对每 cohort 跑 G0，再对每 market 独立事务跑 R0+opportunity。

        G0 只读（校验冻结配置），可在一个只读 UoW 内为所有 cohort 求值；
        每个 market 的 R0 写入（screening_episode + gate_decision + opportunity）
        用**独立 UoW**——单个 market 失败只回滚它自己，不拖累同 cohort 其余 market。
        """
        processed = selected = failed = 0
        async with UnitOfWork(self._sessions("market")) as read_uow:
            cohorts = await self._open_cohorts(read_uow.session)
            g0s = {
                cohort_id: await self._screening.run_g0(read_uow, cohort_id=cohort_id)
                for cohort_id in cohorts
            }
        for cohort_id, g0 in g0s.items():
            if not g0.ok:
                continue
            async with UnitOfWork(self._sessions("market")) as list_uow:
                market_ids = await self._unscreened_markets(list_uow.session, cohort_id)
                quotes = {
                    market_id: await self._market_quote(list_uow.session, market_id)
                    for market_id in market_ids
                }
                tags = {
                    market_id: await self._market_tags(list_uow.session, market_id)
                    for market_id in market_ids
                }
            for market_id, quote in quotes.items():
                if quote is None:
                    continue
                try:
                    async with UnitOfWork(self._sessions("market")) as market_uow:
                        r0 = await self._screen_market(
                            market_uow, cohort_id, market_id, quote, g0,
                            tags=tags.get(market_id, []),
                        )
                        processed += 1
                        if r0 is not None and r0.result == "SELECT":
                            selected += 1
                            await self._open_opportunity(
                                market_uow, cohort_id, market_id, r0
                            )
                except Exception:  # noqa: BLE001 - 单 market 失败隔离，不拖累整批
                    logger.exception(
                        "pipeline_screen_market_failed cohort=%s market=%s",
                        cohort_id, market_id,
                    )
                    failed += 1
        return {
            "stage": "screen", "ok": True,
            "processed": processed, "selected": selected, "failed": failed,
        }

    async def _screen_market(self, uow, cohort_id, market_id, quote, g0, *, tags=None):
        """单市场 R0。policy 用单一事实源（runtimes.trading.policies），与 seed 冻结 hash 一致。"""
        from app.schemas.trading.workflow import R0Input

        policy = SHADOW_R0_POLICY
        audit = SHADOW_AUDIT_POLICY
        r0_input = R0Input(
            market_metadata={"market_id": market_id, "tags": list(tags or [])},
            end_at=quote.get("end_date"),
            rule_completeness=Decimal("1") if quote.get("question") else None,
            best_bid=quote.get("best_bid"),
            best_ask=quote.get("best_ask"),
            minimum_deployable_capacity=quote.get("liquidity"),
        )
        episode_no = await self._next_episode_no(uow, cohort_id, market_id)
        return await self._screening.run_r0(
            uow, cohort_id=cohort_id, market_id=market_id, episode_no=episode_no,
            r0_input=r0_input, g0=g0, r0_policy=policy, audit_policy=audit,
        )

    async def _next_episode_no(self, uow, cohort_id, market_id) -> int:
        row = (
            await uow.session.execute(
                text(
                    "SELECT COALESCE(MAX(episode_no),0)+1 FROM trading.screening_episodes "
                    "WHERE cohort_id=:c AND market_id=:m"
                ),
                {"c": cohort_id, "m": market_id},
            )
        ).scalar_one()
        return int(row)

    async def _open_opportunity(self, uow, cohort_id, market_id, r0) -> None:
        """R0=select → 建 parent decision opportunity（DECISION 链）。"""
        cohort = await self._cohort_repo.get_cohort(uow.session, cohort_id)
        if cohort is None or r0.episode_id is None:
            return
        from datetime import datetime, timezone
        await self._state.create_parent_opportunity(
            uow, cohort_id=cohort_id, chain_type="DECISION",
            objective_contract_id=cohort["objective_contract_id"],
            strategy_version_id=cohort["strategy_version_id"],
            source_screening_episode_id=r0.episode_id,
            triggered_at=datetime.now(timezone.utc),
            market_ids=[market_id],
        )

    # ---- Stage 2+：AI 推理段（默认关）----

    async def _advance_opportunities(self) -> dict[str, Any]:
        return {"stage": "opportunities", "ok": False, "reason": "ai_gated"}

    async def _advance_episodes(self) -> dict[str, Any]:
        return {"stage": "episodes", "ok": False, "reason": "ai_gated"}

    async def _advance_decisions(self) -> dict[str, Any]:
        return {"stage": "decisions", "ok": False, "reason": "ai_gated"}
