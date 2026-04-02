from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Setting
from app.logics.base import BaseLogic
from app.services.redis import cache_get, cache_set, cache_del

SETTINGS_CACHE_KEY = "settings:all"


class SettingLogic(BaseLogic):
    model = Setting
    cache_prefix = "setting"

    def allowed_filters(self):
        return ["id", "category", "name", "created_at"]

    def allowed_sorts(self):
        return ["id", "category", "created_at"]

    def keyword_fields(self):
        return ["category", "name", "label", "remark"]

    async def get_all(self, db: AsyncSession) -> dict:
        """获取全部配置，按 category 分组"""
        cached = await cache_get(SETTINGS_CACHE_KEY)
        if cached is not None:
            return cached

        stmt = select(Setting)
        result = await db.execute(stmt)
        rows = result.scalars().all()
        data = {}
        for row in rows:
            if row.category not in data:
                data[row.category] = {}
            data[row.category][row.name] = row.value
        await cache_set(SETTINGS_CACHE_KEY, data)
        return data

    async def set_many(self, db: AsyncSession, items: dict):
        """
        批量设置配置项
        items 格式: {"category": {"name": "value", ...}, ...}
        """
        for category, kv in items.items():
            if not isinstance(kv, dict):
                continue
            for name, value in kv.items():
                stmt = select(Setting).where(
                    and_(Setting.category == category, Setting.name == name)
                )
                result = await db.execute(stmt)
                existing = result.scalar_one_or_none()
                if existing:
                    existing.value = str(value) if value is not None else ""
                else:
                    db.add(
                        Setting(
                            category=category,
                            name=name,
                            value=str(value) if value is not None else "",
                        )
                    )
        await db.commit()
        await cache_del(SETTINGS_CACHE_KEY)

    async def get(
        self, db: AsyncSession, category: str, name: str, default: str = None
    ) -> str | None:
        """获取单个配置值"""
        all_settings = await self.get_all(db)
        return all_settings.get(category, {}).get(name, default)

    async def set(
        self, db: AsyncSession, category: str, name: str, value: str, label: str = None
    ):
        """设置单个配置项"""
        stmt = select(Setting).where(
            and_(Setting.category == category, Setting.name == name)
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = str(value) if value is not None else ""
            if label is not None:
                existing.label = label
        else:
            db.add(
                Setting(
                    category=category,
                    name=name,
                    value=str(value) if value is not None else "",
                    label=label,
                )
            )
        await db.commit()
        await cache_del(SETTINGS_CACHE_KEY)


setting_logic = SettingLogic()
