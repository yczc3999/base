"""
定时任务基类

新建任务：在 app/tasks/ 下新建 .py 文件，继承 BaseTask，实现 run()
"""

import logging
import time
import uuid
from app.services.redis import get_redis, cache_set
from app.config import settings

logger = logging.getLogger("task")
PREFIX = settings.APP_NAME

# Lua 脚本：仅当锁值匹配时才删除（原子操作，防误删他人锁）
_DEL_IF_MATCH = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class BaseTask:
    name: str = ""
    interval: int = 60
    enabled: bool = True

    async def run(self):
        raise NotImplementedError

    async def execute(self):
        """执行入口（带防重复 + 错误处理 + 最近执行状态记录）"""
        cache_key = f"{PREFIX}:task:lock:{self.__class__.__name__}"
        r = await get_redis()

        # 唯一 token 标识锁持有者
        token = uuid.uuid4().hex

        # 锁 TTL = interval * 2（防止任务执行超时导致锁提前过期）
        lock_ttl = max(self.interval * 2, 30)
        locked = await r.set(cache_key, token, ex=lock_ttl, nx=True)
        if not locked:
            return

        start = time.time()
        status = "ok"
        error = None
        try:
            await self.run()
        except Exception as e:
            status = "failed"
            error = str(e)
            logger.error(f"[{self.name}] failed: {e}")
        finally:
            # 原子删除：只删自己持有的锁，不误删他人锁
            await r.eval(_DEL_IF_MATCH, 1, cache_key, token)
            # 记录最近执行状态（供 task_monitor 页面展示）
            await self._record_last_run(status, start, error)

    async def _record_last_run(self, status: str, start: float, error: str | None):
        """写 {PREFIX}:task:last_run:{class_name} → {status, time, duration, error}.

        7 天兜底过期；Redis 故障不影响任务本体。
        """
        try:
            await cache_set(
                f"{PREFIX}:task:last_run:{self.__class__.__name__}",
                {
                    "status": status,
                    "time": start,  # epoch 秒
                    "duration": round(time.time() - start, 3),
                    "error": error,
                },
                ttl=7 * 24 * 3600,
            )
        except Exception:
            logger.warning("record last_run failed for %s", self.__class__.__name__)
