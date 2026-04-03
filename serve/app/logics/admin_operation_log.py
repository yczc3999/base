import json
from app.models import AdminOperationLog
from app.logics.base import BaseLogic
from app.services.database import async_session


class AdminOperationLogLogic(BaseLogic):
    model = AdminOperationLog
    cache_prefix = ""

    def allowed_filters(self):
        return ["id", "user_id", "username", "module", "action", "method", "ip", "status_code", "created_at"]

    def allowed_sorts(self):
        return ["id", "created_at", "duration"]

    def keyword_fields(self):
        return ["username", "module", "action", "url", "ip"]

    def export_header_map(self):
        return {
            "id": "ID",
            "username": "操作用户",
            "module": "模块",
            "action": "操作",
            "method": "方法",
            "url": "URL",
            "ip": "IP 地址",
            "status_code": "状态码",
            "duration": "耗时(ms)",
            "created_at": "操作时间",
        }

    async def record(
        self,
        user_id: int,
        username: str,
        module: str,
        action: str,
        method: str,
        url: str,
        params: dict | None,
        ip: str,
        user_agent: str | None = None,
        status_code: int = 0,
        duration: int = 0,
    ):
        """异步写入操作日志"""
        if params:
            params = {k: ("***" if "password" in k.lower() else v) for k, v in params.items()}

        try:
            async with async_session() as db:
                log = AdminOperationLog(
                    user_id=user_id or 0,
                    username=username or "",
                    module=module,
                    action=action,
                    method=method,
                    url=url,
                    params=json.dumps(params, ensure_ascii=False, default=str) if params else None,
                    ip=ip,
                    user_agent=user_agent,
                    status_code=status_code,
                    duration=duration,
                )
                db.add(log)
                await db.commit()
        except Exception:
            pass


admin_operation_log_logic = AdminOperationLogLogic()
