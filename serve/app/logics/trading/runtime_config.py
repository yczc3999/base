"""后台运行时配置 Logic（模型 API key 状态/写入 + pipeline AI 开关）。

安全不变量：
- 任何返回结构**绝不包含明文 key**；``last4`` 由 vault 解密后计算并立即丢弃明文
  （purpose 固定 ``model_invoke``——purpose 参与 AAD 绑定，store/read 必须一致；
  READ 审计由 VaultService 自动追加）。
- 写只进：``set_credential`` 只追加新 version（entry 已存在走 rotation 语义，
  旧 version retire、历史密文不删）；``clear_credential`` 只 disable entry →
  运行时回退 env 兜底。
- flag 变更（``pipeline.ai_enabled``）upsert + append-only ``runtime_flag_events``。
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.trading.runtime_config import RuntimeConfigRepository
from app.services.model_gateway import credentials
from app.services.model_gateway.credentials import (
    MODEL_CREDENTIAL_PROVIDERS,
    MODEL_CREDENTIAL_PURPOSE,
    MODEL_CREDENTIAL_SECRET_KIND,
    MODEL_GATEWAY_IDENTITY,
    MODEL_KEY_ID,
    MODEL_KEY_VERSION,
    credential_entry_name,
)
from app.services.model_gateway.transport import provider_credential_env_key
from app.services.vault import VaultService

PIPELINE_AI_FLAG = "pipeline.ai_enabled"
_MAX_API_KEY_BYTES = 512


def _env_credential_set(provider: str) -> bool:
    env_key = provider_credential_env_key(provider)
    return bool(env_key and os.environ.get(env_key, "").strip())


def _parse_flag_bool(value: str) -> bool:
    return str(value).strip().lower() == "true"


class RuntimeConfigLogic:
    """运行时配置读写语义；SQL 在 Repository，凭证密文在 VaultService。"""

    def __init__(
        self,
        *,
        vault: VaultService | None = None,
        repo: RuntimeConfigRepository | None = None,
    ) -> None:
        self._vault = vault
        self._repo = repo or RuntimeConfigRepository()

    # ---- 读：状态 + 掩码 ----

    async def list_items(self, session: AsyncSession) -> dict[str, Any]:
        """4 个 provider 状态 + ``pipeline.ai_enabled``；输出绝不含明文 key。"""
        credentials = [
            await self._credential_status(session, provider)
            for provider in MODEL_CREDENTIAL_PROVIDERS
        ]
        return {"credentials": credentials, "flags": await self._flag_status(session)}

    async def _credential_status(
        self, session: AsyncSession, provider: str
    ) -> dict[str, Any]:
        entry = await credentials.vault_repo.get_entry_by_name(
            session, name=credential_entry_name(provider)
        )
        if entry is None or entry.get("status") != "active":
            env_set = _env_credential_set(provider)
            return {
                "provider": provider,
                "configured": env_set,
                "source": "env" if env_set else "unset",
                "last4": None,
                "version_no": None,
                "updated_at": None,
            }
        version = await credentials.vault_repo.get_active_version(session, entry_id=entry["id"])
        last4: str | None = None
        if version is not None and self._vault is not None:
            plaintext = await self._vault.read_secret(
                session,
                entry_id=entry["id"],
                version_id=version["id"],
                purpose=MODEL_CREDENTIAL_PURPOSE,
                identity=MODEL_GATEWAY_IDENTITY,
            )
            try:
                text_value = plaintext.decode("utf-8")
                last4 = text_value[-4:] if len(text_value) >= 4 else None
            finally:
                del plaintext  # 明文即刻丢弃；只有 last4 离开本函数
        return {
            "provider": provider,
            "configured": version is not None,
            "source": "db",
            "last4": last4,
            "version_no": version["version_no"] if version is not None else None,
            "updated_at": version["created_at"] if version is not None else None,
        }

    async def _flag_status(self, session: AsyncSession) -> dict[str, Any]:
        from app.config import settings

        row = await self._repo.get_flag(session, flag_key=PIPELINE_AI_FLAG)
        if row is None:
            return {
                PIPELINE_AI_FLAG: {
                    "value": bool(
                        getattr(settings, "PM_V2_PIPELINE_AI_ENABLED", False)
                    ),
                    "source": "default",
                    "updated_at": None,
                    "updated_by": None,
                }
            }
        return {
            PIPELINE_AI_FLAG: {
                "value": _parse_flag_bool(row["flag_value"]),
                "source": "db",
                "updated_at": row["updated_at"],
                "updated_by": row["updated_by"],
            }
        }

    # ---- 写：凭证 ----

    async def set_credential(
        self, session: AsyncSession, *, provider: str, api_key: str, actor: str
    ) -> dict[str, Any]:
        if provider not in MODEL_CREDENTIAL_PROVIDERS:
            raise ValueError("runtime_config_provider_unknown")
        key = api_key.strip()
        if not key:
            raise ValueError("runtime_config_api_key_empty")
        key_bytes = key.encode("utf-8")
        if len(key_bytes) > _MAX_API_KEY_BYTES:
            raise ValueError("runtime_config_api_key_too_long")
        if self._vault is None:
            raise ValueError("vault_keyring_not_configured")
        name = credential_entry_name(provider)
        entry = await credentials.vault_repo.get_entry_by_name(session, name=name)
        if entry is None:
            entry = await self._vault.create_entry(
                session,
                name=name,
                secret_kind=MODEL_CREDENTIAL_SECRET_KIND,
                runtime_identity=MODEL_GATEWAY_IDENTITY,
            )
            version = await self._vault.store_secret(
                session,
                entry_id=entry["id"],
                secret=key_bytes,
                purpose=MODEL_CREDENTIAL_PURPOSE,
                identity=MODEL_GATEWAY_IDENTITY,
                key_id=MODEL_KEY_ID,
                key_version=MODEL_KEY_VERSION,
            )
        else:
            if entry.get("status") != "active":
                reactivated = await credentials.vault_repo.mark_entry_active(
                    session, entry_id=entry["id"]
                )
                if not reactivated:
                    raise RuntimeError("runtime_config_entry_reactivate_conflict")
            version = await self._vault.rotate_secret(
                session,
                entry_id=entry["id"],
                secret=key_bytes,
                purpose=MODEL_CREDENTIAL_PURPOSE,
                identity=MODEL_GATEWAY_IDENTITY,
                key_id=MODEL_KEY_ID,
                key_version=MODEL_KEY_VERSION,
            )
        return {
            "provider": provider,
            "configured": True,
            "source": "db",
            "version_no": version["version_no"],
        }

    async def clear_credential(
        self, session: AsyncSession, *, provider: str, actor: str
    ) -> dict[str, Any]:
        if provider not in MODEL_CREDENTIAL_PROVIDERS:
            raise ValueError("runtime_config_provider_unknown")
        entry = await credentials.vault_repo.get_entry_by_name(
            session, name=credential_entry_name(provider)
        )
        if entry is None or entry.get("status") != "active":
            raise ValueError("runtime_config_credential_not_set")
        disabled = await credentials.vault_repo.mark_entry_disabled(session, entry_id=entry["id"])
        if not disabled:
            raise RuntimeError("runtime_config_entry_disable_conflict")
        await credentials.vault_repo.insert_access_event(
            session,
            entry_id=entry["id"],
            secret_version_id=None,
            subject=actor,
            identity=actor,
            purpose="admin_clear",
            key_version=None,
            result="DISABLED",
            result_reason="admin_clear",
        )
        env_set = _env_credential_set(provider)
        return {
            "provider": provider,
            "configured": env_set,
            "source": "env" if env_set else "unset",
        }

    # ---- 写：AI 开关 ----

    async def set_ai_enabled(
        self, session: AsyncSession, *, enabled: bool, actor: str
    ) -> dict[str, Any]:
        new_value = "true" if enabled else "false"
        old = await self._repo.get_flag(session, flag_key=PIPELINE_AI_FLAG)
        row = await self._repo.upsert_flag(
            session, flag_key=PIPELINE_AI_FLAG, flag_value=new_value, actor=actor
        )
        await self._repo.insert_flag_event(
            session,
            flag_key=PIPELINE_AI_FLAG,
            old_value=old["flag_value"] if old else None,
            new_value=new_value,
            actor=actor,
        )
        return {
            "flag_key": PIPELINE_AI_FLAG,
            "value": enabled,
            "source": "db",
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        }
