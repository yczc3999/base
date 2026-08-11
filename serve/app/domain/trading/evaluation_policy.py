"""Deployment-owned frozen P3 evaluation policy.

The runtime must not depend on ``tests/`` being present in the deployed image.  The
JSON resource beside this module is the immutable, content-addressed copy accepted by
WP-04.  Every read verifies its self hash before exposing a policy subtree.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domain.trading.hashing import canonical_hash

_SPEC_PATH = Path(__file__).with_name("p_evaluation_spec_v1.json")
_SCHEMA_VERSION = "p3/evaluation-spec/v1"


@lru_cache(maxsize=1)
def evaluation_spec() -> dict[str, Any]:
    with _SPEC_PATH.open(encoding="utf-8") as stream:
        spec = json.load(stream)
    if spec.get("schema_version") != _SCHEMA_VERSION:
        raise RuntimeError("p3_evaluation_spec_schema_mismatch")
    stored = spec.get("content_hash")
    material = dict(spec)
    material.pop("content_hash", None)
    if not isinstance(stored, str) or canonical_hash(material) != stored:
        raise RuntimeError("p3_evaluation_spec_hash_mismatch")
    return spec


def evaluation_policy(name: str) -> dict[str, Any]:
    value = evaluation_spec().get(name)
    if not isinstance(value, dict):
        raise RuntimeError(f"p3_evaluation_policy_missing:{name}")
    return value


def evaluation_policy_hash(name: str) -> str:
    return canonical_hash(evaluation_policy(name))
