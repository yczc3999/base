"""
C1a 级联删除测试 — 验证关联表 FK + ON DELETE CASCADE 语义

测试策略：SQLite 内存库（显式开启外键约束）+ 模拟 role_menus/admin_user_roles 结构
"""

import pytest
import pytest_asyncio
from sqlalchemy import ForeignKey, Integer, String, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class FKBase(DeclarativeBase):
    pass


class Role(FKBase):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


class Menu(FKBase):
    __tablename__ = "menus"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(50))


class RoleMenu(FKBase):
    __tablename__ = "role_menus"
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    menu_id: Mapped[int] = mapped_column(ForeignKey("menus.id", ondelete="CASCADE"), primary_key=True)


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # SQLite 需显式开启外键约束
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(FKBase.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session


@pytest.mark.asyncio
async def test_fk_blocks_orphan_insert(db):
    """外键阻止插入不存在的 role/menu 引用"""
    db.add(RoleMenu(role_id=999, menu_id=999))
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_delete_role_cascades_to_role_menus(db):
    """删 role → role_menus 自动清理"""
    role = Role(id=1, name="admin")
    menu = Menu(id=1, slug="system")
    db.add(role)
    db.add(menu)
    await db.commit()
    db.add(RoleMenu(role_id=1, menu_id=1))
    await db.commit()

    # 删除 role
    await db.delete(role)
    await db.commit()

    from sqlalchemy import select
    result = await db.execute(select(RoleMenu))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_delete_menu_cascades_to_role_menus(db):
    """删 menu → role_menus 自动清理"""
    role = Role(id=1, name="admin")
    menu = Menu(id=1, slug="system")
    db.add(role)
    db.add(menu)
    await db.commit()
    db.add(RoleMenu(role_id=1, menu_id=1))
    await db.commit()

    await db.delete(menu)
    await db.commit()

    from sqlalchemy import select
    result = await db.execute(select(RoleMenu))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_cascade_keeps_unrelated_rows(db):
    """级联只删关联行，不影响其他关联"""
    role1 = Role(id=1, name="admin")
    role2 = Role(id=2, name="editor")
    menu = Menu(id=1, slug="system")
    db.add_all([role1, role2, menu])
    await db.commit()
    db.add_all([RoleMenu(role_id=1, menu_id=1), RoleMenu(role_id=2, menu_id=1)])
    await db.commit()

    await db.delete(role1)
    await db.commit()

    from sqlalchemy import select
    remaining = (await db.execute(select(RoleMenu))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].role_id == 2
