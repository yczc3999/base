from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Role, RoleMenu, AdminUserRole
from app.logics.base import BaseLogic, BizError
from app.services.redis import cache_del
from app.config import settings


class RoleLogic(BaseLogic):
    model = Role
    cache_prefix = "role"
    create_rules = {
        "name": "required|min:2|max:50|alpha_num",
        "label": "required|max:50",
    }
    edit_rules = {
        "name": "min:2|max:50|alpha_num",
        "label": "max:50",
    }

    def allowed_filters(self):
        return ["id", "name", "label", "status"]

    def allowed_sorts(self):
        return ["id", "sort", "created_at"]

    def keyword_fields(self):
        return ["name", "label"]

    async def do_delete(self, db: AsyncSession, ids: list[int]):
        """覆写删除，校验关联用户"""
        for pk_value in ids:
            stmt = select(AdminUserRole).where(AdminUserRole.role_id == pk_value)
            result = await db.execute(stmt)
            if result.scalar_one_or_none():
                raise BizError("该角色下有关联用户，无法删除")
        await super().do_delete(db, ids)

    def after_delete(self, pk_value):
        """删除后清理 role_menus（同步标记，实际清理在 do_delete 中）"""
        pass

    async def do_delete(self, db: AsyncSession, ids: list[int]):
        """覆写删除：校验关联 + 清理 role_menus"""
        for pk_value in ids:
            # 校验关联用户
            stmt = select(AdminUserRole).where(AdminUserRole.role_id == pk_value)
            result = await db.execute(stmt)
            if result.scalar_one_or_none():
                raise BizError("该角色下有关联用户，无法删除")

        # 先删关联
        for pk_value in ids:
            await db.execute(delete(RoleMenu).where(RoleMenu.role_id == pk_value))

        await super().do_delete(db, ids)

    # ==================== 权限分配 ====================

    async def assign_menus(self, db: AsyncSession, role_id: int, menu_ids: list[int]):
        """分配菜单权限：全量覆盖"""
        # 校验角色存在
        role = await self.get_detail(db, role_id)
        self.assert_true(role is not None, "角色不存在")

        # 删旧
        await db.execute(delete(RoleMenu).where(RoleMenu.role_id == role_id))

        # 插新
        for menu_id in menu_ids:
            db.add(RoleMenu(role_id=role_id, menu_id=menu_id))

        await db.commit()

        # 清除该角色下所有用户的权限缓存
        await self._clear_role_perms_cache(db, role_id)

    async def get_menu_ids(self, db: AsyncSession, role_id: int) -> list[int]:
        """获取角色已分配的菜单 ID 列表"""
        stmt = select(RoleMenu.menu_id).where(RoleMenu.role_id == role_id)
        result = await db.execute(stmt)
        return [row[0] for row in result.all()]

    async def get_roles_by_user(self, db: AsyncSession, user_id: int) -> list[dict]:
        """获取用户的角色列表"""
        stmt = (
            select(Role)
            .join(AdminUserRole, AdminUserRole.role_id == Role.id)
            .where(AdminUserRole.admin_user_id == user_id)
            .where(Role.status == Role.Status.ACTIVE)
        )
        result = await db.execute(stmt)
        return [self.format_data(row) for row in result.scalars().all()]

    async def _clear_role_perms_cache(self, db: AsyncSession, role_id: int):
        """清除该角色下所有用户的权限缓存"""
        stmt = select(AdminUserRole.admin_user_id).where(AdminUserRole.role_id == role_id)
        result = await db.execute(stmt)
        user_ids = [row[0] for row in result.all()]
        for uid in user_ids:
            await cache_del(f"{settings.APP_NAME}:user_perms:{uid}")


role_logic = RoleLogic()
