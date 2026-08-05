"""定时任务 / 队列监控 admin 端.

只读 (list/queue) 用 admin:task_monitor:list 权限, 手动触发用 admin:task_monitor:trigger。
"""
from fastapi import APIRouter, Request, Depends

from app.deps import AuthInfo, require_perms
from app.logics.task_monitor import task_monitor_logic
from app.logics.base import BizError
from app.utils.response import ok, fail

router = APIRouter()

_perm_list = require_perms("admin:task_monitor:list")
_perm_trigger = require_perms("admin:task_monitor:trigger")


@router.get("/task_monitor/tasks")
async def list_tasks(auth: AuthInfo = Depends(_perm_list)):
    try:
        data = await task_monitor_logic.list_tasks()
        return ok(data)
    except BizError as e:
        return fail(e.msg, e.code)


@router.post("/task_monitor/trigger")
async def trigger(request: Request, auth: AuthInfo = Depends(_perm_trigger)):
    body = await request.json()
    class_name = body.get("task")
    if not class_name:
        return fail("缺少 task 参数")
    try:
        name = await task_monitor_logic.trigger(class_name)
        return ok(msg=f"已触发「{name}」，worker 将随后执行")
    except BizError as e:
        return fail(e.msg, e.code)


@router.get("/task_monitor/queue")
async def queue_status(auth: AuthInfo = Depends(_perm_list)):
    try:
        data = await task_monitor_logic.queue_status()
        return ok(data)
    except BizError as e:
        return fail(e.msg, e.code)
