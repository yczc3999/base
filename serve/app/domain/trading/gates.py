"""Pure validation of a Gate's frozen cohort policy/release binding."""

from __future__ import annotations

from typing import Any


def assert_frozen_gate_binding(
    lineage: dict[str, Any], *, policy_type: str, policy_hash: str,
    version_manifest_id: int
) -> None:
    """Reject caller-selected Gate versions not frozen by the opportunity cohort.

    WP-01C stores the exact per-policy hashes on the cohort.  A Gate may select one
    of those frozen policies, but it may not supply an unrelated hash or release.
    """

    if lineage.get("cohort_release_manifest_id") != version_manifest_id:
        raise ValueError("gate_release_binding_mismatch")
    policies = lineage.get("cohort_policy_hashes")
    if (
        not isinstance(policies, dict)
        or not isinstance(policy_type, str)
        or not policy_type
        or not isinstance(policy_hash, str)
        or len(policy_hash) != 64
        or any(char not in "0123456789abcdef" for char in policy_hash)
        or policies.get(policy_type) != policy_hash
    ):
        raise ValueError(f"gate_policy_binding_mismatch:{policy_type}")
