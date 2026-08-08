"""
WP-00b-r2 key identity 协议验收测试（纯函数，无需 Redis）。

覆盖：类型严格（非 str 抛 TypeError）、namespace 可读（保留层级 + 校验 +
:~: 边界）、跨 namespace/segment 分割无碰撞、R1 特殊字符/Unicode/空串继续通过。
"""

import pytest

from app.services.redis_keys import (
    BOUNDARY,
    build_redis_key,
    decode_key_segment,
    encode_key_segment,
    validate_namespace,
)

NS = "pm:v2:prod:cache"


# ---------------- 类型严格：只接受 str ----------------

def test_non_str_segment_rejected():
    with pytest.raises(TypeError):
        encode_key_segment(1)
    with pytest.raises(TypeError):
        encode_key_segment(1.5)
    with pytest.raises(TypeError):
        encode_key_segment(None)
    with pytest.raises(TypeError):
        build_redis_key(NS, "a", 1)
    with pytest.raises(TypeError):
        build_redis_key(NS, 1, "a")


def test_str_segment_accepted():
    assert encode_key_segment("1") == "1"          # 字符串 "1" 正常
    assert build_redis_key(NS, "1") != build_redis_key(NS, "1", "x")


# ---------------- namespace 可读 + 校验 ----------------

def test_namespace_readable_preserved():
    """namespace 不编码，保持可读层级。"""
    k = build_redis_key("pm:v2:prod:cache", "a:b", "c")
    assert k.startswith("pm:v2:prod:cache:~:")
    assert "pm%3Av2%3Aprod%3Acache" not in k     # 不再整体编码


def test_namespace_validation():
    validate_namespace("pm:v2:prod:cache")       # 合法
    with pytest.raises(TypeError):
        validate_namespace(123)
    with pytest.raises(ValueError):
        validate_namespace("")                    # 空
    with pytest.raises(ValueError):
        validate_namespace("a:~:b")               # ~ 保留非法
    with pytest.raises(ValueError):
        validate_namespace("a:b::c")              # 空层
    with pytest.raises(ValueError):
        validate_namespace("a:b:c%20d")           # 层含空格


def test_boundary_is_reserved_and_unique():
    """BOUNDARY = ':~:'；namespace 无 ~，动态段无裸 ~ → 边界唯一。"""
    assert BOUNDARY == ":~:"
    k = build_redis_key(NS, "a~b", "c")
    # 段内 ~ 被编码为 %7E，不产生第二个裸 ~
    assert k.count(":~:") == 1
    assert "a~b" not in k
    assert "%7E" in k


def test_exact_key_format_no_double_colon():
    """精确格式 {namespace}:~:{encoded...}，不得出现 :~::。"""
    # 有段：ns:~:seg（一个冒号边界，非两个）
    assert build_redis_key("pm:v2:prod:cache", "a", "b") == "pm:v2:prod:cache:~:a:b"
    # 多段段间以 : 分隔
    assert build_redis_key("pm:v2:prod:cache", "x:y", "z") == "pm:v2:prod:cache:~:x%3Ay:z"
    # 无 :~:: 子串
    k = build_redis_key("pm:v2:prod:cache", "a", "b")
    assert ":~::" not in k
    assert k.count(":~:") == 1


def test_zero_segments_behavior():
    """build_redis_key(namespace) 零动态段 → 返回 {namespace}:~:。"""
    assert build_redis_key("pm:v2:prod:cache") == "pm:v2:prod:cache:~:"
    assert build_redis_key("pm:v2:prod:cache") != "pm:v2:prod:cache"


# ---------------- 无碰撞 ----------------

def test_collision_regression_exact_review_case():
    k1 = build_redis_key(NS, "a:b", "c")
    k2 = build_redis_key(NS, "a", "b:c")
    assert k1 != k2


def test_collision_free_over_segment_permutations():
    segments = ["a:b", "a", "b:c", "c", "x:y", "xy", "", "a%3Ab", "a b", "a/b", "中文", "é"]
    seen: set[str] = set()
    for left in segments:
        for right in segments:
            joined = build_redis_key(NS, left, right)
            assert joined not in seen, f"collision between {left!r},{right!r}"
            seen.add(joined)


def test_collision_free_across_namespaces():
    """相同输入 → 相同 key；不同 namespace/段分割 → 不同 key。"""
    cases = [
        ("pm:a", "x", "y"),
        ("pm:a", "x", "y"),          # 与第一个相同
        ("pm:a", "x", "y:z"),        # 同 ns 不同段值
        ("pm:a", "x:y"),             # 同 ns 不同段分割
        ("pm:a", "x", "y"),          # 重复
        ("pm:b", "x"),               # 不同 ns
        ("pm", "a:x"),               # ns 与段重新分割
    ]
    keys = [build_redis_key(ns, *segs) for ns, *segs in cases]
    unique_inputs = len({tuple(c) for c in cases})
    assert len(set(keys)) == unique_inputs


# ---------------- R1 回归：特殊字符 / Unicode / 空串 / 可逆 ----------------

def test_special_characters_encoded():
    k = build_redis_key(NS, "a:b", "c%d", "e f", "g/h", "x~y")
    assert "%3A" in k and "%25" in k and "%20" in k and "%2F" in k and "%7E" in k
    body = k.split(":~:", 1)[1]
    for segment in body.split(":"):
        assert ":" not in segment      # 段内无裸冒号
        assert "~" not in segment      # 段内无裸波浪号


def test_unicode_no_collision():
    assert build_redis_key(NS, "市场") != build_redis_key(NS, "市", "场")
    assert build_redis_key(NS, "é") != build_redis_key(NS, "é")


def test_deterministic():
    assert build_redis_key(NS, "a:b", "c") == build_redis_key(NS, "a:b", "c")


def test_encode_decode_roundtrip():
    samples = ["a:b", "a", "b:c", "%", "%3A", " ", "/", "~", "中文", "é", "", "a.b-c_d"]
    for s in samples:
        assert decode_key_segment(encode_key_segment(s)) == s


def test_empty_segment_encodes_to_nothing():
    # 空串段编码为空，但段间仍以 : 分隔 → 不产生碰撞
    assert build_redis_key(NS, "a", "") != build_redis_key(NS, "a")


def test_numeric_values_not_silently_coerced():
    # 整数/浮点必须显式 str()；不隐式强转
    assert encode_key_segment(str(1)) == "1"
    assert build_redis_key(NS, "a", str(12)) != build_redis_key(NS, "a", str(1), str(2))
