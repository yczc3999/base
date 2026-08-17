-- ============================================================
-- Base Framework — 标签 + 搜索关键词
-- 通用内容能力，独立于任何下游项目
-- 标签 = SEO 关键词 = 着陆页（一表三角色）
-- ============================================================


-- ============================================================
-- tags
-- 状态机：
--   status=0 待审核 → status=1 上线（前台展示） / status=2 忽略
--   harvested=true 表示已用作种子被搜索引擎扩展过
-- ============================================================
CREATE TABLE IF NOT EXISTS tags (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(50) NOT NULL,
    slug            VARCHAR(50),
    color           VARCHAR(20),
    description     VARCHAR(200),
    source          SMALLINT NOT NULL DEFAULT 0,
    -- 0=手动 1=Google Suggest 2=Yandex 3=DuckDuckGo
    seed_keyword    VARCHAR(200),
    harvested       BOOLEAN NOT NULL DEFAULT FALSE,
    fetched_at      TIMESTAMP,
    article_count   INTEGER NOT NULL DEFAULT 0,
    sort            INTEGER NOT NULL DEFAULT 0,
    status          SMALLINT NOT NULL DEFAULT 0,
    -- 0=待审核 1=上线 2=忽略
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT tags_name_key UNIQUE (name),
    CONSTRAINT tags_slug_key UNIQUE (slug)
);

CREATE INDEX IF NOT EXISTS idx_tags_status ON tags(status);
CREATE INDEX IF NOT EXISTS idx_tags_harvested ON tags(harvested) WHERE harvested = FALSE;


-- ============================================================
-- search_keywords —— SEO 关键词候选池
-- 从 keyword_harvester 递归挖出来的原始词
-- 审核通过 (status=1) 后自动转 tag（创建对应 tags 记录）
-- ============================================================
CREATE TABLE IF NOT EXISTS search_keywords (
    id              SERIAL PRIMARY KEY,
    keyword         VARCHAR(200) NOT NULL,
    seed_keyword    VARCHAR(200),
    source          SMALLINT NOT NULL DEFAULT 0,
    tag_id          INTEGER,
    harvested       BOOLEAN NOT NULL DEFAULT FALSE,
    status          SMALLINT NOT NULL DEFAULT 0,
    -- 0=待审核 1=已上线（已绑 tag） 2=已忽略
    -- 以下 GSC/Bing 指标字段（Phase 2，可选）
    clicks          INTEGER NOT NULL DEFAULT 0,
    impressions     INTEGER NOT NULL DEFAULT 0,
    ctr             NUMERIC(5,4),
    position        NUMERIC(5,1),
    fetched_at      TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT search_keywords_keyword_key UNIQUE (keyword)
);

CREATE INDEX IF NOT EXISTS idx_search_keywords_status ON search_keywords(status);
CREATE INDEX IF NOT EXISTS idx_search_keywords_unharvested
    ON search_keywords(id) WHERE harvested = FALSE AND status != 2;
