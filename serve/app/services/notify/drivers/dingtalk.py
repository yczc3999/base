"""
钉钉机器人通知驱动 — 纯 HTTP

config:
    {"webhook": "https://oapi.dingtalk.com/robot/send?access_token=xxx", "secret": "SECxxx"}
"""

import json
import time
import hmac
import hashlib
import base64
import ssl
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote_plus

from app.services.base import BaseDriver


class DingtalkDriver(BaseDriver):

    async def send(self, title: str, content: str, **kwargs) -> bool:
        webhook = self._get("webhook", required=True)
        if self.service.failed:
            return False

        secret = self._get("secret")

        # 加签
        if secret:
            timestamp = str(int(time.time() * 1000))
            sign_str = f"{timestamp}\n{secret}"
            sign = quote_plus(base64.b64encode(
                hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).digest()
            ).decode())
            webhook = f"{webhook}&timestamp={timestamp}&sign={sign}"

        payload = json.dumps({
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"### {title}\n\n{content}",
            },
        }).encode()

        req = Request(webhook, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "BasePlatform/1.0")

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                result = json.loads(resp.read().decode())
                if result.get("errcode") != 0:
                    self.service._fail(f"钉钉发送失败: {result.get('errmsg', '未知错误')}")
                    return False
                return True
        except Exception as e:
            self.service._fail(f"钉钉请求失败: {e}")
            return False
