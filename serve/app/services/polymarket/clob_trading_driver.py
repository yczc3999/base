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
import inspect
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from importlib import metadata as importlib_metadata
from typing import Any, Awaitable, Callable, Mapping

from app.schemas.polymarket.clob_private import (
    CancelItemResult,
    CancelOrdersResponse,
    OrderResponse,
    PrivateApiCredentials,
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
UnixClock = Callable[[], float]
HeartbeatTransport = Callable[
    [str, str, bytes, Mapping[str, str]],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]
CredentialProvider = Callable[[], PrivateApiCredentials]
ExchangeResolver = Callable[[str], str]
TrustedTimeProvider = Callable[[], float | Awaitable[float]]

ACK = "ACK"
REJECTED = "REJECTED"
AUTH_STOP = "AUTH_STOP"
THROTTLED = "THROTTLED"
UNKNOWN = "UNKNOWN"

PINNED_SDK_PACKAGE = "polymarket-client"
PINNED_SDK_VERSION = "0.5.0"
PINNED_SDK_TAG = "polymarket-client-v0.5.0"
PINNED_SDK_COMMIT = "974d2e22ca92445d8ab7ecd7715a247f1ea7d65a"
HEARTBEAT_PATH = "/v1/heartbeats"

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


@dataclass(frozen=True, slots=True)
class PreparedSignedOrder:
    """Opaque SDK order plus the exact bytes that will be sent.

    Only non-sensitive pre-send evidence is exposed to orchestration.  The SDK
    object, raw signature, owner API key and body bytes are intentionally
    excluded from repr and have no public accessor.
    """

    _sdk_order: Any = field(repr=False)
    _owner_api_key: str = field(repr=False)
    _body_bytes: bytes = field(repr=False)
    path: str
    body_hash: str
    salt: int
    timestamp: int

    def __repr__(self) -> str:
        return (
            "PreparedSignedOrder(path=%r, body_hash=%r, salt=%r, timestamp=%r)"
            % (self.path, self.body_hash, self.salt, self.timestamp)
        )


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
        unix_clock: UnixClock | None = None,
        heartbeat_transport: HeartbeatTransport | None = None,
        heartbeat_credentials: CredentialProvider | None = None,
        trusted_time_provider: TrustedTimeProvider | None = None,
        chain_id: int | None = None,
        exchange_address: str | None = None,
        exchange_resolver: ExchangeResolver | None = None,
        base_url: str = "https://clob.polymarket.com",
    ) -> None:
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url must be absolute http(s), got {base_url!r}")
        self._client = client
        self._policy = policy or PrivateSubmitPolicy()
        self._clock = clock or time.monotonic
        self._unix_clock = unix_clock or time.time
        self._heartbeat_transport = heartbeat_transport
        self._heartbeat_credentials = heartbeat_credentials
        self._trusted_time_provider = trusted_time_provider
        self._chain_id = int(chain_id) if chain_id is not None else None
        self._exchange_address = (
            str(exchange_address).lower() if exchange_address is not None else None
        )
        self._exchange_resolver = exchange_resolver
        self._base_url = base_url.rstrip("/")
        self._transport_calls = 0

    # ---- injection / tripwire ----

    @property
    def transport_calls(self) -> int:
        return self._transport_calls

    @property
    def has_client(self) -> bool:
        return self._client is not None

    @property
    def chain_id(self) -> int | None:
        """Frozen execution chain advertised to the orchestration preflight."""

        return self._chain_id

    def exchange_address_for(self, token_id: str) -> str | None:
        """Return the frozen Standard/NegRisk exchange for an outcome token.

        The resolver is configuration only and must not perform network I/O.  Keeping
        this identity outside the opaque signed order lets orchestration reject a
        mismatched driver *before* it asks the SDK to touch signer material.
        """

        if self._exchange_resolver is not None:
            value = self._exchange_resolver(str(token_id))
            return str(value).lower() if value else None
        return self._exchange_address

    def assert_execution_identity(
        self, *, token_id: str, chain_id: int, exchange_address: str
    ) -> None:
        """Fail closed before signing when driver and frozen runtime identity differ."""

        actual_exchange = self.exchange_address_for(token_id)
        if self._chain_id is None or self._chain_id != int(chain_id):
            raise PolymarketError("wire_chain_identity_mismatch")
        if (
            actual_exchange is None
            or actual_exchange != str(exchange_address).lower()
        ):
            raise PolymarketError("wire_exchange_identity_mismatch")

    def _require_client(self) -> Any:
        if self._client is None:
            raise EgressTripwireError(REASON_EGRESS_TRIPWIRE)
        return self._client

    def _bump(self) -> None:
        self._transport_calls += 1

    # ---- clock / L2 ----

    def assert_clock_skew(self, *, unix_now: float, trusted_server_time: float) -> None:
        """时钟偏差超过冻结阈值 → 停止 submit（fail-closed）。"""
        skew = abs(float(unix_now) - float(trusted_server_time))
        if skew > self._policy.max_clock_skew_s:
            raise PolymarketError("wire_clock_skew_exceeded")

    async def assert_trusted_clock(self) -> None:
        """Require an injected `/time` observation before any signer is invoked."""

        if self._trusted_time_provider is None:
            raise PolymarketError("wire_trusted_time_required")
        value = self._trusted_time_provider()
        trusted = await value if inspect.isawaitable(value) else value
        if isinstance(trusted, bool) or not isinstance(trusted, (int, float)):
            raise PolymarketError("wire_trusted_time_invalid")
        self.assert_clock_skew(
            unix_now=float(self._unix_clock()), trusted_server_time=float(trusted),
        )

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

    @staticmethod
    def assert_pinned_sdk() -> None:
        """Fail closed if the installed runtime is not the frozen SDK version."""
        try:
            version = importlib_metadata.version(PINNED_SDK_PACKAGE)
        except importlib_metadata.PackageNotFoundError as exc:
            raise PolymarketError("wire_sdk_not_installed") from exc
        if version != PINNED_SDK_VERSION:
            raise PolymarketError("wire_sdk_version_mismatch")

    @staticmethod
    def _client_owner_api_key(client: Any) -> str:
        credentials = getattr(client, "credentials", None)
        owner = getattr(credentials, "key", None)
        if not isinstance(owner, str) or not owner:
            raise PolymarketError("wire_sdk_credentials_unavailable")
        return owner

    def prepare_signed_order(self, signed_order: Any) -> PreparedSignedOrder:
        """Freeze the pinned SDK's exact ``POST /order`` bytes before submit.

        There is deliberately no best-effort/canonical fallback: if the SDK
        payload cannot be built exactly, the attempt must not be persisted or
        sent under a misleading hash.
        """
        self.assert_pinned_sdk()
        client = self._require_client()
        owner = self._client_owner_api_key(client)
        path, body = exact_order_wire_bytes(signed_order, owner_api_key=owner)
        return PreparedSignedOrder(
            _sdk_order=signed_order,
            _owner_api_key=owner,
            _body_bytes=body,
            path=path,
            body_hash=hashlib.sha256(body).hexdigest(),
            salt=int(getattr(signed_order, "salt", 0)),
            timestamp=int(getattr(signed_order, "timestamp", 0)),
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
    ) -> PreparedSignedOrder:
        """用 SDK 创建并签名 limit order（无网络）；返回精确 wire 封装。

        签名/salt/timestamp 由 SDK 生成；调用方持久化公开的 pre-send evidence，
        再将同一 ``PreparedSignedOrder`` 交回 ``submit_order`` 单次发送。
        SDK object/secret/signature/body bytes 不出 Driver。
        """
        # Clock authority is part of the signature boundary.  A missing/stale
        # observation must stop before the SDK can access signer material.
        await self.assert_trusted_clock()
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
        return self.prepare_signed_order(result)

    async def submit_order(self, signed_order: PreparedSignedOrder) -> SubmitOutcome:
        """每个 attempt 只发送一次；write/read timeout、断连、5xx、不可判定 → UNKNOWN。"""
        client = self._require_client()
        if not isinstance(signed_order, PreparedSignedOrder):
            raise PolymarketError("wire_prepared_order_required")
        # Credential rotation between prepare and submit changes the exact body
        # (the owner is part of it).  Never send bytes different from the hash
        # committed by the caller.
        owner = self._client_owner_api_key(client)
        path, current_body = exact_order_wire_bytes(
            signed_order._sdk_order, owner_api_key=owner
        )
        if (
            path != signed_order.path
            or owner != signed_order._owner_api_key
            or current_body != signed_order._body_bytes
            or hashlib.sha256(current_body).hexdigest() != signed_order.body_hash
        ):
            raise PolymarketError("wire_exact_body_changed_after_prepare")
        started = self._clock()
        try:
            response = await asyncio.to_thread(client.post_order, signed_order._sdk_order)
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
        # The SDK convenience method signs and sends in one call, making the
        # required pre-send persistence of exact bytes impossible.
        self._require_client()
        raise PolymarketError("wire_pre_send_persistence_required")

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

    async def get_order(self, *, order_id: str) -> DataApiOpenOrder | None:
        """Authoritative single-order lookup.

        Only an explicit provider 404 is represented as ``None``. Transport,
        authentication and schema failures stay indeterminate and therefore can
        never be mistaken for proof that an UNKNOWN submit missed the venue.
        """
        client = self._require_client()
        if not isinstance(order_id, str) or not order_id:
            raise ValueError("order_id_required")
        try:
            item = await asyncio.to_thread(client.get_order, order_id=order_id)
        except Exception as exc:
            try:
                from polymarket import RequestRejectedError
            except Exception:  # pragma: no cover - pinned SDK import is tested separately
                RequestRejectedError = ()  # type: ignore[assignment]
            self._bump()
            if RequestRejectedError and isinstance(exc, RequestRejectedError) and exc.status == 404:
                return None
            raise PolymarketError(REASON_ORDER_INDETERMINATE) from None
        self._bump()
        return self._normalize_open_order(item)

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
        # Wallet deployment is an on-chain side effect and is outside WP-05.
        # Fail before calling the SDK convenience method, even with a configured
        # client, so a readiness probe can never create a wallet.
        self._require_client()
        raise PolymarketError("wire_wallet_deployment_forbidden")

    async def send_heartbeat(self, heartbeat_id: str) -> dict[str, Any]:
        """``POST /v1/heartbeats``：首空 ID → 轮换 ID 链；失败即停止新单。"""
        if self._heartbeat_transport is None or self._heartbeat_credentials is None:
            raise EgressTripwireError(REASON_EGRESS_TRIPWIRE)
        if not isinstance(heartbeat_id, str):
            raise ValueError("heartbeat_id_must_be_string")
        body = json.dumps(
            {"heartbeat_id": heartbeat_id}, separators=(",", ":")
        ).encode("utf-8")
        timestamp = int(self._unix_clock())
        credentials = self._heartbeat_credentials()
        if not isinstance(credentials, PrivateApiCredentials):
            raise PolymarketError("wire_heartbeat_credentials_invalid")
        headers = _build_l2_headers(
            credentials,
            address=_client_signer_address(self._client),
            unix_seconds=timestamp,
            method="POST",
            path=HEARTBEAT_PATH,
            body=body,
        )
        started = self._clock()
        try:
            response_or_awaitable = self._heartbeat_transport(
                "POST", HEARTBEAT_PATH, body, headers
            )
            if inspect.isawaitable(response_or_awaitable):
                response = await response_or_awaitable
            else:
                response = response_or_awaitable
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
        if isinstance(response, Mapping):
            next_id = response.get("heartbeat_id")
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
            size=_safe_decimal(getattr(item, "original_size", Decimal("0"))),
            original_size=_safe_decimal(getattr(item, "original_size", None)),
            size_matched=_safe_decimal(getattr(item, "size_matched", None)),
            status=getattr(item, "status", None),
            created_at=getattr(item, "created_at", None),
        )

    def _normalize_trade(self, item: Any) -> DataApiTrade:
        trader_side = str(getattr(item, "trader_side", "TAKER") or "TAKER").upper()
        order_id = str(getattr(item, "taker_order_id", None) or "")
        side = str(getattr(item, "side", "BUY"))
        price = _safe_decimal(getattr(item, "price", Decimal("0")))
        size = _safe_decimal(getattr(item, "size", Decimal("0")))
        fee = None
        if trader_side == "MAKER":
            owner = self._client_owner_api_key(self._require_client())
            matches = [
                maker for maker in tuple(getattr(item, "maker_orders", ()) or ())
                if str(getattr(maker, "owner", "")) == owner
            ]
            if len(matches) != 1:
                raise PolymarketError("wire_trade_maker_order_ambiguous")
            maker = matches[0]
            order_id = str(getattr(maker, "order_id", "") or "")
            side = str(getattr(maker, "side", "BUY"))
            price = _safe_decimal(getattr(maker, "price", price))
            size = _safe_decimal(getattr(maker, "matched_amount", size))
            fee = Decimal("0")
        else:
            # The account-trade wire exposes fee_rate_bps, not a charged-cash
            # field.  Freeze the documented taker formula here so replay and REST
            # recovery do not silently drop fees.
            rate_bps = _safe_decimal(getattr(item, "fee_rate_bps", None))
            if rate_bps is not None and price is not None and size is not None:
                fee = size * rate_bps / Decimal("10000") * price * (Decimal("1") - price)
        return DataApiTrade(
            trade_id=str(getattr(item, "id", "")),
            order_id=order_id,
            token_id=str(getattr(item, "token_id", "") or getattr(item, "asset_id", "")),
            side=side,
            price=price,
            size=size,
            fee=fee,
            status=str(getattr(item, "status", "") or "") or None,
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
        expected_funder: str,
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
        self.assert_pinned_sdk()
        raw_order = _sdk_order(signed_order)
        maker = str(getattr(raw_order, "maker", "") or "")
        signer = str(getattr(raw_order, "signer", "") or "")
        signature_type = int(getattr(raw_order, "signature_type", -1))
        signature = str(getattr(raw_order, "signature", "") or "")
        body_hash = canonical_order_body_hash(signed_order)
        inner_sig = _inner_eoa_signature(signature)
        recovered = None
        trailer_present = False
        if signature_type == 3 and inner_sig is not None:
            recovered = self._recover_inner_eoa(raw_order, inner_sig, chain_id, exchange_address)
            trailer_present = _has_erc7739_trailer(signature)
        funder_matches = bool(expected_funder) and (
            maker.lower() == str(expected_funder).lower()
            and signer.lower() == str(expected_funder).lower()
        )
        return {
            "maker": maker,
            "signer": signer,
            "deposit_wallet_maker_funder": funder_matches,
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


def _client_signer_address(client: Any) -> str:
    if client is None:
        raise EgressTripwireError(REASON_EGRESS_TRIPWIRE)
    address = getattr(client, "signer", None)
    if not isinstance(address, str) or not address:
        raise PolymarketError("wire_signer_address_unavailable")
    return address


def _build_l2_headers(
    credentials: PrivateApiCredentials,
    *,
    address: str,
    unix_seconds: int,
    method: str,
    path: str,
    body: bytes | None,
) -> dict[str, str]:
    """Build the pinned SDK's exact L2 headers for the exact body bytes."""
    from polymarket._internal.hmac import build_hmac_signature

    message = build_l2_hmac_message(
        unix_seconds=unix_seconds,
        method=method,
        path_without_query=path,
        body=body,
    )
    # ``build_hmac_signature`` constructs the same message internally.  The
    # explicit comparison above validates the stricter repository contract and
    # prevents accidental query/body normalisation before signing.
    if not message.startswith(f"{unix_seconds}{method.upper()}{path}"):
        raise PolymarketError("wire_l2_message_mismatch")
    body_text = body.decode("utf-8") if body is not None else None
    signature = build_hmac_signature(
        secret=credentials.secret,
        timestamp=unix_seconds,
        method=method.upper(),
        path=path,
        body=body_text,
    )
    return {
        "POLY_ADDRESS": address,
        "POLY_API_KEY": credentials.api_key,
        "POLY_PASSPHRASE": credentials.passphrase,
        "POLY_SIGNATURE": signature,
        "POLY_TIMESTAMP": str(unix_seconds),
    }


def _sdk_order(value: Any) -> Any:
    return value._sdk_order if isinstance(value, PreparedSignedOrder) else value


def exact_order_wire_bytes(
    signed_order: Any, *, owner_api_key: str | None = None
) -> tuple[str, bytes]:
    """Return the locked SDK's exact path/body bytes, without fallback."""
    if isinstance(signed_order, PreparedSignedOrder):
        return signed_order.path, signed_order._body_bytes
    if not isinstance(owner_api_key, str) or not owner_api_key:
        raise PolymarketError("wire_owner_api_key_required_for_exact_body")
    try:
        from polymarket._internal.actions.orders.post import build_post_order_request

        path, payload = build_post_order_request(
            signed_order, owner_api_key=owner_api_key
        )
    except Exception as exc:
        raise PolymarketError("wire_exact_body_build_failed") from exc
    # This is byte-for-byte the serialisation used by pinned SDK 0.5.0's
    # SyncTransport._request.
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if path != "/order":
        raise PolymarketError("wire_order_path_mismatch")
    return path, body


def _safe_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("decimal_bool_forbidden")
    if isinstance(value, float):
        raise ValueError("decimal_float_forbidden")
    return Decimal(str(value))


def canonical_order_body_hash(signed_order: Any) -> str:
    """Hash the exact final bytes frozen by ``prepare_signed_order``."""
    if not isinstance(signed_order, PreparedSignedOrder):
        raise PolymarketError("wire_prepared_order_required_for_body_hash")
    actual = hashlib.sha256(signed_order._body_bytes).hexdigest()
    if actual != signed_order.body_hash:
        raise PolymarketError("wire_prepared_body_hash_mismatch")
    return actual


def expected_order_hash_for(
    signed_order: Any, *, chain_id: int, exchange_address: str
) -> str:
    """确定性 order content hash（EIP-712 Order 内容；供 attempt pre-send 持久化）。"""
    try:
        from polymarket._internal.actions.orders.types import UnsignedOrder
        from polymarket._internal.actions.orders.typed_data import (
            build_order_typed_data,
        )

        raw_order = _sdk_order(signed_order)
        unsigned = UnsignedOrder(
            chain_id=int(chain_id),
            builder=str(getattr(raw_order, "builder", "") or ""),
            exchange_address=str(exchange_address),
            expiration=int(getattr(raw_order, "expiration", 0)),
            maker=str(getattr(raw_order, "maker", "") or ""),
            maker_amount=int(getattr(raw_order, "maker_amount", 0)),
            metadata=str(getattr(raw_order, "metadata", "") or ""),
            order_type=str(getattr(raw_order, "order_type", "GTC")),
            salt=int(getattr(raw_order, "salt", 0)),
            side=str(getattr(raw_order, "side", "BUY")),
            signature_type=int(getattr(raw_order, "signature_type", 3)),
            signer=str(getattr(raw_order, "signer", "") or ""),
            taker_amount=int(getattr(raw_order, "taker_amount", 0)),
            timestamp=int(getattr(raw_order, "timestamp", 0)),
            token_id=str(getattr(raw_order, "token_id", "") or ""),
        )
        typed = build_order_typed_data(unsigned)
        from eth_account.messages import _hash_eip191_message, encode_typed_data

        digest = _hash_eip191_message(encode_typed_data(full_message=typed))
        # Persistence uses the repository's 64-lowercase-hex hash type.
        return bytes(digest).hex()
    except Exception as exc:  # noqa: BLE001 - never silently substitute another hash
        raise PolymarketError("wire_expected_order_hash_failed") from exc


def sdk_manifest_hash_for(signed_order: Any) -> str:
    """SDK manifest + 签名向量 hash（attempt pre-send 持久化；不含 secret）。"""
    ClobTradingDriver.assert_pinned_sdk()
    raw_order = _sdk_order(signed_order)
    signature = str(getattr(raw_order, "signature", "") or "")
    if not signature:
        raise PolymarketError("wire_signature_missing")
    payload = {
        "package": PINNED_SDK_PACKAGE,
        "version": PINNED_SDK_VERSION,
        "tag": PINNED_SDK_TAG,
        "tag_commit": PINNED_SDK_COMMIT,
        "signature_type": int(getattr(raw_order, "signature_type", -1)),
        "post_only": bool(getattr(raw_order, "post_only", False)),
        "order_type": str(getattr(raw_order, "order_type", "") or ""),
        "expiration": int(getattr(raw_order, "expiration", 0)),
        "maker": str(getattr(raw_order, "maker", "") or ""),
        "signer": str(getattr(raw_order, "signer", "") or ""),
        "signature_sha256": hashlib.sha256(signature.encode("ascii")).hexdigest(),
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
    "SubmitOutcome", "PreparedSignedOrder", "ClobTradingDriver", "EgressTripwireError",
    "PINNED_SDK_PACKAGE", "PINNED_SDK_VERSION", "PINNED_SDK_TAG",
    "PINNED_SDK_COMMIT", "HEARTBEAT_PATH",
    "canonical_order_body_hash", "exact_order_wire_bytes",
    "expected_order_hash_for", "sdk_manifest_hash_for",
]
