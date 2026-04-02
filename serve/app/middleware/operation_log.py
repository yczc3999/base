"""
操作日志中间件

自动记录 admin 端 POST 请求的操作日志（含耗时、参数脱敏）。

注意：使用 Starlette 的 receive 缓存机制，避免 body 被消费后路由读不到。
"""

import json
import asyncio
import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from app.logics.admin_operation_log import admin_operation_log_logic
from app.utils.token import verify_token
from app.utils.helpers import get_client_ip


class OperationLogMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # 只记录 admin 端的写操作
        if not path.startswith("/api/admin/") or request.method == "GET":
            return await call_next(request)

        # 缓存 body（Starlette 的 request.body() 内部会缓存，后续 request.json() 可复用）
        # 但中间件和路由在不同的 scope 中，需要确保 body 被缓存到 request._body
        params = {}
        try:
            if request.method == "POST":
                body = await request.body()  # Starlette 会缓存到 request._body
                params = json.loads(body) if body else {}
        except Exception:
            pass

        # 解析模块和动作
        parts = path.replace("/api/admin/", "").strip("/").split("/")
        module = parts[0] if len(parts) > 0 else ""
        action = parts[1] if len(parts) > 1 else ""

        # 从 token 读用户信息
        user_id = 0
        username = ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                user_info = await verify_token(auth_header[7:])
                if user_info:
                    user_id = user_info.get("user_id", 0)
                    username = user_info.get("username", "")
            except Exception:
                pass

        start = time.monotonic()
        response = await call_next(request)
        duration = int((time.monotonic() - start) * 1000)

        ip = get_client_ip(request)
        ua = request.headers.get("user-agent", "")

        asyncio.create_task(
            admin_operation_log_logic.record(
                user_id=user_id,
                username=username,
                module=module,
                action=action,
                method=request.method,
                url=path,
                params=params,
                ip=ip,
                user_agent=ua,
                status_code=response.status_code,
                duration=duration,
            )
        )

        return response
