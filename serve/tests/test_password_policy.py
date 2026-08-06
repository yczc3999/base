"""
P2-2 密码策略测试 — 强度校验 + 过期判定

覆盖:
  1. validate_password_strength: 长度/大小写/数字/特殊字符
  2. 宽松规则下通过
  3. save 时校验弱密码被拒
  4. password_expired 过期/未过期/不启用
"""

from datetime import datetime, timedelta

import pytest

from app.logics.base import BizError


STRICT = {
    "min_length": 8, "require_upper": True, "require_lower": True,
    "require_digit": True, "require_symbol": False, "max_age_days": 0,
}
LOOSE = {
    "min_length": 6, "require_upper": False, "require_lower": False,
    "require_digit": False, "require_symbol": False, "max_age_days": 0,
}


# ==================== 强度校验 ====================

def test_strength_ok():
    from app.utils.password_policy import validate_password_strength
    validate_password_strength("Abcdef12", STRICT)  # 不抛即通过


def test_strength_too_short():
    from app.utils.password_policy import validate_password_strength
    with pytest.raises(BizError, match="长度"):
        validate_password_strength("Ab1", STRICT)


def test_strength_missing_upper():
    from app.utils.password_policy import validate_password_strength
    with pytest.raises(BizError, match="大写"):
        validate_password_strength("abcdef12", STRICT)


def test_strength_missing_digit():
    from app.utils.password_policy import validate_password_strength
    with pytest.raises(BizError, match="数字"):
        validate_password_strength("Abcdefgh", STRICT)


def test_strength_loose_passes():
    from app.utils.password_policy import validate_password_strength
    validate_password_strength("abc123", LOOSE)  # 宽松规则下弱密码也通过


# ==================== save 校验 ====================

@pytest.mark.asyncio
async def test_save_rejects_weak_password(mock_redis, monkeypatch):
    import app.utils.password_policy as pp

    async def _strict_rules(db):
        return STRICT
    monkeypatch.setattr(pp, "get_policy_rules", _strict_rules)

    from app.logics.admin_user import admin_user_logic
    with pytest.raises(BizError, match="长度"):
        await admin_user_logic.save(None, {"password": "abc"})


# ==================== 过期判定 ====================

@pytest.mark.asyncio
async def test_password_expired_after_max_age(mock_redis, monkeypatch):
    import app.utils.password_policy as pp

    async def _rules(db):
        return {**STRICT, "max_age_days": 30}
    monkeypatch.setattr(pp, "get_policy_rules", _rules)

    old = datetime.now() - timedelta(days=40)
    assert await pp.password_expired(None, old) is True
    recent = datetime.now() - timedelta(days=5)
    assert await pp.password_expired(None, recent) is False


@pytest.mark.asyncio
async def test_password_expired_disabled(mock_redis, monkeypatch):
    import app.utils.password_policy as pp

    async def _rules(db):
        return {**STRICT, "max_age_days": 0}
    monkeypatch.setattr(pp, "get_policy_rules", _rules)

    old = datetime.now() - timedelta(days=400)
    assert await pp.password_expired(None, old) is False
    assert await pp.password_expired(None, None) is False
