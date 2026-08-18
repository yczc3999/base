"""
阶段 4/验收 12.3：代表性鉴权行为测试（离线）

直接在真实鉴权依赖函数上验证信封，不触发全栈请求（避免既有
Event loop is closed 环境问题与 DB/Redis 外部依赖）：

1. require_auth：无 Bearer → BizError(401)（对应 {"code":401} 信封）。
2. require_auth：无效 Bearer → BizError(401)。
3. require_admin：client scope → BizError(403)。
4. require_client：admin scope → BizError(403)。
5. current_auth：从已执行的 Route middleware state 获取 AuthInfo，
   缺失时以配置错误失败（与 test_route_registry 互补）。

覆盖依赖链 require_auth → BizError(401) / 各 require_* scope → BizError(403)，
即 12.3 的 401/403 信封验证。
"""

import asyncio

import pytest

from app.deps import AuthInfo, current_auth
from app.logics.base import BizError


class _FakeRequest:
    """仅提供 require_auth / current_auth 所需的最小接口。"""

    def __init__(self, headers=None):
        self.headers = headers or {}
        self.state = type("State", (), {})()


def test_require_auth_no_token_401():
    async def run():
        req = _FakeRequest()
        from app.deps import require_auth

        try:
            await require_auth(req)
            raise AssertionError("should raise 401")
        except BizError as e:
            return e.code, e.msg

    code, msg = asyncio.run(run())
    assert code == 401
    assert msg == "请登录"


def test_require_auth_invalid_token_401(monkeypatch):
    async def invalid_token(_token):
        return None

    monkeypatch.setattr("app.deps.verify_token", invalid_token)

    async def run():
        req = _FakeRequest({"authorization": "Bearer invalid-token"})
        from app.deps import require_auth

        try:
            await require_auth(req)
            raise AssertionError("should raise 401")
        except BizError as e:
            return e.code

    assert asyncio.run(run()) == 401


def test_require_admin_rejects_client_scope_403():
    auth = AuthInfo(
        user_id=1, scope="client", username="c1", access_token="tok", extra={}
    )
    # require_admin 是普通 async 函数，参数名 auth，直接传 AuthInfo 验证 scope 校验
    try:
        import asyncio
        from app.deps import require_admin

        result = asyncio.run(require_admin(auth=auth))
        raise AssertionError(f"should raise 403, got {result}")
    except BizError as e:
        assert e.code == 403
        assert e.msg == "无权限"


def test_require_client_rejects_admin_scope_403():
    import asyncio

    from app.deps import require_client

    auth = AuthInfo(
        user_id=1, scope="admin", username="a1", access_token="tok", extra={}
    )
    try:
        result = asyncio.run(require_client(auth=auth))
        raise AssertionError(f"should raise 403, got {result}")
    except BizError as e:
        assert e.code == 403


def test_require_auth_writes_state_for_current_auth(monkeypatch):
    """require_auth 成功后写入 request.state.auth，current_auth 可读。"""
    from app.deps import require_auth

    async def valid_token(_token):
        return {
            "user_id": 7,
            "scope": "admin",
            "username": "route-test",
            "is_super_admin": True,
        }

    monkeypatch.setattr("app.deps.verify_token", valid_token)
    req = _FakeRequest({"authorization": "Bearer valid-token"})
    auth = asyncio.run(require_auth(req))

    assert current_auth(req) is auth
    assert auth.user_id == 7
    assert auth.scope == "admin"
    assert auth.is_super_admin is True


def test_current_auth_missing_state_raises_config_error():
    """current_auth 在 middleware 缺失时以配置错误失败（硬失败）。"""
    req = _FakeRequest()
    with pytest.raises(RuntimeError):
        current_auth(req)
