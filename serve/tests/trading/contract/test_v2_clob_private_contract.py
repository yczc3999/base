"""WP-05 Checkpoint C：private CLOB contract（SDK type-3 wire golden + /v1/heartbeats ID 链）。

证明：maker/funder 为 Deposit Wallet、signatureType=3、ERC-7739 wrapper、EOA recovery、
Standard domain（Polymarket CTF Exchange/v2/chainId 137）、final wire body hash 确定性可复算。
不访问公网、不落任何 signature/secret 明文。
"""

from __future__ import annotations

import hashlib
import json

import pytest

from eth_account import Account
from eth_account.messages import encode_typed_data
from polymarket._internal.actions.orders.orders import create_signed_order
from polymarket._internal.actions.orders.types import BYTES32_ZERO, UnsignedOrder
from polymarket._internal.actions.orders.typed_data import (
    build_order_signature,
    build_order_typed_data,
)

from app.services.polymarket.clob_trading_driver import (
    ClobTradingDriver,
    canonical_order_body_hash,
    expected_order_hash_for,
    sdk_manifest_hash_for,
)
from app.services.polymarket.service import PolymarketService

CHAIN_ID = 137
EXCHANGE_STANDARD = "0x" + "11" * 20
EXCHANGE_NEGRISK = "0x" + "33" * 20
TOKEN_ID = "1234567890123456789012345678901234567890123456789012345678901234"


def _unsigned(chain_id=CHAIN_ID, exchange_address=EXCHANGE_STANDARD) -> UnsignedOrder:
    return UnsignedOrder(
        chain_id=chain_id,
        builder=BYTES32_ZERO,
        exchange_address=exchange_address,
        expiration=2_000_000_000,
        maker="0x" + "22" * 20,   # Deposit Wallet maker
        maker_amount=100,
        metadata=BYTES32_ZERO,
        order_type="GTC",
        salt=123_456_789,
        side="BUY",
        signature_type=3,
        signer="0x" + "22" * 20,  # Deposit Wallet signer
        taker_amount=55,
        timestamp=1_700_000_000,
        token_id=TOKEN_ID,
    )


def _signed_order(unsigned: UnsignedOrder, eoa: Account):
    typed = build_order_typed_data(unsigned)
    message = encode_typed_data(full_message=typed)
    inner = eoa.sign_message(message).signature.hex()
    full = build_order_signature(unsigned, "0x" + inner)
    return create_signed_order(unsigned, full, post_only=False)


def _golden(unsigned, signed_order, expected_eoa) -> dict:
    driver = ClobTradingDriver(client=None)
    return driver.validate_signed_order_golden(
        signed_order, chain_id=unsigned.chain_id,
        exchange_address=unsigned.exchange_address, expected_eoa=expected_eoa,
    )


def test_sdk_type3_wire_golden_standard_domain():
    eoa = Account.from_key(b"\x01" * 32)
    unsigned = _unsigned()
    signed_order = _signed_order(unsigned, eoa)
    golden = _golden(unsigned, signed_order, eoa.address)

    assert golden["signature_type"] == 3
    assert golden["signature_type_is_three"] is True
    assert golden["deposit_wallet_maker_funder"] is True       # maker == signer == Deposit Wallet
    assert golden["eoa_recovery_matches"] is True               # 内层签名恢复 EOA
    assert golden["erc7739_wrapper"] is True                    # ERC-7739 trailer 存在
    assert golden["domain_name"] == "Polymarket CTF Exchange"
    assert golden["domain_version"] == "2"
    assert golden["chain_id"] == 137
    assert golden["verifying_contract"] == EXCHANGE_STANDARD
    assert len(golden["final_wire_body_hash"]) == 64


def test_sdk_type3_wire_golden_negrisk_domain():
    eoa = Account.from_key(b"\x02" * 32)
    unsigned = _unsigned(exchange_address=EXCHANGE_NEGRISK)
    signed_order = _signed_order(unsigned, eoa)
    golden = _golden(unsigned, signed_order, eoa.address)
    assert golden["eoa_recovery_matches"] is True
    assert golden["verifying_contract"] == EXCHANGE_NEGRISK


def test_sdk_golden_consistent_with_fixture():
    """SDK golden 与 p5 private_clob_golden_v1.json 的 identity 断言一致。"""
    from tests.trading.fixtures.p5_execution.p5_helpers import frozen_scenario

    golden = frozen_scenario("clob_golden")
    assert golden["identity"]["signature_type"] == 3
    assert golden["identity"]["signing_actor"] == "EOA"
    assert golden["identity"]["maker"] == "Deposit Wallet"
    assert golden["identity"]["funder"] == "Deposit Wallet"
    assert golden["identity"]["wire_signer"] == "Deposit Wallet"
    assert golden["identity"]["inner_signature_recovery"] == "EOA"
    assert golden["identity"]["erc7739_wrapper"] is True
    assert golden["submit"]["endpoint"] == "POST /order"
    assert golden["submit"]["transport_policy"] == "single_send_no_automatic_retry"


def test_expected_order_hash_and_sdk_manifest_hash_deterministic():
    eoa = Account.from_key(b"\x03" * 32)
    unsigned = _unsigned()
    signed_order = _signed_order(unsigned, eoa)
    h1 = expected_order_hash_for(signed_order, chain_id=137, exchange_address=EXCHANGE_STANDARD)
    h2 = expected_order_hash_for(signed_order, chain_id=137, exchange_address=EXCHANGE_STANDARD)
    assert h1 == h2 and len(h1) == 64
    s1 = sdk_manifest_hash_for(signed_order)
    s2 = sdk_manifest_hash_for(signed_order)
    assert s1 == s2 and len(s1) == 64
    assert canonical_order_body_hash(signed_order) == canonical_order_body_hash(signed_order)


def test_l2_hmac_canonical_input_matches_frozen_contract():
    driver = ClobTradingDriver(client=None)
    msg = driver.l2_hmac_input(
        unix_seconds=1_700_000_000, method="POST", path_without_query="/order", body=b"{}",
    )
    assert msg == "1700000000POST/order{}"
    # 无 body 时为空串。
    empty = driver.l2_hmac_input(
        unix_seconds=1_700_000_000, method="GET", path_without_query="/time", body=None,
    )
    assert empty == "1700000000GET/time"


class _HeartbeatClient:
    def __init__(self, chain):
        self._chain = list(chain)
        self.calls = []

    def post_heartbeat(self, heartbeat_id):
        self.calls.append(heartbeat_id)
        if not self._chain:
            return {"heartbeat_id": None}
        return {"heartbeat_id": self._chain.pop(0)}


@pytest.mark.asyncio
async def test_heartbeat_id_chain_single_path_no_fallback():
    """/v1/heartbeats 首空 ID → 轮换 ID 链；一次请求一个 ID；不双发/不 fallback。"""
    client = _HeartbeatClient(["hb-1", "hb-2", "hb-3"])
    driver = ClobTradingDriver(client=client)
    first = await driver.send_heartbeat("")
    assert first["ok"] is True and first["heartbeat_id"] == "hb-1"
    second = await driver.send_heartbeat("hb-1")
    assert second["heartbeat_id"] == "hb-2"
    third = await driver.send_heartbeat("hb-2")
    assert third["heartbeat_id"] == "hb-3"
    assert client.calls == ["", "hb-1", "hb-2"]
    assert driver.transport_calls == 3


def test_service_factory_private_drivers():
    service = PolymarketService()
    trading = service.clob_trading()
    assert trading.has_client is False  # fake-only：未注入 client → egress tripwire
    ws = service.user_ws()
    assert ws is not None
    data = service.data_api()
    assert data is not None
