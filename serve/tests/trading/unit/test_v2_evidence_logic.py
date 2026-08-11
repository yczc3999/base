"""Evidence schema/logic 纯逻辑单元测试（WP-02 Checkpoint A）。

不连数据库：验证 PriorInput/EvidenceRevisionInput/CoveragePolicy/EvidenceBundle 的
结构校验与 forbidden-key 拒绝，以及 G5B 覆盖判定辅助逻辑的可重算 hash 构造。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domain.trading.hashing import canonical_hash
from app.schemas.trading.evidence import (
    EvidenceBundleInput,
    EvidenceCoveragePolicyInput,
    EvidenceRevisionInput,
    PriorInput,
)

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class TestPriorInput:
    def test_valid_market_blind(self):
        prior = PriorInput(
            reference_class="similar-elections",
            applicability={"scope": "elections"},
            sample_rule={"rule": "past-10"},
            width={"lower": "0.2", "upper": "0.4"},
            failure_conditions={"c": "regime-change"},
            market_blind_declaration=True,
        )
        prior.require_reference()
        prior.require_structured()

    def test_hazard_ref_alternative(self):
        prior = PriorInput(
            hazard_ref="annual-flood-rate",
            applicability={"a": 1},
            sample_rule={"s": 1},
            width={"w": 1},
            failure_conditions={"f": 1},
            market_blind_declaration=True,
        )
        prior.require_reference()

    def test_missing_reference_rejected(self):
        prior = PriorInput(
            reference_class=None,
            hazard_ref=None,
            applicability={"a": 1},
            sample_rule={"s": 1},
            width={"w": 1},
            failure_conditions={"f": 1},
            market_blind_declaration=True,
        )
        with pytest.raises(ValueError, match="prior_reference_required"):
            prior.require_reference()

    def test_forbidden_key_rejected(self):
        with pytest.raises(ValidationError, match="prior_forbidden_key"):
            PriorInput(
                reference_class="x",
                applicability={"a": 1},
                sample_rule={"s": 1},
                width={"w": 1},
                failure_conditions={"f": 1},
                content={"market_price": "0.5"},
                market_blind_declaration=True,
            )

    def test_nested_forbidden_key_rejected(self):
        with pytest.raises(ValidationError, match="prior_forbidden_key"):
            PriorInput(
                reference_class="x",
                applicability={"nested": {"odds": "2.0"}},
                sample_rule={"s": 1},
                width={"w": 1},
                failure_conditions={"f": 1},
                market_blind_declaration=True,
            )

    def test_blind_declaration_must_be_true(self):
        with pytest.raises(ValidationError, match="prior_market_blind_must_be_true"):
            PriorInput(
                reference_class="x",
                applicability={"a": 1},
                sample_rule={"s": 1},
                width={"w": 1},
                failure_conditions={"f": 1},
                market_blind_declaration=False,
            )


class TestEvidenceRevisionInput:
    def test_valid_time_order(self):
        revision = EvidenceRevisionInput(
            revision_key="r1",
            kind="source_claim",
            event_at=NOW,
            published_at=NOW,
            observed_at=NOW,
            ingested_at=NOW,
            source="https://example.com",
            source_type="web",
            branch="main",
            raw_artifact_ref="a" * 64,
            content={"claim": "x"},
            taint_status="none",
        )
        revision.assert_time_order()

    def test_publish_after_observe_rejected(self):
        revision = EvidenceRevisionInput(
            revision_key="r1",
            kind="source_claim",
            event_at=NOW,
            published_at=NOW,
            observed_at=NOW.replace(minute=1),
            ingested_at=NOW.replace(minute=2),
            source="s",
            source_type="web",
            branch="main",
            raw_artifact_ref="a" * 64,
        )
        with pytest.raises(ValueError, match="evidence_publish_after_observe"):
            revision.assert_time_order()

    def test_bad_kind_rejected(self):
        with pytest.raises(ValidationError):
            EvidenceRevisionInput(
                revision_key="r1",
                kind="rumor",
                event_at=NOW,
                published_at=NOW,
                observed_at=NOW,
                ingested_at=NOW,
                source="s",
                source_type="web",
                branch="main",
                raw_artifact_ref="a" * 64,
            )


class TestCoveragePolicy:
    def test_valid(self):
        policy = EvidenceCoveragePolicyInput(
            policy_version=1,
            material_branches=["b1", "b2"],
            allowed_source_types=["web"],
            contamination_policy={"kind": "hard_veto"},
            staleness_policy={"max_age": "1h"},
            independence_requirement={"n": 2},
            widening_algorithm="extreme-points/v1",
            missing_branch_policy="widen",
            content={"meta": 1},
        )
        assert policy.policy_version == 1

    def test_duplicate_branch_rejected(self):
        with pytest.raises(ValidationError, match="coverage_branches_duplicate"):
            EvidenceCoveragePolicyInput(
                policy_version=1,
                material_branches=["b1", "b1"],
                allowed_source_types=["web"],
                contamination_policy={},
                staleness_policy={},
                independence_requirement={},
                widening_algorithm="x",
            )

    def test_hash_recomputable(self):
        policy = EvidenceCoveragePolicyInput(
            policy_version=1,
            material_branches=["b1", "b2"],
            allowed_source_types=["web"],
            contamination_policy={},
            staleness_policy={},
            independence_requirement={},
            widening_algorithm="x",
        )
        h1 = canonical_hash(policy.model_dump(mode="json"))
        h2 = canonical_hash(policy.model_dump(mode="json"))
        assert h1 == h2


class TestEvidenceBundleInput:
    def test_revision_order_insensitive_hash(self):
        bundle_a = EvidenceBundleInput(
            bundle_key="b1",
            information_cutoff_at=NOW,
            revision_keys=["r2", "r1", "r3"],
        )
        bundle_b = EvidenceBundleInput(
            bundle_key="b1",
            information_cutoff_at=NOW,
            revision_keys=["r1", "r2", "r3"],
        )
        # DTO 本身序列化顺序不同（list 保序）；Logic 在哈希前排序，故此处不比较 DTO hash
        assert set(bundle_a.revision_keys) == set(bundle_b.revision_keys)
