"""
华为云短信驱动 — 纯 HTTP + WSSE 认证

API: https://smsapi.cn-north-4.myhuaweicloud.com:443/sms/batchSendSms/v1

config:
    {
        "app_key": "...",
        "app_secret": "...",
        "sender": "csms12345678",
        "template_id": "xxx",
        "signature": "..."
    }
"""

import hashlib
import base64
import time
import uuid
import json
import ssl
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from app.services.base import BaseDriver


class HuaweiSmsDriver(BaseDriver):

    API = "https://smsapi.cn-north-4.myhuaweicloud.com:443/sms/batchSendSms/v1"

    async def send(self, phone: str, template_id: str, params: dict) -> bool:
        app_key = self._get("app_key", required=True)
        app_secret = self._get("app_secret", required=True)
        sender = self._get("sender", required=True)
        signature = self._get("signature") or ""
        if self.service.failed:
            return False

        template_id = template_id or self._get("template_id")

        if not phone.startswith("+"):
            phone = f"+86{phone}"

        # WSSE 认证
        nonce = str(uuid.uuid4()).replace("-", "")
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        password_digest = base64.b64encode(
            hashlib.sha256(f"{nonce}{created}{app_secret}".encode()).digest()
        ).decode()

        wsse = f'UsernameToken Username="{app_key}", PasswordDigest="{password_digest}", Nonce="{nonce}", Created="{created}"'

        template_paras = json.dumps([str(v) for v in params.values()])
        body = urlencode({
            "from": sender,
            "to": phone,
            "templateId": template_id,
            "templateParas": template_paras,
            "signature": signature,
        })

        req = Request(self.API, data=body.encode(), method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Authorization", 'WSSE realm="SDP", profile="UsernameToken", type="Appkey"')
        req.add_header("X-WSSE", wsse)

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                result = json.loads(resp.read().decode())
                code = result.get("code")
                if code != "000000":
                    self.service._fail(f"华为云短信失败: {result.get('description', code)}")
                    return False
                return True
        except Exception as e:
            self.service._fail("华为云短信请求失败")
            return False
