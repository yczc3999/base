"""
QueryHelper — 通用列表查询 DSL

════════════════════════════════════════════════════════════
一、基础用法（字段级条件，默认 AND 关系）
════════════════════════════════════════════════════════════

  简写（自动推导 eq / in）：
    {"status": 1}                              → status = 1
    {"status": [1, 2]}                         → status IN (1, 2)

  标准格式：
    {"status": {"op": "eq", "value": 1}}       → status = 1
    {"price":  {"op": "between", "value": [10, 100]}}
    {"name":   {"op": "like", "value": "张"}}
    {"deleted_at": {"op": "is_null"}}

════════════════════════════════════════════════════════════
二、操作符清单（30 个）
════════════════════════════════════════════════════════════

  比较：eq, neq, gt, gte, lt, lte
  集合：in, not_in
  范围：between, not_between
  模糊：like, not_like, prefix, suffix
  空值：is_null, not_null, is_empty, not_empty
  日期：date_eq, date_gt, date_gte, date_lt, date_lte, date_between,
        year_eq, month_eq, day_eq
  JSON：json_contains

════════════════════════════════════════════════════════════
三、逻辑组合（$and / $or 任意嵌套）
════════════════════════════════════════════════════════════

  {"$or": [{"status": 1}, {"status": 2}]}

  {"$and": [
    {"status": 1},
    {"$or": [
      {"username": {"op": "like", "value": "admin"}},
      {"email":    {"op": "suffix", "value": "@vip.com"}}
    ]}
  ]}

════════════════════════════════════════════════════════════
四、keyword 搜索（一个词搜多个字段，OR 关系）
════════════════════════════════════════════════════════════

  独立参数：keyword=张三
  由 Logic 层的 keyword_fields() 决定搜索哪些字段

════════════════════════════════════════════════════════════
五、空值 / 零值处理规则
════════════════════════════════════════════════════════════

  简写模式：None / "" / [] → 跳过；0 / False / "0" → 保留
  标准模式：各操作符有各自的 value 校验规则（见 _apply_operator）
"""

from sqlalchemy import or_, and_, func, cast, String, extract

OPERATORS = {
    # 比较
    "eq", "neq", "gt", "gte", "lt", "lte",
    # 集合
    "in", "not_in",
    # 范围
    "between", "not_between",
    # 模糊
    "like", "not_like", "prefix", "suffix",
    # 空值
    "is_null", "not_null", "is_empty", "not_empty",
    # 日期
    "date_eq", "date_gt", "date_gte", "date_lt", "date_lte", "date_between",
    "year_eq", "month_eq", "day_eq",
    # 时间
    "time_eq", "time_gt", "time_gte", "time_lt", "time_lte",
    # JSON（PostgreSQL JSONB）
    "json_contains", "json_extract_eq",
}


def _is_blank(value) -> bool:
    """简写模式空值判断：None / "" / [] 跳过，0 / False / "0" 保留"""
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    return False


def _is_valid_str(value) -> bool:
    """有效搜索字符串（非空、非纯空格）"""
    return isinstance(value, str) and value.strip() != ""


def _is_valid_list(value, min_len: int = 1) -> bool:
    """有效非空数组"""
    return isinstance(value, list) and len(value) >= min_len


def _safe_int(value) -> int | None:
    """安全的 int 转换，失败返回 None"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _parse_hour(value) -> int | None:
    """从 'HH:MM:SS' 或 'HH' 格式解析小时数，失败返回 None"""
    if not _is_valid_str(value):
        return None
    try:
        h = int(value.split(":")[0])
        return h if 0 <= h <= 23 else None
    except (ValueError, IndexError):
        return None


def _is_valid_range(value) -> bool:
    """有效范围值（长度 2，两个元素非 None）"""
    return (
        isinstance(value, list)
        and len(value) == 2
        and value[0] is not None
        and value[1] is not None
    )


def apply_filters(model, filters: dict, whitelist: list[str] = None) -> list:
    """
    将 filters dict 转换为 SQLAlchemy where 条件列表

    :param model:      SQLAlchemy Model 类
    :param filters:    筛选条件（支持 $and / $or 嵌套）
    :param whitelist:  允许的字段白名单，None = 不限制
    :return:           conditions list（顶层 AND 关系）
    """
    if not isinstance(filters, dict):
        return []

    conditions = []
    for key, condition in filters.items():
        # ── 逻辑组合 ──
        if key == "$or" and isinstance(condition, list):
            clauses = []
            for sub in condition:
                if isinstance(sub, dict):
                    sub_conds = apply_filters(model, sub, whitelist)
                    if sub_conds:
                        clauses.append(and_(*sub_conds) if len(sub_conds) > 1 else sub_conds[0])
            if clauses:
                conditions.append(or_(*clauses))
            continue

        if key == "$and" and isinstance(condition, list):
            clauses = []
            for sub in condition:
                if isinstance(sub, dict):
                    clauses.extend(apply_filters(model, sub, whitelist))
            if clauses:
                conditions.append(and_(*clauses))
            continue

        # ── 字段条件 ──
        if whitelist is not None and key not in whitelist:
            continue

        col = getattr(model, key, None)
        if col is None:
            continue

        # 简写模式
        if not isinstance(condition, dict):
            if _is_blank(condition):
                continue
            if isinstance(condition, list):
                conditions.append(col.in_(condition))
            else:
                conditions.append(col == condition)
            continue

        # 标准格式
        op = condition.get("op", "eq")
        value = condition.get("value")

        if op not in OPERATORS:
            continue

        cond = _apply_operator(col, op, value)
        if cond is not None:
            conditions.append(cond)

    return conditions


def apply_keyword(model, keyword: str, fields: list[str]):
    """keyword 搜索：一个关键词搜多个字段（OR）"""
    if not isinstance(keyword, str):
        return None
    keyword = keyword.strip()
    if not keyword or not fields:
        return None
    clauses = []
    for field in fields:
        col = getattr(model, field, None)
        if col is not None:
            clauses.append(col.ilike(f"%{keyword}%"))
    return or_(*clauses) if clauses else None


def _apply_operator(col, op: str, value):
    match op:
        # ── 比较 ──
        case "eq":
            return col == value
        case "neq":
            return col != value
        case "gt":
            return col > value if value is not None else None
        case "gte":
            return col >= value if value is not None else None
        case "lt":
            return col < value if value is not None else None
        case "lte":
            return col <= value if value is not None else None

        # ── 集合 ──
        case "in":
            return col.in_(value) if _is_valid_list(value) else None
        case "not_in":
            return col.notin_(value) if _is_valid_list(value) else None

        # ── 范围 ──
        case "between":
            return col.between(value[0], value[1]) if _is_valid_range(value) else None
        case "not_between":
            return ~col.between(value[0], value[1]) if _is_valid_range(value) else None

        # ── 模糊 ──
        case "like":
            return col.ilike(f"%{value}%") if _is_valid_str(value) else None
        case "not_like":
            return ~col.ilike(f"%{value}%") if _is_valid_str(value) else None
        case "prefix":
            return col.ilike(f"{value}%") if _is_valid_str(value) else None
        case "suffix":
            return col.ilike(f"%{value}") if _is_valid_str(value) else None

        # ── 空值 ──
        case "is_null":
            return col.is_(None)
        case "not_null":
            return col.isnot(None)
        case "is_empty":
            return or_(col.is_(None), col == "")
        case "not_empty":
            return and_(col.isnot(None), col != "")

        # ── 日期 ──
        # date_eq/gt/gte/lt/lte：将字段 cast 为 DATE 再比较
        # 前端传 "2026-03-31" 字符串即可
        case "date_eq":
            return cast(col, String).like(f"{value}%") if _is_valid_str(value) else None
        case "date_gt":
            return col > value if value is not None else None
        case "date_gte":
            return col >= value if value is not None else None
        case "date_lt":
            return col < value if value is not None else None
        case "date_lte":
            return col <= value if value is not None else None
        case "date_between":
            return col.between(value[0], value[1]) if _is_valid_range(value) else None
        case "year_eq":
            return extract("year", col) == _safe_int(value) if _safe_int(value) is not None else None
        case "month_eq":
            return extract("month", col) == _safe_int(value) if _safe_int(value) is not None else None
        case "day_eq":
            return extract("day", col) == _safe_int(value) if _safe_int(value) is not None else None

        # ── 时间（提取时间部分比较）──
        # 前端传 "14:30:00" 格式
        case "time_eq":
            h = _parse_hour(value)
            return extract("hour", col) == h if h is not None else None
        case "time_gt":
            return cast(func.to_char(col, "HH24:MI:SS"), String) > value if _is_valid_str(value) else None
        case "time_gte":
            return cast(func.to_char(col, "HH24:MI:SS"), String) >= value if _is_valid_str(value) else None
        case "time_lt":
            return cast(func.to_char(col, "HH24:MI:SS"), String) < value if _is_valid_str(value) else None
        case "time_lte":
            return cast(func.to_char(col, "HH24:MI:SS"), String) <= value if _is_valid_str(value) else None

        # ── JSON（PostgreSQL JSONB）──
        # json_contains：JSONB 字段包含某个值（文本匹配）
        # 用法：{"tags": {"op": "json_contains", "value": "vip"}}
        case "json_contains":
            if value is not None:
                return col.cast(String).ilike(f"%{value}%")
            return None
        # json_extract_eq：提取 JSON 某个 key 的值做等值比较
        # 用法：{"config": {"op": "json_extract_eq", "value": {"key": "level", "val": "gold"}}}
        case "json_extract_eq":
            if isinstance(value, dict) and "key" in value and "val" in value:
                return col[value["key"]].as_string() == str(value["val"])
            return None

        case _:
            return None
