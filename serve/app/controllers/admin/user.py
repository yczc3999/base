import asyncio
from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.database import get_db
from app.utils.response import ok, fail
from app.utils.token import create_token_pair, revoke_token, revoke_all_tokens, refresh_access_token
from app.logics.admin_user import admin_user_logic
from app.logics.admin_login_log import admin_login_log_logic
from app.logics.setting import setting_logic
from app.models import AdminLoginLog
from app.controllers.base import crud_router
from app.deps import AuthInfo, require_admin, require_perms

router = APIRouter()

# 自动 CRUD（需要管理员登录）
router.include_router(crud_router("user", admin_user_logic, tags=["admin-user"], auth_dep=require_admin, perms_prefix="admin:user"))


# ---- DTO ----

class LoginDto(BaseModel):
    username: str
    password: str


class ChangePasswordDto(BaseModel):
    oldPassword: str
    newPassword: str


class RefreshDto(BaseModel):
    refresh_token: str


class AssignRolesDto(BaseModel):
    user_id: int
    role_ids: list[int]


# ---- 公开接口（不加 Depends，天然公开）----

@router.post("/user/login")
async def login(dto: LoginDto, request: Request, db: AsyncSession = Depends(get_db)):
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    user = await admin_user_logic.verify_login(db, dto.username, dto.password)
    if not user:
        asyncio.create_task(
            admin_login_log_logic.record(
                user_id=0, username=dto.username,
                ip=ip, user_agent=ua, status=AdminLoginLog.Status.FAIL, remark="用户名或密码错误",
            )
        )
        return fail("用户名或密码错误")

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

    return ok({**tokens, "user": safe_user})


@router.post("/user/refreshToken")
async def refresh_token(dto: RefreshDto):
    result = await refresh_access_token(dto.refresh_token)
    if not result:
        return fail("refresh_token 无效或已过期", code=401)
    return ok(result)


# ---- 需要登录的接口（加 Depends）----

@router.get("/user/info")
async def user_info(auth: AuthInfo = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    user = await admin_user_logic.get_detail(db, auth.user_id)
    if not user:
        return fail("用户不存在")
    return ok(user)


@router.post("/user/changePassword")
async def change_password(dto: ChangePasswordDto, auth: AuthInfo = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    success = await admin_user_logic.change_password(db, auth.user_id, dto.oldPassword, dto.newPassword)
    if not success:
        return fail("原密码错误")
    await revoke_all_tokens("admin", auth.user_id)
    return ok(msg="密码修改成功，请重新登录")


@router.post("/user/logout")
async def logout(auth: AuthInfo = Depends(require_admin)):
    await revoke_token(auth.access_token)
    return ok(msg="已退出登录")


@router.get("/user/menus")
async def user_menus(auth: AuthInfo = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """获取当前用户的菜单树 + 权限列表"""
    data = await admin_user_logic.get_user_menus(db, auth.user_id, auth.is_super_admin)
    return ok(data)


@router.get("/user/roleIds")
async def user_role_ids(
    user_id: int,
    auth: AuthInfo = Depends(require_admin),
    _: None = Depends(require_perms("admin:user:list")),
    db: AsyncSession = Depends(get_db),
):
    """获取指定用户的角色 ID 列表"""
    ids = await admin_user_logic.get_role_ids(db, user_id)
    return ok(ids)


@router.post("/user/assignRoles")
async def assign_roles(
    dto: AssignRolesDto,
    auth: AuthInfo = Depends(require_admin),
    _: None = Depends(require_perms("admin:user:assignRole")),
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


@router.post("/user/updateProfile")
async def update_profile(dto: UpdateProfileDto, auth: AuthInfo = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """修改个人资料（只能改自己的）"""
    data = {k: v for k, v in dto.model_dump().items() if v is not None}
    if not data:
        return fail("没有要修改的内容")
    data["id"] = auth.user_id
    await admin_user_logic.save(db, data)
    user = await admin_user_logic.get_detail(db, auth.user_id)
    return ok(user)
