"""WP-06 Checkpoint A —— Polygon JSON-RPC contract（frozen golden，无网络）。

证明：三个 RPC 节点在同一 finalized snapshot block 对 chainId/block/code/storage/call/
receipt 逐项一致；eth_getCode 返回满长 hex 字节，keccak-256 与 registry 中 runtime /
resolved code hash 全长相等；EIP-1967 implementation slot 与 Beacon ``implementation()``
都能解析到实现代码；receipt 的 status/blockNumber/blockHash 结构、reorg/removed 语义
与 fixture 一致。不访问公网，不写 DB。
"""

from __future__ import annotations

import json

import pytest

from tests.trading.fixtures.p6_settlement.p6_helpers import (
    code_keccak,
    registry,
    rpc_golden,
    selector,
    slot32,
    verify_three_rpc_agreement,
)

CHAIN_ID = 137
SNAPSHOT_BLOCK = 91842167


def test_three_rpc_nodes_agree_on_every_response() -> None:
    verify_three_rpc_agreement()


def test_eth_chain_id() -> None:
    golden = rpc_golden()
    resp = golden["responses"]["eth_chainId"]["node-a"]
    assert resp["result"] == hex(CHAIN_ID)


def test_snapshot_block_hash_frozen() -> None:
    golden = rpc_golden()
    block = golden["responses"]["eth_getBlockByNumber_snapshot"]["node-a"]["result"]
    assert block["number"] == hex(SNAPSHOT_BLOCK)
    assert block["hash"] == golden["snapshot_block_hash"]


def test_finalized_block_has_higher_number_than_receipt() -> None:
    golden = rpc_golden()
    finalized = golden["responses"]["eth_getBlockByNumber_finalized"]["node-a"]["result"]
    receipt = golden["responses"]["eth_getTransactionReceipt_confirmed"]["node-a"]["result"]
    assert int(finalized["number"], 16) > int(receipt["blockNumber"], 16)


@pytest.mark.parametrize(
    "rpc_key,reg_name",
    [
        ("eth_getCode_pusd", "pusd"),
        ("eth_getCode_ctf", "ctf"),
        ("eth_getCode_deposit_wallet", "deposit_wallet"),
        ("eth_getCode_ctf_adapter", "ctf_adapter_standard"),
        ("eth_getCode_neg_risk_adapter", "neg_risk_adapter"),
    ],
)
def test_runtime_code_keccak_matches_registry(rpc_key: str, reg_name: str) -> None:
    golden = rpc_golden()
    code = golden["responses"][rpc_key]["node-a"]["result"]
    assert code.startswith("0x") and (len(code) - 2) % 2 == 0
    assert len(code) - 2 >= 512, "code must be full-length, not truncated"
    entry = {e["name"]: e for e in registry()["entries"]}[reg_name]
    assert code_keccak(code) == entry["runtime_keccak"]


def test_eip1967_deposit_wallet_implementation_slot() -> None:
    golden = rpc_golden()
    reg = {e["name"]: e for e in registry()["entries"]}
    entry = reg["deposit_wallet"]
    impl_addr = entry["resolved_implementation_or_beacon"]
    slot_val = golden["responses"]["eth_getStorageAt_deposit_wallet_impl"]["node-a"]["result"]
    assert slot_val == slot32(impl_addr)
    impl_code = golden["responses"]["eth_getCode_deposit_wallet_impl"]["node-a"]["result"]
    assert code_keccak(impl_code) == entry["resolved_code_keccak"]
    # proxy-only hash 不算通过：impl 处代码必须全长 keccak。
    assert entry["resolved_code_keccak"] != entry["runtime_keccak"]


def test_neg_risk_adapter_eip1967_implementation() -> None:
    golden = rpc_golden()
    reg = {e["name"]: e for e in registry()["entries"]}
    entry = reg["neg_risk_adapter"]
    impl_addr = entry["resolved_implementation_or_beacon"]
    slot_val = golden["responses"]["eth_getStorageAt_neg_risk_adapter_impl"]["node-a"]["result"]
    assert slot_val == slot32(impl_addr)
    impl_code = golden["responses"]["eth_getCode_neg_risk_adapter_impl"]["node-a"]["result"]
    assert code_keccak(impl_code) == entry["resolved_code_keccak"]


def test_beacon_proxy_resolves_implementation_and_code() -> None:
    golden = rpc_golden()
    reg = {e["name"]: e for e in registry()["entries"]}
    entry = reg["ctf_adapter_standard"]
    assert entry["proxy_kind"] == "beacon"
    beacon_addr = entry["resolved_implementation_or_beacon"]
    # beacon slot 指向 beacon 合约
    slot_val = golden["responses"]["eth_getStorageAt_ctf_adapter_beacon"]["node-a"]["result"]
    assert slot_val == slot32(beacon_addr)
    # beacon runtime code keccak
    beacon_code = golden["responses"]["eth_getCode_beacon"]["node-a"]["result"]
    assert code_keccak(beacon_code) == entry["beacon_runtime_keccak"]
    # beacon.implementation() 返回最终实现
    impl_call = golden["responses"]["eth_call_beacon_implementation"]["node-a"]["result"]
    assert impl_call == slot32(entry["beacon_implementation"])
    # 最终实现代码 keccak 与 resolved 一致
    impl_code = golden["responses"]["eth_getCode_beacon_impl"]["node-a"]["result"]
    assert code_keccak(impl_code) == entry["beacon_implementation_code_keccak"]
    assert entry["resolved_code_keccak"] == entry["beacon_implementation_code_keccak"]
    assert entry["beacon_implementation_code_keccak"] != entry["runtime_keccak"]


def test_receipt_contract() -> None:
    golden = rpc_golden()
    confirmed = golden["responses"]["eth_getTransactionReceipt_confirmed"]["node-a"]["result"]
    assert confirmed["status"] == "0x1"
    assert confirmed["transactionHash"].startswith("0x")
    assert confirmed["blockNumber"].startswith("0x")
    assert confirmed["blockHash"].startswith("0x")
    failed = golden["responses"]["eth_getTransactionReceipt_failed"]["node-a"]["result"]
    assert failed["status"] == "0x0"
    missing = golden["responses"]["eth_getTransactionReceipt_missing"]["node-a"]["result"]
    assert missing is None  # receipt 消失 → UNKNOWN/REORGED


def test_rpc_request_shapes_strict() -> None:
    golden = rpc_golden()
    req = golden["requests"]
    assert req["eth_getCode_pusd"]["method"] == "eth_getCode"
    assert req["eth_getCode_pusd"]["params"][1] == hex(SNAPSHOT_BLOCK)
    assert req["eth_getStorageAt_deposit_wallet_impl"]["params"][1] == golden["eip1967_implementation_slot"]
    assert req["eth_getBlockByNumber_finalized"]["params"][0] == "finalized"
    assert req["eth_call_beacon_implementation"]["params"][0]["data"] == selector("implementation()")


def test_rpc_responses_strict_shape_and_quantity() -> None:
    golden = rpc_golden()
    for key, resp in golden["responses"].items():
        for node, payload in resp.items():
            assert isinstance(payload, dict), key
            if key.startswith("eth_getCode"):
                result = payload["result"]
                assert result.startswith("0x") and (len(result) - 2) % 2 == 0
            elif key.startswith("eth_getStorageAt") or key.startswith("eth_call"):
                result = payload["result"]
                assert result.startswith("0x") and len(result) == 66, f"{key} not 32-byte slot"
