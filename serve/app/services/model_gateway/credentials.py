"""模型网关凭证解析（后台运行时配置：DB vault 优先，env 兜底）。

- vault entry 命名 ``model-api-key:{provider}``，``secret_kind="api_credential"``，
  固定 ``runtime_identity="model-gateway"``（admin 写与 gateway 读用同一 identity
  构造 ``VaultService``，AAD/identity 校验才过得去）。
- 每次 ``ModelGatewayService.execute`` 在只读解析事务内调
  ``resolve_model_credential``：后台改完 key **无需重启**即生效。
- 无 entry / entry 非 active / 无 active version / vault 未配置 → 返回 ``None``
  （由 transport 回退 env）；解密/identity 失败不吞异常（fail closed，
  READ/AUTH 审计由 ``VaultService`` 自动追加）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.trading.vault import VaultRepository
from app.services.vault import VaultKeyError, VaultService
from app.services.vault.keyring_loader import load_keyring

logger = logging.getLogger(__name__)

MODEL_GATEWAY_IDENTITY = "model-gateway"
MODEL_CREDENTIAL_ENTRY_PREFIX = "model-api-key:"
MODEL_CREDENTIAL_SECRET_KIND = "api_credential"
# purpose 参与 AAD 绑定（envelope.build_aad），同一 entry 的 store/rotate/read 必须
# 用同一 purpose，否则 aad_hash 校验 fail closed。模型凭证全程固定 "model_invoke"
# （admin status/last4 读取也用同一 purpose，审计仍记录 READ 事件与时间线）。
MODEL_CREDENTIAL_PURPOSE = "model_invoke"
MODEL_KEY_ID = "master"
MODEL_KEY_VERSION = "v1"
MODEL_CREDENTIAL_PROVIDERS = ("deepseek", "xai", "kimi", "packy")

# 查询用 repo 无状态，模块级共享；写路径同样走它（SQL 只在本层）。
vault_repo = VaultRepository()


def credential_entry_name(provider: str) -> str:
    return f"{MODEL_CREDENTIAL_ENTRY_PREFIX}{provider}"


async def resolve_model_credential(
    session: AsyncSession,
    vault: VaultService | None,
    provider: str,
) -> str | None:
    """按名字查 vault entry 并解密 active version；查不到 → ``None``（调用方回退 env）。"""
    if vault is None:
        return None
    entry = await vault_repo.get_entry_by_name(
        session, name=credential_entry_name(provider)
    )
    if entry is None or entry.get("status") != "active":
        return None
    version = await vault_repo.get_active_version(session, entry_id=entry["id"])
    if version is None:
        return None
    plaintext = await vault.read_secret(
        session,
        entry_id=entry["id"],
        version_id=version["id"],
        purpose=MODEL_CREDENTIAL_PURPOSE,
        identity=MODEL_GATEWAY_IDENTITY,
    )
    return plaintext.decode("utf-8")


def build_model_gateway_vault(
    settings: Any,
    sessions_factory: Callable[[], Any] | None = None,
) -> VaultService | None:
    """用固定 identity ``model-gateway`` 构造 vault；keyring 未配置/不可用 → ``None``。

    ``sessions_factory``（零参 → async_sessionmaker）存在时为失败审计提供独立事务
    sink（不占调用方 UoW）；缺省时失败审计退化为日志（VaultService 内建兜底）。
    """
    ref = (getattr(settings, "PM_V2_VAULT_KEYRING_REF", "") or "").strip()
    if not ref:
        return None
    try:
        keyring = load_keyring(ref)
    except VaultKeyError as exc:
        logger.error("model_gateway_vault.keyring_unavailable reason=%s", exc)
        return None
    failure_audit = (
        _durable_failure_audit(sessions_factory) if sessions_factory is not None else None
    )
    return VaultService(
        vault_repo,
        keyring,
        env=getattr(settings, "PM_V2_VAULT_ENV", "dev") or "dev",
        runtime_identity=MODEL_GATEWAY_IDENTITY,
        failure_audit=failure_audit,
    )


def _durable_failure_audit(sessions_factory: Callable[[], Any]):
    """失败审计独立事务 sink：只写 access event，绝不携带 secret 明文。"""

    async def sink(event: Any) -> None:
        from app.db.uow import UnitOfWork

        entry_id = int(event.get("entry_id", 0) or 0)
        if entry_id <= 0:
            return
        async with UnitOfWork(sessions_factory()) as audit_uow:
            entry = await vault_repo.get_entry(audit_uow.session, entry_id=entry_id)
            if entry is None:
                return
            await vault_repo.insert_access_event(
                audit_uow.session,
                entry_id=entry_id,
                secret_version_id=event.get("secret_version_id"),
                subject=f"vault.{event.get('operation', 'unknown')}",
                identity=str(event.get("identity", "unknown")),
                purpose=str(event.get("purpose", "unknown")),
                key_version=event.get("key_version"),
                result=str(event.get("result", "FAILED")),
                result_reason=str(event.get("reason", "unknown")),
            )

    return sink
