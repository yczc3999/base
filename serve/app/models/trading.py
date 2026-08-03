"""交易 bot 管理模块（rb_ 前缀，桥接 postgrad-signal-lab 账本）"""
from datetime import datetime
from sqlalchemy import (
    Integer, BigInteger, String, Text, DateTime, Numeric, JSON, func,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class RbTrade(Base):
    """平仓 round-trip（桥接自 trades.jsonl）"""

    __tablename__ = "rb_trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    signal_id: Mapped[str | None] = mapped_column(String(128))
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    pool_address: Mapped[str | None] = mapped_column(String(64))
    token_address: Mapped[str | None] = mapped_column(String(64))
    token_symbol: Mapped[str | None] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(8), default="long", nullable=False)
    entry_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    exit_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    amount_usd: Mapped[float | None] = mapped_column(Numeric(20, 8))
    pnl_usd: Mapped[float | None] = mapped_column(Numeric(20, 8))
    pnl_pct: Mapped[float | None] = mapped_column(Numeric(12, 6))
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(8), default="PAPER", nullable=False)
    raw: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class RbPosition(Base):
    """持仓（桥接自 signals.jsonl，trades.jsonl 平仓联动）"""

    __tablename__ = "rb_positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    signal_id: Mapped[str | None] = mapped_column(String(128))
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    pool_address: Mapped[str | None] = mapped_column(String(64))
    token_address: Mapped[str | None] = mapped_column(String(64))
    token_symbol: Mapped[str | None] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(8), default="long", nullable=False)
    entry_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float | None] = mapped_column(Numeric(38, 18))
    amount_usd: Mapped[float | None] = mapped_column(Numeric(20, 8))
    status: Mapped[str] = mapped_column(String(8), default="open", nullable=False)
    pnl_usd: Mapped[float | None] = mapped_column(Numeric(20, 8))
    raw: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RbStrategy(Base):
    """策略配置（保存即下发 control.json）"""

    __tablename__ = "rb_strategies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), default="PAPER", nullable=False)
    params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RbHeartbeat(Base):
    """bot 心跳（桥接自 run.log HEARTBEAT 行）"""

    __tablename__ = "rb_heartbeats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    block: Mapped[int | None] = mapped_column(BigInteger)
    pools: Mapped[int | None] = mapped_column(Integer)
    open_count: Mapped[int | None] = mapped_column(Integer)
    signals_total: Mapped[int | None] = mapped_column(Integer)
    trades_total: Mapped[int | None] = mapped_column(Integer)
    cum_pnl_usd: Mapped[float | None] = mapped_column(Numeric(20, 8))
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class RbExecution(Base):
    """执行记录原文（桥接自 executor/ledger/executions.jsonl）"""

    __tablename__ = "rb_executions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str | None] = mapped_column(String(32))
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
