-- 015: Keyword 模块重构 — 合并 tags / article_tags / search_keywords 为单表 keywords
--
-- 设计文档: serve/docs/keyword-refactor-design.md
--
-- 前置断言: 三张老表必须全空, 否则中止 (生产保护)
-- 变更: DROP 旧 3 表 → CREATE keywords + article_keywords
--      菜单 seed 更新 (content-tag → content-keyword)

BEGIN;

-- 1) 空表强断言
DO $$
BEGIN
    IF (SELECT COUNT(*) FROM tags) +
       (SELECT COUNT(*) FROM search_keywords) +
       (SELECT COUNT(*) FROM article_tags) <> 0
    THEN
        RAISE EXCEPTION 'legacy tag/search_keyword/article_tag tables not empty; refusing to drop';
    END IF;
END $$;

DROP TABLE IF EXISTS article_tags;
DROP TABLE IF EXISTS search_keywords;
DROP TABLE IF EXISTS tags;

-- 2) 新表
CREATE TABLE keywords (
    id              BIGSERIAL PRIMARY KEY,
    keyword         VARCHAR(200) NOT NULL,
    keyword_norm    VARCHAR(200) NOT NULL,
    slug            VARCHAR(200),

    stage           VARCHAR(16)  NOT NULL DEFAULT 'candidate',
    review_status   VARCHAR(16)  NOT NULL DEFAULT 'pending',

    source_code         VARCHAR(32)  NOT NULL,
    seed_keyword        VARCHAR(200),
    expanded_as_seed_at TIMESTAMP,
    fetched_at          TIMESTAMP    NOT NULL DEFAULT NOW(),

    metrics_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    ai_review_json  JSONB,

    color           VARCHAR(20),
    description     VARCHAR(200),
    sort            INTEGER NOT NULL DEFAULT 0,
    article_count   INTEGER NOT NULL DEFAULT 0,

    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_keywords_stage_review CHECK (
        (stage = 'candidate') OR
        (stage = 'approved' AND review_status IN ('human_approved', 'ai_approved')) OR
        (stage = 'archived' AND review_status IN ('human_rejected', 'ai_rejected'))
    )
);

CREATE UNIQUE INDEX uq_keywords_norm ON keywords(keyword_norm);
CREATE UNIQUE INDEX uq_keywords_slug ON keywords(slug) WHERE slug IS NOT NULL;
CREATE INDEX idx_keywords_stage ON keywords(stage);
CREATE INDEX idx_keywords_review ON keywords(review_status) WHERE stage = 'candidate';
CREATE INDEX idx_keywords_seed_unexpanded ON keywords(expanded_as_seed_at)
    WHERE expanded_as_seed_at IS NULL;
CREATE INDEX idx_keywords_source_time ON keywords(source_code, fetched_at DESC);

CREATE TABLE article_keywords (
    article_id  BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    keyword_id  BIGINT NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
    is_primary  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (article_id, keyword_id)
);
CREATE INDEX idx_ak_keyword_time ON article_keywords(keyword_id, created_at DESC);
CREATE INDEX idx_ak_primary ON article_keywords(keyword_id) WHERE is_primary = TRUE;

-- 3) 菜单: content-tag → content-keyword (幂等)
-- 3a) 如旧的 content-tag 存在且新的未存在, 就地改
UPDATE menus
   SET label = '关键词管理', path = '/content/keyword',
       template_path = 'content/keyword/index', slug = 'content-keyword'
 WHERE slug = 'content-tag'
   AND NOT EXISTS (SELECT 1 FROM menus WHERE slug = 'content-keyword');

-- 3b) 都不存在则新增 (依赖 content 父菜单)
INSERT INTO menus (parent_id, label, slug, path, template_path, icon, sort, type, is_visible, is_cache)
SELECT m.id, '关键词管理', 'content-keyword', '/content/keyword',
       'content/keyword/index', 'Search', 15, 1, true, true
  FROM menus m WHERE m.slug = 'content'
   AND NOT EXISTS (SELECT 1 FROM menus WHERE slug = 'content-keyword');

-- 3c) 残留清理
DELETE FROM menus WHERE slug = 'content-tag';

-- 3d) admin 角色绑新菜单
INSERT INTO role_menus (role_id, menu_id)
SELECT 1, id FROM menus WHERE slug = 'content-keyword'
ON CONFLICT DO NOTHING;

COMMIT;
