"""SEO simhash 服务 —— 内容指纹 + 相似度查重

使用 64-bit simhash（Charikar 2002）。无外部依赖，纯 Python 实现。

策略基准 §3：hamming 距离 ≥ 阈值（默认 10）才算"足够独特"，否则 skip。
查询规模：库里最近 200 篇 vs 候选文，全 Python 比较即可（< 5ms）。
未来如规模超 10k，可建 simhash 索引（位分块）。

用法：
    from app.services.seo_simhash import seo_simhash_service
    sh = seo_simhash_service.compute(article_text)
    nearest = await seo_simhash_service.nearest_distance(db, sh, exclude_id=42)
    if nearest is not None and nearest < 10:
        skip()
"""
from __future__ import annotations

import hashlib
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")  # 英文 + CJK


def _tokenize(s: str) -> list[str]:
    r"""简易分词：英文按 \w 切，中文按字切（不引入 jieba 重依赖）。"""
    tokens: list[str] = []
    for m in _TOKEN_RE.finditer(s.lower()):
        w = m.group()
        if any("\u4e00" <= c <= "\u9fff" for c in w):
            # 中文按字切
            tokens.extend(w)
        else:
            tokens.append(w)
    return tokens


def _hash64(token: str) -> int:
    """稳定的 64-bit hash（不依赖 Python hash() 的随机化）。"""
    h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(h, "big", signed=False)


def simhash64(text_content: str) -> int:
    """Charikar simhash 64-bit。"""
    tokens = _tokenize(text_content)
    if not tokens:
        return 0
    v = [0] * 64
    for tok in tokens:
        h = _hash64(tok)
        for i in range(64):
            if h & (1 << i):
                v[i] += 1
            else:
                v[i] -= 1
    fp = 0
    for i in range(64):
        if v[i] > 0:
            fp |= (1 << i)
    return fp


def hamming(a: int, b: int) -> int:
    """两个 64-bit 整数的 hamming 距离。"""
    # PostgreSQL BIGINT 是 signed 64-bit，可能带负号；取无符号 64
    a &= 0xFFFFFFFFFFFFFFFF
    b &= 0xFFFFFFFFFFFFFFFF
    return bin(a ^ b).count("1")


# === 业务服务 ===

class SeoSimhashService:
    NEAREST_LOOKBACK = 200  # 比对范围：最近发布的 N 篇

    def compute(self, article_text: str) -> int:
        """对外稳定 API：算文章的 simhash。"""
        return simhash64(article_text)

    async def nearest_distance(
        self, db: AsyncSession, candidate_hash: int, exclude_id: int | None = None,
    ) -> int | None:
        """返回库里最近 N 篇中，距离 candidate 最近的 hamming 距离。
        库为空时返 None（视为"无重复"，由调用方决定怎么处理）。
        """
        if not candidate_hash:
            return None

        sql = (
            "SELECT id, simhash FROM articles "
            "WHERE simhash IS NOT NULL AND deleted_at IS NULL "
            + ("AND id != :ex " if exclude_id else "")
            + "ORDER BY id DESC LIMIT :n"
        )
        params: dict = {"n": self.NEAREST_LOOKBACK}
        if exclude_id:
            params["ex"] = exclude_id
        r = await db.execute(text(sql), params)
        rows = r.mappings().all()
        if not rows:
            return None

        return min(hamming(candidate_hash, int(row["simhash"])) for row in rows)

    async def update_for_article(
        self, db: AsyncSession, article_id: int, content: str,
    ) -> int:
        """计算并写回 articles.simhash。返回算出的 hash。"""
        sh = self.compute(content)
        # PostgreSQL BIGINT 是 signed；正确转换正数到 signed 表示
        signed = sh - (1 << 64) if sh >= (1 << 63) else sh
        await db.execute(
            text("UPDATE articles SET simhash = :h WHERE id = :id"),
            {"h": signed, "id": article_id},
        )
        return sh


seo_simhash_service = SeoSimhashService()
