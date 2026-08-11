"""Private CLOB trading driver（WP-05 Checkpoint C）。

封装锁定 SDK（``polymarket-client==0.5.0`` 的 ``SecureClient``）的私有下单/撤单/查单。
SDK object/secret/signature **绝不离开本 Driver**；Logic 决定 Gate/retry/reservation/状态转换。

- 所有构造器可注入 fake client / clock；未注入 client 时任何 wire 调用立即
  ``REASON_EGRESS_TRIPWIRE``（无真实 hostname connect/socket）。
- 私有 submit **单次发送**：socket write/response header/body timeout、断连、5xx、
  不可判定 200 body 一律 ``UNKNOWN``，禁止换 salt/timestamp/signature 盲重发。
- L2 HMAC 输入 ``unix_seconds + UPPERCASE_METHOD + PATH_WITHOUT_QUERY + EXACT_BODY_OR_EMPTY``
  （``base.build_l2_hmac_message``）；时钟偏差超过冻结阈值停止 submit。
- type-3/ERC-7739/L1/L2 auth 由 SDK 处理，但 Driver 校验 wire golden（EOA recovery、
  Deposit Wallet maker/funder、signatureType=3、Standard/NegRisk domain、final wire hash）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from app.schemas.polymarket.clob_private import (
    CancelItemResult,
    CancelOrdersResponse,
    OrderResponse,
    classify_order_response,
)
from app.schemas.polymarket.common import PolymarketError, RequestReceipt
from app.schemas.polymarket.data_api import DataApiOpenOrder, DataApiTrade
from app.services.polymarket.base import (
    REASON_EGRESS_TRIPWIRE,
    REASON_ORDER_INDETERMINATE,
    PrivateSubmitPolicy,
    build_l2_hmac_message,
)

Clock = Callable[[], float]

ACK = "ACK"
REJECTED = "REJECTED"
AUTH_STOP = "AUTH_STOP"
THROTTLED = "THROTTLED"
UNKNOWN = "UNKNOWN"

_INNER_SIG_HEX = 130  # 65 bytes EOA signature, hex without 0x
# ERC-7739 trailer = app_domain_separator(64) + contents_hash(64) + contents_type(2*N) + length(4)
_ERC7739_TRAILER_FIXED_HEX = 132


@dataclass(frozen=True)
class SubmitOutcome:
    """一次 submit 的结果分类（单次发送；绝不含 signature/secret）。"""

    cls: str
    order_id: str | None = None
    order_response: OrderResponse | None = None
    http_status: int | None = None
    error_code: str | None = None
    latency_ms: int = 0


class EgressTripwireError(PolymarketError):
    """未注入 client/transport 时任何 wire 调用立即失败。"""


class ClobTradingDriver:
    """封装 ``SecureClient`` 私有下单/撤单/查单；单次发送、egress tripwire。"""

    def __init__(
        self,
        client: Any = None,
        *,
        policy: PrivateSubmitPolicy | None = None,
        clock: Clock | None = None,
        base_url: str = "https://clob.polymarket.com",
    ) -> None:
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url must be absolute http(s), got {base_url!r}")
        self._client = client
        self._policy = policy or PrivateSubmitPolicy()
        self._clock = clock or time.monotonic
        self._base_url = base_url.rstrip("/")
        self._transport_calls = 0

    # ---- injection / tripwire ----

    @property
    def transport_calls(self) -> int:
        return self._transport_calls

    @property
    def has_client(self) -> bool:
        return self._client is not None

    def _require_client(self) -> Any:
        if self._client is None:
            raise EgressTripwireError(REASON_EGRESS_TRIPWIRE)
        return self._client

    def _bump(self) -> None:
        self._transport_calls += 1

    # ---- clock / L2 ----

    def assert_clock_skew(self, *, unix_now: int, trusted_server_time: int) -> None:
        """时钟偏差超过冻结阈值 → 停止 submit（fail-closed）。"""
        skew = abs(int(unix_now) - int(trusted_server_time))
        if skew > self._policy.max_clock_skew_s:
            raise PolymarketError("wire_clock_skew_exceeded")

    def l2_hmac_input(
        self, *, unix_seconds: int, method: str, path_without_query: str, body: bytes | None
    ) -> str:
        """返回 L2 HMAC 规范输入串（供 SDK ``build_hmac_signature`` 消费）。"""
        return build_l2_hmac_message(
            unix_seconds=unix_seconds,
            method=method,
            path_without_query=path_without_query,
            body=body,
        )

    # ---- wire 方法（单次发送；SDK object/secret/signature 不出 Driver）----

    async def create_signed_order(
        self,
        *,
        token_id: str,
        price: Decimal | str,
        size: Decimal | str,
        side: str,
        post_only: bool = False,
        expiration: int | None = None,
    ) -> Any:
        """用 SDK 创建并签名 limit order（无网络）；返回 ``SignedOrder``。

        签名/salt/timestamp 由 SDK 生成；调用方据此计算 pre-send hash 并持久化，
        再用同一个 SignedOrder 单次发送。SDK object/secret 不出 Driver。
        """
        client = self._require_client()
        result = await asyncio.to_thread(
            client.create_limit_order,
            token_id=token_id,
            price=price,
            size=size,
            side=side,
            post_only=post_only,
            expiration=expiration,
        )
        self._bump()
        return result

    async def submit_order(self, signed_order: Any) -> SubmitOutcome:
        """每个 attempt 只发送一次；write/read timeout、断连、5xx、不可判定 → UNKNOWN。"""
        client = self._require_client()
        started = self._clock()
        try:
            response = await asyncio.to_thread(client.post_order, signed_order)
        except PolymarketError as exc:
            self._bump()
            return self._from_polymarket_error(exc, started)
        except Exception:
            self._bump()
            return SubmitOutcome(
                UNKNOWN,
                error_code=REASON_ORDER_INDETERMINATE,
                latency_ms=max(0, int((self._clock() - started) * 1000)),
            )
        self._bump()
        return self._normalize_submit(response, started)

    async def place_limit_order(
        self,
        *,
        token_id: str,
        price: Decimal | str,
        size: Decimal | str,
        side: str,
        post_only: bool = False,
        expiration: int | None = None,
    ) -> SubmitOutcome:
        client = self._require_client()
        started = self._clock()
        try:
            response = await asyncio.to_thread(
                client.place_limit_order,
                token_id=token_id,
                price=price,
                size=size,
                side=side,
                post_only=post_only,
                expiration=expiration,
            )
        except PolymarketError as exc:
            self._bump()
            return self._from_polymarket_error(exc, started)
        except Exception:
            self._bump()
            return SubmitOutcome(
                UNKNOWN,
                error_code=REASON_ORDER_INDETERMINATE,
                latency_ms=max(0, int((self._clock() - started) * 1000)),
            )
        self._bump()
        return self._normalize_submit(response, started)

    async def cancel_orders(self, order_ids: list[str]) -> CancelOrdersResponse:
        client = self._require_client()
        started = self._clock()
        try:
            response = await asyncio.to_thread(client.cancel_orders, order_ids=order_ids)
        except PolymarketError as exc:
            self._bump()
            raise
        except Exception:
            self._bump()
            raise PolymarketError(REASON_ORDER_INDETERMINATE)
        self._bump()
        return self._normalize_cancel(response)

    async def list_open_orders(
        self, *, token_id: str | None = None, id: str | None = None, market: str | None = None
    ) -> list[DataApiOpenOrder]:
        client = self._require_client()
        paginator = client.list_open_orders(token_id=token_id, id=id, market=market)
        items = await asyncio.to_thread(list, paginator)
        self._bump()
        return [self._normalize_open_order(item) for item in items]

    async def list_trades(
        self,
        *,
        token_id: str | None = None,
        id: str | None = None,
        market: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> list[DataApiTrade]:
        client = self._require_client()
        paginator = client.list_account_trades(
            token_id=token_id, id=id, market=market, after=after, before=before,
        )
        items = await asyncio.to_thread(list, paginator)
        self._bump()
        return [self._normalize_trade(item) for item in items]

    async def setup_gasless_wallet(self) -> Any:
        client = self._require_client()
        result = await asyncio.to_thread(client.setup_gasless_wallet)
        self._bump()
        return result

    async def send_heartbeat(self, heartbeat_id: str) -> dict[str, Any]:
        """``POST /v1/heartbeats``：首空 ID → 轮换 ID 链；失败即停止新单。"""
        client = self._require_client()
        started = self._clock()
        try:
            response = await asyncio.to_thread(client.post_heartbeat, heartbeat_id)
        except PolymarketError as exc:
            self._bump()
            return {
                "ok": False, "heartbeat_id": None, "http_status": getattr(exc, "http_status", None),
                "error_code": exc.reason_code,
                "latency_ms": max(0, int((self._clock() - started) * 1000)),
            }
        except Exception:
            self._bump()
            return {
                "ok": False, "heartbeat_id": None, "http_status": None,
                "error_code": REASON_ORDER_INDETERMINATE,
                "latency_ms": max(0, int((self._clock() - started) * 1000)),
            }
        self._bump()
        next_id = None
        if isinstance(response, dict):
            next_id = response.get("heartbeat_id")
        elif response is not None:
            next_id = getattr(response, "heartbeat_id", None)
        return {
            "ok": next_id is not None,
            "heartbeat_id": str(next_id) if next_id is not None else None,
            "http_status": 200,
            "error_code": None,
            "latency_ms": max(0, int((self._clock() - started) * 1000)),
        }

    # ---- 归一化 ----

    def _normalize_submit(self, response: Any, started: float) -> SubmitOutcome:
        latency_ms = max(0, int((self._clock() - started) * 1000))
        ok = bool(
            getattr(response, "ok", None)
            if getattr(response, "ok", None) is not None
            else getattr(response, "success", False)
        )
        order_id = getattr(response, "order_id", None)
        status = getattr(response, "status", None)
        error_msg = getattr(response, "message", None) or getattr(response, "error_msg", None)
        parsed = OrderResponse(
            order_id=str(order_id) if order_id is not None else None,
            status=str(status) if status is not None else None,
            success=ok,
            error_msg=str(error_msg) if error_msg is not None else None,
            making_amount=_safe_decimal(getattr(response, "making_amount", None)),
            taking_amount=_safe_decimal(getattr(response, "taking_amount", None)),
            trade_ids=tuple(getattr(response, "trade_ids", ()) or ()),
            transactions_hashes=tuple(getattr(response, "transactions_hashes", ()) or ()),
        )
        classification = classify_order_response(
            parsed, http_status=200, error_code=None
        )
        return SubmitOutcome(
            classification.cls, order_id=parsed.order_id, order_response=parsed,
            http_status=200, error_code=None, latency_ms=latency_ms,
        )

    def _from_polymarket_error(self, exc: PolymarketError, started: float) -> SubmitOutcome:
        latency_ms = max(0, int((self._clock() - started) * 1000))
        receipts = getattr(exc, "receipts", None) or ()
        last: RequestReceipt | None = receipts[-1] if receipts else None
        http_status = getattr(exc, "http_status", None)
        if http_status is None and last is not None:
            http_status = last.http_status
        classification = classify_order_response(
            None, http_status=http_status, error_code=exc.reason_code
        )
        return SubmitOutcome(
            classification.cls, order_response=None, http_status=http_status,
            error_code=exc.reason_code, latency_ms=latency_ms,
        )

    def _normalize_cancel(self, response: Any) -> CancelOrdersResponse:
        canceled = tuple(getattr(response, "canceled", ()) or ())
        not_canceled = dict(getattr(response, "not_canceled", {}) or {})
        items: list[CancelItemResult] = []
        seen: set[str] = set()
        for order_id in canceled:
            seen.add(str(order_id))
            items.append(CancelItemResult(order_id=str(order_id), ok=True))
        for order_id, error in not_canceled.items():
            if str(order_id) in seen:
                continue
            items.append(CancelItemResult(order_id=str(order_id), ok=False, error=str(error)))
        return CancelOrdersResponse(items=items, success=not not_canceled)

    def _normalize_open_order(self, item: Any) -> DataApiOpenOrder:
        return DataApiOpenOrder(
            order_id=str(getattr(item, "id", "")),
            token_id=str(getattr(item, "token_id", "") or getattr(item, "asset_id", "")),
            side=str(getattr(item, "side", "BUY")),
            price=_safe_decimal(getattr(item, "price", Decimal("0"))),
            size=_safe_decimal(getattr(item, "size_matched", getattr(item, "original_size", Decimal("0")))),
            original_size=_safe_decimal(getattr(item, "original_size", None)),
            size_matched=_safe_decimal(getattr(item, "size_matched", None)),
            status=getattr(item, "status", None),
            created_at=getattr(item, "created_at", None),
        )

    def _normalize_trade(self, item: Any) -> DataApiTrade:
        return DataApiTrade(
            trade_id=str(getattr(item, "id", "")),
            order_id=str(getattr(item, "taker_order_id", None) or ""),
            token_id=str(getattr(item, "token_id", "") or getattr(item, "asset_id", "")),
            side=str(getattr(item, "side", "BUY")),
            price=_safe_decimal(getattr(item, "price", Decimal("0"))),
            size=_safe_decimal(getattr(item, "size", Decimal("0"))),
            fee=None,
            matched_at=str(getattr(item, "matched_at", None) or ""),
        )

    # ---- wire golden 校验（type-3 / ERC-7739 / EOA recovery / domain / wire hash）----

    def validate_signed_order_golden(
        self,
        signed_order: Any,
        *,
        chain_id: int,
        exchange_address: str,
        expected_eoa: str,
        protocol_version: str = "2",
    ) -> dict[str, Any]:
        """校验 SDK 生成的 type-3 wire golden，返回逐项验证结果。

        - maker/signer 必须同为 Deposit Wallet（type-3 外层 signer = maker）。
        - signatureType 必须为 3；内层 EOA 签名可恢复为 ``expected_eoa``。
        - ERC-7739 trailer 存在（signature 长度 = 内层 130 + trailer 160 hex）。
        - Standard domain（name=Polymarket CTF Exchange / version / chainId / verifyingContract）。
        - final wire body hash（canonical JSON）确定性可复算。
        本方法只读字段做验证，不导出 signature/secret。
        """
        maker = str(getattr(signed_order, "maker", "") or "")
        signer = str(getattr(signed_order, "signer", "") or "")
        signature_type = int(getattr(signed_order, "signature_type", -1))
        signature = str(getattr(signed_order, "signature", "") or "")
        body_hash = canonical_order_body_hash(signed_order)
        inner_sig = _inner_eoa_signature(signature)
        recovered = None
        trailer_present = False
        if signature_type == 3 and inner_sig is not None:
            recovered = self._recover_inner_eoa(signed_order, inner_sig, chain_id, exchange_address)
            trailer_present = _has_erc7739_trailer(signature)
        return {
            "maker": maker,
            "signer": signer,
            "deposit_wallet_maker_funder": bool(maker) and maker == signer,
            "signature_type": signature_type,
            "signature_type_is_three": signature_type == 3,
            "inner_signature_recovered_eoa": recovered,
            "eoa_recovery_matches": bool(recovered) and recovered.lower() == str(expected_eoa).lower(),
            "erc7739_wrapper": trailer_present,
            "domain_name": "Polymarket CTF Exchange",
            "domain_version": protocol_version,
            "chain_id": int(chain_id),
            "verifying_contract": str(exchange_address),
            "final_wire_body_hash": body_hash,
        }

    def _recover_inner_eoa(
        self, signed_order: Any, inner_sig_hex: str, chain_id: int, exchange_address: str
    ) -> str | None:
        """用 eth-account 从内层 65 字节签名恢复 EOA（EIP-712 typed data）。"""
        try:
            from eth_account import Account
            from eth_account.messages import encode_typed_data

            from polymarket._internal.actions.orders.types import UnsignedOrder
            from polymarket._internal.actions.orders.typed_data import build_order_typed_data

            unsigned = UnsignedOrder(
                chain_id=int(chain_id),
                builder=str(getattr(signed_order, "builder", "") or ""),
                exchange_address=str(exchange_address),
                expiration=int(getattr(signed_order, "expiration", 0)),
                maker=str(getattr(signed_order, "maker", "") or ""),
                maker_amount=int(getattr(signed_order, "maker_amount", 0)),
                metadata=str(getattr(signed_order, "metadata", "") or ""),
                order_type=str(getattr(signed_order, "order_type", "GTC")),
                salt=int(getattr(signed_order, "salt", 0)),
                side=str(getattr(signed_order, "side", "BUY")),
                signature_type=int(getattr(signed_order, "signature_type", 3)),
                signer=str(getattr(signed_order, "signer", "") or ""),
                taker_amount=int(getattr(signed_order, "taker_amount", 0)),
                timestamp=int(getattr(signed_order, "timestamp", 0)),
                token_id=str(getattr(signed_order, "token_id", "") or ""),
            )
            typed = build_order_typed_data(unsigned)
            message = encode_typed_data(full_message=typed)
            signature = "0x" + inner_sig_hex
            recovered = Account.recover_message(message, signature=signature)
            return str(recovered)
        except Exception:  # noqa: BLE001 - golden 校验失败返回 None（不泄漏 signature）
            return None


def _safe_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("decimal_bool_forbidden")
    if isinstance(value, float):
        raise ValueError("decimal_float_forbidden")
    return Decimal(str(value))


def canonical_order_body_hash(signed_order: Any) -> str:
    """对 SignedOrder 的 canonical JSON 做 sha256（final wire body hash）。

    只含订单身份/数量/签名（signature 作为 wire 的一部分参与 hash），不含
    API key / passphrase / L2 secret 等认证字段。
    """
    fields = {
        "maker": str(getattr(signed_order, "maker", "") or ""),
        "signer": str(getattr(signed_order, "signer", "") or ""),
        "signature_type": int(getattr(signed_order, "signature_type", -1)),
        "signature": str(getattr(signed_order, "signature", "") or ""),
        "token_id": str(getattr(signed_order, "token_id", "") or ""),
        "maker_amount": str(getattr(signed_order, "maker_amount", 0)),
        "taker_amount": str(getattr(signed_order, "taker_amount", 0)),
        "side": str(getattr(signed_order, "side", "") or ""),
        "price": str(getattr(signed_order, "price", None) or ""),
        "size": str(getattr(signed_order, "size", None) or ""),
        "salt": int(getattr(signed_order, "salt", 0)),
        "timestamp": int(getattr(signed_order, "timestamp", 0)),
        "expiration": int(getattr(signed_order, "expiration", 0)),
        "post_only": bool(getattr(signed_order, "post_only", False)),
    }
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def expected_order_hash_for(
    signed_order: Any, *, chain_id: int, exchange_address: str
) -> str:
    """确定性 order content hash（EIP-712 Order 内容；供 attempt pre-send 持久化）。"""
    try:
        from polymarket._internal.actions.orders.types import UnsignedOrder
        from polymarket._internal.actions.orders.typed_data import (
            build_order_typed_data,
        )

        unsigned = UnsignedOrder(
            chain_id=int(chain_id),
            builder=str(getattr(signed_order, "builder", "") or ""),
            exchange_address=str(exchange_address),
            expiration=int(getattr(signed_order, "expiration", 0)),
            maker=str(getattr(signed_order, "maker", "") or ""),
            maker_amount=int(getattr(signed_order, "maker_amount", 0)),
            metadata=str(getattr(signed_order, "metadata", "") or ""),
            order_type=str(getattr(signed_order, "order_type", "GTC")),
            salt=int(getattr(signed_order, "salt", 0)),
            side=str(getattr(signed_order, "side", "BUY")),
            signature_type=int(getattr(signed_order, "signature_type", 3)),
            signer=str(getattr(signed_order, "signer", "") or ""),
            taker_amount=int(getattr(signed_order, "taker_amount", 0)),
            timestamp=int(getattr(signed_order, "timestamp", 0)),
            token_id=str(getattr(signed_order, "token_id", "") or ""),
        )
        typed = build_order_typed_data(unsigned)
        message = json.dumps(
            typed["message"], sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(message.encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001 - hash 计算失败不泄漏 signature
        return canonical_order_body_hash(signed_order)


def sdk_manifest_hash_for(signed_order: Any) -> str:
    """SDK manifest + 签名向量 hash（attempt pre-send 持久化；不含 secret）。"""
    payload = {
        "package": "polymarket-client",
        "version": "0.5.0",
        "signature_type": int(getattr(signed_order, "signature_type", -1)),
        "post_only": bool(getattr(signed_order, "post_only", False)),
        "order_type": str(getattr(signed_order, "order_type", "") or ""),
        "expiration": int(getattr(signed_order, "expiration", 0)),
        "maker": str(getattr(signed_order, "maker", "") or ""),
        "signer": str(getattr(signed_order, "signer", "") or ""),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _inner_eoa_signature(signature: str) -> str | None:
    """提取内层 65 字节 EOA 签名（ERC-7739 trailer 之前）。"""
    if not signature:
        return None
    hex_body = signature[2:] if signature.startswith("0x") else signature
    if len(hex_body) < _INNER_SIG_HEX:
        return None
    return hex_body[:_INNER_SIG_HEX]


def _has_erc7739_trailer(signature: str) -> bool:
    """ERC-7739 trailer 结构校验：``inner(130) + app_domain_separator(64) +
    contents_hash(64) + contents_type(2*N) + length(4)``，其中 length 与 N 自洽。"""
    if not signature:
        return False
    hex_body = signature[2:] if signature.startswith("0x") else signature
    if len(hex_body) < _INNER_SIG_HEX + _ERC7739_TRAILER_FIXED_HEX + 4:
        return False
    trailer = hex_body[_INNER_SIG_HEX:]
    try:
        type_len = int(trailer[-4:], 16)
    except ValueError:
        return False
    return len(trailer) == _ERC7739_TRAILER_FIXED_HEX + 2 * type_len


__all__ = [
    "ACK", "REJECTED", "AUTH_STOP", "THROTTLED", "UNKNOWN",
    "SubmitOutcome", "ClobTradingDriver", "EgressTripwireError",
    "canonical_order_body_hash",
    "expected_order_hash_for", "sdk_manifest_hash_for",
]
