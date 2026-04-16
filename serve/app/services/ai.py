"""统一 AI 调用 — 兼容 OpenAI API 格式

DeepSeek / MiniMax / GLM / OpenAI 都走同一个接口，只是 base_url 和 model 不同。
配置从 settings 表读取（category=ai）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import AsyncIterator

import httpx

from app.logics.setting import setting_logic
from app.services.database import async_session

logger = logging.getLogger(__name__)

PROVIDER_DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "minimax": {"base_url": "https://api.minimaxi.com/v1", "model": "MiniMax-M2.7-highspeed"},
    "glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
}


async def get_ai_config() -> dict:
    """从 settings 表读 AI 配置"""
    async with async_session() as db:
        provider = await setting_logic.get(db, "ai", "provider", "deepseek")
        api_key = await setting_logic.get(db, "ai", "api_key", "")
        base_url = await setting_logic.get(db, "ai", "base_url", "")
        model = await setting_logic.get(db, "ai", "model", "")

    defaults = PROVIDER_DEFAULTS.get(provider, {})
    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url or defaults.get("base_url", ""),
        "model": model or defaults.get("model", ""),
    }


async def chat(prompt: str, system: str = "") -> str:
    """单轮对话，返回文本"""
    cfg = await get_ai_config()
    if not cfg["api_key"]:
        raise ValueError("AI 未配置 API Key，请到 设置 → AI 配置 填写")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "messages": messages,
                "max_tokens": 4000,
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data["choices"][0]["message"]["content"]
        return strip_think_tags(raw)


async def chat_stream(prompt: str, system: str = "") -> AsyncIterator[str]:
    """流式对话，逐 chunk yield"""
    cfg = await get_ai_config()
    if not cfg["api_key"]:
        raise ValueError("AI 未配置 API Key")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST", url,
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "messages": messages,
                "max_tokens": 4000,
                "temperature": 0.3,
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL)


def strip_think_tags(text: str) -> str:
    """去掉 AI 返回的 <think>...</think> 推理块，只保留正文。

    部分模型（DeepSeek-R1、MiniMax-M2.7 等）会在回复开头输出 <think> 标签包裹的思考过程，
    业务代码只需要最终回答。
    """
    cleaned = _THINK_RE.sub('', text).strip()
    return cleaned if cleaned else text
