from datetime import datetime
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import AdminUser, AdminUserRole
from app.logics.base import BaseLogic
from app.utils.password import hash_password, verify_password
from app.services.redis import cache_get, cache_set, cache_del
from app.config import settings


class AdminUserLogic(BaseLogic):
    model = AdminUser
    cache_prefix = "admin_user"
    cache_fields = ["username", "email"]
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
        return ["id", "username", "nickname", "email", "phone", "status", "is_super_admin", "created_at"]

    def allowed_sorts(self):
        return ["id", "created_at", "updated_at", "status"]

    def keyword_fields(self):
        return ["username", "nickname", "email", "phone"]

    def before_create(self, data: dict) -> dict:
        # 超级管理员标记和 token 版本号不允许通过接口设置
        data.pop("is_super_admin", None)
        data.pop("token_version", None)
        if "password" in data and data["password"]:
            data["password"] = hash_password(data["password"])
            # P2-2: 记录密码修改时间 (过期策略用)
            data["password_changed_at"] = datetime.now()
        return data

    def before_edit(self, data: dict) -> dict:
        # 超级管理员标记和 token 版本号不允许通过接口修改
        data.pop("is_super_admin", None)
        data.pop("token_version", None)
        if "password" in data and data["password"]:
            data["password"] = hash_password(data["password"])
            # P2-2: 密码变更时刷新修改时间
            data["password_changed_at"] = datetime.now()
        else:
            data.pop("password", None)
        return data

    async def save(self, db: AsyncSession, data: dict) -> dict:
        """P2-2 密码策略: 设置/修改密码时校验强度（rules 从 settings 读）."""
        if data.get("password"):
            from app.utils.password_policy import get_policy_rules, validate_password_strength
            rules = await get_policy_rules(db)
            validate_password_strength(data["password"], rules)
        return await super().save(db, data)

    # ==================== 登录（支持用户名 / 邮箱） ====================

    async def verify_login(self, db: AsyncSession, login_name: str, password: str) -> dict | None:
        """
        验证登录

        :param login_name: 用户名或邮箱（包含 @ 走邮箱查询）
        :param password:   明文密码
        """
        if "@" in login_name:
            user = await self.get_by_field(db, "email", login_name)
        else:
            user = await self.get_by_field(db, "username", login_name)

        if not user:
            return None
        if user.get("status") != AdminUser.Status.ACTIVE:
            return None
        if not verify_password(password, user["password"]):
            return None
        return user

    async def change_password(self, db: AsyncSession, user_id: int, old_password: str, new_password: str) -> bool:
        user = await self.get_by_field(db, "id", user_id)
        if not user:
            return False
        if not verify_password(old_password, user["password"]):
            return False
        # 传明文, before_edit 会 hash
        await self.save(db, {"id": user_id, "password": new_password})
        return True

    # ==================== 角色管理 ====================

    async def assign_roles(self, db: AsyncSession, user_id: int, role_ids: list[int]):
        """分配角色：全量覆盖"""
        await db.execute(delete(AdminUserRole).where(AdminUserRole.admin_user_id == user_id))
        for role_id in role_ids:
            db.add(AdminUserRole(admin_user_id=user_id, role_id=role_id))
        await db.commit()
        # 清除权限缓存
        await cache_del(f"{settings.APP_NAME}:user_perms:{user_id}")

    async def get_role_ids(self, db: AsyncSession, user_id: int) -> list[int]:
        """获取用户已分配的角色 ID 列表"""
        stmt = select(AdminUserRole.role_id).where(AdminUserRole.admin_user_id == user_id)
        result = await db.execute(stmt)
        return [row[0] for row in result.all()]

    # ==================== 权限查询 ====================

    async def get_user_perms(self, db: AsyncSession, user_id: int, is_super: bool = False) -> list[str]:
        """
        获取用户权限列表（走 Redis 缓存）

        超级管理员返回全部权限，普通管理员按角色合并。
        """
        from app.logics.menu import menu_logic

        if is_super:
            return await menu_logic.get_all_perms(db)

        # WP-07A Admin Read API 使用 PostgreSQL READ ONLY request UoW。该路径必须
        # 与业务查询处于同一 snapshot，且 GET 不得产生 Redis cache 写；因此直接从
        # RBAC 事实表读取，也不触发 cache_get/network。
        info = getattr(db, "info", None)
        if info is None and hasattr(db, "sync_session"):
            info = db.sync_session.info
        if info and info.get("admin_read_only"):
            role_ids = await self.get_role_ids(db, user_id)
            return await menu_logic.get_perms_by_role_ids(db, role_ids)

        # 查缓存
        cache_key = f"{settings.APP_NAME}:user_perms:{user_id}"
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached

        # 查 DB
        role_ids = await self.get_role_ids(db, user_id)
        perms = await menu_logic.get_perms_by_role_ids(db, role_ids)

        # 写缓存
        await cache_set(cache_key, perms, settings.TOKEN_EXPIRES_IN)
        return perms

    async def get_user_menus(self, db: AsyncSession, user_id: int, is_super: bool = False) -> dict:
        """
        获取用户菜单树 + 权限列表

        :return: {"menus": [...], "permissions": [...]}
        """
        from app.logics.menu import menu_logic

        if is_super:
            menus = await menu_logic.get_tree(db)
            perms = await menu_logic.get_all_perms(db)
            # 超管的树也只返回 type=0/1 且 status=1 的
            menus = _filter_active_menus(menus)
        else:
            role_ids = await self.get_role_ids(db, user_id)
            menus = await menu_logic.get_tree_by_role_ids(db, role_ids)
            perms = await menu_logic.get_perms_by_role_ids(db, role_ids)

        return {"menus": menus, "permissions": perms}


def _filter_active_menus(tree: list) -> list:
    """递归过滤 status=1 且 type!=2 的菜单"""
    result = []
    for node in tree:
        if node.get("status") != 1:
            continue
        if node.get("type") == 2:  # 按钮不进菜单树
            continue
        children = node.get("children", [])
        if children:
            node["children"] = _filter_active_menus(children)
        result.append(node)
    return result


admin_user_logic = AdminUserLogic()
