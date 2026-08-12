"""迁移集成测试共享 helper（WP-01B）。

动态分区子表名按 ``<table>_<YYYYMM|YYYYMMDD>`` 生成，SQLAlchemy metadata 无法静态表达，
``alembic check`` 必然把它们判为 ``remove_table``/``remove_index``。诚实做法是白名单化，
并断言 modeled 表零 drift（任务 §6.2）。
"""

from __future__ import annotations

# 所有动态分区父表前缀（0002 月分区 + 0011 日分区 + 0021 月分区）
DYNAMIC_PARTITION_PREFIXES = (
    "outbox_delivery_history_",        # 0002: 月 RANGE
    "pm_source_event_batches_",        # 0011: 日 RANGE
    "pm_source_event_index_",          # 0011: 日 RANGE
    "pm_book_checkpoints_",            # 0011: 日 RANGE
    "pm_book_levels_",                 # 0011: 日 RANGE
    "ai_invocations_",                 # 0021: 月 RANGE
    "ai_tool_calls_",                  # 0021: 月 RANGE
    "ai_validation_results_",          # 0021: 月 RANGE
)


def is_dynamic_partition(name: str) -> bool:
    """判定表名是否为动态分区子表（白名单）。"""
    return any(name.startswith(prefix) for prefix in DYNAMIC_PARTITION_PREFIXES)


def _fk_references_dynamic_partition(diff) -> bool:
    """``remove_fk`` diff 是否引用动态分区子表（分区继承的 FK，metadata 无法静态表达）。"""
    try:
        fk = diff[1]
        for element in fk.elements:
            target = getattr(element, "target_fullname", "") or ""
            # ``trading.pm_book_checkpoints_20260813.id`` → 取表段
            qualified = target.rsplit(".", 1)[0]
            table = qualified.rsplit(".", 1)[-1] if "." in qualified else qualified
            if is_dynamic_partition(table):
                return True
    except Exception:
        return False
    return False


def _is_admin_keyset_index(name: str) -> bool:
    """WP-07A（b1000070）Admin Read 查询优化 composite index。

    这些 index 是跨表 keyset 分页优化，不映射到任何 ORM model 的 __table_args__
    （就像动态分区子表名无法静态表达），因此 alembic check 必然判为 remove_index。
    """
    return name.startswith("ix_v2_admin_")


def split_dynamic_diffs(diffs: list) -> tuple[list, list]:
    """把 alembic check 的 diffs 拆成 (dynamic_partition_diffs, modeled_diffs)。

    modeled_diffs 为空即表示全部 modeled 表零 drift。分区子表的 remove_table/remove_index、
    其继承的 remove_fk，以及 WP-07A Admin Read keyset index 一律白名单化。
    """
    dynamic: list = []
    modeled: list = []
    for d in diffs:
        kind = d[0]
        name = ""
        if kind in ("remove_table", "remove_index"):
            name = getattr(d[1], "name", "")
        is_dynamic = (
            is_dynamic_partition(name)
            or (kind == "remove_fk" and _fk_references_dynamic_partition(d))
            or (kind == "remove_index" and _is_admin_keyset_index(name))
        )
        if is_dynamic:
            dynamic.append(d)
        else:
            modeled.append(d)
    return dynamic, modeled
