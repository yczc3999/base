"""flatten V2 pages into Base nav

Revision ID: b1000074
Revises: b1000073
Create Date: 2026-08-13 07:00:00

v2_0074 —— 交易页不再挂在不可见目录 ``v2-admin``（侧栏会把它画成独立分组）。
可见列表页提到顶级，中文名，路径去掉 ``/v2/`` 前缀（``#/markets`` 与 ``#/dashboard`` 同级）。
``v2-admin`` 只保留 type=2 权限按钮和隐藏详情。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b1000074"
down_revision: Union[str, Sequence[str], None] = "b1000073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# slug, label, path, icon, sort
_VISIBLE = (
    ("v2-page-dashboard", "总览", "/overview", "Activity", 20),
    ("v2-page-markets", "市场", "/markets", "TrendCharts", 21),
    ("v2-page-tags", "标签", "/tags", "Collection", 22),
    ("v2-page-components", "组件", "/components", "Files", 23),
    ("v2-page-episodes", "回合", "/episodes", "Timer", 24),
    ("v2-page-decisions", "决策", "/decisions", "Shield", 25),
    ("v2-page-execution", "执行", "/execution", "Connection", 26),
    ("v2-page-models-ai", "模型", "/models-ai", "MagicStick", 27),
    ("v2-page-ai-invocations", "AI 调用", "/ai-invocations", "Search", 28),
    ("v2-page-costs", "费用", "/costs", "Coin", 29),
    ("v2-page-config", "策略配置", "/config", "Sliders", 30),
    ("v2-page-releases", "发布", "/releases", "Document", 31),
    ("v2-page-evaluation", "评估", "/evaluation", "FileBarChart", 32),
    ("v2-page-replay", "回放", "/replay", "LogIn", 33),
    ("v2-page-integrity", "完整性", "/integrity", "Monitor", 34),
)

_HIDDEN = (
    ("v2-page-market-detail", "市场详情", "/markets/:id"),
    ("v2-page-component-detail", "组件详情", "/components/:id"),
    ("v2-page-episode-detail", "回合详情", "/episodes/:id"),
    ("v2-page-decision-detail", "决策详情", "/decisions/:id"),
    ("v2-page-ai-detail", "AI 调用详情", "/ai-invocations/:id"),
    ("v2-page-artifacts", "制品", "/artifacts/:content_hash"),
)

_DOWN_VISIBLE = (
    ("v2-page-dashboard", "Dashboard", "/v2/dashboard", 1),
    ("v2-page-markets", "Markets", "/v2/markets", 2),
    ("v2-page-components", "Components", "/v2/components", 3),
    ("v2-page-episodes", "Episodes", "/v2/episodes", 4),
    ("v2-page-decisions", "Decisions", "/v2/decisions", 5),
    ("v2-page-execution", "Execution", "/v2/execution", 6),
    ("v2-page-models-ai", "Models & AI", "/v2/models-ai", 7),
    ("v2-page-ai-invocations", "AI Invocations", "/v2/ai-invocations", 8),
    ("v2-page-costs", "Costs", "/v2/costs", 9),
    ("v2-page-config", "Strategy Config", "/v2/config", 10),
    ("v2-page-releases", "Releases", "/v2/releases", 11),
    ("v2-page-evaluation", "Evaluation", "/v2/evaluation", 12),
    ("v2-page-replay", "Replay", "/v2/replay", 13),
    ("v2-page-integrity", "Integrity", "/v2/integrity", 14),
    ("v2-page-tags", "Tags", "/tags", 15),
)

_DOWN_HIDDEN = (
    ("v2-page-market-detail", "Market Detail", "/v2/markets/:id"),
    ("v2-page-component-detail", "Component Detail", "/v2/components/:id"),
    ("v2-page-episode-detail", "Episode Detail", "/v2/episodes/:id"),
    ("v2-page-decision-detail", "Decision Detail", "/v2/decisions/:id"),
    ("v2-page-ai-detail", "AI Invocation Detail", "/v2/ai-invocations/:id"),
    ("v2-page-artifacts", "Artifacts", "/v2/artifacts/:content_hash"),
)


def _sql_path(path: str) -> str:
    if ":" not in path:
        return f"'{path}'"
    prefix, parameter = path.split(":", 1)
    return f"'{prefix}' || chr(58) || '{parameter}'"


def upgrade() -> None:
    visible_sql = "\n".join(
        "    UPDATE public.menus"
        f" SET parent_id = 0, label = '{label}', path = '{path}',"
        f" icon = '{icon}', sort = {sort}"
        f" WHERE slug = '{slug}' AND type = 1 AND is_visible = true;"
        for slug, label, path, icon, sort in _VISIBLE
    )
    hidden_sql = "\n".join(
        "    UPDATE public.menus"
        f" SET label = '{label}', path = {_sql_path(path)}"
        f" WHERE slug = '{slug}' AND type = 1 AND is_visible = false;"
        for slug, label, path in _HIDDEN
    )
    op.execute(
        f"""
DO $v2_wp_menus_join_base$
BEGIN
    IF to_regclass('public.menus') IS NULL THEN
        RETURN;
    END IF;
{visible_sql}
{hidden_sql}
END $v2_wp_menus_join_base$
"""
    )


def downgrade() -> None:
    visible_sql = "\n".join(
        "    UPDATE public.menus"
        f" SET parent_id = v2_dir, label = '{label}', path = '{path}',"
        f" icon = NULL, sort = {sort}"
        f" WHERE slug = '{slug}' AND type = 1 AND is_visible = true;"
        for slug, label, path, sort in _DOWN_VISIBLE
    )
    hidden_sql = "\n".join(
        "    UPDATE public.menus"
        f" SET label = '{label}', path = {_sql_path(path)}"
        f" WHERE slug = '{slug}' AND type = 1 AND is_visible = false;"
        for slug, label, path in _DOWN_HIDDEN
    )
    op.execute(
        f"""
DO $v2_wp_menus_join_base_down$
DECLARE v2_dir bigint;
BEGIN
    IF to_regclass('public.menus') IS NULL THEN
        RETURN;
    END IF;
    SELECT id INTO v2_dir FROM public.menus WHERE slug = 'v2-admin';
    IF v2_dir IS NULL THEN
        RETURN;
    END IF;
{visible_sql}
{hidden_sql}
END $v2_wp_menus_join_base_down$
"""
    )
