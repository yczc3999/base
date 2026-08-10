"""Polymarket Driver 公共框架（WP-01B Checkpoint A）。

- ``WirePolicy``：connect/read/write/total 超时、有限重试（429/425/5xx）+ jitter、
  rate-limit token bucket、批量上限。
- ``HttpPolymarketDriver``：每次调用生成脱敏 ``RequestReceipt``（失败也要生成，任务 §2.7）；
  JSON 用 ``parse_float=Decimal`` 解析并拒绝 NaN/Infinity；非 retryable 4xx 不重试。
- 禁止模块级连接 singleton：Driver 短生命周期，Service 按调用构造。
- 不写 DB/Redis、不做业务判断（实施合同 §5.1）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

import httpx

from app.schemas.polymarket.common import (
    REASON_CONNECT_TIMEOUT,
    REASON_EMPTY_RESPONSE,
    REASON_HTTP_425,
    REASON_HTTP_4XX,
    REASON_HTTP_5XX,
    REASON_MALFORMED_JSON,
    REASON_READ_TIMEOUT,
    REASON_TOTAL_TIMEOUT,
    REASON_TOO_MANY_REQUESTS,
    DriverCallResult,
    PolymarketError,
    RequestReceipt,
    is_retryable_status,
    redact_headers,
)

Clock = Callable[[], float]


@dataclass(frozen=True)
class WirePolicy:
    """Provider wire 超时/重试/限流参数；测试用显式 policy fixture（任务 §2.9）。"""

    connect_timeout_s: float = 5.0
    read_timeout_s: float = 15.0
    write_timeout_s: float = 15.0
    total_timeout_s: float | None = None
    max_retries: int = 3
    base_backoff_s: float = 0.25
    max_backoff_s: float = 8.0
    jitter_s: float = 0.1
    rate_per_second: float = 10.0
    rate_burst: int = 20
    max_batch_size: int = 500

    def __post_init__(self) -> None:
        positive = {
            "connect_timeout_s": self.connect_timeout_s,
            "read_timeout_s": self.read_timeout_s,
            "write_timeout_s": self.write_timeout_s,
            "max_backoff_s": self.max_backoff_s,
            "rate_per_second": self.rate_per_second,
        }
        for name, value in positive.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError(f"{name} must be > 0")
        for name, value in {
            "base_backoff_s": self.base_backoff_s,
            "jitter_s": self.jitter_s,
        }.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError(f"{name} must be >= 0")
        if self.total_timeout_s is not None and (
            isinstance(self.total_timeout_s, bool)
            or not isinstance(self.total_timeout_s, (int, float))
            or not math.isfinite(float(self.total_timeout_s))
            or self.total_timeout_s <= 0
        ):
            raise ValueError("total_timeout_s must be > 0 when set")
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or not (0 <= self.max_retries <= 10)
        ):
            raise ValueError("max_retries must be in 0..10")
        if (
            isinstance(self.rate_burst, bool)
            or not isinstance(self.rate_burst, int)
            or self.rate_burst <= 0
        ):
            raise ValueError("rate_burst must be > 0")
        if (
            isinstance(self.max_batch_size, bool)
            or not isinstance(self.max_batch_size, int)
            or self.max_batch_size <= 0
        ):
            raise ValueError("max_batch_size must be > 0")


class RateLimiter:
    """进程内 token bucket（上限 policy；429/Retry-After 信号由重试层叠加）。"""

    def __init__(self, rate_per_second: float, burst: int, clock: Clock = time.monotonic) -> None:
        if rate_per_second <= 0:
            raise ValueError(f"rate_per_second must be > 0, got {rate_per_second}")
        if burst <= 0:
            raise ValueError(f"burst must be > 0, got {burst}")
        self._rate = rate_per_second
        self._burst = float(burst)
        self._tokens = float(burst)
        self._last = clock()
        self._clock = clock
        self._lock = asyncio.Lock()

    async def acquire(self, n: int = 1) -> None:
        while True:
            async with self._lock:
                now = self._clock()
                elapsed = max(0.0, now - self._last)
                self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                wait = (n - self._tokens) / self._rate
            await asyncio.sleep(wait)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reject_constant(value: str) -> Any:
    raise ValueError("json_nan_infinity_forbidden")


def parse_json_bytes(body: bytes) -> Any:
    """``parse_float=Decimal`` + 拒绝 NaN/Infinity；空响应 fail-closed。"""
    if not body:
        raise PolymarketError(REASON_EMPTY_RESPONSE)
    try:
        return json.loads(
            body,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
        raise PolymarketError(REASON_MALFORMED_JSON) from exc


def _status_reason(status: int) -> str:
    if status == 429:
        return REASON_TOO_MANY_REQUESTS
    if status == 425:
        return REASON_HTTP_425
    if status >= 500:
        return REASON_HTTP_5XX
    return REASON_HTTP_4XX


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        return None


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _request_fingerprint(
    method: str,
    url: str,
    params: dict[str, Any],
    body: bytes | None,
) -> bytes:
    """Canonical request identity without persisting raw query/header values.

    Public wire calls still need distinct receipts for token/cursor/side.  The
    fingerprint includes the normalized final URL only inside the hash input;
    the receipt continues to expose just the endpoint path.
    """
    final_url = str(httpx.URL(url, params=sorted(params.items())))
    canonical = {
        "method": method.upper(),
        "url": final_url,
        "body_sha256": _hash(body or b""),
    }
    return json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()


class HttpPolymarketDriver:
    """HTTP Driver 基类：统一超时、有限重试、限流、JSON/Decimal 解析与 receipts。"""

    def __init__(
        self,
        base_url: str,
        *,
        policy: WirePolicy,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url must be absolute http(s), got {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self._policy = policy
        self._transport = transport
        self._clock = clock or time.monotonic
        self._limiter = RateLimiter(policy.rate_per_second, policy.rate_burst, self._clock)

    # ---- 内部请求 ----

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            self._policy.read_timeout_s,
            connect=self._policy.connect_timeout_s,
            write=self._policy.write_timeout_s,
        )

    def _backoff_seconds(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            retry_after = _retry_after_seconds(response)
            if retry_after is not None:
                return min(retry_after, self._policy.max_backoff_s)
        cap = min(self._policy.base_backoff_s * (2 ** attempt), self._policy.max_backoff_s)
        return cap + random.uniform(0.0, self._policy.jitter_s)

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> DriverCallResult:
        """执行一次（最多重试）HTTP 调用；返回 typed/raw/receipts。失败抛 PolymarketError。"""
        url = f"{self.base_url}{path}"
        if params is None:
            params = {}
        body_bytes = json.dumps(json_body, separators=(",", ":")).encode() if json_body is not None else None
        request_headers = dict(headers or {})
        if json_body is not None:
            request_headers.setdefault("content-type", "application/json")
        request_fingerprint = _request_fingerprint(method, url, params, body_bytes)
        loop = asyncio.get_running_loop()
        deadline = (
            loop.time() + self._policy.total_timeout_s
            if self._policy.total_timeout_s is not None
            else None
        )

        client = httpx.AsyncClient(timeout=self._timeout(), transport=self._transport)
        receipts: list[RequestReceipt] = []
        try:
            for attempt in range(self._policy.max_retries + 1):
                try:
                    if deadline is None:
                        await self._limiter.acquire()
                    else:
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            raise asyncio.TimeoutError
                        await asyncio.wait_for(self._limiter.acquire(), timeout=remaining)
                except asyncio.TimeoutError as exc:
                    receipts.append(
                        RequestReceipt.new(
                            endpoint=url,
                            method=method,
                            http_status=None,
                            latency_ms=0,
                            error_code=REASON_TOTAL_TIMEOUT,
                            request_body=body_bytes,
                            response_body=None,
                            retry_count=attempt,
                            request_fingerprint=request_fingerprint,
                            redacted_header_names=redact_headers(request_headers),
                        )
                    )
                    raise PolymarketError(
                        REASON_TOTAL_TIMEOUT, receipts=receipts
                    ) from exc
                t0 = self._clock()
                error_code: str | None = None
                http_status: int | None = None
                response_body: bytes | None = None
                retry_after: float | None = None
                try:
                    if deadline is None:
                        response = await client.request(
                            method,
                            url,
                            params=params,
                            content=body_bytes,
                            headers=request_headers,
                        )
                    else:
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            raise asyncio.TimeoutError
                        response = await asyncio.wait_for(
                            client.request(
                                method,
                                url,
                                params=params,
                                content=body_bytes,
                                headers=request_headers,
                            ),
                            timeout=remaining,
                        )
                    http_status = response.status_code
                    retry_after = _retry_after_seconds(response)
                    response_body = await response.aread()
                except httpx.ConnectTimeout as exc:
                    error_code = REASON_CONNECT_TIMEOUT
                except httpx.ReadTimeout as exc:
                    error_code = REASON_READ_TIMEOUT
                except httpx.TimeoutException:
                    error_code = REASON_TOTAL_TIMEOUT
                except asyncio.TimeoutError:
                    error_code = REASON_TOTAL_TIMEOUT
                except httpx.HTTPError as exc:
                    error_code = "wire_http_transport"

                latency_ms = int((self._clock() - t0) * 1000)
                receipt = RequestReceipt.new(
                    endpoint=url,
                    method=method,
                    http_status=http_status,
                    latency_ms=latency_ms,
                    error_code=error_code,
                    request_body=body_bytes,
                    response_body=response_body,
                    retry_count=attempt,
                    request_fingerprint=request_fingerprint,
                    retry_after_s=retry_after,
                    redacted_header_names=redact_headers(request_headers),
                )
                receipts.append(receipt)

                if error_code is None and http_status == 200:
                    try:
                        typed = parse_json_bytes(response_body)
                    except PolymarketError as exc:
                        raise PolymarketError(
                            exc.reason_code,
                            http_status=http_status,
                            detail=exc.detail,
                            receipts=receipts,
                        ) from exc
                    return DriverCallResult(typed=typed, raw=response_body, receipts=tuple(receipts))

                retryable = (
                    (error_code is not None and error_code != REASON_TOTAL_TIMEOUT)
                    or (http_status is not None and is_retryable_status(http_status))
                )
                if retryable and attempt < self._policy.max_retries:
                    backoff = self._backoff_seconds(
                        attempt, response if error_code is None else None
                    )
                    if deadline is not None:
                        remaining = deadline - loop.time()
                        if remaining <= backoff:
                            raise PolymarketError(
                                REASON_TOTAL_TIMEOUT, receipts=receipts
                            )
                    await asyncio.sleep(backoff)
                    continue

                if error_code is not None:
                    raise PolymarketError(
                        error_code, http_status=http_status, receipts=receipts
                    )
                assert http_status is not None
                raise PolymarketError(
                    _status_reason(http_status),
                    http_status=http_status,
                    receipts=receipts,
                )
            raise PolymarketError("wire_unreachable")
        finally:
            await client.aclose()

    # ---- 便捷 ----

    async def get_json(self, path: str, *, params: dict[str, Any] | None = None,
                       headers: dict[str, str] | None = None) -> DriverCallResult:
        return await self.request_json("GET", path, params=params, headers=headers)

    async def post_json(self, path: str, *, json_body: Any,
                        headers: dict[str, str] | None = None) -> DriverCallResult:
        return await self.request_json("POST", path, json_body=json_body, headers=headers)

    async def aclose(self) -> None:
        """无持久连接；保留以符合短生命周期 Driver 合同。"""
