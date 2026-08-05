"""
QueryHelper 测试 — 验证过滤 DSL 生成正确 SQLAlchemy 条件

测试策略：用真实 Column 构建表达式，检查条件逻辑（不连 DB）
"""

import pytest
from sqlalchemy import Column, Integer, String, DateTime
from app.utils.query import apply_filters, apply_keyword, OPERATORS


# 一个裸表定义（只用 Column，不绑定 DB）
from sqlalchemy.orm import declarative_base

_Base = declarative_base()


class FakeModel(_Base):
    __tablename__ = "fake"
    id = Column(Integer, primary_key=True)
    name = Column(String(50))
    email = Column(String(100))
    status = Column(Integer)
    created_at = Column(DateTime)


def _sql(model):
    """把条件转成可读的 SQL 字符串（用于断言）"""
    from sqlalchemy import select
    stmt = select(model.__table__.c.id)
    for cond in model._conds:
        stmt = stmt.where(cond)
    return str(stmt)


@pytest.mark.parametrize("op", sorted(OPERATORS))
def test_operators_registered(op):
    """所有声明的操作符都在 OPERATORS 集合里"""
    assert op in OPERATORS


def test_eq_shorthand():
    """简写 {field: value} → equality"""
    conds = apply_filters(FakeModel, {"status": 1})
    assert len(conds) == 1
    assert "fake.status" in _cond_str(conds[0])


def test_in_shorthand():
    """简写 {field: [1,2]} → IN"""
    conds = apply_filters(FakeModel, {"status": [1, 2]})
    assert len(conds) == 1
    assert "IN" in _cond_str(conds[0])


def test_standard_eq():
    conds = apply_filters(FakeModel, {"status": {"op": "eq", "value": 1}})
    assert "fake.status" in _cond_str(conds[0])


def test_gt():
    conds = apply_filters(FakeModel, {"status": {"op": "gt", "value": 5}})
    assert "fake.status" in _cond_str(conds[0])
    assert ">" in _cond_str(conds[0])


def test_like():
    conds = apply_filters(FakeModel, {"name": {"op": "like", "value": "张"}})
    assert "LIKE" in _cond_str(conds[0])


def test_between():
    conds = apply_filters(FakeModel, {"status": {"op": "between", "value": [1, 10]}})
    assert "BETWEEN" in _cond_str(conds[0])


def test_is_null():
    conds = apply_filters(FakeModel, {"email": {"op": "is_null"}})
    assert "IS NULL" in _cond_str(conds[0])


def test_not_null():
    conds = apply_filters(FakeModel, {"email": {"op": "not_null"}})
    assert "IS NOT NULL" in _cond_str(conds[0])


def test_whitelist_blocks_non_allowed():
    """白名单外字段被过滤掉"""
    conds = apply_filters(FakeModel, {"status": 1, "name": "x"}, whitelist=["status"])
    assert len(conds) == 1


def test_or_combination():
    """$or 组合"""
    conds = apply_filters(FakeModel, {"$or": [{"status": 1}, {"status": 2}]})
    assert len(conds) == 1
    assert "OR" in _cond_str(conds[0])


def test_and_or_nested():
    """$and 嵌套 $or（外层 $and 包成单个 and_，内含 status + or 组合）"""
    conds = apply_filters(FakeModel, {"$and": [
        {"status": 1},
        {"$or": [{"name": "a"}, {"name": "b"}]},
    ]})
    assert len(conds) == 1
    assert "AND" in _cond_str(conds[0])


def test_unknown_field_ignored():
    """不存在的字段被忽略"""
    conds = apply_filters(FakeModel, {"nonexistent": 1})
    assert len(conds) == 0


def test_blank_values_skipped():
    """简写模式：None / "" / [] 跳过"""
    conds = apply_filters(FakeModel, {"status": None, "name": "", "email": []})
    assert len(conds) == 0


def test_keyword_multiple_fields():
    """keyword 搜多字段 OR"""
    conds = apply_keyword(FakeModel, "张三", ["name", "email"])
    assert conds is not None
    assert "OR" in str(conds)


def test_keyword_empty_returns_none():
    assert apply_keyword(FakeModel, "", ["name"]) is None
    assert apply_keyword(FakeModel, "  ", ["name"]) is None


def test_not_operator():
    conds = apply_filters(FakeModel, {"status": {"op": "neq", "value": 0}})
    assert "!=" in _cond_str(conds[0])


def test_prefix_suffix():
    conds = apply_filters(FakeModel, {"name": {"op": "prefix", "value": "vip"}})
    assert "LIKE" in _cond_str(conds[0])
    conds = apply_filters(FakeModel, {"email": {"op": "suffix", "value": "@vip.com"}})
    assert "LIKE" in _cond_str(conds[0])


def _cond_str(cond) -> str:
    return str(cond).replace("\n", " ")
