from app.models import AdminLoginLog
from app.logics.base import BaseLogic
from app.services.database import async_session


class AdminLoginLogLogic(BaseLogic):
    model = AdminLoginLog
    cache_prefix = ""

    def allowed_filters(self):
        return ["id", "user_id", "username", "ip", "status", "created_at"]

    def allowed_sorts(self):
        return ["id", "created_at"]

    def keyword_fields(self):
        return ["username", "ip", "remark"]

    def export_header_map(self):
        return {
            "id": "ID",
            "username": "用户名",
            "ip": "IP 地址",
            "status": "状态",
            "remark": "备注",
            "created_at": "登录时间",
        }

    def format_export_row(self, row, context=None):
        status = row.get("status")
        row["status"] = "成功" if status == 1 or status == "1" else "失败"
        return row

    async def record(
        self,
        user_id: int,
        username: str,
        ip: str,
        user_agent: str | None = None,
        status: int = 1,
        remark: str | None = None,
    ):
        try:
            async with async_session() as db:
                log = AdminLoginLog(
                    user_id=user_id or 0,
                    username=username or "",
                    ip=ip,
                    user_agent=user_agent,
                    status=status,
                    remark=remark,
                )
                db.add(log)
                await db.commit()
        except Exception:
            pass


admin_login_log_logic = AdminLoginLogLogic()
