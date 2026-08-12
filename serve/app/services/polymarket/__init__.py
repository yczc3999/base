"""Polymarket 公共行情 Driver 包（WP-01B Checkpoint A）。

Driver 只做 wire；Service 按调用构造短生命周期 Driver；业务状态转换在 Logic。
"""

from app.services.polymarket.base import (
    HttpPolymarketDriver,
    PrivateSubmitPolicy,
    RateLimiter,
    WirePolicy,
    build_l2_hmac_message,
    parse_json_bytes,
)
from app.services.polymarket.gamma_driver import GammaDriver
from app.services.polymarket.clob_public_driver import ClobPublicDriver
from app.services.polymarket.clob_trading_driver import (
    ACK,
    AUTH_STOP,
    REJECTED,
    THROTTLED,
    UNKNOWN,
    ClobTradingDriver,
    EgressTripwireError,
    SubmitOutcome,
    canonical_order_body_hash,
    expected_order_hash_for,
    sdk_manifest_hash_for,
)
from app.services.polymarket.data_api_driver import (
    DATA_API_BASE_URL,
    DataApiDriver,
)
from app.services.polymarket.market_ws_driver import (
    MarketWsDriver,
    MarketWsMessage,
    MarketWsPolicy,
    PING_TEXT,
    PONG_TEXT,
    SUBSCRIBE_TEMPLATE,
)
from app.services.polymarket.user_ws_driver import (
    SUBSCRIBE_TEMPLATE as USER_WS_SUBSCRIBE_TEMPLATE,
)
from app.services.polymarket.user_ws_driver import (
    UserWsDriver,
    UserWsMessage,
    UserWsPolicy,
)
from app.services.polymarket.geoblock_driver import (
    GeoblockCheckError,
    GeoblockDriver,
    GeoblockResult,
    fixture_geoblock_transport,
)
from app.services.polymarket.polygon_driver import (
    PolygonDriver,
    fixture_polygon_transport,
)
from app.services.polymarket.relayer_driver import (
    PreparedRelayerBatch,
    RelayerDriver,
    fixture_relayer_transport,
)
from app.services.polymarket.service import (
    ChainMarketWireConfig,
    PolymarketService,
    PrivateMarketWireConfig,
    PublicMarketWireConfig,
    RelayerVaultRefs,
    VaultSecretVersionRef,
)

__all__ = [
    "HttpPolymarketDriver",
    "RateLimiter",
    "WirePolicy",
    "PrivateSubmitPolicy",
    "build_l2_hmac_message",
    "parse_json_bytes",
    "GammaDriver",
    "ClobPublicDriver",
    "MarketWsDriver",
    "MarketWsMessage",
    "MarketWsPolicy",
    "PING_TEXT",
    "PONG_TEXT",
    "SUBSCRIBE_TEMPLATE",
    "PolymarketService",
    "PublicMarketWireConfig",
    # WP-05 private CLOB
    "ClobTradingDriver",
    "SubmitOutcome",
    "EgressTripwireError",
    "canonical_order_body_hash",
    "expected_order_hash_for",
    "sdk_manifest_hash_for",
    "ACK", "REJECTED", "AUTH_STOP", "THROTTLED", "UNKNOWN",
    # WP-05 user WS
    "UserWsDriver",
    "UserWsMessage",
    "UserWsPolicy",
    "USER_WS_SUBSCRIBE_TEMPLATE",
    # WP-05 data API
    "DataApiDriver",
    "DATA_API_BASE_URL",
    "PrivateMarketWireConfig",
    "ChainMarketWireConfig",
    "VaultSecretVersionRef",
    "RelayerVaultRefs",
    # WP-06 Polygon / Relayer / geoblock
    "PolygonDriver",
    "fixture_polygon_transport",
    "RelayerDriver",
    "PreparedRelayerBatch",
    "fixture_relayer_transport",
    "GeoblockDriver",
    "GeoblockResult",
    "GeoblockCheckError",
    "fixture_geoblock_transport",
]
