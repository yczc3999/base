"""freeze base schema contract

Revision ID: b1000001
Revises: cdabba1e3903
Create Date: 2026-08-09 07:38:00

v2_0001 —— 冻结 Base legacy schema 兼容合同（WP-01A-01）。

正式语义从「自动对齐 Base metadata」收敛为「只读兼容合同」（任务 §2.2）：V2 不修改
18 张 Base 表，只验证目标库可用。

- 在线 ``upgrade()``：调用 ``app.base_schema_contract.validate_base_schema``。
  EMPTY（0 表）与 COMPATIBLE（18 表签名一致）都不产生任何 Base DDL；partial
  （1–17 表）与 incompatible（签名不符）在 version 前进前抛错，外层事务整体回滚。
- 离线：生成等价的 PostgreSQL precondition（DO 块），应用 SQL 时执行同样的
  EMPTY/partial/incompatible 检查，不因 offline 模式静默跳过。
- ``downgrade()``：只回退 version，不触碰任何 Base 对象。
"""
from typing import Sequence, Union

from alembic import context, op
from sqlalchemy import text

from app.base_schema_contract import (
    offline_precondition_sql,
    validate_base_schema,
)


# revision identifiers, used by Alembic.
revision: str = "b1000001"
down_revision: Union[str, Sequence[str], None] = "cdabba1e3903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: 只验证 Base 兼容合同，不产生 Base DDL。"""
    if context.is_offline_mode():
        op.execute(text(offline_precondition_sql()))
    else:
        validate_base_schema(op.get_bind())


def downgrade() -> None:
    """Downgrade schema: 不改 Base 对象，仅由 Alembic 回退 version。"""
    pass
