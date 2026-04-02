"""
存储服务

用法：
    from app.services.storage import storage_service

    url = await storage_service.upload(db, "uploads/2026/04/02/abc.jpg", file_bytes)
    if storage_service.failed:
        return fail(storage_service.error)
    print(url)  # https://cdn.example.com/uploads/2026/04/02/abc.jpg

    await storage_service.delete(db, "uploads/2026/04/02/abc.jpg")

    exists = await storage_service.exists(db, "uploads/2026/04/02/abc.jpg")
"""

from app.services.base import BaseService


class StorageService(BaseService):
    category = "storage"

    @property
    def driver_map(self) -> dict:
        from app.services.storage.drivers.local import LocalDriver
        from app.services.storage.drivers.aliyun_oss import AliyunOssDriver
        from app.services.storage.drivers.qcloud_cos import QcloudCosDriver
        from app.services.storage.drivers.qiniu import QiniuDriver
        from app.services.storage.drivers.s3 import S3Driver

        return {
            "local": LocalDriver,
            "aliyun_oss": AliyunOssDriver,
            "qcloud_cos": QcloudCosDriver,
            "qiniu": QiniuDriver,
            "s3": S3Driver,
        }

    async def upload(self, db, path: str, content: bytes, visible: bool = True) -> str | None:
        """上传文件，返回访问 URL"""
        self._reset()
        driver = await self.get_driver(db)
        if not driver:
            return None
        return await self._safe_call(driver.upload(path, content, visible))

    async def delete(self, db, path: str) -> bool:
        """删除文件"""
        self._reset()
        driver = await self.get_driver(db)
        if not driver:
            return False
        return await self._safe_call(driver.delete(path))

    async def exists(self, db, path: str) -> bool:
        """判断文件是否存在"""
        self._reset()
        driver = await self.get_driver(db)
        if not driver:
            return False
        return await self._safe_call(driver.exists(path))

    async def url(self, db, path: str) -> str:
        """获取文件访问 URL"""
        self._reset()
        driver = await self.get_driver(db)
        if not driver:
            return ""
        return self._succeed(driver.get_url(path))


storage_service = StorageService()
