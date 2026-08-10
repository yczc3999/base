"""Polymarket Service：Driver 工厂（WP-01B Checkpoint A）。

按显式 wire config 构造**短生命周期** Driver；禁止模块级有状态 singleton
（实施合同 §5.2）。每个调用方持有 Driver 一个调用/连接的生命周期。

transport/clock 可注入：contract 测试用 ``httpx.MockTransport`` 与固定 clock，
不访问公网（任务 §6.1）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.polymarket.base import WirePolicy, Clock
from app.services.polymarket.clob_public_driver import ClobPublicDriver
from app.services.polymarket.gamma_driver import GammaDriver
from app.services.polymarket.market_ws_driver import MarketWsDriver, MarketWsPolicy


@dataclass(frozen=True)
class PublicMarketWireConfig:
    """公共行情 wire 配置（typed；测试用显式 policy fixture，任务 §2.9）。"""

    gamma_base_url: str = "https://gamma-api.polymarket.com"
    clob_public_base_url: str = "https://clob.polymarket.com"
    market_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    gamma_policy: WirePolicy = WirePolicy()
    clob_policy: WirePolicy = WirePolicy()
    ws_policy: MarketWsPolicy = MarketWsPolicy()


class PolymarketService:
    """构造短生命周期 Driver；不持有任何长期连接。"""

    def __init__(
        self,
        config: PublicMarketWireConfig | None = None,
        *,
        transport=None,
        clock: Clock | None = None,
    ) -> None:
        self._config = config or PublicMarketWireConfig()
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
