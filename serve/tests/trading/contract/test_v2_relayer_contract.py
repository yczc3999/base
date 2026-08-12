"""Official py-sdk Deposit Wallet EIP-712/body/auth/calldata frozen wire contract."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from eth_abi import decode as abi_decode
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak

from tests.trading.fixtures.p6_settlement.p6_helpers import relayer_golden

G = relayer_golden()


def _typed_message():
    body = G["submit"]["body"]
    call = body["depositWalletParams"]["calls"][0]
    return {
        "domain": G["eip712"]["domain"],
        "types": {
            "EIP712Domain": G["eip712"]["eip712_domain_fields"],
            **G["eip712"]["types"],
        },
        "primaryType": "Batch",
        "message": {
            "wallet": body["depositWalletParams"]["depositWallet"],
            "nonce": int(body["nonce"]),
            "deadline": int(body["depositWalletParams"]["deadline"]),
            "calls": [{"target": call["target"], "value": int(call["value"]),
                       "data": bytes.fromhex(call["data"][2:])}],
        },
    }


def test_nonce_and_raw_state_wire_exact() -> None:
    assert G["nonce"]["request"]["headers_required"] == ["RELAYER_API_KEY", "RELAYER_API_KEY_ADDRESS"]
    assert G["nonce"]["response"]["nonce"].isdigit()
    assert G["submit"]["response"] == {
        "state": "STATE_NEW", "transactionHash": None, "transactionID": "tx-0001"
    }
    assert G["status"]["response"]["state"] == "STATE_CONFIRMED"
    assert G["status"]["path_template"] == "/v1/account/transactions/{transaction_id}"
    assert G["status"]["legacy_frozen_as"] == "DRIFT_NOT_USED"


def test_eip712_real_fixture_signature_hash_and_recovery() -> None:
    signable = encode_typed_data(full_message=_typed_message())
    digest = "0x" + keccak(b"\x19" + signable.version + signable.header + signable.body).hex()
    assert digest == G["eip712"]["typed_data_hash"]
    recovered = Account.recover_message(signable, signature=G["eip712"]["signature"])
    assert recovered.lower() == G["eip712"]["fixture_signer_address"]
    assert recovered.lower() == G["eip712"]["recovered_signer"]
    assert G["eip712"]["domain"]["verifyingContract"] == G["signer"]["deposit_wallet"]
    assert G["submit"]["body"]["to"] == G["signer"]["factory"]
    assert G["signature"]["independently_recoverable"] is True


def test_exact_body_hmac_and_builder_headers() -> None:
    exact = json.dumps(G["submit"]["body"], separators=(",", ":"))
    assert exact == G["submit"]["exact_serialized_body"]
    assert hashlib.sha256(exact.encode()).hexdigest() == G["submit"]["exact_serialized_body_sha256"]
    expected_input = f"{G['deadline']['trusted_now']}POST/submit{exact}"
    assert expected_input == G["submit"]["hmac"]["input"]
    secret = hashlib.sha256(b"pm-v2/fixture/builder-secret/v1").digest()
    signature = base64.urlsafe_b64encode(hmac.new(secret, expected_input.encode(), hashlib.sha256).digest()).decode()
    assert signature == G["submit"]["hmac"]["expected_signature_b64"]
    assert G["submit"]["headers"] == [
        "POLY_BUILDER_API_KEY", "POLY_BUILDER_TIMESTAMP",
        "POLY_BUILDER_PASSPHRASE", "POLY_BUILDER_SIGNATURE",
    ]


def test_standard_calldata_independent_abi_decode_order_and_values() -> None:
    condition = bytes.fromhex(G["conditions"]["condition_id"][2:])
    parent = bytes.fromhex(G["amounts"]["parent_collection_id"][2:])
    partition = tuple(int(x) for x in G["amounts"]["partition"])
    amount = G["amounts"]["pusd_base_units_per_pair"]
    vectors = [
        ("split_standard", ["address", "bytes32", "bytes32", "uint256[]", "uint256"],
         (G["submit"]["body"]["depositWalletParams"]["calls"][0]["data"], G["calldata"]["split_standard"]),
         G["submit"]["body"]["depositWalletParams"]["calls"][0]["target"], G["adapters"]["standard"]),
        ("merge_standard", ["address", "bytes32", "bytes32", "uint256[]", "uint256"],
         (G["calldata"]["merge_standard"], G["calldata"]["merge_standard"]), None, None),
    ]
    for name, types, pair, target, adapter in vectors:
        assert pair[0] == pair[1]
        data = G["calldata"][name]
        collateral, decoded_parent, decoded_condition, decoded_partition, decoded_amount = abi_decode(types, bytes.fromhex(data[10:]))
        expected_collateral = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
        assert collateral.lower() == expected_collateral
        assert decoded_parent == parent
        assert decoded_condition == condition
        assert decoded_partition == partition
        assert decoded_amount == amount
        if target is not None:
            assert target.lower() == adapter.lower()
    collateral, decoded_parent, decoded_condition, decoded_partition = abi_decode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        bytes.fromhex(G["calldata"]["redeem_standard"][10:]),
    )
    assert collateral.lower() == "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
    assert decoded_parent == parent and decoded_condition == condition
    assert decoded_partition == partition
    assert len(G["calldata"]["split_standard"]) == 522
    assert len(G["calldata"]["redeem_standard"]) == 458
