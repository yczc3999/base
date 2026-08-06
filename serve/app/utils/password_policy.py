"""密码策略 — 强度校验 + 定期过期, 规则存 settings(category=password_policy).

配置项:
  min_length    最小长度 (默认 8)
  require_upper 需大写字母 (1/0, 默认 1)
  require_lower 需小写字母 (1/0, 默认 1)
  require_digit 需数字     (1/0, 默认 1)
  require_symbol 需特殊字符 (1/0, 默认 0)
  max_age_days  密码最长使用天数 (0=不过期, 默认 0)

接入点: 修改密码 / 注册 / 后台创建用户时校验强度; 登录时标记过期。
"""
from app.logics.base import BizError


async def get_policy_rules(db) -> dict:
    """从 settings 表读取策略规则（默认值兜底）."""
    from app.logics.setting import setting_logic

    async def _int(name, default):
        v = await setting_logic.get(db, "password_policy", name, str(default))
        try:
            return int(v or default)
        except (TypeError, ValueError):
            return default

    async def _bool(name, default):
        return await setting_logic.get(db, "password_policy", name, str(default)) != "0"

    return {
        "min_length": await _int("min_length", 8),
        "require_upper": await _bool("require_upper", True),
        "require_lower": await _bool("require_lower", True),
        "require_digit": await _bool("require_digit", True),
        "require_symbol": await _bool("require_symbol", False),
        "max_age_days": await _int("max_age_days", 0),
    }


def validate_password_strength(password: str, rules: dict) -> None:
    """校验密码强度, 不满足抛 BizError."""
    if not password:
        raise BizError("密码不能为空")
    min_len = int(rules.get("min_length", 8))
    if len(password) < min_len:
        raise BizError(f"密码长度至少 {min_len} 位")
    if rules.get("require_upper") and not any(c.isupper() for c in password):
        raise BizError("密码需包含大写字母")
    if rules.get("require_lower") and not any(c.islower() for c in password):
        raise BizError("密码需包含小写字母")
    if rules.get("require_digit") and not any(c.isdigit() for c in password):
        raise BizError("密码需包含数字")
    if rules.get("require_symbol") and not any(not c.isalnum() for c in password):
        raise BizError("密码需包含特殊字符")


async def password_expired(db, password_changed_at) -> bool:
    """密码是否超过最长使用期限."""
    rules = await get_policy_rules(db)
    max_days = int(rules.get("max_age_days", 0))
    if max_days <= 0 or not password_changed_at:
        return False
    from datetime import datetime
    # 兼容 datetime 与 ISO 字符串
    if isinstance(password_changed_at, str):
        try:
            from datetime import datetime as _dt
            password_changed_at = _dt.fromisoformat(password_changed_at)
        except ValueError:
            return False
    age_days = (datetime.now() - password_changed_at).days
    return age_days >= max_days
