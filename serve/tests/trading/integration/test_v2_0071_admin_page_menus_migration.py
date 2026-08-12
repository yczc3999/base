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

EXPECTED_PAGES = (
    ("v2-page-dashboard", "Dashboard", "/v2/dashboard", "v2/dashboard/index", "v2:dashboard:view", True, 1),
    ("v2-page-markets", "Markets", "/v2/markets", "v2/markets/index", "v2:markets:view", True, 2),
    ("v2-page-components", "Components", "/v2/components", "v2/components/index", "v2:components:view", True, 3),
    ("v2-page-episodes", "Episodes", "/v2/episodes", "v2/episodes/index", "v2:episodes:view", True, 4),
    ("v2-page-decisions", "Decisions", "/v2/decisions", "v2/decisions/index", "v2:decisions:view", True, 5),
    ("v2-page-execution", "Execution", "/v2/execution", "v2/execution/index", "v2:execution:view", True, 6),
    ("v2-page-models-ai", "Models & AI", "/v2/models-ai", "v2/models-ai/index", "v2:models:view", True, 7),
    ("v2-page-ai-invocations", "AI Invocations", "/v2/ai-invocations", "v2/ai-invocations/index", "v2:ai:view", True, 8),
    ("v2-page-costs", "Costs", "/v2/costs", "v2/costs/index", "v2:costs:view", True, 9),
    ("v2-page-config", "Strategy Config", "/v2/config", "v2/config/index", "v2:config:view", True, 10),
    ("v2-page-releases", "Releases", "/v2/releases", "v2/releases/index", "v2:release:view", True, 11),
    ("v2-page-evaluation", "Evaluation", "/v2/evaluation", "v2/evaluation/index", "v2:evaluation:view", True, 12),
    ("v2-page-replay", "Replay", "/v2/replay", "v2/replay/index", "v2:replay:view", True, 13),
    ("v2-page-integrity", "Integrity", "/v2/integrity", "v2/integrity/index", "v2:integrity:view", True, 14),
    ("v2-page-market-detail", "Market Detail", "/v2/markets/:id", "v2/markets/detail", "v2:markets:view", False, 101),
    ("v2-page-component-detail", "Component Detail", "/v2/components/:id", "v2/components/detail", "v2:components:view", False, 102),
    ("v2-page-episode-detail", "Episode Detail", "/v2/episodes/:id", "v2/episodes/detail", "v2:episodes:view", False, 103),
    ("v2-page-decision-detail", "Decision Detail", "/v2/decisions/:id", "v2/decisions/detail", "v2:decisions:view", False, 104),
    ("v2-page-ai-detail", "AI Invocation Detail", "/v2/ai-invocations/:id", "v2/ai-invocations/detail", "v2:ai:view", False, 105),
    ("v2-page-artifacts", "Artifacts", "/v2/artifacts/:content_hash", "v2/artifacts/detail", "v2:artifact:read", False, 106),
)
PAGE_SLUGS = tuple(row[0] for row in EXPECTED_PAGES)
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
    exact_pages = _query(
        url,
        "SELECT slug,label,path,template_path,perms,is_visible,sort "
        "FROM public.menus WHERE slug LIKE 'v2-page-%' ORDER BY sort",
    )
    assert exact_pages == list(EXPECTED_PAGES)
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
    # Stamp back without deleting rows, then execute 0071 again to exercise the seed itself.
    _run(command.stamp, "b1000070", url)
    _run(command.upgrade, V71, url)
    pages = _query(url, "SELECT count(*) FROM public.menus WHERE slug LIKE 'v2-page-%'")
    assert pages == [(20,)]


def test_seed_first_upgrade_rejects_permission_swapped_to_wrong_slug(temp_pg_db):
    """首次 upgrade 也要求 slug 与 permission 精确对应。"""
    url = temp_pg_db.url
    _run(command.upgrade, V52, url)
    _seed_base(url)
    _run(command.upgrade, "b1000070", url)
    parent_id = _query(url, "SELECT id FROM public.menus WHERE slug='v2-admin'")[0][0]
    _execute(
        url,
        "INSERT INTO public.menus "
        "(parent_id,type,slug,label,path,template_path,perms,is_visible,sort,status) "
        "VALUES (:p,1,'v2-page-markets','Markets','/v2/markets','v2/markets/index',"
        "'v2:episodes:view',true,2,1)",
        {"p": parent_id},
    )
    with pytest.raises(Exception, match="v2_wp07b_menu_slug_conflict"):
        _run(command.upgrade, V71, url)


def test_downgrade_tampered_menu_rejects(temp_pg_db):
    """篡改 0071 菜单行内容 → downgrade preflight 拒绝。"""
    url = temp_pg_db.url
    _upgrade_with_base(url)
    _execute(url, "UPDATE public.menus SET path='/v2/markets-tampered' WHERE slug='v2-page-markets'")
    with pytest.raises(Exception, match="v2_wp07b_menu_tampered"):
        _run(command.downgrade, V52, url)


def test_downgrade_missing_menu_rejects(temp_pg_db):
    url = temp_pg_db.url
    _upgrade_with_base(url)
    _execute(url, "DELETE FROM public.menus WHERE slug='v2-page-markets'")
    with pytest.raises(Exception, match="v2_wp07b_menu_missing:v2-page-markets"):
        _run(command.downgrade, "b1000070", url)


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
