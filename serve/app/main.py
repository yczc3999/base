import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, ORJSONResponse, Response
from sqlalchemy import text
from app.config import settings
from app.logics.base import BizError
from app.middleware.operation_log import OperationLogMiddleware
from app.middleware.cors import CORSMiddleware, CORS_CONFIG
from app.observability import (
    configure_logging,
    configure_tracing,
    render_metrics,
    shutdown_tracing,
)
from app.services.database import get_db
from app.services.redis import get_redis, close_redis
from app.services.runtime import build_runtime_resources, safe_unready_snapshot

# 注意：不再调用 logging.basicConfig——V2 日志只能经 observability.configure_logging 安装，
# 避免非 V2 handler 绕过统一 redactor（WP-00d2-r1）。
logger = logging.getLogger(__name__)

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
from app.controllers.client import message as client_message
from app.controllers.admin import dict as admin_dict
from app.controllers.admin import client_user as admin_client_user
from app.controllers.admin import task_monitor as admin_task_monitor
from app.controllers.admin import db_backup as admin_db_backup
from app.controllers.admin import migration as admin_migration
from app.controllers.admin import monitor as admin_monitor
from app.controllers.admin import import_api as admin_import
from app.controllers.admin import session as admin_session
from app.controllers.admin import cache as admin_cache
from app.controllers.admin import trash as admin_trash
from app.controllers.admin.trading.router import router as admin_trading_router
from app.controllers import dict as dict_public


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- V2 observability（WP-00d2；OTEL disabled 时零 exporter）----
    configure_logging(
        level=settings.OBS_LOG_LEVEL,
        json_output=settings.OBS_LOG_JSON,
        service=settings.OBS_SERVICE_NAME,
        version=settings.OBS_SERVICE_VERSION,
    )
    runtime = None
    try:
        configure_tracing(
            enabled=settings.OTEL_ENABLED,
            endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
            allow_insecure_http=settings.OTEL_ALLOW_INSECURE_HTTP,
            ratio=settings.OTEL_TRACE_SAMPLE_RATIO,
            timeout_s=settings.OTEL_EXPORT_TIMEOUT_S,
            service=settings.OBS_SERVICE_NAME,
            version=settings.OBS_SERVICE_VERSION,
        )
        # ---- V2 runtime 构造 + 首次健康（started 后）----
        # 可等待、异常安全：构造失败时逆序关闭自建资源并重新抛出（阻止 startup）
        runtime = await build_runtime_resources(settings)
        runtime.mark_started()
        snapshot = await runtime.health_snapshot()
        app.state.trading_runtime = runtime
        logger.info("v2 runtime startup: %s", snapshot["status"])
        # ---- startup prewarm（失败只记固定 reason code，不泄原始异常）----
        try:
            async for session in get_db():
                await session.execute(text("SELECT 1"))
            logger.info("startup prewarm db: ok")
        except Exception:  # noqa: BLE001
            logger.warning("startup prewarm db failed")
        try:
            r = await get_redis()
            await r.ping()
            logger.info("startup prewarm redis: ok")
        except Exception:  # noqa: BLE001
            logger.warning("startup prewarm redis failed")
        logger.info("startup prewarm complete")
        yield
    finally:
        # 异常退出（yield 前/yield 内/取消）与正常退出都执行清理：Runtime → close_redis →
        # shutdown_tracing；一项失败不阻止后续项；每项至多一次。
        if runtime is not None:
            try:
                failed = await runtime.close()
                if failed:
                    logger.warning("v2 runtime shutdown partial: %s", ",".join(failed))
            except Exception:  # noqa: BLE001
                logger.warning("v2 runtime shutdown partial failure")
        try:
            await close_redis()
        except Exception:  # noqa: BLE001
            logger.warning("legacy redis shutdown failure")
        try:
            shutdown_tracing()
        except Exception:  # noqa: BLE001
            logger.warning("tracing shutdown failure")
        finally:
            # 清指针，避免 tracing 关闭异常或重启复用 app 时残留旧 runtime
            app.state.trading_runtime = None


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


# ---- 健康检查（最先注册，避免被 web SEO 的 /{name} 兜底路由遮蔽）----

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/live")
async def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(request: Request):
    """刷新 runtime 安全快照；required 全过 200，任一 required 失败同结构 503。
    编排自身抛异常也映射为固定 schema 503，不落入 Base 全局 HTTP-200 异常包装。"""
    try:
        runtime = getattr(request.app.state, "trading_runtime", None)
        if runtime is None:
            return JSONResponse(
                safe_unready_snapshot(settings.ARTIFACT_DRIVER), status_code=503
            )
        snapshot = await runtime.health_snapshot()
        status_code = 200 if snapshot["status"] == "ready" else 503
        return JSONResponse(snapshot, status_code=status_code)
    except Exception:  # noqa: BLE001 - 编排异常→固定 unready 503，不泄原文
        return JSONResponse(
            safe_unready_snapshot(settings.ARTIFACT_DRIVER), status_code=503
        )


@app.get("/metrics")
async def metrics():
    """低基数 Prometheus 指标；PROMETHEUS_ENABLED=false 时 404；渲染异常→固定纯文本 503。"""
    if not settings.PROMETHEUS_ENABLED:
        raise HTTPException(status_code=404, detail="metrics disabled")
    try:
        data, ctype = render_metrics()
    except Exception:  # noqa: BLE001 - 渲染异常→固定 503，不泄异常原文
        return Response(
            content="metrics unavailable\n", status_code=503,
            media_type="text/plain; charset=utf-8",
        )
    return Response(content=data, media_type=ctype)


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
app.include_router(admin_migration.router, prefix="/api/admin")
app.include_router(admin_monitor.router, prefix="/api/admin")
app.include_router(admin_import.router, prefix="/api/admin")
app.include_router(admin_session.router, prefix="/api/admin")
app.include_router(admin_cache.router, prefix="/api/admin")
app.include_router(admin_trash.router, prefix="/api/admin")
app.include_router(admin_trading_router, prefix="/api/admin")

@app.middleware("http")
async def _admin_read_observe(request, call_next):
    """记录 pm_admin_query_seconds / pm_admin_response_bytes（WP-07A；label endpoint/result）。"""
    if request.url.path.startswith("/api/admin/v2/"):
        import time

        from app.observability.metrics import ADMIN_QUERY_SECONDS, ADMIN_RESPONSE_BYTES

        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started
        response.headers["Cache-Control"] = "private, no-store"
        endpoint = _v2_endpoint_label(request.url.path)
        result = "ok" if response.status_code < 400 else "error"
        ADMIN_QUERY_SECONDS.labels(endpoint=endpoint, result=result).observe(elapsed)
        size = _response_size(response)
        ADMIN_RESPONSE_BYTES.labels(endpoint=endpoint, result=result).observe(size)
        return response
    return await call_next(request)


def _response_size(response) -> int:
    """不消费 StreamingResponse body；优先使用 ASGI 已生成的 Content-Length。"""
    raw = response.headers.get("content-length")
    if raw and raw.isdigit():
        return int(raw)
    body = getattr(response, "body", None)
    return len(body) if body is not None else 0


def _v2_endpoint_label(path: str) -> str:
    """/api/admin/v2/<seg>/... → v2/<seg>（label 只含 endpoint，不含业务 ID）。"""
    marker = "/api/admin/v2/"
    idx = path.find(marker)
    if idx < 0:
        return "non-v2"
    rest = path[idx + len(marker):]
    seg = rest.split("/")[0] if rest else ""
    return f"v2/{seg}" if seg else "v2"
  # /api/admin/trading/runtime + /api/admin/v2/*
app.include_router(web_seo.router)  # /sitemap.xml /robots.txt /{key}.txt 根路径

# 隐私文件代理 + 数据字典公开端点（不走 /api/admin 前缀）
app.include_router(file_proxy_router, prefix="/api")
app.include_router(dict_public.router, prefix="/api")

# client 端
app.include_router(client_user.router, prefix="/api/client")
app.include_router(client_message.router, prefix="/api/client")


# ---- 静态文件（public 存储，外网直接访问）----
import os
from fastapi.staticfiles import StaticFiles
_storage_public = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "public")
os.makedirs(_storage_public, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=_storage_public), name="uploads")
