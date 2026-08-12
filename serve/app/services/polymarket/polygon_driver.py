"""Polygon typed JSON-RPC driver（WP-06 Checkpoint C）。

只实现 chain 只读调用：``eth_chainId, eth_getCode, eth_getStorageAt, eth_call,
eth_getTransactionReceipt, eth_getBlockByNumber``。response shape/hex/quantity 严格校验
（``schemas/polymarket/chain``），保存脱敏 receipt，**不做 DB 写**。

- 默认 ``require_injected_transport=true``：缺 transport 时任何调用在构造任何 client /
  socket 前立即 ``EgressTripwireError``（``wire_egress_tripwire``）。
- ``finality_check``：Relayer CONFIRMED 不等于 finality；必须 canonical receipt
  （status=0x1 + blockNumber/blockHash 非空 + 同高度 block hash 一致）+ finalized
  block（``finalized.number > receipt.blockNumber``）才返回 FINALIZED 判定。
- ``require_three_rpc_agreement``：fixture 生成时三节点一致；运行时对 code/slot/call
  做三节点一致性核验（任一分歧 fail-closed）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Sequence

from app.schemas.polymarket.chain import (
    FinalityCheck,
    JsonRpcResponse,
    RpcBlock,
    RpcReceipt,
    validate_address,
    validate_hex_data,
    validate_hex_quantity,
)
from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.base import EgressTripwireError

# RPC-level transport：``transport.request(payload: dict, endpoint: str) -> dict``
# 返回 JSON-RPC response dict。注入的 transport 即 fake transport（fake-only）。
RpcTransport = Callable[[dict, str], dict | Awaitable[dict]]
Clock = Callable[[], float]

_ID = 1


def _next_id() -> int:
    global _ID
    _ID += 1
    return _ID


def _redact_uri(endpoint: str) -> str:
    """endpoint 中 auth/query secret 不进入日志/receipt。"""
    try:
        from urllib.parse import urlsplit

        p = urlsplit(endpoint)
        host = p.hostname or ""
        return f"{p.scheme}://{host}"
    except Exception:
        return "<endpoint>"


class PolygonDriver:
    """Polygon PoS typed JSON-RPC driver（fake-only；require_injected_transport）。"""

    def __init__(
        self,
        *,
        rpc_urls: Sequence[str] | None = None,
        transport: RpcTransport | None = None,
        require_injected_transport: bool = True,
        clock: Clock | None = None,
        chain_id: int = 137,
        finalized_tag: str = "finalized",
    ) -> None:
        self._rpc_urls = tuple(url.rstrip("/") for url in (rpc_urls or ()))
        self._transport = transport
        self._require_injected_transport = require_injected_transport
        self._clock = clock or time.monotonic
        self._chain_id = int(chain_id)
        self._finalized_tag = finalized_tag
        self._transport_calls = 0
        self._fake_calls = 0
        self._real_calls = 0
        self._next_endpoint = 0

    # ---- counters / tripwire ----

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
        if self._transport is None:
            if self._require_injected_transport:
                raise EgressTripwireError()
            raise PolymarketError("wire_no_transport")
        return self._transport

    def _endpoint(self) -> str:
        if not self._rpc_urls:
            return "https://rpc.ankr.com/polygon"  # 仅占位；缺 transport 时不会到达
        url = self._rpc_urls[self._next_endpoint % len(self._rpc_urls)]
        self._next_endpoint += 1
        return url

    # ---- JSON-RPC 核心 ----

    async def _call(self, method: str, params: list[Any]) -> dict:
        transport = self._ensure_transport()
        payload = {
            "jsonrpc": "2.0",
            "id": _next_id(),
            "method": method,
            "params": params,
        }
        endpoint = self._endpoint()
        self._transport_calls += 1
        if self._transport is not None:
            self._fake_calls += 1
        else:
            self._real_calls += 1
        try:
            raw = await transport(payload, endpoint)
        except EgressTripwireError:
            raise
        except Exception as exc:
            raise PolymarketError(
                "rpc_transport_failure", detail=_redact_uri(endpoint)
            ) from exc
        if not isinstance(raw, dict):
            raise PolymarketError("rpc_response_not_object")
        try:
            parsed = JsonRpcResponse.model_validate(raw)
        except Exception as exc:
            raise PolymarketError("rpc_response_malformed") from exc
        if parsed.id != payload["id"]:
            raise PolymarketError("rpc_response_id_mismatch")
        if parsed.error is not None:
            raise PolymarketError(
                f"rpc_error:{parsed.error.code}",
                detail=parsed.error.message,
            )
        return parsed.result

    async def _call_strict_quantity(self, method: str, params: list[Any], *, path: str) -> str:
        result = await self._call(method, params)
        return validate_hex_quantity(result, path=path)

    # ---- chain 只读 API ----

    async def eth_chain_id(self) -> str:
        result = await self._call("eth_chainId", [])
        chain_id = validate_hex_quantity(result, path="eth_chainId")
        if int(chain_id, 16) != self._chain_id:
            raise PolymarketError(
                f"rpc_chain_id_mismatch:expected={self._chain_id}"
            )
        return chain_id

    async def eth_get_code(self, address: str, *, block_tag: str | None = None) -> str:
        addr = validate_address(address, path="eth_getCode")
        tag = block_tag or "latest"
        result = await self._call("eth_getCode", [addr, tag])
        return validate_hex_data(result, path="eth_getCode", allow_empty=True)

    async def eth_get_storage_at(self, address: str, slot: str, *,
                                 block_tag: str | None = None) -> str:
        addr = validate_address(address, path="eth_getStorageAt")
        if not isinstance(slot, str) or not slot.startswith("0x") or len(slot) != 66:
            raise PolymarketError("eth_getStorageAt_slot_invalid")
        tag = block_tag or "latest"
        result = await self._call("eth_getStorageAt", [addr, slot, tag])
        return validate_hex_data(result, path="eth_getStorageAt", allow_empty=False)

    async def eth_call(self, *, to: str, data: str, block_tag: str | None = None) -> str:
        addr = validate_address(to, path="eth_call")
        calldata = validate_hex_data(data, path="eth_call", allow_empty=True)
        tag = block_tag or "latest"
        result = await self._call(
            "eth_call", [{"to": addr, "data": calldata}, tag]
        )
        return validate_hex_data(result, path="eth_call", allow_empty=True)

    async def eth_get_transaction_receipt(self, tx_hash: str) -> RpcReceipt | None:
        if not isinstance(tx_hash, str) or not tx_hash.startswith("0x"):
            raise PolymarketError("eth_getTransactionReceipt_hash_invalid")
        result = await self._call("eth_getTransactionReceipt", [tx_hash])
        if result is None:
            return None
        try:
            return RpcReceipt.model_validate(result)
        except Exception as exc:
            raise PolymarketError("receipt_malformed") from exc

    async def eth_get_block_by_number(self, block_tag: str) -> RpcBlock | None:
        result = await self._call("eth_getBlockByNumber", [block_tag, False])
        if result is None:
            return None
        try:
            return RpcBlock.model_validate(result)
        except Exception as exc:
            raise PolymarketError("block_malformed") from exc

    async def get_balance_of(self, *, token: str, account: str,
                             balance_of_selector: str, block_tag: str | None = None) -> str:
        """``eth_call balanceOf(address)`` → 32-byte result。"""
        data = balance_of_selector + account[2:].rjust(64, "0")
        result = await self.eth_call(to=token, data=data, block_tag=block_tag)
        if len(result) != 66:
            raise PolymarketError("balance_result_not_32bytes")
        return result

    # ---- finality 核验 ----

    async def finality_check(self, tx_hash: str) -> FinalityCheck:
        """canonical receipt + finalized block 核验（fail-closed）。"""
        receipt = await self.eth_get_transaction_receipt(tx_hash)
        if receipt is None:
            raise PolymarketError("finality_receipt_missing")
        if not receipt.success:
            raise PolymarketError("finality_receipt_failed")
        if receipt.removed:
            raise PolymarketError("finality_receipt_removed")
        # 同高度 canonical block hash 必须一致
        block = await self.eth_get_block_by_number(receipt.block_number)
        if block is None or block.hash != receipt.block_hash:
            raise PolymarketError("finality_canonical_block_mismatch")
        finalized = await self.eth_get_block_by_number(self._finalized_tag)
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
        self,
        *,
        address: str,
        expected_runtime_keccak: str,
        block_tag: str,
    ) -> str:
        """复核 address 处 runtime code keccak（proxy-only hash 不算通过）。"""
        code = await self.eth_get_code(address, block_tag=block_tag)
        from eth_utils import keccak as _keccak

        actual = "0x" + _keccak(bytes.fromhex(code[2:])).hex() if len(code) > 2 else None
        if actual is None or actual != expected_runtime_keccak:
            raise PolymarketError("registry_runtime_code_drift")
        return actual
