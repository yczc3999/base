"""Strict, fixture-only Polygon JSON-RPC boundary for WP-06."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

from app.schemas.polymarket.chain import (
    FinalityCheck,
    JsonRpcResponse,
    RpcBlock,
    RpcReceipt,
    validate_address,
    validate_hex32,
    validate_hex_data,
    validate_hex_quantity,
)
from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.base import EgressTripwireError

RpcTransport = Callable[[dict[str, Any], str], dict | Awaitable[dict]]
Clock = Callable[[], float]
_FIXTURE_MARKER = "__pm_fixture_transport__"
_ID = 1


def fixture_polygon_transport(transport: RpcTransport) -> RpcTransport:
    """Mark a deterministic no-egress JSON-RPC transport for WP-06 tests."""
    setattr(transport, _FIXTURE_MARKER, True)
    return transport


def _next_id() -> int:
    global _ID
    _ID += 1
    return _ID


def _origin(endpoint: str) -> str:
    try:
        parts = urlsplit(endpoint)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{parts.hostname}{port}"
    except Exception:
        raise PolymarketError("rpc_endpoint_invalid") from None


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _block_tag(value: object, *, path: str) -> str:
    if value in ("latest", "finalized"):
        return str(value)
    return validate_hex_quantity(value, path=path)


class PolygonDriver:
    def __init__(
        self,
        *,
        rpc_urls: Sequence[str] | None = None,
        transport: RpcTransport | None = None,
        require_injected_transport: bool = True,
        fixture_only: bool = True,
        clock: Clock | None = None,
        chain_id: int = 137,
        finalized_tag: str = "finalized",
    ) -> None:
        self._rpc_urls = tuple(url.rstrip("/") for url in (rpc_urls or ()))
        self._transport = transport
        self._require_injected_transport = require_injected_transport
        self._fixture_only = bool(fixture_only)
        self._clock = clock or time.monotonic
        self._chain_id = int(chain_id)
        self._finalized_tag = finalized_tag
        self._transport_calls = 0
        self._fake_calls = 0
        self._real_calls = 0
        self._next_endpoint = 0
        if self._chain_id != 137:
            raise ValueError("polygon_chain_id_must_be_137")
        if self._finalized_tag != "finalized":
            raise ValueError("polygon_finalized_tag_invalid")
        origins = tuple(_origin(url) for url in self._rpc_urls)
        if self._transport is not None and (not self._fixture_only or not bool(
            getattr(self._transport, _FIXTURE_MARKER, False)
        )):
            raise EgressTripwireError()
        if len(origins) != len(set(origins)):
            raise ValueError("polygon_rpc_origins_not_unique")

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

    def _ensure_transport(self) -> RpcTransport:
        transport = self._transport
        if transport is None:
            if self._require_injected_transport:
                raise EgressTripwireError()
            raise PolymarketError("wire_no_transport")
        if not self._fixture_only or not bool(getattr(transport, _FIXTURE_MARKER, False)):
            raise EgressTripwireError()
        return transport

    def _endpoint(self) -> str:
        if not self._rpc_urls:
            raise PolymarketError("rpc_endpoint_missing")
        endpoint = self._rpc_urls[self._next_endpoint % len(self._rpc_urls)]
        self._next_endpoint += 1
        return endpoint

    async def _call_endpoint(self, method: str, params: list[Any], endpoint: str) -> Any:
        transport = self._ensure_transport()
        payload = {"jsonrpc": "2.0", "id": _next_id(), "method": method, "params": params}
        self._transport_calls += 1
        self._fake_calls += 1
        try:
            raw = transport(payload, endpoint)
            if inspect.isawaitable(raw):
                raw = await raw
        except (AssertionError, asyncio.CancelledError, EgressTripwireError):
            raise
        except Exception:
            raise PolymarketError("rpc_transport_failure") from None
        if not isinstance(raw, dict):
            raise PolymarketError("rpc_response_not_object")
        try:
            parsed = JsonRpcResponse.model_validate(raw)
        except Exception:
            raise PolymarketError("rpc_response_malformed") from None
        if parsed.id != payload["id"]:
            raise PolymarketError("rpc_response_id_mismatch")
        if parsed.error is not None:
            # Provider messages may echo credentials/URLs.  Persist only numeric code.
            raise PolymarketError(f"rpc_error:{parsed.error.code}")
        return parsed.result

    async def _call(self, method: str, params: list[Any]) -> Any:
        self._ensure_transport()
        return await self._call_endpoint(method, params, self._endpoint())

    async def _call_consensus(self, method: str, params: list[Any]) -> Any:
        self._ensure_transport()
        if len(self._rpc_urls) < 3:
            raise PolymarketError("rpc_three_origin_consensus_required")
        values = [
            await self._call_endpoint(method, params, endpoint) for endpoint in self._rpc_urls
        ]
        if len({_canonical(value) for value in values}) != 1:
            raise PolymarketError("rpc_three_origin_consensus_mismatch")
        return values[0]

    async def eth_chain_id(self, *, consensus: bool = False) -> str:
        result = await (self._call_consensus("eth_chainId", []) if consensus else self._call("eth_chainId", []))
        chain_id = validate_hex_quantity(result, path="eth_chainId")
        if int(chain_id, 16) != self._chain_id:
            raise PolymarketError("rpc_chain_id_mismatch")
        return chain_id

    async def eth_get_code(
        self, address: str, *, block_tag: str | None = None, consensus: bool = False
    ) -> str:
        address = validate_address(address, path="eth_getCode")
        tag = _block_tag(block_tag or "latest", path="eth_getCode_block")
        call = self._call_consensus if consensus else self._call
        result = await call("eth_getCode", [address, tag])
        return validate_hex_data(result, path="eth_getCode", allow_empty=True)

    async def eth_get_storage_at(
        self,
        address: str,
        slot: str,
        *,
        block_tag: str | None = None,
        consensus: bool = False,
    ) -> str:
        address = validate_address(address, path="eth_getStorageAt")
        slot = validate_hex32(slot, path="eth_getStorageAt_slot")
        tag = _block_tag(block_tag or "latest", path="eth_getStorageAt_block")
        call = self._call_consensus if consensus else self._call
        result = await call("eth_getStorageAt", [address, slot, tag])
        result = validate_hex_data(result, path="eth_getStorageAt")
        if len(result) != 66:
            raise PolymarketError("eth_getStorageAt_result_not_32bytes")
        return result

    async def eth_call(
        self,
        *,
        to: str,
        data: str,
        block_tag: str | None = None,
        consensus: bool = False,
    ) -> str:
        to = validate_address(to, path="eth_call")
        data = validate_hex_data(data, path="eth_call", allow_empty=True)
        tag = _block_tag(block_tag or "latest", path="eth_call_block")
        call = self._call_consensus if consensus else self._call
        result = await call("eth_call", [{"to": to, "data": data}, tag])
        return validate_hex_data(result, path="eth_call", allow_empty=True)

    async def eth_get_transaction_receipt(
        self, tx_hash: str, *, consensus: bool = False
    ) -> RpcReceipt | None:
        tx_hash = validate_hex32(tx_hash, path="eth_getTransactionReceipt")
        call = self._call_consensus if consensus else self._call
        result = await call("eth_getTransactionReceipt", [tx_hash])
        if result is None:
            return None
        try:
            receipt = RpcReceipt.model_validate(result)
        except Exception:
            raise PolymarketError("receipt_malformed") from None
        if receipt.transaction_hash != tx_hash:
            raise PolymarketError("receipt_transaction_hash_mismatch")
        return receipt

    async def eth_get_block_by_number(
        self, block_tag: str, *, consensus: bool = False
    ) -> RpcBlock | None:
        block_tag = _block_tag(block_tag, path="eth_getBlockByNumber")
        call = self._call_consensus if consensus else self._call
        result = await call("eth_getBlockByNumber", [block_tag, False])
        if result is None:
            return None
        try:
            block = RpcBlock.model_validate(result)
        except Exception:
            raise PolymarketError("block_malformed") from None
        if block_tag not in ("finalized", "latest") and block.number != block_tag.lower():
            raise PolymarketError("block_number_mismatch")
        return block

    async def finality_check(self, tx_hash: str) -> FinalityCheck:
        """Three-origin receipt/canonical/finalized proof bound to the requested tx."""
        receipt = await self.eth_get_transaction_receipt(tx_hash, consensus=True)
        if receipt is None:
            raise PolymarketError("finality_receipt_missing")
        if not receipt.success:
            raise PolymarketError("finality_receipt_failed")
        if receipt.has_removed_log:
            raise PolymarketError("finality_receipt_removed")
        block = await self.eth_get_block_by_number(receipt.block_number, consensus=True)
        if block is None or block.hash != receipt.block_hash:
            raise PolymarketError("finality_canonical_block_mismatch")
        finalized = await self.eth_get_block_by_number(self._finalized_tag, consensus=True)
        if finalized is None:
            raise PolymarketError("finality_finalized_unsupported")
        return FinalityCheck(
            receipt=receipt,
            canonical_block_hash=block.hash,
            finalized_block_number=finalized.number_int,
            finalized_block_hash=finalized.hash,
            finalized_after_receipt=finalized.number_int > receipt.block_number_int,
        )

    async def verify_registry_code(
        self, *, address: str, expected_runtime_keccak: str, block_tag: str
    ) -> str:
        code = await self.eth_get_code(address, block_tag=block_tag, consensus=True)
        if code == "0x":
            raise PolymarketError("registry_runtime_code_empty")
        actual = "0x" + keccak(bytes.fromhex(code[2:])).hex()
        if actual != expected_runtime_keccak:
            raise PolymarketError("registry_runtime_code_drift")
        return actual

    async def verify_registry_entry(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve and verify none/EIP-1967/beacon entries from three RPC origins."""
        required = {
            "address", "chain_id", "proxy_kind", "runtime_keccak",
            "resolved_implementation_or_beacon", "resolved_code_keccak",
            "snapshot_block_number", "snapshot_block_hash",
        }
        if not isinstance(entry, Mapping) or not required.issubset(entry):
            raise PolymarketError("registry_entry_incomplete")
        if entry["chain_id"] != self._chain_id:
            raise PolymarketError("registry_chain_drift")
        snapshot_number = entry["snapshot_block_number"]
        if isinstance(snapshot_number, bool) or not isinstance(snapshot_number, int) or snapshot_number <= 0:
            raise PolymarketError("registry_snapshot_block_invalid")
        tag = hex(snapshot_number)
        snapshot = await self.eth_get_block_by_number(tag, consensus=True)
        if snapshot is None or snapshot.hash != entry["snapshot_block_hash"]:
            raise PolymarketError("registry_snapshot_block_drift")
        runtime = await self.verify_registry_code(
            address=entry["address"], expected_runtime_keccak=entry["runtime_keccak"], block_tag=tag
        )
        kind = entry["proxy_kind"]
        resolved = entry["resolved_implementation_or_beacon"]
        resolved_hash = entry["resolved_code_keccak"]
        evidence: dict[str, Any] = {"runtime_keccak": runtime, "proxy_kind": kind}
        if kind == "none":
            if resolved is not None or resolved_hash != runtime:
                raise PolymarketError("registry_none_resolution_invalid")
            return evidence
        extra = entry.get("extra") or {}
        if kind == "eip1967":
            slot = extra.get("implementation_slot") or entry.get("eip1967_implementation_slot")
            value = await self.eth_get_storage_at(entry["address"], slot, block_tag=tag, consensus=True)
            actual_address = "0x" + value[-40:]
            if int(actual_address, 16) == 0:
                raise PolymarketError("registry_implementation_empty")
            if not resolved or actual_address != resolved.lower():
                raise PolymarketError("registry_implementation_drift")
            actual_hash = await self.verify_registry_code(
                address=actual_address, expected_runtime_keccak=resolved_hash, block_tag=tag
            )
            evidence.update(implementation=actual_address, resolved_code_keccak=actual_hash)
        elif kind == "beacon":
            slot = extra.get("beacon_slot") or entry.get("eip1967_beacon_slot")
            value = await self.eth_get_storage_at(entry["address"], slot, block_tag=tag, consensus=True)
            beacon = "0x" + value[-40:]
            if int(beacon, 16) == 0:
                raise PolymarketError("registry_beacon_empty")
            if not resolved or beacon != resolved.lower():
                raise PolymarketError("registry_beacon_drift")
            beacon_hash = await self.verify_registry_code(
                address=beacon,
                expected_runtime_keccak=extra.get("beacon_runtime_keccak") or entry.get("beacon_runtime_keccak"),
                block_tag=tag,
            )
            implementation_word = await self.eth_call(
                to=beacon, data="0x5c60da1b", block_tag=tag, consensus=True
            )
            if len(implementation_word) != 66:
                raise PolymarketError("registry_beacon_implementation_shape_invalid")
            implementation = "0x" + implementation_word[-40:]
            expected_implementation = extra.get("beacon_implementation") or entry.get("beacon_implementation")
            if implementation != str(expected_implementation).lower():
                raise PolymarketError("registry_beacon_implementation_drift")
            actual_hash = await self.verify_registry_code(
                address=implementation, expected_runtime_keccak=resolved_hash, block_tag=tag
            )
            evidence.update(
                beacon=beacon,
                beacon_runtime_keccak=beacon_hash,
                implementation=implementation,
                resolved_code_keccak=actual_hash,
            )
        else:
            raise PolymarketError("registry_proxy_kind_invalid")
        return evidence

    async def erc20_balance_of(
        self, token: str, account: str, *, block_tag: str = "finalized"
    ) -> int:
        token = validate_address(token, path="erc20_token")
        account = validate_address(account, path="erc20_account")
        data = "0x70a08231" + abi_encode(["address"], [account]).hex()
        result = await self.eth_call(to=token, data=data, block_tag=block_tag, consensus=True)
        return self._decode_single_uint(result, path="erc20_balance")

    async def erc1155_balance_of(
        self, token: str, account: str, token_id: int | str, *, block_tag: str = "finalized"
    ) -> int:
        token = validate_address(token, path="erc1155_token")
        account = validate_address(account, path="erc1155_account")
        if isinstance(token_id, bool):
            raise PolymarketError("erc1155_token_id_invalid")
        try:
            token_value = int(token_id, 0) if isinstance(token_id, str) else int(token_id)
        except (TypeError, ValueError):
            raise PolymarketError("erc1155_token_id_invalid") from None
        if token_value < 0 or token_value >= 1 << 256:
            raise PolymarketError("erc1155_token_id_invalid")
        data = "0x00fdd58e" + abi_encode(["address", "uint256"], [account, token_value]).hex()
        result = await self.eth_call(to=token, data=data, block_tag=block_tag, consensus=True)
        return self._decode_single_uint(result, path="erc1155_balance")

    async def erc1155_is_approved_for_all(
        self, token: str, owner: str, operator: str, *, block_tag: str = "finalized"
    ) -> bool:
        token = validate_address(token, path="erc1155_token")
        owner = validate_address(owner, path="erc1155_owner")
        operator = validate_address(operator, path="erc1155_operator")
        data = "0xe985e9c5" + abi_encode(["address", "address"], [owner, operator]).hex()
        result = await self.eth_call(to=token, data=data, block_tag=block_tag, consensus=True)
        if len(result) != 66:
            raise PolymarketError("erc1155_approval_result_not_32bytes")
        try:
            return bool(abi_decode(["bool"], bytes.fromhex(result[2:]))[0])
        except Exception:
            raise PolymarketError("erc1155_approval_result_invalid") from None

    async def get_balance_of(
        self,
        *,
        token: str,
        account: str,
        balance_of_selector: str,
        block_tag: str | None = None,
    ) -> str:
        if balance_of_selector.lower() != "0x70a08231":
            raise PolymarketError("balance_selector_invalid")
        value = await self.erc20_balance_of(token, account, block_tag=block_tag or "finalized")
        return "0x" + value.to_bytes(32, "big").hex()

    @staticmethod
    def _decode_single_uint(result: str, *, path: str) -> int:
        if len(result) != 66:
            raise PolymarketError(f"{path}_result_not_32bytes")
        try:
            return int(abi_decode(["uint256"], bytes.fromhex(result[2:]))[0])
        except Exception:
            raise PolymarketError(f"{path}_result_invalid") from None
