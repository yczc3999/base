"""Pydantic v2 响应/请求模型。

约定：所有 HTTP 接口返回统一的 {code, msg, data} 信封（见 app/utils/response.py）。
这里的模型只描述该信封的形状，不改变契约。
"""
