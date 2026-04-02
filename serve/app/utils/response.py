"""
统一响应格式

所有接口返回相同的 JSON 结构：
    {"code": 0, "msg": "success", "data": ...}

使用方式：
    return ok({"list": [], "total": 0})     # 成功
    return ok(msg="操作成功")                 # 成功，无数据
    return fail("参数错误")                   # 失败，code=1
    return fail("请登录", code=401)           # 失败，指定 code
"""

from typing import Any


def ok(data: Any = None, msg: str = "success") -> dict:
    """
    成功响应

    :param data: 业务数据（任意类型）
    :param msg:  提示信息
    """
    return {"code": 0, "msg": msg, "data": data}


def fail(msg: str = "操作失败", code: int = 1) -> dict:
    """
    失败响应

    :param msg:  错误信息
    :param code: 错误码（1=通用失败，401=未登录，403=无权限）
    """
    return {"code": code, "msg": msg, "data": None}
