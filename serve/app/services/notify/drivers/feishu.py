"""
飞书机器人通知驱动 — 纯 HTTP

config:
    {"webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx", "secret": "xxx"}
"""

import json
import time
import hmac
import hashlib
import base64
import ssl
from urllib.request import Request, urlopen

from app.services.base import BaseDriver


class FeishuDriver(BaseDriver):
    async def send(self, title: str, content: str, **kwargs) -> bool:
        webhook = self._get("webhook", required=True)
        if self.service.failed:
            return False

        secret = self._get("secret")
        payload_dict = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": title}},
                "elements": [{"tag": "markdown", "content": content}],
            },
        }

        # 加签
        if secret:
            timestamp = str(int(time.time()))
            sign_str = f"{timestamp}\n{secret}"
            sign = base64.b64encode(
                hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).digest()
            ).decode()
            payload_dict["timestamp"] = timestamp
            payload_dict["sign"] = sign

        payload = json.dumps(payload_dict).encode()
        req = Request(webhook, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                result = json.loads(resp.read().decode())
                if result.get("code") != 0:
                    self.service._fail(f"飞书发送失败: {result.get('msg', '未知错误')}")
                    return False
                return True
        except Exception as e:
            self.service._fail("飞书请求失败")
            return False
