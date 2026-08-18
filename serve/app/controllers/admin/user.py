import asyncio
from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.database import get_db
from app.utils.response import ok, fail
from app.utils.token import create_token_pair, revoke_token, revoke_all_tokens, refresh_access_token
from app.logics.admin_user import admin_user_logic
from app.logics.admin_login_log import admin_login_log_logic
from app.logics.setting import setting_logic
from app.models import AdminLoginLog
from app.deps import AuthInfo, current_auth


# ---- DTO ----

class LoginDto(BaseModel):
    username: str
    password: str
    captcha_id: str = ""      # 验证码 ID（captcha_enabled 时必填）
    captcha_code: str = ""    # 验证码内容


class ChangePasswordDto(BaseModel):
    oldPassword: str
    newPassword: str


class RefreshDto(BaseModel):
    refresh_token: str


class AssignRolesDto(BaseModel):
    user_id: int
    role_ids: list[int]


# ---- 公开接口（不加 Depends，天然公开）----

async def get_captcha():
    """获取验证码: {captcha_id, svg}（纯 SVG, 无需登录）."""
    from app.utils.captcha import generate_captcha
    return ok(await generate_captcha())


async def login(dto: LoginDto, request: Request, db: AsyncSession = Depends(get_db)):
    from app.utils.rate_limit import check_rate_limit
    from app.utils.helpers import get_client_ip
    from app.utils.account_lock import (
        check_account_locked, record_login_failure, clear_login_failures,
    )

    ip = get_client_ip(request)
    ua = request.headers.get("user-agent", "")

    # 登录限速：同 IP 5 分钟内最多 10 次
    if not await check_rate_limit(f"login:{ip}", max_attempts=10, window=300):
        return fail("请求过于频繁，请稍后再试", 429)

    # 验证码（P2-1）: 可配置开关, 开启时登录必须通过验证码
    captcha_enabled = await setting_logic.get(db, "login_security", "captcha_enabled", "1")
    if captcha_enabled != "0":
        from app.utils.captcha import verify_captcha
        if not await verify_captcha(dto.captcha_id, dto.captcha_code):
            return fail("验证码错误或已过期", 400)

    # 重试次数门：账号级失败锁定（阈值/窗口可配置, 防换 IP 暴力破解）
    max_failures = int(await setting_logic.get(db, "login_security", "max_failures", "5") or 5)
    lock_minutes = int(await setting_logic.get(db, "login_security", "lock_minutes", "15") or 15)
    if await check_account_locked(dto.username, max_failures):
        return fail(f"登录失败次数过多，账号已锁定，请 {lock_minutes} 分钟后再试", 429)

    user = await admin_user_logic.verify_login(db, dto.username, dto.password)
    if not user:
        await record_login_failure(dto.username, lock_minutes * 60)
        asyncio.create_task(
            admin_login_log_logic.record(
                user_id=0, username=dto.username,
                ip=ip, user_agent=ua, status=AdminLoginLog.Status.FAIL, remark="用户名或密码错误",
            )
        )
        return fail("用户名或密码错误")

    # 登录成功清零失败计数
    await clear_login_failures(dto.username)

    multi_login = await setting_logic.get(db, "platform", "multi_login", "1")
    allow_multi = multi_login == "1"

    safe_user = {k: v for k, v in user.items() if k != "password"}

    tokens = await create_token_pair(
        user_id=user["id"],
        scope="admin",
        user_info={"username": user["username"], "is_super_admin": user.get("is_super_admin", False)},
        multi_login=allow_multi,
    )

    # 更新登录时间和 IP
    from datetime import datetime
    await admin_user_logic.save(db, {
        "id": user["id"], "last_login_at": datetime.now(), "last_login_ip": ip,
    })

    asyncio.create_task(
        admin_login_log_logic.record(
            user_id=user["id"], username=user["username"],
            ip=ip, user_agent=ua, status=AdminLoginLog.Status.SUCCESS,
        )
    )

    # 操作日志补记用户名（中间件记录时还没 token）
    from app.logics.admin_operation_log import admin_operation_log_logic
    asyncio.create_task(
        admin_operation_log_logic.record(
            user_id=user["id"], username=user["username"],
            module="user", action="login", method="POST",
            url="/api/admin/user/login", params={"username": dto.username},
            ip=ip, user_agent=ua, status_code=200, duration=0,
        )
    )

    # P2-2: 密码过期标记（前端提示修改密码）
    from app.utils.password_policy import password_expired
    expired = await password_expired(db, user.get("password_changed_at"))

    return ok({**tokens, "user": safe_user, "password_expired": expired})


async def refresh_token(dto: RefreshDto):
    result = await refresh_access_token(dto.refresh_token)
    if not result:
        return fail("refresh_token 无效或已过期", code=401)
    return ok(result)


# ---- 需要登录（鉴权由 Route Manifest 提供）----

async def user_info(auth: AuthInfo = Depends(current_auth), db: AsyncSession = Depends(get_db)):
    user = await admin_user_logic.get_detail(db, auth.user_id)
    if not user:
        return fail("用户不存在")
    return ok(user)


async def change_password(dto: ChangePasswordDto, auth: AuthInfo = Depends(current_auth), db: AsyncSession = Depends(get_db)):
    success = await admin_user_logic.change_password(db, auth.user_id, dto.oldPassword, dto.newPassword)
    if not success:
        return fail("原密码错误")
    await revoke_all_tokens("admin", auth.user_id)
    return ok(msg="密码修改成功，请重新登录")


async def logout(auth: AuthInfo = Depends(current_auth)):
    await revoke_token(auth.access_token)
    return ok(msg="已退出登录")


async def user_menus(auth: AuthInfo = Depends(current_auth), db: AsyncSession = Depends(get_db)):
    """获取当前用户的菜单树 + 权限列表"""
    data = await admin_user_logic.get_user_menus(db, auth.user_id, auth.is_super_admin)
    return ok(data)


async def user_role_ids(
    user_id: int,
    auth: AuthInfo = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
):
    """获取指定用户的角色 ID 列表"""
    ids = await admin_user_logic.get_role_ids(db, user_id)
    return ok(ids)


async def assign_roles(
    dto: AssignRolesDto,
    auth: AuthInfo = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
):
    """给用户分配角色"""
    await admin_user_logic.assign_roles(db, dto.user_id, dto.role_ids)
    return ok(msg="分配成功")


# ---- 个人中心 ----

class UpdateProfileDto(BaseModel):
    nickname: str = None
    avatar: str = None
    email: str = None
    phone: str = None


async def update_profile(dto: UpdateProfileDto, auth: AuthInfo = Depends(current_auth), db: AsyncSession = Depends(get_db)):
    """修改个人资料（只能改自己的）"""
    data = {k: v for k, v in dto.model_dump().items() if v is not None}
    if not data:
        return fail("没有要修改的内容")
    data["id"] = auth.user_id
    await admin_user_logic.save(db, data)
    user = await admin_user_logic.get_detail(db, auth.user_id)
    return ok(user)
