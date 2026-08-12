"""V2 Admin Read 共享依赖与响应辅助（WP-07A Checkpoint A/B）。

Controller 只 DTO/鉴权/UoW/响应；SQL 在 Repository；read 语义在 Logic。
- 列表统一走 ``admin_logic().page(...)``（keyset/cursor/filter/as_of）。
- cursor 无效 → ``fail(reason, 400)``。
"""

from __future__ import annotations

from app.db.cursor import CursorError
from app.logics.trading.admin_read import AdminReadLogic
from app.repositories.trading.admin_read import AdminReadRepository
from app.utils.response import fail, ok

_admin_logic: AdminReadLogic | None = None
_admin_repo = AdminReadRepository()


def get_admin_logic() -> AdminReadLogic:
    """惰性构建；APP_KEY 为空时 fail-closed（cursor 必须由非空服务端 secret 派生）。"""
    global _admin_logic
    if _admin_logic is None:
        _admin_logic = AdminReadLogic()
    return _admin_logic


def reset_admin_logic(logic: AdminReadLogic | None = None) -> None:
    """测试注入：允许替换为带固定 codec 的 logic。"""
    global _admin_logic
    _admin_logic = logic


def get_admin_repo() -> AdminReadRepository:
    return _admin_repo


def ok_page(page: dict) -> dict:
    return ok(page)
