"""WP-06 Checkpoint A —— Relayer Deposit Wallet wire contract（frozen golden，无网络）。

证明：nonce 请求/响应与 golden 全等；EIP-712 domain/types 与 golden 全等（用 eth_account
encode_typed_data 构造同一 typed data）；submit body 的 exact serialized bytes 与 golden
全等，HMAC input 使用同一 exact bytes（sign 与 send 共用同一对象）；Builder header 名/
顺序与 golden 全等且不含任何 secret 明文；status 只用官方
``/v1/account/transactions/{id}``（legacy ``/transaction`` 记录为 drift，无 fallback）。
不访问公网、不落 signature/secret。
"""

from __future__ import annotations

import json

from eth_account.messages import encode_typed_data

from tests.trading.fixtures.p6_settlement.p6_helpers import relayer_golden

CHAIN_ID = 137
DEPOSIT_WALLET = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"


def test_nonce_request_and_response() -> None:
    golden = relayer_golden()
    nonce = golden["nonce"]
    assert nonce["request"]["method"] == "GET"
    assert nonce["request"]["path"] == "/v1/account/transactions/params"
    assert nonce["request"]["params"]["type"] == "WALLET"
    assert nonce["request"]["headers_required"] == ["RELAYER_API_KEY", "RELAYER_API_KEY_ADDRESS"]
    resp = nonce["response"]
    assert resp["address"].startswith("0x")
    assert resp["nonce"].isdigit(), "nonce must be a non-empty decimal string"
    assert int(resp["nonce"]) >= 0


def test_eip712_domain_and_types_exact() -> None:
    golden = relayer_golden()
    domain = golden["eip712"]["domain"]
    assert domain == {
        "name": "DepositWallet",
        "version": "1",
        "chainId": CHAIN_ID,
        "verifyingContract": DEPOSIT_WALLET,
    }
    types = golden["eip712"]["types"]
    assert types["Call"] == [
        {"name": "target", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "data", "type": "bytes"},
    ]
    assert types["Batch"] == [
        {"name": "wallet", "type": "address"},
        {"name": "nonce", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
        {"name": "calls", "type": "Call[]"},
    ]
    assert golden["eip712"]["primary_type"] == "Batch"


def test_eip712_typed_data_encodeable() -> None:
    golden = relayer_golden()
    body = golden["submit"]["body"]
    call = body["depositWalletParams"]["calls"][0]
    typed = {
        "domain": golden["eip712"]["domain"],
        "types": golden["eip712"]["types"],
        "primaryType": "Batch",
        "message": {
            "wallet": body["depositWalletParams"]["depositWallet"],
            "nonce": int(body["nonce"]),
            "deadline": int(body["depositWalletParams"]["deadline"]),
            "calls": [
                {
                    "target": call["target"],
                    "value": int(call["value"]),
                    "data": bytes.fromhex(call["data"][2:]),
                }
            ],
        },
    }
    structured = encode_typed_data(full_message=typed)
    assert len(structured.body) == 32  # 可编码且生成 digest


def test_submit_body_exact_serialized_bytes() -> None:
    golden = relayer_golden()
    body = golden["submit"]["body"]
    assert body["type"] == "WALLET"
    assert body["to"] == DEPOSIT_WALLET
    assert body["from"].startswith("0x")
    assert body["nonce"] == golden["nonce"]["response"]["nonce"]
    assert body["depositWalletParams"]["depositWallet"] == DEPOSIT_WALLET
    assert body["depositWalletParams"]["deadline"] == str(golden["deadline"]["deadline"])
    # exact serialized bytes 必须与 body 的直接 JSON 序列化一致（sign 与 send 共用同一对象）
    exact = json.dumps(body, separators=(",", ":")).encode().decode()
    assert exact == golden["submit"]["exact_serialized_body"]
    assert body["signature"].startswith("0x")
    assert len(body["signature"]) == 132  # 0x + 65 bytes hex


def test_hmac_input_uses_exact_serialized_body() -> None:
    golden = relayer_golden()
    body = golden["submit"]["body"]
    exact = json.dumps(body, separators=(",", ":")).encode().decode()
    hmac_input = golden["submit"]["hmac"]["input"]
    assert hmac_input == f"{golden['deadline']['trusted_now']}POST/submit" + exact
    assert golden["submit"]["hmac"]["input_template"] == (
        "{timestamp}{UPPERCASE_METHOD}{path_without_query}{exact_serialized_body}"
    )
    assert golden["submit"]["hmac"]["algorithm"] == "hmac-sha256"
    assert golden["submit"]["hmac"]["secret_base64_decoded"] is True
    assert golden["submit"]["hmac"]["signature_b64_urlsafe_padded"] is True
    assert golden["submit"]["hmac"]["secret_not_in_fixture"] is True


def test_builder_headers_exact_and_no_secret_plaintext() -> None:
    golden = relayer_golden()
    headers = golden["submit"]["headers"]
    assert headers == [
        "POLY_BUILDER_API_KEY",
        "POLY_BUILDER_TIMESTAMP",
        "POLY_BUILDER_PASSPHRASE",
        "POLY_BUILDER_SIGNATURE",
    ]
    markers = golden["submit"]["header_secret_markers"]
    assert markers["POLY_BUILDER_API_KEY"] == "secret-ref"
    assert markers["POLY_BUILDER_TIMESTAMP"] == "unix-seconds"
    assert markers["POLY_BUILDER_PASSPHRASE"] == "secret-ref"
    assert markers["POLY_BUILDER_SIGNATURE"] == "computed"
    # 任何 header 明文 secret 都不得进入 fixture
    raw = json.dumps(golden)
    assert "RELAYER_API_KEY=" not in raw
    assert "POLY_BUILDER_API_KEY=" not in raw


def test_status_only_official_route_no_legacy_fallback() -> None:
    golden = relayer_golden()
    status = golden["status"]
    assert status["path_template"] == "/v1/account/transactions/{transaction_id}"
    assert status["legacy_path"] == "/transaction?id=..."
    assert status["legacy_frozen_as"] == "DRIFT_NOT_USED"
    assert golden["base_url"] == "https://relayer-v2.polymarket.com"
    # 成功/失败响应结构
    ok = status["response"]
    assert ok["transaction_id"] == "tx-0001"
    assert ok["transaction_hash"].startswith("0x")
    assert ok["state"] == "CONFIRMED"
    failed = status["response_failed"]
    assert failed["state"] == "FAILED"
    assert status["states"]["success_terminal"] == ["CONFIRMED"]
    assert status["states"]["failure_terminal"] == ["INVALID", "FAILED"]
    assert status["states"]["confirmed_is_not_finality"] is True


def test_deadline_rule() -> None:
    golden = relayer_golden()
    assert golden["deadline"]["ttl_s"] == 600
    assert golden["deadline"]["deadline"] == golden["deadline"]["trusted_now"] + 600
    assert golden["deadline"]["rule"] == "deadline = trusted_now + 600s"


def test_calldata_standard_and_neg_risk() -> None:
    golden = relayer_golden()
    calldata = golden["calldata"]
    for key in ("split_standard", "merge_standard", "redeem_standard"):
        data = calldata[key]
        assert data.startswith("0x") and (len(data) - 2) % 2 == 0
        assert len(data) - 2 >= 128
        # calldata 是 keccak 覆盖的确定性字节
        assert golden["calldata_keccak"][key].startswith("0x")
        assert len(golden["calldata_keccak"][key]) == 66
    assert golden["amounts"]["pusd_base_units_per_pair"] == 1_000_000
    assert golden["amounts"]["partition"] == ["1", "2"]
    assert golden["amounts"]["pusd_decimals"] == 6
    assert golden["adapters"]["standard"] == "0xAdA100Db00Ca00073811820692005400218FcE1f"
    assert golden["adapters"]["neg_risk"] == "0xadA2005600Dec949baf300f4C6120000bDB6eAab"


def test_signature_is_fake_and_full_length() -> None:
    golden = relayer_golden()
    sig = golden["signature"]
    assert sig["length_hex"] == 130
    assert sig["full_length"] is True
    assert sig["not_a_real_signature"] is True
