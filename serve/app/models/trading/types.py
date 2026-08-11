"""Trading ORM 类型工厂（WP-01A-02）。

每个工厂函数返回**独立**的 type 实例：类型对象持有 DDL 元数据（collation 等），共享实例
会让多个表/列互相影响编译结果。所有金额/数量用 ``NUMERIC(38,0)``（base unit），所有业务
时间用 ``TIMESTAMPTZ``，hash 与外部 ID 用 ``C`` collation（字节序比较，与 UUID/分片一致）。
禁止 float 与 naive datetime（见任务 §2.7）。
"""

from sqlalchemy import DateTime, Numeric, String, Text


def utc_timestamp_type() -> DateTime:
    """独立 ``TIMESTAMPTZ``（PostgreSQL ``TIMESTAMP WITH TIME ZONE``）。"""
    return DateTime(timezone=True)


def base_unit_type() -> Numeric:
    """独立 ``NUMERIC(38,0)``（base unit 整数金额/数量）。"""
    return Numeric(38, 0)


def decimal_measure_type() -> Numeric:
    """Deterministic fractional measure (prices, PnL/EV and ratios)."""
    return Numeric(38, 12)


def probability_type() -> Numeric:
    """Probability/weight stored without the integer-base-unit truncation."""
    return Numeric(38, 12)


def sha256_type() -> String:
    """独立 ``VARCHAR(64) COLLATE "C"``（content/sha256 hash）。"""
    return String(64, collation="C")


def external_id_type() -> Text:
    """独立 ``TEXT COLLATE "C"``（外部/aggregate 标识，字节序比较）。"""
    return Text(collation="C")
