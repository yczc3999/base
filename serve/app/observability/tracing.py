"""
V2 OpenTelemetry Trace primitives（WP-00d1）— 技术追踪，不接 lifespan。

- 只用 W3C `traceparent/tracestate`；不传播 baggage。非法 carrier fail-closed 为无父上下文。
- 确定性自定义 Sampler：有父 span 时继承父采样决定；无父且 operation ∈
  {execution, ledger, reconciliation} 100% 采样；其余按 TraceIdRatioBased 确定性 ratio。
- span name/operation 必须匹配 `[a-z0-9_.-]{1,96}`；operation 是低基数枚举语义，不含业务 ID。
- span attributes 仅允许固定技术键 + 业务链 ID；prompt/body/tool payload、headers、URL
  userinfo、Authorization/Cookie/secret/signature/token 一律拒绝或 [REDACTED]。
- `OTEL_ENABLED=false` 返回 None/no-op 且零网络；启用时使用 OTLP HTTP exporter，timeout 来自
  config。测试通过注入 in-memory/fake exporter，禁止访问真实 collector。
- 配置和 shutdown 幂等；不得在 import 时替换全局 provider。Trace 只做技术追踪，不落业务状态。
"""

from __future__ import annotations

import re
from typing import Mapping, MutableMapping

from opentelemetry import trace as _otel_trace
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import (
    Decision,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)

# 无父 span 时也 100% 采样的低基数 operation（performance 设计 §12：execution/ledger/
# reconciliation/错误和慢调用 100% 保留；本 primitive 只锁定三个操作语义）
ALWAYS_SAMPLE_OPERATIONS = frozenset({"execution", "ledger", "reconciliation"})

_NAME_RE = re.compile(r"^[a-z0-9_.-]{1,96}$")

# span attribute allowlist：固定技术键 + 业务链 ID（prompt/body/payload/凭据一律拒绝）
_ATTR_ALLOWLIST = frozenset({
    "operation",
    "dependency",
    "provider",
    "model",
    "role",
    "stage",
    "gate",
    "result",
    "status_class",
    "method",
    "mode",
    "version",
    # 业务链 ID（低基数，非敏感；仅用于技术关联，不落业务状态）
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

_propagator = TraceContextTextMapPropagator()
# 不变量：模块级 `_provider` 要么是 None，要么是**未 shutdown** 的活跃 provider。
# 所有关闭/替换路径在置 None 或替换前对旧 provider shutdown 恰一次，保证无后台资源泄漏。
_provider: TracerProvider | None = None


class _PolicySampler(Sampler):
    """确定性采样策略：父采样决定必须继承；always-sample operations 无父时 100%；
    其余按 TraceIdRatioBased 确定性 ratio（同一 trace_id 恒同决策）。"""

    def __init__(self, ratio: float) -> None:
        self._ratio_sampler = TraceIdRatioBased(ratio)

    def get_description(self) -> str:
        return f"PolicySampler(ratio={self._ratio_sampler._rate})"

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind=None,
        attributes: Mapping[str, object] | None = None,
        links=None,
        trace_state=None,
    ) -> SamplingResult:
        # 关键 1：SDK 用 sampling_result.attributes 作为 span attributes，必须把传入的
        # attributes 原样带回，否则导出的 span 属性会丢失。
        # 关键 2：SDK 把 start_span 的 context 参数传给 parent_context（默认 None），但
        # get_current_span(None) 会回退到运行时 context——嵌套时即父 span。必须始终调用，
        # 否则"已有父采样决定必须继承"在嵌套场景失效。
        parent_span = _otel_trace.get_current_span(parent_context)
        sc = parent_span.get_span_context()
        if sc.is_valid:
            # 已有父采样决定必须继承（无论本 ratio / operation）
            if sc.trace_flags.sampled:
                return SamplingResult(
                    Decision.RECORD_AND_SAMPLE,
                    attributes=attributes,
                    trace_state=trace_state,
                )
            return SamplingResult(Decision.DROP)
        attrs = dict(attributes or {})
        if attrs.get("operation") in ALWAYS_SAMPLE_OPERATIONS:
            return SamplingResult(
                Decision.RECORD_AND_SAMPLE,
                attributes=attributes,
                trace_state=trace_state,
            )
        # 无父且非 always-sample：确定性 ratio
        return self._ratio_sampler.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )


def _sanitize_attributes(attributes: Mapping[str, object]) -> dict[str, object]:
    """只允许 allowlist 键；string value 复用日志层的同一敏感文本边界（不维护第二套规则）。
    仅接受 OTel 合法 scalar（str/bool/int/float）与同类型 scalar sequence；bytes/arbitrary
    object/混合/超限序列 ValueError，绝不交给 SDK 隐式 str/repr。序列每个字符串元素同样脱敏。
    原输入对象不修改（新 dict / 新 tuple）。"""
    from app.observability.logging import _clean_string

    out: dict[str, object] = {}
    for key, value in attributes.items():
        if key not in _ATTR_ALLOWLIST:
            raise ValueError(f"span attribute key {key!r} not in allowlist")
        if isinstance(value, str):
            out[key] = _clean_string(value)
        elif isinstance(value, bool):
            out[key] = value
        elif isinstance(value, (int, float)):
            out[key] = value
        elif isinstance(value, (list, tuple)):
            if not value:
                raise ValueError(
                    f"span attribute {key!r} sequence must be non-empty "
                    f"(homogeneous scalar sequence)"
                )
            if len(value) > 64:
                raise ValueError(
                    f"span attribute {key!r} sequence exceeds 64 items"
                )
            first = type(value[0])
            # 同质必须 `type(v) is first`：拒绝 [1, True]（bool 是 int 子类，不得混入）
            if not all(type(v) is first for v in value):
                raise ValueError(
                    f"span attribute {key!r} sequence must be homogeneous, got mixed types"
                )
            if first is not str and first not in (bool, int, float):
                raise ValueError(
                    f"span attribute {key!r} sequence elements must be scalar, "
                    f"got {first.__name__}"
                )
            if first is bool:
                cleaned: tuple = tuple(value)
            elif first is str:
                cleaned = tuple(_clean_string(v) for v in value)
            else:
                cleaned = tuple(value)
            out[key] = cleaned
        else:
            raise ValueError(
                f"span attribute {key!r} value of type {type(value).__name__} is not "
                f"an OTel scalar or scalar sequence"
            )
    return out


def configure_tracing(
    *,
    enabled: bool,
    endpoint: str,
    allow_insecure_http: bool,
    ratio: float,
    timeout_s: float,
    service: str,
    version: str,
    exporter=None,
) -> TracerProvider | None:
    """配置全局 TracerProvider。`enabled=False` 返回 None 且零网络 exporter。
    exporter 缺省按 endpoint/timeout 建 OTLP HTTP exporter（真实 collector 仅在生产启用时创建）。
    生命周期合同（§4.3）：
    - 先验证 ratio/timeout 所需参数，再改变任何旧 provider 状态；坏配置不破坏当前链。
    - enabled=True 成功构造新 provider 后**原子替换**模块引用，并对旧 provider 恰 shutdown 一次；
      构造失败保留旧 provider 可用，且不误 shutdown。
    - enabled=False 对现存 provider shutdown 一次并清空引用；重复 disabled 为 no-op。"""
    global _provider
    # 先校验 ratio/timeout（两条路径），坏配置在改变任何旧 provider 状态前即拒绝
    if not (0.0 <= ratio <= 1.0):
        raise ValueError(f"ratio must be in [0.0, 1.0], got {ratio}")
    if timeout_s <= 0:
        raise ValueError(f"timeout_s must be > 0, got {timeout_s}")
    if not enabled:
        if _provider is not None:
            _provider.shutdown()
            _provider = None
        return None
    resource = Resource.create({"service.name": service, "service.version": version})
    provider = TracerProvider(resource=resource, sampler=_PolicySampler(ratio))
    if exporter is None:
        # 真实 collector 仅在此路径创建（config 已保证 endpoint 合法 + http 已 opt-in）
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=endpoint, timeout=timeout_s)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    # 原子替换：先赋值新引用，再 shutdown 旧 provider 恰一次。
    # 若上面构造抛错，_provider 保持旧值且旧 provider 未被 shutdown（不误关）。
    old = _provider
    _provider = provider
    if old is not None:
        old.shutdown()
    return provider


def shutdown_tracing() -> None:
    """关闭当前 provider 一次并清空引用；重复调用为 no-op。关闭后 `start_span` 用全局
    no-op tracer，不再向已关闭 exporter 写入。"""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None


def start_span(name: str, *, operation: str, attributes: Mapping[str, object] | None = None):
    """在当前 context 下开启子 span（context manager）。name/operation 必须匹配
    `[a-z0-9_.-]{1,96}`；operation 强制写入 span attribute 供采样策略判定。"""
    if not _NAME_RE.match(name):
        raise ValueError(f"span name must match [a-z0-9_.-]{{1,96}}, got {name!r}")
    if not _NAME_RE.match(operation) or operation.endswith("_id"):
        # operation 是低基数枚举语义，不含业务 ID（如 *_id）
        raise ValueError(
            f"operation must match [a-z0-9_.-]{{1,96}} without business-id suffix, "
            f"got {operation!r}"
        )
    attrs = dict(attributes or {})
    attrs["operation"] = operation
    attrs = _sanitize_attributes(attrs)
    # disabled 时 _provider 为 None → 全局 no-op provider 的 tracer（零记录零网络）
    tracer = (
        _provider.get_tracer(__name__)
        if _provider is not None
        else _otel_trace.get_tracer(__name__)
    )
    return tracer.start_as_current_span(name, attributes=attrs)


def current_trace_ids() -> tuple[str | None, str | None]:
    """当前 span 的 32 位小写 trace_id / 16 位小写 span_id；无有效 span 返回 (None, None)。"""
    span = _otel_trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None, None
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


def inject_trace_context(carrier: MutableMapping[str, str]) -> None:
    """向 carrier 注入 W3C traceparent/tracestate（不传播 baggage）。"""
    _propagator.inject(carrier)


def extract_trace_context(carrier: Mapping[str, str]) -> Context:
    """从 carrier 提取 W3C 父 context；非法 carrier fail-closed 为无父上下文。"""
    try:
        return _propagator.extract(carrier)
    except Exception:  # noqa: BLE001 - 非法/异常 carrier 一律无父，不抛 secret/整头
        return Context()
