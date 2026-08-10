"""Outbox envelope 合同（WP-01A-02，Checkpoint D）。

versioned envelope 必须校验 topic/schema/idempotency/release/deadline/priority/payload；
canonical 序列化稳定；原始 secret、任意 Python object 和 float 拒绝（金额/价格必须走
Decimal/base-unit，禁止 float）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.db.uow import UnitOfWork

PRIORITY_MIN = 0
PRIORITY_MAX = 255
EVENT_ID_HEX = 64
IDEMPOTENCY_KEY_MAX = 255
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_URL_USERINFO_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@")
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "password",
        "passphrase",
        "privatekey",
        "secret",
        "setcookie",
        "signature",
        "token",
        "apikey",
    }
)


class OutboxValidationError(ValueError):
    """固定 reason code；不含 DSN / secret / Provider message。"""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _reject_float(value: Any, path: str) -> None:
    """递归拒绝 float；允许 int/Decimal/str/bool/None/dict/list。"""
    if isinstance(value, float):
        raise OutboxValidationError(f"outbox_payload_float_at_{path or 'root'}")
    if isinstance(value, dict):
        for k, v in value.items():
            _reject_float(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _reject_float(v, f"{path}[{i}]")


def _normalized_key(value: object) -> str:
    return re.sub(r"[\s_.-]+", "", str(value)).lower()


def _reject_secret(value: Any, path: str = "") -> None:
    """拒绝明文凭据；业务 ``token_id`` 等非精确敏感 key 不误伤。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if _normalized_key(key) in _SENSITIVE_KEYS:
                raise OutboxValidationError(
                    f"outbox_payload_secret_at_{path or 'root'}"
                )
            _reject_secret(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_secret(item, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if (
            "-----begin " in lowered and "private key-----" in lowered
        ) or _BEARER_RE.search(value) or _URL_USERINFO_RE.search(value):
            raise OutboxValidationError(
                f"outbox_payload_secret_at_{path or 'root'}"
            )


def _assert_json_serializable(value: Any) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise OutboxValidationError("outbox_payload_not_json_serializable") from e


@dataclass(frozen=True)
class OutboxEnvelope:
    """不可变 outbox 事件 envelope。``event_id`` 由 canonical 内容推导。"""

    event_id: str
    topic: str
    schema_version: int
    aggregate_type: str
    aggregate_id: str
    idempotency_key: str
    priority: int
    payload: dict | None
    artifact_ref: str | None
    release_manifest_id: int | None = None
    deadline: datetime | None = None
    available_at: datetime | None = None

    # ---- 校验 ----

    def validate(self, *, check_event_id: bool = True) -> None:
        if check_event_id:
            if not isinstance(self.event_id, str) or not _SHA256_RE.fullmatch(self.event_id):
                raise OutboxValidationError("outbox_event_id_invalid")
            if self.event_id != self.canonical_sha256():
                raise OutboxValidationError("outbox_event_id_hash_mismatch")
        if not isinstance(self.topic, str) or not self.topic.strip():
            raise OutboxValidationError("outbox_topic_invalid")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version <= 0
        ):
            raise OutboxValidationError("outbox_schema_version_invalid")
        if not isinstance(self.aggregate_type, str) or not self.aggregate_type.strip():
            raise OutboxValidationError("outbox_aggregate_type_invalid")
        if not isinstance(self.aggregate_id, str) or not self.aggregate_id.strip():
            raise OutboxValidationError("outbox_aggregate_id_invalid")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise OutboxValidationError("outbox_idempotency_key_invalid")
        if len(self.idempotency_key) > IDEMPOTENCY_KEY_MAX:
            raise OutboxValidationError("outbox_idempotency_key_too_long")
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or not (PRIORITY_MIN <= self.priority <= PRIORITY_MAX)
        ):
            raise OutboxValidationError("outbox_priority_out_of_range")
        has_payload = self.payload is not None
        has_artifact = self.artifact_ref is not None
        if has_payload == has_artifact:
            raise OutboxValidationError("outbox_payload_xor_artifact_required")
        if has_payload:
            if not isinstance(self.payload, dict):
                raise OutboxValidationError("outbox_payload_not_object")
            _assert_json_serializable(self.payload)
            _reject_float(self.payload, "")
            _reject_secret(self.payload)
        if has_artifact:
            if not isinstance(self.artifact_ref, str) or not _SHA256_RE.fullmatch(self.artifact_ref):
                raise OutboxValidationError("outbox_artifact_ref_invalid")
        if self.release_manifest_id is not None and (
            isinstance(self.release_manifest_id, bool)
            or not isinstance(self.release_manifest_id, int)
            or self.release_manifest_id <= 0
        ):
            raise OutboxValidationError("outbox_release_manifest_invalid")
        for field_name, value in (
            ("deadline", self.deadline),
            ("available_at", self.available_at),
        ):
            if value is not None and (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise OutboxValidationError(f"outbox_{field_name}_invalid")

    # ---- canonical 序列化（稳定、去时区歧义）----

    def canonical_parts(self) -> dict:
        def _iso(dt: datetime | None) -> str | None:
            if dt is None:
                return None
            return dt.astimezone(timezone.utc).isoformat()

        return {
            "topic": self.topic,
            "schema_version": self.schema_version,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "idempotency_key": self.idempotency_key,
            "priority": self.priority,
            "payload": self.payload,
            "artifact_ref": self.artifact_ref,
            "release_manifest_id": self.release_manifest_id,
            "deadline": _iso(self.deadline),
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.canonical_parts(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def create_envelope(
    *,
    topic: str,
    schema_version: int,
    aggregate_type: str,
    aggregate_id: str,
    idempotency_key: str,
    priority: int = 128,
    payload: dict | None = None,
    artifact_ref: str | None = None,
    release_manifest_id: int | None = None,
    deadline: datetime | None = None,
    available_at: datetime | None = None,
) -> OutboxEnvelope:
    """构造并校验 envelope；event_id 由 canonical 内容推导（确定性）。"""
    env = OutboxEnvelope(
        event_id="",
        topic=topic,
        schema_version=schema_version,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        idempotency_key=idempotency_key,
        priority=priority,
        payload=payload,
        artifact_ref=artifact_ref,
        release_manifest_id=release_manifest_id,
        deadline=deadline,
        available_at=available_at,
    )
    env.validate(check_event_id=False)
    result = replace(env, event_id=env.canonical_sha256())
    result.validate()
    return result


class OutboxHandler(Protocol):
    """DB-only handler；业务写与 completion 必须共用传入 UoW。"""

    handler_name: str

    async def handle(
        self,
        envelope: OutboxEnvelope,
        uow: "UnitOfWork",
        fencing_token: int,
    ) -> None: ...
