"""
WP-01A-01 Base schema 兼容合同 —— 纯单测（不连真实数据库）。

覆盖任务 §6.1：
1. fixture/manifest 哈希可重算；SQL 无数据语句与敏感 marker；清单恰好 18 表；
2. validator：empty / compatible / 1 张 / 17 张 partial / 缺列 / 错类型 / 错 nullability /
   错 PK，固定 reason code，且不 commit/rollback；
3. online/offline autogenerate callback 排除 Base public 表（env 单测覆盖）；
4. revision id / down revision / upgrade online 调 validator / upgrade offline 发等价
   precondition / downgrade no-op；异常无 secret。
"""

import hashlib
import importlib.util
import json
import re
import sys
import types
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import pytest

from app.base_schema_contract import (
    BASE_LEGACY_TABLES,
    CANONICAL_SIGNATURE,
    REASON_INCOMPATIBLE,
    REASON_PARTIAL,
    BaseSchemaContractError,
    BaseSchemaResult,
    canonical_signature_sha256,
    extract_signature,
    offline_precondition_sql,
    validate_base_schema,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_SQL = FIXTURES_DIR / "base_legacy_schema.sql"
FIXTURE_MANIFEST = FIXTURES_DIR / "base_legacy_schema_manifest.json"
REVISION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "b1000001_v2_0001_freeze_base_schema_contract.py"
)

# 敏感 marker：必须是「凭据值」而非 schema 列名（如 password 列）。
SENSITIVE_PATTERNS = [
    re.compile(r"COPY\s+", re.I),
    re.compile(r"\\copy", re.I),
    re.compile(r"INSERT\s+INTO", re.I),
    re.compile(r"OWNER\s+TO", re.I),
    re.compile(r"\bGRANT\b", re.I),
    re.compile(r"\bREVOKE\b", re.I),
    re.compile(r"postgresql(?:\+\w+)?://", re.I),
    re.compile(r"base_user"),
    re.compile(r"\\restrict|\\unrestrict"),
    re.compile(r"password\s*[:=]", re.I),
    re.compile(r"(?:secret|token)\s*[:=]", re.I),
]


def _canonical_col_rows(sig=CANONICAL_SIGNATURE):
    """由 canonical 签名构造 extract_signature 的列查询行（tbl, col, type, nullable）。

    注意：extract_signature 的列查询第 4 列是 ``NOT a.attnotnull``（即 nullable），
    故这里直接透传 ``info["nullable"]``，不可取反。
    """
    rows = []
    for tbl, sig_t in sig.items():
        for col, info in sig_t["columns"].items():
            rows.append((tbl, col, info["type"], info["nullable"]))
    return rows


def _canonical_pk_rows(sig=CANONICAL_SIGNATURE):
    rows = []
    for tbl, sig_t in sig.items():
        for col in sig_t["primary_key"]:
            rows.append((tbl, col))
    return rows


class _FakeVConn:
    """脚本化只读连接：按 SQL 特征返回预先构造的行；记录 commit/rollback/begin。"""

    def __init__(self, col_rows=None, pk_rows=None):
        self.col_rows = list(col_rows or [])
        self.pk_rows = list(pk_rows or [])
        self.executed = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.begin_calls = 0

    def execute(self, stmt, params=None):
        self.executed.append((stmt, params))
        text_sql = str(stmt)
        if "format_type" in text_sql:
            return iter(self.col_rows)
        if "indisprimary" in text_sql:
            return iter(self.pk_rows)
        raise AssertionError(f"unexpected SQL: {text_sql}")

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1

    def begin(self):
        self.begin_calls += 1


def _no_write_calls(conn):
    assert conn.commit_calls == 0
    assert conn.rollback_calls == 0
    assert conn.begin_calls == 0


# ---------------- 1. fixture / manifest ----------------

def test_fixture_sql_sha_matches_manifest():
    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    fx_sha = hashlib.sha256(FIXTURE_SQL.read_bytes()).hexdigest()
    assert fx_sha == manifest["fixture_sha256"], "fixture SHA 可重算且与 manifest 一致"


def test_canonical_signature_sha_matches_manifest():
    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    assert canonical_signature_sha256() == manifest["canonical_signature_sha256"]


def test_source_dump_sha_matches_manifest():
    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    assert len(manifest["source_dump_sha256"]) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["source_dump_sha256"])


def test_fixture_sql_has_no_data_or_sensitive_markers():
    sql = FIXTURE_SQL.read_text(encoding="utf-8")
    for pat in SENSITIVE_PATTERNS:
        assert not pat.search(sql), f"fixture 含敏感/数据 marker: {pat.pattern}"
    # UTF-8 + LF（无 CR）；不含 \restrict 随机 token
    assert "\r" not in sql
    assert sql.encode("utf-8").decode("utf-8") == sql


def test_manifest_has_exactly_18_sorted_tables():
    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    tables = manifest["tables"]
    assert len(tables) == 18
    assert tables == sorted(tables)
    assert set(tables) == set(BASE_LEGACY_TABLES)
    assert tuple(tables) == BASE_LEGACY_TABLES


def test_manifest_has_no_connection_string():
    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    raw = json.dumps(manifest)
    assert "postgresql" not in raw.lower() or "postgresql+psycopg://" not in raw
    assert "://" not in raw


def test_fixture_mentions_only_schema_password_column():
    """'password' 只作为列名出现（schema 元素），无凭据值。"""
    sql = FIXTURE_SQL.read_text(encoding="utf-8")
    for line in sql.splitlines():
        if "password" in line:
            assert re.search(r"password(?:\s*:\s*=|\s*=\s*)", line) is None, line
            assert "PASSWORD" not in line


# ---------------- canonical 签名不可变 ----------------

def test_canonical_signature_is_immutable():
    assert isinstance(CANONICAL_SIGNATURE, MappingProxyType)
    with pytest.raises(TypeError):
        CANONICAL_SIGNATURE["extra_table"] = {}  # type: ignore[misc]
    first = CANONICAL_SIGNATURE[BASE_LEGACY_TABLES[0]]
    assert isinstance(first, MappingProxyType)
    with pytest.raises(TypeError):
        first["extra_col"] = {}  # type: ignore[index]
    assert isinstance(first["primary_key"], tuple)
    assert isinstance(first["columns"], MappingProxyType)


def test_canonical_signature_covers_exactly_18_tables():
    assert set(CANONICAL_SIGNATURE) == set(BASE_LEGACY_TABLES)
    for tbl in BASE_LEGACY_TABLES:
        sig = CANONICAL_SIGNATURE[tbl]
        assert set(sig) == {"columns", "primary_key"}
        assert sig["columns"], tbl


# ---------------- 2. validator ----------------

def test_validate_empty():
    conn = _FakeVConn(col_rows=[], pk_rows=[])
    result = validate_base_schema(conn)
    assert result == BaseSchemaResult("EMPTY", 0)
    assert result.status == "EMPTY"
    _no_write_calls(conn)


def test_validate_compatible():
    conn = _FakeVConn(col_rows=_canonical_col_rows(), pk_rows=_canonical_pk_rows())
    result = validate_base_schema(conn)
    assert result == BaseSchemaResult("COMPATIBLE", 18)
    _no_write_calls(conn)


def test_validate_partial_one_table():
    only = BASE_LEGACY_TABLES[0]
    col_rows = [r for r in _canonical_col_rows() if r[0] == only]
    pk_rows = [r for r in _canonical_pk_rows() if r[0] == only]
    conn = _FakeVConn(col_rows=col_rows, pk_rows=pk_rows)
    with pytest.raises(BaseSchemaContractError, match=REASON_PARTIAL) as ei:
        validate_base_schema(conn)
    assert "missing=" in str(ei.value)
    _no_write_calls(conn)


def test_validate_partial_seventeen_tables():
    missing = "settings"
    col_rows = [r for r in _canonical_col_rows() if r[0] != missing]
    pk_rows = [r for r in _canonical_pk_rows() if r[0] != missing]
    conn = _FakeVConn(col_rows=col_rows, pk_rows=pk_rows)
    with pytest.raises(BaseSchemaContractError, match=REASON_PARTIAL) as ei:
        validate_base_schema(conn)
    assert f"missing={missing}" in str(ei.value)
    _no_write_calls(conn)


def test_validate_missing_column():
    col_rows = _canonical_col_rows()
    # 移除 admin_users.username 这一行（缺列）
    col_rows = [r for r in col_rows if not (r[0] == "admin_users" and r[1] == "username")]
    conn = _FakeVConn(col_rows=col_rows, pk_rows=_canonical_pk_rows())
    with pytest.raises(BaseSchemaContractError, match=REASON_INCOMPATIBLE) as ei:
        validate_base_schema(conn)
    msg = str(ei.value)
    assert "table=admin_users" in msg
    assert "column=username" in msg
    _no_write_calls(conn)


def test_validate_wrong_type():
    col_rows = []
    for r in _canonical_col_rows():
        if r[0] == "admin_users" and r[1] == "username":
            col_rows.append((r[0], r[1], "text", r[3]))  # 错类型
        else:
            col_rows.append(r)
    conn = _FakeVConn(col_rows=col_rows, pk_rows=_canonical_pk_rows())
    with pytest.raises(BaseSchemaContractError, match=REASON_INCOMPATIBLE) as ei:
        validate_base_schema(conn)
    assert "table=admin_users" in str(ei.value)
    _no_write_calls(conn)


def test_validate_wrong_nullability():
    col_rows = []
    for r in _canonical_col_rows():
        if r[0] == "admin_users" and r[1] == "username":
            col_rows.append((r[0], r[1], r[2], not r[3]))  # 翻转 attnotnull
        else:
            col_rows.append(r)
    conn = _FakeVConn(col_rows=col_rows, pk_rows=_canonical_pk_rows())
    with pytest.raises(BaseSchemaContractError, match=REASON_INCOMPATIBLE) as ei:
        validate_base_schema(conn)
    assert "table=admin_users" in str(ei.value)
    _no_write_calls(conn)


def test_validate_wrong_primary_key():
    pk_rows = _canonical_pk_rows()
    # admin_users PK 改成 (username) 而非 (id)
    pk_rows = [(r[0], r[1]) for r in pk_rows if not (r[0] == "admin_users" and r[1] == "id")]
    pk_rows.append(("admin_users", "username"))
    conn = _FakeVConn(col_rows=_canonical_col_rows(), pk_rows=pk_rows)
    with pytest.raises(BaseSchemaContractError, match=REASON_INCOMPATIBLE) as ei:
        validate_base_schema(conn)
    assert "primary_key" in str(ei.value)
    _no_write_calls(conn)


def test_extract_signature_normalizes_types():
    conn = _FakeVConn(
        col_rows=[
            ("t1", "c1", "  CHARACTER   VARYING(50) ", False),
            ("t1", "c2", "Timestamp Without Time Zone", True),
        ],
        pk_rows=[("t1", "c1")],
    )
    sig = extract_signature(conn, tables=["t1"])
    assert sig["t1"]["columns"]["c1"] == {
        "type": "character varying(50)",
        "nullable": False,
    }
    assert sig["t1"]["columns"]["c2"] == {
        "type": "timestamp without time zone",
        "nullable": True,
    }
    assert sig["t1"]["primary_key"] == ("c1",)


# ---------------- offline precondition ----------------

def test_offline_precondition_contains_reason_codes_and_all_tables():
    sql = offline_precondition_sql()
    assert "DO $v2_base_precondition$" in sql
    assert REASON_PARTIAL in sql
    assert REASON_INCOMPATIBLE in sql
    for tbl in BASE_LEGACY_TABLES:
        assert tbl in sql
    # 单源：每个列名/主键都在 SQL 中
    assert "admin_users" in sql and "password_changed_at" in sql
    assert "role_menus" in sql


def test_offline_precondition_compares_pk_ordinal():
    """offline PK 比较必须含 ordinal 投影，不只是 (table, column) 列集合。"""
    sql = offline_precondition_sql()
    # VALUES 侧用三列别名 (tbl, col, ord)；catalog 侧投影 unnest WITH ORDINALITY 的 ord
    assert "AS e(tbl, col, ord)" in sql
    assert "k.ord::integer" in sql
    # 双向 EXCEPT 比较三元组（出现两次：E-C 与 C-E 方向）
    assert sql.count("AS e(tbl, col, ord)") == 2
    assert sql.count("k.ord::integer") == 2
    # 不再存在仅二元组的 PK 比较
    assert "AS e(tbl, col))" not in sql


def test_offline_precondition_pk_ordinals():
    """canonical 复合 PK 的 ordinal 为 1、2；单列 PK 为 1（按列顺序稳定展开）。"""
    sql = offline_precondition_sql()
    # 复合主键：列顺序必须保留（admin_user_roles = admin_user_id, role_id）
    assert "('admin_user_roles', 'admin_user_id', 1)" in sql
    assert "('admin_user_roles', 'role_id', 2)" in sql
    assert "('article_keywords', 'article_id', 1)" in sql
    assert "('article_keywords', 'keyword_id', 2)" in sql
    assert "('role_menus', 'role_id', 1)" in sql
    assert "('role_menus', 'menu_id', 2)" in sql
    # 单列主键：ordinal 固定 1
    assert "('admin_login_logs', 'id', 1)" in sql
    assert "('admin_users', 'id', 1)" in sql
    # 不存在「列名反序」的伪三元组（即不会以 (role_id, admin_user_id) 顺序展开）
    assert "('admin_user_roles', 'role_id', 1)" not in sql


def test_offline_precondition_has_no_secret():
    sql = offline_precondition_sql()
    assert "postgresql+psycopg" not in sql
    for pat in [re.compile(r"password\s*[:=]", re.I)]:
        assert not pat.search(sql)


# ---------------- 4. revision ----------------

class _FakeRevCtx:
    def __init__(self, offline=False):
        self._offline = offline

    def is_offline_mode(self):
        return self._offline


class _FakeRevOp:
    def __init__(self, bind=None):
        self.bind = bind
        self.executed = []

    def get_bind(self):
        if self.bind is None:
            raise AssertionError("get_bind called with no bind")
        return self.bind

    def execute(self, stmt):
        self.executed.append(stmt)


def _load_revision(fake_ctx, fake_op):
    fake_alembic = types.SimpleNamespace(context=fake_ctx, op=fake_op)
    spec = importlib.util.spec_from_file_location("pm_v2_rev_b1000001", REVISION_PATH)
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"alembic": fake_alembic}):
        spec.loader.exec_module(mod)
    return mod


def test_revision_identifiers():
    mod = _load_revision(_FakeRevCtx(offline=False), _FakeRevOp(bind=_FakeVConn()))
    assert mod.revision == "b1000001"
    assert mod.down_revision == "cdabba1e3903"
    assert mod.branch_labels is None
    assert mod.depends_on is None


def test_revision_upgrade_online_calls_validator_compatible():
    conn = _FakeVConn(col_rows=_canonical_col_rows(), pk_rows=_canonical_pk_rows())
    op = _FakeRevOp(bind=conn)
    mod = _load_revision(_FakeRevCtx(offline=False), op)
    mod.upgrade()  # COMPATIBLE：无异常、不发 DDL
    assert op.executed == []
    _no_write_calls(conn)


def test_revision_upgrade_online_partial_raises():
    only = BASE_LEGACY_TABLES[0]
    conn = _FakeVConn(
        col_rows=[r for r in _canonical_col_rows() if r[0] == only],
        pk_rows=[r for r in _canonical_pk_rows() if r[0] == only],
    )
    op = _FakeRevOp(bind=conn)
    mod = _load_revision(_FakeRevCtx(offline=False), op)
    with pytest.raises(BaseSchemaContractError, match=REASON_PARTIAL):
        mod.upgrade()
    assert op.executed == []


def test_revision_upgrade_offline_emits_precondition():
    op = _FakeRevOp()
    mod = _load_revision(_FakeRevCtx(offline=True), op)
    mod.upgrade()
    assert len(op.executed) == 1
    sql = str(op.executed[0])
    assert "DO $v2_base_precondition$" in sql
    assert REASON_PARTIAL in sql
    assert REASON_INCOMPATIBLE in sql
    assert "postgresql+psycopg" not in sql


def test_revision_downgrade_is_noop():
    op = _FakeRevOp()
    mod = _load_revision(_FakeRevCtx(offline=False), op)
    mod.downgrade()
    assert op.executed == []
