"""group trading pages under 交易

Revision ID: b1000075
Revises: b1000074
Create Date: 2026-08-13 08:00:00

v2_0075 —— 可见交易页挂到顶级目录「交易」（与「内容管理」「系统管理」同级），
不再 15 条平铺在侧栏。路径仍是 ``/markets`` ``/tags``，不加 ``/v2/``。
``v2-admin`` 只留权限按钮和隐藏详情。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b1000075"
down_revision: Union[str, Sequence[str], None] = "b1000074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DIR_SLUG = "trading"
_DIR_LABEL = "交易"
_PAGES = (
    "v2-page-dashboard",
    "v2-page-markets",
    "v2-page-tags",
    "v2-page-components",
    "v2-page-episodes",
    "v2-page-decisions",
    "v2-page-execution",
    "v2-page-models-ai",
    "v2-page-ai-invocations",
    "v2-page-costs",
    "v2-page-config",
    "v2-page-releases",
    "v2-page-evaluation",
    "v2-page-replay",
    "v2-page-integrity",
)
_SLUG_IN = ", ".join(f"'{slug}'" for slug in _PAGES)


def upgrade() -> None:
    op.execute(
        f"""
DO $v2_wp_trading_menu_group$
DECLARE trading_dir bigint;
BEGIN
    IF to_regclass('public.menus') IS NULL THEN
        RETURN;
    END IF;
    INSERT INTO public.menus (parent_id, type, slug, label, icon, is_visible, sort, status)
    SELECT 0, 0, '{_DIR_SLUG}', '{_DIR_LABEL}', 'TrendCharts', true, 1, 1
     WHERE NOT EXISTS (SELECT 1 FROM public.menus WHERE slug = '{_DIR_SLUG}');
    IF EXISTS (
        SELECT 1 FROM public.menus
         WHERE slug = '{_DIR_SLUG}'
           AND NOT (parent_id = 0 AND type = 0 AND label = '{_DIR_LABEL}'
                    AND is_visible = true AND status = 1)
    ) THEN
        RAISE EXCEPTION 'v2_wp_trading_menu_slug_conflict'
        USING ERRCODE='55000';
    END IF;
    SELECT id INTO trading_dir FROM public.menus WHERE slug = '{_DIR_SLUG}';
    UPDATE public.menus
       SET parent_id = trading_dir
     WHERE slug IN ({_SLUG_IN})
       AND type = 1
       AND is_visible = true;
END $v2_wp_trading_menu_group$
"""
    )


def downgrade() -> None:
    op.execute(
        f"""
DO $v2_wp_trading_menu_group_down$
BEGIN
    IF to_regclass('public.menus') IS NULL THEN
        RETURN;
    END IF;
    UPDATE public.menus
       SET parent_id = 0
     WHERE slug IN ({_SLUG_IN})
       AND type = 1
       AND is_visible = true;
    DELETE FROM public.menus
     WHERE slug = '{_DIR_SLUG}'
       AND type = 0
       AND NOT EXISTS (
           SELECT 1 FROM public.menus c WHERE c.parent_id = public.menus.id
       );
END $v2_wp_trading_menu_group_down$
"""
    )
