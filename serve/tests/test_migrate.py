"""
Migration Runner 测试 — SQL 拆分 / 幂等 / 执行

测试策略：用 SQLite 内存库替代 PostgreSQL，验证 runner 核心逻辑
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import migrate as migrate_mod
from app.migrate import _split_statements, run_migrations, _ensure_schema_table, _applied_versions


# ==================== SQL 拆分 ====================

def test_split_simple_statements():
    sql = "CREATE TABLE a (id INT); CREATE TABLE b (id INT);"
    stmts = _split_statements(sql)
    assert len(stmts) == 2


def test_split_ignores_semicolon_in_string():
    sql = "INSERT INTO t VALUES ('a;b'); SELECT 1;"
    stmts = _split_statements(sql)
    assert len(stmts) == 2
    assert "'a;b'" in stmts[0]


def test_split_single_statement_no_trailing():
    stmts = _split_statements("SELECT 1")
    assert len(stmts) == 1
    assert stmts[0].strip() == "SELECT 1"


# ==================== 幂等执行 ====================

@pytest_asyncio.fixture
async def sqlite_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        yield db, engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_schema_table(sqlite_session):
    db, _ = sqlite_session
    await _ensure_schema_table(db)
    await db.commit()
    # 重复调用幂等
    await _ensure_schema_table(db)
    await db.commit()


@pytest.mark.asyncio
async def test_applied_versions_empty(sqlite_session):
    db, _ = sqlite_session
    await _ensure_schema_table(db)
    await db.commit()
    applied = await _applied_versions(db)
    assert applied == set()


@pytest.mark.asyncio
async def test_apply_single_migration(sqlite_session):
    db, _ = sqlite_session
    await _ensure_schema_table(db)
    await db.commit()
    await migrate_mod._apply_one(db, "test_001.sql", "CREATE TABLE t1 (id INT);")
    await db.commit()
    applied = await _applied_versions(db)
    assert "test_001.sql" in applied


@pytest.mark.asyncio
async def test_apply_skips_begin_commit(sqlite_session):
    """文件自带 BEGIN/COMMIT 时，runner 去掉外层避免嵌套事务"""
    db, _ = sqlite_session
    await _ensure_schema_table(db)
    await db.commit()
    content = "BEGIN; CREATE TABLE t2 (id INT); COMMIT;"
    await migrate_mod._apply_one(db, "test_002.sql", content)
    await db.commit()
    # 表已建
    result = await db.execute(migrate_mod.text("SELECT name FROM sqlite_master WHERE name='t2'"))
    assert result.scalar_one_or_none() == "t2"
