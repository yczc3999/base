"""
七牛云存储驱动 — 纯 HTTP + HMAC-SHA1 签名

config:
    {
        "access_key": "...",
        "secret_key": "...",
        "bucket": "my-bucket",
        "domain": "https://cdn.example.com"
    }
"""

import hmac
import hashlib
import base64
import json
import ssl
from urllib.request import Request, urlopen

from app.services.base import BaseDriver


class QiniuDriver(BaseDriver):

    async def upload(self, path: str, content: bytes, visible: bool = True) -> str:
        ak = self._get("access_key", required=True)
        sk = self._get("secret_key", required=True)
        bucket = self._get("bucket", required=True)
        if self.service.failed:
            return ""

        # 生成上传 token
        import time as _time
        policy = json.dumps({"scope": f"{bucket}:{path}", "deadline": int(_time.time()) + 600})  # 10 分钟过期
        encoded_policy = base64.urlsafe_b64encode(policy.encode()).decode()
        sign = base64.urlsafe_b64encode(
            hmac.new(sk.encode(), encoded_policy.encode(), hashlib.sha1).digest()
        ).decode()
        upload_token = f"{ak}:{sign}:{encoded_policy}"

        # multipart 上传
        boundary = "----QiniuBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="key"\r\n\r\n{path}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="token"\r\n\r\n{upload_token}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.split("/")[-1]}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()

        upload_url = self._get("upload_url") or "https://up.qiniup.com/"
        req = Request(upload_url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=30, context=ctx) as resp:
                if resp.status == 200:
                    return self.get_url(path)
                self.service._fail(f"七牛上传失败: HTTP {resp.status}")
                return ""
        except Exception as e:
            self.service._fail(f"七牛请求失败: {e}")
            return ""

    async def delete(self, path: str) -> bool:
        ak = self._get("access_key", required=True)
        sk = self._get("secret_key", required=True)
        bucket = self._get("bucket", required=True)
        if self.service.failed:
            return False

        entry = base64.urlsafe_b64encode(f"{bucket}:{path}".encode()).decode()
        url = f"https://rs.qiniu.com/delete/{entry}"

        sign_str = f"/delete/{entry}\n"
        sign = base64.urlsafe_b64encode(
            hmac.new(sk.encode(), sign_str.encode(), hashlib.sha1).digest()
        ).decode()

        req = Request(url, method="POST")
        req.add_header("Authorization", f"QBox {ak}:{sign}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                return resp.status == 200
        except Exception as e:
            self.service._fail(f"七牛删除失败: {e}")
            return False

    async def exists(self, path: str) -> bool:
        ak = self._get("access_key", required=True)
        sk = self._get("secret_key", required=True)
        bucket = self._get("bucket", required=True)
        if self.service.failed:
            return False

        entry = base64.urlsafe_b64encode(f"{bucket}:{path}".encode()).decode()
        url = f"https://rs.qiniu.com/stat/{entry}"

        sign_str = f"/stat/{entry}\n"
        sign = base64.urlsafe_b64encode(
            hmac.new(sk.encode(), sign_str.encode(), hashlib.sha1).digest()
        ).decode()

        req = Request(url, method="POST")
        req.add_header("Authorization", f"QBox {ak}:{sign}")

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_url(self, path: str) -> str:
        domain = self._get("domain", required=True)
        return f"{domain.rstrip('/')}/{path.lstrip('/')}"
