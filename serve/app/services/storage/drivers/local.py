"""
本地存储驱动

config:
    {"path": "/data/uploads", "domain": "https://cdn.example.com"}
"""

import os
from app.services.base import BaseDriver


class LocalDriver(BaseDriver):

    async def upload(self, path: str, content: bytes, visible: bool = True) -> str:
        base = self._get("path") or "/data/uploads"
        full_path = os.path.join(base, path.lstrip("/"))
        directory = os.path.dirname(full_path)

        try:
            os.makedirs(directory, exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(content)
            return self.get_url(path)
        except Exception as e:
            self.service._fail(f"本地存储写入失败: {e}")
            return ""

    async def delete(self, path: str) -> bool:
        base = self._get("path") or "/data/uploads"
        full_path = os.path.join(base, path.lstrip("/"))
        try:
            if os.path.exists(full_path):
                os.remove(full_path)
            return True
        except Exception as e:
            self.service._fail(f"本地存储删除失败: {e}")
            return False

    async def exists(self, path: str) -> bool:
        base = self._get("path") or "/data/uploads"
        full_path = os.path.join(base, path.lstrip("/"))
        return os.path.exists(full_path)

    def get_url(self, path: str) -> str:
        domain = self._get("domain") or ""
        return f"{domain.rstrip('/')}/{path.lstrip('/')}" if domain else path
