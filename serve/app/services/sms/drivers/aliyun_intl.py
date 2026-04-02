"""
阿里云国际短信驱动 — 纯 HTTP + ACS3-HMAC-SHA256 签名（V3）

API: POST https://dysmsapi.ap-southeast-1.aliyuncs.com
文档: https://help.aliyun.com/zh/sdk/product-overview/v3-request-structure-and-signature

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
import json
import time
import uuid
import ssl
from urllib.request import Request, urlopen

from app.services.base import BaseDriver


class AliyunIntlSmsDriver(BaseDriver):

    HOST = "dysmsapi.ap-southeast-1.aliyuncs.com"

    async def send(self, phone: str, template_id: str, params: dict) -> bool:
        ak_id = self._get("access_key_id", required=True)
        ak_secret = self._get("access_key_secret", required=True)
        sign_name = self._get("sign_name", required=True)
        if self.service.failed:
            return False

        template_id = template_id or self._get("template_code")

        body = json.dumps({
            "PhoneNumbers": phone,
            "SignName": sign_name,
            "TemplateCode": template_id,
            "TemplateParam": json.dumps(params, ensure_ascii=False),
        }, ensure_ascii=False)

        nonce = str(uuid.uuid4())
        date = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        body_hash = hashlib.sha256(body.encode()).hexdigest()

        signed_headers = "host;x-acs-action;x-acs-content-sha256;x-acs-date;x-acs-signature-nonce;x-acs-version"
        canonical = (
            f"POST\n/\n\n"
            f"host:{self.HOST}\n"
            f"x-acs-action:SendSms\n"
            f"x-acs-content-sha256:{body_hash}\n"
            f"x-acs-date:{date}\n"
            f"x-acs-signature-nonce:{nonce}\n"
            f"x-acs-version:2017-05-25\n\n"
            f"{signed_headers}\n"
            f"{body_hash}"
        )

        hashed_canonical = hashlib.sha256(canonical.encode()).hexdigest()
        string_to_sign = f"ACS3-HMAC-SHA256\n{hashed_canonical}"

        signature = hmac.new(
            ak_secret.encode(), string_to_sign.encode(), hashlib.sha256
        ).hexdigest()

        auth = f"ACS3-HMAC-SHA256 Credential={ak_id},SignedHeaders={signed_headers},Signature={signature}"

        url = f"https://{self.HOST}"
        req = Request(url, data=body.encode(), method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        req.add_header("Host", self.HOST)
        req.add_header("x-acs-action", "SendSms")
        req.add_header("x-acs-version", "2017-05-25")
        req.add_header("x-acs-date", date)
        req.add_header("x-acs-signature-nonce", nonce)
        req.add_header("x-acs-content-sha256", body_hash)
        req.add_header("Authorization", auth)

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                result = json.loads(resp.read().decode())
                if result.get("Code") != "OK":
                    self.service._fail(f"阿里云国际短信失败: {result.get('Message', result.get('Code', '未知'))}")
                    return False
                return True
        except Exception as e:
            self.service._fail(f"阿里云国际短信请求失败: {e}")
            return False
