"""
企业微信机器人通知驱动 — 纯 HTTP

config:
    {"webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"}
"""

import json
import ssl
from urllib.request import Request, urlopen

from app.services.base import BaseDriver


class WechatWorkDriver(BaseDriver):
    async def send(self, title: str, content: str, **kwargs) -> bool:
        webhook = self._get("webhook", required=True)
        if self.service.failed:
            return False

        payload = json.dumps(
            {
                "msgtype": "markdown",
                "markdown": {
                    "content": f"### {title}\n\n{content}",
                },
            }
        ).encode()

        req = Request(webhook, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "BasePlatform/1.0")

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                result = json.loads(resp.read().decode())
                if result.get("errcode") != 0:
                    self.service._fail(
                        f"企业微信发送失败: {result.get('errmsg', '未知错误')}"
                    )
                    return False
                return True
        except Exception as e:
            self.service._fail(f"企业微信请求失败: {e}")
            return False
