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
