"""Trading 常驻运行时 supervisor（WP-07C）。

显式注册、隔离启动、首失败 fence、逆序优雅关闭。**不依赖 Base `worker.py`
的非递归任务扫描**（v2-implementation-contract §8 硬约束）。

- 每个 runtime 一个 :class:`RuntimeSpec`，声明 name / build 工厂 / 专属 DB pool
  profile / 是否参与 health。
- ``async main()``：按 spec 顺序 build → ``asyncio.create_task`` 启动 → 任一 runtime
  首失败即触发全组 fence（设置共享 stop_event，对齐 P-execution-readiness
  「首失败 fencing」），再逆序 cancel + await。
- pool 隔离：每 runtime 用各自 profile 的 ``engines.session_factory(profile)``；
  ``execution`` 不与其他 runtime 共用 pool/并发池。
"""

from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# build 工厂签名：注入 session_factory(profile) 与 control/cache redis 与共享 stop_event，
# 返回一个 ``async def run(stop_event)`` 协程工厂或已构造的 runtime 对象（带 .run）。
RuntimeRunner = Callable[[asyncio.Event], Awaitable[None]]


@dataclass(frozen=True)
class RuntimeSpec:
    """单个常驻 runtime 的注册项。

    - ``build``: 同步工厂，接收 supervisor 上下文，返回 ``RuntimeRunner``。
    - ``pool_profile``: 该 runtime 独占的 DB pool profile 名（api/market/execution/
      cognition/evaluation/replay/reconciliation/outbox）。
    """

    name: str
    pool_profile: str
    build: Callable[["SupervisorContext"], RuntimeRunner]


@dataclass
class SupervisorContext:
    """传给每个 build 工厂的依赖容器；由 supervisor 在启动前装配。"""

    session_factory_for: Callable[[str], Any]  # profile -> async_sessionmaker
    control_redis: Any
    cache_redis: Any
    artifacts: Any
    gateway: Any
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunningRuntime:
    spec: RuntimeSpec
    runner: RuntimeRunner
    task: asyncio.Task | None = None


class RuntimeSupervisor:
    """注册并驱动一组常驻 runtime。"""

    def __init__(self, context: SupervisorContext) -> None:
        self._ctx = context
        self._specs: list[RuntimeSpec] = []
        self._running: list[RunningRuntime] = []
        self._stop_event = asyncio.Event()

    def register(self, spec: RuntimeSpec) -> None:
        if any(s.name == spec.name for s in self._specs):
            raise ValueError(f"duplicate_runtime:{spec.name}")
        self._specs.append(spec)

    @property
    def stop_event(self) -> asyncio.Event:
        return self._stop_event

    def registry_snapshot(self) -> list[dict[str, str]]:
        """dry-run / health 用：注册清单（name + pool_profile）。"""
        return [
            {"name": s.name, "pool_profile": s.pool_profile} for s in self._specs
        ]

    def build_all(self) -> None:
        """按注册顺序 build 所有 runtime（未启动任务）。"""
        for spec in self._specs:
            runner = spec.build(self._ctx)
            self._running.append(RunningRuntime(spec=spec, runner=runner))

    async def run(self) -> int:
        """启动全部 runtime；任一失败 → fence 全组并优雅关闭。返回失败 runtime 数。"""
        if not self._running:
            self.build_all()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop_event.set)
            except NotImplementedError:  # 非 Unix / 已有关闭钩子
                pass

        async def _supervise(rr: RunningRuntime) -> None:
            try:
                await rr.runner(self._stop_event)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 首失败 fence
                logger.exception("runtime_failed name=%s", rr.spec.name)
                self._stop_event.set()

        for rr in self._running:
            rr.task = asyncio.create_task(_supervise(rr), name=rr.spec.name)
        # 等待全部结束（正常 stop_event 或 fence）
        results = await asyncio.gather(
            *(rr.task for rr in self._running), return_exceptions=True
        )
        failures = sum(
            1 for r in results if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError)
        )
        return failures

    async def shutdown(self) -> None:
        """逆序取消并等待；幂等。"""
        self._stop_event.set()
        for rr in reversed(self._running):
            if rr.task is not None and not rr.task.done():
                rr.task.cancel()
        for rr in reversed(self._running):
            if rr.task is not None:
                try:
                    await rr.task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
