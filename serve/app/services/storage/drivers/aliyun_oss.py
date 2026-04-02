"""
阿里云 OSS 驱动 — 纯 HTTP + HMAC-SHA1 签名（V1）

注意: V1 签名对 2025-03 前创建的 Bucket 仍然有效。
     新 Bucket（2025-09 后）需要 V4 签名，届时升级此驱动。

config:
    {
        "access_key_id": "...",
        "access_key_secret": "...",
        "bucket": "my-bucket",
        "endpoint": "oss-cn-beijing.aliyuncs.com",
        "domain": ""
    }
"""

import hmac
import hashlib
import base64
import ssl
from email.utils import formatdate
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from app.services.base import BaseDriver


class AliyunOssDriver(BaseDriver):

    async def upload(self, path: str, content: bytes, visible: bool = True) -> str:
        bucket = self._get("bucket", required=True)
        endpoint = self._get("endpoint", required=True)
        ak_id = self._get("access_key_id", required=True)
        ak_secret = self._get("access_key_secret", required=True)
        if self.service.failed:
            return ""

        host = f"{bucket}.{endpoint}"
        ct = "application/octet-stream"
        date = formatdate(usegmt=True)
        acl = "public-read" if visible else "private"
        resource = f"/{bucket}/{path.lstrip('/')}"

        string_to_sign = f"PUT\n\n{ct}\n{date}\nx-oss-object-acl:{acl}\n{resource}"
        signature = base64.b64encode(
            hmac.new(ak_secret.encode(), string_to_sign.encode(), hashlib.sha1).digest()
        ).decode()

        url = f"https://{host}/{path.lstrip('/')}"
        req = Request(url, data=content, method="PUT")
        req.add_header("Content-Type", ct)
        req.add_header("Date", date)
        req.add_header("x-oss-object-acl", acl)
        req.add_header("Authorization", f"OSS {ak_id}:{signature}")

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=30, context=ctx) as resp:
                if resp.status in (200, 201):
                    return self.get_url(path)
                self.service._fail(f"OSS 上传失败: HTTP {resp.status}")
                return ""
        except HTTPError as e:
            self.service._fail(f"OSS 上传失败: HTTP {e.code}")
            return ""
        except Exception as e:
            self.service._fail(f"OSS 请求失败: {e}")
            return ""

    async def delete(self, path: str) -> bool:
        bucket = self._get("bucket", required=True)
        endpoint = self._get("endpoint", required=True)
        ak_id = self._get("access_key_id", required=True)
        ak_secret = self._get("access_key_secret", required=True)
        if self.service.failed:
            return False

        host = f"{bucket}.{endpoint}"
        date = formatdate(usegmt=True)
        resource = f"/{bucket}/{path.lstrip('/')}"
        string_to_sign = f"DELETE\n\n\n{date}\n{resource}"
        signature = base64.b64encode(
            hmac.new(ak_secret.encode(), string_to_sign.encode(), hashlib.sha1).digest()
        ).decode()

        url = f"https://{host}/{path.lstrip('/')}"
        req = Request(url, method="DELETE")
        req.add_header("Date", date)
        req.add_header("Authorization", f"OSS {ak_id}:{signature}")

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            self.service._fail(f"OSS 删除失败: {e}")
            return False

    async def exists(self, path: str) -> bool:
        bucket = self._get("bucket", required=True)
        endpoint = self._get("endpoint", required=True)
        ak_id = self._get("access_key_id", required=True)
        ak_secret = self._get("access_key_secret", required=True)
        if self.service.failed:
            return False

        host = f"{bucket}.{endpoint}"
        date = formatdate(usegmt=True)
        resource = f"/{bucket}/{path.lstrip('/')}"
        string_to_sign = f"HEAD\n\n\n{date}\n{resource}"
        signature = base64.b64encode(
            hmac.new(ak_secret.encode(), string_to_sign.encode(), hashlib.sha1).digest()
        ).decode()

        url = f"https://{host}/{path.lstrip('/')}"
        req = Request(url, method="HEAD")
        req.add_header("Date", date)
        req.add_header("Authorization", f"OSS {ak_id}:{signature}")

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
        endpoint = self._get("endpoint")
        return f"https://{bucket}.{endpoint}/{path.lstrip('/')}"
