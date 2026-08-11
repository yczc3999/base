"""AI runner unit tests（WP-02 Checkpoint B）。

- cache key：exact、输入顺序无关、只缓存 ACCEPTED+network=NONE。
- validator：json/secret/taint/probability rollup。
- redaction：敏感字段脱敏 + secret echo quarantine 判定。
"""

from __future__ import annotations

import asyncio

from app.ai_runtime.cache import cache_key, cacheable
from app.ai_runtime.runner import (
    AIRunner,
    ARTIFACT_REF_CACHE_LIMIT,
    estimate_response_cost,
    normalize_pricing_snapshot,
)
from app.ai_runtime.redaction import detect_taint, redact_for_storage, requires_quarantine
from app.ai_runtime.validator import (
    validate_blind_taint,
    validate_json_schema,
    validate_probability_rollup,
    validate_secret_quarantine,
    validate_normalized_output,
)
from app.services.model_gateway.contracts import ModelResponse, ToolReceipt


def asyncio_run(coro):
    return asyncio.run(coro)


class TestCacheKey:
    def test_exact_and_order_insensitive(self):
        key1 = cache_key(
            role="planner_prior", input_manifest_hash="a" * 64,
            provider="deepseek", route="direct", model="deepseek-v4-pro",
            prompt_hash="b" * 64, schema_hash="c" * 64, code_hash="d" * 64,
            network_policy="NONE", tools=None, domains=None, sampling={"temperature": 0},
            seed=1, effort=None, max_tokens=None,
        )
        key2 = cache_key(
            role="planner_prior", input_manifest_hash="a" * 64,
            provider="deepseek", route="direct", model="deepseek-v4-pro",
            prompt_hash="b" * 64, schema_hash="c" * 64, code_hash="d" * 64,
            network_policy="NONE", tools=None, domains=None, sampling={"temperature": 0},
            seed=1, effort=None, max_tokens=None,
        )
        assert key1 == key2
        # 输入 manifest hash 变化 → key 不同
        key3 = cache_key(
            role="planner_prior", input_manifest_hash="e" * 64,
            provider="deepseek", route="direct", model="deepseek-v4-pro",
            prompt_hash="b" * 64, schema_hash="c" * 64, code_hash="d" * 64,
            network_policy="NONE", tools=None, domains=None, sampling={"temperature": 0},
            seed=1, effort=None, max_tokens=None,
        )
        assert key1 != key3
        # 模型变化 → key 不同
        key4 = cache_key(
            role="planner_prior", input_manifest_hash="a" * 64,
            provider="deepseek", route="direct", model="deepseek-v4",
            prompt_hash="b" * 64, schema_hash="c" * 64, code_hash="d" * 64,
            network_policy="NONE", tools=None, domains=None, sampling={"temperature": 0},
            seed=1, effort=None, max_tokens=None,
        )
        assert key1 != key4

    def test_tools_order_insensitive(self):
        key1 = cache_key(
            role="r", input_manifest_hash="a" * 64, provider="xai", route="direct",
            model="grok-4.5", prompt_hash="b" * 64, schema_hash="c" * 64, code_hash="d" * 64,
            network_policy="WEB_X", tools=["b", "a"], domains=["b.example", "a.example"],
            sampling={}, seed=None, effort="high", max_tokens=2048,
        )
        key2 = cache_key(
            role="r", input_manifest_hash="a" * 64, provider="xai", route="direct",
            model="grok-4.5", prompt_hash="b" * 64, schema_hash="c" * 64, code_hash="d" * 64,
            network_policy="WEB_X", tools=["a", "b"], domains=["a.example", "b.example"],
            sampling={}, seed=None, effort="high", max_tokens=2048,
        )
        assert key1 == key2
        for override in (
            {"domains": ["different.example"]},
            {"effort": "low"},
            {"max_tokens": 4096},
        ):
            dimensions = {
                "domains": ["a.example", "b.example"],
                "effort": "high",
                "max_tokens": 2048,
                **override,
            }
            changed = cache_key(
                role="r", input_manifest_hash="a" * 64,
                provider="xai", route="direct", model="grok-4.5",
                prompt_hash="b" * 64, schema_hash="c" * 64,
                code_hash="d" * 64, network_policy="WEB_X",
                tools=["a", "b"], sampling={}, seed=None,
                **dimensions,
            )
            assert changed != key1

    def test_cacheable_only_accepted_network_none(self):
        assert cacheable(True, "NONE")
        assert not cacheable(True, "WEB_X")
        assert not cacheable(False, "NONE")
        assert not cacheable(False, "WEB_X")


class TestPricing:
    def test_deterministic_base_unit_estimate(self):
        snapshot, currency, state = normalize_pricing_snapshot({
            "currency": "USD_MICRO",
            "input_per_1m": 1_000_000,
            "cache_per_1m": 500_000,
            "output_per_1m": 2_000_000,
            "reasoning_per_1m": 3_000_000,
            "tool_per_call": 7,
            "search_per_call": 11,
        })
        response = ModelResponse(
            returned_provider="xai",
            returned_route="direct",
            returned_model="grok-4.5",
            raw_text="{}",
            input_tokens=120,
            cache_tokens=20,
            output_tokens=30,
            reasoning_tokens=10,
            tool_receipts=[ToolReceipt(0, "web_search", "v1", {}, None)],
        )
        assert (currency, state) == ("USD_MICRO", "PENDING")
        # 100 input + 10 cache + 60 output + 30 reasoning + 7 tool + 11 search.
        assert estimate_response_cost(snapshot, response) == (
            218,
            "USD_MICRO",
            "ESTIMATED",
        )

    def test_explicit_unpriced_is_honest(self):
        snapshot, currency, state = normalize_pricing_snapshot({"status": "UNPRICED"})
        response = ModelResponse("deepseek", "direct", "deepseek-v4-pro", "{}")
        assert (currency, state) == (None, "UNPRICED")
        assert estimate_response_cost(snapshot, response) == (0, None, "UNPRICED")


def test_artifact_ref_cache_is_bounded_lru():
    runner = AIRunner(gateway=object(), artifacts=object())
    for index in range(ARTIFACT_REF_CACHE_LIMIT + 2):
        runner._remember_artifact_ref((f"{index:064x}", "application/json"), object())
    assert len(runner._artifact_ref_cache) == ARTIFACT_REF_CACHE_LIMIT
    assert (f"{0:064x}", "application/json") not in runner._artifact_ref_cache
    assert (
        f"{ARTIFACT_REF_CACHE_LIMIT + 1:064x}",
        "application/json",
    ) in runner._artifact_ref_cache


class TestValidator:
    async def _run(self, fn, **kwargs):
        return await fn(
            raw_response=None, parsed_output=None, normalized_output=None,
            blind_context=False, network_policy="NONE", **kwargs,
        )

    def test_json_parser_valid(self):
        result = asyncio_run(validate_json_schema(raw_response='{"a":1}', parsed_output={"a": 1},
                                                  normalized_output=None, blind_context=False,
                                                  network_policy="NONE"))
        assert result.passed and result.severity == "hard"

    def test_json_parser_invalid(self):
        result = asyncio_run(validate_json_schema(raw_response='{bad', parsed_output=None,
                                                  normalized_output=None, blind_context=False,
                                                  network_policy="NONE"))
        assert not result.passed and result.reason_code == "json_decode_failed"

    def test_secret_quarantine(self):
        result = asyncio_run(validate_secret_quarantine(raw_response='Bearer sk-abcdefgh12345678',
                                                        parsed_output=None, normalized_output=None,
                                                        blind_context=False, network_policy="NONE"))
        assert not result.passed and result.reason_code == "secret_echo"

    def test_blind_taint(self):
        result = asyncio_run(validate_blind_taint(raw_response=None,
                                                  parsed_output={"Q": {"w0": "0.5"}, "quote": "0.5"},
                                                  normalized_output=None,
                                                  blind_context=True, network_policy="NONE"))
        assert not result.passed and result.reason_code == "taint"
        assert result.details["hits"]

    def test_blind_taint_decodes_raw_json_recursively(self):
        result = asyncio_run(validate_blind_taint(
            raw_response='{"outer":{"quote":"0.73"}}',
            parsed_output=None,
            normalized_output=None,
            blind_context=True,
            network_policy="NONE",
        ))
        assert not result.passed and result.reason_code == "taint"
        assert result.details["hits"] == ["$json.outer.quote"]

    def test_normalized_output_is_required(self):
        result = asyncio_run(validate_normalized_output(
            raw_response="{}", parsed_output={}, normalized_output=None,
            blind_context=True, network_policy="NONE",
        ))
        assert not result.passed and result.reason_code == "normalized_output_missing"

    def test_blind_clean(self):
        result = asyncio_run(validate_blind_taint(raw_response=None,
                                                  parsed_output={"Q": {"w0": "0.5"}},
                                                  normalized_output=None,
                                                  blind_context=True, network_policy="NONE"))
        assert result.passed

    def test_probability_rollup_valid(self):
        result = asyncio_run(validate_probability_rollup(raw_response=None,
                                                         parsed_output={"Q": {"w0": "0.6", "w1": "0.4"}},
                                                         normalized_output=None,
                                                         blind_context=True, network_policy="NONE"))
        assert result.passed

    def test_probability_rollup_invalid(self):
        result = asyncio_run(validate_probability_rollup(raw_response=None,
                                                         parsed_output={"Q": {"w0": "0.5", "w1": "0.4"}},
                                                         normalized_output=None,
                                                         blind_context=True, network_policy="NONE"))
        assert not result.passed


class TestRedaction:
    def test_redact_for_storage(self):
        safe = redact_for_storage({
            "authorization": "Bearer abc", "nested": {"api_key": "k", "ok": 1},
        })
        assert safe["authorization"] == "[REDACTED]"
        assert safe["nested"]["api_key"] == "[REDACTED]"
        assert safe["nested"]["ok"] == 1

    def test_redaction_preserves_public_token_identity_and_usage(self):
        value = {
            "token_id": "12345",
            "clob_token_ids": ["yes", "no"],
            "max_tokens": 2048,
            "input_tokens": 120,
            "output_tokens": 44,
        }
        assert redact_for_storage(value) == value

    def test_redaction_removes_only_credential_token_fields(self):
        safe = redact_for_storage({
            "access_token": "credential",
            "refresh-token": "credential-2",
            "oauth_client_secret": "credential-3",
            "session_token": "credential-4",
        })
        assert set(safe.values()) == {"[REDACTED]"}

    def test_requires_quarantine(self):
        assert requires_quarantine("Bearer sk-abcdef12345678")
        assert requires_quarantine({"data": "-----BEGIN PRIVATE KEY-----"})
        assert not requires_quarantine("plain text")

    def test_detect_taint(self):
        hits = detect_taint({"nested": {"odds": "2.0"}, "Q": {"w0": "1"}})
        assert "nested.odds" in hits
        assert not any("Q" in hit for hit in hits)
