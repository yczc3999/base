"""fix tags menu path to Base hash route

Revision ID: b1000073
Revises: b1000072
Create Date: 2026-08-13 00:10:00

v2_0073 —— Tags 列表走 Base 菜单 path ``/tags``（hash 下为 ``#/tags``），
不再使用前端 ``/v2/tags``。已写入 ``/v2/tags`` 的 0072 行就地改 path。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b1000073"
down_revision: Union[str, Sequence[str], None] = "b1000072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
DO $v2_wp_tags_menu_path$
BEGIN
    IF to_regclass('public.menus') IS NULL THEN
        RETURN;
    END IF;
    UPDATE public.menus
       SET path = '/tags'
     WHERE slug = 'v2-page-tags'
       AND path = '/v2/tags';
END $v2_wp_tags_menu_path$
"""
    )


def downgrade() -> None:
    op.execute(
        """
DO $v2_wp_tags_menu_path_down$
BEGIN
    IF to_regclass('public.menus') IS NULL THEN
        RETURN;
    END IF;
    UPDATE public.menus
       SET path = '/v2/tags'
     WHERE slug = 'v2-page-tags'
       AND path = '/tags';
END $v2_wp_tags_menu_path_down$
"""
    )
