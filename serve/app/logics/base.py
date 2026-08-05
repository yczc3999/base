import json
from typing import Any, Type
from sqlalchemy import select, func, delete, update, asc, desc, null
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.redis import cache_get, cache_set, cache_del, cache_del_pattern
from app.utils.query import apply_filters, apply_keyword

DEFAULT_TTL = 3600


class BizError(Exception):
    """业务异常"""

    def __init__(self, msg: str = "操作失败", code: int = 400):
        self.msg = msg
        self.code = code
        super().__init__(msg)


class BaseLogic:
    """
    通用 CRUD Logic 基类

    子类配置项：
        model           SQLAlchemy Model 类
        pk_name         主键字段名（默认 "id"）
        cache_prefix    缓存前缀（空字符串 = 不缓存）
        cache_fields    额外缓存字段（如 ["username"]）
        except_keys     输出时过滤的敏感字段
        cache_ttl       缓存过期时间（秒）

    子类可覆写的白名单 / 钩子：
        allowed_filters()   允许过滤的字段白名单
        allowed_sorts()     允许排序的字段白名单
        keyword_fields()    keyword 搜索的目标字段
        format_save_data()  入库前数据格式化
        before_create()     创建前钩子
        before_edit()       编辑前钩子
        before_delete()     删除前钩子
        after_delete()      删除后钩子
        format_data()       单条输出格式化
    """

    model: Type = None
    pk_name: str = "id"
    cache_prefix: str = ""
    cache_fields: list[str] = []
    except_keys: list[str] = []  # 输出时过滤的字段（类级默认）
    cache_ttl: int = DEFAULT_TTL
    bind_user_column: str = (
        ""  # 绑定用户字段（如 "user_id"），非空时 CRUD 自动按当前用户过滤
    )
    create_rules: dict = {}  # 创建时的校验规则（传给 validate）
    edit_rules: dict = {}  # 编辑时的校验规则（传给 validate，partial=True）

    def __init__(self):
        self._except_keys: list[str] | None = None

    # ==================== 白名单（子类覆写） ====================

    def allowed_filters(self) -> list[str] | None:
        """允许过滤的字段白名单，None = 不限制"""
        return None

    def allowed_sorts(self) -> list[str]:
        """允许排序的字段白名单"""
        return [self.pk_name, "created_at", "updated_at"]

    def keyword_fields(self) -> list[str]:
        """keyword 搜索的目标字段列表"""
        return []

    # ==================== except_keys 动态控制 ====================

    def set_except(self, keys: list[str]) -> "BaseLogic":
        """临时设置输出过滤字段（不影响类级默认值）"""
        self._except_keys = keys
        return self

    def disable_except(self) -> "BaseLogic":
        """临时关闭输出过滤（返回所有字段，含敏感字段）"""
        self._except_keys = []
        return self

    def reset_except(self) -> "BaseLogic":
        """恢复为类级默认 except_keys"""
        self._except_keys = None
        return self

    @property
    def _active_except_keys(self) -> list[str]:
        """当前生效的 except_keys（实例级优先，None 时回退到类级）"""
        return self._except_keys if self._except_keys is not None else self.except_keys

    # ==================== 断言 ====================

    @staticmethod
    def assert_true(condition: bool, msg: str = "操作失败", code: int = 400):
        if not condition:
            raise BizError(msg, code)

    @property
    def _has_deleted_at(self) -> bool:
        """模型是否有 deleted_at 列（软删除支持）"""
        return hasattr(self.model, "deleted_at")

    # ==================== 列表 ====================

    async def get_list(
        self, db: AsyncSession, query: dict, user_id: int = None, is_super: bool = False
    ) -> dict:
        """
        分页列表查询

        :param db:        数据库 Session
        :param query:     查询参数（page/pageSize/sortField/sortOrder/filters/keyword）
        :param user_id:   当前用户 ID（bind_user_column 非空时自动过滤）
        :param is_super:  是否超级管理员（True 时绕过 bind_user_column）
        """
        try:
            page = max(1, int(query.get("page", 1)))
        except (ValueError, TypeError):
            page = 1
        try:
            page_size = min(100, max(1, int(query.get("pageSize", 20))))
        except (ValueError, TypeError):
            page_size = 20
        sort_field = query.get("sortField", self.pk_name)
        sort_order = query.get("sortOrder", "desc")

        # filters
        filters = query.get("filters", {})
        if isinstance(filters, str):
            try:
                filters = json.loads(filters)
            except (json.JSONDecodeError, TypeError):
                filters = {}

        conditions = apply_filters(self.model, filters, self.allowed_filters())

        # bind_user_column：自动按当前用户过滤（超级管理员绕过）
        if self.bind_user_column and user_id is not None and not is_super:
            col = getattr(self.model, self.bind_user_column, None)
            if col is not None:
                conditions.append(col == user_id)

        # 软删除过滤：有 deleted_at 列则只显示未删除的记录
        if self._has_deleted_at:
            conditions.append(getattr(self.model, "deleted_at").is_(null()))

        # keyword 搜索
        keyword = query.get("keyword", "")
        if keyword:
            kw_cond = apply_keyword(self.model, keyword, self.keyword_fields())
            if kw_cond is not None:
                conditions.append(kw_cond)

        # 排序白名单校验
        if sort_field not in self.allowed_sorts():
            sort_field = self.pk_name
        col = getattr(self.model, sort_field, None) or getattr(self.model, self.pk_name)
        order = asc(col) if sort_order == "asc" else desc(col)

        # 查询（捕获类型不匹配等数据库错误，返回空结果而非 500）
        stmt = (
            select(self.model)
            .where(*conditions)
            .order_by(order)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        count_stmt = select(func.count()).select_from(self.model).where(*conditions)

        try:
            result = await db.execute(stmt)
            total_result = await db.execute(count_stmt)
        except Exception:
            await db.rollback()
            return {"list": [], "total": 0, "page": page, "pageSize": page_size}

        rows = [self.format_data(row) for row in result.scalars().all()]
        total = total_result.scalar_one()

        return {"list": rows, "total": total, "page": page, "pageSize": page_size}

    # ==================== 详情 ====================

    async def get_detail(
        self, db: AsyncSession, pk_value: Any = None, condition: dict = None
    ) -> dict | None:
        """
        详情查询

        支持两种调用方式：
        1. 按主键查询（走缓存）：get_detail(db, 1)
        2. 按条件查询（不走缓存）：get_detail(db, condition={"username": "admin"})

        :param pk_value:  主键值
        :param condition: 条件字典（如 {"id_card": "370902xxx", "status": 1}）
        """
        # 按条件查询（不走缓存）
        if condition and isinstance(condition, dict):
            conditions = []
            for field, value in condition.items():
                col = getattr(self.model, field, None)
                if col is None:
                    continue
                if isinstance(value, list):
                    conditions.append(col.in_(value))
                else:
                    conditions.append(col == value)
            if not conditions:
                return None
            stmt = select(self.model).where(*conditions)
            result = await db.execute(stmt)
            record = result.scalar_one_or_none()
            if record is None:
                return None
            return self.format_data(record)

        # 按主键查询（走缓存）
        if pk_value is None:
            return None

        cache_key = self._cache_key(self.pk_name, pk_value)
        cached = await cache_get(cache_key)
        if cached is not None:
            return self._strip_sensitive(cached)

        stmt = select(self.model).where(getattr(self.model, self.pk_name) == pk_value)
        # 软删除过滤
        if self._has_deleted_at:
            stmt = stmt.where(getattr(self.model, "deleted_at").is_(null()))
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None

        data = self._to_dict(record, with_sensitive=True)
        await self._set_cache(data)
        return self.format_data(record)

    # ==================== 按字段查询（走缓存，含敏感字段） ====================

    async def get_by_field(
        self, db: AsyncSession, field: str, value: Any
    ) -> dict | None:
        """按字段查询，返回含敏感字段的完整数据（用于内部逻辑如密码校验）"""
        cache_key = self._cache_key(field, value)
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached

        stmt = select(self.model).where(getattr(self.model, field) == value)
        # 软删除过滤
        if self._has_deleted_at:
            stmt = stmt.where(getattr(self.model, "deleted_at").is_(null()))
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None

        data = self._to_dict(record, with_sensitive=True)
        await self._set_cache(data)
        return data

    # ==================== 创建 ====================

    async def create(self, db: AsyncSession, data: dict) -> dict:
        # 自动校验（如果配置了 create_rules）
        if self.create_rules:
            from app.utils.validator import validate

            validate(data, self.create_rules)
        data.pop(self.pk_name, None)
        data = self.format_save_data(data, is_update=False)
        data = self.before_create(data)
        record = self.model(**data)
        db.add(record)
        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            err_msg = str(e)
            if "UniqueViolation" in err_msg or "unique constraint" in err_msg.lower():
                raise BizError("数据已存在（唯一约束冲突）")
            raise BizError(f"创建失败：{err_msg[:200]}")
        await db.refresh(record)
        result = self.format_data(record)
        await self._set_cache(self._to_dict(record, with_sensitive=True))
        return result

    # ==================== 编辑 ====================

    async def modify(self, db: AsyncSession, pk_value: Any, data: dict) -> dict:
        # 自动校验（编辑用 partial 模式，只校验传了的字段）
        if self.edit_rules:
            from app.utils.validator import validate

            validate(data, self.edit_rules, partial=True)
        data = self.format_save_data(data, is_update=True)
        data = self.before_edit(data)

        # 先清旧缓存（读旧值），再更新 DB
        await self._clear_cache(pk_value, db)

        update_data = {}
        for k, v in data.items():
            if k == self.pk_name or v is None:
                continue
            # ISO 字符串 → datetime（防止缓存数据类型不匹配）
            if isinstance(v, str) and k.endswith("_at"):
                from datetime import datetime as dt
                try:
                    v = dt.fromisoformat(v)
                except (ValueError, TypeError):
                    pass
            update_data[k] = v
        stmt = (
            update(self.model)
            .where(getattr(self.model, self.pk_name) == pk_value)
            .values(**update_data)
        )
        try:
            await db.execute(stmt)
            await db.commit()
        except Exception as e:
            await db.rollback()
            err_msg = str(e)
            if "UniqueViolation" in err_msg or "unique constraint" in err_msg.lower():
                raise BizError("数据已存在（唯一约束冲突）")
            raise BizError(f"更新失败：{err_msg[:200]}")
        return await self.get_detail(db, pk_value)

    # ==================== save（兼容 create/modify 合一调用） ====================

    async def save(self, db: AsyncSession, data: dict) -> dict:
        pk_value = data.get(self.pk_name)
        if pk_value:
            return await self.modify(db, pk_value, data)
        return await self.create(db, data)

    # ==================== 删除 ====================

    async def do_delete(self, db: AsyncSession, ids: list[int]):
        for pk_value in ids:
            self.before_delete(pk_value)

            # 软删除：如果 model 有 deleted_at 字段
            if hasattr(self.model, "deleted_at"):
                from datetime import datetime

                stmt = (
                    update(self.model)
                    .where(getattr(self.model, self.pk_name) == pk_value)
                    .values(deleted_at=datetime.now())
                )
                await db.execute(stmt)
            else:
                stmt = delete(self.model).where(
                    getattr(self.model, self.pk_name) == pk_value
                )
                await db.execute(stmt)

            await self._clear_cache(pk_value, db)
            self.after_delete(pk_value)
        await db.commit()

    # ==================== 事务 ====================

    @staticmethod
    async def transaction(db: AsyncSession, callback):
        """事务包装"""
        async with db.begin():
            return await callback()

    # ==================== 生命周期钩子（子类覆写） ====================

    def format_save_data(self, data: dict, is_update: bool = False) -> dict:
        """
        入库前格式化

        创建时：移除空字符串字段（不写入默认值以外的空值）
        编辑时：保留空字符串（允许用户清空字段）
        """
        if is_update:
            return data
        return {k: v for k, v in data.items() if v != ""}

    def before_create(self, data: dict) -> dict:
        return data

    def before_edit(self, data: dict) -> dict:
        return data

    def before_delete(self, pk_value: Any):
        pass

    def after_delete(self, pk_value: Any):
        pass

    # ==================== 输出格式化 ====================

    def format_data(self, record) -> dict:
        """单条输出格式化（子类可覆写增加自定义逻辑）"""
        return self._strip_sensitive(self._to_dict(record))

    def _to_dict(self, record, with_sensitive: bool = False) -> dict:
        """Model → dict，含日期转换"""
        data = {c.key: getattr(record, c.key) for c in record.__table__.columns}
        if not with_sensitive:
            for key in self._active_except_keys:
                data.pop(key, None)
        for k, v in data.items():
            if hasattr(v, "isoformat"):
                data[k] = v.isoformat()
        return data

    def _strip_sensitive(self, data: dict) -> dict:
        keys = self._active_except_keys
        if not keys:
            return data
        return {k: v for k, v in data.items() if k not in keys}

    # ==================== 缓存 ====================

    def _cache_key(self, field: str, value: Any) -> str:
        return f"{self.cache_prefix}:{field}:{value}"

    async def _set_cache(self, data: dict):
        if not self.cache_prefix:
            return
        pk_value = data.get(self.pk_name)
        if pk_value is not None:
            await cache_set(
                self._cache_key(self.pk_name, pk_value), data, self.cache_ttl
            )
        for field in self.cache_fields:
            fv = data.get(field)
            if fv is not None:
                await cache_set(self._cache_key(field, fv), data, self.cache_ttl)

    async def _clear_cache(self, pk_value: Any, db: AsyncSession):
        if not self.cache_prefix:
            return
        stmt = select(self.model).where(getattr(self.model, self.pk_name) == pk_value)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        keys_to_del = [self._cache_key(self.pk_name, pk_value)]
        if record:
            for field in self.cache_fields:
                fv = getattr(record, field, None)
                if fv is not None:
                    keys_to_del.append(self._cache_key(field, fv))
        await cache_del(*keys_to_del)

    async def clear_all_cache(self):
        """清除该 Logic 的所有缓存"""
        if self.cache_prefix:
            await cache_del_pattern(f"{self.cache_prefix}:*")

    # ==================== 导出（子类覆写） ====================

    def export_fields(self) -> list[str]:
        """导出的字段列表（默认：所有列 - except_keys）"""
        all_cols = [c.key for c in self.model.__table__.columns]
        return [f for f in all_cols if f not in self.except_keys]

    def export_header_map(self) -> dict[str, str]:
        """
        导出表头映射：{字段名: 显示名}

        子类必须覆写此方法以启用导出功能。
        返回空 dict 时导出接口会返回"该模块不支持导出"。

        示例：
            return {"id": "ID", "username": "用户名", "created_at": "创建时间"}
        """
        return {}

    def format_export_row(self, row: dict, context: dict | None = None) -> dict:
        """
        格式化导出行（子类覆写）

        在写入 XLSX 前对每行数据做格式化，如状态码→文字、ID→名称等。
        context 由 get_export_context() 提供，用于传递预加载的映射数据。
        """
        return row

    def get_export_context(self) -> dict:
        """
        导出格式化上下文（子类覆写）

        在导出开始前调用一次，返回的 dict 会传给每次 format_export_row() 调用。
        用于预加载 ID→名称映射等数据，避免逐行查询。

        示例：
            return {"status_map": {0: "禁用", 1: "正常"}}
        """
        return {}

    async def do_export(self, db: AsyncSession, params: dict) -> str:
        """
        执行数据导出

        :param db:     数据库 Session
        :param params: 导出参数 {file_key, filters, show_fields}
        :return:       生成的文件路径
        """
        from app.utils.export_helper import ExportHelper

        header_map = self.export_header_map()
        fields = self.export_fields()

        # show_fields 过滤（前端可选择显示哪些列）
        show_fields = params.get("show_fields")
        if show_fields:
            header_map = {k: v for k, v in header_map.items() if k in show_fields}
            fields = [f for f in fields if f in show_fields or f == self.pk_name]

        # 确保导出字段都在 header_map 中
        fields = [f for f in fields if f in header_map]

        helper = ExportHelper(
            model=self.model,
            fields=fields,
            header_map=header_map,
            file_key=params["file_key"],
            allowed_filters=self.allowed_filters(),
        )

        context = self.get_export_context()

        return await helper.export(
            db=db,
            filters=params.get("filters", {}),
            row_formatter=self.format_export_row,
            format_context=context,
        )

    async def cache_rebuild(self, db: AsyncSession, pk_value: Any):
        """
        重建单条记录的缓存

        先清除旧缓存，再从 DB 读取最新数据写入缓存。
        适用于直接操作 DB 后需要同步缓存的场景。
        """
        if not self.cache_prefix:
            return
        await self._clear_cache(pk_value, db)
        stmt = select(self.model).where(getattr(self.model, self.pk_name) == pk_value)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            data = self._to_dict(record, with_sensitive=True)
            await self._set_cache(data)
