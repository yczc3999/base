from fastapi import APIRouter
from app.logics.admin_operation_log import admin_operation_log_logic
from app.logics.admin_login_log import admin_login_log_logic
from app.controllers.base import crud_router
from app.deps import require_admin

router = APIRouter()

# 操作日志
router.include_router(crud_router(
    "operationLog", admin_operation_log_logic,
    tags=["admin-log"],
    auth_dep=require_admin,
    perms_prefix="admin:log:operation",
))

# 登录日志
router.include_router(crud_router(
    "loginLog", admin_login_log_logic,
    tags=["admin-log"],
    auth_dep=require_admin,
    perms_prefix="admin:log:login",
))
