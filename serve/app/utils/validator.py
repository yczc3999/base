"""
通用参数校验器

对 dict 数据做规则校验，适用于 CRUD 入口等无 Pydantic DTO 的场景。
校验失败抛出 BizError。

用法：
    from app.utils.validator import validate

    # 校验请求数据
    validate(data, {
        "username": "required|min:3|max:20|alpha_num",
        "password": "required|min:6",
        "email":    "email",
        "age":      "numeric|min:0|max:150",
        "phone":    "phone",
        "status":   "in:0,1",
    })

    # 部分校验（编辑时只校验传了的字段）
    validate(data, {
        "nickname": "max:20",
        "status":   "in:0,1",
    }, partial=True)

支持的规则（16 条）：
    required        必填（不能为 None / "" / 空白）
    min:N           最小值（数字）或最小长度（字符串）
    max:N           最大值（数字）或最大长度（字符串）
    length:N        精确长度
    alpha           纯字母
    numeric         纯数字
    alpha_num       字母 + 数字
    no_symbols      禁止特殊字符（只允许字母数字下划线中文）
    integer         必须是整数
    boolean         必须是布尔值
    email           邮箱格式
    phone           手机号（中国大陆 11 位）
    url             URL 格式
    in:a,b,c        值必须在列表中
    not_in:a,b,c    值不能在列表中
    regex:pattern   正则匹配
"""

import re
from app.logics.base import BizError

# 中国大陆手机号
_PHONE_RE = re.compile(r'^1[3-9]\d{9}$')
# 邮箱
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
# URL
_URL_RE = re.compile(r'^https?://\S+$')


def validate(data: dict, rules: dict, partial: bool = False, messages: dict = None):
    """
    校验 dict 数据

    :param data:     待校验数据
    :param rules:    校验规则 {"字段名": "规则1|规则2:参数"}
    :param partial:  部分校验模式（True 时跳过 data 中不存在的字段，编辑场景用）
    :param messages: 自定义错误信息 {"字段名.规则名": "自定义消息"}
    :raises BizError: 校验失败时抛出
    """
    messages = messages or {}
    errors = []

    for field, rule_str in rules.items():
        value = data.get(field)
        rule_list = rule_str.split("|")

        # partial 模式下，字段不存在则跳过（但存在的字段仍然校验）
        if partial and field not in data:
            continue

        for rule in rule_list:
            rule = rule.strip()
            if not rule:
                continue

            error = _check_rule(field, value, rule)
            if error:
                # 自定义消息覆盖
                key = f"{field}.{rule.split(':')[0]}"
                msg = messages.get(key, error)
                errors.append(msg)
                break  # 每个字段只报第一个错误

    if errors:
        raise BizError(errors[0], 422)


def _check_rule(field: str, value, rule: str) -> str | None:
    """
    单条规则校验

    :return: 错误信息字符串，通过返回 None
    """
    # 解析规则名和参数
    parts = rule.split(":", 1)
    name = parts[0]
    param = parts[1] if len(parts) > 1 else ""

    match name:
        case "required":
            if value is None or (isinstance(value, str) and value.strip() == ""):
                return f"{field} 不能为空"

        case "min":
            if value is None:
                return None  # 非 required 时允许空
            n = float(param)
            if isinstance(value, (int, float)):
                if value < n:
                    return f"{field} 不能小于 {param}"
            elif isinstance(value, str):
                if len(value) < n:
                    return f"{field} 长度不能少于 {param} 个字符"

        case "max":
            if value is None:
                return None
            n = float(param)
            if isinstance(value, (int, float)):
                if value > n:
                    return f"{field} 不能大于 {param}"
            elif isinstance(value, str):
                if len(value) > n:
                    return f"{field} 长度不能超过 {param} 个字符"

        case "length":
            if value is None:
                return None
            n = int(param)
            if isinstance(value, str) and len(value) != n:
                return f"{field} 长度必须为 {param} 个字符"

        case "alpha":
            if value and isinstance(value, str) and not value.isalpha():
                return f"{field} 只能包含字母"

        case "numeric":
            if value and isinstance(value, str) and not value.isdigit():
                return f"{field} 只能包含数字"

        case "alpha_num":
            if value and isinstance(value, str) and not value.isalnum():
                return f"{field} 只能包含字母和数字"

        case "email":
            if value and isinstance(value, str) and not _EMAIL_RE.match(value):
                return f"{field} 邮箱格式不正确"

        case "phone":
            if value and isinstance(value, str) and not _PHONE_RE.match(value):
                return f"{field} 手机号格式不正确"

        case "url":
            if value and isinstance(value, str) and not _URL_RE.match(value):
                return f"{field} URL 格式不正确"

        case "in":
            allowed = [v.strip() for v in param.split(",")]
            if value is not None and str(value) not in allowed:
                return f"{field} 的值必须是 {param} 之一"

        case "not_in":
            disallowed = [v.strip() for v in param.split(",")]
            if value is not None and str(value) in disallowed:
                return f"{field} 的值不能是 {param}"

        case "no_symbols":
            if value and isinstance(value, str) and not re.match(r'^[\w\u4e00-\u9fff]+$', value):
                return f"{field} 不能包含特殊字符"

        case "regex":
            if value and isinstance(value, str) and not re.match(param, value):
                return f"{field} 格式不正确"

        case "integer":
            if value is not None:
                try:
                    int(value)
                except (ValueError, TypeError):
                    return f"{field} 必须是整数"

        case "boolean":
            if value is not None and value not in (True, False, 0, 1, "0", "1", "true", "false"):
                return f"{field} 必须是布尔值"

    return None
