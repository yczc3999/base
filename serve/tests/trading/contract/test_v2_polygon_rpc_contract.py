"""Frozen three-origin Polygon registry/finality contract; no live egress."""
from __future__ import annotations

import hashlib
import json

from tests.trading.fixtures.p6_settlement.p6_helpers import (
    code_keccak,
    registry,
    rpc_golden,
    slot32,
    verify_three_rpc_agreement,
)

CHAIN_ID = 137
SNAPSHOT_BLOCK = 91842167


def test_three_independent_origin_snapshot_agreement_and_response_hashes() -> None:
    golden = rpc_golden()
    assert len(golden["rpc_nodes"]) == 3
    assert len(set(golden["endpoint_origin_sha256"].values())) == 3
    assert all(len(value) == 64 for value in golden["endpoint_origin_sha256"].values())
    verify_three_rpc_agreement()
    for key, per_node in golden["responses"].items():
        for node, response in per_node.items():
            digest = hashlib.sha256(
                json.dumps(response, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            assert digest == golden["response_sha256"][key][node]


def test_snapshot_block_and_chain_are_real_frozen_values() -> None:
    golden = rpc_golden()
    block = golden["responses"]["eth_getBlockByNumber_snapshot"]["node-a"]["result"]
    assert golden["responses"]["eth_chainId"]["node-a"]["result"] == "0x89"
    assert block["number"] == hex(SNAPSHOT_BLOCK)
    assert block["hash"] == golden["snapshot_block_hash"]
    assert golden["source_evidence"]["raw_complete_bytes"] is True


def test_all_registry_runtime_bytes_and_resolution_paths() -> None:
    golden = rpc_golden()
    entries = {item["name"]: item for item in registry()["entries"]}
    keys = {
        "pusd": "pusd", "ctf": "ctf", "deposit_wallet": "deposit_wallet",
        "ctf_adapter_standard": "ctf_adapter", "neg_risk_adapter": "neg_risk_adapter",
    }
    assert {name: item["proxy_kind"] for name, item in entries.items()} == {
        "pusd": "eip1967", "ctf": "none", "deposit_wallet": "eip1967",
        "ctf_adapter_standard": "none", "neg_risk_adapter": "none",
    }
    for name, key in keys.items():
        entry = entries[name]
        code = golden["responses"][f"eth_getCode_{key}"]["node-a"]["result"]
        assert code.startswith("0x") and len(code) > 2 and (len(code) - 2) % 2 == 0
        assert code_keccak(code) == entry["runtime_keccak"]
        assert entry["extra"]["runtime_code_bytes"] == (len(code) - 2) // 2
        if entry["proxy_kind"] == "eip1967":
            slot = golden["responses"][f"eth_getStorageAt_{key}_impl"]["node-a"]["result"]
            assert slot == slot32(entry["resolved_implementation_or_beacon"])
            impl = golden["responses"][f"eth_getCode_{key}_impl"]["node-a"]["result"]
            assert code_keccak(impl) == entry["resolved_code_keccak"]
            assert entry["runtime_keccak"] != entry["resolved_code_keccak"]
        else:
            assert entry["resolved_implementation_or_beacon"] is None
            assert entry["resolved_code_keccak"] == entry["runtime_keccak"]


def test_factory_proxy_and_real_deployed_wallet_beacon_are_distinct_paths() -> None:
    golden = rpc_golden()
    entry = next(item for item in registry()["entries"] if item["name"] == "deposit_wallet")
    proof = entry["extra"]["factory_beacon_evidence"]
    factory_result = golden["responses"]["eth_call_deposit_wallet_factory_beacon"]["node-a"]["result"]
    assert factory_result == slot32(proof["beacon"])
    wallet_code = golden["responses"]["eth_getCode_deployed_wallet_sample"]["node-a"]["result"]
    assert code_keccak(wallet_code) == proof["deployed_wallet_runtime_keccak"]
    wallet_slot = golden["responses"]["eth_getStorageAt_deployed_wallet_beacon"]["node-a"]["result"]
    assert wallet_slot == slot32(proof["beacon"])
    beacon_code = golden["responses"]["eth_getCode_deposit_wallet_beacon"]["node-a"]["result"]
    assert code_keccak(beacon_code) == proof["beacon_runtime_keccak"]
    implementation = golden["responses"]["eth_call_deposit_wallet_beacon_implementation"]["node-a"]["result"]
    assert implementation == slot32(proof["beacon_implementation"])
    impl_code = golden["responses"]["eth_getCode_deposit_wallet_beacon_impl"]["node-a"]["result"]
    assert code_keccak(impl_code) == proof["beacon_implementation_code_keccak"]


def test_receipt_and_finalized_vectors_are_explicitly_synthetic_conformance() -> None:
    golden = rpc_golden()
    assert set(golden["source_evidence"]["synthetic_keys"]) >= {
        "eth_getTransactionReceipt_confirmed", "eth_getBlockByNumber_finalized"
    }
    receipt = golden["responses"]["eth_getTransactionReceipt_confirmed"]["node-a"]["result"]
    finalized = golden["responses"]["eth_getBlockByNumber_finalized"]["node-a"]["result"]
    assert receipt["status"] == "0x1"
    assert int(finalized["number"], 16) > int(receipt["blockNumber"], 16)


def test_capture_command_is_local_reproducible_and_does_not_embed_endpoints() -> None:
    from pathlib import Path

    command = rpc_golden()["source_evidence"]["capture_command"]
    assert command.startswith("./.venv/bin/python ")
    assert "http://" not in command and "https://" not in command
    relative_script = command.split()[1]
    assert Path(relative_script).is_file()
    source = Path(relative_script).read_text(encoding="utf-8")
    assert "PM_V2_POLYGON_RPC_URLS" in source
    assert "endpoint_origin_sha256" in source
