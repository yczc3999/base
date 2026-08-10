"""WP-01B 共享真 PostgreSQL 集成验收 fixture（integration/ 与 replay/ 共用）。

仅当环境变量 ``V2_TEST_ADMIN_DATABASE_URL`` 存在时才运行（缺省时整模块 skip）。fixture
只创建/删除 ``pm_v2_test_*`` 临时库；禁止在管理库或业务库直接运行 downgrade。

- ``temp_pg_db``：唯一临时库，删除前双重校验（前缀 + 非 template/current/admin），fail-closed。
- ``migrated_pg_db``：在临时库上执行 alembic upgrade head（当前为 b1000011）。
"""

import os
import types
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

ADMIN_URL_ENV = "V2_TEST_ADMIN_DATABASE_URL"
TEMP_PREFIX = "pm_v2_test_"
_RESERVED_DB_NAMES = frozenset({"postgres", "template0", "template1"})
ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"


@pytest.fixture
def temp_pg_db():
    """生成唯一 ``pm_v2_test_<hex>`` 临时库；finally 断开残留连接并删库。"""
    admin_url = os.environ.get(ADMIN_URL_ENV)
    if not admin_url:
        pytest.skip(f"{ADMIN_URL_ENV} not set — skip real-PostgreSQL integration")

    dbname = f"{TEMP_PREFIX}{uuid.uuid4().hex[:12]}"
    assert dbname.startswith(TEMP_PREFIX), f"unexpected db name: {dbname}"
    assert dbname not in _RESERVED_DB_NAMES, f"refusing reserved db name: {dbname}"

    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        admin.dispose()

    db_url = make_url(admin_url).set(database=dbname).render_as_string(hide_password=False)
    try:
        yield types.SimpleNamespace(name=dbname, url=db_url)
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            with admin.connect() as c:
                rows = c.execute(
                    text(
                        "SELECT datname, datistemplate FROM pg_database WHERE datname=:n"
                    ),
                    {"n": dbname},
                ).fetchall()
                assert rows and rows[0][1] is False, f"refusing to drop non-template check failed: {dbname}"
                assert dbname.startswith(TEMP_PREFIX), f"refusing non-prefixed drop: {dbname}"
                c.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=:n AND pid<>pg_backend_pid()"
                    ),
                    {"n": dbname},
                )
                c.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        finally:
            admin.dispose()


@pytest.fixture
def migrated_pg_db(temp_pg_db):
    """在临时库上执行 alembic upgrade head（b1000011），供 WP-01B 集成使用。"""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    engine = create_engine(temp_pg_db.url, poolclass=NullPool)
    conn = engine.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, "head")
    finally:
        conn.close()
        engine.dispose()
    return temp_pg_db
