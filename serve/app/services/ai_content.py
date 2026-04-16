"""AI 内容生成 — 所有 prompt 模板和业务调用集中在此

Controller 只传参数，不拼 prompt。
"""

from __future__ import annotations

import re
import logging

from app.services import ai as ai_service

logger = logging.getLogger(__name__)

"""默认 prompt 是通用占位，建议每个项目在
settings.ai.system_editor / system_rewrite 里覆盖为自己的业务设定。
"""
SYSTEM_EDITOR = (
    "你是 SEO 内容编辑。\n"
    "直接给方案，像博主写攻略，Markdown 格式。"
)

SYSTEM_REWRITE = "你是 SEO 内容编辑。重构时像老司机分享经验。"


# ---- 文章相关 ----

async def gen_title(content: str) -> str:
    if not content:
        return ""
    result = await ai_service.chat(
        f"根据以下内容，生成一个吸引人的中文标题（15-30字），只返回标题：\n\n{content[:1500]}",
        system=SYSTEM_EDITOR
    )
    return result.strip().strip('"\'')


async def gen_slug(title: str) -> str:
    if not title:
        return ""
    result = await ai_service.chat(
        f"为这个标题生成英文 URL slug（全小写、连字符、5-8 词）：\n{title}\n只返回 slug。",
        system="只输出 slug，不要解释。"
    )
    return re.sub(r'[^a-z0-9-]', '', result.strip().lower())


async def gen_summary(content: str) -> str:
    if not content:
        return ""
    result = await ai_service.chat(
        f"用 2-3 句话概括以下内容（50-100字）：\n\n{content[:2000]}",
        system=SYSTEM_EDITOR
    )
    return result.strip()


async def gen_article(keyword: str) -> str:
    return await ai_service.chat(
        f"围绕「{keyword}」写一篇 1200-2000 字的实用教程。\n\n"
        f"要求：\n"
        f"- 第一行是吸引人的标题（不加#号）\n"
        f"- Markdown 格式，4-6 个 ## 小标题\n"
        f"- 有真实场景（留学生在宿舍、华人过年想看春晚等）\n"
        f"- 有具体操作步骤\n"
        f"- 自然提到回国加速/VPN 作为解决方案\n"
        f"- 结尾有总结 + 行动引导\n"
        f"- 像朋友分享经验，不要客服腔",
        system=SYSTEM_EDITOR
    )


async def chat(content: str, prior_content: str = "") -> str:
    """带可选上次生成内容作为上下文的 chat。prior_content 超长自动截断。"""
    if not prior_content:
        return await ai_service.chat(content, system=SYSTEM_EDITOR)
    snippet = prior_content.strip()[:3000]
    prompt = f"{content}\n\n上次生成的内容：\n{snippet}"
    return await ai_service.chat(prompt, system=SYSTEM_EDITOR)


async def rewrite_article(original: str) -> dict:
    """完整润色一篇文章，返回 {title, content, summary, slug}"""
    rewritten = await ai_service.chat(
        f"深度重构以下文章：\n"
        f"1. 完全重写，用自己的话\n"
        f"2. 自然专业的中文\n"
        f"3. 保留核心知识点\n"
        f"4. Markdown 格式\n"
        f"5. 字数不少于原文 80%\n\n"
        f"原文（{len(original)}字）：\n{original[:5000]}",
        system=SYSTEM_REWRITE
    )

    title = await gen_title(rewritten[:1000])
    summary = await gen_summary(rewritten[:1500])
    slug = await gen_slug(title)

    return {
        "title": title,
        "content": rewritten.strip(),
        "summary": summary,
        "slug": slug,
        "word_count": len(rewritten),
    }


# ---- 标签相关 ----

async def gen_seeds(topic: str, count: int) -> list[str]:
    try:
        text = await ai_service.chat(
            f"为以下业务生成 {count} 个长尾搜索关键词，中英文都要。"
            f"每行一个，侧重'怎么做/推荐/哪个好/教程'类，只输出列表。\n\n业务：{topic}",
            system="你是 SEO 专家。"
        )
        return [line.strip() for line in text.strip().split("\n") if line.strip()][:count]
    except Exception as e:
        logger.warning("AI seeds failed: %s", e)
        return _fallback_seeds(count)


DEFAULT_TAG_REVIEW_SYSTEM = "你是 SEO 关键词严审员，只返回 JSON。宁严勿宽。"

# ⚠️ base 框架默认是通用骨架。具体业务必须在
# settings.ai_review.tag_user_template 配置。
# 前端 /content/tag 页面的"审核规则"按钮 + AI 辅助生成可帮用户填。
DEFAULT_TAG_REVIEW_USER_TEMPLATE = (
    "你是 SEO 关键词审核员。\n"
    "⚠️ 注意：当前是【base 框架默认骨架】，未配置具体业务，AI 只能做粗糙判定。\n"
    "请到后台『内容管理 → 标签 → 审核规则』按钮，描述你的业务让 AI 生成专属规则。\n\n"
    "默认判定（兜底逻辑）：\n"
    "✅ approve：明确是 SEO 友好的中长尾关键词（5-30 字，有具体意图，能做落地页）\n"
    "❌ reject：URL、新闻标题整句、纯品牌名、过度宽泛词（如『视频』、『VPN』）、\n"
    "        明显违规/敏感内容、与任何业务都无关的话题\n"
    "⚠️ uncertain：其他情况（默认偏保守）\n\n"
    "待审关键词：\n"
    "{keywords}\n\n"
    '只返回 JSON 数组，每项 {"keyword":"...","decision":"approve|reject|uncertain"}。'
)


async def generate_review_prompt(industry: str, audience: str = "") -> dict:
    """根据业务描述，让 AI 生成 system + user 模板的初稿。
    返回 {"system": ..., "user_template": ...}（user_template 含 {keywords} 占位符）。
    """
    meta_prompt = (
        f"你是一个 SEO 专家。请为以下业务设计一套\"关键词审核\"AI prompt 模板，"
        f"用于 AI 自动判定一批关键词是否值得做着陆页。\n\n"
        f"业务描述：{industry}\n"
        + (f"目标受众：{audience}\n" if audience else "")
        + "\n请输出 JSON 格式（不要其他说明）：\n"
        '{\n'
        '  "system": "AI 身份和约束（短，1-2 行）",\n'
        '  "user_template": "完整审核规则模板，必须包含 {keywords} 占位符，结尾要求 AI 只返回 JSON 数组 [{\\"keyword\\":\\"...\\",\\"decision\\":\\"approve|reject|uncertain\\"}]"\n'
        '}\n\n'
        "user_template 必须包含：\n"
        "1. 业务方向和目标用户的精确描述\n"
        "2. 至少 3 个 approve 范例 + 3 个 reject 范例\n"
        "3. 默认宁严勿宽的指令\n"
        "4. {keywords} 占位符（后端注入待审词列表）\n"
        "5. 输出 JSON 格式约定"
    )
    resp = await ai_service.chat(meta_prompt, system="你是 SEO 严审 prompt 设计师，只返回 JSON。")
    import json as _json
    import re as _re
    m = _re.search(r'\{.*\}', resp, _re.DOTALL)
    if not m:
        raise ValueError("AI 未返回有效 JSON")
    data = _json.loads(m.group())
    if "{keywords}" not in (data.get("user_template") or ""):
        raise ValueError("AI 生成的模板缺 {keywords} 占位符")
    return {
        "system": data.get("system", "").strip(),
        "user_template": data.get("user_template", "").strip(),
    }


async def _load_review_prompts() -> tuple[str, str]:
    """从 settings 读审核提示词，空/缺失 → 用内置默认。

    category = ai_review
    - tag_system_prompt
    - tag_user_template  （必须含 {keywords} 占位符，否则回退默认）
    """
    from app.services.database import async_session
    from app.logics.setting import setting_logic

    async with async_session() as db:
        sys_p = await setting_logic.get(db, "ai_review", "tag_system_prompt", "")
        usr_t = await setting_logic.get(db, "ai_review", "tag_user_template", "")

    system = sys_p.strip() if sys_p and sys_p.strip() else DEFAULT_TAG_REVIEW_SYSTEM
    # 模板必须能注入关键词；否则硬回退默认，避免误配置把审核玩坏
    template = usr_t if usr_t and "{keywords}" in usr_t else DEFAULT_TAG_REVIEW_USER_TEMPLATE
    return system, template


async def review_tags(keywords: list[str]) -> list[dict]:
    """审核标签是否和业务相关，返回 [{keyword, decision}, ...]

    prompt 从 settings.ai_review 读取（不同项目可配不同目标），缺失时用内置默认。
    """
    import json as json_mod
    import re as re_mod

    system, template = await _load_review_prompts()
    kw_block = "\n".join(f"- {kw}" for kw in keywords)
    prompt = template.replace("{keywords}", kw_block)

    resp_text = await ai_service.chat(prompt, system=system)
    match = re_mod.search(r'\[.*\]', resp_text, re_mod.DOTALL)
    if not match:
        return []
    return json_mod.loads(match.group())


async def test_connection() -> str:
    return await ai_service.chat("回复'OK'两个字即可。")


# ---- 标签生成文章 ----

async def gen_article_for_tag(tag_name: str) -> dict:
    """围绕标签生成文章，返回 {title, content, summary, slug}"""
    content = await ai_service.chat(
        f"围绕「{tag_name}」写一篇 800-1500 字的实用教程/攻略。\n\n"
        f"要求：\n"
        f"- 第一行是文章标题（不加 # 号）\n"
        f"- Markdown 格式，有 2-4 个小标题\n"
        f"- 实用、有干货、有具体操作步骤\n"
        f"- 适当提到回国加速/VPN 解决方案",
        system=SYSTEM_EDITOR
    )

    lines = content.strip().split('\n')
    title = lines[0].strip().lstrip('#').strip()
    body = '\n'.join(lines[1:]).strip()

    slug = await gen_slug(title)
    summary = await gen_summary(body[:1500])

    return {"title": title[:200], "content": body, "summary": summary, "slug": slug}


def _fallback_seeds(count: int) -> list[str]:
    """AI 生成种子词失败时的兜底 —— base 框架仅提供占位说明。
    项目应在 settings.ai.seed_topic 配置自己的业务，让 gen_seeds 用 AI 生成。
    """
    return [f"种子词占位 {i+1}（请到 AI 配置填业务描述让 AI 生成）" for i in range(count)]
