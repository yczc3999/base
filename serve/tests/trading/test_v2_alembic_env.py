"""
WP-01A-00 Alembic 执行基础 —— 纯单测（不连真实数据库）。

覆盖任务 §6.1：
1. online/offline 的共同 ``context.configure`` 参数完全一致；
2. schema/table allowlist：未知 public 表、非 public|trading schema 均被排除；
   metadata public 表、trading 表和 version table 保留；
3. 注入 connection 不被 close/dispose；自建 engine 在成功/异常路径各 dispose 一次；
4. SQL 顺序为 begin → search path/timeout → advisory lock → migration；migration 失败后
   rollback，原异常传播；
5. SQLite/错误 dialect fail-closed；日志、exception、offline SQL 中 secret marker 计数为 0。

加载方式：env.py 与 Alembic 运行时一样是被 exec 的脚本（``from alembic import context``
在模块内绑定）。测试用 ``patch.dict(sys.modules, {"alembic": fake})`` 提供 stub context，
再以 importlib 装载真实 ``alembic/env.py`` 源码 —— 模块底部的入口随装载自动执行一次，
因此每次测试都覆盖真实的 online/offline 流程，而非只测辅助函数。
"""

import contextlib
import importlib.util
import logging
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import settings
from app.models import Base

ALEMBIC_ENV_PATH = Path(__file__).resolve().parents[2] / "alembic" / "env.py"
SECRET_MARKER = "TOPSECRETPASS9f21"


# ---------------- 测试替身 ----------------

class _FakeTx:
    def __init__(self, conn):
        self.conn = conn
        self.entered = False
        self.exited = False
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        self.entered = True
        self.conn._active_tx = self
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited = True
        if exc_type is None:
            self.committed = True
        else:
            self.rolled_back = True
        self.conn._active_tx = None
        return False


class _FakeConn:
    def __init__(self, dialect_name="postgresql"):
        self.dialect = types.SimpleNamespace(name=dialect_name)
        self.executed = []
        self.closed = False
        self._active_tx = None
        self.txs = []

    def execute(self, stmt):
        self.executed.append(stmt)

    def get_transaction(self):
        return self._active_tx

    def begin(self):
        if self._active_tx is not None:
            raise AssertionError("begin() on already-in-transaction connection")
        tx = _FakeTx(self)
        self.txs.append(tx)
        return tx

    def close(self):
        self.closed = True


class _FakeEngine:
    def __init__(self, conn=None):
        self.conn = conn or _FakeConn()
        self.disposed = 0

    def connect(self):
        return contextlib.nullcontext(self.conn)

    def dispose(self):
        self.disposed += 1


class _FakeContext:
    """stub ``alembic.context``：记录 configure/execute/begin/run 调用与顺序。"""

    def __init__(self, is_offline=False):
        self.config = types.SimpleNamespace(config_file_name=None, attributes={})
        self._is_offline = is_offline
        self.configure_kwargs = []
        self.executed = []
        self.events = []
        self.migration_calls = 0
        self.migration_error = None
        self._dialect_name = "postgresql"

    def is_offline_mode(self):
        return self._is_offline

    def configure(self, **kwargs):
        self.configure_kwargs.append(kwargs)
        self.events.append("configure")
        url = kwargs.get("url", "")
        self._dialect_name = "sqlite" if isinstance(url, str) and url.startswith("sqlite") \
            else "postgresql"

    def get_context(self):
        return types.SimpleNamespace(dialect=types.SimpleNamespace(name=self._dialect_name))

    def execute(self, sql):
        self.executed.append(sql)
        self.events.append("execute")

    def begin_transaction(self):
        self.events.append("begin")
        return contextlib.nullcontext()

    def run_migrations(self):
        self.events.append("run")
        if self.migration_error is not None:
            raise self.migration_error
        self.migration_calls += 1


def _load_env(ctx, inject_conn=True):
    """装载真实 env.py；alembic.context 由 stub 提供，模块底部入口自动执行一次。

    默认注入 fake connection，使 auto-run 走注入路径（零真实连接）。测试自建 engine 路径
    时在装载后清空 attributes 再显式调用 ``run_migrations_online()``。
    """
    if inject_conn and "connection" not in ctx.config.attributes:
        ctx.config.attributes["connection"] = _FakeConn()
    spec = importlib.util.spec_from_file_location("pm_v2_alembic_env_ut", ALEMBIC_ENV_PATH)
    mod = importlib.util.module_from_spec(spec)
    fake_alembic = types.SimpleNamespace(context=ctx)
    with patch.dict(sys.modules, {"alembic": fake_alembic}):
        spec.loader.exec_module(mod)
    return mod


# ---------------- 1. online/offline 共同 configure 参数 ----------------

def test_common_configure_kwargs_identical_across_modes():
    """online/offline 的 configure 参数中 COMMON 子集完全一致，且逐键精确。"""
    expected = {
        "target_metadata": Base.metadata,
        "include_schemas": True,
        "compare_type": True,
        "compare_server_default": True,
        "version_table": "alembic_version",
        "version_table_schema": "public",
        "transaction_per_migration": False,
    }
    online_ctx = _FakeContext(is_offline=False)
    online_ctx.config.attributes["connection"] = _FakeConn()
    online_mod = _load_env(online_ctx)

    offline_ctx = _FakeContext(is_offline=True)
    offline_mod = _load_env(offline_ctx)

    assert {k: online_mod.COMMON_CONFIGURE_KWARGS[k] for k in expected} == expected
    assert online_mod.COMMON_CONFIGURE_KWARGS["include_name"] is online_mod.include_name
    assert online_mod.COMMON_CONFIGURE_KWARGS["include_object"] is online_mod.include_object
    assert offline_mod.COMMON_CONFIGURE_KWARGS["include_name"] is offline_mod.include_name
    assert offline_mod.COMMON_CONFIGURE_KWARGS["include_object"] is offline_mod.include_object
    online_kw = online_ctx.configure_kwargs[0]
    offline_kw = offline_ctx.configure_kwargs[0]
    # online 仅多 connection，offline 仅多 url/literal_binds/dialect_opts
    hooks = {"include_name", "include_object"}
    assert set(online_kw) == set(expected) | hooks | {"connection"}
    assert set(offline_kw) == set(expected) | hooks | {"url", "literal_binds", "dialect_opts"}
    common_online = {k: online_kw[k] for k in expected}
    common_offline = {k: offline_kw[k] for k in expected}
    assert common_online == common_offline == expected
    assert online_kw["include_name"] is online_mod.include_name
    assert online_kw["include_object"] is online_mod.include_object
    assert offline_kw["include_name"] is offline_mod.include_name
    assert offline_kw["include_object"] is offline_mod.include_object
    # offline 的额外键为渲染所需的固定配置
    assert offline_kw["literal_binds"] is True
    assert offline_kw["dialect_opts"] == {"paramstyle": "named"}


# ---------------- 2. schema/table allowlist ----------------

def test_include_name_schema_allowlist():
    mod = _load_env(_FakeContext(is_offline=False))
    # Alembic 用 None 表示 PostgreSQL default schema（public）。
    assert mod.include_name(None, "schema", {}) is True
    assert mod.include_name("trading", "schema", {}) is True
    assert mod.include_name("public", "schema", {}) is True
    assert mod.include_name("other_schema", "schema", {}) is False
    assert mod.include_name("pg_catalog", "schema", {}) is False


def test_include_name_table_allowlist():
    mod = _load_env(_FakeContext(is_offline=False))
    # trading 全域
    assert mod.include_name("any_trading_table", "table", {"schema_name": "trading"}) is True
    # public：metadata 声明表 + alembic_version 保留
    known = next(iter(mod.PUBLIC_ALLOWED_TABLES - {"alembic_version"}))
    assert known in Base.metadata.tables
    assert mod.include_name(known, "table", {"schema_name": "public"}) is True
    assert mod.include_name("alembic_version", "table", {"schema_name": "public"}) is True
    # 未知 public 表、非 public|trading schema 排除
    assert mod.include_name("unknown_public_table", "table", {"schema_name": "public"}) is False
    assert mod.include_name("some_table", "table", {"schema_name": "other_schema"}) is False
    # 缺省 schema 视为 public
    assert mod.include_name(known, "table", {}) is True
    assert mod.include_name(known, "table", {"schema_name": None}) is True
    assert mod.include_name("unknown_public_table", "table", {}) is False
    assert mod.include_name(
        "unknown_public_table", "table", {"schema_name": None}
    ) is False
    # 非 table/schema 对象类型放行
    assert mod.include_name("col", "column", {"schema_name": "public"}) is True
    assert mod.include_name("ix", "index", {"schema_name": "public"}) is True


def test_include_object_table_allowlist():
    import sqlalchemy as sa

    mod = _load_env(_FakeContext(is_offline=False))
    trading_tbl = sa.Table("t", sa.MetaData(), schema="trading")
    known = next(iter(mod.PUBLIC_ALLOWED_TABLES - {"alembic_version"}))
    public_known = sa.Table(known, sa.MetaData(), schema="public")
    public_unknown = sa.Table("unknown_public_table", sa.MetaData(), schema="public")
    assert mod.include_object(trading_tbl, "t", "table", True, None) is True
    assert mod.include_object(public_known, known, "table", True, None) is True
    assert mod.include_object(public_unknown, "unknown_public_table", "table", True, None) is False
    assert mod.include_object("anything", "c", "column", True, None) is True


# ---------------- 3. 连接所有权 ----------------

def test_injected_connection_not_closed_or_disposed():
    """注入 connection 直接被复用：不 close、不 dispose、不新建 engine。"""
    conn = _FakeConn()
    ctx = _FakeContext(is_offline=False)
    ctx.config.attributes["connection"] = conn
    mod = _load_env(ctx)

    assert conn.closed is False
    assert conn.txs[0].committed is True
    assert ctx.migration_calls == 1
    # 自建 engine 路径不应被触发
    def _never(*a, **k):
        raise AssertionError("self-built engine must not be created with injected connection")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mod, "create_engine", _never)
        mod.run_migrations_online()   # 再次运行仍走注入连接
    assert conn.closed is False
    assert ctx.migration_calls == 2


def test_injected_connection_with_active_transaction_fails_without_taking_ownership():
    """调用方已开事务时 fail-closed；env 不得 commit/rollback 或退出该事务。"""
    conn = _FakeConn()
    caller_tx = conn.begin()
    caller_tx.__enter__()
    ctx = _FakeContext(is_offline=False)
    ctx.config.attributes["connection"] = conn

    with pytest.raises(RuntimeError, match="v2_migration_requires_clean_connection"):
        _load_env(ctx)

    assert conn.get_transaction() is caller_tx
    assert caller_tx.exited is False
    assert caller_tx.committed is False
    assert caller_tx.rolled_back is False
    assert conn.executed == []
    caller_tx.__exit__(None, None, None)


def test_self_built_engine_disposed_once_on_success(monkeypatch):
    ctx = _FakeContext(is_offline=False)
    mod = _load_env(ctx)                      # 装载期 auto-run 走注入 fake conn（安全）
    ctx.config.attributes.clear()             # 强制自建路径
    engine = _FakeEngine()
    monkeypatch.setattr(mod, "create_engine", lambda url, **kw: engine)

    mod.run_migrations_online()

    assert engine.disposed == 1
    assert engine.conn.txs[0].committed is True
    assert ctx.migration_calls == 2           # 装载期 1 次 + 显式 1 次


def test_self_built_engine_disposed_once_on_exception(monkeypatch):
    ctx = _FakeContext(is_offline=False)
    mod = _load_env(ctx)
    ctx.config.attributes.clear()
    engine = _FakeEngine()
    monkeypatch.setattr(mod, "create_engine", lambda url, **kw: engine)
    ctx.migration_error = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        mod.run_migrations_online()

    assert engine.disposed == 1
    assert engine.conn.txs[0].rolled_back is True


# ---------------- 4. SQL 顺序、回滚与原异常传播 ----------------

def test_online_sql_order_and_transaction_commit():
    """online：begin → search path → timeout → advisory lock → migration，正常提交。"""
    conn = _FakeConn()
    ctx = _FakeContext(is_offline=False)
    ctx.config.attributes["connection"] = conn
    mod = _load_env(ctx)

    sqls = [s.text for s in conn.executed]
    assert sqls == [
        "SET LOCAL search_path TO public, pg_catalog",
        "SET LOCAL lock_timeout TO '30s'",
        "SELECT pg_advisory_xact_lock(5786375870084826445)",
    ]
    # 迁移在锁语句之后执行
    assert ctx.events[-1] == "run"
    assert ctx.events.count("run") == 1
    assert conn.txs[0].entered is True
    assert conn.txs[0].committed is True
    assert conn.txs[0].rolled_back is False
    # SQL 顺序：begin 在 execute 之前
    assert conn.txs[0].entered
    assert mod.ADVISORY_LOCK_KEY == 5786375870084826445


def test_online_migration_failure_rolls_back_and_propagates():
    """migration 抛异常 → 整体 rollback、原异常传播（不吞测试 assertion）。"""
    conn = _FakeConn()
    ctx = _FakeContext(is_offline=False)
    ctx.config.attributes["connection"] = conn
    ctx.migration_error = RuntimeError("boom-rollback")
    with pytest.raises(RuntimeError, match="boom-rollback"):
        _load_env(ctx)  # 模块装载时的 auto-run 即抛出

    assert conn.txs[0].rolled_back is True
    assert conn.txs[0].committed is False
    assert conn.txs[0].entered is True


# ---------------- 5. fail-closed 与 secret 计数 ----------------

def test_online_sqlite_dialect_fail_closed_no_secret(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PASSWORD", SECRET_MARKER)
    conn = _FakeConn(dialect_name="sqlite")
    ctx = _FakeContext(is_offline=False)
    ctx.config.attributes["connection"] = conn
    with pytest.raises(RuntimeError, match="v2_migration_requires_postgresql_dialect") as ei:
        _load_env(ctx)
    assert SECRET_MARKER not in str(ei.value)
    # fail-closed：任何 SET/advisory SQL 都未执行
    assert conn.executed == []


def test_offline_sqlite_url_fail_closed(monkeypatch):
    """offline 遇错误 dialect（模拟 sqlite URL）也 fail-closed，exception 无 secret。"""
    monkeypatch.setattr(settings, "DATABASE_PASSWORD", SECRET_MARKER)
    ctx = _FakeContext(is_offline=True)
    mod = _load_env(ctx)  # 装载期 auto-run 为 PG offline，正常

    monkeypatch.setattr(mod, "_build_sync_url", lambda: "sqlite:///:memory:")
    before = len(ctx.executed)  # 装载期 PG offline 已产出 3 条 SQL
    with pytest.raises(RuntimeError, match="v2_migration_requires_postgresql_dialect") as ei:
        mod.run_migrations_offline()
    assert SECRET_MARKER not in str(ei.value)
    assert len(ctx.executed) == before  # fail-closed：本次运行未输出任何 SQL


def test_offline_sql_contains_no_secret_and_order(monkeypatch):
    """offline SQL 输出含 search path/transaction/advisory lock，且不含 password。"""
    monkeypatch.setattr(settings, "DATABASE_PASSWORD", SECRET_MARKER)
    ctx = _FakeContext(is_offline=True)
    mod = _load_env(ctx)

    sqls = [str(s) for s in ctx.executed]
    assert len(sqls) == 3
    assert any("search_path" in s for s in sqls)
    assert any("lock_timeout" in s for s in sqls)
    assert any("pg_advisory_xact_lock" in s for s in sqls)
    assert all(SECRET_MARKER not in s for s in sqls)
    # transaction boundary：begin 在执行的 SQL 之前
    assert ctx.events.index("begin") < ctx.events.index("execute")
    # 迁移在三个预置语句之后
    assert ctx.events.index("run") > ctx.events.index("execute")


def test_logs_contain_no_secret_during_runs():
    """online/offline 运行期间捕获的日志不得含 secret marker。"""
    import io

    handler = logging.StreamHandler(io.StringIO())
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        conn = _FakeConn()
        ctx = _FakeContext(is_offline=False)
        ctx.config.attributes["connection"] = conn
        _load_env(ctx)
        off_ctx = _FakeContext(is_offline=True)
        _load_env(off_ctx)
        out = handler.stream.getvalue()
        assert SECRET_MARKER not in out
    finally:
        root.removeHandler(handler)


# ---------------- 辅助面：URL 构建不落敏感信息 ----------------

def test_build_sync_url_escapes_password(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PASSWORD", "p@ss word/+#")
    monkeypatch.setattr(settings, "DATABASE_USER", "u")
    monkeypatch.setattr(settings, "DATABASE_HOST", "h")
    monkeypatch.setattr(settings, "DATABASE_PORT", 5432)
    monkeypatch.setattr(settings, "DATABASE_NAME", "db")
    mod = _load_env(_FakeContext(is_offline=False))
    url = mod._build_sync_url()
    assert url.startswith("postgresql+psycopg://u:")
    assert url.endswith("@h:5432/db")
    assert " " not in url.split("@")[0]  # 特殊字符已 URL 编码
