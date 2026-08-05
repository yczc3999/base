"""
Validator 测试 — 规则边界行为（N1 修复验证）
"""

import pytest
from app.utils.validator import validate, _normalize_enum


# ==================== 归一化 ====================

@pytest.mark.parametrize("value,expected", [
    (True, "1"),
    (False, "0"),
    (1, "1"),
    (0, "0"),
    ("1", "1"),
    ("0", "0"),
    ("true", "1"),
    ("false", "0"),
    ("True", "1"),
    ("False", "0"),
    ("active", "active"),
])
def test_normalize_enum(value, expected):
    assert _normalize_enum(value) == expected


# ==================== in / not_in ====================

def test_in_with_boolean_true():
    """布尔 True 能匹配 in:1（此前 str(True)='True' 匹配失败）"""
    validate({"status": True}, {"status": "in:0,1"})


def test_in_with_boolean_false():
    validate({"status": False}, {"status": "in:0,1"})


def test_in_rejects_wrong_value():
    with pytest.raises(Exception):
        validate({"status": 2}, {"status": "in:0,1"})


def test_not_in_with_boolean():
    with pytest.raises(Exception):
        validate({"status": True}, {"status": "not_in:1"})


def test_in_with_string_number():
    validate({"status": "1"}, {"status": "in:0,1"})


# ==================== boolean ====================

@pytest.mark.parametrize("val", [True, False, 1, 0, "1", "0", "true", "false", "True", "False"])
def test_boolean_accepts_valid_forms(val):
    """boolean 接受所有合法布尔表示（含大写 True/False）"""
    validate({"flag": val}, {"flag": "boolean"})


def test_boolean_rejects_invalid():
    with pytest.raises(Exception):
        validate({"flag": "yes"}, {"flag": "boolean"})


# ==================== regex ====================

def test_regex_fullmatch_required():
    """regex 全匹配：前缀匹配不通过"""
    # 正例：完整匹配
    validate({"code": "AB12"}, {"code": "regex:^[A-Z]{2}\\d{2}$"})
    # 反例：部分匹配（re.fullmatch 应拒绝）
    with pytest.raises(Exception):
        validate({"code": "AB12X"}, {"code": "regex:^[A-Z]{2}\\d{2}$"})


# ==================== 常规规则仍工作 ====================

def test_required():
    with pytest.raises(Exception):
        validate({"username": ""}, {"username": "required"})
    validate({"username": "admin"}, {"username": "required"})


def test_min_max():
    validate({"age": 20}, {"age": "min:0|max:150"})
    with pytest.raises(Exception):
        validate({"age": 200}, {"age": "max:150"})
