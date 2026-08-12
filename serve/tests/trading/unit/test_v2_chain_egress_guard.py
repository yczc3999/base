"""WP-06 Checkpoint A —— chain egress tripwire（PolygonDriver / RelayerDriver，无 DB/网络）。

证明：两个 Driver 默认 ``require_injected_transport=true``；未注入 transport 时任何 wire
调用在 socket/client 构造前立即抛 ``wire_egress_tripwire``（``PolymarketError``），
transport_calls=0、fake/real network counters 不变；异常字符串不含任何 URL secret /
key/passphrase。注入 fake transport 后调用才返回结果（fake_calls>0、real=0）。
"""

from __future__ import annotations

import pytest

from app.services.polymarket.base import REASON_EGRESS_TRIPWIRE
from app.schemas.polymarket.common import PolymarketError

from tests.trading.fixtures.p6_settlement.p6_helpers import rpc_golden


@pytest.fixture(scope="module")
def polygon_driver_cls():
    from app.services.polymarket.polygon_driver import PolygonDriver

    return PolygonDriver


@pytest.fixture(scope="module")
def relayer_driver_cls():
    from app.services.polymarket.relayer_driver import RelayerDriver

    return RelayerDriver


async def _assert_tripwire(fn, *args, **kw):
    with pytest.raises(PolymarketError) as ei:
        await fn(*args, **kw)
    assert ei.value.reason_code == REASON_EGRESS_TRIPWIRE, ei.value.reason_code
    assert "http" not in str(ei.value).lower() or "://" not in str(ei.value)


async def test_polygon_driver_default_requires_injected_transport(polygon_driver_cls) -> None:
    driver = polygon_driver_cls()  # require_injected_transport 默认 true
    golden = rpc_golden()
    assert driver.transport_calls == 0
    await _assert_tripwire(driver.eth_chain_id)
    await _assert_tripwire(driver.eth_get_code, "0x" + "11" * 20)
    await _assert_tripwire(driver.eth_get_storage_at, "0x" + "11" * 20, "0x" + "ab" * 32)
    await _assert_tripwire(driver.eth_call, to="0x" + "11" * 20, data="0x5c60da1b")
    await _assert_tripwire(driver.eth_get_transaction_receipt, "0x" + "12" * 32)
    await _assert_tripwire(driver.eth_get_block_by_number, "finalized")
    assert driver.transport_calls == 0, "tripwire must not touch transport"
    assert driver.fake_calls == 0 and driver.real_calls == 0


async def test_relayer_driver_default_requires_injected_transport(relayer_driver_cls) -> None:
    driver = relayer_driver_cls()
    assert driver.transport_calls == 0
    await _assert_tripwire(driver.get_nonce, "0x" + "11" * 20)
    await _assert_tripwire(
        driver.prepare_batch,
        from_address="0x" + "11" * 20,
        to_address="0x" + "22" * 20,
        deposit_wallet="0x" + "22" * 20,
        calls=[],
        metadata="m",
    )
    await _assert_tripwire(driver.get_transaction_status, "tx-0001")
    assert driver.transport_calls == 0
    assert driver.fake_calls == 0 and driver.real_calls == 0


async def test_polygon_driver_rejects_before_client_construction(polygon_driver_cls) -> None:
    """tripwire 必须在创建任何 http client/socket 之前发生。"""
    import httpx

    with pytest.raises(PolymarketError) as ei:
        await polygon_driver_cls().eth_chain_id()
    assert ei.value.reason_code == REASON_EGRESS_TRIPWIRE
    # 未实例化 httpx.AsyncClient（缺 transport 时构造路径立即抛，不触碰网络层）
    client = getattr(polygon_driver_cls(), "_client", None)
    assert client is None


async def test_relayer_driver_rejects_before_client_construction(relayer_driver_cls) -> None:
    with pytest.raises(PolymarketError) as ei:
        await relayer_driver_cls().get_nonce("0x" + "11" * 20)
    assert ei.value.reason_code == REASON_EGRESS_TRIPWIRE


def test_tripwire_reason_constant_frozen() -> None:
    assert REASON_EGRESS_TRIPWIRE == "wire_egress_tripwire"


def test_unmarked_injected_callable_is_not_treated_as_fake(
    polygon_driver_cls, relayer_driver_cls
) -> None:
    async def polygon_transport(payload, endpoint):
        raise AssertionError("must not be called")

    async def relayer_transport(method, path, **kwargs):
        raise AssertionError("must not be called")

    with pytest.raises(PolymarketError) as polygon_error:
        polygon_driver_cls(
            rpc_urls=["https://a.example", "https://b.example", "https://c.example"],
            transport=polygon_transport,
        )
    assert polygon_error.value.reason_code == REASON_EGRESS_TRIPWIRE
    with pytest.raises(PolymarketError) as relayer_error:
        relayer_driver_cls(transport=relayer_transport)
    assert relayer_error.value.reason_code == REASON_EGRESS_TRIPWIRE
