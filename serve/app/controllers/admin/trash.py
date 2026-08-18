"""回收站 — 跨模块软删除记录管理（恢复/彻底删除）.

模块 = 模型含 deleted_at 列的 BaseLogic（动态发现, 下游加 deleted_at 自动出现）。
复用 import_helper.resolve_logic_module 解析模块, 模块名白名单限制在 app.logics。
"""
import importlib
import inspect
import pkgutil

from fastapi import Depends, Request

from app.deps import AuthInfo, current_auth
from app.logics.base import BaseLogic
from app.utils.response import ok, fail


def _trash_modules() -> list[dict]:
    """发现支持回收站的模块: 模型含 deleted_at 列的 BaseLogic."""
    import app.logics as logics_pkg

    result = []
    for mod_info in pkgutil.iter_modules(logics_pkg.__path__):
        if mod_info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"app.logics.{mod_info.name}")
        except Exception:
            continue
        for _name, obj in inspect.getmembers(mod):
            if isinstance(obj, BaseLogic):
                model = getattr(obj, "model", None)
                if model is not None and hasattr(model, "deleted_at"):
                    result.append({"module": mod_info.name, "label": mod_info.name})
                    break  # 每模块取一个即可
    return sorted(result, key=lambda x: x["module"])


def _resolve(module: str) -> BaseLogic:
    from app.utils.import_helper import resolve_logic_module
    return resolve_logic_module(module)


async def trash_modules(auth: AuthInfo = Depends(current_auth)):
    """支持回收站的模块列表."""
    return ok(_trash_modules())


async def trash_list(request: Request, auth: AuthInfo = Depends(current_auth)):
    """某模块回收站记录列表."""
    module = request.query_params.get("module", "")
    if not module:
        return fail("缺少 module 参数")
    try:
        logic = _resolve(module)
        query = dict(request.query_params)
        result = await _trash_list(logic, query, auth)
        return ok(result)
    except Exception as e:
        from app.logics.base import BizError
        if isinstance(e, BizError):
            return fail(e.msg, e.code)
        return fail(f"读取回收站失败: {e}")


async def _trash_list(logic: BaseLogic, query: dict, auth: AuthInfo) -> dict:
    from app.services.database import async_session
    async with async_session() as db:
        return await logic.get_trash(db, query, user_id=auth.user_id, is_super=auth.is_super_admin)


async def trash_restore(request: Request, auth: AuthInfo = Depends(current_auth)):
    """恢复软删除记录."""
    body = await request.json()
    module = body.get("module", "")
    ids = body.get("ids", [])
    if not module or not ids:
        return fail("缺少 module / ids")
    try:
        logic = _resolve(module)
        from app.services.database import async_session
        async with async_session() as db:
            await logic.restore(db, ids)
        return ok(msg=f"已恢复 {len(ids)} 条")
    except Exception as e:
        from app.logics.base import BizError
        if isinstance(e, BizError):
            return fail(e.msg, e.code)
        return fail(f"恢复失败: {e}")


async def trash_purge(request: Request, auth: AuthInfo = Depends(current_auth)):
    """彻底删除（物理删除, 不可恢复）."""
    body = await request.json()
    module = body.get("module", "")
    ids = body.get("ids", [])
    if not module or not ids:
        return fail("缺少 module / ids")
    try:
        logic = _resolve(module)
        from app.services.database import async_session
        async with async_session() as db:
            await logic.purge(db, ids)
        return ok(msg=f"已彻底删除 {len(ids)} 条")
    except Exception as e:
        from app.logics.base import BizError
        if isinstance(e, BizError):
            return fail(e.msg, e.code)
        return fail(f"删除失败: {e}")
