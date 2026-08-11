"""Kimi K3 joint forecaster wire mapping（WP-02 Checkpoint B）。

网络策略固定 NONE/tools=[]（任务 §5.4）；transport 注入以便离线 golden fixture 测试。
"""

from __future__ import annotations

import json
from typing import Any

from app.services.model_gateway.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderError,
)
from app.services.model_gateway.drivers.base import ModelDriver, redact


class KimiDriver(ModelDriver):
    driver_name = "kimi"
    provider = "kimi"

    async def request(self, model_request: ModelRequest) -> ModelResponse:
        self._assert_no_network_for_none(model_request)
        payload = redact(
            {
                "model": model_request.requested_model,
                "messages": [
                    {"role": "system", "content": model_request.prompt_text},
                    {"role": "user", "content": json.dumps(model_request.input_manifest)},
                ],
                "temperature": model_request.sampling.get("temperature", 0.0),
                "max_tokens": model_request.max_tokens or 4096,
            }
        )
        headers = {"Content-Type": "application/json"}
        try:
            status, body = await self._transport(
                endpoint="/chat/completions",
                headers=redact(headers),
                json=payload,
                timeout=self._timeout,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("kimi_transport_failed", retriable=True, detail="transport") from exc
        if status >= 500:
            raise ProviderError("kimi_5xx", status_code=status, retriable=True, detail=str(status))
        if status == 429:
            raise ProviderError("kimi_rate_limited", status_code=429, retriable=True, detail="429")
        if status >= 400:
            raise ProviderError("kimi_4xx", status_code=status, retriable=False, detail=str(status))
        try:
            parsed = json.loads(body) if isinstance(body, str) else body
            choice = parsed["choices"][0]
            message = choice["message"]
            usage = parsed.get("usage", {})
            return ModelResponse(
                returned_provider="kimi",
                returned_route=model_request.requested_route,
                returned_model=message.get("model") or model_request.requested_model,
                raw_text=message.get("content") or "",
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                provider_request_id=parsed.get("id"),
            )
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("kimi_response_malformed", retriable=False, detail="parse") from exc
