"""Polymarket Relayer-v2 Deposit Wallet driver（WP-06 Checkpoint C）。

只实现 §2.2 冻结 wire：``GET /v1/account/transactions/params``（nonce）、
``POST /submit``（exact body）、``GET /v1/account/transactions/{id}``（status）。
EIP-712 domain ``DepositWallet/1/chainId=137``；deadline=``trusted_now+600s``；
HMAC 输入 ``timestamp + UPPERCASE_METHOD + path + exact serialized body``。

- 默认 ``require_injected_transport=true``：缺 transport 任何调用在 socket/client 构造前
  立即 ``EgressTripwireError``。
- exact serialized body 与 HMAC bytes 共用同一对象（sign 与 send 同一 bytes）。
- timeout/断连/5xx/bad body 一律 ``OUTCOME_UNKNOWN``；不生成新 nonce/deadline/signature；
  driver 不做 DB 写、不持久化 secret/signature/raw signed body。
- Builder/Relayer secret 只以注入式 signer/HMAC 回调出现；header 名固定。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from app.schemas.polymarket.chain import RelayerStatus
from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.base import EgressTripwireError

# HTTP-like transport：``transport.request(method, path, *, params, body, headers)
# -> tuple[int, bytes]`` 返回 (status_code, response_body)。
RelayerTransport = Callable[..., tuple[int, bytes] | Awaitable[tuple[int, bytes]]]
TrustedTimeProvider = Callable[[], int]
Signer = Callable[[str], str]                  # 输入 typed-data hash / 输出 EIP-712 sig hex
HmacSigner = Callable[[bytes], str]            # 输入 exact bytes / 输出 urlsafe b64 签名
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


@dataclass(frozen=True)
class SubmitOutcome:
    """一次 submit 的分类结果（单次发送；不含 signature/secret 明文）。"""

    cls: str
    http_status: int | None = None
    transaction_id: str | None = None
    state: str | None = None
    latency_ms: int = 0


class RelayerDriver:
    """Relayer-v2 Deposit Wallet driver（fake-only；require_injected_transport）。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        transport: RelayerTransport | None = None,
        require_injected_transport: bool = True,
        clock: Clock | None = None,
        trusted_time_provider: TrustedTimeProvider | None = None,
        signer: Signer | None = None,
        hmac_signer: HmacSigner | None = None,
        deadline_ttl_s: int = 600,
    ) -> None:
        self._base_url = (base_url or "https://relayer-v2.polymarket.com").rstrip("/")
        self._transport = transport
        self._require_injected_transport = require_injected_transport
        self._clock = clock or time.monotonic
        self._trusted_time_provider = trusted_time_provider
        self._signer = signer
        self._hmac_signer = hmac_signer
        self._deadline_ttl_s = int(deadline_ttl_s)
        self._transport_calls = 0
        self._fake_calls = 0
        self._real_calls = 0

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
        if self._transport is None:
            if self._require_injected_transport:
                raise EgressTripwireError()
            raise PolymarketError("wire_no_transport")
        return self._transport

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
        if self._transport is not None:
            self._fake_calls += 1
        else:
            self._real_calls += 1
        result = await transport(
            method, path, params=params or {}, body=body, headers=dict(headers or {})
        )
        if not (isinstance(result, tuple) and len(result) == 2):
            raise PolymarketError("relayer_transport_response_invalid")
        status, response_body = result
        if not isinstance(status, int):
            raise PolymarketError("relayer_transport_status_invalid")
        if not isinstance(response_body, bytes):
            raise PolymarketError("relayer_transport_body_invalid")
        return status, response_body

    def _parse_json(self, body: bytes) -> Any:
        if not body:
            raise PolymarketError("relayer_empty_response")
        try:
            return json.loads(body, parse_float=float)
        except (json.JSONDecodeError, ValueError) as exc:
            raise PolymarketError("relayer_malformed_json") from exc

    # ---- trusted time / deadline ----

    def trusted_now(self) -> int:
        if self._trusted_time_provider is not None:
            return int(self._trusted_time_provider())
        return int(time.time())

    def deadline(self) -> int:
        return self.trusted_now() + self._deadline_ttl_s

    # ---- nonce ----

    async def get_nonce(self, address: str) -> str:
        """GET /v1/account/transactions/params?address=&type=WALLET → nonce 十进制字符串。"""
        status, body = await self._request(
            "GET", NONCE_PATH, params={"address": address, "type": "WALLET"},
            headers={name: "" for name in NONCE_HEADERS},
        )
        if status != 200:
            raise PolymarketError(f"relayer_nonce_http_{status}")
        parsed = self._parse_json(body)
        if not isinstance(parsed, dict):
            raise PolymarketError("relayer_nonce_response_not_object")
        nonce = parsed.get("nonce")
        if not isinstance(nonce, str) or not nonce.isdigit():
            raise PolymarketError("relayer_nonce_not_decimal")
        return nonce

    # ---- exact body / HMAC ----

    def build_submit_body(
        self,
        *,
        from_address: str,
        to_address: str,
        nonce: str,
        deadline: int,
        deposit_wallet: str,
        calls: list[dict[str, str]],
        metadata: str,
        signature: str,
    ) -> dict:
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

    def serialize_body_exact(self, body: dict) -> bytes:
        """exact serialized bytes：sign 与 send 共用同一对象（禁止重复序列化）。"""
        return json.dumps(body, separators=(",", ":")).encode()

    def build_hmac_input(self, *, timestamp: int, method: str,
                         path: str, body_bytes: bytes) -> str:
        method_upper = method.upper()
        body_text = body_bytes.decode("utf-8", errors="strict")
        return f"{timestamp}{method_upper}{path}{body_text}"

    def hmac_signature(self, hmac_input: str) -> str:
        """用注入式 HMAC signer 生成 Builder signature（secret 不进入本 Driver）。"""
        if self._hmac_signer is None:
            raise PolymarketError("relayer_hmac_signer_missing")
        signature = self._hmac_signer(hmac_input.encode())
        if not isinstance(signature, str) or not signature:
            raise PolymarketError("relayer_hmac_signer_invalid")
        return signature

    # ---- submit ----

    async def submit_batch(
        self,
        *,
        from_address: str,
        to_address: str,
        nonce: str,
        deposit_wallet: str,
        calls: list[dict[str, str]],
        metadata: str,
        signature: str,
        deadline: int | None = None,
    ) -> SubmitOutcome:
        """POST /submit 单次发送。timeout/5xx/bad body → OUTCOME_UNKNOWN。"""
        # 缺 transport 在构造 body/signature 前立即 tripwire（不生成新 nonce/deadline/sig）
        self._ensure_transport()
        now = self.trusted_now()
        deadline = deadline or self.deadline()
        body = self.build_submit_body(
            from_address=from_address, to_address=to_address, nonce=nonce,
            deadline=deadline, deposit_wallet=deposit_wallet, calls=calls,
            metadata=metadata, signature=signature,
        )
        body_bytes = self.serialize_body_exact(body)
        hmac_input = self.build_hmac_input(
            timestamp=now, method="POST", path=SUBMIT_PATH, body_bytes=body_bytes,
        )
        builder_signature = self.hmac_signature(hmac_input)
        headers = {
            "POLY_BUILDER_API_KEY": "{{builder-api-key-ref}}",
            "POLY_BUILDER_TIMESTAMP": str(now),
            "POLY_BUILDER_PASSPHRASE": "{{builder-passphrase-ref}}",
            "POLY_BUILDER_SIGNATURE": builder_signature,
            "content-type": "application/json",
        }
        t0 = self._clock()
        try:
            status, resp_body = await self._request(
                "POST", SUBMIT_PATH, body=body_bytes, headers=headers,
            )
        except EgressTripwireError:
            raise
        except Exception:
            return SubmitOutcome(cls=OUTCOME_UNKNOWN, latency_ms=int((self._clock() - t0) * 1000))
        latency = int((self._clock() - t0) * 1000)
        if status != 200:
            return SubmitOutcome(cls=OUTCOME_UNKNOWN, http_status=status, latency_ms=latency)
        try:
            parsed = self._parse_json(resp_body)
        except PolymarketError:
            return SubmitOutcome(cls=OUTCOME_UNKNOWN, http_status=status, latency_ms=latency)
        if not isinstance(parsed, dict):
            return SubmitOutcome(cls=OUTCOME_UNKNOWN, http_status=status, latency_ms=latency)
        tx_id = parsed.get("transaction_id") or parsed.get("id")
        state = parsed.get("state")
        return SubmitOutcome(
            cls="SUBMITTED" if tx_id else OUTCOME_UNKNOWN,
            http_status=status,
            transaction_id=str(tx_id) if tx_id is not None else None,
            state=str(state) if state is not None else None,
            latency_ms=latency,
        )

    # ---- status ----

    async def get_transaction_status(self, transaction_id: str) -> RelayerStatus:
        """GET /v1/account/transactions/{id}（legacy /transaction 无 fallback）。"""
        status, body = await self._request(
            "GET", f"/v1/account/transactions/{transaction_id}"
        )
        if status != 200:
            raise PolymarketError(f"relayer_status_http_{status}")
        parsed = self._parse_json(body)
        try:
            return RelayerStatus.model_validate(parsed)
        except Exception as exc:
            raise PolymarketError("relayer_status_malformed") from exc
