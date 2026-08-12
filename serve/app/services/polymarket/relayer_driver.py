"""Frozen Polymarket Relayer-v2 Deposit Wallet wire (WP-06).

The driver has no HTTP client and accepts only an explicitly marked fixture transport in
WP-06.  Preparation fetches the official WALLET nonce, creates/signs the exact EIP-712
Batch and returns one opaque immutable envelope.  Submission sends those exact bytes;
it never regenerates a nonce, deadline, signature, or JSON body.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Awaitable, Callable, Mapping

from eth_account import Account
from eth_account.messages import SignableMessage, encode_typed_data
from eth_utils import keccak

from app.schemas.polymarket.chain import (
    RelayerStatus,
    normalize_relayer_state,
    validate_relayer_transaction_id,
)
from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.base import EgressTripwireError

RelayerTransport = Callable[..., tuple[int, bytes] | Awaitable[tuple[int, bytes]]]
TrustedTimeProvider = Callable[[], int]
Signer = Callable[[SignableMessage], str]
NonceAuthProvider = Callable[[str], Mapping[str, str]]
BuilderAuthProvider = Callable[[int, str, str, bytes], Mapping[str, str]]
Clock = Callable[[], float]

NONCE_PATH = "/v1/account/transactions/params"
SUBMIT_PATH = "/submit"
BUILDER_HEADERS = (
    "POLY_BUILDER_API_KEY",
    "POLY_BUILDER_TIMESTAMP",
    "POLY_BUILDER_PASSPHRASE",
    "POLY_BUILDER_SIGNATURE",
)
NONCE_HEADERS = ("RELAYER_API_KEY", "RELAYER_API_KEY_ADDRESS")
OUTCOME_UNKNOWN = "UNKNOWN"
_FIXTURE_MARKER = "__pm_fixture_transport__"


def fixture_relayer_transport(transport: RelayerTransport) -> RelayerTransport:
    """Mark a deterministic no-egress transport as allowed by the WP-06 tripwire."""
    setattr(transport, _FIXTURE_MARKER, True)
    return transport


@dataclass(frozen=True, slots=True)
class PreparedRelayerBatch:
    """Opaque, single-use-compatible exact wire envelope.

    ``body_bytes`` contains the fake/test signature and is deliberately excluded from
    repr.  Persistence records the public binding fields and hashes, then passes this
    same object to :meth:`submit_prepared`; raw signed bytes are not projected to DB/log.
    """

    nonce: str
    deadline: int
    from_address: str
    to_address: str
    deposit_wallet: str
    typed_data_hash: str
    body_hash: str
    body_bytes: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class SubmitOutcome:
    cls: str
    http_status: int | None = None
    transaction_id: str | None = None
    transaction_hash: str | None = None
    state: str | None = None
    raw_state: str | None = None
    latency_ms: int = 0


class RelayerDriver:
    """Deposit Wallet/WALLET Relayer boundary; fake conformance only in WP-06."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        transport: RelayerTransport | None = None,
        require_injected_transport: bool = True,
        fixture_only: bool = True,
        clock: Clock | None = None,
        trusted_time_provider: TrustedTimeProvider | None = None,
        signer: Signer | None = None,
        nonce_auth_provider: NonceAuthProvider | None = None,
        builder_auth_provider: BuilderAuthProvider | None = None,
        deadline_ttl_s: int = 600,
        chain_id: int = 137,
    ) -> None:
        self._base_url = (base_url or "https://relayer-v2.polymarket.com").rstrip("/")
        self._transport = transport
        self._require_injected_transport = require_injected_transport
        self._fixture_only = bool(fixture_only)
        self._clock = clock or __import__("time").monotonic
        self._trusted_time_provider = trusted_time_provider
        self._signer = signer
        self._nonce_auth_provider = nonce_auth_provider
        self._builder_auth_provider = builder_auth_provider
        self._deadline_ttl_s = int(deadline_ttl_s)
        self._chain_id = int(chain_id)
        self._transport_calls = 0
        self._fake_calls = 0
        self._real_calls = 0
        if self._deadline_ttl_s != 600:
            raise ValueError("relayer_deadline_ttl_must_be_600")
        if self._chain_id != 137:
            raise ValueError("relayer_chain_id_must_be_137")
        if self._transport is not None and (not self._fixture_only or not bool(
            getattr(self._transport, _FIXTURE_MARKER, False)
        )):
            raise EgressTripwireError()

    @property
    def fixture_only(self) -> bool:
        return self._fixture_only

    @property
    def transport_calls(self) -> int:
        return self._transport_calls

    @property
    def fake_calls(self) -> int:
        return self._fake_calls

    @property
    def real_calls(self) -> int:
        return self._real_calls

    @property
    def base_url(self) -> str:
        return self._base_url

    def _ensure_transport(self) -> RelayerTransport:
        transport = self._transport
        if transport is None:
            if self._require_injected_transport:
                raise EgressTripwireError()
            raise PolymarketError("wire_no_transport")
        if not self._fixture_only or not bool(getattr(transport, _FIXTURE_MARKER, False)):
            raise EgressTripwireError()
        return transport

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, bytes]:
        transport = self._ensure_transport()
        self._transport_calls += 1
        self._fake_calls += 1
        try:
            result = transport(
                method, path, params=params or {}, body=body, headers=dict(headers or {})
            )
            if inspect.isawaitable(result):
                result = await result
        except (AssertionError, asyncio.CancelledError, EgressTripwireError):
            raise
        except Exception:
            # Provider/transport exceptions may embed endpoint URLs, auth headers,
            # request bodies, or secrets. Collapse them at the wire boundary and
            # deliberately suppress exception chaining.
            raise PolymarketError("relayer_transport_failure") from None
        if not (isinstance(result, tuple) and len(result) == 2):
            raise PolymarketError("relayer_transport_response_invalid")
        status, response_body = result
        if isinstance(status, bool) or not isinstance(status, int):
            raise PolymarketError("relayer_transport_status_invalid")
        if not isinstance(response_body, bytes):
            raise PolymarketError("relayer_transport_body_invalid")
        return status, response_body

    @staticmethod
    def _parse_json(body: bytes) -> Any:
        if not body:
            raise PolymarketError("relayer_empty_response")

        def reject_constant(_value: str) -> Any:
            raise ValueError("json_nonfinite_forbidden")

        try:
            return json.loads(
                body,
                parse_float=Decimal,
                parse_constant=reject_constant,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            raise PolymarketError("relayer_malformed_json") from None

    def trusted_now(self) -> int:
        if self._trusted_time_provider is None:
            raise PolymarketError("relayer_trusted_time_missing")
        try:
            value = self._trusted_time_provider()
        except (AssertionError, asyncio.CancelledError, EgressTripwireError):
            raise
        except Exception:
            raise PolymarketError("relayer_trusted_time_failure") from None
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise PolymarketError("relayer_trusted_time_invalid")
        return value

    def deadline(self) -> int:
        return self.trusted_now() + self._deadline_ttl_s

    @staticmethod
    def _validate_address(value: str, *, path: str) -> str:
        from app.schemas.polymarket.chain import validate_address

        return validate_address(value, path=path)

    def _nonce_headers(self, address: str) -> Mapping[str, str]:
        if self._nonce_auth_provider is None:
            raise PolymarketError("relayer_nonce_auth_missing")
        try:
            headers = dict(self._nonce_auth_provider(address))
        except (AssertionError, asyncio.CancelledError, EgressTripwireError):
            raise
        except Exception:
            raise PolymarketError("relayer_nonce_auth_failure") from None
        if set(headers) != set(NONCE_HEADERS):
            raise PolymarketError("relayer_nonce_auth_shape_invalid")
        if any(not isinstance(headers[name], str) or not headers[name] for name in NONCE_HEADERS):
            raise PolymarketError("relayer_nonce_auth_value_invalid")
        if headers["RELAYER_API_KEY_ADDRESS"].lower() != address.lower():
            raise PolymarketError("relayer_nonce_auth_identity_mismatch")
        return {name: headers[name] for name in NONCE_HEADERS}

    def _builder_headers(
        self, *, timestamp: int, method: str, path: str, body: bytes
    ) -> Mapping[str, str]:
        if self._builder_auth_provider is None:
            raise PolymarketError("relayer_builder_auth_missing")
        try:
            headers = dict(self._builder_auth_provider(timestamp, method, path, body))
        except (AssertionError, asyncio.CancelledError, EgressTripwireError):
            raise
        except Exception:
            raise PolymarketError("relayer_builder_auth_failure") from None
        if set(headers) != set(BUILDER_HEADERS):
            raise PolymarketError("relayer_builder_auth_shape_invalid")
        if any(not isinstance(headers[name], str) or not headers[name] for name in BUILDER_HEADERS):
            raise PolymarketError("relayer_builder_auth_value_invalid")
        if headers["POLY_BUILDER_TIMESTAMP"] != str(timestamp):
            raise PolymarketError("relayer_builder_timestamp_mismatch")
        return {name: headers[name] for name in BUILDER_HEADERS}

    async def get_nonce(self, address: str) -> str:
        self._ensure_transport()
        address = self._validate_address(address, path="relayer_nonce")
        status, body = await self._request(
            "GET",
            NONCE_PATH,
            params={"address": address, "type": "WALLET"},
            headers=self._nonce_headers(address),
        )
        if status != 200:
            raise PolymarketError(f"relayer_nonce_http_{status}")
        parsed = self._parse_json(body)
        if not isinstance(parsed, dict) or set(parsed) != {"address", "nonce"}:
            raise PolymarketError("relayer_nonce_response_shape_invalid")
        response_address = parsed.get("address")
        if not isinstance(response_address, str) or response_address.lower() != address:
            raise PolymarketError("relayer_nonce_response_identity_mismatch")
        nonce = parsed.get("nonce")
        if not isinstance(nonce, str) or not nonce or not nonce.isdigit():
            raise PolymarketError("relayer_nonce_not_decimal")
        return nonce

    @staticmethod
    def build_typed_data(
        *, deposit_wallet: str, nonce: str, deadline: int, calls: list[dict[str, str]]
    ) -> dict[str, Any]:
        return {
            "domain": {
                "name": "DepositWallet",
                "version": "1",
                "chainId": 137,
                "verifyingContract": deposit_wallet,
            },
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "Call": [
                    {"name": "target", "type": "address"},
                    {"name": "value", "type": "uint256"},
                    {"name": "data", "type": "bytes"},
                ],
                "Batch": [
                    {"name": "wallet", "type": "address"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "deadline", "type": "uint256"},
                    {"name": "calls", "type": "Call[]"},
                ],
            },
            "primaryType": "Batch",
            "message": {
                "wallet": deposit_wallet,
                "nonce": int(nonce),
                "deadline": deadline,
                "calls": [
                    {
                        "target": call["target"],
                        "value": int(call["value"]),
                        "data": bytes.fromhex(call["data"][2:]),
                    }
                    for call in calls
                ],
            },
        }

    @staticmethod
    def typed_data_hash(signable: SignableMessage) -> str:
        return "0x" + keccak(b"\x19" + signable.version + signable.header + signable.body).hex()

    @staticmethod
    def build_submit_body(
        *,
        from_address: str,
        to_address: str,
        nonce: str,
        deadline: int,
        deposit_wallet: str,
        calls: list[dict[str, str]],
        metadata: str,
        signature: str,
    ) -> dict[str, Any]:
        return {
            "type": "WALLET",
            "from": from_address,
            "to": to_address,
            "nonce": nonce,
            "signature": signature,
            "metadata": metadata,
            "depositWalletParams": {
                "depositWallet": deposit_wallet,
                "deadline": str(deadline),
                "calls": calls,
            },
        }

    @staticmethod
    def serialize_body_exact(body: dict[str, Any]) -> bytes:
        return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @staticmethod
    def build_hmac_input(*, timestamp: int, method: str, path: str, body_bytes: bytes) -> str:
        return f"{timestamp}{method.upper()}{path}{body_bytes.decode('utf-8', errors='strict')}"

    async def prepare_batch(
        self,
        *,
        from_address: str,
        to_address: str,
        deposit_wallet: str,
        calls: list[dict[str, str]],
        metadata: str,
    ) -> PreparedRelayerBatch:
        """Fetch nonce once and create the one exact envelope later submitted unchanged."""
        self._ensure_transport()
        if self._signer is None:
            raise PolymarketError("relayer_signer_missing")
        from_address = self._validate_address(from_address, path="relayer_from")
        to_address = self._validate_address(to_address, path="relayer_to")
        deposit_wallet = self._validate_address(deposit_wallet, path="relayer_wallet")
        if not isinstance(metadata, str) or len(metadata) > 500:
            raise PolymarketError("relayer_metadata_invalid")
        if not calls:
            raise PolymarketError("relayer_calls_empty")
        normalized_calls: list[dict[str, str]] = []
        from app.schemas.polymarket.chain import validate_hex_data

        for call in calls:
            if not isinstance(call, dict) or set(call) != {"target", "value", "data"}:
                raise PolymarketError("relayer_call_shape_invalid")
            target = self._validate_address(call["target"], path="relayer_call")
            value = call["value"]
            if not isinstance(value, str) or not value.isdigit():
                raise PolymarketError("relayer_call_value_invalid")
            data = validate_hex_data(call["data"], path="relayer_call", allow_empty=True)
            normalized_calls.append({"target": target, "value": value, "data": data})
        nonce = await self.get_nonce(from_address)
        deadline = self.deadline()
        typed = self.build_typed_data(
            deposit_wallet=deposit_wallet,
            nonce=nonce,
            deadline=deadline,
            calls=normalized_calls,
        )
        try:
            signable = encode_typed_data(full_message=typed)
            signature = self._signer(signable)
        except (AssertionError, asyncio.CancelledError):
            raise
        except Exception:
            raise PolymarketError("relayer_signing_failed") from None
        if not isinstance(signature, str) or len(signature) != 132 or not signature.startswith("0x"):
            raise PolymarketError("relayer_signature_invalid")
        try:
            recovered = Account.recover_message(signable, signature=signature)
        except Exception:
            raise PolymarketError("relayer_signature_recovery_failed") from None
        if recovered.lower() != from_address:
            raise PolymarketError("relayer_signature_identity_mismatch")
        body = self.build_submit_body(
            from_address=from_address,
            to_address=to_address,
            nonce=nonce,
            deadline=deadline,
            deposit_wallet=deposit_wallet,
            calls=normalized_calls,
            metadata=metadata,
            signature=signature,
        )
        body_bytes = self.serialize_body_exact(body)
        return PreparedRelayerBatch(
            nonce=nonce,
            deadline=deadline,
            from_address=from_address,
            to_address=to_address,
            deposit_wallet=deposit_wallet,
            typed_data_hash=self.typed_data_hash(signable),
            body_hash=hashlib.sha256(body_bytes).hexdigest(),
            body_bytes=body_bytes,
        )

    async def submit_prepared(self, prepared: PreparedRelayerBatch) -> SubmitOutcome:
        self._ensure_transport()
        if not isinstance(prepared, PreparedRelayerBatch):
            raise PolymarketError("relayer_prepared_type_invalid")
        if hashlib.sha256(prepared.body_bytes).hexdigest() != prepared.body_hash:
            raise PolymarketError("relayer_prepared_body_hash_mismatch")
        timestamp = self.trusted_now()
        headers = self._builder_headers(
            timestamp=timestamp, method="POST", path=SUBMIT_PATH, body=prepared.body_bytes
        )
        headers = {**headers, "content-type": "application/json"}
        t0 = self._clock()
        try:
            status, response_body = await self._request(
                "POST", SUBMIT_PATH, body=prepared.body_bytes, headers=headers
            )
        except (AssertionError, asyncio.CancelledError, EgressTripwireError):
            raise
        except Exception:
            return SubmitOutcome(
                cls=OUTCOME_UNKNOWN, latency_ms=int((self._clock() - t0) * 1000)
            )
        latency = int((self._clock() - t0) * 1000)
        if status < 200 or status >= 300:
            return SubmitOutcome(cls=OUTCOME_UNKNOWN, http_status=status, latency_ms=latency)
        try:
            parsed = self._parse_json(response_body)
        except PolymarketError:
            return SubmitOutcome(cls=OUTCOME_UNKNOWN, http_status=status, latency_ms=latency)
        if not isinstance(parsed, dict) or set(parsed) - {
            "transactionID", "transactionHash", "state"
        }:
            return SubmitOutcome(cls=OUTCOME_UNKNOWN, http_status=status, latency_ms=latency)
        tx_id = parsed.get("transactionID")
        tx_hash = parsed.get("transactionHash")
        raw_state = parsed.get("state")
        try:
            tx_id = validate_relayer_transaction_id(tx_id)
        except ValueError:
            return SubmitOutcome(cls=OUTCOME_UNKNOWN, http_status=status, latency_ms=latency)
        try:
            state = normalize_relayer_state(raw_state)
        except ValueError:
            return SubmitOutcome(cls=OUTCOME_UNKNOWN, http_status=status, latency_ms=latency)
        if tx_hash in (None, ""):
            tx_hash = None
        else:
            try:
                from app.schemas.polymarket.chain import validate_hex32

                tx_hash = validate_hex32(tx_hash, path="relayer_submit_transaction")
            except PolymarketError:
                return SubmitOutcome(cls=OUTCOME_UNKNOWN, http_status=status, latency_ms=latency)
        return SubmitOutcome(
            cls="SUBMITTED",
            http_status=status,
            transaction_id=tx_id,
            transaction_hash=tx_hash,
            state=state,
            raw_state=raw_state,
            latency_ms=latency,
        )

    async def get_transaction_status(self, transaction_id: str) -> RelayerStatus:
        self._ensure_transport()
        try:
            transaction_id = validate_relayer_transaction_id(transaction_id)
        except ValueError:
            raise PolymarketError("relayer_transaction_id_invalid") from None
        path = f"/v1/account/transactions/{transaction_id}"
        timestamp = self.trusted_now()
        headers = self._builder_headers(timestamp=timestamp, method="GET", path=path, body=b"")
        status, body = await self._request("GET", path, headers=headers)
        if status != 200:
            raise PolymarketError(f"relayer_status_http_{status}")
        parsed = self._parse_json(body)
        try:
            result = RelayerStatus.model_validate(parsed)
        except Exception:
            raise PolymarketError("relayer_status_malformed") from None
        if result.transaction_id != transaction_id:
            raise PolymarketError("relayer_status_identity_mismatch")
        return result
