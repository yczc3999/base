"""
WP-00d1 低基数 Prometheus 指标验收测试。

覆盖：允许标签可采集；每个禁止 business-ID label 被拒绝；同名同 schema 幂等、同名不同 schema
ValueError；render 类型与内容；不同 registry 不互相污染；pm_ 前缀 + Prometheus 命名；bucket
单调约束。
"""

import pytest
from prometheus_client import Counter, Gauge, Histogram

from app.observability.metrics import (
    DEFAULT_BUCKETS,
    MetricCatalog,
    make_counter,
    make_gauge,
    make_histogram,
    new_registry,
    render_metrics,
)


def test_allowed_labels_collectable():
    c = make_counter("pm_jobs_total", "jobs", ("stage", "gate", "result"))
    c.labels(stage="s", gate="g", result="ok").inc()
    g = make_gauge("pm_queue_age_seconds", "age", ("mode", "operation"))
    g.labels(mode="consume", operation="x").set(3)
    h = make_histogram("pm_latency_seconds", "lat", ("provider", "model", "version"))
    h.labels(provider="p", model="m", version="v").observe(0.5)
    data, ctype = render_metrics()
    assert b"pm_jobs_total" in data
    assert b"pm_queue_age_seconds" in data
    assert b"pm_latency_seconds" in data
    assert ctype.startswith("text/plain")


@pytest.mark.parametrize("label", [
    "market_id", "token_id", "condition_id", "episode_id", "submission_id",
    "intent_id", "execution_id", "chain_id", "user_id", "address_id", "order_id",
    "trade_id", "market", "token", "order", "trade", "condition",
])
def test_business_id_labels_rejected(label):
    with pytest.raises(ValueError):
        make_counter("pm_x_total", "x", (label,))


@pytest.mark.parametrize("label", [
    "nonsense", "stage_id", "gate_x", "model_name", "provider_name",
])
def test_unknown_labels_rejected(label):
    with pytest.raises(ValueError):
        make_counter("pm_y_total", "y", (label,))


def test_name_must_be_pm_prefixed():
    with pytest.raises(ValueError):
        make_counter("jobs_total", "x")
    with pytest.raises(ValueError):
        make_counter("pm_bad name_total", "x")
    with pytest.raises(ValueError):
        make_counter("pm_", "x")
    make_counter("pm_ok_total", "x")  # 合法


def test_same_name_same_schema_idempotent():
    a = make_counter("pm_same_total", "desc", ("stage",))
    b = make_counter("pm_same_total", "other desc", ("stage",))
    assert a is b
    a.labels(stage="s").inc(2)
    assert b.labels(stage="s")._value.get() == 2.0


def test_same_name_different_kind_rejected():
    make_counter("pm_kind_total", "c", ())
    with pytest.raises(ValueError):
        make_gauge("pm_kind_total", "g", ())


def test_same_name_different_labels_rejected():
    make_counter("pm_labels_total", "c", ("stage",))
    with pytest.raises(ValueError):
        make_counter("pm_labels_total", "c", ("result",))


def test_same_name_different_buckets_rejected():
    make_histogram("pm_bucket_seconds", "h", (), (0.1, 0.2, 0.3))
    with pytest.raises(ValueError):
        make_histogram("pm_bucket_seconds", "h", (), (0.1, 0.2, 0.4))


def test_histogram_bucket_static_and_monotonic():
    make_histogram("pm_mono_seconds", "h", (), (0.1, 0.2, 0.3))
    with pytest.raises(ValueError):
        make_histogram("pm_badmono_seconds", "h", (), (0.3, 0.2, 0.1))
    with pytest.raises(ValueError):
        make_histogram("pm_badmono2_seconds", "h", (), (0.1, 0.1, 0.2))
    with pytest.raises(ValueError):
        make_histogram("pm_empty_seconds", "h", (), ())
    assert DEFAULT_BUCKETS == tuple(sorted(DEFAULT_BUCKETS))  # 静态单调


def test_render_is_bytes_and_ctype():
    make_counter("pm_render_total", "x", ())
    data, ctype = render_metrics()
    assert isinstance(data, bytes)
    assert isinstance(ctype, str)
    assert "text/plain" in ctype


def test_registries_isolated():
    cat1 = MetricCatalog()
    cat2 = MetricCatalog()
    c1 = cat1.counter("pm_iso_total", "x", ())
    c1.inc(7)                                   # 无标签 counter 直接 inc
    data1, _ = cat1.render()
    data2, _ = cat2.render()
    assert b"pm_iso_total" in data1
    assert b"pm_iso_total" not in data2  # 不互相污染
    # 默认 catalog 也不含 cat1 的指标
    default_data, _ = render_metrics()
    assert b"pm_iso_total" not in default_data


def test_new_registry_independent_of_default():
    reg = new_registry()
    make_counter("pm_iso2_total", "x", ())            # 默认 catalog
    c = Counter("pm_iso2_total", "x", registry=reg)    # 独立 registry 同名不冲突
    c.inc()
    data, _ = render_metrics(reg)
    assert b"pm_iso2_total" in data


def test_histogram_is_histogram_type():
    h = make_histogram("pm_type_seconds", "h", ())
    assert isinstance(h, Histogram)
    assert isinstance(make_counter("pm_type_c_total", "c", ()), Counter)
    assert isinstance(make_gauge("pm_type_g_total", "g", ()), Gauge)
