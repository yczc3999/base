"""
V2 低基数 Prometheus 指标 primitives（WP-00d1）。

- 显式 `CollectorRegistry`；import 时不污染默认全局 registry；提供 registry/catalog 注入以便测试。
- 同名同 schema（kind/labels/buckets）幂等返回既有；同名不同类型、label 或 bucket 必须 ValueError。
- metric 名必须以 `pm_` 开头且符合 Prometheus 命名。
- label key 唯一 allowlist；任何 `*_id`、market/token/condition/episode/submission/intent/
  execution/chain/user/address/order/trade 等业务 key 必须拒绝。
- histogram 默认 bucket 是有限、单调、静态 tuple；不从请求/配置动态造 label/bucket。
- `render_metrics(registry)` 输出不含日志 context、secret 或 trace/business ID。
- 模块只定义技术指标 primitive；不伪造尚未存在的 AI/交易业务指标。
"""

from __future__ import annotations

import re
import threading

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# 默认 histogram bucket：有限、单调、静态（performance 设计 §12 阶段延迟量级）
DEFAULT_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5,
    10.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0,
)

# label key 唯一 allowlist（platform 设计 §4.2：stage/gate/result/role/provider/version 等低基数）
LABEL_ALLOWLIST = frozenset({
    "stage",
    "gate",
    "result",
    "role",
    "provider",
    "model",
    "version",
    "dependency",
    "operation",
    "method",
    "status_class",
    "mode",
})

# 业务标签子串：任何标签含其一即拒绝
_BUSINESS_SUBSTR = (
    "market",
    "token",
    "condition",
    "episode",
    "submission",
    "intent",
    "execution",
    "chain",
    "user",
    "address",
    "order",
    "trade",
)

_METRIC_NAME_RE = re.compile(r"^pm_[a-zA-Z_:][a-zA-Z0-9_:]*$")

_KINDS = ("counter", "gauge", "histogram")


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not _METRIC_NAME_RE.match(name):
        raise ValueError(
            f"metric name must start with 'pm_' and be a valid Prometheus name, got {name!r}"
        )


def _validate_labels(labelnames: tuple[str, ...]) -> None:
    for label in labelnames:
        if not isinstance(label, str) or label not in LABEL_ALLOWLIST:
            raise ValueError(
                f"metric label {label!r} not in allowlist {sorted(LABEL_ALLOWLIST)}"
            )
        if label.endswith("_id"):
            raise ValueError(f"metric label {label!r} must not be a business id")
        for sub in _BUSINESS_SUBSTR:
            if sub in label:
                raise ValueError(
                    f"metric label {label!r} contains business term {sub!r}"
                )


def _validate_buckets(buckets: tuple[float, ...]) -> None:
    if not buckets:
        raise ValueError("histogram buckets must be non-empty")
    prev = buckets[0]
    for b in buckets[1:]:
        if b <= prev:
            raise ValueError("histogram buckets must be strictly increasing")
        prev = b


class MetricCatalog:
    """受控创建 + catalog 注入；import 时不污染默认全局 registry。"""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self._registry = registry if registry is not None else CollectorRegistry()
        # name -> (kind, metric, labelnames, buckets)
        self._catalog: dict[str, tuple[str, object, tuple[str, ...], tuple | None]] = {}
        self._lock = threading.Lock()

    @property
    def registry(self) -> CollectorRegistry:
        return self._registry

    def _get_or_create(
        self,
        name: str,
        kind: str,
        description: str,
        labelnames: tuple[str, ...],
        buckets: tuple | None,
    ) -> object:
        _validate_name(name)
        if kind not in _KINDS:
            raise ValueError(f"unknown metric kind {kind!r}")
        _validate_labels(labelnames)
        if buckets is not None:
            _validate_buckets(buckets)
        key = name
        with self._lock:
            existing = self._catalog.get(key)
            if existing is not None:
                ekind, emetric, elabels, ebuckets = existing
                if ekind != kind or elabels != labelnames or ebuckets != buckets:
                    raise ValueError(
                        f"metric {name!r} already registered with different "
                        f"schema (kind={ekind} labels={elabels} buckets={ebuckets})"
                    )
                return emetric
            if kind == "counter":
                metric = Counter(name, description, labelnames, registry=self._registry)
            elif kind == "gauge":
                metric = Gauge(name, description, labelnames, registry=self._registry)
            else:
                metric = Histogram(
                    name, description, labelnames, buckets=buckets, registry=self._registry
                )
            self._catalog[key] = (kind, metric, labelnames, buckets)
            return metric

    def counter(
        self, name: str, description: str, labelnames: tuple[str, ...] = ()
    ) -> Counter:
        return self._get_or_create(name, "counter", description, labelnames, None)  # type: ignore[return-value]

    def gauge(
        self, name: str, description: str, labelnames: tuple[str, ...] = ()
    ) -> Gauge:
        return self._get_or_create(name, "gauge", description, labelnames, None)  # type: ignore[return-value]

    def histogram(
        self,
        name: str,
        description: str,
        labelnames: tuple[str, ...] = (),
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> Histogram:
        return self._get_or_create(name, "histogram", description, labelnames, buckets)  # type: ignore[return-value]

    def render(self) -> tuple[bytes, str]:
        """渲染本 registry；输出不包含日志 context / secret / trace / business ID。"""
        return (
            generate_latest(self._registry),
            "text/plain; version=0.0.4; charset=utf-8",
        )


_default_catalog = MetricCatalog()


def make_counter(
    name: str, description: str, labelnames: tuple[str, ...] = (), catalog: MetricCatalog | None = None
) -> Counter:
    return (catalog or _default_catalog).counter(name, description, labelnames)


def make_gauge(
    name: str, description: str, labelnames: tuple[str, ...] = (), catalog: MetricCatalog | None = None
) -> Gauge:
    return (catalog or _default_catalog).gauge(name, description, labelnames)


def make_histogram(
    name: str,
    description: str,
    labelnames: tuple[str, ...] = (),
    buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    catalog: MetricCatalog | None = None,
) -> Histogram:
    return (catalog or _default_catalog).histogram(name, description, labelnames, buckets)


def render_metrics(registry: CollectorRegistry | None = None) -> tuple[bytes, str]:
    """渲染给定 registry（缺省为默认 catalog 的 registry）。"""
    if registry is None:
        return _default_catalog.render()
    return generate_latest(registry), "text/plain; version=0.0.4; charset=utf-8"


def new_registry() -> CollectorRegistry:
    """独立的空 registry（不污染默认；测试隔离）。"""
    return CollectorRegistry()


def default_registry() -> CollectorRegistry:
    return _default_catalog.registry


# ---- WP-05 Checkpoint C：execution metrics（低基数；label 全在 allowlist）----

def execution_event_counter(catalog: MetricCatalog | None = None) -> Counter:
    """execution 事件计数：label ``(operation, result)``，均为低基数枚举。"""
    return make_counter(
        "pm_execution_events_total",
        "execution order/reconcile events by operation and result",
        ("operation", "result"),
        catalog=catalog,
    )


def execution_unknown_counter(catalog: MetricCatalog | None = None) -> Counter:
    """UNKNOWN 提交计数（hard stop 次数），label ``status_class``。"""
    return make_counter(
        "pm_execution_unknown_total",
        "indeterminate private submit outcomes (UNKNOWN hard stop)",
        ("status_class",),
        catalog=catalog,
    )


def execution_heartbeat_drift_gauge(catalog: MetricCatalog | None = None) -> Gauge:
    """heartbeat 漂移毫秒（monotonic 调度漂移 ≤500ms），label ``mode``。"""
    return make_gauge(
        "pm_execution_heartbeat_drift_ms",
        "heartbeat schedule drift in milliseconds",
        ("mode",),
        catalog=catalog,
    )


def execution_latency_histogram(
    catalog: MetricCatalog | None = None,
    buckets: tuple[float, ...] = DEFAULT_BUCKETS,
) -> Histogram:
    """execution 步骤延迟（submit/fill/reconcile），label ``operation``。"""
    return make_histogram(
        "pm_execution_latency_seconds",
        "execution step latency in seconds",
        ("operation",),
        buckets,
        catalog=catalog,
    )
