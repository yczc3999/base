from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Menu, RoleMenu
from app.logics.base import BaseLogic, BizError
from app.utils.helpers import to_tree


class MenuLogic(BaseLogic):
    model = Menu
    cache_prefix = "menu"
    create_rules = {
        "slug": "required|min:2|max:50",
        "label": "required|max:50",
        "type": "required|in:0,1,2",
    }
    edit_rules = {
        "slug": "min:2|max:50",
        "label": "max:50",
    }

    def allowed_filters(self):
        return ["id", "parent_id", "type", "slug", "label", "status", "is_visible"]

    def allowed_sorts(self):
        return ["id", "sort", "created_at"]

    def keyword_fields(self):
        return ["slug", "label", "perms"]

    def before_create(self, data: dict) -> dict:
        """校验层级关系"""
        menu_type = int(data.get("type", 0))
        parent_id = int(data.get("parent_id", 0))

        if menu_type == Menu.Type.DIRECTORY and parent_id != 0:
            raise BizError("目录只能在顶级创建")
        # type=1(菜单) 和 type=0(子目录如日志管理) 的 parent 必须是 type=0 的目录
        # type=2(按钮) 的 parent 必须是 type=1 的菜单
        return data

    async def _validate_parent(self, db: AsyncSession, data: dict):
        """异步校验父级类型"""
        parent_id = int(data.get("parent_id", 0))
        menu_type = int(data.get("type", 0))

        if parent_id == 0:
            return

        parent = await self.get_detail(db, parent_id)
        if not parent:
            raise BizError("父级菜单不存在")

        parent_type = parent.get("type", 0)
        if menu_type == Menu.Type.MENU and parent_type != Menu.Type.DIRECTORY:
            raise BizError("菜单页面只能创建在目录下")
        if menu_type == Menu.Type.BUTTON and parent_type != Menu.Type.MENU:
            raise BizError("按钮权限只能创建在菜单页面下")
        if menu_type == Menu.Type.DIRECTORY and parent_type != Menu.Type.DIRECTORY:
            raise BizError("子目录只能创建在目录下")

    async def create(self, db: AsyncSession, data: dict) -> dict:
        await self._validate_parent(db, data)
        return await super().create(db, data)

    async def modify(self, db: AsyncSession, pk_value, data: dict) -> dict:
        if "parent_id" in data or "type" in data:
            # 合并现有数据做校验
            existing = await self.get_detail(db, pk_value)
            if existing:
                check = {**existing, **data}
                await self._validate_parent(db, check)
        return await super().modify(db, pk_value, data)

    def before_delete(self, pk_value):
        """同步钩子里不能查 DB，用 _check_children 异步校验"""
        pass

    async def do_delete(self, db: AsyncSession, ids: list[int]):
        """覆写删除，增加子菜单校验"""
        for pk_value in ids:
            stmt = select(Menu).where(Menu.parent_id == pk_value)
            result = await db.execute(stmt)
            if result.scalar_one_or_none():
                raise BizError("请先删除子菜单")
        await super().do_delete(db, ids)

    # ==================== 树形查询 ====================

    async def get_tree(self, db: AsyncSession) -> list:
        """获取完整菜单树（管理菜单用，包含所有状态）"""
        stmt = select(Menu).order_by(Menu.sort.asc(), Menu.id.asc())
        result = await db.execute(stmt)
        rows = [self.format_data(row) for row in result.scalars().all()]
        return to_tree(rows)

    async def get_tree_by_role_ids(self, db: AsyncSession, role_ids: list[int]) -> list:
        """获取指定角色的菜单树（type=0 和 type=1，status=1）"""
        if not role_ids:
            return []
        stmt = (
            select(Menu)
            .join(RoleMenu, RoleMenu.menu_id == Menu.id)
            .where(RoleMenu.role_id.in_(role_ids))
            .where(Menu.type.in_([Menu.Type.DIRECTORY, Menu.Type.MENU]))
            .where(Menu.status == Menu.Status.ACTIVE)
            .order_by(Menu.sort.asc(), Menu.id.asc())
        )
        result = await db.execute(stmt)
        # 去重（多角色可能有重复菜单）
        seen = set()
        rows = []
        for row in result.scalars().all():
            if row.id not in seen:
                seen.add(row.id)
                rows.append(self.format_data(row))
        return to_tree(rows)

    async def get_perms_by_role_ids(self, db: AsyncSession, role_ids: list[int]) -> list[str]:
        """获取指定角色的权限标识列表（type=2 的 perms）"""
        if not role_ids:
            return []
        stmt = (
            select(Menu.perms)
            .join(RoleMenu, RoleMenu.menu_id == Menu.id)
            .where(RoleMenu.role_id.in_(role_ids))
            .where(Menu.type == Menu.Type.BUTTON)
            .where(Menu.status == Menu.Status.ACTIVE)
            .where(Menu.perms.isnot(None))
            .where(Menu.perms != "")
        )
        result = await db.execute(stmt)
        return list(set(row[0] for row in result.all()))

    async def get_all_perms(self, db: AsyncSession) -> list[str]:
        """获取全部权限标识（超级管理员用）"""
        stmt = (
            select(Menu.perms)
            .where(Menu.type == Menu.Type.BUTTON)
            .where(Menu.status == Menu.Status.ACTIVE)
            .where(Menu.perms.isnot(None))
            .where(Menu.perms != "")
        )
        result = await db.execute(stmt)
        return list(set(row[0] for row in result.all()))


menu_logic = MenuLogic()
