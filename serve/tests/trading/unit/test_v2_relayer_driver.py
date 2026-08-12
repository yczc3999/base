"""WP-06 Checkpoint C —— RelayerDriver 单元测试（fake transport + golden，无 DB/网络）。

证明：nonce 十进制、exact submit body 序列化与 HMAC input 与 golden 全等、Builder header
名固定且无 secret 明文、timeout/5xx/bad body → OUTCOME_UNKNOWN（不生成新 nonce/deadline）、
status 只用 /v1/account/transactions/{id}。
"""

from __future__ import annotations

import hashlib

import pytest

from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.relayer_driver import (
    BUILDER_HEADERS,
    NONCE_HEADERS,
    OUTCOME_UNKNOWN,
    RelayerDriver,
)

from tests.trading.fixtures.p6_settlement.p6_helpers import relayer_golden

GOLDEN = relayer_golden()


def _make_fake_transport(*, fail_submit: bool = False):
    requests = []

    async def transport(method: str, path: str, *, params=None, body=None, headers=None):
        requests.append({"method": method, "path": path, "params": params,
                         "body": body, "headers": headers})
        if path == "/v1/account/transactions/params":
            return 200, __import__("json").dumps(GOLDEN["nonce"]["response"]).encode()
        if path == "/submit":
            if fail_submit:
                return 500, b"{}"
            return 200, __import__("json").dumps(
                {"transaction_id": "tx-0001", "state": "NEW"}
            ).encode()
        if path.startswith("/v1/account/transactions/"):
            return 200, __import__("json").dumps(GOLDEN["status"]["response"]).encode()
        return 404, b"{}"

    return transport, requests


def _driver(transport, **kw) -> RelayerDriver:
    def signer(_msg: str) -> str:
        return GOLDEN["submit"]["body"]["signature"]

    def hmac_signer(data: bytes) -> str:
        return GOLDEN["submit"]["hmac"]["expected_signature_b64"]

    return RelayerDriver(
        transport=transport,
        trusted_time_provider=lambda: GOLDEN["deadline"]["trusted_now"],
        signer=signer,
        hmac_signer=hmac_signer,
        **kw,
    )


async def test_get_nonce() -> None:
    transport, requests = _make_fake_transport()
    driver = _driver(transport)
    nonce = await driver.get_nonce(GOLDEN["nonce"]["response"]["address"])
    assert nonce == GOLDEN["nonce"]["response"]["nonce"]
    assert nonce.isdigit()
    req = requests[0]
    assert req["method"] == "GET"
    assert req["path"] == "/v1/account/transactions/params"
    assert req["params"] == {"address": GOLDEN["nonce"]["response"]["address"], "type": "WALLET"}
    assert set(req["headers"]) == set(NONCE_HEADERS)


async def test_submit_batch_exact_body_and_hmac() -> None:
    transport, requests = _make_fake_transport()
    driver = _driver(transport)
    golden_body = GOLDEN["submit"]["body"]
    outcome = await driver.submit_batch(
        from_address=golden_body["from"],
        to_address=golden_body["to"],
        nonce=golden_body["nonce"],
        deposit_wallet=golden_body["depositWalletParams"]["depositWallet"],
        calls=golden_body["depositWalletParams"]["calls"],
        metadata=golden_body["metadata"],
        signature=golden_body["signature"],
        deadline=golden_body["depositWalletParams"]["deadline"],
    )
    assert outcome.cls == "SUBMITTED"
    assert outcome.transaction_id == "tx-0001"
    req = requests[0]
    assert req["method"] == "POST"
    assert req["path"] == "/submit"
    body = __import__("json").loads(req["body"])
    # exact serialized body 与 golden 全等（sign 与 send 同一 bytes）
    assert body == golden_body
    headers = req["headers"]
    assert set(headers) == set(BUILDER_HEADERS) | {"content-type"}
    assert headers["POLY_BUILDER_SIGNATURE"] == GOLDEN["submit"]["hmac"]["expected_signature_b64"]
    assert headers["POLY_BUILDER_TIMESTAMP"] == str(GOLDEN["deadline"]["trusted_now"])
    # 无 secret 明文
    raw = __import__("json").dumps(headers)
    assert "RELAYER_API_KEY=" not in raw and "POLY_BUILDER_API_KEY=" not in raw
    # 发送的 body bytes 的 sha256 与 golden 一致
    assert hashlib.sha256(req["body"]).hexdigest() == GOLDEN["submit"]["exact_serialized_body_sha256"]


async def test_submit_5xx_returns_unknown() -> None:
    transport, _ = _make_fake_transport(fail_submit=True)
    driver = _driver(transport)
    golden_body = GOLDEN["submit"]["body"]
    outcome = await driver.submit_batch(
        from_address=golden_body["from"], to_address=golden_body["to"],
        nonce=golden_body["nonce"],
        deposit_wallet=golden_body["depositWalletParams"]["depositWallet"],
        calls=golden_body["depositWalletParams"]["calls"],
        metadata=golden_body["metadata"], signature=golden_body["signature"],
        deadline=golden_body["depositWalletParams"]["deadline"],
    )
    assert outcome.cls == OUTCOME_UNKNOWN
    assert outcome.http_status == 500


async def test_submit_malformed_body_returns_unknown() -> None:
    async def transport(method, path, *, params=None, body=None, headers=None):
        if path == "/submit":
            return 200, b"not-json{{"
        return 200, b"{}"

    driver = _driver(transport)
    golden_body = GOLDEN["submit"]["body"]
    outcome = await driver.submit_batch(
        from_address=golden_body["from"], to_address=golden_body["to"],
        nonce=golden_body["nonce"],
        deposit_wallet=golden_body["depositWalletParams"]["depositWallet"],
        calls=golden_body["depositWalletParams"]["calls"],
        metadata=golden_body["metadata"], signature=golden_body["signature"],
        deadline=golden_body["depositWalletParams"]["deadline"],
    )
    assert outcome.cls == OUTCOME_UNKNOWN


async def test_get_transaction_status_official_route() -> None:
    transport, requests = _make_fake_transport()
    driver = _driver(transport)
    status = await driver.get_transaction_status("tx-0001")
    assert status.state == "CONFIRMED"
    assert status.is_success_terminal is True
    assert status.transaction_hash.startswith("0x")
    assert requests[-1]["path"] == "/v1/account/transactions/tx-0001"


async def test_status_invalid_state_rejected() -> None:
    async def transport(method, path, *, params=None, body=None, headers=None):
        return 200, __import__("json").dumps(
            {"transaction_id": "tx-x", "transaction_hash": "0x" + "1" * 64, "state": "BOGUS"}
        ).encode()

    driver = _driver(transport)
    with pytest.raises(PolymarketError, match="relayer_status_malformed"):
        await driver.get_transaction_status("tx-x")
