"""
HTTP 请求工具 — 异步 HTTP 客户端

用于服务端对外发起 HTTP 请求（调第三方 API、Webhook 等）。

用法：
    from app.utils.http import Http

    # 快捷方式
    data = await Http.get("https://api.example.com/users")
    data = await Http.post("https://api.example.com/users", json={"name": "张三"})

    # 链式调用
    resp = await (Http("https://api.example.com/users")
        .header("X-Api-Key", "xxx")
        .timeout(10)
        .post(json={"name": "张三"}))
"""

import asyncio
from typing import Any
try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

import json
import ssl
from urllib.request import Request, urlopen
from urllib.parse import urlencode


class Http:
    """异步 HTTP 客户端（优先用 httpx，降级用 urllib）"""

    def __init__(self, url: str):
        self._url = url
        self._headers: dict[str, str] = {}
        self._timeout: float = 30

    def header(self, key: str, value: str) -> "Http":
        """设置请求头"""
        self._headers[key] = value
        return self

    def headers(self, headers: dict[str, str]) -> "Http":
        """批量设置请求头"""
        self._headers.update(headers)
        return self

    def timeout(self, seconds: float) -> "Http":
        """设置超时时间（秒）"""
        self._timeout = seconds
        return self

    async def get(self, params: dict = None, **kwargs) -> dict | str | None:
        """发起 GET 请求"""
        return await self._request("GET", params=params, **kwargs)

    async def post(self, json: dict = None, data: dict = None, **kwargs) -> dict | str | None:
        """发起 POST 请求"""
        return await self._request("POST", json=json, data=data, **kwargs)

    async def _request(self, method: str, **kwargs) -> dict | str | None:
        if _HAS_HTTPX:
            return await self._request_httpx(method, **kwargs)
        return await asyncio.to_thread(self._request_urllib, method, **kwargs)

    async def _request_httpx(self, method: str, **kwargs) -> dict | str | None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.request(method, self._url, headers=self._headers, **kwargs)
            try:
                return resp.json()
            except Exception:
                return resp.text

    def _request_urllib(self, method: str, **kwargs) -> dict | str | None:
        """urllib 降级方案（不依赖 httpx）"""
        import json as json_mod
        url = self._url
        body = None
        headers = {**self._headers}

        if kwargs.get("params"):
            url += "?" + urlencode(kwargs["params"])
        if kwargs.get("json"):
            body = json_mod.dumps(kwargs["json"]).encode()
            headers.setdefault("Content-Type", "application/json")
        elif kwargs.get("data"):
            body = urlencode(kwargs["data"]).encode()
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

        req = Request(url, data=body, headers=headers, method=method)
        ctx = ssl.create_default_context()
        with urlopen(req, timeout=self._timeout, context=ctx) as resp:
            text = resp.read().decode()
            try:
                return json_mod.loads(text)
            except Exception:
                return text

    # ---- 静态快捷方法 ----

    @staticmethod
    async def GET(url: str, params: dict = None, headers: dict = None, timeout: float = 30) -> dict | str | None:
        """
        快捷 GET 请求

        data = await Http.GET("https://api.example.com/users", params={"page": 1})
        """
        h = Http(url).timeout(timeout)
        if headers:
            h.headers(headers)
        return await h.get(params=params)

    @staticmethod
    async def POST(url: str, json: dict = None, data: dict = None, headers: dict = None, timeout: float = 30) -> dict | str | None:
        """
        快捷 POST 请求

        data = await Http.POST("https://api.example.com/users", json={"name": "张三"})
        """
        h = Http(url).timeout(timeout)
        if headers:
            h.headers(headers)
        return await h.post(json=json, data=data)
