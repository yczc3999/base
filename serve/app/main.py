from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.config import settings
from app.logics.base import BizError
from app.middleware.operation_log import OperationLogMiddleware
from app.middleware.cors import CORSMiddleware, CORS_CONFIG
from app.services.redis import close_redis
from app.controllers.admin import user as admin_user
from app.controllers.admin import setting as admin_setting
from app.controllers.admin import log as admin_log
from app.controllers.admin import menu as admin_menu
from app.controllers.admin import role as admin_role
from app.controllers.admin import message as admin_message
from app.controllers.admin.file import router as admin_file_router, file_proxy_router
from app.controllers.admin import dashboard as admin_dashboard
from app.controllers.admin import export as admin_export
from app.controllers.admin import article as admin_article
from app.controllers.admin import keyword as admin_keyword
from app.controllers.admin import seo as admin_seo
from app.controllers.web import seo as web_seo
from app.controllers.client import user as client_user
from app.controllers.admin import dict as admin_dict
from app.controllers.admin import client_user as admin_client_user
from app.controllers.admin import task_monitor as admin_task_monitor
from app.controllers.admin import db_backup as admin_db_backup
from app.controllers import dict as dict_public


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_redis()


app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
    docs_url="/docs" if settings.APP_DEBUG else None,
    redoc_url=None,
)

# ---- 中间件（注册顺序与执行顺序相反）----
app.add_middleware(OperationLogMiddleware)
app.add_middleware(CORSMiddleware, **CORS_CONFIG)

# ---- 全局异常处理 ----

@app.exception_handler(BizError)
async def biz_exception_handler(request: Request, exc: BizError):
    return JSONResponse({"code": exc.code, "msg": exc.msg, "data": None}, status_code=200)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if settings.APP_DEBUG:
        import traceback
        msg = traceback.format_exc()
    else:
        msg = "服务器内部错误"
    return JSONResponse({"code": 500, "msg": msg, "data": None}, status_code=200)


# ---- 路由 ----

# admin 端
app.include_router(admin_user.router, prefix="/api/admin")
app.include_router(admin_setting.router, prefix="/api/admin")
app.include_router(admin_log.router, prefix="/api/admin")
app.include_router(admin_menu.router, prefix="/api/admin")
app.include_router(admin_role.router, prefix="/api/admin")
app.include_router(admin_message.router, prefix="/api/admin")
app.include_router(admin_file_router, prefix="/api/admin")
app.include_router(admin_dashboard.router, prefix="/api/admin")
app.include_router(admin_export.router, prefix="/api/admin")
app.include_router(admin_article.router, prefix="/api/admin")
app.include_router(admin_keyword.router, prefix="/api/admin")
app.include_router(admin_seo.router, prefix="/api/admin")
app.include_router(admin_dict.router, prefix="/api/admin")
app.include_router(admin_client_user.router, prefix="/api/admin")
app.include_router(admin_task_monitor.router, prefix="/api/admin")
app.include_router(admin_db_backup.router, prefix="/api/admin")
app.include_router(web_seo.router)  # /sitemap.xml /robots.txt /{key}.txt 根路径

# 隐私文件代理 + 数据字典公开端点（不走 /api/admin 前缀）
app.include_router(file_proxy_router, prefix="/api")
app.include_router(dict_public.router, prefix="/api")

# client 端
app.include_router(client_user.router, prefix="/api/client")


# ---- 静态文件（public 存储，外网直接访问）----
import os
from fastapi.staticfiles import StaticFiles
_storage_public = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "public")
os.makedirs(_storage_public, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=_storage_public), name="uploads")


# ---- 健康检查 ----

@app.get("/health")
async def health():
    return {"status": "ok"}
