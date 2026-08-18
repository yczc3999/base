"""
CrudController — 标准 CRUD HTTP Handler（不注册路由）

从旧 `controllers/base.py::crud_router()` 拆分而来：

- 只保留 Handler 实现（get_list / get_detail / do_edit / do_delete / do_export）。
- 不创建路由对象，不声明 URL，不在内部声明鉴权/权限 dependency。
- 鉴权与权限策略由 Route Manifest（app.routes）在编译期注入。

与旧 crud_router() 的行为契约完全一致：
- bind-user 注入（创建时注入 user_id，编辑/删除时校验归属）。
- BizError → fail(msg, code)。
- doEdit 内部按有无主键区分 create/edit 权限分支（perms_prefix）。
- doExport 通过 Queue 异步导出。

CrudController 通过 request.state.auth 读取可选 AuthInfo：
- protected action 缺失 AuthInfo → 路由配置错误（RuntimeError）。
- public action 传 None（user_id=None / is_super_admin=False）。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.base import BaseLogic, BizError
from app.services.database import get_db
from app.utils.response import ok, fail

DEFAULT_ACTIONS = {
    "read": ["getList", "getDetail"],
    "write": ["doEdit"],
    "delete": ["doDelete"],
    "export": ["doExport"],
}


class CrudController:
    """标准 CRUD 的 5 个 HTTP Handler。

    实例字段：
    - logic：BaseLogic 子类实例
    - perms_prefix：权限前缀（用于 doEdit create/edit 动态分支、doExport 检查）
    - actions：权限分组（兼容参数，当前只合并到局部变量，不改变路由行为）
    """

    def __init__(
        self,
        logic: BaseLogic,
        *,
        perms_prefix: str = "",
        actions: dict[str, list[str]] | None = None,
    ) -> None:
        self.logic = logic
        self.perms_prefix = perms_prefix
        self.actions = {**DEFAULT_ACTIONS, **(actions or {})}

    # ---- 鉴权上下文 ----

    def _auth_context(self, request: Request):
        """读取 request.state.auth（由 Route middleware 写入）。

        protected action 缺失 AuthInfo 视为路由配置错误；
        public action 由调用方传 None。
        """
        auth = getattr(request.state, "auth", None)
        return auth

    # ---- getList ----

    async def get_list(
        self,
        request: Request,
        db: AsyncSession,
        user_id: int | None,
        is_super: bool,
    ) -> dict[str, Any]:
        try:
            query = dict(request.query_params)
            result = await self.logic.get_list(
                db, query, user_id=user_id, is_super=is_super
            )
            return ok(result)
        except BizError as e:
            return fail(e.msg, e.code)

    # ---- getDetail ----

    async def get_detail(
        self,
        request: Request,
        db: AsyncSession,
        user_id: int | None,
        is_super: bool,
    ) -> dict[str, Any]:
        pk_value = request.query_params.get("id")
        if not pk_value:
            return fail("缺少主键参数")
        try:
            pk_int = int(pk_value)
        except (ValueError, TypeError):
            return fail("主键参数格式错误")
        try:
            result = await self.logic.get_detail(db, pk_int)
        except BizError as e:
            return fail(e.msg, e.code)
        if result is None:
            return fail("数据不存在")
        # bindUserColumn 校验：非超管只能看自己的数据
        if (
            self.logic.bind_user_column
            and user_id is not None
            and not is_super
        ):
            if result.get(self.logic.bind_user_column) != user_id:
                return fail("数据不存在")
        return ok(result)

    # ---- doEdit ----

    async def do_edit(
        self,
        request: Request,
        db: AsyncSession,
        user_id: int | None,
        is_super: bool,
    ) -> dict[str, Any]:
        data = await request.json()

        # perms_prefix 权限校验：根据有无主键区分 create / edit
        if self.perms_prefix and user_id is not None and not is_super:
            from app.logics.admin_user import admin_user_logic

            user_perms = await admin_user_logic.get_user_perms(db, user_id)
            if data.get(self.logic.pk_name):
                if f"{self.perms_prefix}:edit" not in user_perms:
                    return fail("无权限", 403)
            else:
                if f"{self.perms_prefix}:create" not in user_perms:
                    return fail("无权限", 403)

        # bindUserColumn：创建时自动注入当前用户 ID，编辑时校验记录归属
        if self.logic.bind_user_column and user_id is not None and not is_super:
            if data.get(self.logic.pk_name):
                # 编辑路径：校验目标记录归属
                record = await self.logic.get_detail(db, data[self.logic.pk_name])
                if record and record.get(self.logic.bind_user_column) != user_id:
                    return fail("无权编辑该数据")
            else:
                # 创建路径：自动注入当前用户 ID
                data[self.logic.bind_user_column] = user_id
        try:
            result = await self.logic.save(db, data)
        except BizError as e:
            return fail(e.msg, e.code)
        return ok(result)

    # ---- doDelete ----

    async def do_delete(
        self,
        request: Request,
        db: AsyncSession,
        user_id: int | None,
        is_super: bool,
    ) -> dict[str, Any]:
        body = await request.json()
        raw_ids = body.get("ids", "")
        try:
            if isinstance(raw_ids, list):
                ids = [int(i) for i in raw_ids]
            elif isinstance(raw_ids, int):
                ids = [raw_ids]
            elif isinstance(raw_ids, str) and raw_ids:
                ids = [int(i.strip()) for i in raw_ids.split(",") if i.strip()]
            else:
                return fail("缺少 ids 参数")
        except (ValueError, TypeError):
            return fail("ids 参数格式错误")

        # bindUserColumn 校验：非超管只能删自己的数据
        if (
            self.logic.bind_user_column
            and user_id is not None
            and not is_super
        ):
            for pk_id in ids:
                record = await self.logic.get_detail(db, pk_id)
                if record and record.get(self.logic.bind_user_column) != user_id:
                    return fail("无权删除该数据")

        try:
            await self.logic.do_delete(db, ids)
        except BizError as e:
            return fail(e.msg, e.code)
        return ok(msg="删除成功")

    # ---- doExport ----

    async def do_export(
        self, request: Request, user_id: int | None
    ) -> dict[str, Any]:
        # 检查 Logic 是否支持导出
        if not self.logic.export_header_map():
            return fail("该模块不支持导出")

        from app.utils.export_helper import generate_export_key
        from app.queue import Queue

        file_key = generate_export_key(user_id or 0)
        body = (
            await request.json()
            if request.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        logic_path = f"{type(self.logic).__module__}.{type(self.logic).__name__}"

        await Queue.push(
            "handle_export",
            {
                "file_key": file_key,
                "logic_path": logic_path,
                "filters": body.get("filters", {}),
                "show_fields": body.get("showFields"),
            },
        )

        return ok({"key": file_key})