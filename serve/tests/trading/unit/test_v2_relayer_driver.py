"""Relayer exact Deposit Wallet wire, opaque prepare/submit and recovery boundaries."""
from __future__ import annotations

import base64
import hashlib
import hmac
import asyncio
import json
import traceback

import pytest
from eth_account import Account

from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.relayer_driver import (
    BUILDER_HEADERS,
    NONCE_HEADERS,
    OUTCOME_UNKNOWN,
    PreparedRelayerBatch,
    RelayerDriver,
    fixture_relayer_transport,
)
from tests.trading.fixtures.p6_settlement.p6_helpers import relayer_golden

G = relayer_golden()
PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
ACCOUNT = Account.from_key(PK)
FAKE_HMAC_SECRET = hashlib.sha256(b"pm-v2/fixture/builder-secret/v1").digest()


def make_transport(*, submit_status=200, submit_body=None):
    requests = []

    @fixture_relayer_transport
    async def transport(method, path, *, params=None, body=None, headers=None):
        requests.append({"method": method, "path": path, "params": params, "body": body, "headers": headers})
        if path == "/v1/account/transactions/params":
            return 200, json.dumps(G["nonce"]["response"], separators=(",", ":")).encode()
        if path == "/submit":
            response = submit_body if submit_body is not None else G["submit"]["response"]
            return submit_status, json.dumps(response, separators=(",", ":")).encode()
        if path.startswith("/v1/account/transactions/"):
            return 200, json.dumps(G["status"]["response"], separators=(",", ":")).encode()
        return 404, b"{}"

    return transport, requests


def nonce_auth(address):
    return {
        "RELAYER_API_KEY": "fixture-relayer-key",
        "RELAYER_API_KEY_ADDRESS": address,
    }


def builder_auth(timestamp, method, path, body):
    message = f"{timestamp}{method.upper()}{path}{body.decode()}".encode()
    signature = base64.urlsafe_b64encode(hmac.new(FAKE_HMAC_SECRET, message, hashlib.sha256).digest()).decode()
    return {
        "POLY_BUILDER_API_KEY": "fixture-builder-key",
        "POLY_BUILDER_TIMESTAMP": str(timestamp),
        "POLY_BUILDER_PASSPHRASE": "fixture-passphrase",
        "POLY_BUILDER_SIGNATURE": signature,
    }


def driver(transport, **kwargs):
    return RelayerDriver(
        transport=transport,
        trusted_time_provider=lambda: G["deadline"]["trusted_now"],
        signer=lambda signable: "0x" + ACCOUNT.sign_message(signable).signature.hex(),
        nonce_auth_provider=nonce_auth,
        builder_auth_provider=builder_auth,
        **kwargs,
    )


async def prepare(d):
    body = G["submit"]["body"]
    return await d.prepare_batch(
        from_address=body["from"],
        to_address=body["to"],
        deposit_wallet=body["depositWalletParams"]["depositWallet"],
        calls=body["depositWalletParams"]["calls"],
        metadata=body["metadata"],
    )


async def test_prepare_exact_eip712_signature_body_and_nonce_identity() -> None:
    transport, requests = make_transport()
    d = driver(transport)
    prepared = await prepare(d)
    assert isinstance(prepared, PreparedRelayerBatch)
    assert prepared.nonce == "42" and prepared.deadline == G["deadline"]["deadline"]
    assert prepared.typed_data_hash == G["eip712"]["typed_data_hash"]
    assert prepared.body_hash == G["submit"]["exact_serialized_body_sha256"]
    assert prepared.body_bytes.decode() == G["submit"]["exact_serialized_body"]
    assert "signature" not in repr(prepared) and G["submit"]["body"]["signature"] not in repr(prepared)
    nonce_request = requests[0]
    assert nonce_request["params"] == G["nonce"]["request"]["params"]
    assert tuple(nonce_request["headers"]) == NONCE_HEADERS


async def test_submit_prepared_sends_same_bytes_and_official_camel_response() -> None:
    transport, requests = make_transport()
    d = driver(transport)
    prepared = await prepare(d)
    outcome = await d.submit_prepared(prepared)
    assert outcome.cls == "SUBMITTED"
    assert outcome.transaction_id == "tx-0001"
    assert outcome.raw_state == "STATE_NEW" and outcome.state == "NEW"
    sent = requests[-1]
    assert sent["path"] == "/submit" and sent["body"] is prepared.body_bytes
    assert hashlib.sha256(sent["body"]).hexdigest() == prepared.body_hash
    assert tuple(k for k in sent["headers"] if k != "content-type") == BUILDER_HEADERS
    assert sent["headers"]["POLY_BUILDER_SIGNATURE"] == G["submit"]["hmac"]["expected_signature_b64"]


async def test_tampered_prepared_body_rejected_before_transport() -> None:
    transport, requests = make_transport()
    d = driver(transport)
    original = await prepare(d)
    tampered = PreparedRelayerBatch(
        nonce=original.nonce, deadline=original.deadline,
        from_address=original.from_address, to_address=original.to_address,
        deposit_wallet=original.deposit_wallet, typed_data_hash=original.typed_data_hash,
        body_hash=original.body_hash, body_bytes=original.body_bytes + b" ",
    )
    before = len(requests)
    with pytest.raises(PolymarketError, match="prepared_body_hash_mismatch"):
        await d.submit_prepared(tampered)
    assert len(requests) == before


@pytest.mark.parametrize(
    "status,response",
    [
        (500, {"error": "fixture"}),
        (200, {"transaction_id": "wrong-alias", "state": "STATE_NEW"}),
        (200, {"transactionID": "tx", "state": "NEW"}),
    ],
)
async def test_submit_5xx_bad_alias_or_bad_state_is_unknown(status, response) -> None:
    transport, _ = make_transport(submit_status=status, submit_body=response)
    d = driver(transport)
    assert (await d.submit_prepared(await prepare(d))).cls == OUTCOME_UNKNOWN


async def test_status_official_snake_response_raw_state_normalized_once_and_identity_bound() -> None:
    transport, requests = make_transport()
    status = await driver(transport).get_transaction_status("tx-0001")
    assert status.state == "STATE_CONFIRMED"
    assert status.normalized_state == "CONFIRMED" and status.is_success_terminal
    assert requests[-1]["path"] == "/v1/account/transactions/tx-0001"
    assert tuple(requests[-1]["headers"]) == BUILDER_HEADERS

    @fixture_relayer_transport
    async def wrong_identity(method, path, **kwargs):
        return 200, json.dumps({**G["status"]["response"], "transaction_id": "other"}).encode()

    with pytest.raises(PolymarketError, match="status_identity_mismatch"):
        await driver(wrong_identity).get_transaction_status("tx-0001")

    @fixture_relayer_transport
    async def coerced_id(method, path, **kwargs):
        return 200, json.dumps({**G["status"]["response"], "transaction_id": 1}).encode()

    with pytest.raises(PolymarketError, match="status_malformed"):
        await driver(coerced_id).get_transaction_status("tx-0001")


async def test_missing_trusted_time_or_auth_fails_before_signing_or_submit() -> None:
    transport, requests = make_transport()
    signer_calls = 0

    def signer(_message):
        nonlocal signer_calls
        signer_calls += 1
        return G["submit"]["body"]["signature"]

    d = RelayerDriver(
        transport=transport, signer=signer,
        nonce_auth_provider=nonce_auth, builder_auth_provider=builder_auth,
    )
    with pytest.raises(PolymarketError, match="trusted_time_missing"):
        await prepare(d)
    assert signer_calls == 0
    assert len(requests) == 1  # nonce read occurred; no signature or submit


async def test_sync_fixture_transport_supported() -> None:
    calls = []

    @fixture_relayer_transport
    def sync_transport(method, path, **kwargs):
        calls.append(path)
        return 200, json.dumps(G["nonce"]["response"]).encode()

    assert await driver(sync_transport).get_nonce(G["nonce"]["response"]["address"]) == "42"
    assert calls == ["/v1/account/transactions/params"]


async def test_transport_exception_is_sanitized_without_traceback_context() -> None:
    @fixture_relayer_transport
    async def leaking_transport(method, path, **kwargs):
        raise RuntimeError("TOPSECRET endpoint=https://user:pass@example.invalid key=abc")

    d = driver(leaking_transport)
    try:
        await d.get_nonce(G["nonce"]["response"]["address"])
    except PolymarketError as exc:
        rendered = "".join(traceback.format_exception(exc))
        assert exc.reason_code == "relayer_transport_failure"
        assert "TOPSECRET" not in rendered
        assert "user:pass" not in rendered
        assert exc.__cause__ is None and exc.__suppress_context__ is True
    else:
        raise AssertionError("expected sanitized transport failure")


async def test_submit_transport_exception_is_unknown_and_secret_free() -> None:
    transport, _ = make_transport()
    d = driver(transport)
    prepared = await prepare(d)

    @fixture_relayer_transport
    async def leaking_transport(method, path, **kwargs):
        raise RuntimeError("TOPSECRET submit ambiguity")

    d._transport = leaking_transport
    outcome = await d.submit_prepared(prepared)
    assert outcome.cls == OUTCOME_UNKNOWN
    assert "TOPSECRET" not in repr(outcome)


@pytest.mark.parametrize(
    "submit_body",
    [
        {"transactionID": "tx/escape", "state": "STATE_NEW"},
        {"transactionID": "tx?query", "state": "STATE_NEW"},
        {"transactionID": 1, "state": "STATE_NEW"},
        {"transactionID": "tx", "transactionHash": 1, "state": "STATE_NEW"},
    ],
)
async def test_submit_identity_and_hash_types_are_strict(submit_body) -> None:
    transport, _ = make_transport(submit_body=submit_body)
    d = driver(transport)
    assert (await d.submit_prepared(await prepare(d))).cls == OUTCOME_UNKNOWN


async def test_status_provider_error_text_is_redacted_and_types_are_strict() -> None:
    @fixture_relayer_transport
    async def failed(method, path, **kwargs):
        return 200, json.dumps({
            **G["status"]["response_failed"],
            "transaction_id": "tx-0002",
            "error_msg": "TOPSECRET endpoint credential",
        }).encode()

    status = await driver(failed).get_transaction_status("tx-0002")
    assert status.error_msg == "provider_error_present"
    assert "TOPSECRET" not in repr(status)

    @fixture_relayer_transport
    async def bad_type(method, path, **kwargs):
        return 200, json.dumps({
            **G["status"]["response"],
            "transaction_id": "tx-0001",
            "error_msg": 1,
        }).encode()

    with pytest.raises(PolymarketError, match="status_malformed"):
        await driver(bad_type).get_transaction_status("tx-0001")


def test_auth_mapping_order_is_irrelevant_and_output_order_is_frozen() -> None:
    transport, _ = make_transport()

    def reversed_nonce(address):
        return {
            "RELAYER_API_KEY_ADDRESS": address,
            "RELAYER_API_KEY": "fixture-relayer-key",
        }

    def reversed_builder(timestamp, method, path, body):
        normal = builder_auth(timestamp, method, path, body)
        return {name: normal[name] for name in reversed(BUILDER_HEADERS)}

    d = RelayerDriver(
        transport=transport,
        trusted_time_provider=lambda: G["deadline"]["trusted_now"],
        signer=lambda signable: "0x" + ACCOUNT.sign_message(signable).signature.hex(),
        nonce_auth_provider=reversed_nonce,
        builder_auth_provider=reversed_builder,
    )
    assert tuple(d._nonce_headers(G["nonce"]["response"]["address"])) == NONCE_HEADERS
    assert tuple(d._builder_headers(timestamp=1, method="GET", path="/p", body=b"")) == BUILDER_HEADERS


def test_auth_and_trusted_time_callback_exceptions_are_sanitized() -> None:
    transport, _ = make_transport()

    def leaking(*args, **kwargs):
        raise RuntimeError("TOPSECRET auth callback")

    base = dict(
        transport=transport,
        trusted_time_provider=lambda: G["deadline"]["trusted_now"],
        signer=lambda signable: "0x" + ACCOUNT.sign_message(signable).signature.hex(),
        nonce_auth_provider=nonce_auth,
        builder_auth_provider=builder_auth,
    )
    cases = [
        (RelayerDriver(**{**base, "nonce_auth_provider": leaking}), lambda d: d._nonce_headers(G["nonce"]["response"]["address"])),
        (RelayerDriver(**{**base, "builder_auth_provider": leaking}), lambda d: d._builder_headers(timestamp=1, method="GET", path="/p", body=b"")),
        (RelayerDriver(**{**base, "trusted_time_provider": leaking}), lambda d: d.trusted_now()),
    ]
    for d, invoke in cases:
        try:
            invoke(d)
        except PolymarketError as exc:
            rendered = "".join(traceback.format_exception(exc))
            assert "TOPSECRET" not in rendered
            assert exc.__suppress_context__ is True
        else:
            raise AssertionError("expected sanitized callback failure")


async def test_cancelled_transport_propagates_and_timeout_has_path_specific_semantics() -> None:
    @fixture_relayer_transport
    async def cancelled(method, path, **kwargs):
        raise asyncio.CancelledError()

    d = driver(cancelled)
    with pytest.raises(asyncio.CancelledError):
        await d.get_nonce(G["nonce"]["response"]["address"])

    transport, _ = make_transport()
    normal = driver(transport)
    prepared = await prepare(normal)

    @fixture_relayer_transport
    async def timed_out(method, path, **kwargs):
        raise TimeoutError("TOPSECRET timeout endpoint")

    timeout_driver = driver(timed_out)
    try:
        await timeout_driver.get_nonce(G["nonce"]["response"]["address"])
    except PolymarketError as exc:
        rendered = "".join(traceback.format_exception(exc))
        assert exc.reason_code == "relayer_transport_failure"
        assert "TOPSECRET" not in rendered
        assert exc.__suppress_context__ is True
    else:
        raise AssertionError("expected sanitized nonce timeout")

    normal._transport = timed_out
    outcome = await normal.submit_prepared(prepared)
    assert outcome.cls == OUTCOME_UNKNOWN
    assert outcome.transaction_id is None and outcome.transaction_hash is None
