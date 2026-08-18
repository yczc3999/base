import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, ORJSONResponse
from sqlalchemy import text
from app.config import settings
from app.logics.base import BizError
from app.middleware.operation_log import OperationLogMiddleware
from app.middleware.cors import CORSMiddleware, CORS_CONFIG
from app.services.database import get_db
from app.services.redis import get_redis, close_redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from app.routes import register_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup prewarm ----
    try:
        async for session in get_db():
            await session.execute(text("SELECT 1"))
        logger.info("startup prewarm db: ok")
    except Exception as e:  # noqa: BLE001
        logger.error("startup prewarm db failed: %s", e)
    try:
        r = await get_redis()
        await r.ping()
        logger.info("startup prewarm redis: ok")
    except Exception as e:  # noqa: BLE001
        logger.error("startup prewarm redis failed: %s", e)
    logger.info("startup prewarm complete")
    yield
    await close_redis()


app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
    docs_url="/docs" if settings.APP_DEBUG else None,
    redoc_url=None,
    default_response_class=ORJSONResponse,
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


# ---- 路由（唯一注册入口：app.routes.register_routes）----
# 路由契约、health、/uploads mount、SEO fallback 均由集中式 RouteRegistry
# 编译安装，见 serve/docs/route-registry-design.md。
register_routes(app)
