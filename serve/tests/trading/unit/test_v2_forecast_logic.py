"""Forecast schema 单元测试（WP-02 Checkpoint A）。

验证 Q/U/lease/coherence DTO 的结构校验与 blind forbidden-key 拒绝；
G6 的 DB 编排在 integration 测试中覆盖。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.trading.forecast import (
    CoherenceCheckInput,
    ForecastLeaseInput,
    ForecastSubmissionInput,
    PayoutProjectionInput,
    QDistributionInput,
)

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class TestQDistributionInput:
    def test_valid(self):
        QDistributionInput(values={"w0": "0.6", "w1": "0.4"})

    def test_float_rejected(self):
        # 类型注解 dict[str,str] 在自定义校验器前拒绝非字符串
        with pytest.raises(ValidationError):
            QDistributionInput(values={"w0": 0.6, "w1": "0.4"})

    def test_non_decimal_rejected(self):
        with pytest.raises(ValidationError, match="q_invalid_decimal"):
            QDistributionInput(values={"w0": "abc", "w1": "0.4"})

    def test_empty_rejected(self):
        with pytest.raises(ValidationError):
            QDistributionInput(values=[])

    def test_negative_value_rejected(self):
        with pytest.raises(ValidationError, match="q_negative"):
            QDistributionInput(values={"w0": "-0.1", "w1": "1.1"})


class TestForecastSubmissionInput:
    def test_valid(self):
        submission = ForecastSubmissionInput(
            submission_key="sub-1",
            Q=QDistributionInput(values={"w0": "0.6", "w1": "0.4"}),
            U=[
                QDistributionInput(values={"w0": "0.6", "w1": "0.4"}),
                QDistributionInput(values={"w0": "0.5", "w1": "0.5"}),
            ],
            forecast_input_manifest_id=1,
        )
        assert submission.submission_key == "sub-1"

    def test_blind_forbidden_key_rejected(self):
        with pytest.raises(ValidationError, match="blind_forbidden_key"):
            ForecastSubmissionInput(
                submission_key="sub-1",
                Q=QDistributionInput(values={"w0": "0.6", "w1": "0.4", "quote": "0.5"}),
                U=[QDistributionInput(values={"w0": "0.6", "w1": "0.4"})],
                forecast_input_manifest_id=1,
            )

    def test_empty_u_rejected(self):
        with pytest.raises(ValidationError):
            ForecastSubmissionInput(
                submission_key="sub-1",
                Q=QDistributionInput(values={"w0": "1", "w1": "0"}),
                U=[],
                forecast_input_manifest_id=1,
            )


class TestPayoutProjectionInput:
    def test_valid(self):
        PayoutProjectionInput(
            contract_spec_id=1,
            pm_token_id=2,
            algorithm_hash="a" * 64,
        )

    def test_bad_hash_rejected(self):
        with pytest.raises(ValidationError):
            PayoutProjectionInput(
                contract_spec_id=1,
                pm_token_id=2,
                algorithm_hash="short",
            )


class TestForecastLeaseInput:
    def test_valid(self):
        ForecastLeaseInput(
            valid_until=NOW,
            invalidation_conditions={"fact_freshness": {"max_age_hours": 24}},
            evidence_hash="a" * 64,
            schema_hash="b" * 64,
            spec_hash="c" * 64,
        )

    def test_quote_in_conditions_rejected(self):
        with pytest.raises(ValidationError, match="blind_forbidden_key"):
            ForecastLeaseInput(
                valid_until=NOW,
                invalidation_conditions={"quote": "0.5"},
                evidence_hash="a" * 64,
                schema_hash="b" * 64,
                spec_hash="c" * 64,
            )

    def test_bad_hash_rejected(self):
        with pytest.raises(ValidationError):
            ForecastLeaseInput(
                valid_until=NOW,
                invalidation_conditions={},
                evidence_hash="x",
                schema_hash="b" * 64,
                spec_hash="c" * 64,
            )


class TestCoherenceCheckInput:
    def test_valid_hard(self):
        CoherenceCheckInput(
            check_name="q_nonneg_total",
            passed=True,
            severity="hard",
        )

    def test_bad_severity_rejected(self):
        with pytest.raises(ValidationError):
            CoherenceCheckInput(
                check_name="x",
                passed=True,
                severity="medium",
            )

    def test_failed_hard_with_reason(self):
        CoherenceCheckInput(
            check_name="q_not_total",
            passed=False,
            severity="hard",
            reason_code="g6_q_not_total",
        )
