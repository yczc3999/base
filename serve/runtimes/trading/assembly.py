"""Trading 常驻运行时装配（WP-07C）。

把 §3.1 的常驻 worker 映射到 §8 的 runtime 文件，并构造每个 runtime 的 build 工厂。
这是唯一给 :class:`runtimes.trading.supervisor.RuntimeSupervisor` 注册 spec 的地方——
显式注册，不走 Base 任务扫描。

§3.1 八 worker → §8 七文件映射（本模块注释即权威映射）：

- REST universe scheduler → ``market_ingest``（``UniverseIngestor.run_once`` 循环）
- Market WS consumer → ``market_ingest``（``BookWsIngestor.run_epoch``）
- Cohort/R0 + Contract/Opportunity coordinator + Research + Blind forecast +
  Reveal/decision → ``cognition``（``CognitionRuntime.run_cognition_chain``；§3.1 的
  3/4/5/6/7 五个 worker 是这条认知链的阶段，非独立进程）
- Label/evaluation worker → ``evaluation``（``EvaluationRuntime``）
- 执行/心跳（P-exec-readiness）→ ``execution``（Shadow/Private/UserWs runtimes）
- 对账/链恢复 → ``reconciliation``（``ReconciliationRuntime``）
- 回放（P3）→ ``replay``（``ReplayRuntime``）
- 传输 → ``outbox``（Publisher/Sweeper/Consumer 常驻循环）

依赖重型 runtime（execution/cognition 需要 provider 凭证、网关、vault）在本 WP 中
**注册但其 build 工厂在缺依赖时 fail closed**（记日志并 fence），不伪造可用状态；
outbox 与 reconciliation 这类纯 DB/Redis 驱动的 runtime 完整装配并可常驻。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.outbox.repository import OutboxRepository

from runtimes.trading._dispatch import ALL_TOPICS, TradingEventDispatch
from runtimes.trading.outbox import (
    OutboxConsumerRuntime,
    OutboxLoopPolicy,
    OutboxPublisherRuntime,
    OutboxSweeperRuntime,
)
from runtimes.trading.supervisor import RuntimeSpec, SupervisorContext

logger = logging.getLogger(__name__)


def _outbox_specs() -> list[RuntimeSpec]:
    """outbox 三件套：publisher / sweeper / consumer 各自独立常驻任务。"""
    policy = OutboxLoopPolicy()

    def build_publisher(ctx: SupervisorContext):
        rt = OutboxPublisherRuntime(
            ctx.session_factory_for("outbox"),
            ctx.control_redis,
            owner="outbox:publisher",
            policy=policy,
        )
        return rt.run

    def build_sweeper(ctx: SupervisorContext):
        rt = OutboxSweeperRuntime(
            ctx.session_factory_for("outbox"), policy=policy
        )
        return rt.run

    def build_consumer(ctx: SupervisorContext):
        dispatch = ctx.config.get("dispatch")
        if dispatch is None:
            raise RuntimeError("outbox_consumer_requires_dispatch")
        rt = OutboxConsumerRuntime(
            ctx.session_factory_for("outbox"),
            ctx.control_redis,
            dispatch,
            consumer_id="outbox:consumer",
            topics=ALL_TOPICS,
            policy=policy,
        )
        return rt.run

    return [
        RuntimeSpec("outbox-publisher", "outbox", build_publisher),
        RuntimeSpec("outbox-sweeper", "outbox", build_sweeper),
        RuntimeSpec("outbox-consumer", "outbox", build_consumer),
    ]


def build_dispatch(ctx: SupervisorContext) -> TradingEventDispatch:
    """构造 trading 事件分发器（5 个域 handler）。依赖由 ctx 注入。

    Repository 全部用默认构造（无参）；需要 provider/vault 的 Logic（execution 私有链）
    在本 WP 只装 shadow 路径，私有执行逻辑由后续 checkpoint 注入。
    """
    from app.handlers.trading.cognition import CognitionHandler
    from app.handlers.trading.decision import DecisionHandler
    from app.handlers.trading.evaluation import EvaluationHandler
    from app.handlers.trading.execution import ExecutionHandler
    from app.handlers.trading.settlement import SettlementHandler
    from app.logics.trading.decision import DecisionLogic
    from app.logics.trading.evaluation import EvaluationLogic
    from app.logics.trading.evidence import EvidenceLogic
    from app.logics.trading.execution import ShadowExecutionLogic
    from app.logics.trading.forecast import ForecastLogic
    from app.logics.trading.settlement import SettlementLogic
    from app.repositories.trading.decision import DecisionRepository
    from app.repositories.trading.execution import ExecutionRepository
    from app.repositories.trading.ledger import LedgerRepository
    from app.repositories.trading.forecast import ForecastRepository
    from app.repositories.trading.workflow import WorkflowRepository

    forecast_repo = ForecastRepository()
    workflow_repo = WorkflowRepository()
    cognition = CognitionHandler(
        EvidenceLogic(forecast_repo, workflow_repo),
        ForecastLogic(forecast_repo, workflow_repo),
        ctx.artifacts,
    )
    decision = DecisionHandler(DecisionLogic(DecisionRepository(), workflow_repo))
    evaluation = EvaluationHandler(EvaluationLogic())
    execution = ExecutionHandler(
        ShadowExecutionLogic(ExecutionRepository(), LedgerRepository())
    )
    settlement = SettlementHandler(SettlementLogic(artifact_store=ctx.artifacts))
    return TradingEventDispatch(
        cognition=cognition,
        decision=decision,
        evaluation=evaluation,
        execution=execution,
        settlement=settlement,
    )


def _build_universe_ingestor(ctx: SupervisorContext, config_release_id: int):
    """装配 UniverseIngestor（Stage 0 感知）。gamma 走公网 REST，无需凭证。"""
    from app.logics.trading.universe import UniverseLogic
    from app.outbox.repository import OutboxRepository
    from app.repositories.trading.market import MarketRepository
    from app.repositories.trading.market_stream import MarketStreamRepository
    from app.services.polymarket import PolymarketService
    from app.db.uow import UnitOfWork
    from runtimes.trading.market_ingest import UniverseIngestor

    market_repo = MarketRepository()
    stream_repo = MarketStreamRepository()
    service = PolymarketService()  # 公网 Gamma，无需 key
    session_factory = ctx.session_factory_for("market")

    def uow_factory():
        return UnitOfWork(session_factory)

    return UniverseIngestor(
        gamma=service.gamma(),
        artifacts=ctx.artifacts,
        uow_factory=uow_factory,
        market_repo=market_repo,
        stream_repo=stream_repo,
        universe=UniverseLogic(market_repo),
        outbox_repo=OutboxRepository(),
        config_release_id=config_release_id,
    )


def _pipeline_spec() -> RuntimeSpec:
    """pipeline driver：sensing + screening（G0/R0）。AI 段默认门控。"""
    from app.config import settings
    from runtimes.trading.pipeline import PipelineDriver, PipelinePolicy

    def build(ctx: SupervisorContext):
        policy = PipelinePolicy(
            ai_enabled=getattr(settings, "PM_V2_PIPELINE_AI_ENABLED", False),
            screen_enabled=True,
            sense_enabled=True,
        )
        from runtimes.trading.seed import ensure_pipeline_seed

        # 懒装配：种子 + ingestor 在 driver 首次 run 时建立（build 不在 async 上下文）。
        class _LazyPipeline:
            async def run(self, stop_event):
                from app.db.uow import UnitOfWork
                async with UnitOfWork(ctx.session_factory_for("market")) as uow:
                    seed = await ensure_pipeline_seed(uow.session)
                ingestor = _build_universe_ingestor(ctx, seed.release_id)
                driver = PipelineDriver(
                    sessions_factory=ctx.session_factory_for,
                    universe_ingestor=ingestor,
                    cognition_runtime=None,  # AI 段后续接（ai_enabled 门控）
                    policy=policy,
                )
                await driver.run(stop_event)

        return _LazyPipeline().run

    return RuntimeSpec("pipeline", "market", build)


def default_specs() -> list[RuntimeSpec]:
    """默认注册的全部常驻 runtime：outbox 传输三件套 + pipeline 驱动器（感知+筛选）。

    cognition/execution/evaluation/reconciliation/replay 的 AI/vault 依赖在 pipeline
    ai_enabled 放行后接入（见 pipeline.py Stage 2+）。
    """
    return [*_outbox_specs(), _pipeline_spec()]
