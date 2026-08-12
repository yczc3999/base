"""WP-07B Checkpoint A —— 0071 admin page menus migration（真 PostgreSQL）。

覆盖：空库/Base 升级、20 页面菜单（14 可见 + 6 隐藏）、slug 冲突 fail、
downgrade preflight（role_menus 绑定拒绝 / 菜单缺失拒绝 / 未知表拒绝）、downgrade 清理。
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
V71 = "b1000071"

PAGE_SLUGS = (
    "v2-page-dashboard", "v2-page-markets", "v2-page-components", "v2-page-episodes",
    "v2-page-decisions", "v2-page-execution", "v2-page-models-ai", "v2-page-ai-invocations",
    "v2-page-costs", "v2-page-config", "v2-page-releases", "v2-page-evaluation",
    "v2-page-replay", "v2-page-integrity", "v2-page-market-detail", "v2-page-component-detail",
    "v2-page-episode-detail", "v2-page-decision-detail", "v2-page-ai-detail", "v2-page-artifacts",
)
VISIBLE_PAGES = PAGE_SLUGS[:14]
HIDDEN_PAGES = PAGE_SLUGS[14:]


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
    cmd = ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d",
           db_url.split("///", 1)[1], "-f",
           str(SERVE_DIR / "tests/trading/fixtures/base_legacy_schema.sql")]
    run = subprocess.run(cmd, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr[-500:]


def _upgrade_with_base(url):
    _run(command.upgrade, V52, url)
    _seed_base(url)
    _run(command.upgrade, V71, url)


def test_empty_upgrade_no_menus(temp_pg_db):
    """空库（无 Base menus）→ 菜单 seed 静默跳过。"""
    url = temp_pg_db.url
    _run(command.upgrade, V71, url)
    has_menu = _query(url, "SELECT to_regclass('public.menus') IS NOT NULL")
    assert has_menu == [(False,)]


def test_base_upgrade_seeds_pages(temp_pg_db):
    url = temp_pg_db.url
    _upgrade_with_base(url)
    pages = {r[0] for r in _query(
        url, "SELECT slug FROM public.menus WHERE parent_id=(SELECT id FROM public.menus "
             "WHERE slug='v2-admin') AND type=1 AND slug LIKE 'v2-page-%'")}
    assert pages == set(PAGE_SLUGS)
    visible = {r[0] for r in _query(
        url, "SELECT slug FROM public.menus WHERE type=1 AND slug LIKE 'v2-page-%' "
             "AND is_visible=true")}
    assert visible == set(VISIBLE_PAGES)
    hidden = {r[0] for r in _query(
        url, "SELECT slug FROM public.menus WHERE type=1 AND slug LIKE 'v2-page-%' "
             "AND is_visible=false")}
    assert hidden == set(HIDDEN_PAGES)
    # 不隐式授权普通角色
    bound = _query(url,
        "SELECT count(*) FROM public.role_menus rm JOIN public.menus m ON m.id=rm.menu_id "
        "WHERE m.slug LIKE 'v2-page-%'")
    assert bound == [(0,)]
    # 详情页 path 带参数
    detail_path = _query(url, "SELECT path FROM public.menus WHERE slug='v2-page-episode-detail'")
    assert detail_path == [("/v2/episodes/:id",)]


def test_seed_idempotent(temp_pg_db):
    url = temp_pg_db.url
    _upgrade_with_base(url)
    _run(command.upgrade, V71, url)
    pages = _query(url, "SELECT count(*) FROM public.menus WHERE slug LIKE 'v2-page-%'")
    assert pages == [(20,)]


def test_slug_conflict_fails(temp_pg_db):
    """篡改 0071 菜单行内容 → downgrade preflight 拒绝。"""
    url = temp_pg_db.url
    _upgrade_with_base(url)
    _execute(url, "UPDATE public.menus SET path='/v2/markets-tampered' WHERE slug='v2-page-markets'")
    with pytest.raises(Exception, match="v2_wp07b_menu_tampered"):
        _run(command.downgrade, V52, url)


def test_downgrade_preflight_role_menu_bound_rejects(temp_pg_db):
    url = temp_pg_db.url
    _upgrade_with_base(url)
    engine = create_engine(url)
    with engine.begin() as c:
        rid = c.execute(text("INSERT INTO public.roles (name, label) VALUES ('t','T') "
                             "RETURNING id")).scalar_one()
        mid = c.execute(text("SELECT id FROM public.menus WHERE slug='v2-page-markets'")).scalar_one()
        c.execute(text("INSERT INTO public.role_menus (role_id, menu_id) VALUES (:r,:m)"),
                  {"r": rid, "m": mid})
    engine.dispose()
    with pytest.raises(Exception, match="v2_wp07b_role_menu_bound"):
        _run(command.downgrade, V52, url)


def test_downgrade_unknown_object_rejects(temp_pg_db):
    url = temp_pg_db.url
    _upgrade_with_base(url)
    _execute(url, "CREATE TABLE trading.intruder_0071 (id integer)")
    with pytest.raises(Exception, match="v2_wp07b_unknown_object"):
        _run(command.downgrade, V52, url)
    _execute(url, "DROP TABLE trading.intruder_0071")


def test_downgrade_cleanup(temp_pg_db):
    """无 role_menus 绑定 → downgrade 清理 0071 菜单，不影响 0070 权限。"""
    url = temp_pg_db.url
    _upgrade_with_base(url)
    _run(command.downgrade, "b1000070", url)
    pages = _query(url, "SELECT count(*) FROM public.menus WHERE slug LIKE 'v2-page-%'")
    assert pages == [(0,)]
    # 0070 BUTTON 权限保留
    perms = _query(url, "SELECT count(*) FROM public.menus WHERE perms LIKE 'v2:%' AND type=2")
    assert perms == [(16,)]
