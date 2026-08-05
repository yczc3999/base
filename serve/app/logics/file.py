"""
文件管理 Logic

上传流程：
    FileLogic.upload(db, file, category, is_private, user_id)
        → 校验（大小/类型）
        → 生成路径（{prefix}/{YYYY}/{MM}/{DD}/{uuid}.{ext}）
        → StorageService 上传
        → 写入 files 表（path + url + platform）
        → 返回 file record

删除流程：
    FileLogic.delete_files(db, ids)
        → 查 record 拿 platform
        → StorageService.get_specific_driver(platform) 删存储文件
        → 删 DB 记录
"""

import os
import uuid
from datetime import date
from sqlalchemy import select, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import UploadFile

from app.models import File
from app.logics.base import BaseLogic, BizError
from app.services.storage import storage_service

# 默认限制
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
ALLOWED_FILE_TYPES = ALLOWED_IMAGE_TYPES | {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
    "application/x-rar-compressed",
    "application/x-7z-compressed",
    "text/plain",
    "text/csv",
    "video/mp4",
    "audio/mpeg",
}

# 本地存储根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_STORAGE_ROOT = os.path.join(_PROJECT_ROOT, "storage")
_PUBLIC_ROOT = os.path.join(_STORAGE_ROOT, "public")
_PRIVATE_ROOT = os.path.join(_STORAGE_ROOT, "private")


class FileLogic(BaseLogic):
    model = File
    cache_prefix = ""
    bind_user_column = "user_id"

    def allowed_filters(self):
        return ["id", "user_id", "category", "mime_type", "ext", "is_private", "platform", "created_at"]

    def allowed_sorts(self):
        return ["id", "created_at", "size"]

    def keyword_fields(self):
        return ["original_name", "name"]

    # ==================== 上传 ====================

    async def upload(
        self,
        db: AsyncSession,
        file: UploadFile,
        category: str = "default",
        is_private: bool = False,
        user_id: int = None,
        max_size: int = MAX_FILE_SIZE,
        allowed_types: set = None,
    ) -> dict:
        """
        上传文件

        :param file:          FastAPI UploadFile
        :param category:      分类（avatar/document/export/...）
        :param is_private:    隐私文件（强制 Local，代理访问）
        :param user_id:       上传者 ID
        :param max_size:      最大大小（字节）
        :param allowed_types: 允许的 MIME 类型集合
        """
        # 读取文件内容
        content = await file.read()
        size = len(content)
        filename = file.filename or "unknown"
        content_type = file.content_type or "application/octet-stream"

        # 校验大小
        if size > max_size:
            raise BizError(f"文件大小超出限制（最大 {max_size // 1024 // 1024}MB）")

        # 校验类型
        if allowed_types and content_type not in allowed_types:
            raise BizError(f"不支持的文件类型: {content_type}")

        # 生成路径
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
        uuid_name = f"{uuid.uuid4().hex}.{ext}"
        prefix = "private" if is_private else category
        today = date.today()
        path = f"{prefix}/{today:%Y}/{today:%m}/{today:%d}/{uuid_name}"

        # 上传到存储
        if is_private:
            # private 文件只存本地 storage/private，不走 OSS
            full_path = os.path.join(_PRIVATE_ROOT, path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(content)
            url = ""
            platform = "local"
        else:
            driver = await storage_service.get_driver(db)
            if storage_service.failed:
                raise BizError(storage_service.error)
            url = await driver.upload(path, content, visible=True)
            if storage_service.failed:
                raise BizError(storage_service.error)
            config = await storage_service.get_config(db)
            platform = config.get("default", "local")

        # 写入 DB，失败时尝试回删已上传的文件
        record = File(
            name=uuid_name,
            original_name=filename,
            path=path,
            url=url or "",
            platform=platform,
            mime_type=content_type,
            size=size,
            ext=ext,
            is_private=is_private,
            user_id=user_id,
            category=category,
        )
        db.add(record)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            try:
                await driver.delete(path)
            except Exception:
                pass
            raise BizError("文件记录保存失败")
        await db.refresh(record)

        # 隐私文件回填代理 URL
        if is_private:
            record.url = f"/api/file/{record.id}"
            await db.commit()

        return self.format_data(record)

    # ==================== 删除（含存储清理） ====================

    async def delete_files(self, db: AsyncSession, ids: list[int], user_id: int = None, is_super: bool = False):
        """删除文件（DB + 存储双删，非超管仅删自己的文件）"""
        for file_id in ids:
            stmt = select(File).where(File.id == file_id)
            result = await db.execute(stmt)
            record = result.scalar_one_or_none()
            if not record:
                continue

            # 归属校验：非超管只能删自己的文件
            if user_id is not None and not is_super and record.user_id != user_id:
                continue

            # 删存储文件
            try:
                driver = await storage_service.get_specific_driver(db, record.platform)
                if driver:
                    await driver.delete(record.path)
            except Exception:
                pass  # 存储删除失败不阻塞

            # 删 DB
            await db.execute(sql_delete(File).where(File.id == file_id))

        await db.commit()


file_logic = FileLogic()
