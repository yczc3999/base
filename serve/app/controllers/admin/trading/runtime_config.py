"""运行时配置 Admin API（模型 API key 写只进/掩码读 + pipeline AI 开关）。

安全边界：
- 任何响应绝不包含明文 key（最多 last4 掩码）；key 只经 vault 信封加密落库。
- GET 读状态会追加 vault READ 审计（``purpose="admin_status"``），与
  ``get_admin_read_db`` 的 ``SET TRANSACTION READ ONLY`` 不兼容，故本域读写统一
  用 ``get_db`` + ``require_perms``，GET 成功后显式 commit 以持久化审计事件。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import AuthInfo, get_db, require_perms
from app.logics.trading.runtime_config import RuntimeConfigLogic
from app.utils.response import fail, ok

router = APIRouter()

_logic: RuntimeConfigLogic | None = None


def _get_logic() -> RuntimeConfigLogic:
    """惰性构建：vault keyring 未配置时 vault=None（读回退 env、写 fail closed 503）。"""
    global _logic
    if _logic is None:
        from app.config import settings
        from app.services.database import engines
        from app.services.model_gateway.credentials import build_model_gateway_vault

        vault = build_model_gateway_vault(
            settings, lambda: engines.session_factory("api")
        )
        _logic = RuntimeConfigLogic(vault=vault)
    return _logic


def reset_runtime_config_logic(logic: RuntimeConfigLogic | None = None) -> None:
    """测试注入：允许替换为假 vault/repo 的 logic。"""
    global _logic
    _logic = logic


class CredentialBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str


class AiEnabledBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


@router.get("/v2/runtime-config")
async def get_runtime_config(
    session: AsyncSession = Depends(get_db),
    auth: AuthInfo = Depends(require_perms("v2:runtime-config:view")),
):
    items = await _get_logic().list_items(session)
    await session.commit()  # 持久化 vault READ 审计事件
    return ok(items)


@router.put("/v2/runtime-config/credentials/{provider}")
async def set_credential(
    provider: str,
    body: CredentialBody,
    session: AsyncSession = Depends(get_db),
    auth: AuthInfo = Depends(require_perms("v2:runtime-config:manage")),
):
    try:
        result = await _get_logic().set_credential(
            session, provider=provider, api_key=body.api_key, actor=auth.username
        )
    except ValueError as exc:
        code = str(exc)
        if code == "vault_keyring_not_configured":
            return fail(code, 503)
        return fail(code, 400)
    await session.commit()
    return ok(result)


@router.delete("/v2/runtime-config/credentials/{provider}")
async def clear_credential(
    provider: str,
    session: AsyncSession = Depends(get_db),
    auth: AuthInfo = Depends(require_perms("v2:runtime-config:manage")),
):
    try:
        result = await _get_logic().clear_credential(
            session, provider=provider, actor=auth.username
        )
    except ValueError as exc:
        code = str(exc)
        if code == "runtime_config_credential_not_set":
            return fail(code, 404)
        return fail(code, 400)
    await session.commit()
    return ok(result)


@router.put("/v2/runtime-config/flags/pipeline-ai-enabled")
async def set_pipeline_ai_enabled(
    body: AiEnabledBody,
    session: AsyncSession = Depends(get_db),
    auth: AuthInfo = Depends(require_perms("v2:runtime-config:manage")),
):
    result = await _get_logic().set_ai_enabled(
        session, enabled=body.enabled, actor=auth.username
    )
    await session.commit()
    return ok(result)
