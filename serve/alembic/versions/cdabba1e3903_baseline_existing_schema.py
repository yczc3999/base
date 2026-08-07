"""baseline existing schema

Revision ID: cdabba1e3903
Revises:
Create Date: 2026-08-07 06:30:13.998420

空基线（no-op）：alembic 引入时 DB 已存在完整 schema（由 databases/migrations/*.sql
经 python -m app.migrate 建好）。此版本仅标记「当前 DB 状态 = 基线」，不执行任何 DDL。

注意（模型↔DB 已存在漂移，基线不消解）：
  - 手写迁移里是手工命名索引 idx_* / uq_*，模型里是 index=True / unique=True（生成 ix_*）；
  - 模型部分 JSON 列在 DB 实为 JSONB；
  - DB 有 chk_* 检查约束未在模型中。
`alembic revision --autogenerate` 会报出这些差异。消解漂移应单独出「对齐迁移」
（重命名索引 / 统一 JSONB），不要把这批差异并进后续业务迁移。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cdabba1e3903'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
