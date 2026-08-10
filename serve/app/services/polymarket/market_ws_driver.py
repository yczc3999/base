"""Wire-only driver for the public Polymarket Market WebSocket.

The driver owns connection/subscription, application PING/PONG liveness and
raw-frame receipts.  Book authority, epochs and reconnect policy remain in the
calling Logic layer.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.schemas.polymarket.common import (
    PolymarketError,
    RequestReceipt,
    REASON_WS_DISCONNECT,
    REASON_WS_PONG_TIMEOUT,
    REASON_WS_SUBSCRIBE,
)
from app.schemas.polymarket.market_ws import MarketWsFrameBase, parse_market_ws_frame

SUBSCRIBE_TEMPLATE = {
    "type": "market",
    "custom_feature_enabled": True,
}
PING_TEXT = "PING"
PONG_TEXT = "PONG"


@dataclass(frozen=True)
class MarketWsMessage:
    receive_seq: int
    received_at: datetime
    raw_text: str
    frame: MarketWsFrameBase
    receipts: tuple[RequestReceipt, ...] = ()


@dataclass(frozen=True)
class MarketWsPolicy:
    connect_timeout_s: float = 10.0
    recv_timeout_s: float = 30.0
    ping_interval_s: float = 10.0
    pong_timeout_s: float = 35.0

    def __post_init__(self) -> None:
        for name in (
            "connect_timeout_s",
            "recv_timeout_s",
            "ping_interval_s",
            "pong_timeout_s",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name}_must_be_positive")
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{name}_must_be_positive")


class MarketWsDriver:
    """One short-lived Market WS connection."""

    def __init__(
        self,
        url: str,
        assets_ids: list[str],
        *,
        policy: MarketWsPolicy | None = None,
        ws_connect: Callable[[], Awaitable[Any]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not assets_ids or any(not isinstance(item, str) or not item for item in assets_ids):
            raise ValueError("assets_ids empty or invalid")
        if not url.startswith(("ws://", "wss://")):
            raise ValueError(f"url must be ws(s), got {url!r}")
        self.url = url
        self.assets_ids = list(assets_ids)
        self._policy = policy or MarketWsPolicy()
        self._ws_connect = ws_connect
        import time as _time

        self._clock = clock or _time.monotonic
        self._ws: Any | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._receive_seq = 0
        self._awaiting_pong_since: float | None = None
        self._subscribed = False
        self._fault_event = asyncio.Event()
        self._terminal_reason: str | None = None
        # Receipts are handed to the caller on every message/error.  Keep only
        # a bounded diagnostic tail locally; retaining every frame for a
        # long-lived high-rate socket would itself become an outage.
        self._receipts: deque[RequestReceipt] = deque(maxlen=64)

    @property
    def receive_seq(self) -> int:
        return self._receive_seq

    @property
    def receipts(self) -> tuple[RequestReceipt, ...]:
        return tuple(self._receipts)

    def _subscribe_bytes(self) -> bytes:
        payload = dict(SUBSCRIBE_TEMPLATE)
        payload["assets_ids"] = self.assets_ids
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()

    def _new_receipt(
        self,
        *,
        method: str,
        started_at: float,
        error_code: str | None,
        request_body: bytes | None = None,
        response_body: bytes | None = None,
    ) -> RequestReceipt:
        receipt = RequestReceipt.new(
            endpoint=self.url,
            method=method,
            http_status=None,
            latency_ms=max(0, int((self._clock() - started_at) * 1000)),
            error_code=error_code,
            request_body=request_body,
            response_body=response_body,
            retry_count=0,
        )
        self._receipts.append(receipt)
        return receipt

    def _error(self, reason: str, receipt: RequestReceipt) -> PolymarketError:
        # ``common.PolymarketError`` acquired the receipts field in this same
        # checkpoint.  The compatibility branch makes this file safe during a
        # partially-applied checkout as well.
        try:
            return PolymarketError(reason, receipts=tuple(self._receipts))
        except TypeError:
            error = PolymarketError(reason)
            error.receipts = (receipt,)  # type: ignore[attr-defined]
            return error

    async def connect(self) -> None:
        """Connect and send the documented subscription frame."""
        started = self._clock()
        subscription = self._subscribe_bytes()
        try:
            connector = self._ws_connect
            if connector is None:
                import websockets

                awaitable = websockets.connect(self.url)
            else:
                awaitable = connector()
            self._ws = await asyncio.wait_for(
                awaitable, timeout=self._policy.connect_timeout_s
            )
            await asyncio.wait_for(
                self._ws.send(subscription.decode()),
                timeout=self._policy.connect_timeout_s,
            )
            self._subscribed = True
        except BaseException as exc:
            ws, self._ws = self._ws, None
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass
            if isinstance(exc, asyncio.CancelledError):
                raise
            receipt = self._new_receipt(
                method="WS CONNECT+SUBSCRIBE",
                started_at=started,
                error_code=REASON_WS_SUBSCRIBE,
                request_body=subscription,
            )
            raise self._error(REASON_WS_SUBSCRIBE, receipt) from exc

        self._new_receipt(
            method="WS CONNECT+SUBSCRIBE",
            started_at=started,
            error_code=None,
            request_body=subscription,
        )
        self._ping_task = asyncio.create_task(self._ping_loop())

    def _set_fault(self, reason: str) -> None:
        if self._terminal_reason is None:
            self._terminal_reason = reason
            self._fault_event.set()

    async def _pong_watchdog(self, ping_started_at: float) -> None:
        try:
            await asyncio.sleep(self._policy.pong_timeout_s)
            if self._awaiting_pong_since == ping_started_at:
                self._set_fault(REASON_WS_PONG_TIMEOUT)
        except asyncio.CancelledError:
            raise

    async def _ping_loop(self) -> None:
        try:
            while self._ws is not None:
                await asyncio.sleep(self._policy.ping_interval_s)
                if self._ws is None:
                    return
                started = self._clock()
                try:
                    await self._ws.send(PING_TEXT)
                except Exception:
                    self._new_receipt(
                        method="WS PING",
                        started_at=started,
                        error_code=REASON_WS_DISCONNECT,
                        request_body=PING_TEXT.encode(),
                    )
                    self._set_fault(REASON_WS_DISCONNECT)
                    return
                self._new_receipt(
                    method="WS PING",
                    started_at=started,
                    error_code=None,
                    request_body=PING_TEXT.encode(),
                )
                # Additional PINGs must not move the original deadline.  A
                # stream of data frames therefore cannot mask a missing PONG.
                if self._awaiting_pong_since is None:
                    self._awaiting_pong_since = started
                    self._watchdog_task = asyncio.create_task(
                        self._pong_watchdog(started)
                    )
        except asyncio.CancelledError:
            raise

    async def _raise_terminal(self, started: float) -> None:
        reason = self._terminal_reason or REASON_WS_DISCONNECT
        receipt = self._new_receipt(
            method="WS RECV",
            started_at=started,
            error_code=reason,
        )
        raise self._error(reason, receipt)

    async def next_frame(self) -> MarketWsMessage:
        """Receive one frame while independently enforcing the PONG deadline."""
        if self._ws is None or not self._subscribed:
            started = self._clock()
            receipt = self._new_receipt(
                method="WS RECV",
                started_at=started,
                error_code=REASON_WS_DISCONNECT,
            )
            raise self._error(REASON_WS_DISCONNECT, receipt)
        if self._fault_event.is_set():
            await self._raise_terminal(self._clock())

        started = self._clock()
        recv_task = asyncio.create_task(self._ws.recv())
        fault_task = asyncio.create_task(self._fault_event.wait())
        try:
            done, _ = await asyncio.wait(
                {recv_task, fault_task},
                timeout=self._policy.recv_timeout_s,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if fault_task in done and self._fault_event.is_set():
                recv_task.cancel()
                await asyncio.gather(recv_task, return_exceptions=True)
                await self._raise_terminal(started)
            if recv_task not in done:
                recv_task.cancel()
                await asyncio.gather(recv_task, return_exceptions=True)
                receipt = self._new_receipt(
                    method="WS RECV",
                    started_at=started,
                    error_code=REASON_WS_PONG_TIMEOUT,
                )
                raise self._error(REASON_WS_PONG_TIMEOUT, receipt)
            try:
                raw = recv_task.result()
            except Exception as exc:
                receipt = self._new_receipt(
                    method="WS RECV",
                    started_at=started,
                    error_code=REASON_WS_DISCONNECT,
                )
                raise self._error(REASON_WS_DISCONNECT, receipt) from exc
        finally:
            if not recv_task.done():
                recv_task.cancel()
                await asyncio.gather(recv_task, return_exceptions=True)
            fault_task.cancel()
            await asyncio.gather(fault_task, return_exceptions=True)

        raw_text = (
            raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        )
        receipt = self._new_receipt(
            method="WS RECV",
            started_at=started,
            error_code=None,
            response_body=raw_text.encode(),
        )
        self._receive_seq += 1
        frame = parse_market_ws_frame(raw_text)
        if frame.event_type == "pong":
            self._awaiting_pong_since = None
            if self._watchdog_task is not None:
                self._watchdog_task.cancel()
                await asyncio.gather(self._watchdog_task, return_exceptions=True)
                self._watchdog_task = None
        return MarketWsMessage(
            receive_seq=self._receive_seq,
            received_at=datetime.now(timezone.utc),
            raw_text=raw_text,
            frame=frame,
            receipts=(receipt,),
        )

    async def aclose(self) -> None:
        tasks = [task for task in (self._ping_task, self._watchdog_task) if task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._ping_task = None
        self._watchdog_task = None
        ws, self._ws = self._ws, None
        self._subscribed = False
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
