"""Polymarket Service：Driver 工厂（WP-01B Checkpoint A；WP-05 Checkpoint C private 追加）。

按显式 wire config 构造**短生命周期** Driver；禁止模块级有状态 singleton
（实施合同 §5.2）。每个调用方持有 Driver 一个调用/连接的生命周期。

transport/clock 可注入：contract 测试用 ``httpx.MockTransport`` 与固定 clock，
不访问公网（任务 §6.1）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.polymarket.base import WirePolicy, Clock, PrivateSubmitPolicy
from app.services.polymarket.clob_public_driver import ClobPublicDriver
from app.services.polymarket.clob_trading_driver import ClobTradingDriver
from app.services.polymarket.data_api_driver import DataApiDriver, DATA_API_BASE_URL
from app.services.polymarket.gamma_driver import GammaDriver
from app.services.polymarket.market_ws_driver import MarketWsDriver, MarketWsPolicy
from app.services.polymarket.user_ws_driver import UserWsDriver, UserWsPolicy

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


class PolymarketService:
    """构造短生命周期 Driver；不持有任何长期连接。"""

    def __init__(
        self,
        config: PublicMarketWireConfig | None = None,
        private_config: PrivateMarketWireConfig | None = None,
        *,
        transport=None,
        clock: Clock | None = None,
    ) -> None:
        self._config = config or PublicMarketWireConfig()
        self._private_config = private_config or PrivateMarketWireConfig()
        self._transport = transport
        self._clock = clock

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
