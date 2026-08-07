"""
CrudRouter — 自动生成 CRUD 接口

用法：
    # 全部需要登录（默认）
    crud_router("user", admin_user_logic)

    # 整个模块免登录
    crud_router("article", article_logic, need_auth=False)

    # 部分方法免登录
    crud_router("product", product_logic, no_auth=["getList", "getDetail"])

    # 指定鉴权依赖
    crud_router("user", admin_user_logic, auth_dep=require_admin)

配置项：
    prefix          路由前缀
    logic           BaseLogic 子类实例
    tags            OpenAPI 标签
    need_auth       整个模块是否需要登录（默认 True）
    no_auth         不需要登录的方法名列表
    auth_dep        鉴权依赖（默认 require_auth）
    actions         权限分组（预留 RBAC）

鉴权规则（通过 Depends 注入）：
    1. need_auth=False → 整个模块免登录
    2. need_auth=True + no_auth=["getList"] → 仅 getList 免登录
    3. 其余方法 → 需要 Bearer Token

bindUserColumn 联动：
    如果 Logic 设置了 bind_user_column（如 "user_id"），
    CRUD 自动按当前登录用户过滤数据（getList/getDetail/doEdit/doDelete）。
"""

from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.logics.base import BaseLogic, BizError
from app.services.database import get_db
from app.deps import AuthInfo, require_auth, require_perms
from app.utils.response import ok, fail
from app.schemas.base import ListResponse, DetailResponse

DEFAULT_ACTIONS = {
    "read": ["getList", "getDetail"],
    "write": ["doEdit"],
    "delete": ["doDelete"],
    "export": ["doExport"],
}


def crud_router(
    prefix: str,
    logic: BaseLogic,
    tags: list[str] = None,
    need_auth: bool = True,
    no_auth: list[str] = None,
    auth_dep=None,
    perms_prefix: str = "",
    actions: dict = None,
) -> APIRouter:
    router = APIRouter(prefix=f"/{prefix}", tags=tags or [prefix])

    _actions = {**DEFAULT_ACTIONS, **(actions or {})}
    _no_auth = set(no_auth or [])

    # 权限校验依赖（perms_prefix 非空时生效）
    _perm_list = require_perms(f"{perms_prefix}:list") if perms_prefix else None
    _perm_detail = require_perms(f"{perms_prefix}:detail") if perms_prefix else None
    _perm_create = require_perms(f"{perms_prefix}:create") if perms_prefix else None
    _perm_edit = require_perms(f"{perms_prefix}:edit") if perms_prefix else None
    _perm_delete = require_perms(f"{perms_prefix}:delete") if perms_prefix else None
    _auth_dep = auth_dep or require_auth

    all_methods = ["getList", "getDetail", "doEdit", "doDelete"]

    def _needs_auth(method_name: str) -> bool:
        if not need_auth:
            return False
        return method_name not in _no_auth

    # ==================== getList ====================

    # response_model 描述 {code, msg, data} 信封（见 app/schemas/base.py），
    # 不改契约，仅做 OpenAPI 文档 + 响应校验。data 为 ListData。
    if _needs_auth("getList"):
        if _perm_list:
            @router.get("/getList", response_model=ListResponse)
            async def get_list(request: Request, auth: AuthInfo = Depends(_perm_list), db: AsyncSession = Depends(get_db)):
                return await _do_get_list(request, db, auth.user_id, auth.is_super_admin)
        else:
            @router.get("/getList", response_model=ListResponse)
            async def get_list(request: Request, auth: AuthInfo = Depends(_auth_dep), db: AsyncSession = Depends(get_db)):
                return await _do_get_list(request, db, auth.user_id, auth.is_super_admin)
    else:
        @router.get("/getList", response_model=ListResponse)
        async def get_list_public(request: Request, db: AsyncSession = Depends(get_db)):
            return await _do_get_list(request, db, None, False)

    async def _do_get_list(request: Request, db: AsyncSession, user_id: int | None, is_super: bool):
        try:
            query = dict(request.query_params)
            result = await logic.get_list(db, query, user_id=user_id, is_super=is_super)
            return ok(result)
        except BizError as e:
            return fail(e.msg, e.code)

    # ==================== getDetail ====================

    # response_model 描述 {code, msg, data} 信封，data 为单行 dict。
    if _needs_auth("getDetail"):
        if _perm_detail:
            @router.get("/getDetail", response_model=DetailResponse)
            async def get_detail(request: Request, auth: AuthInfo = Depends(_perm_detail), db: AsyncSession = Depends(get_db)):
                return await _do_get_detail(request, db, auth.user_id, auth.is_super_admin)
        else:
            @router.get("/getDetail", response_model=DetailResponse)
            async def get_detail(request: Request, auth: AuthInfo = Depends(_auth_dep), db: AsyncSession = Depends(get_db)):
                return await _do_get_detail(request, db, auth.user_id, auth.is_super_admin)
    else:
        @router.get("/getDetail", response_model=DetailResponse)
        async def get_detail_public(request: Request, db: AsyncSession = Depends(get_db)):
            return await _do_get_detail(request, db, None, False)

    async def _do_get_detail(request: Request, db: AsyncSession, user_id: int | None, is_super: bool):
        pk_value = request.query_params.get("id")
        if not pk_value:
            return fail("缺少主键参数")
        try:
            pk_int = int(pk_value)
        except (ValueError, TypeError):
            return fail("主键参数格式错误")
        try:
            result = await logic.get_detail(db, pk_int)
        except BizError as e:
            return fail(e.msg, e.code)
        if result is None:
            return fail("数据不存在")
        # bindUserColumn 校验：非超管只能看自己的数据
        if logic.bind_user_column and user_id is not None and not is_super:
            if result.get(logic.bind_user_column) != user_id:
                return fail("数据不存在")
        return ok(result)

    # ==================== doEdit ====================

    if _needs_auth("doEdit"):
        # doEdit 权限校验在 _do_edit 内部根据有无主键区分 create/edit
        @router.post("/doEdit")
        async def do_edit(request: Request, auth: AuthInfo = Depends(_auth_dep), db: AsyncSession = Depends(get_db)):
            return await _do_edit(request, db, auth.user_id, auth.is_super_admin)
    else:
        @router.post("/doEdit")
        async def do_edit_public(request: Request, db: AsyncSession = Depends(get_db)):
            return await _do_edit(request, db, None, False)

    async def _do_edit(request: Request, db: AsyncSession, user_id: int | None, is_super: bool):
        data = await request.json()

        # perms_prefix 权限校验：根据有无主键区分 create / edit
        if perms_prefix and user_id is not None and not is_super:
            from app.logics.admin_user import admin_user_logic
            user_perms = await admin_user_logic.get_user_perms(db, user_id)
            if data.get(logic.pk_name):
                if f"{perms_prefix}:edit" not in user_perms:
                    return fail("无权限", 403)
            else:
                if f"{perms_prefix}:create" not in user_perms:
                    return fail("无权限", 403)

        # bindUserColumn：创建时自动注入当前用户 ID，编辑时校验记录归属
        if logic.bind_user_column and user_id is not None and not is_super:
            if data.get(logic.pk_name):
                # 编辑路径：校验目标记录归属
                record = await logic.get_detail(db, data[logic.pk_name])
                if record and record.get(logic.bind_user_column) != user_id:
                    return fail("无权编辑该数据")
            else:
                # 创建路径：自动注入当前用户 ID
                data[logic.bind_user_column] = user_id
        try:
            result = await logic.save(db, data)
        except BizError as e:
            return fail(e.msg, e.code)
        return ok(result)

    # ==================== doDelete ====================

    if _needs_auth("doDelete"):
        if _perm_delete:
            @router.post("/doDelete")
            async def do_delete(request: Request, auth: AuthInfo = Depends(_perm_delete), db: AsyncSession = Depends(get_db)):
                return await _do_delete(request, db, auth.user_id, auth.is_super_admin)
        else:
            @router.post("/doDelete")
            async def do_delete(request: Request, auth: AuthInfo = Depends(_auth_dep), db: AsyncSession = Depends(get_db)):
                return await _do_delete(request, db, auth.user_id, auth.is_super_admin)
    else:
        @router.post("/doDelete")
        async def do_delete_public(request: Request, db: AsyncSession = Depends(get_db)):
            return await _do_delete(request, db, None, False)

    async def _do_delete(request: Request, db: AsyncSession, user_id: int | None, is_super: bool):
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
        if logic.bind_user_column and user_id is not None and not is_super:
            for pk_id in ids:
                record = await logic.get_detail(db, pk_id)
                if record and record.get(logic.bind_user_column) != user_id:
                    return fail("无权删除该数据")

        try:
            await logic.do_delete(db, ids)
        except BizError as e:
            return fail(e.msg, e.code)
        return ok(msg="删除成功")

    # ==================== doExport ====================

    _perm_export = require_perms(f"{perms_prefix}:export") if perms_prefix else None
    _logic_path = f"{type(logic).__module__}.{type(logic).__name__}"

    if _needs_auth("doExport"):
        if _perm_export:
            @router.post("/doExport")
            async def do_export(request: Request, auth: AuthInfo = Depends(_perm_export)):
                return await _do_export(request, auth.user_id)
        else:
            @router.post("/doExport")
            async def do_export(request: Request, auth: AuthInfo = Depends(_auth_dep)):
                return await _do_export(request, auth.user_id)
    else:
        @router.post("/doExport")
        async def do_export_public(request: Request):
            return await _do_export(request, 0)

    async def _do_export(request: Request, user_id: int):
        # 检查 Logic 是否支持导出
        if not logic.export_header_map():
            return fail("该模块不支持导出")

        from app.utils.export_helper import generate_export_key
        from app.queue import Queue

        file_key = generate_export_key(user_id)
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}

        await Queue.push("handle_export", {
            "file_key": file_key,
            "logic_path": _logic_path,
            "filters": body.get("filters", {}),
            "show_fields": body.get("showFields"),
        })

        return ok({"key": file_key})

    return router
