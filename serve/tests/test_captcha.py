"""
P2-1 验证码测试 — 生成/校验/一次性/过期

覆盖:
  1. generate_captcha 返回 captcha_id + 合法 SVG
  2. verify_captcha 正确匹配
  3. 验证码一次性 (校验后删除)
  4. 错误验证码拒绝
  5. 不存在/过期 id 拒绝
"""

import pytest

from app.config import settings
from app.utils.captcha import generate_captcha, verify_captcha

PREFIX = settings.APP_NAME


@pytest.mark.asyncio
async def test_generate_captcha_returns_svg(mock_redis):
    data = await generate_captcha()
    assert data["captcha_id"]
    assert data["svg"].startswith("<svg")
    assert data["svg"].endswith("</svg>")
    # 验证码已存 Redis (TTL 5min)
    assert await mock_redis.get(f"{PREFIX}:captcha:{data['captcha_id']}") is not None


@pytest.mark.asyncio
async def test_verify_correct_code(mock_redis):
    data = await generate_captcha()
    code = await mock_redis.get(f"{PREFIX}:captcha:{data['captcha_id']}")
    assert await verify_captcha(data["captcha_id"], code) is True


@pytest.mark.asyncio
async def test_verify_case_insensitive(mock_redis):
    data = await generate_captcha()
    code = await mock_redis.get(f"{PREFIX}:captcha:{data['captcha_id']}")
    assert await verify_captcha(data["captcha_id"], code.lower()) is True


@pytest.mark.asyncio
async def test_captcha_single_use(mock_redis):
    """一次性: 校验成功后再次校验失败"""
    data = await generate_captcha()
    code = await mock_redis.get(f"{PREFIX}:captcha:{data['captcha_id']}")
    assert await verify_captcha(data["captcha_id"], code) is True
    assert await verify_captcha(data["captcha_id"], code) is False


@pytest.mark.asyncio
async def test_verify_wrong_code(mock_redis):
    data = await generate_captcha()
    assert await verify_captcha(data["captcha_id"], "XXXX") is False


@pytest.mark.asyncio
async def test_verify_unknown_id(mock_redis):
    assert await verify_captcha("nonexistent", "ABCD") is False


@pytest.mark.asyncio
async def test_verify_empty_params(mock_redis):
    assert await verify_captcha("", "") is False
