"""Shadow 阶段冻结 policy 单一事实源（WP-07C）。

run_r0 / run_g1 / run_g5b 等会校验传入 policy 的 content hash 与 cohort 冻结的
policy_hashes 一致（否则抛 freeze_mismatch）。若 seed 与 pipeline 各写一份硬编码，
任何一处改动都会让 hash 漂移、链断裂。

因此这里集中定义 shadow 阶段用到的冻结 policy，seed（算冻结 hash）与 pipeline
（传入 policy）共用同一对象，保证 hash 恒等。

- ``SHADOW_R0_POLICY`` / ``SHADOW_AUDIT_POLICY``：R0 筛选 + reject-audit 抽样。
- ``SHADOW_COVERAGE_POLICY``：G5B 证据覆盖（AI 段用，当前 ai_gated，占位定义）。
- ``build_shadow_policy_hashes()``：r0/reject_audit/evidence_coverage 用真实内容
  hash，其余 7 类 policy（当前阶段未消费）用稳定占位 64-hex（与测试模板同款口径）。
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.trading.hashing import canonical_hash
from app.logics.trading.screening import AUDIT_ALGORITHM_VERSION
from app.repositories.trading.cohort import REQUIRED_COHORT_POLICIES
from app.schemas.trading.evidence import EvidenceCoveragePolicyInput
from app.schemas.trading.workflow import RejectAuditPolicyInput, R0PolicyInput

SHADOW_R0_POLICY = R0PolicyInput(
    policy_version=1,
    minimum_rule_completeness=Decimal("0.75"),
    maximum_research_cost=Decimal("100"),
    require_two_sided_quote=True,
    defer_recheck_condition="book_or_rules_change",
    reject_recheck_condition="capacity_or_cost_change",
)

SHADOW_AUDIT_POLICY = RejectAuditPolicyInput(
    policy_version=1,
    algorithm_version=AUDIT_ALGORITHM_VERSION,  # "hmac-sha256-u64/v1"
    salt="shadow/reject-audit/v1",
    reject_probability=Decimal("1"),
    defer_probability=Decimal("1"),
)

SHADOW_COVERAGE_POLICY = EvidenceCoveragePolicyInput(
    policy_version=1,
    material_branches=["w0", "w1"],
    allowed_source_types=["web"],
    contamination_policy={"kind": "hard_veto"},
    staleness_policy={"max_age": "48h"},
    independence_requirement={"n": 2},
    widening_algorithm="extreme-points/v1",
    missing_branch_policy="widen",
    content={"meta": {"kind": "shadow"}},
)


def build_shadow_policy_hashes() -> dict[str, str]:
    """cohort 冻结的 policy_hashes：r0/reject_audit/evidence_coverage 用真 hash。"""
    hashes = {
        name: f"{index:x}" * 64
        for index, name in enumerate(REQUIRED_COHORT_POLICIES, start=1)
    }
    hashes["r0"] = canonical_hash(SHADOW_R0_POLICY.model_dump(mode="json"))
    hashes["reject_audit"] = canonical_hash(SHADOW_AUDIT_POLICY.model_dump(mode="json"))
    hashes["evidence_coverage"] = canonical_hash(
        SHADOW_COVERAGE_POLICY.model_dump(mode="json")
    )
    return hashes
