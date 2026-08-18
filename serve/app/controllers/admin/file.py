import os
from fastapi import Depends, Request, UploadFile, File as FastAPIFile, Form
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.database import get_db
from app.utils.response import ok, fail
from app.logics.file import file_logic, ALLOWED_IMAGE_TYPES, ALLOWED_FILE_TYPES
from app.logics.base import BizError
from app.deps import AuthInfo, current_auth


async def upload_file(
    file: UploadFile = FastAPIFile(...),
    category: str = Form(default="default"),
    is_private: bool = Form(default=False),
    auth: AuthInfo = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
):
    """上传文件（默认限制：ALLOWED_FILE_TYPES + 50MB）"""
    try:
        result = await file_logic.upload(
            db, file,
            category=category,
            is_private=is_private,
            user_id=auth.user_id,
            allowed_types=ALLOWED_FILE_TYPES,
        )
        return ok(result)
    except BizError as e:
        return fail(e.msg, e.code)


async def upload_image(
    file: UploadFile = FastAPIFile(...),
    category: str = Form(default="avatar"),
    is_private: bool = Form(default=False),
    auth: AuthInfo = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
):
    """上传图片（限制类型为图片）"""
    try:
        result = await file_logic.upload(
            db, file,
            category=category,
            is_private=is_private,
            user_id=auth.user_id,
            max_size=5 * 1024 * 1024,  # 图片最大 5MB
            allowed_types=ALLOWED_IMAGE_TYPES,
        )
        return ok(result)
    except BizError as e:
        return fail(e.msg, e.code)


async def batch_delete(
    request: Request,
    auth: AuthInfo = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
):
    """批量删除文件（DB + 存储双删，非超管仅删自己的文件）"""
    body = await request.json()
    ids = body.get("ids", [])
    if isinstance(ids, int):
        ids = [ids]
    if not ids:
        return fail("缺少 ids")

    await file_logic.delete_files(db, ids, user_id=auth.user_id, is_super=auth.is_super_admin)
    return ok(msg="删除成功")


# ==================== 隐私文件代理 ====================
# 注意：这个路由注册在 /api/file/{id}，不是 /api/admin/file/{id}
# 路由由 app.routes.admin._register_private_file() 注册

async def proxy_private_file(
    file_id: int,
    auth: AuthInfo = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
):
    """隐私文件代理访问"""
    from sqlalchemy import select
    from app.models import File

    stmt = select(File).where(File.id == file_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()

    if not record:
        return fail("文件不存在")

    if not record.is_private:
        return fail("非隐私文件，请直接访问 URL")

    # 权限校验：只能访问自己的文件，超管除外
    if not auth.is_super_admin and record.user_id != auth.user_id:
        return fail("无权限", 403)

    # 读本地文件
    from app.logics.file import _PRIVATE_ROOT
    full_path = os.path.join(_PRIVATE_ROOT, record.path.lstrip("/"))

    if not os.path.exists(full_path):
        return fail("文件不存在")

    return FileResponse(
        full_path,
        media_type=record.mime_type or "application/octet-stream",
        filename=record.original_name,
    )


# 需要在此 import，因为上面 proxy 用到了
from app.services.storage import storage_service
