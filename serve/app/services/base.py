"""
BaseService — 所有第三方服务的基类

核心设计：
    1. 错误不抛出，存在实例上，调用方自己决定处理方式
    2. 配置从 settings 表加载，驱动按 default 自动选择
    3. 纯 HTTP 调用，不依赖任何第三方 SDK

用法：
    await sms_service.send_code(db, "13800138000", "1234")
    if sms_service.failed:
        return fail(sms_service.error)
    # sms_service.result 是返回值

错误判断：
    sms_service.ok          → True = 成功
    sms_service.failed      → True = 失败
    sms_service.error       → 错误信息字符串（成功时为 None）
    sms_service.result      → 最后一次操作的返回值（失败时为 None）
"""

import json
import traceback
from typing import Any


class BaseService:
    """
    第三方服务基类

    子类需设置：
        category:   str           — settings 表的 category（如 "sms"）
        driver_map: dict          — 驱动映射 {"aliyun": AliyunDriver, ...}
    """

    category: str = ""
    driver_map: dict = {}

    def __init__(self):
        self._error: str | None = None
        self._result: Any = None

    # ==================== 状态属性 ====================

    @property
    def ok(self) -> bool:
        """最后一次操作是否成功"""
        return self._error is None

    @property
    def failed(self) -> bool:
        """最后一次操作是否失败"""
        return self._error is not None

    @property
    def error(self) -> str | None:
        """错误信息（成功时为 None）"""
        return self._error

    @property
    def result(self) -> Any:
        """最后一次操作的返回值（失败时为 None）"""
        return self._result

    # ==================== 状态控制（子类/驱动内部用） ====================

    def _succeed(self, data: Any = None) -> Any:
        """标记成功，存储返回值"""
        self._error = None
        self._result = data
        return data

    def _fail(self, msg: str) -> None:
        """标记失败，存储错误信息"""
        self._error = msg
        self._result = None
        return None

    def _reset(self):
        """重置状态（每次操作前调用）"""
        self._error = None
        self._result = None

    # ==================== 配置加载 ====================

    async def get_config(self, db) -> dict:
        """从 settings 表加载该 category 的全部配置"""
        from app.logics.setting import setting_logic
        try:
            all_settings = await setting_logic.get_all(db)
            return all_settings.get(self.category, {})
        except Exception as e:
            self._fail(f"配置加载失败: {e}")
            return {}

    async def get_default_name(self, db) -> str | None:
        """获取默认驱动名称"""
        config = await self.get_config(db)
        if self.failed:
            return None
        name = config.get("default", "")
        if not name:
            self._fail(f"{self.category} 未配置默认服务商")
            return None
        return name

    async def get_driver_config(self, db, driver_name: str) -> dict:
        """获取指定驱动的配置"""
        config = await self.get_config(db)
        if self.failed:
            return {}
        raw = config.get(driver_name, {})
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {}
        return raw if isinstance(raw, dict) else {}

    # ==================== 驱动工厂 ====================

    async def get_driver(self, db):
        """获取默认驱动实例"""
        self._reset()

        driver_name = await self.get_default_name(db)
        if not driver_name:
            return None

        return await self.get_specific_driver(db, driver_name)

    async def get_specific_driver(self, db, driver_name: str):
        """获取指定名称的驱动实例"""
        if driver_name not in self.driver_map:
            self._fail(f"不支持的服务商: {driver_name}")
            return None

        driver_config = await self.get_driver_config(db, driver_name)
        if self.failed:
            return None

        try:
            driver_cls = self.driver_map[driver_name]
            driver = driver_cls(driver_config, self)
            return driver
        except Exception as e:
            self._fail(f"驱动初始化失败({driver_name}): {e}")
            return None

    # ==================== 安全调用包装 ====================

    async def _safe_call(self, coro):
        """
        安全调用异步方法，捕获所有异常存入 error

        如果 driver 内部已经调了 _fail，不会被覆盖。
        """
        try:
            result = await coro
            # driver 内部可能已经 _fail 了，不覆盖
            if not self.failed:
                return self._succeed(result)
            return result
        except Exception as e:
            return self._fail(str(e))


class BaseDriver:
    """
    驱动基类

    每个具体驱动继承此类。
    通过 self.service 引用所属 Service，可调用 service._fail() 报错。
    """

    def __init__(self, config: dict, service: BaseService):
        self.config = config
        self.service = service

    def _get(self, key: str, required: bool = False) -> str:
        """
        从 config 中取值

        :param key:      配置键名
        :param required: 是否必填（缺失时标记 service 失败）
        """
        val = self.config.get(key, "")
        if required and not val:
            self.service._fail(f"缺少配置项: {key}")
        return val
