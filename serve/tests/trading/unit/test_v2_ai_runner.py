"""AI runner unit tests（WP-02 Checkpoint B）。

- cache key：exact、输入顺序无关、只缓存 ACCEPTED+network=NONE。
- validator：json/secret/taint/probability rollup。
- redaction：敏感字段脱敏 + secret echo quarantine 判定。
"""

from __future__ import annotations

import asyncio

from app.ai_runtime.cache import cache_key, cacheable
from app.ai_runtime.redaction import detect_taint, redact_for_storage, requires_quarantine
from app.ai_runtime.validator import (
    validate_blind_taint,
    validate_json_schema,
    validate_probability_rollup,
    validate_secret_quarantine,
)


def asyncio_run(coro):
    return asyncio.run(coro)


class TestCacheKey:
    def test_exact_and_order_insensitive(self):
        key1 = cache_key(
            role="planner_prior", input_manifest_hash="a" * 64,
            provider="deepseek", route="direct", model="deepseek-v4-pro",
            prompt_hash="b" * 64, schema_hash="c" * 64, code_hash="d" * 64,
            network_policy="NONE", tools=None, sampling={"temperature": 0}, seed=1,
        )
        key2 = cache_key(
            role="planner_prior", input_manifest_hash="a" * 64,
            provider="deepseek", route="direct", model="deepseek-v4-pro",
            prompt_hash="b" * 64, schema_hash="c" * 64, code_hash="d" * 64,
            network_policy="NONE", tools=None, sampling={"temperature": 0}, seed=1,
        )
        assert key1 == key2
        # 输入 manifest hash 变化 → key 不同
        key3 = cache_key(
            role="planner_prior", input_manifest_hash="e" * 64,
            provider="deepseek", route="direct", model="deepseek-v4-pro",
            prompt_hash="b" * 64, schema_hash="c" * 64, code_hash="d" * 64,
            network_policy="NONE", tools=None, sampling={"temperature": 0}, seed=1,
        )
        assert key1 != key3
        # 模型变化 → key 不同
        key4 = cache_key(
            role="planner_prior", input_manifest_hash="a" * 64,
            provider="deepseek", route="direct", model="deepseek-v4",
            prompt_hash="b" * 64, schema_hash="c" * 64, code_hash="d" * 64,
            network_policy="NONE", tools=None, sampling={"temperature": 0}, seed=1,
        )
        assert key1 != key4

    def test_tools_order_insensitive(self):
        key1 = cache_key(
            role="r", input_manifest_hash="a" * 64, provider="xai", route="direct",
            model="grok-4.5", prompt_hash="b" * 64, schema_hash="c" * 64, code_hash="d" * 64,
            network_policy="WEB_X", tools=["b", "a"], sampling={}, seed=None,
        )
        key2 = cache_key(
            role="r", input_manifest_hash="a" * 64, provider="xai", route="direct",
            model="grok-4.5", prompt_hash="b" * 64, schema_hash="c" * 64, code_hash="d" * 64,
            network_policy="WEB_X", tools=["a", "b"], sampling={}, seed=None,
        )
        assert key1 == key2

    def test_cacheable_only_accepted_network_none(self):
        assert cacheable(True, "NONE")
        assert not cacheable(True, "WEB_X")
        assert not cacheable(False, "NONE")
        assert not cacheable(False, "WEB_X")


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

    def test_requires_quarantine(self):
        assert requires_quarantine("Bearer sk-abcdef12345678")
        assert requires_quarantine({"data": "-----BEGIN PRIVATE KEY-----"})
        assert not requires_quarantine("plain text")

    def test_detect_taint(self):
        hits = detect_taint({"nested": {"odds": "2.0"}, "Q": {"w0": "1"}})
        assert "nested.odds" in hits
        assert not any("Q" in hit for hit in hits)
