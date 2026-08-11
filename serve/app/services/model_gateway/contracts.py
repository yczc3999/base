"""Provider-neutral model gateway contracts（WP-02 Checkpoint B）。

- ``ModelRequest``：一次 provider request 的完整、可重放描述（role/network/tools/input manifest
  hash/prompt/schema/sampling/seed）。不携带明文 secret。
- ``ModelResponse``：provider 返回的规范化结果（returned provider/route/model、raw text、
  usage、tool receipts、provider request id、时间）。
- ``ToolReceipt``：一次工具调用的可验证收据（arguments/result/source_urls/published/observed）。
- ``ProviderError``：失败/超时/限流的结构化错误（不含 DSN/secret）。

Driver 只做 wire mapping；重试/fallback/cache/状态机属于 Runner（任务 §5.2）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# 网络能力（任务 §5.4）
NETWORK_NONE = "NONE"
NETWORK_WEB_X = "WEB_X"
NETWORK_SEARCH_URL = "SEARCH_URL"

# 合法 provider（任务 §2.10；Anthropic/OpenAI 不在首版注册表）
ALLOWED_PROVIDERS = frozenset({"deepseek", "xai", "gemini", "kimi", "packy"})


@dataclass(frozen=True)
class ToolReceipt:
    """一次工具调用的可验证收据；无 tool receipt 的引用不算可验证研究。"""

    ordinal: int
    tool_type: str
    tool_version: str | None
    arguments: dict
    result_text: str | None
    source_urls: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    observed_at: datetime | None = None
    provider_tool_call_id: str | None = None


@dataclass(frozen=True)
class ModelRequest:
    """一次 provider request 的完整描述；不携带 secret。"""

    role: str
    stage: str
    episode_id: int
    attempt_no: int
    experiment_variant: str
    # requested provider/route/model（allowlist 由 registry 校验）
    requested_provider: str
    requested_route: str
    requested_model: str
    network_policy: str = NETWORK_NONE
    allowed_tools: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    prompt_text: str = ""
    prompt_hash: str = ""
    schema_text: str = ""
    schema_hash: str = ""
    input_manifest: dict = field(default_factory=dict)
    input_manifest_hash: str = ""
    sampling: dict = field(default_factory=dict)
    seed: int | None = None
    effort: str | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None

    def assert_blind_context(self, context_class: str) -> None:
        """Blind 调用不允许 quote/odds/crowd/label/future-fact 上下文。"""
        if context_class not in ("CONTRACT", "PRIOR", "EVIDENCE"):
            raise ValueError(f"blind_context_forbidden:{context_class}")
        if self.network_policy != NETWORK_NONE or self.allowed_tools:
            raise ValueError("blind_network_forbidden")


@dataclass(frozen=True)
class ModelResponse:
    """Provider 返回的规范化结果。"""

    returned_provider: str
    returned_route: str
    returned_model: str
    raw_text: str
    parsed_output: dict | None = None
    normalized_output: dict | None = None
    input_tokens: int | None = None
    cache_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    tool_receipts: list[ToolReceipt] = field(default_factory=list)
    provider_request_id: str | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ProviderError(Exception):
    """结构化 provider 失败；reason 固定 code，detail 不含 DSN/secret/raw body。"""

    reason: str
    status_code: int | None = None
    retriable: bool = False
    detail: str | None = None

    def __str__(self) -> str:
        base = f"provider_error:{self.reason}"
        return base if self.detail is None else f"{base}:{self.detail}"
