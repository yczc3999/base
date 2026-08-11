"""Gemini 3.6 Flash independent verifier wire mapping（WP-02 Checkpoint B）。

允许 Search/URL（任务 §5.4）；transport 注入以便离线 golden fixture 测试。
"""

from __future__ import annotations

import json
from typing import Any

from app.services.model_gateway.contracts import (
    ModelRequest,
    ModelResponse,
    NETWORK_SEARCH_URL,
    ProviderError,
    ToolReceipt,
)
from app.services.model_gateway.drivers.base import ModelDriver, redact


class GeminiDriver(ModelDriver):
    driver_name = "gemini"
    provider = "gemini"

    async def request(self, model_request: ModelRequest) -> ModelResponse:
        tools = []
        if model_request.network_policy == NETWORK_SEARCH_URL:
            tools = [{"googleSearchRetrieval": {}}]
        payload = redact(
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": model_request.prompt_text},
                            {"text": json.dumps(model_request.input_manifest)},
                        ],
                    }
                ],
                "tools": tools,
                "generationConfig": {
                    "temperature": model_request.sampling.get("temperature", 0.0),
                    "maxOutputTokens": model_request.max_tokens or 4096,
                },
            }
        )
        headers = {"Content-Type": "application/json"}
        try:
            status, body = await self._transport(
                endpoint="/v1beta/models:generateContent",
                headers=redact(headers),
                json=payload,
                timeout=self._timeout,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("gemini_transport_failed", retriable=True, detail="transport") from exc
        if status >= 500:
            raise ProviderError("gemini_5xx", status_code=status, retriable=True, detail=str(status))
        if status == 429:
            raise ProviderError("gemini_rate_limited", status_code=429, retriable=True, detail="429")
        if status >= 400:
            raise ProviderError("gemini_4xx", status_code=status, retriable=False, detail=str(status))
        try:
            parsed = json.loads(body) if isinstance(body, str) else body
            candidates = parsed["candidates"]
            parts = candidates[0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts if "text" in part)
            usage = parsed.get("usageMetadata", {})
            receipts: list[ToolReceipt] = []
            for index, part in enumerate(parts):
                if "groundingChunks" in part:
                    urls = [
                        chunk.get("web", {}).get("uri", "")
                        for chunk in part.get("groundingChunks", [])
                    ]
                    receipts.append(
                        ToolReceipt(
                            ordinal=index,
                            tool_type="search_url",
                            tool_version="v1",
                            arguments={},
                            result_text=None,
                            source_urls=[url for url in urls if url],
                        )
                    )
            return ModelResponse(
                returned_provider="gemini",
                returned_route=model_request.requested_route,
                returned_model=model_request.requested_model,
                raw_text=text,
                input_tokens=usage.get("promptTokenCount"),
                output_tokens=usage.get("candidatesTokenCount"),
                tool_receipts=receipts,
            )
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("gemini_response_malformed", retriable=False, detail="parse") from exc
