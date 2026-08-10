"""
WP-01A-02 Outbox envelope 合同 —— 纯单测（不连数据库/Redis）。

校验 topic/schema/idempotency/release/deadline/priority/payload；canonical 序列化稳定；
原始 secret、任意 Python object 和 float 拒绝；event_id 由 canonical 内容推导（确定性）。
"""

import hashlib
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.outbox.contracts import (
    EVENT_ID_HEX,
    IDEMPOTENCY_KEY_MAX,
    OutboxEnvelope,
    OutboxValidationError,
    create_envelope,
)


def _env(**kw):
    base = dict(
        topic="market.resolved",
        schema_version=1,
        aggregate_type="market",
        aggregate_id="m-1",
        idempotency_key="idem-1",
        priority=128,
        payload={"outcome": "yes"},
    )
    base.update(kw)
    return create_envelope(**base)


def test_happy_path_event_id_deterministic_64hex():
    a = _env()
    b = _env()
    assert a.event_id == b.event_id
    assert len(a.event_id) == EVENT_ID_HEX
    assert a.event_id == hashlib.sha256(a.canonical_bytes()).hexdigest()
    assert a.validate() is None


def test_different_content_different_event_id():
    assert _env(payload={"outcome": "yes"}).event_id != _env(payload={"outcome": "no"}).event_id
    assert _env(idempotency_key="k1").event_id != _env(idempotency_key="k2").event_id


def test_invalid_topic_schema_aggregate():
    for kw, reason in [
        ({"topic": ""}, "outbox_topic_invalid"),
        ({"topic": "  "}, "outbox_topic_invalid"),
        ({"schema_version": 0}, "outbox_schema_version_invalid"),
        ({"schema_version": -1}, "outbox_schema_version_invalid"),
        ({"schema_version": True}, "outbox_schema_version_invalid"),
        ({"aggregate_type": ""}, "outbox_aggregate_type_invalid"),
        ({"aggregate_id": ""}, "outbox_aggregate_id_invalid"),
    ]:
        with pytest.raises(OutboxValidationError) as ei:
            _env(**kw)
        assert ei.value.reason_code == reason


def test_idempotency_key_rules():
    with pytest.raises(OutboxValidationError, match="outbox_idempotency_key_invalid"):
        _env(idempotency_key="")
    with pytest.raises(OutboxValidationError, match="outbox_idempotency_key_too_long"):
        _env(idempotency_key="x" * (IDEMPOTENCY_KEY_MAX + 1))


def test_priority_range():
    for bad in (-1, 256, True):
        with pytest.raises(OutboxValidationError, match="outbox_priority_out_of_range"):
            _env(priority=bad)
    assert _env(priority=0).priority == 0
    assert _env(priority=255).priority == 255


def test_payload_xor_artifact_required():
    with pytest.raises(OutboxValidationError, match="outbox_payload_xor_artifact_required"):
        _env(payload=None, artifact_ref=None)
    with pytest.raises(OutboxValidationError, match="outbox_payload_xor_artifact_required"):
        _env(payload={"a": 1}, artifact_ref="a" * 64)


def test_payload_must_be_object():
    with pytest.raises(OutboxValidationError, match="outbox_payload_not_object"):
        _env(payload=[1, 2, 3])


def test_float_rejected_recursively():
    for payload in ({"p": 1.5}, {"nested": [{"x": 0.1}]}, {"l": [1.0]}):
        with pytest.raises(OutboxValidationError, match="outbox_payload_float"):
            _env(payload=payload)


def test_non_json_serializable_rejected():
    class Weird:
        pass

    with pytest.raises(OutboxValidationError, match="outbox_payload_not_json_serializable"):
        _env(payload={"obj": Weird()})


def test_artifact_ref_must_be_64hex():
    with pytest.raises(OutboxValidationError, match="outbox_artifact_ref_invalid"):
        _env(payload=None, artifact_ref="not-a-hash")
    with pytest.raises(OutboxValidationError, match="outbox_artifact_ref_invalid"):
        _env(payload=None, artifact_ref="x" * 63)
    with pytest.raises(OutboxValidationError, match="outbox_artifact_ref_invalid"):
        _env(payload=None, artifact_ref="A" * 64)
    env = _env(payload=None, artifact_ref="a" * 64)
    assert env.artifact_ref == "a" * 64


def test_release_and_deadline_validation():
    with pytest.raises(OutboxValidationError, match="outbox_release_manifest_invalid"):
        _env(release_manifest_id="abc")
    with pytest.raises(OutboxValidationError, match="outbox_deadline_invalid"):
        _env(deadline="2026-01-01")
    with pytest.raises(OutboxValidationError, match="outbox_deadline_invalid"):
        _env(deadline=datetime(2026, 1, 1))
    with pytest.raises(OutboxValidationError, match="outbox_available_at_invalid"):
        _env(available_at=datetime(2026, 1, 1))
    with pytest.raises(OutboxValidationError, match="outbox_release_manifest_invalid"):
        _env(release_manifest_id=True)
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert _env(deadline=dt, release_manifest_id=7).deadline == dt


def test_canonical_serialization_stable_and_tz_normalized():
    from datetime import timedelta
    from datetime import timezone as tz

    # 同一时刻：00:00 UTC == 02:00 +02:00
    a = _env(deadline=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc))
    b = _env(deadline=datetime(2026, 1, 1, 2, 0, tzinfo=tz(timedelta(hours=2))))
    assert a.canonical_bytes() == b.canonical_bytes()


def test_envelope_immutable():
    env = _env()
    with pytest.raises(Exception):
        env.payload = {"mutated": True}  # frozen dataclass


def test_validate_returns_none_on_valid():
    env = _env()
    assert env.validate() is None


def test_event_id_excluded_from_canonical():
    """event_id 推导不能自引用：canonical 不含 event_id 字段。"""
    env = _env()
    parts = env.canonical_parts()
    assert "event_id" not in parts


def test_event_id_is_bound_to_canonical_content():
    env = _env()
    with pytest.raises(OutboxValidationError, match="outbox_event_id_hash_mismatch"):
        replace(env, event_id="0" * 64).validate()


@pytest.mark.parametrize(
    "payload",
    [
        {"password": "MARK"},
        {"api_key": "MARK"},
        {"nested": {"authorization": "Bearer MARK"}},
        {"value": "Bearer TOPSECRET"},
        {"value": "https://alice:hunter2@example.test/path"},
        {"value": "-----BEGIN PRIVATE KEY-----MARK"},
    ],
)
def test_plaintext_credentials_rejected(payload):
    with pytest.raises(OutboxValidationError, match="outbox_payload_secret"):
        _env(payload=payload)


def test_business_token_id_is_not_misclassified_as_secret():
    assert _env(payload={"token_id": "123456"}).payload == {"token_id": "123456"}
