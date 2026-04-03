"""
Redis Session Token 管理

存储结构：
  {APP_NAME}:token:{access_token}                → user_info JSON           TTL=TOKEN_EXPIRES_IN
  {APP_NAME}:refresh:{refresh_token}             → refresh_data JSON        TTL=REFRESH_TOKEN_EXPIRES_IN
  {APP_NAME}:user_tokens:{scope}:{user_id}       → set of access_tokens    TTL=REFRESH_TOKEN_EXPIRES_IN
  {APP_NAME}:user_refreshes:{scope}:{user_id}    → set of refresh_tokens   TTL=REFRESH_TOKEN_EXPIRES_IN

流程：
  登录    → create_token_pair()    → 返回 access_token + refresh_token
  鉴权    → verify_token()         → 查 Redis 拿 user_info
  续期    → refresh_access_token() → 用 refresh_token 换新 access_token
  登出    → revoke_token()         → 删 access_token + refresh_token
  踢人    → revoke_all_tokens()    → 删该用户所有 token（通过索引，不全量 SCAN）
"""

import json
import secrets
from app.config import settings
from app.services.redis import get_redis

_PREFIX = settings.APP_NAME


def _gen_token() -> str:
    return secrets.token_urlsafe(48)


def _token_key(token: str) -> str:
    return f"{_PREFIX}:token:{token}"


def _refresh_key(token: str) -> str:
    return f"{_PREFIX}:refresh:{token}"


def _user_tokens_key(scope: str, user_id: int) -> str:
    return f"{_PREFIX}:user_tokens:{scope}:{user_id}"


def _user_refreshes_key(scope: str, user_id: int) -> str:
    return f"{_PREFIX}:user_refreshes:{scope}:{user_id}"


async def create_token_pair(
    user_id: int,
    scope: str = "admin",
    user_info: dict = None,
    multi_login: bool = True,
) -> dict:
    """
    创建 access_token + refresh_token

    :param user_id:     用户 ID
    :param scope:       作用域（admin / client）
    :param user_info:   附加信息（username 等），存入 Redis
    :param multi_login: 是否允许多点登录，False 时先踢掉旧 token
    :return: {"access_token", "refresh_token", "expires_in"}
    """
    r = await get_redis()

    if not multi_login:
        await revoke_all_tokens(scope, user_id)

    access_token = _gen_token()
    refresh_token = _gen_token()

    full_info = {"user_id": user_id, "scope": scope, **(user_info or {})}
    token_data = json.dumps(full_info, default=str)
    refresh_data = json.dumps({**full_info, "access_token": access_token}, default=str)

    ut_key = _user_tokens_key(scope, user_id)
    ur_key = _user_refreshes_key(scope, user_id)

    # pipeline 批量写入
    pipe = r.pipeline()
    pipe.set(_token_key(access_token), token_data, ex=settings.TOKEN_EXPIRES_IN)
    pipe.set(_refresh_key(refresh_token), refresh_data, ex=settings.REFRESH_TOKEN_EXPIRES_IN)
    pipe.sadd(ut_key, access_token)
    pipe.expire(ut_key, settings.REFRESH_TOKEN_EXPIRES_IN)
    pipe.sadd(ur_key, refresh_token)
    pipe.expire(ur_key, settings.REFRESH_TOKEN_EXPIRES_IN)
    await pipe.execute()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": settings.TOKEN_EXPIRES_IN,
    }


async def verify_token(token: str) -> dict | None:
    """验证 access_token，返回 user_info 或 None"""
    r = await get_redis()
    raw = await r.get(_token_key(token))
    if raw is None:
        return None
    return json.loads(raw)


async def refresh_access_token(refresh_token: str) -> dict | None:
    """
    用 refresh_token 换新 access_token

    旧 access_token 被删除，refresh_token 保持不变（直到过期）。
    使用 pipeline 保证原子性。

    :return: {"access_token", "expires_in"} 或 None
    """
    r = await get_redis()
    raw = await r.get(_refresh_key(refresh_token))
    if raw is None:
        return None

    refresh_data = json.loads(raw)
    user_id = refresh_data["user_id"]
    scope = refresh_data["scope"]
    old_access_token = refresh_data.get("access_token")

    new_access_token = _gen_token()

    # 从 refresh_data 恢复完整 user_info（登录时已存入）
    user_info = {k: v for k, v in refresh_data.items() if k != "access_token"}
    token_data = json.dumps(user_info, default=str)

    # 更新 refresh_data 中的 access_token 引用
    refresh_data["access_token"] = new_access_token
    ttl = await r.ttl(_refresh_key(refresh_token))
    if ttl <= 0:
        return None  # refresh_token 已过期

    ut_key = _user_tokens_key(scope, user_id)

    # pipeline 原子操作：删旧 → 写新 → 更新索引
    pipe = r.pipeline()
    if old_access_token:
        pipe.delete(_token_key(old_access_token))
        pipe.srem(ut_key, old_access_token)
    pipe.set(_token_key(new_access_token), token_data, ex=settings.TOKEN_EXPIRES_IN)
    pipe.set(_refresh_key(refresh_token), json.dumps(refresh_data, default=str), ex=ttl)
    pipe.sadd(ut_key, new_access_token)
    await pipe.execute()

    return {
        "access_token": new_access_token,
        "expires_in": settings.TOKEN_EXPIRES_IN,
    }


async def revoke_token(access_token: str, refresh_token: str = None):
    """登出：删除 access_token 和 refresh_token"""
    r = await get_redis()

    raw = await r.get(_token_key(access_token))
    pipe = r.pipeline()
    pipe.delete(_token_key(access_token))

    if raw:
        data = json.loads(raw)
        scope = data.get("scope", "admin")
        user_id = data["user_id"]
        pipe.srem(_user_tokens_key(scope, user_id), access_token)

    if refresh_token:
        pipe.delete(_refresh_key(refresh_token))
        # 也从用户 refresh 索引中移除
        if raw:
            pipe.srem(_user_refreshes_key(scope, user_id), refresh_token)

    await pipe.execute()


async def revoke_all_tokens(scope: str, user_id: int):
    """
    踢人：删除该用户所有 token

    通过用户级索引（user_tokens + user_refreshes）精确删除，不需要全量 SCAN。
    """
    r = await get_redis()
    ut_key = _user_tokens_key(scope, user_id)
    ur_key = _user_refreshes_key(scope, user_id)

    # 拿到所有 access_token 和 refresh_token
    access_tokens = await r.smembers(ut_key)
    refresh_tokens = await r.smembers(ur_key)

    pipe = r.pipeline()

    # 删所有 access_token
    for t in access_tokens:
        pipe.delete(_token_key(t))

    # 删所有 refresh_token
    for t in refresh_tokens:
        pipe.delete(_refresh_key(t))

    # 删索引本身
    pipe.delete(ut_key)
    pipe.delete(ur_key)

    await pipe.execute()
