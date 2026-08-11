"""P5 execution-readiness + P-stability 共享 fixture helper（WP-05 Checkpoint A）。

- ``load_p5_spec`` / ``p5_spec_sha256``：读取冻结的 ``p_execution_readiness_spec_v1.json``
  （解析 dict / 原始文件字节 SHA-256）。
- ``frozen_spec``：返回 spec dict 并断言 ``content_hash`` 与「删除 content_hash 后」的
  canonical hash 自洽（与 P3 spec 口径一致）。
- ``spec_policy_hashes``：十组 policy 子对象（sdk/type3/heartbeat/reconcile/order_transition/
  unknown_retry/reservation/vault_aad/kill_switch/fake_only）的 canonical hash。
- ``load_scenario`` / ``scenario_sha256`` / ``frozen_scenario``：六个 scenario fixture 的
  加载 / 原始字节 SHA-256 / content_hash 自洽校验。
- ``sdk_golden_hash``：返回 sdk_source_manifest 中记录的 SDK golden hash（占位值）。
- ``canonical_hash``：re-export（供下游复用）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.domain.trading.hashing import canonical_bytes, canonical_hash  # noqa: F401  (re-export)

FIXTURE_DIR = Path(__file__).resolve().parent
P5_SPEC_PATH = FIXTURE_DIR / "p_execution_readiness_spec_v1.json"

SCENARIO_FILES: dict[str, str] = {
    "sdk_source": "sdk_source_manifest_v1.json",
    "heartbeat_drift": "official_heartbeat_drift_v1.json",
    "event_log": "stability_event_log_v1.json",
    "snapshot": "stability_snapshot_v1.json",
    "clob_golden": "private_clob_golden_v1.json",
    "user_ws_reconcile": "user_ws_reconcile_v1.json",
}

# spec_policy_hashes() 输出的键 → spec 顶层 policy 子对象键
POLICY_HASH_KEYS: dict[str, str] = {
    "sdk_hash": "sdk",
    "type3_hash": "type3_identity",
    "heartbeat_hash": "heartbeats",
    "reconcile_hash": "user_ws_rest_reconcile",
    "order_transition_hash": "order_transition_table",
    "unknown_retry_hash": "unknown_retry_matrix",
    "reservation_hash": "reservation",
    "vault_aad_hash": "vault_aad",
    "kill_switch_hash": "kill_switch_matrix",
    "fake_only_hash": "fake_only",
}


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _file_sha256(path: Path) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _without_content_hash(obj: dict) -> dict:
    stripped = dict(obj)
    stripped.pop("content_hash", None)
    return stripped


def load_p5_spec() -> dict:
    """读取冻结 execution-readiness spec（含 content_hash 字段）。"""
    return _load_json(P5_SPEC_PATH)


def p5_spec_sha256() -> str:
    """spec 文件原始字节的 SHA-256（manifest 记录口径）。"""
    return _file_sha256(P5_SPEC_PATH)


def frozen_spec() -> dict:
    """返回 spec dict，断言 content_hash 自洽。"""
    spec = load_p5_spec()
    stored = spec.get("content_hash")
    assert isinstance(stored, str) and len(stored) == 64, (
        "p5 spec content_hash missing/malformed"
    )
    recomputed = canonical_hash(_without_content_hash(spec))
    assert recomputed == stored, (
        f"p5 spec content_hash mismatch: file={stored} recomputed={recomputed}"
    )
    return spec


def spec_policy_hashes() -> dict[str, str]:
    """十组 policy 子对象的 canonical hash。"""
    spec = load_p5_spec()
    return {
        key: canonical_hash(spec[policy_key])
        for key, policy_key in POLICY_HASH_KEYS.items()
    }


def load_scenario(name: str) -> dict:
    """加载指定 scenario fixture。"""
    if name not in SCENARIO_FILES:
        raise KeyError(f"unknown p5 scenario: {name!r}; expected {sorted(SCENARIO_FILES)}")
    return _load_json(FIXTURE_DIR / SCENARIO_FILES[name])


def scenario_sha256(name: str) -> str:
    """指定 scenario fixture 原始字节 SHA-256。"""
    if name not in SCENARIO_FILES:
        raise KeyError(f"unknown p5 scenario: {name!r}")
    return _file_sha256(FIXTURE_DIR / SCENARIO_FILES[name])


def frozen_scenario(name: str) -> dict:
    """返回 scenario dict，断言 content_hash 自洽。"""
    scenario = load_scenario(name)
    stored = scenario.get("content_hash")
    assert isinstance(stored, str) and len(stored) == 64, (
        f"{name} content_hash missing/malformed"
    )
    recomputed = canonical_hash(_without_content_hash(scenario))
    assert recomputed == stored, (
        f"{name} content_hash mismatch: file={stored} recomputed={recomputed}"
    )
    return scenario


def sdk_golden_hash() -> str:
    """sdk_source_manifest 记录的 SDK golden hash（占位值，与 spec 一致）。"""
    manifest = load_scenario("sdk_source")
    golden = manifest["sdk"]["golden_sha256"]
    assert isinstance(golden, str) and len(golden) == 64, "sdk golden_sha256 malformed"
    return golden
