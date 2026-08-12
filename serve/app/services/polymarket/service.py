"""Polymarket Service：Driver 工厂（WP-01B Checkpoint A；WP-05 Checkpoint C private 追加）。

按显式 wire config 构造**短生命周期** Driver；禁止模块级有状态 singleton
（实施合同 §5.2）。每个调用方持有 Driver 一个调用/连接的生命周期。

transport/clock 可注入：contract 测试用 ``httpx.MockTransport`` 与固定 clock，
不访问公网（任务 §6.1）。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from eth_account import Account

from app.db.uow import UnitOfWork
from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.base import WirePolicy, Clock, PrivateSubmitPolicy
from app.services.polymarket.clob_public_driver import ClobPublicDriver
from app.services.polymarket.clob_trading_driver import ClobTradingDriver
from app.services.polymarket.data_api_driver import DataApiDriver, DATA_API_BASE_URL
from app.services.polymarket.gamma_driver import GammaDriver
from app.services.polymarket.market_ws_driver import MarketWsDriver, MarketWsPolicy
from app.services.polymarket.user_ws_driver import UserWsDriver, UserWsPolicy
from app.services.polymarket.relayer_driver import RelayerDriver

USER_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"


@dataclass(frozen=True)
class PublicMarketWireConfig:
    """公共行情 wire 配置（typed；测试用显式 policy fixture，任务 §2.9）。"""

    gamma_base_url: str = "https://gamma-api.polymarket.com"
    clob_public_base_url: str = "https://clob.polymarket.com"
    market_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    gamma_policy: WirePolicy = WirePolicy()
    clob_policy: WirePolicy = WirePolicy()
    ws_policy: MarketWsPolicy = MarketWsPolicy()


@dataclass(frozen=True)
class PrivateMarketWireConfig:
    """私有 CLOB / User WS / Data API wire 配置（WP-05 Checkpoint C）。"""

    clob_private_base_url: str = "https://clob.polymarket.com"
    user_ws_url: str = USER_WS_URL
    data_api_base_url: str = DATA_API_BASE_URL
    private_submit_policy: PrivateSubmitPolicy = PrivateSubmitPolicy()
    user_ws_policy: UserWsPolicy = UserWsPolicy()
    data_api_policy: WirePolicy = WirePolicy(max_retries=1)


@dataclass(frozen=True, slots=True)
class VaultSecretVersionRef:
    """Opaque vault identity; plaintext or environment secret values are impossible here."""

    entry_id: int
    version_id: int

    def __post_init__(self) -> None:
        if isinstance(self.entry_id, bool) or int(self.entry_id) <= 0:
            raise ValueError("vault_secret_entry_ref_invalid")
        if isinstance(self.version_id, bool) or int(self.version_id) <= 0:
            raise ValueError("vault_secret_version_ref_invalid")


@dataclass(frozen=True, slots=True)
class RelayerVaultRefs:
    """The only credential inputs accepted by the production Relayer factory."""

    signer: VaultSecretVersionRef
    builder: VaultSecretVersionRef
    account_context: str | None = None


@dataclass(frozen=True, slots=True)
class ChainMarketWireConfig:
    """WP-06 fake-conformance Relayer construction settings (no credential fields)."""

    relayer_base_url: str = "https://relayer-v2.polymarket.com"
    deadline_ttl_s: int = 600
    chain_id: int = 137

    def __post_init__(self) -> None:
        if self.deadline_ttl_s != 600:
            raise ValueError("relayer_deadline_ttl_must_be_600")
        if self.chain_id != 137:
            raise ValueError("relayer_chain_id_must_be_137")


_SIGNER_PURPOSE = "relayer_sign"
_BUILDER_PURPOSE = "relayer_builder"
_BUILDER_KEYS = {
    "relayer_api_key",
    "builder_api_key",
    "builder_passphrase",
    "builder_secret_base64",
}


class PolymarketService:
    """构造短生命周期 Driver；不持有任何长期连接。"""

    def __init__(
        self,
        config: PublicMarketWireConfig | None = None,
        private_config: PrivateMarketWireConfig | None = None,
        *,
        transport=None,
        clock: Clock | None = None,
        chain_config: ChainMarketWireConfig | None = None,
        relayer_transport=None,
    ) -> None:
        self._config = config or PublicMarketWireConfig()
        self._private_config = private_config or PrivateMarketWireConfig()
        self._transport = transport
        self._clock = clock
        self._chain_config = chain_config or ChainMarketWireConfig()
        self._relayer_transport = relayer_transport

    def gamma(self) -> GammaDriver:
        return GammaDriver(
            self._config.gamma_base_url,
            policy=self._config.gamma_policy,
            transport=self._transport,
            clock=self._clock,
        )

    def clob_public(self) -> ClobPublicDriver:
        return ClobPublicDriver(
            self._config.clob_public_base_url,
            policy=self._config.clob_policy,
            transport=self._transport,
            clock=self._clock,
        )

    def market_ws(self, assets_ids: list[str]) -> MarketWsDriver:
        return MarketWsDriver(
            self._config.market_ws_url,
            assets_ids,
            policy=self._config.ws_policy,
            clock=self._clock,
        )

    # ---- WP-05 Checkpoint C：private 工厂 ----

    def clob_trading(self, client: object | None = None) -> ClobTradingDriver:
        """私有 CLOB 下单 Driver；fake-only 时注入 fake client，否则 egress tripwire。"""
        return ClobTradingDriver(
            client,
            policy=self._private_config.private_submit_policy,
            clock=self._clock,
            base_url=self._private_config.clob_private_base_url,
        )

    def user_ws(self) -> UserWsDriver:
        return UserWsDriver(
            self._private_config.user_ws_url,
            policy=self._private_config.user_ws_policy,
            clock=self._clock,
        )

    def data_api(self) -> DataApiDriver:
        return DataApiDriver(
            self._private_config.data_api_base_url,
            policy=self._private_config.data_api_policy,
            transport=self._transport,
            clock=self._clock,
        )

    @asynccontextmanager
    async def relayer_vault_window(
        self,
        *,
        sessions_factory: Any,
        vault_service: Any,
        refs: RelayerVaultRefs,
        runtime_identity: str,
        expected_signing_identity: str,
        trusted_time_provider: Any,
    ) -> AsyncIterator[RelayerDriver]:
        """Yield a fixture-only Relayer driver backed only by audited Vault refs.

        Each read owns a separate transaction, so a later rejected read cannot roll
        back an earlier successful access record. Decrypted bytes live only for this
        context and are overwritten on exit. Raw callbacks/keys are not accepted by
        this production composition boundary.
        """
        if not isinstance(refs, RelayerVaultRefs):
            raise TypeError("relayer_vault_refs_required")
        if not isinstance(runtime_identity, str) or not runtime_identity.strip():
            raise ValueError("relayer_runtime_identity_required")
        if not bool(getattr(vault_service, "durable_failure_audit_configured", False)):
            raise RuntimeError("relayer_vault_durable_failure_audit_required")

        signer_material = await self._read_vault_ref(
            sessions_factory=sessions_factory,
            vault_service=vault_service,
            ref=refs.signer,
            purpose=_SIGNER_PURPOSE,
            runtime_identity=runtime_identity,
            account_context=refs.account_context,
        )
        builder_material: bytearray | None = None
        builder_values: dict[str, str] = {}
        active = True
        try:
            try:
                signer_address = self._signer_address(signer_material)
                if not hmac.compare_digest(
                    signer_address.lower(), str(expected_signing_identity).lower()
                ):
                    raise ValueError("relayer_signer_identity_mismatch")
            except Exception:
                await vault_service.audit_consumer_failure(
                    entry_id=refs.signer.entry_id,
                    version_id=refs.signer.version_id,
                    identity=runtime_identity,
                    purpose=_SIGNER_PURPOSE,
                    reason="credential_identity_invalid",
                )
                raise PolymarketError("relayer_signer_credential_invalid") from None

            builder_material = await self._read_vault_ref(
                sessions_factory=sessions_factory,
                vault_service=vault_service,
                ref=refs.builder,
                purpose=_BUILDER_PURPOSE,
                runtime_identity=runtime_identity,
                account_context=refs.account_context,
            )
            try:
                builder_values = self._decode_builder_credential(builder_material)
                builder_secret = bytearray(self._decode_builder_secret(
                    builder_values["builder_secret_base64"]
                ))
            except Exception:
                await vault_service.audit_consumer_failure(
                    entry_id=refs.builder.entry_id,
                    version_id=refs.builder.version_id,
                    identity=runtime_identity,
                    purpose=_BUILDER_PURPOSE,
                    reason="credential_shape_invalid",
                )
                raise PolymarketError("relayer_builder_credential_invalid") from None

            def require_active() -> None:
                if not active:
                    raise PolymarketError("relayer_credential_window_closed")

            def signer(message: Any) -> str:
                require_active()
                try:
                    signature = Account.sign_message(
                        message, private_key=bytes(signer_material)
                    ).signature.hex()
                except Exception:
                    raise PolymarketError("relayer_signing_failed") from None
                return signature if signature.startswith("0x") else f"0x{signature}"

            def nonce_auth(address: str) -> dict[str, str]:
                require_active()
                return {
                    "RELAYER_API_KEY": builder_values["relayer_api_key"],
                    "RELAYER_API_KEY_ADDRESS": address,
                }

            def builder_auth(
                timestamp: int, method: str, path: str, body: bytes
            ) -> dict[str, str]:
                require_active()
                material = f"{timestamp}{method.upper()}{path}".encode("utf-8") + body
                signature = base64.urlsafe_b64encode(
                    hmac.new(bytes(builder_secret), material, hashlib.sha256).digest()
                ).decode("ascii")
                return {
                    "POLY_BUILDER_API_KEY": builder_values["builder_api_key"],
                    "POLY_BUILDER_TIMESTAMP": str(timestamp),
                    "POLY_BUILDER_PASSPHRASE": builder_values["builder_passphrase"],
                    "POLY_BUILDER_SIGNATURE": signature,
                }

            yield RelayerDriver(
                base_url=self._chain_config.relayer_base_url,
                transport=self._relayer_transport,
                require_injected_transport=True,
                fixture_only=True,
                clock=self._clock,
                trusted_time_provider=trusted_time_provider,
                signer=signer,
                nonce_auth_provider=nonce_auth,
                builder_auth_provider=builder_auth,
                deadline_ttl_s=self._chain_config.deadline_ttl_s,
                chain_id=self._chain_config.chain_id,
            )
        finally:
            active = False
            for material in (signer_material, builder_material):
                if material is not None:
                    material[:] = b"\x00" * len(material)
            if "builder_secret" in locals():
                builder_secret[:] = b"\x00" * len(builder_secret)
            builder_values.clear()

    @staticmethod
    async def _read_vault_ref(
        *,
        sessions_factory: Any,
        vault_service: Any,
        ref: VaultSecretVersionRef,
        purpose: str,
        runtime_identity: str,
        account_context: str | None,
    ) -> bytearray:
        async with UnitOfWork(sessions_factory) as uow:
            plaintext = await vault_service.read_secret(
                uow.session,
                entry_id=ref.entry_id,
                version_id=ref.version_id,
                purpose=purpose,
                identity=runtime_identity,
                account=account_context,
            )
        if not isinstance(plaintext, bytes):
            raise PolymarketError("relayer_vault_plaintext_invalid")
        return bytearray(plaintext)

    @staticmethod
    def _signer_address(material: bytearray) -> str:
        if len(material) == 32:
            private_key = bytes(material)
        else:
            try:
                text_value = bytes(material).decode("ascii")
                if not text_value.startswith("0x") or len(text_value) != 66:
                    raise ValueError
                private_key = bytes.fromhex(text_value[2:])
            except Exception:
                raise ValueError("signer_private_key_invalid") from None
            material[:] = private_key
        return Account.from_key(private_key).address

    @staticmethod
    def _decode_builder_credential(material: bytearray) -> dict[str, str]:
        if len(material) > 16_384:
            raise ValueError("builder_credential_too_large")
        try:
            value = json.loads(bytes(material), parse_constant=lambda _v: (_ for _ in ()).throw(ValueError()))
        except Exception:
            raise ValueError("builder_credential_json_invalid") from None
        if not isinstance(value, dict) or set(value) != _BUILDER_KEYS:
            raise ValueError("builder_credential_shape_invalid")
        if any(
            not isinstance(value[key], str) or not value[key] or len(value[key]) > 4096
            for key in _BUILDER_KEYS
        ):
            raise ValueError("builder_credential_value_invalid")
        return dict(value)

    @staticmethod
    def _decode_builder_secret(value: str) -> bytes:
        try:
            decoded = base64.b64decode(value, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("builder_secret_base64_invalid") from None
        if len(decoded) < 16 or len(decoded) > 4096:
            raise ValueError("builder_secret_size_invalid")
        return decoded
