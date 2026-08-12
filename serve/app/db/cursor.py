"""Admin keyset cursor v1（WP-07A Checkpoint A）。

Cursor 是 HMAC 签名 opaque token，payload 固定字段：

```text
version
endpoint
sort_time     （UTC ISO-8601；列表首屏冻结 as_of=statement_timestamp()）
id            （BIGINT 十进制字符串，禁止 JS number）
direction     （asc|desc，与 endpoint 固定方向一致）
filter_hash   （64-hex；H(endpoint + query_version + canonical_filters + direction)）
as_of         （UTC ISO-8601；首屏冻结，后续页复用）
```

- 注入式 codec；生产从既有服务端 ``APP_KEY`` 通过独立 context label 派生 key
  （HMAC-SHA256(APP_KEY, label)），不新增浏览器可见 secret，不在 cursor 中保存
  key id、原始 filter 或身份信息。
- ``decode`` 严格 fail-closed：tamper / endpoint / filter / direction mismatch、
  非 UTC、超长 token、坏签名一律 ``CursorError``（Controller → 400）。
- limit 不进 cursor 身份：改变 limit 不改变 snapshot/filter。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone

CURSOR_VERSION = "v1"
_CURSOR_CONTEXT_LABEL = "pm-admin-cursor/v1"
_MAX_TOKEN_BYTES = 2048


class CursorError(ValueError):
    """cursor 无效；Controller 统一映射 400。"""


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc_iso(value: str) -> datetime:
    """严格解析 UTC ISO-8601（只接受 Z 后缀的 UTC 时间）。"""
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CursorError("cursor_time_not_utc")
    try:
        dt = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CursorError("cursor_time_not_utc") from exc
    if dt.tzinfo is None:
        raise CursorError("cursor_time_not_utc")
    return dt


def derive_key(app_key: str, *, context_label: str = _CURSOR_CONTEXT_LABEL) -> bytes:
    """从服务端 APP_KEY 通过独立 context label 派生 cursor signing key。

    不新增浏览器可见 secret，不在 cursor 中保存 key id。
    """
    if not app_key:
        raise CursorError("cursor_app_key_missing")
    return hmac.new(
        app_key.encode("utf-8"), context_label.encode("utf-8"), hashlib.sha256
    ).digest()


@dataclass(frozen=True)
class CursorPayload:
    """解码后的 cursor payload（全部字段验证后）。"""

    version: str
    endpoint: str
    sort_time: datetime
    id: str
    direction: str
    filter_hash: str
    as_of: datetime


def canonical_filter_hash(
    *, endpoint: str, query_version: str, filters: dict, direction: str,
) -> str:
    """``H(endpoint + query_version + canonical_filters + direction)``。

    filters 为显式 allowlist 后的已规范化 filter dict；canonical 序列化
    （sort_keys + separators），保证跨调用稳定。
    """
    canonical_filters = json.dumps(filters, sort_keys=True, separators=(",", ":"))
    raw = f"{endpoint}{query_version}{canonical_filters}{direction}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CursorCodec:
    """注入式 keyset cursor codec（无状态；encode/decode 不触 DB/网络）。"""

    def __init__(self, secret: bytes | str) -> None:
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        if not secret:
            raise CursorError("cursor_secret_missing")
        self._secret = bytes(secret)

    def _mac(self, payload_b64: str) -> str:
        return hmac.new(self._secret, payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()

    def encode(
        self,
        *,
        endpoint: str,
        sort_time: datetime,
        id: int | str,
        direction: str,
        filter_hash: str,
        as_of: datetime,
    ) -> str:
        """encode opaque token（不含任何 secret/raw filter）。"""
        if direction not in ("asc", "desc"):
            raise CursorError("cursor_direction_invalid")
        if not isinstance(filter_hash, str) or len(filter_hash) != 64:
            raise CursorError("cursor_filter_hash_invalid")
        if isinstance(id, bool):
            raise CursorError("cursor_id_invalid")
        id_str = str(int(id))
        body = {
            "v": CURSOR_VERSION,
            "ep": endpoint,
            "st": _utc_iso(sort_time),
            "id": id_str,
            "dir": direction,
            "fh": filter_hash,
            "as_of": _utc_iso(as_of),
        }
        payload = json.dumps(body, separators=(",", ":"), sort_keys=True)
        payload_b64 = __import__("base64").urlsafe_b64encode(
            payload.encode("utf-8")
        ).decode().rstrip("=")
        return f"{payload_b64}.{self._mac(payload_b64)}"

    def decode(
        self,
        token: str,
        *,
        endpoint: str,
        direction: str,
        filter_hash: str,
    ) -> CursorPayload:
        """decode + 严格校验（endpoint/filter/direction/tamper/超长/坏签名 fail-closed）。"""
        if not isinstance(token, str) or not token:
            raise CursorError("cursor_missing")
        if len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
            raise CursorError("cursor_too_long")
        if "." not in token:
            raise CursorError("cursor_malformed")
        payload_b64, mac = token.rsplit(".", 1)
        expected_mac = self._mac(payload_b64)
        if not hmac.compare_digest(expected_mac, mac):
            raise CursorError("cursor_tampered")
        try:
            padding = "=" * (-len(payload_b64) % 4)
            payload_bytes = __import__("base64").urlsafe_b64decode(payload_b64 + padding)
            body = json.loads(payload_bytes.decode("utf-8"))
        except Exception as exc:
            raise CursorError("cursor_malformed") from exc
        if not isinstance(body, dict):
            raise CursorError("cursor_malformed")
        if body.get("v") != CURSOR_VERSION:
            raise CursorError("cursor_version_invalid")
        if body.get("ep") != endpoint:
            raise CursorError("cursor_endpoint_mismatch")
        if body.get("dir") != direction:
            raise CursorError("cursor_direction_mismatch")
        if body.get("fh") != filter_hash:
            raise CursorError("cursor_filter_mismatch")
        try:
            sort_time = parse_utc_iso(body["st"])
            as_of = parse_utc_iso(body["as_of"])
        except KeyError as exc:
            raise CursorError("cursor_malformed") from exc
        id_str = body.get("id")
        if not isinstance(id_str, str) or not id_str.isdigit():
            raise CursorError("cursor_id_invalid")
        return CursorPayload(
            version=str(body["v"]),
            endpoint=str(body["ep"]),
            sort_time=sort_time,
            id=id_str,
            direction=str(body["dir"]),
            filter_hash=str(body["fh"]),
            as_of=as_of,
        )
