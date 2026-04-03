"""异步数据导出"""

import importlib
import logging
from app.jobs.base import BaseJob
from app.services.database import async_session
from app.services.redis import get_redis

logger = logging.getLogger("job")


class HandleExportJob(BaseJob):
    name = "handle_export"

    async def handle(self, data: dict):
        file_key = data.get("file_key")
        logic_path = data.get("logic_path")  # e.g. "app.logics.admin_user.AdminUserLogic"

        if not file_key or not logic_path:
            logger.error(f"Export job missing params: {data}")
            return

        try:
            # 动态导入 Logic 类
            module_path, class_name = logic_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            logic_cls = getattr(module, class_name)
            logic = logic_cls()

            # 校验 header_map（空 = 不支持导出）
            if not logic.export_header_map():
                logger.error(f"Export not supported: {logic_path}")
                await self._set_error(file_key)
                return

            async with async_session() as db:
                path = await logic.do_export(db, {
                    "file_key": file_key,
                    "filters": data.get("filters", {}),
                    "show_fields": data.get("show_fields"),
                })

            logger.info(f"Export done: {file_key} → {path}")

        except Exception as e:
            logger.error(f"Export failed: {file_key} — {e}")
            await self._set_error(file_key)

    @staticmethod
    async def _set_error(file_key: str):
        r = await get_redis()
        await r.set(f"export@{file_key}", "-1", ex=3600)
