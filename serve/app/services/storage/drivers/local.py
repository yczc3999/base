"""
本地存储驱动

config:
    {"path": "/data/uploads", "domain": "https://cdn.example.com"}
"""

import os
from app.services.base import BaseDriver

# 默认路径指向项目 serve/storage/public（与 main.py 中 /uploads 静态挂载一致）
_DEFAULT_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))), "storage", "public")


class LocalDriver(BaseDriver):

    def _safe_path(self, base: str, path: str) -> str:
        """校验路径，防止路径穿越"""
        full_path = os.path.join(base, path.lstrip("/"))
        normalized = os.path.normpath(full_path)
        base_norm = os.path.normpath(base)
        if not (normalized == base_norm or normalized.startswith(base_norm + os.sep)):
            self.service._fail("非法文件路径")
            return ""
        return normalized

    async def upload(self, path: str, content: bytes, visible: bool = True) -> str:
        base = self._get("path") or _DEFAULT_BASE
        full_path = self._safe_path(base, path)
        if not full_path:
            return ""
        directory = os.path.dirname(full_path)

        try:
            os.makedirs(directory, exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(content)
            return self.get_url(path)
        except Exception as e:
            self.service._fail("本地存储写入失败")
            return ""

    async def delete(self, path: str) -> bool:
        base = self._get("path") or _DEFAULT_BASE
        full_path = self._safe_path(base, path)
        if not full_path:
            return False
        try:
            if os.path.exists(full_path):
                os.remove(full_path)
            return True
        except Exception as e:
            self.service._fail("本地存储删除失败")
            return False

    async def exists(self, path: str) -> bool:
        base = self._get("path") or _DEFAULT_BASE
        full_path = self._safe_path(base, path)
        if not full_path:
            return False
        return os.path.exists(full_path)

    def get_url(self, path: str) -> str:
        domain = self._get("domain") or ""
        return f"{domain.rstrip('/')}/{path.lstrip('/')}" if domain else path
