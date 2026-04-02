"""
Telegram Bot 通知驱动 — 纯 HTTP，零依赖

config:
    {"bot_token": "123456:ABC-xxx", "chat_id": "-100123456789"}
"""

import json
import ssl
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from app.services.base import BaseDriver


class TelegramDriver(BaseDriver):
    async def send(self, title: str, content: str, **kwargs) -> bool:
        bot_token = self._get("bot_token", required=True)
        chat_id = self._get("chat_id", required=True)
        if self.service.failed:
            return False

        text = f"*{title}*\n\n{content}"
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        payload = json.dumps(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
            }
        ).encode()

        req = Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                result = json.loads(resp.read().decode())
                if not result.get("ok"):
                    self.service._fail(
                        f"Telegram 发送失败: {result.get('description', '未知错误')}"
                    )
                    return False
                return True
        except Exception as e:
            self.service._fail(f"Telegram 请求失败: {e}")
            return False
