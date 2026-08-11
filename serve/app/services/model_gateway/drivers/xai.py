"""xAI Grok 4.5 Web/X researcher wire mapping（WP-02 Checkpoint B）。

允许 Web/X tool（任务 §5.4）；transport 注入以便离线 golden fixture 测试。
"""

from __future__ import annotations

import json
from typing import Any

from app.services.model_gateway.contracts import (
    ModelRequest,
    ModelResponse,
    NETWORK_WEB_X,
    ProviderError,
    ToolReceipt,
)
from app.services.model_gateway.drivers.base import ModelDriver, redact


class XAIDriver(ModelDriver):
    driver_name = "xai"
    provider = "xai"

    async def request(self, model_request: ModelRequest) -> ModelResponse:
        tools = []
        if model_request.network_policy == NETWORK_WEB_X:
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web / X for current information.",
                    },
                }
            ]
        payload = redact(
            {
                "model": model_request.requested_model,
                "messages": [
                    {"role": "system", "content": model_request.prompt_text},
                    {"role": "user", "content": json.dumps(model_request.input_manifest)},
                ],
                "tools": tools,
                "temperature": model_request.sampling.get("temperature", 0.0),
                "max_tokens": model_request.max_tokens or 4096,
            }
        )
        headers = {"Content-Type": "application/json"}
        try:
            status, body = await self._transport(
                endpoint="/v1/chat/completions",
                headers=redact(headers),
                json=payload,
                timeout=self._timeout,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("xai_transport_failed", retriable=True, detail="transport") from exc
        if status >= 500:
            raise ProviderError("xai_5xx", status_code=status, retriable=True, detail=str(status))
        if status == 429:
            raise ProviderError("xai_rate_limited", status_code=429, retriable=True, detail="429")
        if status >= 400:
            raise ProviderError("xai_4xx", status_code=status, retriable=False, detail=str(status))
        try:
            parsed = json.loads(body) if isinstance(body, str) else body
            choice = parsed["choices"][0]
            message = choice["message"]
            usage = parsed.get("usage", {})
            receipts: list[ToolReceipt] = []
            tool_calls = message.get("tool_calls") or []
            for index, call in enumerate(tool_calls):
                function = call.get("function", {})
                receipts.append(
                    ToolReceipt(
                        ordinal=index,
                        tool_type="web_search",
                        tool_version="v1",
                        arguments=json.loads(function.get("arguments", "{}")),
                        result_text=None,
                        provider_tool_call_id=call.get("id"),
                    )
                )
            return ModelResponse(
                returned_provider="xai",
                returned_route=model_request.requested_route,
                returned_model=message.get("model") or model_request.requested_model,
                raw_text=message.get("content") or "",
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                tool_receipts=receipts,
                provider_request_id=parsed.get("id"),
            )
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("xai_response_malformed", retriable=False, detail="parse") from exc
