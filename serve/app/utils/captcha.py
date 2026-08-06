"""图形验证码 — 纯 SVG 生成（零 SDK, 无 Pillow 依赖）.

流程:
  GET /api/admin/user/captcha  → 生成 4 字符验证码, 存 Redis(id→code, TTL 5min), 返回 {captcha_id, svg}
  登录时校验 captcha_id + captcha_code → 通过后删除（一次性）

开关: settings(login_security, captcha_enabled, "1") 控制是否强制验证码。
字符集去掉易混淆的 0O1lI, 仅大写字母+数字。
"""
import random
import uuid

from app.config import settings
from app.services.redis import get_redis

# 去掉 0/O/1/l/I 等易混淆字符
CAPTCHA_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CAPTCHA_LEN = 4
CAPTCHA_TTL = 300  # 5 分钟

CAPTCHA_PREFIX = f"{settings.APP_NAME}:captcha:"

_COLORS = ("#2563eb", "#16a34a", "#ef4444", "#f59e0b", "#6b7280")


def _generate_svg(code: str) -> str:
    """生成带随机旋转字符 + 干扰线的 SVG."""
    width, height = 120, 40
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#f1f5f9"/>',
    ]
    # 干扰线
    for _ in range(4):
        parts.append(
            f'<line x1="{random.randint(0, width)}" y1="{random.randint(0, height)}" '
            f'x2="{random.randint(0, width)}" y2="{random.randint(0, height)}" '
            f'stroke="#cbd5e1" stroke-width="1"/>'
        )
    # 字符（随机偏移/旋转/颜色）
    for i, ch in enumerate(code):
        x = 15 + i * 24 + random.randint(-3, 3)
        y = 28 + random.randint(-5, 5)
        rot = random.randint(-20, 20)
        color = random.choice(_COLORS)
        parts.append(
            f'<text x="{x}" y="{y}" fill="{color}" font-size="24" '
            f'font-family="monospace" font-weight="bold" '
            f'transform="rotate({rot} {x} {y})">{ch}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


async def generate_captcha() -> dict:
    """生成验证码: 返回 {captcha_id, svg}."""
    code = "".join(random.choices(CAPTCHA_CHARS, k=CAPTCHA_LEN))
    captcha_id = uuid.uuid4().hex[:16]
    r = await get_redis()
    await r.set(CAPTCHA_PREFIX + captcha_id, code, ex=CAPTCHA_TTL)
    return {"captcha_id": captcha_id, "svg": _generate_svg(code)}


async def verify_captcha(captcha_id: str, code: str) -> bool:
    """校验验证码（一次性, 通过后删除）. 大小写不敏感."""
    if not captcha_id or not code:
        return False
    r = await get_redis()
    key = CAPTCHA_PREFIX + captcha_id
    stored = await r.get(key)
    if stored is None:
        return False
    await r.delete(key)  # 一次性使用
    return stored.strip().lower() == str(code).strip().lower()
