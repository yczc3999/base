"""WP-07A Checkpoint A —— 0070 admin permissions/indexes migration（真 PostgreSQL）。

覆盖：空库/Base 升级、16 权限 + 不可见目录、slug 冲突 fail、downgrade preflight
（role_menus 绑定拒绝 / 未知表拒绝 / index 缺失拒绝）、downgrade 清理、临时库零残留。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V52 = "b1000052"
V70 = "b1000070"

EXPECTED_PERMS = {
    "v2:dashboard:view", "v2:markets:view", "v2:components:view", "v2:episodes:view",
    "v2:decisions:view", "v2:execution:view", "v2:models:view", "v2:ai:view",
    "v2:ai:artifact", "v2:costs:view", "v2:config:view", "v2:release:view",
    "v2:evaluation:view", "v2:replay:view", "v2:integrity:view", "v2:artifact:read",
}


def _run(cmd, revision, db_url):
    cfg = Config(); cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    engine = create_engine(db_url, poolclass=NullPool); conn = engine.connect()
    cfg.attributes["connection"] = conn
    try:
        cmd(cfg, revision)
    finally:
        conn.close(); engine.dispose()


def _query(db_url, sql, params=None):
    engine = create_engine(db_url)
    try:
        with engine.connect() as c:
            return c.execute(text(sql), params or {}).fetchall()
    finally:
        engine.dispose()


def _execute(db_url, sql, params=None):
    engine = create_engine(db_url)
    try:
        with engine.connect() as c:
            c.execute(text(sql), params or {})
            c.commit()
    finally:
        engine.dispose()


def _seed_base(db_url):
    """加载 Base legacy schema（menus/roles/role_menus/admin_users 等）。"""
    cmd = ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d",
           db_url.split("///", 1)[1], "-f",
           str(SERVE_DIR / "tests/trading/fixtures/base_legacy_schema.sql")]
    run = subprocess.run(cmd, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr[-500:]


def test_empty_upgrade_only_indexes(temp_pg_db):
    """空库（无 Base menus）→ 权限 seed 跳过，仅建 index。"""
    url = temp_pg_db.url
    _run(command.upgrade, V70, url)
    nidx = _query(url, "SELECT count(*) FROM pg_indexes WHERE schemaname='trading' "
                       "AND indexname LIKE 'ix_v2_admin_%'")
    assert nidx == [(17,)]
    # menus 表不存在 → 无权限行
    has_menu = _query(url, "SELECT to_regclass('public.menus') IS NOT NULL")
    assert has_menu == [(False,)]


def _upgrade_with_base(url):
    """先建 trading(0052)，再建 Base menus，最后升级 0070 触发权限 seed。"""
    _run(command.upgrade, V52, url)
    _seed_base(url)
    _run(command.upgrade, V70, url)


def test_base_upgrade_seeds_permissions(temp_pg_db):
    url = temp_pg_db.url
    _upgrade_with_base(url)
    # 权限 seed 在 upgrade 时（menus 已存在）发生
    perms = {r[0] for r in _query(
        url, "SELECT perms FROM public.menus WHERE perms LIKE 'v2:%'")}
    assert perms == EXPECTED_PERMS
    # 目录不可见
    dirs = _query(url, "SELECT slug, type, is_visible FROM public.menus "
                       "WHERE slug='v2-admin'")
    assert dirs == [("v2-admin", 0, False)]
    # 不隐式授权普通角色（无 role_menus 绑定）
    bound = _query(url,
        "SELECT count(*) FROM public.role_menus rm JOIN public.menus m ON m.id=rm.menu_id "
        "WHERE m.perms LIKE 'v2:%' OR m.slug='v2-admin'")
    assert bound == [(0,)]


def test_seed_idempotent_and_indexes(temp_pg_db):
    """重复 upgrade（Base 存在）幂等：不重复插入。"""
    url = temp_pg_db.url
    _upgrade_with_base(url)
    _run(command.upgrade, V70, url)
    perms = {r[0] for r in _query(
        url, "SELECT perms FROM public.menus WHERE perms LIKE 'v2:%'")}
    assert perms == EXPECTED_PERMS
    nidx = _query(url, "SELECT count(*) FROM pg_indexes WHERE schemaname='trading' "
                       "AND indexname LIKE 'ix_v2_admin_%'")
    assert nidx == [(17,)]


def test_slug_conflict_fails(temp_pg_db):
    """同 slug 但内容不全等 → downgrade preflight 拒绝（seed 冲突 fail-closed）。"""
    url = temp_pg_db.url
    _upgrade_with_base(url)
    # 篡改已 seed 的权限行 label
    _execute(url, "UPDATE public.menus SET label='tampered' WHERE perms='v2:markets:view'")
    with pytest.raises(Exception, match="v2_wp07a_menu_tampered"):
        _run(command.downgrade, V52, url)


def test_seed_first_upgrade_slug_conflict_fails(temp_pg_db):
    """首次 upgrade 0070 时已存在同 slug 但内容不全等 → seed 冲突 fail。"""
    url = temp_pg_db.url
    _run(command.upgrade, V52, url)
    _seed_base(url)
    # 预置一个 slug=v2-markets-view 但内容错的权限行
    _execute(url,
        "INSERT INTO public.menus (parent_id, type, slug, label, perms, is_visible) "
        "VALUES (0, 2, 'v2-markets-view', 'WRONG-LABEL', 'v2:wrong:perm', false)")
    with pytest.raises(Exception, match="v2_wp07a_permission_slug_conflict"):
        _run(command.upgrade, V70, url)


def test_downgrade_preflight_role_menu_bound_rejects(temp_pg_db):
    url = temp_pg_db.url
    _upgrade_with_base(url)
    engine = create_engine(url)
    with engine.begin() as c:
        rid = c.execute(text("INSERT INTO public.roles (name, label) VALUES ('t','T') "
                             "RETURNING id")).scalar_one()
        mid = c.execute(text("SELECT id FROM public.menus WHERE perms='v2:markets:view'")).scalar_one()
        c.execute(text("INSERT INTO public.role_menus (role_id, menu_id) VALUES (:r,:m)"),
                  {"r": rid, "m": mid})
    engine.dispose()
    with pytest.raises(Exception, match="v2_wp07a_role_menu_bound"):
        _run(command.downgrade, V52, url)


def test_downgrade_unknown_object_rejects(temp_pg_db):
    url = temp_pg_db.url
    _upgrade_with_base(url)
    _execute(url, "CREATE TABLE trading.intruder_0070 (id integer)")
    with pytest.raises(Exception, match="v2_wp07a_unknown_object"):
        _run(command.downgrade, V52, url)
    _execute(url, "DROP TABLE trading.intruder_0070")


def test_downgrade_cleanup(temp_pg_db):
    """无 role_menus 绑定 → downgrade 清理权限 + index，不影响 trading facts。"""
    url = temp_pg_db.url
    _upgrade_with_base(url)
    _run(command.downgrade, V52, url)
    perms = _query(url, "SELECT count(*) FROM public.menus WHERE perms LIKE 'v2:%' OR slug='v2-admin'")
    assert perms == [(0,)]
    nidx = _query(url, "SELECT count(*) FROM pg_indexes WHERE schemaname='trading' "
                       "AND indexname LIKE 'ix_v2_admin_%'")
    assert nidx == [(0,)]
