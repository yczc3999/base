from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.database import get_db
from app.utils.response import ok
from app.logics.setting import setting_logic
from app.deps import AuthInfo, require_admin, require_perms

router = APIRouter()

# 含凭据的敏感 category——非超管读取时掩码值、写入时拒绝
_SENSITIVE_CATEGORIES = frozenset({"seo_creds", "ai", "notify", "sms", "storage", "payment"})


def _mask_sensitive(data: dict) -> dict:
    """非超管读取时掩码敏感 category 的全部值"""
    masked = {}
    for category, kv in data.items():
        if category in _SENSITIVE_CATEGORIES:
            masked[category] = {k: ("***" if v else v) for k, v in (kv or {}).items()}
        else:
            masked[category] = kv
    return masked


# 公开接口 — 登录页获取站点基础信息 (名称/Logo/版权), 无需登录
@router.get("/setting/site")
async def get_site_info(db: AsyncSession = Depends(get_db)):
    all_data = await setting_logic.get_all(db)
    return ok(all_data.get("site", {}))


# 需要管理员登录 + 权限校验
@router.get("/setting/get")
async def get_settings(
    auth: AuthInfo = Depends(require_perms("admin:setting:get")),
    db: AsyncSession = Depends(get_db),
):
    data = await setting_logic.get_all(db)
    if not auth.is_super_admin:
        data = _mask_sensitive(data)
    return ok(data)


# 需要管理员登录 + 权限校验
@router.post("/setting/set")
async def set_settings(
    request: Request,
    auth: AuthInfo = Depends(require_perms("admin:setting:set")),
    db: AsyncSession = Depends(get_db),
):
    body = await request.json()

    # 非超管禁止写入敏感 category
    if not auth.is_super_admin:
        stripped = {k: v for k, v in body.items() if k not in _SENSITIVE_CATEGORIES}
        if len(stripped) != len(body):
            blocked = set(body.keys()) & _SENSITIVE_CATEGORIES
            from app.utils.response import fail
            return fail(f"无权修改以下配置: {', '.join(sorted(blocked))}", 403)
        body = stripped

    # 检测特定 key 变更，主动失效域内 Redis 缓存（避免 web 路由读到旧值）
    new_indexnow_key = (body.get("seo_creds", {}) or {}).get("indexnow_key")

    await setting_logic.set_many(db, body)

    if new_indexnow_key is not None:
        try:
            from app.services.redis import get_redis
            r = await get_redis()
            await r.delete("seo:indexnow_key_cache")
        except Exception:
            pass  # Redis 挂了不影响保存

    return ok(msg="保存成功")


# AI provider 默认配置（source of truth 在 services/ai.py）
@router.get("/setting/ai/provider-defaults")
async def ai_provider_defaults(auth: AuthInfo = Depends(require_admin)):
    from app.services.ai import PROVIDER_DEFAULTS
    return ok({"defaults": PROVIDER_DEFAULTS})


# AI 审核提示词的内置默认（用于"恢复默认"按钮）
@router.get("/setting/ai-review/defaults")
async def ai_review_defaults(auth: AuthInfo = Depends(require_admin)):
    from app.services.ai_content import (
        DEFAULT_TAG_REVIEW_SYSTEM,
        DEFAULT_TAG_REVIEW_USER_TEMPLATE,
    )
    return ok({
        "tag_system_prompt": DEFAULT_TAG_REVIEW_SYSTEM,
        "tag_user_template": DEFAULT_TAG_REVIEW_USER_TEMPLATE,
    })


# AI 辅助生成审核 prompt（用户填业务描述 → AI 出 system + user 模板）
class GenerateReviewPromptDto(BaseModel):
    industry: str = Field(..., min_length=2, max_length=500)
    audience: str = Field(default="", max_length=500)


@router.post("/setting/ai-review/generate")
async def generate_review_prompt(
    dto: GenerateReviewPromptDto,
    auth: AuthInfo = Depends(require_admin),
):
    from app.services.ai_content import generate_review_prompt as _gen
    try:
        result = await _gen(dto.industry, dto.audience)
        return ok(result)
    except Exception as e:
        from app.utils.response import fail
        return fail(f"生成失败：{e}")


# AI 连接测试（读当前配置，调用 provider 检查连通性）
@router.post("/setting/ai/test")
async def ai_test_connection(auth: AuthInfo = Depends(require_admin)):
    from app.services.ai import test_connection
    ok = await test_connection()
    if ok:
        return ok(msg="AI 连接正常")
    return ok(msg="AI 服务返回异常，请检查配置", data={"ok": False})
