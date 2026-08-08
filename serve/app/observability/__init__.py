"""
V2 技术可观测性基础（WP-00d1）。

只提供可测试的 primitives 与 typed config，不接入 FastAPI lifespan、健康端点或业务链
（WP-00d2 才负责初始化、关闭、metrics endpoint 与 Artifact Store factory）。
"""

from app.observability.logging import (
    bind_log_context,
    configure_logging,
    get_log_context,
    redact,
)
from app.observability.metrics import (
    LABEL_ALLOWLIST,
    DEFAULT_BUCKETS,
    MetricCatalog,
    default_registry,
    make_counter,
    make_gauge,
    make_histogram,
    new_registry,
    render_metrics,
)
from app.observability.tracing import (
    ALWAYS_SAMPLE_OPERATIONS,
    configure_tracing,
    current_trace_ids,
    extract_trace_context,
    inject_trace_context,
    shutdown_tracing,
    start_span,
)

__all__ = [
    # logging
    "configure_logging",
    "bind_log_context",
    "get_log_context",
    "redact",
    # metrics
    "LABEL_ALLOWLIST",
    "DEFAULT_BUCKETS",
    "MetricCatalog",
    "default_registry",
    "new_registry",
    "make_counter",
    "make_gauge",
    "make_histogram",
    "render_metrics",
    # tracing
    "ALWAYS_SAMPLE_OPERATIONS",
    "configure_tracing",
    "shutdown_tracing",
    "start_span",
    "current_trace_ids",
    "inject_trace_context",
    "extract_trace_context",
]
