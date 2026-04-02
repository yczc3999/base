from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.database import get_db
from app.utils.response import ok
from app.logics.setting import setting_logic
from app.deps import AuthInfo, require_admin

router = APIRouter()


# 需要管理员登录
@router.get("/setting/get")
async def get_settings(auth: AuthInfo = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = await setting_logic.get_all(db)
    return ok(data)


# 需要管理员登录
@router.post("/setting/set")
async def set_settings(request: Request, auth: AuthInfo = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    await setting_logic.set_many(db, body)
    return ok(msg="保存成功")
