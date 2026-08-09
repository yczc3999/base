"""WP-01A-00 真 PostgreSQL 集成验收 fixture。

仅当环境变量 ``V2_TEST_ADMIN_DATABASE_URL`` 存在时才运行（缺省时整模块 skip，普通全量
suite 可离线通过）。fixture 只创建/删除 ``pm_v2_test_*`` 临时库；禁止在管理库或业务库
直接运行 downgrade。
"""

import os
import types
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

ADMIN_URL_ENV = "V2_TEST_ADMIN_DATABASE_URL"
TEMP_PREFIX = "pm_v2_test_"
# §8 风险：删除前必须确认不是 template/current/admin 库。
_RESERVED_DB_NAMES = frozenset({"postgres", "template0", "template1"})


@pytest.fixture
def temp_pg_db():
    """生成唯一 ``pm_v2_test_<hex>`` 临时库；finally 断开残留连接并删库。

    任何前缀/保留库校验失败都 fail-closed（assert 直接中断），绝不误删。
    """
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

    # 仅替换 database，必须保留管理 URL 的 driver/user/password/host/port/query。
    # 否则远程/非默认端口会“在 A 建库、去 B 跑迁移”。
    db_url = make_url(admin_url).set(database=dbname).render_as_string(
        hide_password=False
    )
    try:
        yield types.SimpleNamespace(name=dbname, url=db_url)
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            with admin.connect() as c:
                # 双重校验后再删：确属 pm_v2_test_* 且非 template/current/admin
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
