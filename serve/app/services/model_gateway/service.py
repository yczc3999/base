"""Model gateway service：只按冻结 model binding 构造 Driver（WP-02 Checkpoint B）。

禁止读取 latest 配置、禁止任意 import/model；每次调用由 Runner 提供确切的
``model_role_binding_id``，本 service 从 DB 冻结 binding 解析 requested provider/route/model
与 network/tools/capability。
"""

from __future__ import annotations

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
        """构造 binding 对应 driver 并执行一次 request（网络调用不在 DB 事务内）。"""
        binding = await self.resolve_binding(session, model_role_binding_id)
        if binding["role"] != model_request.role:
            raise ValueError("model_binding_role_mismatch")
        if binding["network_policy"] != model_request.network_policy:
            raise ValueError("model_binding_network_policy_mismatch")
        if set(binding["allowed_tools"] or []) != set(model_request.allowed_tools):
            raise ValueError("model_binding_tools_mismatch")
        driver = self.build_driver(binding)
        return await driver.request(model_request)
