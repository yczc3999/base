"""WP-06 Checkpoint C —— PolygonDriver 单元测试（fake transport + golden，无 DB/网络）。

证明：eth_chainId 返回 0x89；eth_getCode 满长字节 keccak 与 registry 全长一致；storage
slot 解析 EIP-1967 implementation / Beacon；eth_call 返回 32-byte；receipt 严格校验
（status/blockNumber/blockHash/removed）；finality_check 在 finalized.number >
receipt.blockNumber 时通过、否则 fail-closed；JSON-RPC id/error/quantity 严格。
"""

from __future__ import annotations

import pytest

from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.polygon_driver import PolygonDriver

from tests.trading.fixtures.p6_settlement.p6_helpers import (
    code_keccak,
    frozen_fixture,
    slot32,
)

GOLDEN = frozen_fixture("polygon_rpc_golden")
REGISTRY = frozen_fixture("contract_registry")


def _norm(value):
    """把 hex 参数统一小写（Ethereum 地址/hex quantity 大小写不敏感）。"""
    if isinstance(value, str) and value.startswith("0x"):
        return value.lower()
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return value


_RECEIPT_BY_HASH = {
    "0x" + "12" * 32: "eth_getTransactionReceipt_confirmed",
    "0x" + "34" * 32: "eth_getTransactionReceipt_failed",
}


def _response_for(method: str, params: list) -> dict:
    """从 golden 的 requests/responses 按 method+params 查找响应（大小写不敏感）。"""
    if method == "eth_getTransactionReceipt" and params:
        key = _RECEIPT_BY_HASH.get(params[0])
        if key:
            return GOLDEN["responses"][key]["node-a"]
    if method == "eth_getBlockByNumber":
        tag = params[0]
        if tag == "finalized":
            return GOLDEN["responses"]["eth_getBlockByNumber_finalized"]["node-a"]
        # canonical block：receipt 所在高度的 block hash 必须等于 receipt.blockHash
        return {"result": {"number": tag, "hash": "0x" + "ef" * 32,
                           "timestamp": "0x0", "transactions": []}}
    for key, req in GOLDEN["requests"].items():
        if req["method"] == method and _norm(req["params"]) == _norm(params):
            return GOLDEN["responses"][key]["node-a"]
    raise KeyError(f"no golden response for {method} {params}")


def _make_fake_transport():
    calls = []

    async def transport(payload: dict, endpoint: str) -> dict:
        calls.append((payload["method"], payload["params"]))
        resp = _response_for(payload["method"], payload["params"])
        return {"jsonrpc": "2.0", "id": payload["id"], "result": resp["result"]}

    return transport, calls


def _driver(transport):
    return PolygonDriver(
        rpc_urls=["https://rpc-a.example", "https://rpc-b.example", "https://rpc-c.example"],
        transport=transport,
    )


async def test_eth_chain_id() -> None:
    transport, calls = _make_fake_transport()
    driver = _driver(transport)
    chain_id = await driver.eth_chain_id()
    assert chain_id == "0x89"
    assert int(chain_id, 16) == 137
    assert calls == [("eth_chainId", [])]
    assert driver.transport_calls == 1 and driver.fake_calls == 1 and driver.real_calls == 0


async def test_eth_get_code_matches_registry_keccak() -> None:
    transport, _ = _make_fake_transport()
    driver = _driver(transport)
    entry = {e["name"]: e for e in REGISTRY["entries"]}["pusd"]
    code = await driver.eth_get_code(entry["address"], block_tag=hex(91842167))
    assert code_keccak(code) == entry["runtime_keccak"]


async def test_eth_get_storage_at_implementation_slot() -> None:
    transport, _ = _make_fake_transport()
    driver = _driver(transport)
    entry = {e["name"]: e for e in REGISTRY["entries"]}["deposit_wallet"]
    slot = GOLDEN["eip1967_implementation_slot"]
    value = await driver.eth_get_storage_at(entry["address"], slot, block_tag=hex(91842167))
    assert value == slot32(entry["resolved_implementation_or_beacon"])


async def test_eth_call_beacon_implementation() -> None:
    transport, _ = _make_fake_transport()
    driver = _driver(transport)
    entry = {e["name"]: e for e in REGISTRY["entries"]}["ctf_adapter_standard"]
    impl_call = GOLDEN["responses"]["eth_call_beacon_implementation"]["node-a"]["result"]
    value = await driver.eth_call(
        to=entry["beacon_address"], data="0x5c60da1b", block_tag=hex(91842167)
    )
    assert value == impl_call == slot32(entry["beacon_implementation"])


async def test_eth_get_transaction_receipt_validation() -> None:
    transport, _ = _make_fake_transport()
    driver = _driver(transport)
    confirmed = await driver.eth_get_transaction_receipt("0x" + "12" * 32)
    assert confirmed.success is True
    assert confirmed.block_number_int > 0
    # 无 golden 时 transport 抛 KeyError → rpc_transport_failure
    with pytest.raises(PolymarketError, match="rpc_transport_failure"):
        await driver.eth_get_transaction_receipt("0x" + "99" * 32)


async def test_finality_check_passes_when_finalized_after_receipt() -> None:
    transport, _ = _make_fake_transport()
    driver = _driver(transport)
    # finalized block = snapshot+64 > receipt block = snapshot-32 → passes
    check = await driver.finality_check("0x" + "12" * 32)
    assert check.finalized_after_receipt is True
    assert check.receipt.success is True


async def test_finality_check_fails_when_receipt_failed() -> None:
    # 覆写 transport 使该 tx 返回 failed receipt
    async def transport(payload: dict, endpoint: str) -> dict:
        if payload["method"] == "eth_getTransactionReceipt":
            resp = GOLDEN["responses"]["eth_getTransactionReceipt_failed"]["node-a"]
        else:
            resp = _response_for(payload["method"], payload["params"])
        return {"jsonrpc": "2.0", "id": payload["id"], "result": resp["result"]}

    driver = _driver(transport)
    with pytest.raises(PolymarketError, match="finality_receipt_failed"):
        await driver.finality_check("0x" + "34" * 32)


async def test_jsonrpc_id_mismatch_rejected() -> None:
    async def transport(payload: dict, endpoint: str) -> dict:
        resp = _response_for(payload["method"], payload["params"])
        return {"jsonrpc": "2.0", "id": payload["id"] + 999, "result": resp["result"]}

    driver = _driver(transport)
    with pytest.raises(PolymarketError, match="rpc_response_id_mismatch"):
        await driver.eth_chain_id()


async def test_jsonrpc_error_surfaces() -> None:
    async def transport(payload: dict, endpoint: str) -> dict:
        return {"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32601, "message": "method not found"}}

    driver = _driver(transport)
    with pytest.raises(PolymarketError, match="rpc_error"):
        await driver.eth_chain_id()


async def test_quantity_strict_validation() -> None:
    async def transport(payload: dict, endpoint: str) -> dict:
        # 返回非法 quantity（无 0x 前缀）
        return {"jsonrpc": "2.0", "id": payload["id"], "result": "89"}

    driver = _driver(transport)
    with pytest.raises(PolymarketError, match="eth_chainId"):
        await driver.eth_chain_id()
