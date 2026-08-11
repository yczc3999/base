"""AI 输出 validator：JSON Schema + 确定性语义校验（WP-02 Checkpoint B）。

每个 validator 独立成行；任一 hard validator 失败 → 调用不能形成可供下游使用的 artifact。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.ai_runtime.redaction import detect_taint, requires_quarantine
from app.domain.trading.probability import normalize_q, validate_u

HARD = "hard"
SOFT = "soft"


@dataclass(frozen=True)
class ValidatorResult:
    validator_name: str
    validator_version: str
    passed: bool
    severity: str
    reason_code: str | None = None
    details: dict | None = None


class OutputValidator:
    """组合 validator 集合；每个调用运行全部 validator 并逐条记录。"""

    def __init__(self, validators: list[Any] | None = None) -> None:
        self._validators: list[Any] = list(validators) if validators is not None else list(DEFAULT_VALIDATORS)

    def register(self, validator: Any) -> None:
        self._validators.append(validator)

    async def validate(
        self,
        *,
        raw_response: str | None,
        parsed_output: dict | None,
        normalized_output: dict | None,
        blind_context: bool,
        network_policy: str,
    ) -> list[ValidatorResult]:
        results: list[ValidatorResult] = []
        for validator in self._validators:
            result = await validator(
                raw_response=raw_response,
                parsed_output=parsed_output,
                normalized_output=normalized_output,
                blind_context=blind_context,
                network_policy=network_policy,
            )
            results.append(result)
        return results


# ---------------- 具体 validator ----------------

async def validate_json_schema(*, raw_response, parsed_output, normalized_output, blind_context, network_policy) -> ValidatorResult:
    """parsed output 必须可解析且为 JSON object（若 provider 返回了 text）。"""
    version = "json-schema/v1"
    if parsed_output is None and raw_response:
        try:
            decoded = json.loads(raw_response)
        except (json.JSONDecodeError, TypeError):
            return ValidatorResult("json_parser", version, False, HARD, "json_decode_failed")
        if not isinstance(decoded, dict):
            return ValidatorResult("json_parser", version, False, HARD, "json_not_object")
        return ValidatorResult("json_parser", version, True, HARD)
    return ValidatorResult("json_parser", version, True, HARD)


async def validate_secret_quarantine(*, raw_response, parsed_output, normalized_output, blind_context, network_policy) -> ValidatorResult:
    version = "secret-quarantine/v1"
    if raw_response and requires_quarantine(raw_response):
        return ValidatorResult("secret_quarantine", version, False, HARD, "secret_echo")
    if parsed_output and requires_quarantine(parsed_output):
        return ValidatorResult("secret_quarantine", version, False, HARD, "secret_echo_parsed")
    return ValidatorResult("secret_quarantine", version, True, HARD)


async def validate_blind_taint(*, raw_response, parsed_output, normalized_output, blind_context, network_policy) -> ValidatorResult:
    version = "blind-taint/v1"
    if not blind_context:
        return ValidatorResult("blind_taint", version, True, HARD)
    hits: list[str] = []
    for candidate in (parsed_output, normalized_output, raw_response):
        if candidate is None:
            continue
        hits.extend(detect_taint(candidate))
    if hits:
        return ValidatorResult("blind_taint", version, False, HARD, "taint", {"hits": hits})
    return ValidatorResult("blind_taint", version, True, HARD)


async def validate_probability_rollup(*, raw_response, parsed_output, normalized_output, blind_context, network_policy) -> ValidatorResult:
    """若 parsed output 声称含 Q，则 Q 必须合法（非负、total）。"""
    version = "probability/v1"
    if not isinstance(parsed_output, dict):
        return ValidatorResult("probability_rollup", version, True, SOFT)
    q = parsed_output.get("Q")
    if q is None:
        return ValidatorResult("probability_rollup", version, True, SOFT)
    try:
        normalize_q(q)
    except ValueError as exc:
        return ValidatorResult("probability_rollup", version, False, HARD, str(exc))
    u = parsed_output.get("U")
    if u is not None:
        try:
            validate_u(u, q=normalize_q(q))
        except ValueError as exc:
            return ValidatorResult("probability_rollup", version, False, HARD, str(exc))
    return ValidatorResult("probability_rollup", version, True, SOFT)


DEFAULT_VALIDATORS = [
    validate_json_schema,
    validate_secret_quarantine,
    validate_blind_taint,
    validate_probability_rollup,
]
