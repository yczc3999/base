"""
短信服务

用法：
    from app.services.sms import sms_service

    await sms_service.send_code(db, "13800138000", "1234")
    if sms_service.failed:
        return fail(sms_service.error)

    ok = await sms_service.verify_code("13800138000", "1234")
"""

from app.services.base import BaseService
from app.services.redis import cache_get, cache_set


class SmsService(BaseService):
    category = "sms"

    @property
    def driver_map(self) -> dict:
        from app.services.sms.drivers.aliyun import AliyunSmsDriver
        from app.services.sms.drivers.aliyun_intl import AliyunIntlSmsDriver
        from app.services.sms.drivers.qcloud import QcloudSmsDriver
        from app.services.sms.drivers.huawei import HuaweiSmsDriver

        return {
            "aliyun": AliyunSmsDriver,
            "aliyun_intl": AliyunIntlSmsDriver,
            "qcloud": QcloudSmsDriver,
            "huawei": HuaweiSmsDriver,
        }

    async def send_code(self, db, phone: str, code: str, ttl: int = 120):
        """发送验证码（便捷方法）"""
        self._reset()
        driver = await self.get_driver(db)
        if not driver:
            return None

        # 从驱动配置取模板
        config = await self.get_config(db)
        default = config.get("default", "")
        driver_config = await self.get_driver_config(db, default)
        template_id = driver_config.get("template_code") or driver_config.get("template_id", "")

        result = await self._safe_call(driver.send(phone, template_id, {"code": code}))

        # 存 Redis
        if self.ok:
            await cache_set(f"sms_code:{phone}", code, ttl)

        return result

    async def send(self, db, phone: str, template_id: str, params: dict):
        """发送自定义模板短信"""
        self._reset()
        driver = await self.get_driver(db)
        if not driver:
            return None
        return await self._safe_call(driver.send(phone, template_id, params))

    async def verify_code(self, phone: str, code: str) -> bool:
        """验证验证码"""
        cached = await cache_get(f"sms_code:{phone}")
        if cached is None:
            return False
        return str(cached) == str(code)


sms_service = SmsService()
