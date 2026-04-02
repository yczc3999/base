"""
阿里云 OSS 驱动 — 纯 HTTP + OSS4-HMAC-SHA256 签名（V4）

文档: https://help.aliyun.com/zh/oss/developer-reference/recommend-to-use-signature-version-4
V1 签名已于 2025-09 起逐步停用，必须用 V4。

config:
    {
        "access_key_id": "...",
        "access_key_secret": "...",
        "bucket": "my-bucket",
        "region": "cn-hangzhou",
        "endpoint": "oss-cn-hangzhou.aliyuncs.com",
        "domain": ""  ← 可选，自定义 CDN 域名
    }
"""

import hmac
import hashlib
import ssl
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from app.services.base import BaseDriver


class AliyunOssDriver(BaseDriver):

    def _sign_v4(self, method: str, path: str, headers_dict: dict) -> dict:
        """
        OSS4-HMAC-SHA256 签名

        返回需要添加到请求的 headers（含 Authorization）
        """
        ak_id = self._get("access_key_id", required=True)
        ak_secret = self._get("access_key_secret", required=True)
        bucket = self._get("bucket", required=True)
        region = self._get("region") or self._guess_region()
        if self.service.failed:
            return {}

        now = datetime.now(timezone.utc)
        date_stamp = now.strftime("%Y%m%d")
        iso_date = now.strftime("%Y%m%dT%H%M%SZ")
        host = f"{bucket}.{self._get('endpoint')}"
        resource = f"/{path.lstrip('/')}"

        # 必填头
        headers_dict["Host"] = host
        headers_dict["x-acs-date"] = iso_date
        headers_dict["x-oss-content-sha256"] = "UNSIGNED-PAYLOAD"

        # CanonicalHeaders（小写排序）
        canonical_headers_map = {}
        for k, v in headers_dict.items():
            lk = k.lower()
            if lk.startswith("x-oss-") or lk.startswith("x-acs-") or lk in ("host", "content-type"):
                canonical_headers_map[lk] = str(v).strip()

        sorted_keys = sorted(canonical_headers_map.keys())
        canonical_headers = "".join(f"{k}:{canonical_headers_map[k]}\n" for k in sorted_keys)
        additional_headers = ";".join(k for k in sorted_keys if not k.startswith("x-oss-") and k != "host" and k != "content-type")

        # CanonicalRequest
        canonical = f"{method}\n{resource}\n\n{canonical_headers}\n{';'.join(sorted_keys)}\nUNSIGNED-PAYLOAD"

        # Scope
        scope = f"{date_stamp}/{region}/oss/aliyun_v4_request"

        # StringToSign
        string_to_sign = f"OSS4-HMAC-SHA256\n{iso_date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"

        # SigningKey（4 级派生）
        def _hmac(key, msg):
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        date_key = _hmac(f"aliyun_v4{ak_secret}".encode(), date_stamp)
        date_region_key = _hmac(date_key, region)
        date_region_service_key = _hmac(date_region_key, "oss")
        signing_key = _hmac(date_region_service_key, "aliyun_v4_request")

        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth = f"OSS4-HMAC-SHA256 Credential={ak_id}/{scope}, AdditionalHeaders={additional_headers}, Signature={signature}"

        headers_dict["Authorization"] = auth
        return headers_dict

    def _guess_region(self) -> str:
        """从 endpoint 推导 region"""
        endpoint = self._get("endpoint") or ""
        # oss-cn-hangzhou.aliyuncs.com → cn-hangzhou
        parts = endpoint.replace(".aliyuncs.com", "").split("-", 1)
        return parts[1] if len(parts) > 1 else "cn-hangzhou"

    def _do_request(self, method: str, path: str, data: bytes = None, extra_headers: dict = None) -> any:
        """发送签名请求"""
        bucket = self._get("bucket", required=True)
        endpoint = self._get("endpoint", required=True)
        if self.service.failed:
            return None

        headers = extra_headers or {}
        headers = self._sign_v4(method, path, headers)
        if self.service.failed:
            return None

        url = f"https://{bucket}.{endpoint}/{path.lstrip('/')}"
        req = Request(url, data=data, method=method)
        for k, v in headers.items():
            req.add_header(k, v)

        ctx = ssl.create_default_context()
        return urlopen(req, timeout=30, context=ctx)

    async def upload(self, path: str, content: bytes, visible: bool = True) -> str:
        headers = {"Content-Type": "application/octet-stream"}
        if visible:
            headers["x-oss-object-acl"] = "public-read"
        else:
            headers["x-oss-object-acl"] = "private"

        try:
            resp = self._do_request("PUT", path, content, headers)
            if resp and resp.status in (200, 201):
                return self.get_url(path)
            self.service._fail(f"OSS 上传失败: HTTP {resp.status if resp else 'N/A'}")
            return ""
        except Exception as e:
            self.service._fail(f"OSS 请求失败: {e}")
            return ""

    async def delete(self, path: str) -> bool:
        try:
            resp = self._do_request("DELETE", path)
            return resp is not None and resp.status in (200, 204)
        except Exception as e:
            self.service._fail(f"OSS 删除失败: {e}")
            return False

    async def exists(self, path: str) -> bool:
        try:
            resp = self._do_request("HEAD", path)
            return resp is not None and resp.status == 200
        except Exception:
            return False

    def get_url(self, path: str) -> str:
        domain = self._get("domain")
        if domain:
            return f"{domain.rstrip('/')}/{path.lstrip('/')}"
        bucket = self._get("bucket")
        endpoint = self._get("endpoint")
        return f"https://{bucket}.{endpoint}/{path.lstrip('/')}"
