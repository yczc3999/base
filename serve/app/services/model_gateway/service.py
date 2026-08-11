"""Model gateway service：只按冻结 model binding 构造 Driver（WP-02 Checkpoint B）。

禁止读取 latest 配置、禁止任意 import/model；每次调用由 Runner 提供确切的
``model_role_binding_id``，本 service 从 DB 冻结 binding 解析 requested provider/route/model
与 network/tools/capability。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.model_gateway.contracts import (
    ALLOWED_PROVIDERS,
    NETWORK_NONE,
    ModelRequest,
    ModelResponse,
)
from app.services.model_gateway.drivers import DRIVER_BY_PROVIDER, ModelDriver
from app.services.model_gateway.registry import resolve


class ModelGatewayService:
    """按冻结 binding 构造短生命周期 Driver；模块级无状态 singleton。"""

    def __init__(
        self,
        transport_factory: Callable[[str], Callable[..., Any]],
    ) -> None:
        self._transport_factory = transport_factory

    async def resolve_binding(
        self, session: AsyncSession, model_role_binding_id: int
    ) -> dict[str, Any]:
        """读取冻结 model_role_binding；不存在或非 frozen/active 拒绝。"""
        result = await session.execute(
            text(
                "SELECT id, role, provider, route, model_ref, network_policy, "
                "       allowed_tools, allowed_domains, capability, binding_version, content_hash "
                "FROM trading.model_role_bindings WHERE id=:id"
            ),
            {"id": model_role_binding_id},
        )
        row = result.mappings().first()
        if row is None:
            raise ValueError(f"model_role_binding_missing:{model_role_binding_id}")
        binding = dict(row)
        if binding["provider"] not in ALLOWED_PROVIDERS:
            raise ValueError(f"model_role_binding_provider_not_allowed:{binding['provider']}")
        resolve(binding["provider"], binding["route"], binding["model_ref"])
        return binding

    def build_driver(self, binding: dict[str, Any]) -> ModelDriver:
        provider = binding["provider"]
        driver_cls = DRIVER_BY_PROVIDER.get(provider)
        if driver_cls is None:
            raise ValueError(f"model_driver_not_registered:{provider}")
        transport = self._transport_factory(provider)
        return driver_cls(transport)

    async def execute(
        self,
        session: AsyncSession,
        *,
        model_role_binding_id: int,
        model_request: ModelRequest,
    ) -> ModelResponse:
        """解析 exact binding，结束 SELECT 事务后才执行 provider request。

        ``AsyncSession.execute(SELECT ...)`` 会隐式开启事务。若直接在其后 await HTTP，
        provider 整段延迟都会占着连接/事务。调用方必须传入 clean session；本方法只在
        只读解析期间开启事务，并在任何退出路径 rollback 该只读事务。
        """
        if session.in_transaction():
            raise RuntimeError("model_gateway_requires_clean_session")
        try:
            binding = await self.resolve_binding(session, model_role_binding_id)
            self._assert_exact_binding(binding, model_request)
            driver = self.build_driver(binding)
        finally:
            if session.in_transaction():
                await session.rollback()

        try:
            if model_request.timeout_seconds is not None:
                return await asyncio.wait_for(
                    driver.request(model_request), timeout=model_request.timeout_seconds
                )
            return await driver.request(model_request)
        except asyncio.TimeoutError as exc:
            from app.services.model_gateway.contracts import ProviderError

            raise ProviderError("provider_timeout", retriable=True) from exc

    @staticmethod
    def _assert_exact_binding(
        binding: dict[str, Any], model_request: ModelRequest
    ) -> None:
        """Request 的 provider/route/model/capability 必须与冻结 binding 逐项相等。"""
        expected = {
            "role": model_request.role,
            "provider": model_request.requested_provider,
            "route": model_request.requested_route,
            "model_ref": model_request.requested_model,
            "network_policy": model_request.network_policy,
        }
        for column, requested in expected.items():
            if binding[column] != requested:
                raise ValueError(f"model_binding_{column}_mismatch")
        if set(binding["allowed_tools"] or []) != set(model_request.allowed_tools):
            raise ValueError("model_binding_tools_mismatch")
        if set(binding["allowed_domains"] or []) != set(model_request.allowed_domains):
            raise ValueError("model_binding_domains_mismatch")
