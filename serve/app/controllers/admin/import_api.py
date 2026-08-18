"""通用数据导入 — 模板下载 + 上传导入.

权限: 按模块动态校验 admin:{module}:create（导入本质是批量 create）。
"""
from fastapi import Request, Depends, UploadFile, File as FastAPIFile, Form
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import AuthInfo, current_auth
from app.logics.base import BizError
from app.services.database import get_db
from app.utils.response import ok, fail
from app.utils import import_helper

async def _has_create_perm(auth: AuthInfo, db: AsyncSession, module: str) -> bool:
    if auth.is_super_admin:
        return True
    from app.logics.admin_user import admin_user_logic
    perms = await admin_user_logic.get_user_perms(db, auth.user_id)
    return f"admin:{module}:create" in perms


async def import_template(
    request: Request,
    auth: AuthInfo = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
):
    """下载导入模板 XLSX（表头 = 模块导出表头）."""
    module = request.query_params
    if not module:
        return fail("缺少 module 参数")
    if not await _has_create_perm(auth, db, module):
        return fail("无权限", 403)
    try:
        logic = import_helper.resolve_logic_module(module)
        content = import_helper.build_template_bytes(logic)
        filename = f"import_{module}_template.xlsx"
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except BizError as e:
        return fail(e.msg, e.code)


async def import_upload(
    file: UploadFile = FastAPIFile(...),
    module: str = Form(...),
    auth: AuthInfo = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
):
    """上传 Excel 导入数据（逐行独立事务）."""
    if not await _has_create_perm(auth, db, module):
        return fail("无权限", 403)

    try:
        logic = import_helper.resolve_logic_module(module)
    except BizError as e:
        return fail(e.msg, e.code)

    # 校验扩展名
    filename = (file.filename or "").lower()
    if not filename.endswith((".xlsx", ".xls")):
        return fail("仅支持 .xlsx / .xls 文件")

    try:
        content = await file.read()
        rows = import_helper.parse_rows(logic, content)
    except BizError as e:
        return fail(e.msg, e.code)
    except Exception as e:
        return fail(f"解析文件失败: {e}")

    if not rows:
        return fail("文件中没有可导入的数据行")

    result = await import_helper.import_rows(db, logic, rows)
    return ok(result)
