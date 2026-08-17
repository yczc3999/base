"""
pytest 共享 fixture

测试策略：
- 用 SQLite 内存库 + aiosqlite 替代 PostgreSQL（BaseLogic 的 SQLAlchemy 2 语法通用）
- 用 fake Redis（内存 dict）替代真 Redis（cache_get/cache_set 逻辑一致）
- 避免依赖外部服务，测试可离线运行
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# 保证 app 包可导入
sys.path.insert(0, str(Path(__file__).parent.parent))


# ==================== 事件循环 ====================

@pytest.fixture(scope="session")
def event_loop():
    """pytest-asyncio 需要事件循环 fixture"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ==================== fake Redis ====================

class FakePipeline:
    """管道模式：暂存命令，execute() 时批量执行"""

    def __init__(self, redis):
        self._redis = redis
        self._cmds = []

    def set(self, key, value, ex=None, nx=False):
        self._cmds.append(("set", key, value, ex, nx))
        return self

    def get(self, key):
        self._cmds.append(("get", key))
        return self

    def delete(self, *keys):
        self._cmds.append(("delete", keys))
        return self

    def sadd(self, key, *values):
        self._cmds.append(("sadd", key, values))
        return self

    def smembers(self, key):
        self._cmds.append(("smembers", key))
        return self

    def srem(self, key, *values):
        self._cmds.append(("srem", key, values))
        return self

    def expire(self, key, ttl):
        self._cmds.append(("expire", key, ttl))
        return self

    async def execute(self):
        results = []
        for cmd in self._cmds:
            op = cmd[0]
            if op == "set":
                _, key, value, ex, nx = cmd
                results.append(await self._redis.set(key, value, ex=ex, nx=nx))
            elif op == "get":
                results.append(await self._redis.get(cmd[1]))
            elif op == "delete":
                results.append(await self._redis.delete(*cmd[1]))
            elif op == "sadd":
                results.append(await self._redis.sadd(cmd[1], *cmd[2]))
            elif op == "smembers":
                results.append(await self._redis.smembers(cmd[1]))
            elif op == "srem":
                results.append(await self._redis.srem(cmd[1], *cmd[2]))
            elif op == "expire":
                results.append(await self._redis.expire(cmd[1], cmd[2]))
        return results


class FakeRedis:
    """内存版 Redis 客户端，模拟 token/task/缓存用到的命令语义"""

    def __init__(self):
        self._data: dict[str, str] = {}
        self._sets: dict[str, set] = {}

    # ---- string ----
    async def get(self, key):
        return self._data.get(key)

    async def incr(self, key):
        n = int(self._data.get(key, 0))
        n += 1
        self._data[key] = str(n)
        return n

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self._data:
            return None  # 未设置
        self._data[key] = value
        return True

    async def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self._data:
                del self._data[k]
                n += 1
            if k in self._sets:
                del self._sets[k]
                n += 1
        return n

    async def ttl(self, key):
        return -1 if key not in self._data else 300

    async def eval(self, script, numkeys, *args):
        # 简化的 DEL-IF-MATCH：KEYS[1]=key, ARGV[1]=token
        if numkeys >= 1 and len(args) >= 2:
            key, token = args[0], args[1]
            if self._data.get(key) == token:
                del self._data[key]
                return 1
        return 0

    # ---- set ----
    async def sadd(self, key, *values):
        self._sets.setdefault(key, set()).update(values)
        return 1

    async def smembers(self, key):
        return set(self._sets.get(key, set()))

    async def srem(self, key, *values):
        if key in self._sets:
            self._sets[key].difference_update(values)
            return len(values)
        return 0

    async def expire(self, key, ttl):
        # 简化：TTL 不强制生效，仅存在即可
        return True

    # ---- list ----
    async def lpush(self, key, *values):
        self._data.setdefault(key, "[]")
        import json as _json
        lst = _json.loads(self._data.get(key, "[]"))
        for v in reversed(values):
            lst.insert(0, v)
        self._data[key] = _json.dumps(lst)
        return len(lst)

    async def llen(self, key):
        import json as _json
        return len(_json.loads(self._data.get(key, "[]")))

    async def lrange(self, key, start, end):
        import json as _json
        lst = _json.loads(self._data.get(key, "[]"))
        if end == -1:
            return lst[start:]
        return lst[start:end + 1]

    # ---- zset ----
    async def zadd(self, key, mapping):
        import json as _json
        z = self._data.get(key)
        if z is None:
            z = {}
            self._data[key] = _json.dumps(z)
        else:
            z = _json.loads(z)
        for member, score in mapping.items():
            z[member] = score
        self._data[key] = _json.dumps(z)
        return len(z)

    async def zcard(self, key):
        import json as _json
        return len(_json.loads(self._data.get(key, "{}")))

    # ---- pipeline ----
    def pipeline(self):
        return FakePipeline(self)

    # ---- scan ----
    async def scan(self, cursor=0, match="*", count=1000):
        # 同时搜 string 库(_data) 与 set 库(_sets), 模拟真 Redis 全局扫描
        keys = [k for k in self._data if self._match(match, k)]
        keys += [k for k in self._sets if k not in keys and self._match(match, k)]
        return 0, keys[:count]

    async def dbsize(self):
        return len(self._data)

    @staticmethod
    def _match(pattern: str, key: str) -> bool:
        import fnmatch
        return fnmatch.fnmatch(key, pattern)


@pytest.fixture
def fake_redis():
    """全局 fake Redis 实例"""
    return FakeRedis()


@pytest.fixture
def mock_redis(monkeypatch):
    """把 get_redis 换成返回 fake_redis 的 async 函数（全局覆盖）"""
    from app.services import redis as redis_mod

    fr = FakeRedis()

    async def _get_redis():
        return fr

    # 覆盖 app.services.redis.get_redis（token/task/cache 都从这里拿连接）
    monkeypatch.setattr(redis_mod, "get_redis", _get_redis)
    # 模块级 `from app.services.redis import get_redis` 的导入点也需逐个覆盖
    import app.utils.token as token_mod
    monkeypatch.setattr(token_mod, "get_redis", _get_redis)
    import app.tasks.base as task_mod
    monkeypatch.setattr(task_mod, "get_redis", _get_redis)
    import app.queue as queue_mod
    monkeypatch.setattr(queue_mod, "get_redis", _get_redis)
    import app.logics.task_monitor as tm_mod
    monkeypatch.setattr(tm_mod, "get_redis", _get_redis)
    import app.logics.db_backup as dbb_mod
    monkeypatch.setattr(dbb_mod, "get_redis", _get_redis)
    import app.utils.account_lock as al_mod
    monkeypatch.setattr(al_mod, "get_redis", _get_redis)
    import app.utils.captcha as cap_mod
    monkeypatch.setattr(cap_mod, "get_redis", _get_redis)
    return fr


@pytest.fixture
def mock_redis_cache(monkeypatch, mock_redis):
    """在 mock_redis 基础上，把 cache_* 函数也换成走 fake_redis 的实现"""
    import app.services.redis as redis_mod

    async def cache_get(key):
        val = await mock_redis.get(key)
        if val is None:
            return None
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val

    async def cache_set(key, value, ttl=0):
        data = json.dumps(value, ensure_ascii=False, default=str)
        if ttl > 0:
            await mock_redis.set(key, data, ex=ttl)
        else:
            await mock_redis.set(key, data)

    async def cache_del(*keys):
        await mock_redis.delete(*keys)

    async def cache_del_pattern(pattern, max_iterations=10):
        cursor, keys = await mock_redis.scan(match=pattern)
        await mock_redis.delete(*keys)

    monkeypatch.setattr(redis_mod, "cache_get", cache_get)
    monkeypatch.setattr(redis_mod, "cache_set", cache_set)
    monkeypatch.setattr(redis_mod, "cache_del", cache_del)
    monkeypatch.setattr(redis_mod, "cache_del_pattern", cache_del_pattern)
    return mock_redis


# ==================== SQLite 内存库 ====================

@pytest_asyncio.fixture
async def db_engine():
    """SQLite 内存库（测试模型表）"""
    from sqlalchemy.ext.asyncio import create_async_engine
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    yield engine
    await engine.dispose()
