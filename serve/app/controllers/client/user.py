import asyncio
from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.database import get_db
from app.utils.response import ok, fail
from app.utils.token import create_token_pair, revoke_token, refresh_access_token
from app.logics.user import user_logic
from app.logics.setting import setting_logic
from app.logics.base import BizError
from app.deps import AuthInfo, current_auth

# ---- DTO ----

class LoginDto(BaseModel):
    username: str
    password: str


class RegisterDto(BaseModel):
    username: str
    password: str
    nickname: str = ""


class RefreshDto(BaseModel):
    refresh_token: str


# ---- 公开接口 ----

async def login(dto: LoginDto, request: Request, db: AsyncSession = Depends(get_db)):
    from app.utils.rate_limit import check_rate_limit
    from app.utils.helpers import get_client_ip
    from app.utils.account_lock import (
        check_account_locked, record_login_failure, clear_login_failures,
    )

    ip = get_client_ip(request)
    if not await check_rate_limit(f"client_login:{ip}", max_attempts=10, window=300):
        return fail("请求过于频繁，请稍后再试", 429)

    # 重试次数门：账号级失败锁定
    max_failures = int(await setting_logic.get(db, "login_security", "max_failures", "5") or 5)
    lock_minutes = int(await setting_logic.get(db, "login_security", "lock_minutes", "15") or 15)
    if await check_account_locked(dto.username, max_failures):
        return fail(f"登录失败次数过多，账号已锁定，请 {lock_minutes} 分钟后再试", 429)

    user = await user_logic.verify_login(db, dto.username, dto.password)
    if not user:
        await record_login_failure(dto.username, lock_minutes * 60)
        return fail("用户名或密码错误")

    # 登录成功清零失败计数
    await clear_login_failures(dto.username)

    multi_login = await setting_logic.get(db, "platform", "multi_login", "1")

    safe_user = {k: v for k, v in user.items() if k != "password"}

    tokens = await create_token_pair(
        user_id=user["id"],
        scope="client",
        user_info={"username": user["username"]},
        multi_login=multi_login == "1",
    )

    # 更新登录时间和 IP
    from datetime import datetime
    ip = request.client.host if request.client else ""
    await user_logic.save(db, {"id": user["id"], "last_login_at": datetime.now(), "last_login_ip": ip})

    return ok({**tokens, "user": safe_user})


async def register(dto: RegisterDto, request: Request, db: AsyncSession = Depends(get_db)):
    from app.utils.rate_limit import check_rate_limit
    from app.utils.helpers import get_client_ip

    ip = get_client_ip(request)
    if not await check_rate_limit(f"client_register:{ip}", max_attempts=5, window=300):
        return fail("请求过于频繁，请稍后再试", 429)

    # P2-2 密码策略: 注册密码强度校验
    from app.utils.password_policy import get_policy_rules, validate_password_strength
    rules = await get_policy_rules(db)
    try:
        validate_password_strength(dto.password, rules)
    except BizError as e:
        return fail(e.msg, 422)

    # 检查用户名是否已存在
    existing = await user_logic.get_by_field(db, "username", dto.username)
    if existing:
        return fail("用户名已存在")

    result = await user_logic.create(db, {
        "username": dto.username,
        "password": dto.password,
        "nickname": dto.nickname or dto.username,
    })
    return ok(result)


async def refresh_token(dto: RefreshDto):
    result = await refresh_access_token(dto.refresh_token)
    if not result:
        return fail("refresh_token 无效或已过期", code=401)
    return ok(result)


# ---- 需要登录（鉴权由 Route Manifest 的 require_client middleware 提供）----

async def user_info(
    auth: AuthInfo = Depends(current_auth), db: AsyncSession = Depends(get_db)
):
    user = await user_logic.get_detail(db, auth.user_id)
    if not user:
        return fail("用户不存在")
    return ok(user)


async def logout(auth: AuthInfo = Depends(current_auth)):
    await revoke_token(auth.access_token)
    return ok(msg="已退出登录")
