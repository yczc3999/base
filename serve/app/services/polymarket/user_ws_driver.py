"""User WebSocket driver（WP-05 Checkpoint C）。

私有账户 order/trade 流。Driver 只负责连接/订阅/应用层 PING/PONG/断线判定与 raw-frame
receipts；业务状态转换（断线 → RECONCILING）由调用方 Logic/Runtime 处理。

- 初始 auth 订阅账户全量 order/trade；10s 文本 PING/PONG。
- raw private frame 只以脱敏 artifact hash / typed event 落库（Driver 不做落库，只产出
  ``UserWsMessage``，其 ``frame`` 与 ``artifact_hash`` 由调用方持久化）。
- 断线即判定为需要 RECONCILING（``terminal_reason`` 非空），不自行重连（重连后仍须
  REST 全量回补）。
- 所有构造器可注入 fake WS transport / clock；未注入时任何真实 connect 立即失败。
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.schemas.polymarket.clob_private import PrivateApiCredentials
from app.schemas.polymarket.common import (
    PolymarketError,
    RequestReceipt,
    REASON_WS_DISCONNECT,
    REASON_WS_PONG_TIMEOUT,
    REASON_WS_SUBSCRIBE,
)
from app.schemas.polymarket.user_ws import (
    UserWsFrameBase,
    parse_user_ws_frame,
    user_ws_frame_artifact_hash,
)
from app.services.polymarket.base import REASON_EGRESS_TRIPWIRE

SUBSCRIBE_TEMPLATE = {"type": "user"}
PING_TEXT = "PING"
PONG_TEXT = "PONG"

Clock = Callable[[], float]


@dataclass(frozen=True)
class UserWsPolicy:
    connect_timeout_s: float = 10.0
    recv_timeout_s: float = 30.0
    ping_interval_s: float = 10.0
    pong_timeout_s: float = 35.0

    def __post_init__(self) -> None:
        for name in (
            "connect_timeout_s", "recv_timeout_s", "ping_interval_s", "pong_timeout_s",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name}_must_be_positive")
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{name}_must_be_positive")


@dataclass(frozen=True)
class UserWsMessage:
    receive_seq: int
    received_at: datetime
    frame: UserWsFrameBase
    artifact_hash: str
    receipts: tuple[RequestReceipt, ...] = ()


class UserWsDriver:
    """一条短生命周期 User WS 连接。"""

    def __init__(
        self,
        url: str,
        *,
        policy: UserWsPolicy | None = None,
        ws_connect: Callable[[], Awaitable[Any]] | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not url.startswith(("ws://", "wss://")):
            raise ValueError(f"url must be ws(s), got {url!r}")
        self.url = url
        self._policy = policy or UserWsPolicy()
        self._ws_connect = ws_connect
        self._clock = clock or time.monotonic
        self._ws: Any | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._receive_seq = 0
        self._awaiting_pong_since: float | None = None
        self._subscribed = False
        self._fault_event = asyncio.Event()
        self._terminal_reason: str | None = None
        self._receipts: deque[RequestReceipt] = deque(maxlen=64)
        self._transport_calls = 0

    @property
    def receive_seq(self) -> int:
        return self._receive_seq

    @property
    def receipts(self) -> tuple[RequestReceipt, ...]:
        return tuple(self._receipts)

    @property
    def terminal_reason(self) -> str | None:
        return self._terminal_reason

    @property
    def transport_calls(self) -> int:
        return self._transport_calls

    def _subscribe_bytes(self, credentials: PrivateApiCredentials) -> bytes:
        if not isinstance(credentials, PrivateApiCredentials):
            raise TypeError("credentials_must_be_private_api_credentials")
        payload = {
            "auth": {
                "apiKey": credentials.api_key,
                "secret": credentials.secret,
                "passphrase": credentials.passphrase,
            },
            **SUBSCRIBE_TEMPLATE,
        }
        return json.dumps(payload, separators=(",", ":")).encode()

    def _new_receipt(
        self, *, method: str, started_at: float, error_code: str | None,
        request_body: bytes | None = None, response_body: bytes | None = None,
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
        try:
            return PolymarketError(reason, receipts=tuple(self._receipts))
        except TypeError:
            error = PolymarketError(reason)
            error.receipts = (receipt,)  # type: ignore[attr-defined]
            return error

    def _set_fault(self, reason: str) -> None:
        if self._terminal_reason is None:
            self._terminal_reason = reason
            self._fault_event.set()

    async def connect(self, credentials: PrivateApiCredentials) -> None:
        started = self._clock()
        subscription = self._subscribe_bytes(credentials)
        if self._ws_connect is None:
            # fake-only：未注入 transport 时任何真实 connect/socket 立即失败（egress tripwire）。
            receipt = self._new_receipt(
                method="USER WS CONNECT+SUBSCRIBE",
                started_at=started,
                error_code=REASON_EGRESS_TRIPWIRE,
                request_body=subscription,
            )
            raise PolymarketError(REASON_EGRESS_TRIPWIRE, receipts=(receipt,))
        try:
            awaitable = self._ws_connect()
            self._ws = await asyncio.wait_for(
                awaitable, timeout=self._policy.connect_timeout_s
            )
            await asyncio.wait_for(
                self._ws.send(subscription.decode()),
                timeout=self._policy.connect_timeout_s,
            )
            self._subscribed = True
            self._transport_calls += 1
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
                method="USER WS CONNECT+SUBSCRIBE",
                started_at=started,
                error_code=REASON_WS_SUBSCRIBE,
                request_body=subscription,
            )
            raise self._error(REASON_WS_SUBSCRIBE, receipt) from exc
        self._new_receipt(
            method="USER WS CONNECT+SUBSCRIBE",
            started_at=started,
            error_code=None,
            request_body=subscription,
        )
        self._ping_task = asyncio.create_task(self._ping_loop())

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
                    self._transport_calls += 1
                except Exception:
                    self._new_receipt(
                        method="USER WS PING",
                        started_at=started,
                        error_code=REASON_WS_DISCONNECT,
                        request_body=PING_TEXT.encode(),
                    )
                    self._set_fault(REASON_WS_DISCONNECT)
                    return
                self._new_receipt(
                    method="USER WS PING",
                    started_at=started,
                    error_code=None,
                    request_body=PING_TEXT.encode(),
                )
                if self._awaiting_pong_since is None:
                    self._awaiting_pong_since = started
                    self._watchdog_task = asyncio.create_task(
                        self._pong_watchdog(started)
                    )
        except asyncio.CancelledError:
            raise

    async def _raise_terminal(self, started: float) -> None:
        reason = self._terminal_reason or REASON_WS_DISCONNECT
        receipt = self._new_receipt(method="USER WS RECV", started_at=started, error_code=reason)
        raise self._error(reason, receipt)

    async def next_frame(self) -> UserWsMessage:
        """接收一帧；独立强制 PONG deadline。断线/PONG 超时 → terminal。"""
        if self._ws is None or not self._subscribed:
            started = self._clock()
            receipt = self._new_receipt(
                method="USER WS RECV", started_at=started, error_code=REASON_WS_DISCONNECT,
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
                    method="USER WS RECV", started_at=started, error_code=REASON_WS_PONG_TIMEOUT,
                )
                raise self._error(REASON_WS_PONG_TIMEOUT, receipt)
            try:
                raw = recv_task.result()
            except Exception as exc:
                self._set_fault(REASON_WS_DISCONNECT)
                receipt = self._new_receipt(
                    method="USER WS RECV", started_at=started, error_code=REASON_WS_DISCONNECT,
                )
                raise self._error(REASON_WS_DISCONNECT, receipt) from exc
        finally:
            if not recv_task.done():
                recv_task.cancel()
                await asyncio.gather(recv_task, return_exceptions=True)
            fault_task.cancel()
            await asyncio.gather(fault_task, return_exceptions=True)

        raw_text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        receipt = self._new_receipt(
            method="USER WS RECV", started_at=started, error_code=None,
            response_body=raw_text.encode(),
        )
        self._receive_seq += 1
        frame = parse_user_ws_frame(raw_text)
        if frame.event_type == "pong":
            self._awaiting_pong_since = None
            if self._watchdog_task is not None:
                self._watchdog_task.cancel()
                await asyncio.gather(self._watchdog_task, return_exceptions=True)
                self._watchdog_task = None
        return UserWsMessage(
            receive_seq=self._receive_seq,
            received_at=datetime.now(timezone.utc),
            frame=frame,
            artifact_hash=user_ws_frame_artifact_hash(frame),
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


__all__ = [
    "UserWsDriver", "UserWsPolicy", "UserWsMessage",
    "PING_TEXT", "PONG_TEXT", "SUBSCRIBE_TEMPLATE",
]
