"""
腾讯云短信驱动 — 纯 HTTP + TC3-HMAC-SHA256 签名

API: sms.tencentcloudapi.com

config:
    {
        "secret_id": "...",
        "secret_key": "...",
        "app_id": "1400xxx",
        "sign_name": "...",
        "template_id": "xxx"
    }
"""

import hmac
import hashlib
import json
import time
import ssl
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from app.services.base import BaseDriver


class QcloudSmsDriver(BaseDriver):

    HOST = "sms.tencentcloudapi.com"

    async def send(self, phone: str, template_id: str, params: dict) -> bool:
        secret_id = self._get("secret_id", required=True)
        secret_key = self._get("secret_key", required=True)
        app_id = self._get("app_id", required=True)
        sign_name = self._get("sign_name", required=True)
        if self.service.failed:
            return False

        template_id = template_id or self._get("template_id")

        if not phone.startswith("+"):
            phone = f"+86{phone}"

        payload = json.dumps({
            "SmsSdkAppId": app_id,
            "SignName": sign_name,
            "TemplateId": template_id,
            "PhoneNumberSet": [phone],
            "TemplateParamSet": [str(v) for v in params.values()],
        })

        timestamp = int(time.time())
        date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
        ct = "application/json; charset=utf-8"
        service = "sms"
        action = "SendSms"

        # TC3-HMAC-SHA256 签名
        canonical = (
            f"POST\n/\n\ncontent-type:{ct}\nhost:{self.HOST}\n\n"
            f"content-type;host\n{hashlib.sha256(payload.encode()).hexdigest()}"
        )
        scope = f"{date}/{service}/tc3_request"
        string_to_sign = f"TC3-HMAC-SHA256\n{timestamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"

        def _hmac(key, msg):
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        secret_date = _hmac(f"TC3{secret_key}".encode(), date)
        secret_service = _hmac(secret_date, service)
        secret_signing = _hmac(secret_service, "tc3_request")
        signature = hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth = f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, SignedHeaders=content-type;host, Signature={signature}"

        req = Request(f"https://{self.HOST}", data=payload.encode(), method="POST")
        req.add_header("Content-Type", ct)
        req.add_header("Host", self.HOST)
        req.add_header("X-TC-Action", action)
        req.add_header("X-TC-Timestamp", str(timestamp))
        req.add_header("X-TC-Version", "2021-01-11")
        req.add_header("Authorization", auth)

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                result = json.loads(resp.read().decode())
                error = result.get("Response", {}).get("Error")
                if error:
                    self.service._fail(f"腾讯云短信失败: {error.get('Message', error.get('Code'))}")
                    return False
                return True
        except Exception as e:
            self.service._fail(f"腾讯云短信请求失败: {e}")
            return False
