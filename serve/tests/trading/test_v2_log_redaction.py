"""
WP-00d1 技术日志脱敏与上下文验收测试。

覆盖：嵌套 dict / headers / URL userinfo / exception / prompt·body·tool payload 全部脱敏；
完整输出无注入 secret；context 嵌套恢复与 asyncio task 隔离；trace/span 注入；重复 configure
无重复行；未知 context key ValueError；repr 抛错对象安全占位；敏感 key=value/JSON/Authorization
清洗。
"""

import asyncio
import io
import json
import logging
import sys

import pytest

from app.observability import (
    bind_log_context,
    configure_logging,
    configure_tracing,
    get_log_context,
    redact,
    shutdown_tracing,
    start_span,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_LOGGER = logging.getLogger("test_v2_log")


@pytest.fixture
def capture(capsys):
    """configure 日志 handler 并返回捕获器（每次清空 stderr 缓冲区）。"""

    def _emit(*, json_output=True, service="pollymarket-v2", version="dev",
              level="INFO", callable_=None):
        configure_logging(level=level, json_output=json_output,
                          service=service, version=version)
        capsys.readouterr()  # 清空旧输出
        _LOGGER.info("hello world")
        if callable_ is not None:
            callable_()
        return capsys.readouterr().err

    return _emit


def _json_lines(out: str) -> list[dict]:
    return [json.loads(line) for line in out.strip().splitlines() if line.strip()]


def test_redact_nested_dict_headers_and_userinfo():
    payload = {
        "headers": {"Authorization": "Bearer secret-token-123", "Cookie": "sid=abc"},
        "url": "https://user:pass@example.com/api",
        "config": {"password": "hunter2", "api_key": "AKIAX", "name": "alice"},
        "nested": {"deep": {"token": "t0k3n", "ok": True}},
    }
    out = redact(payload)
    assert out["headers"]["Authorization"] == "[REDACTED]"
    assert out["headers"]["Cookie"] == "[REDACTED]"
    assert "pass" not in out["url"] and "[REDACTED]" in out["url"]
    assert out["config"]["password"] == "[REDACTED]"
    assert out["config"]["api_key"] == "[REDACTED]"
    assert out["config"]["name"] == "alice"
    assert out["nested"]["deep"]["token"] == "[REDACTED]"
    assert out["nested"]["deep"]["ok"] is True
    # 不修改调用者对象
    assert payload["config"]["password"] == "hunter2"


def test_redact_string_authorization_userinfo_and_kv():
    assert redact("Bearer abc123def") == "Bearer [REDACTED]"
    assert redact("https://u:p@host/x") == "https://[REDACTED]@host/x"
    assert redact("password=hunter2&name=x") == "password=[REDACTED]&name=x"
    assert redact('{"password": "hunter2", "name": "x"}') == \
        '{"password": "[REDACTED]", "name": "x"}'


def test_redact_prompt_body_tool_payload():
    out = redact({
        "prompt": "please act as...",
        "request_body": {"text": "sensitive"},
        "tool_input": "raw tool args",
        "tool_output": "tool result",
        "response": "model response",
        "keep": {"report": "ok"},
    })
    assert out["prompt"] == "[REDACTED]"
    assert out["request_body"] == "[REDACTED]"
    assert out["tool_input"] == "[REDACTED]"
    assert out["tool_output"] == "[REDACTED]"
    assert out["response"] == "[REDACTED]"
    assert out["keep"]["report"] == "ok"


def test_redact_unknown_keys_kept_and_business_id_kept():
    out = redact({"chain_id": "c1", "execution_id": "e1", "attempt_id": "a1"})
    assert out == {"chain_id": "c1", "execution_id": "e1", "attempt_id": "a1"}


def test_redact_depth_and_item_limits():
    deep = redact({"a": {"b": {"c": {"d": {"e": {"f": {"g": "x"}}}}}}})
    assert deep["a"]["b"]["c"]["d"]["e"]["f"]["g"] == "[TRUNCATED]"
    big = redact({"password": "x", **{str(i): i for i in range(100)}})
    assert big["password"] == "[REDACTED]"
    assert big["..."] == "[TRUNCATED]"
    assert len(big) <= 66  # 64 items + key + truncation marker


def test_redact_unrepr_presentable():
    class _Broken:
        def __repr__(self):
            raise RuntimeError("boom")

    assert redact({"k": _Broken()})["k"] == "<unrepr-presentable>"
    assert redact(_Broken()) == "<unrepr-presentable>"


def test_json_log_line_fields_and_event(capture):
    out = capture()
    lines = _json_lines(out)
    assert len(lines) == 1
    entry = lines[0]
    assert entry["level"] == "INFO"
    assert entry["logger"] == "test_v2_log"
    assert entry["message"] == "hello world"
    assert entry["service"] == "pollymarket-v2"
    assert entry["service_version"] == "dev"
    assert "timestamp" in entry
    # UTC RFC3339 形态
    assert "T" in entry["timestamp"] and entry["timestamp"].endswith("Z")
    assert "trace_id" not in entry  # 无有效 span 不伪造


def test_json_log_event_field_and_context(capsys):
    configure_logging(level="INFO", json_output=True, service="s", version="v")
    capsys.readouterr()
    with bind_log_context(chain_id="c1", attempt_id="a1"):
        _LOGGER.info("ctx", extra={"event": "domain.event"})
    out = capsys.readouterr().err
    entry = _json_lines(out)[0]
    assert entry["event"] == "domain.event"
    assert entry["chain_id"] == "c1"
    assert entry["attempt_id"] == "a1"


def test_context_nested_restore(capsys):
    configure_logging(level="INFO", json_output=True, service="s", version="v")
    capsys.readouterr()
    with bind_log_context(chain_id="outer"):
        with bind_log_context(attempt_id="a1"):
            _LOGGER.info("inner")
        _LOGGER.info("outer-only")
    out = capsys.readouterr().err
    lines = _json_lines(out)
    assert lines[0]["chain_id"] == "outer"
    assert lines[0]["attempt_id"] == "a1"
    assert lines[1]["chain_id"] == "outer"
    assert "attempt_id" not in lines[1]  # 嵌套退出后恢复


def test_context_async_tasks_isolated():
    async def _task(attempt: str):
        with bind_log_context(attempt_id=attempt):
            await asyncio.sleep(0)
            return get_log_context()

    async def main():
        with bind_log_context(chain_id="outer"):
            r1, r2 = await asyncio.gather(_task("a1"), _task("a2"))
        return r1, r2

    r1, r2 = asyncio.run(main())
    assert r1["chain_id"] == "outer" and r1["attempt_id"] == "a1"
    assert r2["chain_id"] == "outer" and r2["attempt_id"] == "a2"


def test_unknown_context_key_valueerror():
    with pytest.raises(ValueError):
        with bind_log_context(foo="bar"):
            pass
    with pytest.raises(ValueError):
        with bind_log_context(prompt="nope"):
            pass  # prompt 不是合法 context key


def test_configure_repeat_no_duplicate_lines(capsys):
    configure_logging(level="INFO", json_output=True, service="s", version="v")
    configure_logging(level="INFO", json_output=True, service="s", version="v")
    configure_logging(level="INFO", json_output=True, service="s", version="v")
    capsys.readouterr()
    _LOGGER.info("once")
    out = capsys.readouterr().err
    assert len(_json_lines(out)) == 1  # 三条 handler 只剩一条


def test_configure_keeps_foreign_handler(capsys):
    foreign = logging.StreamHandler(io.StringIO())
    logging.getLogger().addHandler(foreign)
    try:
        configure_logging(level="INFO", json_output=True, service="s", version="v")
        configure_logging(level="INFO", json_output=True, service="s", version="v")
        assert foreign in logging.getLogger().handlers  # 非 V2 handler 不被删除
    finally:
        logging.getLogger().removeHandler(foreign)


def test_text_log_line_no_secret(capture):
    def _emit_extra():
        _LOGGER.info("https://user:secret@host and Bearer abc")

    out = capture(json_output=False, callable_=_emit_extra)
    assert "secret" not in out.split("https://")[1].split(" ")[0].replace("@host", "").replace("://", "")
    assert "Bearer [REDACTED]" in out
    assert "user:secret" not in out


def test_trace_and_span_ids_in_json(capsys):
    exporter = InMemorySpanExporter()
    provider = configure_tracing(
        enabled=True, endpoint="https://otel.example:4318",
        allow_insecure_http=False, ratio=1.0, timeout_s=5,
        service="s", version="v", exporter=exporter)
    configure_logging(level="INFO", json_output=True, service="s", version="v")
    capsys.readouterr()
    try:
        with start_span("root", operation="cognition"):
            _LOGGER.info("inside span")
        provider.force_flush()
    finally:
        shutdown_tracing()
    out = capsys.readouterr().err
    entry = _json_lines(out)[0]
    assert "trace_id" in entry and len(entry["trace_id"]) == 32
    assert "span_id" in entry and len(entry["span_id"]) == 16
    assert entry["trace_id"] == format(
        exporter.get_finished_spans()[0].get_span_context().trace_id, "032x")


# ---------------- R1：坏 __str__ 安全格式化 ----------------

def _emit_isolated(monkeypatch, *, level="INFO", json_output=True, exc=False):
    """彻底隔离 pytest 注入的 root log handlers（pytest 会用 base Formatter 格式化坏 __str__
    参数而冒泡），并 monkeypatch `sys.stderr` 捕获本模块 handler 输出——只验证本模块的格式化
    边界。返回捕获的 stderr 文本。"""
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers[:] = []
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    try:
        configure_logging(level=level, json_output=json_output, service="s", version="v")
        if exc:
            try:
                raise _BadStrExc()
            except _BadStrExc:
                _LOGGER.exception("failed op")
        else:
            _LOGGER.info("value=%s", _BadStrArg())
        return buf.getvalue()
    finally:
        root.handlers[:] = saved


class _BadStrArg:
    def __str__(self):
        raise RuntimeError("TOPSECRET leaked from arg str")

    def __repr__(self):
        return "_BadStrArg@0xDEADBEEF"


class _BadStrExc(Exception):
    def __str__(self):
        raise RuntimeError("TOPSECRET leaked from exc str")


def test_bad_message_str_json_safe(monkeypatch):
    """`%s` 参数 __str__ 抛错 → JSON 输出安全占位符（仅异常类型名），无 secret/args/repr/
    "Logging error"。"""
    out = _emit_isolated(monkeypatch)
    assert "Logging error" not in out
    assert "TOPSECRET" not in out
    assert "0xDEADBEEF" not in out
    assert "value=" not in out
    lines = _json_lines(out)
    assert len(lines) == 1
    entry = lines[0]
    assert entry["message"] == "<log-message-format-failed: RuntimeError>"
    assert entry["level"] == "INFO"


def test_bad_message_str_text_safe(monkeypatch):
    out = _emit_isolated(monkeypatch, json_output=False)
    assert "Logging error" not in out
    assert "TOPSECRET" not in out
    assert "0xDEADBEEF" not in out
    assert "<log-message-format-failed: RuntimeError>" in out


def test_bad_exc_str_json_safe(monkeypatch):
    """exception 文本格式化抛错 → JSON 输出受控占位符（stdlib 对坏 __str__ 优雅降级，
    无 TOPSECRET；_safe_exc_text 额外防御）。"""
    out = _emit_isolated(monkeypatch, exc=True)
    assert "Logging error" not in out
    assert "TOPSECRET" not in out
    lines = _json_lines(out)
    assert len(lines) == 1
    entry = lines[0]
    assert entry["level"] == "ERROR"
    assert "exception" in entry  # 受控异常摘要（含类型名，无原始消息）


def test_bad_str_does_not_break_business(monkeypatch):
    """日志参数坏 __str__ 不影响后续正常日志与业务路径。"""
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers[:] = []
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    try:
        configure_logging(level="INFO", json_output=True, service="s", version="v")
        _LOGGER.info("value=%s", _BadStrArg())
        _LOGGER.info("normal after failure")
    finally:
        root.handlers[:] = saved
    entries = _json_lines(buf.getvalue())
    assert len(entries) == 2
    assert entries[0]["message"] == "<log-message-format-failed: RuntimeError>"
    assert entries[1]["message"] == "normal after failure"


def test_redact_cookie_and_session_forms():
    # R3：Cookie/Set-Cookie 从 header 名后整行变红（不是第一个分号）
    assert "TOPSECRET" not in str(redact("Cookie: sid=abc123; theme=dark"))
    assert redact("Cookie: sid=abc123; theme=dark") == "Cookie: [REDACTED]"
    assert "secret" not in redact("Set-Cookie: session=xyz789")
    assert redact("Set-Cookie: session=xyz789") == "Set-Cookie: [REDACTED]"
    # 独立 cookie/session 凭证键值（无 header 前缀）仍按 key=value 脱敏
    assert redact("sid=abc123") == "sid=[REDACTED]"
    assert redact("jwt=eyJhbGciOiJIUzI1NiJ9") == "jwt=[REDACTED]"
    # 词边界：side= / obsession= 不误伤
    assert redact("side=left") == "side=left"
    assert redact("obsession=none") == "obsession=none"


# ---------------- R2：共用敏感文本完整矩阵 ----------------

# 共享表驱动矩阵：日志与 span exporter 用同一份覆盖。
# 每一项 = (输入文本, 该值中必须被脱敏的禁止子串列表)。6 个独立复现 + PEM + Set-Cookie。
SENSITIVE_MATRIX = [
    ("private_key=ZXCV1234", ["ZXCV1234"]),
    ("PRIVATE KEY=ZXCV9999", ["ZXCV9999"]),
    ("private-key=ZXCV-7", ["ZXCV-7"]),
    ("response_body=MODEL_RESPONSE_789", ["MODEL_RESPONSE_789"]),
    ("request-body=REQ_BODY_111", ["REQ_BODY_111"]),
    ("tool_output=TOOL_RESULT_456", ["TOOL_RESULT_456"]),
    ("tool_input=TOOL_INPUT_222", ["TOOL_INPUT_222"]),
    ("raw_payload=RAW_DATA_321", ["RAW_DATA_321"]),
    ("payload=PLD_444", ["PLD_444"]),
    ("token=TOKEN_VALUE_999", ["TOKEN_VALUE_999"]),
    ("access_token=AT_555", ["AT_555"]),
    ("refresh-token=RT_666", ["RT_666"]),
    ("id_token=IT_777", ["IT_777"]),
    ("Set-Cookie: opaque=COOKIE_VALUE_123", ["COOKIE_VALUE_123"]),
    ("set_cookie=SC_888", ["SC_888"]),
    ("Authorization: Bearer authval999", ["authval999"]),
    ("Basic base64cred==", ["base64cred"]),
    ("https://alice:hunter2@host/path", ["hunter2", "alice:"]),
    ("password=pass99", ["pass99"]),
    ("secret_key=sec88", ["sec88"]),
    ("passphrase=pp77", ["pp77"]),
    ("signature=sig66", ["sig66"]),
    ("credential=cred55", ["cred55"]),
    ("prompt=prompt44", ["prompt44"]),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEvQ==\n-----END RSA PRIVATE KEY-----",
     ["MIIEvQ=="]),
    ("-----BEGIN EC PRIVATE KEY-----\nMIIB\n-----END EC PRIVATE KEY-----", ["MIIB"]),
    ("-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----", ["AAAA"]),
    # R5：敏感 assignment 前允许存在普通日志/句子/JSON 前缀，前缀不得吞掉敏感 key。
    ("prefix token=R5_TOKEN_MARK", ["R5_TOKEN_MARK"]),
    ("failed password=R5_PASSWORD_MARK status=500", ["R5_PASSWORD_MARK"]),
    ("note private key=R5_PRIVATE_KEY_MARK", ["R5_PRIVATE_KEY_MARK"]),
    ("x prompt=R5_PROMPT_MARK", ["R5_PROMPT_MARK"]),
    ("INFO Cookie: a=R5_COOKIE_MARK", ["R5_COOKIE_MARK"]),
    ("2026 INFO Set-Cookie: a=R5_SET_COOKIE_MARK; Path=/", ["R5_SET_COOKIE_MARK"]),
    ("meta access token = R5_ACCESS_MARK", ["R5_ACCESS_MARK"]),
    ('prefix {"response body": "R5_BODY_MARK"}', ["R5_BODY_MARK"]),
]

# 非敏感反例（≥8）：不得被过度清洗
NON_SENSITIVE_EXAMPLES = [
    "side=YES", "content_type=json", "model=grok-3", "attempt_id=a1",
    "chain_id=c1", "status=ok", "result=pass", "hash=abc123def",
    "episode_id=e1", "execution_id=ex1",
]


@pytest.mark.parametrize("text,forbidden", SENSITIVE_MATRIX,
                         ids=[t[0][:28] for t in SENSITIVE_MATRIX])
def test_redact_full_sensitive_matrix(text, forbidden):
    cleaned = str(redact(text))
    for f in forbidden:
        assert f not in cleaned, f"secret {f!r} leaked from {text!r}"
    assert "[REDACTED]" in cleaned


@pytest.mark.parametrize("text", NON_SENSITIVE_EXAMPLES)
def test_redact_non_sensitive_not_overredacted(text):
    assert str(redact(text)) == text, f"non-sensitive {text!r} was altered"


def test_redact_cookie_set_cookie_multi_header_keeps_others():
    """Cookie/Set-Cookie header value 整体脱敏，换行后其他 header 保留。"""
    text = "Cookie: sid=abc123\nSet-Cookie: theme=dark\nX-Custom: keep-me"
    out = str(redact(text))
    assert "abc123" not in out and "dark" not in out
    assert out.startswith("Cookie: [REDACTED]")
    assert "Set-Cookie: [REDACTED]" in out
    assert "X-Custom: keep-me" in out


def test_redact_case_and_separator_variants():
    """大小写 + `-`/`_` 分隔变体全部命中。"""
    for t, f in [("PRIVATE_KEY=ABC", "ABC"), ("Private-Key=DEF", "DEF"),
                 ("API_KEY=XYZ", "XYZ"), ("AccessToken=QWER", "QWER"),
                 ("SET-COOKIE: val=secret1", "secret1")]:
        out = str(redact(t))
        assert f not in out, f"variant {t!r} leaked {f!r}"
        assert "[REDACTED]" in out


def test_redact_pem_multiline_fully_hidden():
    pem = ("-----BEGIN ENCRYPTED PRIVATE KEY-----\n"
           "Proc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,ABCD1234\n"
           "MIIEvQIBADANBgkqhkiG9w0BAQ\n"
           "-----END ENCRYPTED PRIVATE KEY-----")
    out = str(redact(pem))
    assert "MIIEvQ" not in out
    assert "ABCD1234" not in out
    assert "[REDACTED]" in out


def test_redact_long_string_truncated_before_clean():
    """超长输入先有界截断再清洗；不复制无限字符串。"""
    long_secret = "secret_value=" + "A" * 10000
    out = str(redact(long_secret))
    assert "A" * 10000 not in out            # 不得复制无限内容
    assert len(out) < 4200                    # 截断到 MAX_CLEAN_STRING_LEN(4096) + 标记
    assert "[REDACTED]" in out or "[TRUNCATED]" in out


# ---------------- R3：redactor 解析边界 ----------------

def _pem(kind: str, body_len: int, *, include_end: bool = True) -> str:
    begin = f"-----BEGIN {kind} PRIVATE KEY-----\n"
    end = f"\n-----END {kind} PRIVATE KEY-----"
    body = "BODY_" + "A" * body_len
    return begin + body + (end if include_end else "")


@pytest.mark.parametrize("body_len,include_end,marker", [
    (4095, True, "BODY_"),          # 截断边界下方，完整
    (4095, False, "BODY_"),         # 截断边界下方，无 END
    (4096, True, "BODY_"),          # 恰在截断边界
    (4096, False, "BODY_"),         # 恰在截断边界，无 END
    (4097, True, "BODY_"),          # 超截断边界，完整
    (4097, False, "BODY_"),         # 超截断边界，无 END（END 在窗口外）
    (10000, True, "BODY_"),         # 长 PEM，完整（截断后 BEGIN 在窗口内）
    (10000, False, "BODY_"),        # 长 PEM，无 END
    (200, True, "BODY_"),           # 短完整
    (200, False, "BODY_"),          # 短无 END
])
def test_redact_pem_lengths(body_len, include_end, marker):
    """长 PEM：完整块与 END 在窗口外/完全无 END 均不泄露主体。"""
    pem = _pem("RSA", body_len, include_end=include_end)
    out = str(redact(pem))
    assert marker not in out
    assert "[REDACTED]" in out


@pytest.mark.parametrize("kind", ["RSA", "EC", "OPENSSH", "DSA", "ENCRYPTED"])
def test_redact_pem_kinds_without_end(kind):
    """各种 PRIVATE KEY 类型，无 END 也被整段脱敏（BEGIN 到窗口末尾）。"""
    pem = _pem(kind, 100, include_end=False)
    out = str(redact(pem))
    assert "BODY_" not in out
    assert "[REDACTED]" in out


def test_redact_pem_end_outside_4096_window():
    """END 在保留窗口之外：BEGIN 出现在窗口内，从 BEGIN 到窗口末尾全部红。"""
    begin = "-----BEGIN RSA PRIVATE KEY-----\n"
    body = "BODY_" + "A" * 4080
    # END 在 4096 截断边界之后
    pem = begin + body + "\n-----END RSA PRIVATE KEY-----"
    out = str(redact(pem))
    assert "BODY_" not in out
    assert "A" * 4080 not in out


@pytest.mark.parametrize("text", [
    'prompt="Tell me TOPSECRET now"',
    'prompt="a,b;c d"',
    'prompt="esc\\"aped x"',
    "prompt='single quoted TOPSECRET'",
    '{"prompt": "line1\nline2 TOPSECRET"}',
    'request_body={"a": 1, "b": "TOPSECRET"}',
])
def test_redact_quoted_value_full(text):
    """quoted value 覆盖空格/逗号/分号/escaped quote/多行 JSON，直到配对引号。"""
    out = str(redact(text))
    assert "TOPSECRET" not in out
    assert "[REDACTED]" in out


def test_redact_unclosed_quoted_to_window_end():
    """未闭合 quote 从起始引号清洗到窗口末尾（跨行）。"""
    out = str(redact('prompt="unclosed\nstill TOPSECRET'))
    assert "TOPSECRET" not in out
    assert "unclosed" not in out


def test_redact_unquoted_sentence_to_line_end():
    """无引号 body/prompt/tool/raw payload 保守清洗到行尾。"""
    for text in ("prompt=Tell me TOPSECRET now",
                 "tool_output=TOOL_RESULT_456 more text",
                 "raw_payload=RAW_DATA_321 trailing"):
        out = str(redact(text))
        assert "TOPSECRET" not in out
        assert "TOOL_RESULT_456" not in out
        assert "RAW_DATA_321" not in out
        assert "[REDACTED]" in out


def test_redact_cookie_multiple_opaque_values():
    """Cookie 多 opaque cookie：从 header 名后整行红，非第一个分号。"""
    out = str(redact("Cookie: a=SECRET1; opaque=SECRET2; theme=dark"))
    assert "SECRET1" not in out and "SECRET2" not in out
    assert out == "Cookie: [REDACTED]"


def test_redact_set_cookie_attributes():
    """Set-Cookie 带属性（Path/HttpOnly）整行红。"""
    out = str(redact("Set-Cookie: opaque=COOKIE_VALUE_123; Path=/; HttpOnly"))
    assert "COOKIE_VALUE_123" not in out
    assert out == "Set-Cookie: [REDACTED]"


@pytest.mark.parametrize("sep", ["\n", "\r\n"])
def test_redact_cookie_line_and_crlf(sep):
    """Cookie 整行到行尾（LF 与 CRLF 均覆盖）；换行后其他 header 保留。"""
    out = str(redact(f"Cookie: a=SECRET1{sep}X-Custom: keep-me"))
    assert "SECRET1" not in out
    assert "X-Custom: keep-me" in out
    assert out.startswith("Cookie: [REDACTED]")


def test_redact_output_length_cap():
    """输出上限固定且不超过 MAX_CLEAN_STRING_LEN + len('[TRUNCATED]')。"""
    long = "prompt=" + "X" * 10000
    out = str(redact(long))
    assert len(out) <= 4096 + len("[TRUNCATED]") + 32  # key/sep/标记小余量
    assert "X" * 10000 not in out


def test_redact_non_sensitive_still_untouched_r3():
    """R3 不回归：现有非敏感反例全部原样。"""
    for text in NON_SENSITIVE_EXAMPLES:
        assert str(redact(text)) == text, f"altered non-sensitive {text!r}"


# ---------------- R4：确定性 scanner ----------------

R4_REPROS = [
    'prompt="don\'t reveal TOPSECRET now',
    "prompt='say \"hello\" then TOPSECRET now",
    'prompt="escaped \\" quote then TOPSECRET now',
]


@pytest.mark.parametrize("text", R4_REPROS)
def test_redact_r4_repros(text):
    """R4 三个复现（相反引号/转义引号）在日志与 exporter 全关。"""
    out = str(redact(text))
    assert "TOPSECRET" not in out
    assert "[REDACTED]" in out


def test_redact_quote_state_table():
    """quote 状态表：配对/未闭合/单双引号/相反引号/escaped quote/偶奇反斜杠/CRLF/多行。"""
    paired = [
        'prompt="paired TOPSECRET"',
        "prompt='paired TOPSECRET'",
        'prompt="esc\\"aped TOPSECRET"',       # escaped quote 内
        'prompt="even\\\\ backslash TOPSECRET"',  # 偶反斜杠（`\\` 转义反斜杠）
        '{"prompt": "line1\nline2 TOPSECRET"}',   # 多行 JSON
    ]
    for text in paired:
        out = str(redact(text))
        assert "TOPSECRET" not in out, f"paired leak: {text!r}"
        assert "[REDACTED]" in out
    unclosed = [
        'prompt="unclosed\nstill TOPSECRET',   # 未闭合跨行
        'prompt="a\r\nb TOPSECRET',            # 未闭合 CRLF
        "prompt='unclosed TOPSECRET",          # 未闭合单引号
        'prompt="don\'t reveal TOPSECRET now', # 未闭合 + 相反引号
    ]
    for text in unclosed:
        out = str(redact(text))
        assert "TOPSECRET" not in out, f"unclosed leak: {text!r}"
        assert "[REDACTED]" in out


def test_redact_odd_even_backslash():
    """奇数反斜杠转义引号不算闭合；偶数反斜杠后引号算闭合。"""
    # 奇数反斜杠：`\"` 是转义引号，不闭合 → 未闭合到窗口末尾，TOPSECRET 全红
    out = str(redact('prompt="escaped \\" quote TOPSECRET'))
    assert "TOPSECRET" not in out
    # 偶数反斜杠：`\\` 转义反斜杠，其后 `"` 闭合 → 只替换 value；引号外 TOPSECRET 是
    # 独立文本（非 value 一部分），逐字符扫描保留属正确行为
    out2 = str(redact('prompt="even\\\\" TOPSECRET'))
    assert "even" not in out2
    assert "[REDACTED]" in out2
    # `\\` + `\"`：`\"` 转义引号 → 引号不闭合 → 未闭合到窗口末尾，TOPSECRET 全红
    out3 = str(redact('prompt="even\\\\\\" TOPSECRET'))
    assert "TOPSECRET" not in out3


def test_redact_repeated_markers_length_hard_cap():
    """重复 1000 个 `token=x` 也不能扩张超过上限（最终硬裁剪）。"""
    text = ";".join(["token=x"] * 1000)
    out = str(redact(text))
    cap = 4096 + len("[TRUNCATED]")
    assert len(out) <= cap


def test_redact_cookie_repeated_hard_cap():
    text = "".join(["Cookie: a=1\n"] * 2000)
    out = str(redact(text))
    cap = 4096 + len("[TRUNCATED]")
    assert len(out) <= cap


def test_redact_bytes_never_output_content():
    """bytes/bytearray 不输出内容，只输出固定 `<bytes length=N>`。"""
    assert redact(b"TOPSECRET content") == "<bytes length=17>"
    assert redact(bytearray(b"abc")) == "<bytes length=3>"
    assert redact({"k": b"TOPSECRET"})["k"] == "<bytes length=9>"


def test_redact_fixed_seed_fuzz_500():
    """固定 seed 生成 ≥500 个含 quote/backslash/delimiter 的字符串：不抛异常、输出有界；
    嵌入 `prompt=` + marker 的样本不得保留 marker。"""
    import random
    rng = random.Random(20260808)
    chars = "abc XYZ:;,&=_'\"\\\r\n\t.0123"
    for _ in range(500):
        n = rng.randint(0, 200)
        s = "".join(rng.choice(chars) for _ in range(n))
        out = str(redact(s))
        assert isinstance(out, str)
        assert len(out) <= 4096 + len("[TRUNCATED]") + 32
        if "prompt=TOPSECRETMARKER" in s:
            assert "TOPSECRETMARKER" not in out


def test_redact_seeded_prompt_samples():
    """固定样本：prompt 以双引号开头、引号永不闭合 → marker 必被清洗。
    随机串仅含 a/b/反斜杠（无成对引号），确保 marker 始终在 value 内。"""
    import random
    rng = random.Random(7)
    leak = 0
    for _ in range(200):
        body = "".join(rng.choice("ab \\") for _ in range(30))
        s = 'prompt="' + body + "TOPSECRETMARKER"
        out = str(redact(s))
        if "TOPSECRETMARKER" in out:
            leak += 1
    assert leak == 0


# ---------------- R5：敏感 key lexical boundary ----------------

R5_PREFIX_REPROS = [
    ("prefix token=TOPSECRET", "TOPSECRET"),
    ("failed password=hunter2", "hunter2"),
    ("note private key=ZXCV1234", "ZXCV1234"),
    ("x prompt=TOPSECRET", "TOPSECRET"),
    ("INFO Cookie: a=SECRET1", "SECRET1"),
]


@pytest.mark.parametrize("text,marker", R5_PREFIX_REPROS)
def test_redact_r5_prefix_repros(text, marker):
    out = str(redact(text))
    assert marker not in out
    assert "[REDACTED]" in out


@pytest.mark.parametrize("surface", [
    "private key", "private_key", "private-key", "PrivateKey",
    "access token", "access_token", "access-token", "AccessToken",
    "response body", "response_body", "response-body", "ResponseBody",
])
def test_redact_r5_multiword_and_separator_variants(surface):
    marker = "R5_VARIANT_MARK"
    out = str(redact(f'prefix "{surface}": "{marker}" tail=keep'))
    assert marker not in out
    assert out.startswith('prefix "')
    assert "tail=keep" in out


def test_redact_r5_multiple_assignments_preserve_non_sensitive_parts():
    out = str(redact("foo=keep token=R5_ONE password=R5_TWO tail=ok"))
    assert "R5_ONE" not in out and "R5_TWO" not in out
    assert "foo=keep" in out and "tail=ok" in out
    assert out.count("[REDACTED]") == 2


@pytest.mark.parametrize("text", [
    "prefixtoken=keep",
    "tokenizer=keep",
    "my_token_hint=keep",
    "content_type=json",
    "side=YES",
    "not a prompt text",
])
def test_redact_r5_exact_boundary_negative_examples(text):
    assert str(redact(text)) == text


def test_redact_r5_fixed_seed_prefixed_assignments_500():
    """随机前导 + lexical boundary + surface 仍必须识别；marker 泄漏数固定为 0。"""
    import random

    rng = random.Random(500_20260808)
    prefixes = ["prefix ", "[INFO] ", "2026-08-08T00:00:00Z ", ";", "(", "{", "note, "]
    surfaces = [
        "token", "password", "private key", "private_key", "private-key", "PrivateKey",
        "access token", "access_token", "AccessToken", "response body", "Set-Cookie",
    ]
    for i in range(500):
        marker = f"R5_FUZZ_MARK_{i}"
        key = rng.choice(surfaces)
        if rng.choice([False, True]):
            key = f'"{key}"'
        delimiter = rng.choice(["=", ":", " = ", " : "])
        value = marker if rng.choice([False, True]) else f'"{marker}"'
        text = rng.choice(prefixes) + key + delimiter + value
        out = str(redact(text))
        assert marker not in out, f"prefixed sensitive value leaked: {text!r}"
        assert len(out) <= 4096 + len("[TRUNCATED]")
