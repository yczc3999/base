import asyncio
from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.database import get_db
from app.utils.response import ok, fail
from app.utils.token import create_token_pair, revoke_token, refresh_access_token
from app.logics.user import user_logic
from app.logics.setting import setting_logic
from app.deps import AuthInfo, require_client

router = APIRouter()


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

@router.post("/user/login")
async def login(dto: LoginDto, request: Request, db: AsyncSession = Depends(get_db)):
    user = await user_logic.verify_login(db, dto.username, dto.password)
    if not user:
        return fail("用户名或密码错误")

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


@router.post("/user/register")
async def register(dto: RegisterDto, db: AsyncSession = Depends(get_db)):
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


@router.post("/user/refreshToken")
async def refresh_token(dto: RefreshDto):
    result = await refresh_access_token(dto.refresh_token)
    if not result:
        return fail("refresh_token 无效或已过期", code=401)
    return ok(result)


# ---- 需要登录 ----

@router.get("/user/info")
async def user_info(auth: AuthInfo = Depends(require_client), db: AsyncSession = Depends(get_db)):
    user = await user_logic.get_detail(db, auth.user_id)
    if not user:
        return fail("用户不存在")
    return ok(user)


@router.post("/user/logout")
async def logout(auth: AuthInfo = Depends(require_client)):
    await revoke_token(auth.access_token)
    return ok(msg="已退出登录")
