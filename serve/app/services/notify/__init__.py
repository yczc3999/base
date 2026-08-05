"""
通知服务

用法：
    from app.services.notify import notify_service

    await notify_service.send(db, "系统告警", "服务器 CPU 超过 90%")
    if notify_service.failed:
        print(notify_service.error)

    # 指定渠道
    await notify_service.send_by(db, "dingtalk", "标题", "内容")

    # 广播（所有已启用渠道）
    results = await notify_service.broadcast(db, "紧急告警", "数据库连接池耗尽")
"""

from app.services.base import BaseService


class NotifyService(BaseService):
    category = "notify"

    @property
    def driver_map(self) -> dict:
        # 延迟导入，避免循环依赖
        from app.services.notify.drivers.telegram import TelegramDriver
        from app.services.notify.drivers.dingtalk import DingtalkDriver
        from app.services.notify.drivers.feishu import FeishuDriver
        from app.services.notify.drivers.wechat_work import WechatWorkDriver
        from app.services.notify.drivers.email_driver import EmailDriver

        return {
            "telegram": TelegramDriver,
            "dingtalk": DingtalkDriver,
            "feishu": FeishuDriver,
            "wechat_work": WechatWorkDriver,
            "email": EmailDriver,
        }

    async def send(self, db, title: str, content: str, **kwargs):
        """通过默认渠道发送通知"""
        self._reset()
        driver = await self.get_driver(db)
        if not driver:
            return None
        return await self._safe_call(driver.send(title, content, **kwargs))

    async def send_by(self, db, driver_name: str, title: str, content: str, **kwargs):
        """通过指定渠道发送"""
        self._reset()
        driver = await self.get_specific_driver(db, driver_name)
        if not driver:
            return None
        return await self._safe_call(driver.send(title, content, **kwargs))

    async def broadcast(self, db, title: str, content: str, **kwargs) -> dict:
        """通过所有已启用渠道广播（每渠道独立状态，互不串扰）"""
        config = await self.get_config(db)
        results = {}

        for name in self.driver_map:
            if config.get(f"{name}_enabled") != "1":
                continue
            # 每渠道独立 reset，避免上一渠道失败污染后续渠道
            self._reset()
            try:
                driver = await self.get_specific_driver(db, name)
                if driver:
                    ok = await driver.send(title, content, **kwargs)
                    results[name] = bool(ok) and not self.failed
                else:
                    results[name] = False
            except Exception:
                results[name] = False

        self._succeed(results)
        return results


notify_service = NotifyService()
