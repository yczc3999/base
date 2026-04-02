"""
腾讯云 COS 驱动 — 纯 HTTP PUT + HMAC-SHA256 签名

config:
    {
        "secret_id": "...",
        "secret_key": "...",
        "bucket": "my-bucket-1250000000",
        "region": "ap-guangzhou",
        "domain": ""
    }
"""

import hmac
import hashlib
import time
import ssl
from urllib.request import Request, urlopen
from urllib.parse import quote

from app.services.base import BaseDriver


class QcloudCosDriver(BaseDriver):

    async def upload(self, path: str, content: bytes, visible: bool = True) -> str:
        bucket = self._get("bucket", required=True)
        region = self._get("region", required=True)
        secret_id = self._get("secret_id", required=True)
        secret_key = self._get("secret_key", required=True)
        if self.service.failed:
            return ""

        host = f"{bucket}.cos.{region}.myqcloud.com"
        uri = f"/{path.lstrip('/')}"
        url = f"https://{host}{uri}"

        headers = self._cos_sign("put", uri, host, secret_id, secret_key)
        req = Request(url, data=content, method="PUT")
        for k, v in headers.items():
            req.add_header(k, v)

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=30, context=ctx) as resp:
                if resp.status == 200:
                    return self.get_url(path)
                self.service._fail(f"COS 上传失败: HTTP {resp.status}")
                return ""
        except Exception as e:
            self.service._fail(f"COS 请求失败: {e}")
            return ""

    async def delete(self, path: str) -> bool:
        bucket = self._get("bucket", required=True)
        region = self._get("region", required=True)
        secret_id = self._get("secret_id", required=True)
        secret_key = self._get("secret_key", required=True)
        if self.service.failed:
            return False

        host = f"{bucket}.cos.{region}.myqcloud.com"
        uri = f"/{path.lstrip('/')}"
        url = f"https://{host}{uri}"

        headers = self._cos_sign("delete", uri, host, secret_id, secret_key)
        req = Request(url, method="DELETE")
        for k, v in headers.items():
            req.add_header(k, v)

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            self.service._fail(f"COS 删除失败: {e}")
            return False

    async def exists(self, path: str) -> bool:
        bucket = self._get("bucket", required=True)
        region = self._get("region", required=True)
        secret_id = self._get("secret_id", required=True)
        secret_key = self._get("secret_key", required=True)
        if self.service.failed:
            return False

        host = f"{bucket}.cos.{region}.myqcloud.com"
        uri = f"/{path.lstrip('/')}"
        url = f"https://{host}{uri}"

        headers = self._cos_sign("head", uri, host, secret_id, secret_key)
        req = Request(url, method="HEAD")
        for k, v in headers.items():
            req.add_header(k, v)

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_url(self, path: str) -> str:
        domain = self._get("domain")
        if domain:
            return f"{domain.rstrip('/')}/{path.lstrip('/')}"
        bucket = self._get("bucket")
        region = self._get("region")
        return f"https://{bucket}.cos.{region}.myqcloud.com/{path.lstrip('/')}"

    @staticmethod
    def _cos_sign(method: str, uri: str, host: str, secret_id: str, secret_key: str) -> dict:
        """腾讯云 COS 请求签名"""
        now = int(time.time())
        key_time = f"{now};{now + 600}"

        sign_key = hmac.new(secret_key.encode(), key_time.encode(), hashlib.sha256).hexdigest()

        http_string = f"{method}\n{uri}\n\nhost={host.lower()}\n"
        sha1_hash = hashlib.sha1(http_string.encode()).hexdigest()
        string_to_sign = f"sha1\n{key_time}\n{sha1_hash}\n"
        signature = hmac.new(sign_key.encode(), string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth = (
            f"q-sign-algorithm=sha1"
            f"&q-ak={secret_id}"
            f"&q-sign-time={key_time}"
            f"&q-key-time={key_time}"
            f"&q-header-list=host"
            f"&q-url-param-list="
            f"&q-signature={signature}"
        )

        return {"Host": host, "Authorization": auth}
