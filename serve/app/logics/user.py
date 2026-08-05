from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User
from app.logics.base import BaseLogic
from app.utils.password import hash_password, verify_password
from app.utils.token import revoke_all_tokens


class UserLogic(BaseLogic):
    model = User
    cache_prefix = "user"
    cache_fields = ["username"]
    except_keys = ["password"]
    create_rules = {
        "username": "required|min:3|max:20|alpha_num",
        "password": "required|min:6",
    }
    edit_rules = {
        "username": "min:3|max:20|alpha_num",
        "nickname": "max:20",
    }

    def allowed_filters(self):
        return ["id", "username", "nickname", "email", "phone", "status", "created_at"]

    def allowed_sorts(self):
        return ["id", "created_at", "updated_at", "status"]

    def keyword_fields(self):
        return ["username", "nickname", "email", "phone"]

    def before_create(self, data: dict) -> dict:
        # S1 守卫: 禁止 mass-assignment 改 token_version (users 无 is_super_admin 列)
        data.pop("token_version", None)
        if "password" in data and data["password"]:
            data["password"] = hash_password(data["password"])
        return data

    def before_edit(self, data: dict) -> dict:
        # S1 守卫: 禁止 mass-assignment 改 token_version
        data.pop("token_version", None)
        if "password" in data and data["password"]:
            data["password"] = hash_password(data["password"])
        else:
            data.pop("password", None)
        return data

    async def modify(self, db: AsyncSession, pk_value, data: dict) -> dict:
        # 禁用用户 → 立即撤销全部 client session (否则已有 token 到期前仍有效)
        result = await super().modify(db, pk_value, data)
        if data.get("status") == User.Status.DISABLED:
            await revoke_all_tokens("client", pk_value)
        return result

    async def verify_login(self, db: AsyncSession, username: str, password: str) -> dict | None:
        user = await self.get_by_field(db, "username", username)
        if not user:
            return None
        if user.get("status") != User.Status.ACTIVE:
            return None
        if not verify_password(password, user["password"]):
            return None
        return user


user_logic = UserLogic()
