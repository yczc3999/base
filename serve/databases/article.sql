-- ============================================================
-- Base Framework — 文章 + 标签关系表
-- 通用内容模块（不含 SEO 自动发布扩展）
-- 完整 SEO 扩展见 seo.sql（如需安装）
-- ============================================================

-- ============================================================
-- articles
-- ============================================================
CREATE TABLE IF NOT EXISTS articles (
    id              SERIAL PRIMARY KEY,
    title           VARCHAR(200) NOT NULL,
    slug            VARCHAR(200) UNIQUE,
    summary         VARCHAR(500),
    excerpt         TEXT,
    content         TEXT NOT NULL,
    cover_image     VARCHAR(500),
    author_id       INTEGER,
    view_count      INTEGER NOT NULL DEFAULT 0,
    is_pinned       BOOLEAN NOT NULL DEFAULT FALSE,
    sort            INTEGER NOT NULL DEFAULT 0,
    source          SMALLINT NOT NULL DEFAULT 0,           -- 0=手动 1=采集
    source_url      VARCHAR(500),
    raw_content     TEXT,
    ai_processed    BOOLEAN NOT NULL DEFAULT FALSE,
    status          SMALLINT NOT NULL DEFAULT 0,           -- 0=草稿 1=已发布
    published_at    TIMESTAMP,
    -- SEO 模块字段（不装 SEO 也兼容，保持 NULL 即可）
    simhash         BIGINT,
    slug_history    JSONB NOT NULL DEFAULT '[]'::jsonb,    -- ["old-slug",...] 用于 301
    deleted_at      TIMESTAMP,                              -- 软删除（返 410）
    scheduled_at    TIMESTAMP,                              -- v2 SEO：发布计划时间
    retry_count     SMALLINT NOT NULL DEFAULT 0,
    last_publish_error TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_articles_slug   ON articles(slug);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_simhash
    ON articles(simhash) WHERE simhash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_articles_live
    ON articles(published_at DESC)
    WHERE status = 1 AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_articles_due_to_publish
    ON articles(scheduled_at)
    WHERE status = 0 AND scheduled_at IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_articles_drafts
    ON articles(created_at DESC)
    WHERE status = 0 AND deleted_at IS NULL;


-- ============================================================
-- article_tags 多对多关系（标签需配合 tag.sql 一起装）
-- ============================================================
CREATE TABLE IF NOT EXISTS article_tags (
    article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    -- tag_id 引用 tags 表（tag.sql 创建）；此处暂不加 FK 约束以支持独立装 article 模块
    tag_id      INTEGER NOT NULL,
    is_primary  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (article_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_article_tags_tag_time
    ON article_tags(tag_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_article_tags_primary
    ON article_tags(tag_id) WHERE is_primary = TRUE;
