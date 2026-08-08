"""
V2 技术日志 primitives（WP-00d1）— JSON/redaction 日志，不接 lifespan。

- `configure_logging` 幂等：可重复调用不产生重复 handler，且不删除/篡改既有非 V2 handler
  （实际接入 main.py 留给 WP-00d2）。
- JSON 模式每条日志为单行对象：timestamp(UTC RFC3339)、level、logger、message、service、
  service_version；有值时加入 event、trace_id、span_id 与上下文。
- 上下文键固定 allowlist；未知键立即 ValueError，不静默扩张。contextvars 嵌套 bind 正确恢复，
  两个 asyncio task 不串 context。
- `redact` 有深度/元素上限的递归副本，不修改调用者对象；敏感类别值统一 `[REDACTED]`；
  字符串清洗 Bearer/Basic、URL userinfo、key=value/JSON secret；repr 自身抛错输出安全占位符，
  不让日志调用破坏业务路径。
- Trace 已激活时自动读取 32 位小写 trace_id / 16 位小写 span_id；无有效 span 时不伪造。
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import traceback
from contextlib import contextmanager
from typing import Iterator

# 允许的上下文字段（业务链 ID + 关联键，低基数，不含 prompt/body/凭据）
_CONTEXT_KEYS = frozenset({
    "chain_id",
    "causation_event_id",
    "attempt_id",
    "idempotency_key",
    "release_manifest_id",
    "forecast_episode_id",
    "submission_id",
    "economic_intent_id",
    "execution_id",
})

_log_context_var: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "pm_log_context", default={}
)

# ---- redaction ----

_MAX_DEPTH = 6
_MAX_ITEMS = 64

# key 归一化后含以下子串即视为敏感（值统一 [REDACTED]）
_SENSITIVE_SUBSTR = (
    "password",
    "passwd",
    "secret",
    "apikey",
    "api_key",
    "passphrase",
    "private_key",
    "signature",
    "access_token",
    "refresh_token",
    "credential",
    "prompt",
    "request_body",
    "response_body",
    "tool_input",
    "tool_output",
    "cookie",
    "authorization",
    "set_cookie",
)

# key 归一化后精确命中即视为敏感
_SENSITIVE_EXACT = frozenset({
    "auth",
    "request",
    "response",
    "body",
    "payload",
    "raw",
    "content",
    "token",
})

_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]+")
_USERINFO_RE = re.compile(r"(https?://)[^/@\s]+@")
# ---- 确定性有界 scanner（R4/R5：不用互相叠加的 quoted/KV 正则猜语法）----
# 允许保留简单 Bearer、URL userinfo 与 PEM/header marker 正则；assignment 的 quote 状态
# 一律由 scanner 处理。key 按大小写 + `-`/`_`/space/tab 归一后精确匹配冻结词表，不用任意子串。
_KEY_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
)
_KEY_SEPARATORS = frozenset("-_ \t")
MAX_ASSIGNMENT_KEY_SPAN = 64

# 敏感 assignment key（归一后精确匹配；归一去掉全部分隔符，覆盖 `access_token`/
# `access-token`/`AccessToken`/`access token` 等变体，不用任意子串）
_SENSITIVE_ASSIGNMENT_KEYS = frozenset({
    "password", "passwd", "secret", "secretkey", "clientsecret", "secretvalue",
    "secrettoken", "apikey", "authorization",
    "cookie", "setcookie", "passphrase", "privatekey", "signature",
    "accesstoken", "refreshtoken", "idtoken", "credential",
    "prompt", "requestbody", "responsebody", "toolinput", "tooloutput",
    "rawpayload", "payload", "token",
    "sid", "session", "jwt", "csrf", "auth",
})

# unquoted 时清洗到行尾的 key（body/prompt/tool/raw payload/cookie header；归一后形式）
_SENTENCE_KEYS = frozenset({
    "prompt", "requestbody", "responsebody", "toolinput", "tooloutput",
    "rawpayload", "payload", "cookie", "setcookie",
})

# 冻结词表按首字符和长度建立确定性查找表。最长优先只决定重叠 key（如 secret/secretkey）
# 的选择；每个输入起点最多查看 MAX_ASSIGNMENT_KEY_SPAN 个字符，词表为模块冻结常数。
_SENSITIVE_KEYS_BY_FIRST = {
    first: tuple(sorted(
        (key for key in _SENSITIVE_ASSIGNMENT_KEYS if key[0] == first),
        key=lambda key: (-len(key), key),
    ))
    for first in sorted({key[0] for key in _SENSITIVE_ASSIGNMENT_KEYS})
}


def _skip_ws(s: str, i: int) -> int:
    n = len(s)
    while i < n and s[i] in " \t":
        i += 1
    return i


def _line_end(s: str, start: int) -> int:
    n = len(s)
    i = start
    while i < n and s[i] not in "\r\n":
        i += 1
    return i


def _match_canonical_key(s: str, start: int, canonical: str):
    """在 ``start`` 前向匹配一个冻结 canonical key。

    输入 key 内可用 ``-``/``_``/space/tab 分隔，但不得跨行；完整 key 后只接受可选的
    matching quote、水平空白和 ``:``/``=``。返回 separator 后位置，失败返回 ``None``。
    原始候选跨度硬限制为 MAX_ASSIGNMENT_KEY_SPAN。
    """
    n = len(s)
    i = start
    j = 0
    while j < len(canonical):
        if i >= n or i - start >= MAX_ASSIGNMENT_KEY_SPAN:
            return None
        ch = s[i]
        if ch in _KEY_SEPARATORS:
            i += 1
            continue
        if ch.lower() != canonical[j]:
            return None
        i += 1
        j += 1

    # quoted JSON/header key：start 的前一字符若为 quote，只消费同类 closing quote；
    # 未带 opening quote 时不能把任意 quote 当作结构尾部。
    opening_quote = s[start - 1] if start > 0 and s[start - 1] in ("'", '"') else None
    if opening_quote is not None and i < n and s[i] == opening_quote:
        if i - start >= MAX_ASSIGNMENT_KEY_SPAN:
            return None
        i += 1
    while i < n and s[i] in " \t":
        if i - start >= MAX_ASSIGNMENT_KEY_SPAN:
            return None
        i += 1
    if i < n and s[i] in ":=":
        return i + 1
    return None


def _match_sensitive_key_at(s: str, i: int):
    """只在 lexical boundary 上匹配已知敏感 key，返回 ``(canonical, sep_end)``。

    非匹配绝不返回跨 token 的 skip 位置；调用者只前进一个字符。因此普通日志前缀不会
    吞掉后面的 ``token=`` / ``Cookie:`` 等真实 assignment。
    """
    n = len(s)
    if i >= n or (i > 0 and s[i - 1] in _KEY_CHARS):
        return None
    first = s[i].lower()
    candidates = _SENSITIVE_KEYS_BY_FIRST.get(first)
    if not candidates:
        return None
    for canonical in candidates:
        sep_end = _match_canonical_key(s, i, canonical)
        if sep_end is not None:
            return canonical, sep_end
    return None


def _consume_value(s: str, vstart: int, norm: str):
    """从 value 起点消费并决定替换区间。返回 (replacement, next_index)。
    quoted：只把与起始 quote 同类且未被奇数反斜杠转义的 quote 当结束；相反 quote 是
    普通字符。找到结束只替换 value；未找到则从 value 起点清洗到行尾/窗口末尾。"""
    n = len(s)
    if vstart >= n:
        return "[REDACTED]", n
    c = s[vstart]
    if c in ("'", '"'):
        quote = c
        k = vstart + 1
        while k < n:
            ch = s[k]
            if ch == "\\":
                k += 2  # 跳过转义字符（含转义反斜杠）
                continue
            if ch == quote:
                break
            k += 1
        if k < n and s[k] == quote:
            return f"{quote}[REDACTED]{quote}", k + 1
        # 未闭合 quote：从 value 起点清洗到窗口末尾（跨行；引号未闭合即 value 延伸到末尾）
        return "[REDACTED]", n
    if norm in _SENTENCE_KEYS:
        return "[REDACTED]", _line_end(s, vstart)
    k = vstart
    while k < n and s[k] not in " \t&,;\r\n":
        k += 1
    return "[REDACTED]", k


def _scan_assignments(s: str) -> str:
    """确定性扫描敏感 assignment/header；冻结 key/span 下 O(n)、额外空间 O(n)。"""
    out: list[str] = []
    i = 0
    copied_to = 0
    n = len(s)
    while i < n:
        matched = _match_sensitive_key_at(s, i)
        if matched is None:
            i += 1  # 失败只前进一个字符，不能跳过后续 lexical boundary
            continue
        norm, sep_end = matched
        vstart = _skip_ws(s, sep_end)
        redacted, next_i = _consume_value(s, vstart, norm)
        out.append(s[copied_to:vstart])
        out.append(redacted)
        copied_to = next_i
        i = next_i
    out.append(s[copied_to:])
    return "".join(out)


# PEM 私钥块（RSA/EC/OPENSSH/DSA/ENCRYPTED PRIVATE KEY）。
# 完整块：BEGIN..END；不完整块：保留窗口内出现合法 BEGIN 即从 BEGIN 到窗口末尾替换，
# 不得要求 END 存在才清洗（长 PEM 被截断时 END 在窗口外）。
_PEM_COMPLETE_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"
    r".*?"
    r"-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----",
    re.DOTALL,
)
_PEM_INCOMPLETE_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----.*$",
    re.DOTALL,
)

# 清洗输入长度上限：超限先有界截断再清洗，防日志/span 复制无限字符串
MAX_CLEAN_STRING_LEN = 4096


def _normalize_key(key: object) -> str:
    return str(key).lower().replace("-", "_")


def _key_sensitive(key: object) -> bool:
    norm = _normalize_key(key)
    if norm in _SENSITIVE_EXACT:
        return True
    return any(sub in norm for sub in _SENSITIVE_SUBSTR)


def _clean_string(value: str) -> str:
    """清洗字符串中的常见 secret 形态，不改变调用者对象。先做有界副本，再经
    PEM/Bearer/userinfo marker 正则 + 单次确定性 assignment scanner，最后硬裁剪。
    O(n)、O(n) 空间（n ≤ 4096）。"""
    if len(value) > MAX_CLEAN_STRING_LEN:
        value = value[:MAX_CLEAN_STRING_LEN] + "[TRUNCATED]"
    s = value
    # PEM marker：完整块先，不完整块后（BEGIN 到窗口末尾）
    s = _PEM_COMPLETE_RE.sub("[REDACTED]", s)
    s = _PEM_INCOMPLETE_RE.sub("[REDACTED]", s)
    # 简单结构形态
    s = _BEARER_RE.sub(lambda m: f"{m.group(1)} [REDACTED]", s)
    s = _USERINFO_RE.sub(r"\1[REDACTED]@", s)
    # 敏感 assignment / header：单次确定性 scanner（quote 状态全由 scanner 处理）
    s = _scan_assignments(s)
    # 最终硬裁剪：重复 1000 个 `token=x` / Cookie 行也不能扩张
    cap = MAX_CLEAN_STRING_LEN + len("[TRUNCATED]")
    if len(s) > cap:
        s = s[:MAX_CLEAN_STRING_LEN] + "[TRUNCATED]"
    return s


def _safe_repr(value: object) -> str:
    try:
        return repr(value)
    except Exception:  # noqa: BLE001 - repr 自身抛错不得破坏日志路径
        return "<unrepr-presentable>"


def _safe_record_message(record: logging.LogRecord) -> str:
    """安全取日志消息：`record.getMessage()` 可能因 `%s` 参数的 `__str__()` 抛普通 Exception
    （此时 stdlib 会输出 raw "Logging error" traceback + 异常消息 + 对象 repr，既泄密又破坏
    结构化日志）。此处仅允许异常类型名进入占位符，不包含异常 message、record.args、对象 repr
    或 traceback；不捕获 KeyboardInterrupt/SystemExit。"""
    try:
        return record.getMessage()
    except Exception as e:  # noqa: BLE001 - 格式化失败不得触发 stdlib Logging error
        return f"<log-message-format-failed: {type(e).__name__}>"


def _safe_exc_text(record: logging.LogRecord) -> str:
    """安全格式化 exception 文本：格式化过程本身抛普通 Exception 时输出仅含异常类型名的
    占位符；不包含异常 message/args/repr/traceback。"""
    try:
        return "".join(traceback.format_exception(*record.exc_info))
    except Exception as e:  # noqa: BLE001
        return f"<exception-format-failed: {type(e).__name__}>"


def _redact_value(value: object, depth: int) -> object:
    if depth > _MAX_DEPTH:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        out: dict = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= _MAX_ITEMS:
                out["..."] = "[TRUNCATED]"
                break
            if _key_sensitive(k):
                out[k] = "[REDACTED]"
            else:
                out[k] = _redact_value(v, depth + 1)
        return out
    if isinstance(value, list):
        out = [_redact_value(v, depth + 1) for v in value[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            out.append("[TRUNCATED]")
        return out
    if isinstance(value, tuple):
        out = [_redact_value(v, depth + 1) for v in value[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            out.append("[TRUNCATED]")
        return tuple(out)
    if isinstance(value, str):
        return _clean_string(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value  # 基本标量原样保留（bool 不得 repr 成 'True'）
    if isinstance(value, (bytes, bytearray)):
        # bytes/bytearray 不输出内容，只输出固定 `<bytes length=N>`（R4）
        return f"<bytes length={len(value)}>"
    return _safe_repr(value)


def redact(value: object) -> object:
    """对 dict/list/tuple 做有深度和元素上限的递归脱敏副本；超限写固定占位符。
    不修改调用者对象；`str()/repr()` 自身抛错的对象输出安全占位符。"""
    return _redact_value(value, 0)


# ---- trace id 读取（无有效 span 不伪造）----

def _current_trace_ids() -> tuple[str | None, str | None]:
    try:
        from opentelemetry import trace as _otel_trace

        span = _otel_trace.get_current_span()
        ctx = span.get_span_context()
        if not ctx.is_valid:
            return None, None
        return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    except Exception:  # noqa: BLE001 - OTel 未启用/异常时视为无 trace
        return None, None


def _rfc3339_utc() -> str:
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S") + f".{now.microsecond // 1000:03d}Z"


# ---- contextvars ----

def get_log_context() -> dict[str, str]:
    """当前上下文字段（副本）。"""
    return dict(_log_context_var.get())


@contextmanager
def bind_log_context(**fields: object) -> Iterator[None]:
    """进入时合并上下文字段（值转 str），退出时恢复之前状态；嵌套正确。"""
    for key in fields:
        if key not in _CONTEXT_KEYS:
            raise ValueError(f"unknown log context key: {key!r}")
    previous = _log_context_var.get()
    merged = {**previous, **{k: str(v) for k, v in fields.items()}}
    token = _log_context_var.set(merged)
    try:
        yield
    finally:
        _log_context_var.reset(token)


# ---- formatters ----

class _JsonFormatter(logging.Formatter):
    def __init__(self, service: str, version: str) -> None:
        super().__init__()
        self._service = service
        self._version = version

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": _rfc3339_utc(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(_safe_record_message(record)),
            "service": self._service,
            "service_version": self._version,
        }
        event = getattr(record, "event", None)
        if event is not None:
            entry["event"] = redact(event)
        ctx = get_log_context()
        if ctx:
            entry.update(ctx)
        tid, sid = _current_trace_ids()
        if tid:
            entry["trace_id"] = tid
        if sid:
            entry["span_id"] = sid
        if record.exc_info:
            entry["exception"] = redact(_safe_exc_text(record))
        try:
            return json.dumps(entry, ensure_ascii=False, default=_safe_repr)
        except Exception:  # noqa: BLE001 - 序列化失败不得破坏日志路径
            return json.dumps(
                {
                    "timestamp": _rfc3339_utc(),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": "<unserializable log record>",
                }
            )


class _TextFormatter(logging.Formatter):
    """非 JSON（本地/测试）单行格式，仍带 service/trace/context 与脱敏。"""

    def __init__(self, service: str, version: str) -> None:
        super().__init__()
        self._service = service
        self._version = version

    def format(self, record: logging.LogRecord) -> str:
        parts = [
            record.levelname,
            f"{self._service}/{self._version}",
        ]
        tid, sid = _current_trace_ids()
        if tid:
            parts.append(f"trace={tid}")
        if sid:
            parts.append(f"span={sid}")
        for key in _CONTEXT_KEYS:
            if key in get_log_context():
                parts.append(f"{key}={get_log_context()[key]}")
        out = " ".join(parts) + " " + redact(_safe_record_message(record))
        if record.exc_info:
            out += "\n" + redact(_safe_exc_text(record))
        return out


# ---- configure ----

_V2_HANDLER_ATTR = "_pm_v2_observability_handler"


def configure_logging(*, level: str, json_output: bool, service: str, version: str) -> None:
    """安装/替换 V2 日志 handler；幂等；不删除已有非 V2 handler。level 为
    DEBUG/INFO/WARNING/ERROR/CRITICAL（大小写不敏感）。"""
    level_name = str(level).upper()
    level_int = getattr(logging, level_name, None)
    if not isinstance(level_int, int):
        raise ValueError(
            f"level must be DEBUG/INFO/WARNING/ERROR/CRITICAL, got {level!r}"
        )
    root = logging.getLogger()
    # 仅移除自己先前安装的 V2 handler，绝不删除非 V2 handler
    for handler in list(root.handlers):
        if getattr(handler, _V2_HANDLER_ATTR, False):
            root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setLevel(level_int)
    if json_output:
        formatter = _JsonFormatter(service=service, version=version)
    else:
        formatter = _TextFormatter(service=service, version=version)
    handler.setFormatter(formatter)
    setattr(handler, _V2_HANDLER_ATTR, True)
    root.addHandler(handler)
    root.setLevel(level_int)
