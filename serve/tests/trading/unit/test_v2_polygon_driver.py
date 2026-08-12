"""PolygonDriver strict/finality/registry unit conformance (fixture transport only)."""
from __future__ import annotations

import asyncio
import json
import traceback

import pytest

from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.polygon_driver import (
    PolygonDriver,
    fixture_polygon_transport,
)
from tests.trading.fixtures.p6_settlement.p6_helpers import code_keccak, frozen_fixture

GOLDEN = frozen_fixture("polygon_rpc_golden")
REGISTRY = frozen_fixture("contract_registry")
URLS = ["https://rpc-a.example", "https://rpc-b.example", "https://rpc-c.example"]
NODE = dict(zip(URLS, GOLDEN["rpc_nodes"], strict=True))


def _norm(value):
    if isinstance(value, str) and value.startswith("0x"):
        return value.lower()
    if isinstance(value, list):
        return [_norm(item) for item in value]
    if isinstance(value, dict):
        return {key: _norm(item) for key, item in value.items()}
    return value


def _key(method: str, params: list) -> str:
    for key, request in GOLDEN["requests"].items():
        if request["method"] == method and _norm(request["params"]) == _norm(params):
            return key
    raise AssertionError(f"unfrozen RPC request: {method} {params}")


def make_transport():
    calls = []

    @fixture_polygon_transport
    async def transport(payload: dict, endpoint: str) -> dict:
        calls.append((endpoint, payload["method"], payload["params"]))
        result = GOLDEN["responses"][_key(payload["method"], payload["params"])][NODE[endpoint]]["result"]
        return {"jsonrpc": "2.0", "id": payload["id"], "result": result}

    return transport, calls


def driver(transport) -> PolygonDriver:
    return PolygonDriver(rpc_urls=URLS, transport=transport)


async def test_chain_id_and_real_full_code() -> None:
    transport, calls = make_transport()
    d = driver(transport)
    assert await d.eth_chain_id() == "0x89"
    pusd = next(item for item in REGISTRY["entries"] if item["name"] == "pusd")
    code = await d.eth_get_code(pusd["address"], block_tag=hex(91842167))
    assert code_keccak(code) == pusd["runtime_keccak"]
    assert d.fixture_only and d.fake_calls == 2 and d.real_calls == 0


async def test_all_registry_paths_verify_against_three_origins() -> None:
    transport, _ = make_transport()
    d = driver(transport)
    for entry in REGISTRY["entries"]:
        evidence = await d.verify_registry_entry(entry)
        assert evidence["runtime_keccak"] == entry["runtime_keccak"]
        if entry["proxy_kind"] == "eip1967":
            assert evidence["resolved_code_keccak"] == entry["resolved_code_keccak"]


async def test_registry_empty_or_implementation_drift_rejected() -> None:
    transport, _ = make_transport()
    d = driver(transport)
    entry = dict(next(item for item in REGISTRY["entries"] if item["name"] == "pusd"))
    entry["resolved_code_keccak"] = "0x" + "00" * 32
    with pytest.raises(PolymarketError, match="registry_runtime_code_drift"):
        await d.verify_registry_entry(entry)


async def test_finality_is_bound_to_transaction_canonical_block_and_three_origins() -> None:
    transport, calls = make_transport()
    d = driver(transport)
    check = await d.finality_check("0x" + "12" * 32)
    assert check.receipt.transaction_hash == "0x" + "12" * 32
    assert check.canonical_block_hash == check.receipt.block_hash
    assert check.finalized_after_receipt is True
    assert len(calls) == 9


async def test_receipt_failed_and_removed_log_rejected() -> None:
    transport, _ = make_transport()
    d = driver(transport)
    with pytest.raises(PolymarketError, match="finality_receipt_failed"):
        await d.finality_check("0x" + "34" * 32)

    base = GOLDEN["responses"]["eth_getTransactionReceipt_confirmed"]["node-a"]["result"]

    @fixture_polygon_transport
    async def removed(payload, endpoint):
        if payload["method"] == "eth_getTransactionReceipt":
            result = {**base, "logs": [{"removed": True}]}
        else:
            result = GOLDEN["responses"][_key(payload["method"], payload["params"])][NODE[endpoint]]["result"]
        return {"jsonrpc": "2.0", "id": payload["id"], "result": result}

    with pytest.raises(PolymarketError, match="finality_receipt_removed"):
        await driver(removed).finality_check("0x" + "12" * 32)


async def test_receipt_transaction_hash_mismatch_rejected() -> None:
    base = GOLDEN["responses"]["eth_getTransactionReceipt_confirmed"]["node-a"]["result"]

    @fixture_polygon_transport
    async def transport(payload, endpoint):
        result = {**base, "transactionHash": "0x" + "99" * 32}
        return {"jsonrpc": "2.0", "id": payload["id"], "result": result}

    with pytest.raises(PolymarketError, match="receipt_transaction_hash_mismatch"):
        await driver(transport).eth_get_transaction_receipt("0x" + "12" * 32)


async def test_consensus_mismatch_rejected() -> None:
    transport, _ = make_transport()

    @fixture_polygon_transport
    async def disagree(payload, endpoint):
        raw = await transport(payload, endpoint)
        if endpoint == URLS[-1] and payload["method"] == "eth_chainId":
            raw["result"] = "0x1"
        return raw

    with pytest.raises(PolymarketError, match="rpc_three_origin_consensus_mismatch"):
        await driver(disagree).eth_chain_id(consensus=True)


async def test_jsonrpc_id_xor_error_and_canonical_quantity_strict() -> None:
    @fixture_polygon_transport
    async def bad_id(payload, endpoint):
        return {"jsonrpc": "2.0", "id": payload["id"] + 1, "result": "0x89"}

    with pytest.raises(PolymarketError, match="rpc_response_id_mismatch"):
        await driver(bad_id).eth_chain_id()

    @fixture_polygon_transport
    async def leaked_error(payload, endpoint):
        return {"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -1, "message": "TOKEN=secret"}}

    with pytest.raises(PolymarketError) as error:
        await driver(leaked_error).eth_chain_id()
    assert error.value.reason_code == "rpc_error:-1"
    assert "secret" not in str(error.value) and error.value.detail is None

    @fixture_polygon_transport
    async def leading_zero(payload, endpoint):
        return {"jsonrpc": "2.0", "id": payload["id"], "result": "0x089"}

    with pytest.raises(PolymarketError, match="quantity_invalid"):
        await driver(leading_zero).eth_chain_id()

    @fixture_polygon_transport
    async def coerced_id(payload, endpoint):
        return {"jsonrpc": "2.0", "id": str(payload["id"]), "result": "0x89"}

    with pytest.raises(PolymarketError, match="rpc_response_malformed"):
        await driver(coerced_id).eth_chain_id()

    receipt = GOLDEN["responses"]["eth_getTransactionReceipt_confirmed"]["node-a"]["result"]

    @fixture_polygon_transport
    async def coerced_removed(payload, endpoint):
        return {
            "jsonrpc": "2.0", "id": payload["id"],
            "result": {**receipt, "logs": [{"removed": "false"}]},
        }

    with pytest.raises(PolymarketError, match="receipt_malformed"):
        await driver(coerced_removed).eth_get_transaction_receipt("0x" + "12" * 32)


async def test_erc20_erc1155_balance_and_approval_decoding() -> None:
    @fixture_polygon_transport
    def transport(payload, endpoint):  # synchronous fixture transport is supported
        data = payload["params"][0]["data"]
        value = 1 if data.startswith("0xe985e9c5") else 123
        return {"jsonrpc": "2.0", "id": payload["id"], "result": "0x" + value.to_bytes(32, "big").hex()}

    d = driver(transport)
    token = "0x" + "11" * 20
    owner = "0x" + "22" * 20
    operator = "0x" + "33" * 20
    assert await d.erc20_balance_of(token, owner) == 123
    assert await d.erc1155_balance_of(token, owner, "42") == 123
    assert await d.erc1155_is_approved_for_all(token, owner, operator) is True


async def test_transport_exception_is_sanitized_without_traceback_secret() -> None:
    @fixture_polygon_transport
    async def leaking_transport(payload, endpoint):
        raise RuntimeError("TOPSECRET endpoint=https://user:pass@example.invalid key=abc")

    d = driver(leaking_transport)
    try:
        await d.eth_chain_id()
    except PolymarketError as exc:
        rendered = "".join(traceback.format_exception(exc))
        assert exc.reason_code == "rpc_transport_failure"
        assert "TOPSECRET" not in rendered
        assert "user:pass" not in rendered
        assert exc.__cause__ is None and exc.__suppress_context__ is True
    else:
        raise AssertionError("expected sanitized transport failure")


async def test_real_deployed_wallet_beacon_registry_kind_verifies_independently() -> None:
    transport, _ = make_transport()
    proof = next(
        item for item in REGISTRY["entries"] if item["name"] == "deposit_wallet"
    )["extra"]["factory_beacon_evidence"]
    entry = {
        "address": proof["deployed_wallet_sample"],
        "chain_id": 137,
        "proxy_kind": "beacon",
        "runtime_keccak": proof["deployed_wallet_runtime_keccak"],
        "resolved_implementation_or_beacon": proof["beacon"],
        "resolved_code_keccak": proof["beacon_implementation_code_keccak"],
        "snapshot_block_number": GOLDEN["snapshot_block_number"],
        "snapshot_block_hash": GOLDEN["snapshot_block_hash"],
        "extra": {
            "beacon_slot": GOLDEN["eip1967_beacon_slot"],
            "beacon_runtime_keccak": proof["beacon_runtime_keccak"],
            "beacon_implementation": proof["beacon_implementation"],
        },
    }
    evidence = await driver(transport).verify_registry_entry(entry)
    assert evidence["beacon"] == proof["beacon"]
    assert evidence["implementation"] == proof["beacon_implementation"]
    assert evidence["resolved_code_keccak"] == proof["beacon_implementation_code_keccak"]


@pytest.mark.parametrize("bad_tag", ["safe", "0x00", "0x01", 1, True])
async def test_rpc_block_tags_are_canonical_and_strict(bad_tag) -> None:
    transport, calls = make_transport()
    d = driver(transport)
    with pytest.raises(PolymarketError, match="quantity_invalid"):
        await d.eth_get_code("0x" + "11" * 20, block_tag=bad_tag)
    assert calls == []


@pytest.mark.parametrize("bad_token_id", [True, -1, 1 << 256, "-1", "1.0"])
async def test_erc1155_token_id_is_uint256_not_coerced_bool(bad_token_id) -> None:
    transport, calls = make_transport()
    with pytest.raises(PolymarketError, match="token_id_invalid"):
        await driver(transport).erc1155_balance_of(
            "0x" + "11" * 20, "0x" + "22" * 20, bad_token_id
        )
    assert calls == []


async def test_cancelled_transport_propagates_but_timeout_is_sanitized() -> None:
    @fixture_polygon_transport
    async def cancelled(payload, endpoint):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await driver(cancelled).eth_chain_id()

    @fixture_polygon_transport
    async def timed_out(payload, endpoint):
        raise TimeoutError("TOPSECRET timeout endpoint")

    try:
        await driver(timed_out).eth_chain_id()
    except PolymarketError as exc:
        rendered = "".join(traceback.format_exception(exc))
        assert exc.reason_code == "rpc_transport_failure"
        assert "TOPSECRET" not in rendered
        assert exc.__suppress_context__ is True
    else:
        raise AssertionError("expected sanitized timeout")
