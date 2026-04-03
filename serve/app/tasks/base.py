"""
定时任务基类

新建任务：在 app/tasks/ 下新建 .py 文件，继承 BaseTask，实现 run()

示例：
    class CleanupTask(BaseTask):
        name = "清理过期文件"
        interval = 3600  # 每小时

        async def run(self):
            ...
"""

import logging
from app.services.redis import get_redis

logger = logging.getLogger("task")


class BaseTask:
    name: str = ""
    interval: int = 60
    enabled: bool = True

    async def run(self):
        raise NotImplementedError

    async def execute(self):
        """执行入口（带防重复 + 错误处理）"""
        cache_key = f"task:running:{self.__class__.__name__}"
        r = await get_redis()

        # 防重复（SET NX，过期时间 = interval）
        locked = await r.set(cache_key, "1", ex=max(self.interval - 1, 5), nx=True)
        if not locked:
            return

        try:
            await self.run()
        except Exception as e:
            logger.error(f"[{self.name}] failed: {e}")
        finally:
            await r.delete(cache_key)
