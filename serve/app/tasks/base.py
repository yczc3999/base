"""
定时任务基类

新建任务：在 app/tasks/ 下新建 .py 文件，继承 BaseTask，实现 run()
"""

import logging
from app.services.redis import get_redis
from app.config import settings

logger = logging.getLogger("task")
PREFIX = settings.APP_NAME


class BaseTask:
    name: str = ""
    interval: int = 60
    enabled: bool = True

    async def run(self):
        raise NotImplementedError

    async def execute(self):
        """执行入口（带防重复 + 错误处理）"""
        cache_key = f"{PREFIX}:task:lock:{self.__class__.__name__}"
        r = await get_redis()

        # 锁 TTL = interval * 2（防止任务执行超时导致锁提前过期）
        lock_ttl = max(self.interval * 2, 30)
        locked = await r.set(cache_key, "1", ex=lock_ttl, nx=True)
        if not locked:
            return

        try:
            await self.run()
        except Exception as e:
            logger.error(f"[{self.name}] failed: {e}")
        finally:
            await r.delete(cache_key)
