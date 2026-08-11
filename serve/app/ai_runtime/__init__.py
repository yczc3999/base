"""AI runtime：attempt 生命周期 + validator + cache + redaction（WP-02 Checkpoint B）。"""

from app.ai_runtime.cache import CacheHit, cache_key, cacheable
from app.ai_runtime.redaction import (
    detect_taint,
    redact_for_storage,
    requires_quarantine,
)
from app.ai_runtime.runner import AIRunner, AttemptOutcome
from app.ai_runtime.validator import (
    DEFAULT_VALIDATORS,
    HARD,
    SOFT,
    OutputValidator,
    ValidatorResult,
    validate_blind_taint,
    validate_json_schema,
    validate_probability_rollup,
    validate_secret_quarantine,
)

__all__ = [
    "CacheHit",
    "cache_key",
    "cacheable",
    "detect_taint",
    "redact_for_storage",
    "requires_quarantine",
    "AIRunner",
    "AttemptOutcome",
    "DEFAULT_VALIDATORS",
    "HARD",
    "SOFT",
    "OutputValidator",
    "ValidatorResult",
    "validate_blind_taint",
    "validate_json_schema",
    "validate_probability_rollup",
    "validate_secret_quarantine",
]
