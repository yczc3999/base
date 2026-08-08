"""
WP-00d1 OTel Trace 传播与采样验收测试。

覆盖：ratio 0/1、三个 always-sample operation、父采样继承、W3C inject/extract、非法 carrier、
敏感 attribute 拒绝/脱敏、shutdown/reconfigure、disabled 零 exporter 调用。
"""

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from test_v2_log_redaction import NON_SENSITIVE_EXAMPLES, SENSITIVE_MATRIX

from app.observability.tracing import (
    ALWAYS_SAMPLE_OPERATIONS,
    configure_tracing,
    current_trace_ids,
    extract_trace_context,
    inject_trace_context,
    shutdown_tracing,
    start_span,
)


def _provider(exporter, ratio=1.0):
    return configure_tracing(
        enabled=True, endpoint="https://otel.example:4318",
        allow_insecure_http=False, ratio=ratio, timeout_s=5,
        service="test", version="dev", exporter=exporter)


def _finished(exporter):
    return [s for s in exporter.get_finished_spans()]


def test_disabled_returns_none_and_zero_exporter(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("OTLPSpanExporter must not be created when disabled")
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter", _boom)
    assert configure_tracing(
        enabled=False, endpoint="", allow_insecure_http=False, ratio=0.05,
        timeout_s=5, service="s", version="v") is None


def test_ratio_1_samples_root_span():
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=1.0)
    try:
        with start_span("root", operation="cognition"):
            pass
        provider.force_flush()
        assert len(_finished(exporter)) == 1
    finally:
        shutdown_tracing()


def test_ratio_0_drops_root_span():
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=0.0)
    try:
        with start_span("root", operation="cognition"):
            pass
        provider.force_flush()
        assert _finished(exporter) == []
    finally:
        shutdown_tracing()


@pytest.mark.parametrize("op", ["execution", "ledger", "reconciliation"])
def test_always_sample_operations_sample_at_ratio_0(op):
    assert op in ALWAYS_SAMPLE_OPERATIONS
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=0.0)
    try:
        with start_span("root", operation=op):
            pass
        provider.force_flush()
        spans = _finished(exporter)
        assert len(spans) == 1
        assert spans[0].name == "root"
        assert spans[0].attributes.get("operation") == op
    finally:
        shutdown_tracing()


def test_parent_sampled_inherited_by_child():
    """父 span 被采样（always-sample op），子 span 在 ratio=0 下仍继承采样。"""
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=0.0)
    try:
        with start_span("parent", operation="execution"):
            with start_span("child", operation="cognition"):
                pass
        provider.force_flush()
        names = {s.name for s in _finished(exporter)}
        assert names == {"parent", "child"}
    finally:
        shutdown_tracing()


def test_parent_dropped_inherited_drop():
    """父 span 未被采样（ratio=0，非 always-sample），子 span 继承丢弃。"""
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=0.0)
    try:
        with start_span("parent", operation="cognition"):
            with start_span("child", operation="execution"):
                pass  # 父未采样 → 子也 DROP（继承决定优先于 always-sample）
        provider.force_flush()
        assert _finished(exporter) == []
    finally:
        shutdown_tracing()


def test_current_trace_ids_inside_span():
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=1.0)
    try:
        assert current_trace_ids() == (None, None)  # 无 span
        with start_span("root", operation="cognition"):
            tid, sid = current_trace_ids()
            assert tid and len(tid) == 32 and tid.islower()
            assert sid and len(sid) == 16 and sid.islower()
            assert tid != "0" * 32 and sid != "0" * 16
    finally:
        shutdown_tracing()


def test_w3c_inject_extract_roundtrip():
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=1.0)
    try:
        carrier = {}
        with start_span("root", operation="execution"):
            inject_trace_context(carrier)
            assert "traceparent" in carrier
            # tracestate 仅在上下文有 tracestate 时才写入（W3C 可选）；无时只写 traceparent
            ctx = extract_trace_context(carrier)
            # 提取出的父 context 与当前 span 相同 trace id
            parent_span = otel_trace.get_current_span(ctx)
            assert parent_span.get_span_context().is_valid
            assert format(parent_span.get_span_context().trace_id, "032x") == current_trace_ids()[0]
            # 不传播 baggage
            assert all(not k.startswith("baggage") for k in carrier)
    finally:
        shutdown_tracing()


def test_invalid_carrier_fails_closed():
    ctx = extract_trace_context({"traceparent": "00-garbage-not-hex-not-length"})
    span = otel_trace.get_current_span(ctx)
    assert not span.get_span_context().is_valid  # 无父，不抛 secret/整头
    ctx2 = extract_trace_context({})
    assert not otel_trace.get_current_span(ctx2).get_span_context().is_valid
    # 非法类型 carrier 也不抛
    ctx3 = extract_trace_context({"traceparent": "00-0" * 40})
    assert not otel_trace.get_current_span(ctx3).get_span_context().is_valid


def test_sensitive_span_attribute_rejected():
    exporter = InMemorySpanExporter()
    _provider(exporter, ratio=1.0)
    try:
        with pytest.raises(ValueError):
            start_span("x", operation="cognition", attributes={"password": "hunter2"})
        with pytest.raises(ValueError):
            start_span("x", operation="cognition", attributes={"prompt": "secret"})
        with pytest.raises(ValueError):
            start_span("x", operation="cognition", attributes={"Authorization": "Bearer x"})
        with pytest.raises(ValueError):
            start_span("x", operation="cognition", attributes={"request_body": "..."})
    finally:
        shutdown_tracing()


def test_allowed_attributes_passed_through():
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=1.0)
    try:
        with start_span("x", operation="cognition",
                        attributes={"chain_id": "c1", "model": "grok", "stage": "s"}):
            pass
        provider.force_flush()
        attrs = _finished(exporter)[0].attributes
        assert attrs["chain_id"] == "c1"
        assert attrs["model"] == "grok"
        assert attrs["stage"] == "s"
        assert attrs["operation"] == "cognition"
    finally:
        shutdown_tracing()


def test_sensitive_value_in_allowed_key_redacted():
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=1.0)
    try:
        with start_span("x", operation="cognition", attributes={"model": "Bearer abc123"}):
            pass
        provider.force_flush()
        attrs = _finished(exporter)[0].attributes
        # 复用日志同一敏感文本边界：Bearer 形态保留前后文，敏感值部分清洗
        assert attrs["model"] == "Bearer [REDACTED]"
    finally:
        shutdown_tracing()


def test_invalid_span_name_and_operation_rejected():
    _provider(InMemorySpanExporter(), ratio=1.0)
    try:
        with pytest.raises(ValueError):
            start_span("Bad Name", operation="cognition")
        with pytest.raises(ValueError):
            start_span("root", operation="has space")
        with pytest.raises(ValueError):
            start_span("x" * 97, operation="cognition")
        with pytest.raises(ValueError):
            start_span("root", operation="cognition_id")  # 业务 ID 不是低基数操作
    finally:
        shutdown_tracing()


def test_reconfigure_and_shutdown_idempotent():
    exporter1 = InMemorySpanExporter()
    p1 = _provider(exporter1, ratio=1.0)
    shutdown_tracing()
    shutdown_tracing()  # 幂等
    exporter2 = InMemorySpanExporter()
    p2 = _provider(exporter2, ratio=1.0)
    try:
        with start_span("again", operation="cognition"):
            pass
        p2.force_flush()
        assert len(_finished(exporter2)) == 1
    finally:
        shutdown_tracing()


# ---------------- R1：span value 复用完整敏感边界 ----------------

@pytest.mark.parametrize("value,forbidden", [
    ("https://alice:hunter2@host/path", ["hunter2", "alice:"]),
    ("Basic dXNlcjpwYXNz", ["dXNlcjpwYXNz"]),
    ("Bearer abc123def", ["abc123def"]),
    ("Cookie: sid=abc123", ["abc123"]),
    ("password=hunter2", ["hunter2"]),
    ("api_key=AKIAXYZ", ["AKIAXYZ"]),
    ("signature=abcd1234", ["abcd1234"]),
    ("access_token=zzz", ["zzz"]),
    ("credential=pk_live_abc", ["pk_live_abc"]),
    ("passphrase=swordfish", ["swordfish"]),
])
def test_span_value_sensitive_forms_redacted(value, forbidden):
    """Span 值复用日志同一敏感边界：URL userinfo / Basic / Cookie / token / signature 等
    全部清洗，导出的 attribute 不含注入 secret。"""
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=1.0)
    try:
        with start_span("x", operation="cognition", attributes={"model": value}):
            pass
        provider.force_flush()
        attr = _finished(exporter)[0].attributes["model"]
        for f in forbidden:
            assert f not in attr
        assert "[REDACTED]" in attr or attr == "[REDACTED]"
    finally:
        shutdown_tracing()


def test_span_value_scalar_sequence_sanitized():
    """scalar sequence 每个字符串元素同样脱敏；原输入对象不修改。"""
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=1.0)
    orig = ["alice", "https://u:p@host", "Bearer abc"]
    try:
        with start_span("x", operation="cognition", attributes={"model": tuple(orig)}):
            pass
        provider.force_flush()
        attr = _finished(exporter)[0].attributes["model"]
        assert attr == ("alice", "https://[REDACTED]@host", "Bearer [REDACTED]")
        assert orig == ["alice", "https://u:p@host", "Bearer abc"]  # 原输入不变
    finally:
        shutdown_tracing()


@pytest.mark.parametrize("bad", [
    b"bytesdata",               # bytes 不是 OTel scalar
    object(),                   # arbitrary object 不交 SDK 隐式 str/repr
    (1, "mixed"),               # 混合类型序列
    (),                         # 空序列
    [object()],                 # 非 scalar 元素
    None,
])
def test_span_attribute_invalid_value_rejected(bad):
    _provider(InMemorySpanExporter(), ratio=1.0)
    try:
        with pytest.raises(ValueError):
            start_span("x", operation="cognition", attributes={"model": bad})
    finally:
        shutdown_tracing()


# ---------------- R1：Trace provider 生命周期 ----------------

def _counting_tracing_module(monkeypatch):
    from app.observability import tracing as T
    state = {"created": [], "shuts": []}

    class Counting(TracerProvider):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            state["created"].append(self)

        def shutdown(self):
            state["shuts"].append(self)
            super().shutdown()

    monkeypatch.setattr(T, "TracerProvider", Counting)
    return T, state


def _cfg_args(exporter):
    return dict(enabled=True, endpoint="https://x:4318", allow_insecure_http=False,
                ratio=1.0, timeout_s=5, service="s", version="v", exporter=exporter)


def test_reconfigure_shuts_down_old_exactly_once(monkeypatch):
    """A→B：A shutdown 恰一次，B 可导出；shutdown 后重复 no-op。"""
    T, state = _counting_tracing_module(monkeypatch)
    ex_a, ex_b = InMemorySpanExporter(), InMemorySpanExporter()
    A = T.configure_tracing(**_cfg_args(ex_a))
    B = T.configure_tracing(**_cfg_args(ex_b))
    assert state["created"] == [A, B]
    assert state["shuts"] == [A]                  # A 恰 shutdown 一次
    with T.start_span("b", operation="cognition"):
        pass
    B.force_flush()
    assert len(ex_b.get_finished_spans()) == 1    # B 可导出
    T.shutdown_tracing()
    assert state["shuts"] == [A, B]               # B shutdown 一次
    T.shutdown_tracing()
    T.shutdown_tracing()
    assert state["shuts"] == [A, B]               # 重复 shutdown no-op


def test_configure_disabled_shuts_down_once(monkeypatch):
    T, state = _counting_tracing_module(monkeypatch)
    A = T.configure_tracing(**_cfg_args(InMemorySpanExporter()))
    assert T._provider is A
    assert T.configure_tracing(enabled=False, endpoint="", allow_insecure_http=False,
                               ratio=1.0, timeout_s=5, service="s", version="v") is None
    assert state["shuts"] == [A] and T._provider is None
    T.configure_tracing(enabled=False, endpoint="", allow_insecure_http=False,
                        ratio=1.0, timeout_s=5, service="s", version="v")
    assert state["shuts"] == [A]                  # 重复 disabled no-op


def test_configure_failure_keeps_old_provider(monkeypatch):
    """新 provider 构造失败 → 保留旧 provider 可用、不误 shutdown。"""
    from app.observability import tracing as T
    calls = {"n": 0}
    shuts = []

    class Flaky(TracerProvider):
        def __init__(self, *a, **k):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("provider construct failed")
            super().__init__(*a, **k)

        def shutdown(self):
            shuts.append(self)
            super().shutdown()

    monkeypatch.setattr(T, "TracerProvider", Flaky)
    ex_a = InMemorySpanExporter()
    A = T.configure_tracing(**_cfg_args(ex_a))
    with pytest.raises(RuntimeError):
        T.configure_tracing(**_cfg_args(InMemorySpanExporter()))
    assert T._provider is A
    assert shuts == []                            # 构造失败不 shutdown 旧 provider
    with T.start_span("a", operation="cognition"):
        pass
    A.force_flush()
    assert len(ex_a.get_finished_spans()) == 1    # 旧 provider 仍导出
    T.shutdown_tracing()


def test_bad_ratio_keeps_old_provider():
    """坏 ratio 在改变任何旧状态前验证 → 旧 provider 保持可用、不 shutdown。"""
    exporter = InMemorySpanExporter()
    p = _provider(exporter, ratio=1.0)
    try:
        with pytest.raises(ValueError):
            configure_tracing(enabled=True, endpoint="https://x:4318",
                              allow_insecure_http=False, ratio=2.0, timeout_s=5,
                              service="s", version="v", exporter=InMemorySpanExporter())
        with start_span("a", operation="cognition"):
            pass
        p.force_flush()
        assert len(_finished(exporter)) == 1      # 旧 provider 仍导出
    finally:
        shutdown_tracing()


def test_shutdown_then_start_span_no_op():
    """关闭后 start_span 使用 no-op tracer：无有效 span、不向已关闭 exporter 写入、不抛错。"""
    exporter = InMemorySpanExporter()
    p = _provider(exporter, ratio=1.0)
    shutdown_tracing()
    with start_span("after", operation="execution"):
        assert current_trace_ids() == (None, None)  # no-op tracer，无有效 span
    p.force_flush()                               # 已关闭 provider 不导出，无异常


# ---------------- R2：span 值复用完整矩阵 + 序列有界 + disabled 校验 ----------------

@pytest.mark.parametrize("text,forbidden", SENSITIVE_MATRIX,
                         ids=[t[0][:28] for t in SENSITIVE_MATRIX])
def test_span_value_full_sensitive_matrix_redacted(text, forbidden):
    """同一敏感矩阵经 span 导出：所有 attribute 字符串均不含注入 secret/userinfo/token。"""
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=1.0)
    try:
        with start_span("x", operation="cognition", attributes={"model": text}):
            pass
        provider.force_flush()
        attr = _finished(exporter)[0].attributes["model"]
        for f in forbidden:
            assert f not in attr, f"span exported secret {f!r} from {text!r}"
        assert "[REDACTED]" in attr
    finally:
        shutdown_tracing()


@pytest.mark.parametrize("text", NON_SENSITIVE_EXAMPLES)
def test_span_value_non_sensitive_not_overredacted(text):
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=1.0)
    try:
        with start_span("x", operation="cognition", attributes={"model": text}):
            pass
        provider.force_flush()
        assert _finished(exporter)[0].attributes["model"] == text
    finally:
        shutdown_tracing()


def test_span_sequence_64_items_ok_65_rejected():
    exporter = InMemorySpanExporter()
    _provider(exporter, ratio=1.0)
    try:
        with start_span("x", operation="cognition", attributes={"model": tuple(str(i) for i in range(64))}):
            pass  # 64 项合法
        with pytest.raises(ValueError):
            start_span("y", operation="cognition",
                       attributes={"model": tuple(str(i) for i in range(65))})
    finally:
        shutdown_tracing()


def test_span_sequence_bool_int_mix_rejected():
    """[1, True] 必须拒绝：bool 是 int 子类，但 `type(v) is int` 判断为 False。"""
    _provider(InMemorySpanExporter(), ratio=1.0)
    try:
        with pytest.raises(ValueError):
            start_span("x", operation="cognition", attributes={"model": [1, True]})
        with pytest.raises(ValueError):
            start_span("y", operation="cognition", attributes={"model": (True, 1)})
    finally:
        shutdown_tracing()


def test_span_long_string_truncated():
    """超长 span 值先有界截断再清洗，不复制无限字符串进 exporter。"""
    exporter = InMemorySpanExporter()
    provider = _provider(exporter, ratio=1.0)
    long_val = "secret_value=" + "B" * 10000
    try:
        with start_span("x", operation="cognition", attributes={"model": long_val}):
            pass
        provider.force_flush()
        attr = _finished(exporter)[0].attributes["model"]
        assert "B" * 10000 not in attr
        assert len(attr) < 4200
    finally:
        shutdown_tracing()


def test_configure_disabled_bad_ratio_keeps_old_provider():
    """active provider 下 disabled + 坏 ratio → ValueError，旧 provider 保留且继续导出。"""
    from app.observability import tracing as T

    exporter = InMemorySpanExporter()
    p = _provider(exporter, ratio=1.0)
    try:
        with pytest.raises(ValueError):
            configure_tracing(enabled=False, endpoint="", allow_insecure_http=False,
                              ratio=-1, timeout_s=5, service="s", version="v")
        assert p is T._provider  # 旧 provider 保留（模块全局引用未变）
        with start_span("a", operation="cognition"):
            pass
        p.force_flush()
        assert len(_finished(exporter)) == 1
    finally:
        shutdown_tracing()


def test_configure_disabled_bad_timeout_keeps_old_provider():
    from app.observability import tracing as T

    exporter = InMemorySpanExporter()
    p = _provider(exporter, ratio=1.0)
    try:
        with pytest.raises(ValueError):
            configure_tracing(enabled=False, endpoint="", allow_insecure_http=False,
                              ratio=1.0, timeout_s=0, service="s", version="v")
        assert p is T._provider
        with start_span("a", operation="cognition"):
            pass
        p.force_flush()
        assert len(_finished(exporter)) == 1
    finally:
        shutdown_tracing()
