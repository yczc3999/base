"""P3 learning 共享 fixture helper（WP-04 Checkpoint A）。

- ``load_p3_spec`` / ``p3_spec_sha256``：读取冻结的 ``p_evaluation_spec_v1.json``
  （解析 dict / 原始文件字节 SHA-256）。
- ``frozen_spec``：返回 spec dict 并断言 ``content_hash`` 与「删除 content_hash 后」的
  canonical hash 自洽（manifest 记录的是删除哈希行后的再哈希，与 WP-03 口径一致）。
- ``spec_policy_hashes``：七个 policy 子对象的 canonical hash，供 P_EVALUATION_SPEC_MANIFEST 使用。
- ``load_scenario`` / ``scenario_sha256`` / ``frozen_scenario``：六个 scenario fixture 的
  加载 / 原始字节 SHA-256 / content_hash 自洽校验。
- ``canonical_hash``：re-export（供下游复用）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.domain.trading.hashing import canonical_bytes, canonical_hash  # noqa: F401  (re-export)

FIXTURE_DIR = Path(__file__).resolve().parent
P3_SPEC_PATH = FIXTURE_DIR / "p_evaluation_spec_v1.json"

SCENARIO_FILES: dict[str, str] = {
    "bernoulli": "bernoulli.json",
    "multiclass": "multiclass.json",
    "mean_only": "mean_only.json",
    "label_conflict": "label_conflict.json",
    "reject_audit": "reject_audit.json",
    "holdout_tamper": "holdout_tamper.json",
}

# spec_policy_hashes() 输出的键 → spec 顶层 policy 子对象键
POLICY_HASH_KEYS: dict[str, str] = {
    "label_policy_hash": "label_policy",
    "target_policy_hash": "target_canonicalization",
    "baseline_policy_hash": "baseline_convention",
    "split_policy_hash": "split_policy",
    "bootstrap_policy_hash": "bootstrap_policy",
    "metric_policy_hash": "metric_policy",
    "promotion_policy_hash": "promotion_policy",
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


def load_p3_spec() -> dict:
    """读取冻结评价 spec（含 content_hash 字段）。"""
    return _load_json(P3_SPEC_PATH)


def p3_spec_sha256() -> str:
    """spec 文件原始字节的 SHA-256（manifest 记录口径）。"""
    return _file_sha256(P3_SPEC_PATH)


def frozen_spec() -> dict:
    """返回 spec dict，断言 content_hash 自洽。"""
    spec = load_p3_spec()
    stored = spec.get("content_hash")
    assert isinstance(stored, str) and len(stored) == 64, (
        "p3 spec content_hash missing/malformed"
    )
    recomputed = canonical_hash(_without_content_hash(spec))
    assert recomputed == stored, (
        f"p3 spec content_hash mismatch: file={stored} recomputed={recomputed}"
    )
    return spec


def spec_policy_hashes() -> dict[str, str]:
    """七个 policy 子对象的 canonical hash，供 P_EVALUATION_SPEC_MANIFEST。"""
    spec = load_p3_spec()
    return {
        key: canonical_hash(spec[policy_key])
        for key, policy_key in POLICY_HASH_KEYS.items()
    }


def load_scenario(name: str) -> dict:
    """加载指定 scenario fixture。"""
    if name not in SCENARIO_FILES:
        raise KeyError(f"unknown p3 scenario: {name!r}; expected {sorted(SCENARIO_FILES)}")
    return _load_json(FIXTURE_DIR / SCENARIO_FILES[name])


def scenario_sha256(name: str) -> str:
    """指定 scenario fixture 原始字节 SHA-256。"""
    if name not in SCENARIO_FILES:
        raise KeyError(f"unknown p3 scenario: {name!r}")
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
