"""Dict / DictItem 逻辑.

dict 是低变更的枚举数据: 前端通过公开端点 `GET /api/dict/items?type=X`
按 type_name 拉取启用项。结果 Redis 永久缓存, dict / dict_item 任何
变更时主动失效 (与 settings 同模式)。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.base import BaseLogic
from app.models.dict import Dict, DictItem
from app.services.redis import cache_get, cache_set, cache_del_pattern

# 按 type_name 的项列表缓存前缀 (永久缓存, 变更时主动清)
ITEMS_CACHE_PREFIX = "dict:items:"


async def _invalidate_items_cache():
    """任何 dict/dict_item 变更后清空全部项缓存 (低频数据, 全清最简单可靠)."""
    await cache_del_pattern(f"{ITEMS_CACHE_PREFIX}*")


class DictLogic(BaseLogic):
    model = Dict
    cache_prefix = "dict"

    create_rules = {
        "type_name": "required|max:50",
        "description": "max:200",
        "status": "in:0,1",
    }
    edit_rules = create_rules

    def allowed_filters(self):
        return ["id", "type_name", "status"]

    def allowed_sorts(self):
        return ["id", "type_name", "created_at"]

    def keyword_fields(self):
        return ["type_name", "description"]

    def before_create(self, data: dict) -> dict:
        # S4 修复: 时间戳由 DB server_default 生成, 禁止客户端伪造
        data.pop("created_at", None)
        data.pop("updated_at", None)
        return data

    def before_edit(self, data: dict) -> dict:
        data.pop("created_at", None)
        data.pop("updated_at", None)
        return data

    async def create(self, db: AsyncSession, data: dict) -> dict:
        result = await super().create(db, data)
        await _invalidate_items_cache()
        return result

    async def modify(self, db: AsyncSession, pk_value, data: dict) -> dict:
        result = await super().modify(db, pk_value, data)
        await _invalidate_items_cache()
        return result

    async def do_delete(self, db: AsyncSession, ids: list[int]):
        await super().do_delete(db, ids)
        await _invalidate_items_cache()

    # ---- 项查询 (公开端点用) ----

    async def get_items_by_type(self, db: AsyncSession, type_name: str) -> list[dict]:
        """按 type_name 返回启用项 [{value, label}], 永久缓存.

        未知类型或全部禁用时返回空列表 (同样缓存, 后续创建/启用时失效)。
        """
        cache_key = f"{ITEMS_CACHE_PREFIX}{type_name}"
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached

        dict_stmt = select(Dict).where(
            Dict.type_name == type_name, Dict.status == Dict.Status.ACTIVE
        )
        d = (await db.execute(dict_stmt)).scalar_one_or_none()
        if d is None:
            await cache_set(cache_key, [])
            return []

        item_stmt = (
            select(DictItem)
            .where(DictItem.dict_id == d.id, DictItem.status == 1)
            .order_by(DictItem.sort, DictItem.id)
        )
        items = (await db.execute(item_stmt)).scalars().all()
        data = [{"value": i.value, "label": i.label} for i in items]
        await cache_set(cache_key, data)
        return data


class DictItemLogic(BaseLogic):
    model = DictItem
    cache_prefix = "dict_item"

    create_rules = {
        "dict_id": "required|integer",
        "value": "required|max:100",
        "label": "required|max:100",
        "sort": "integer",
        "status": "in:0,1",
    }
    edit_rules = create_rules

    def allowed_filters(self):
        return ["id", "dict_id", "status"]

    def allowed_sorts(self):
        return ["id", "sort", "created_at"]

    def keyword_fields(self):
        return ["value", "label"]

    def before_create(self, data: dict) -> dict:
        # S4 修复: 时间戳由 DB server_default 生成, 禁止客户端伪造
        data.pop("created_at", None)
        data.pop("updated_at", None)
        return data

    def before_edit(self, data: dict) -> dict:
        data.pop("created_at", None)
        data.pop("updated_at", None)
        return data

    async def create(self, db: AsyncSession, data: dict) -> dict:
        result = await super().create(db, data)
        await _invalidate_items_cache()
        return result

    async def modify(self, db: AsyncSession, pk_value, data: dict) -> dict:
        result = await super().modify(db, pk_value, data)
        await _invalidate_items_cache()
        return result

    async def do_delete(self, db: AsyncSession, ids: list[int]):
        await super().do_delete(db, ids)
        await _invalidate_items_cache()


dict_logic = DictLogic()
dict_item_logic = DictItemLogic()
