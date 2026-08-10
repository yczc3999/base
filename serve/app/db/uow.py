"""外层事务 UnitOfWork（WP-01A-02，Checkpoint D）。

- ``async with`` 是唯一 begin/commit/rollback/close；Repository 不自行 commit。
- body 异常或 commit 异常 → rollback 并重抛；禁止嵌套静默 commit。
- after-commit hook 只在 commit 成功后运行；hook 失败**不得伪装 DB rollback**（DB 已提交，
  不重抛、不 rollback，仅记录日志），也不得用于外部投递定案。
- 网络调用绝不在数据库事务内（调用方在 commit 后、或直接使用 hook）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

AfterCommitHook = Callable[[], Awaitable[None]]


class UnitOfWork:
    """外层事务；每次进入开一个新 Session，退出时 commit/rollback + close。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._hooks: list[AfterCommitHook] = []
        self._rolled_back = False
        self._committed = False
        self._used = False

    @property
    def session(self) -> AsyncSession:
        """当前事务 Session；未进入或已退出时抛错（禁止悬挂引用）。"""
        if self._session is None:
            raise RuntimeError("uow_not_entered")
        return self._session

    @property
    def committed(self) -> bool:
        return self._committed

    @property
    def rolled_back(self) -> bool:
        return self._rolled_back

    async def __aenter__(self) -> "UnitOfWork":
        if self._session is not None:
            raise RuntimeError("uow_already_entered")
        if self._used:
            raise RuntimeError("uow_already_used")
        self._used = True
        self._session = self._session_factory()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None and not self._rolled_back:
                await self._commit()
            elif exc_type is not None:
                await self._rollback()
        finally:
            # rollback/异常路径的 hook 永远不得泄漏到后续状态。
            self._hooks.clear()
            if self._session is not None:
                await self._session.close()
                self._session = None
        # 不吞异常：body 或 commit 异常原样传播
        return False

    async def _commit(self) -> None:
        try:
            await self._session.commit()
        except BaseException:
            try:
                await self._session.rollback()
            except BaseException:  # pragma: no cover - 回滚失败不掩盖原 commit 异常
                logger.exception("uow commit failed and rollback also failed")
            self._rolled_back = True
            raise
        self._committed = True
        hooks, self._hooks = self._hooks, []
        for hook in hooks:
            await self._run_hook(hook)

    async def _run_hook(self, hook: AfterCommitHook) -> None:
        """hook 失败只记录日志：DB 已提交，不得伪装成回滚、不得重抛。"""
        try:
            await hook()
        except Exception:
            logger.exception("after-commit hook failed; db already committed, hook error not propagated")

    async def _rollback(self) -> None:
        try:
            await self._session.rollback()
        finally:
            self._rolled_back = True

    async def rollback(self) -> None:
        """显式标记回滚（如业务冲突发现）；__aexit__ 将不再 commit。"""
        if self._session is None:
            raise RuntimeError("uow_not_entered")
        await self._rollback()

    def after_commit(self, hook: AfterCommitHook) -> None:
        """注册 commit 成功后运行的 hook（只读/副作用，不得作业务投递定案）。"""
        if self._session is None or self._committed or self._rolled_back:
            raise RuntimeError("uow_not_active")
        if not callable(hook):
            raise TypeError("uow_after_commit_hook_not_callable")
        self._hooks.append(hook)


def uow_factory(session_factory: async_sessionmaker[AsyncSession]):
    """创建一个新的 UoW 实例的工厂（每次调用独立状态）。"""
    def _factory() -> UnitOfWork:
        return UnitOfWork(session_factory)
    return _factory
