"""
AWS S3 / 兼容存储驱动（MinIO / Cloudflare R2 等）— 纯 HTTP + AWS4 签名

config:
    {
        "access_key": "...",
        "secret_key": "...",
        "bucket": "my-bucket",
        "region": "us-east-1",
        "endpoint": "",        ← 留空用 AWS 默认，填了走自定义（MinIO/R2）
        "domain": ""           ← 自定义访问域名
    }
"""

import hmac
import hashlib
import time
import ssl
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from app.services.base import BaseDriver


class S3Driver(BaseDriver):

    async def upload(self, path: str, content: bytes, visible: bool = True) -> str:
        url, headers = self._build_request("PUT", path, content)
        if self.service.failed:
            return ""

        if visible:
            headers["x-amz-acl"] = "public-read"

        req = Request(url, data=content, method="PUT")
        for k, v in headers.items():
            req.add_header(k, v)

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=30, context=ctx) as resp:
                if resp.status == 200:
                    return self.get_url(path)
                self.service._fail(f"S3 上传失败: HTTP {resp.status}")
                return ""
        except Exception as e:
            self.service._fail(f"S3 请求失败: {e}")
            return ""

    async def delete(self, path: str) -> bool:
        url, headers = self._build_request("DELETE", path)
        if self.service.failed:
            return False

        req = Request(url, method="DELETE")
        for k, v in headers.items():
            req.add_header(k, v)

        try:
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=10, context=ctx) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            self.service._fail(f"S3 删除失败: {e}")
            return False

    async def exists(self, path: str) -> bool:
        url, headers = self._build_request("HEAD", path)
        if self.service.failed:
            return False

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
        host = self._get_host()
        return f"https://{host}/{path.lstrip('/')}"

    def _get_host(self) -> str:
        bucket = self._get("bucket")
        endpoint = self._get("endpoint")
        region = self._get("region") or "us-east-1"
        if endpoint:
            return f"{bucket}.{endpoint.lstrip('https://').lstrip('http://')}"
        return f"{bucket}.s3.{region}.amazonaws.com"

    def _build_request(self, method: str, path: str, content: bytes = b"") -> tuple:
        """构建签名请求"""
        ak = self._get("access_key", required=True)
        sk = self._get("secret_key", required=True)
        bucket = self._get("bucket", required=True)
        region = self._get("region") or "us-east-1"
        if self.service.failed:
            return "", {}

        host = self._get_host()
        uri = f"/{path.lstrip('/')}"
        url = f"https://{host}{uri}"

        now = datetime.now(timezone.utc)
        date_stamp = now.strftime("%Y%m%d")
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        payload_hash = hashlib.sha256(content).hexdigest()

        # AWS4 签名
        canonical = (
            f"{method}\n{uri}\n\n"
            f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n\n"
            f"host;x-amz-content-sha256;x-amz-date\n{payload_hash}"
        )

        scope = f"{date_stamp}/{region}/s3/aws4_request"
        string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"

        def _hmac(key, msg):
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        signing_key = _hmac(_hmac(_hmac(_hmac(f"AWS4{sk}".encode(), date_stamp), region), "s3"), "aws4_request")
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth = f"AWS4-HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature={signature}"

        headers = {
            "Host": host,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": auth,
        }

        return url, headers
