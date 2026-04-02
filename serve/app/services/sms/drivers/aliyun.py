"""
阿里云短信驱动 — 纯 HTTP + HMAC-SHA1 签名

API: https://dysmsapi.aliyuncs.com/?Action=SendSms

config:
    {
        "access_key_id": "...",
        "access_key_secret": "...",
        "sign_name": "...",
        "template_code": "SMS_xxx"
    }
"""

import hmac
import hashlib
import base64
import json
import time
import uuid
import ssl
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote

from app.services.base import BaseDriver


class AliyunSmsDriver(BaseDriver):

    API = "https://dysmsapi.aliyuncs.com"

    async def send(self, phone: str, template_id: str, params: dict) -> bool:
        ak_id = self._get("access_key_id", required=True)
        ak_secret = self._get("access_key_secret", required=True)
        sign_name = self._get("sign_name", required=True)
        if self.service.failed:
            return False

        template_id = template_id or self._get("template_code")

        query = {
            "Action": "SendSms",
            "Version": "2017-05-25",
            "Format": "JSON",
            "AccessKeyId": ak_id,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4()),
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "PhoneNumbers": phone,
            "SignName": sign_name,
            "TemplateCode": template_id,
            "TemplateParam": json.dumps(params, ensure_ascii=False),
        }

        # 签名
        sorted_qs = "&".join(f"{quote(k, safe='')}={quote(str(v), safe='')}" for k, v in sorted(query.items()))
        string_to_sign = f"GET&{quote('/', safe='')}&{quote(sorted_qs, safe='')}"
        signature = base64.b64encode(
            hmac.new(f"{ak_secret}&".encode(), string_to_sign.encode(), hashlib.sha1).digest()
        ).decode()
        query["Signature"] = signature

        url = f"{self.API}?{urlencode(query)}"
        req = Request(url)

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                result = json.loads(resp.read().decode())
                if result.get("Code") != "OK":
                    self.service._fail(f"阿里云短信失败: {result.get('Message', result.get('Code', '未知'))}")
                    return False
                return True
        except Exception as e:
            self.service._fail(f"阿里云短信请求失败: {e}")
            return False
