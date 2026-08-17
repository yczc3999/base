-- ============================================================
-- Base SEO 模块（在基础内容表之后执行）
-- 生成时间: 2026-04-15
-- 文档: docs/一期/SEO-策略基准.md
--
-- 本文件幂等：所有 CREATE / ALTER / INSERT 都带 IF NOT EXISTS / ON CONFLICT
-- 可重复执行不会破坏现有数据
-- ============================================================


-- ============================================================
-- 1. articles 扩展 —— SEO 所需列
-- ============================================================
ALTER TABLE articles
    ADD COLUMN IF NOT EXISTS simhash         BIGINT,
    ADD COLUMN IF NOT EXISTS slug_history    JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS deleted_at      TIMESTAMP,
    ADD COLUMN IF NOT EXISTS raw_content     TEXT,
    ADD COLUMN IF NOT EXISTS source          SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS source_url      VARCHAR(500),
    ADD COLUMN IF NOT EXISTS ai_processed    BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_articles_simhash
    ON articles(simhash) WHERE simhash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_articles_live
    ON articles(published_at DESC)
    WHERE status = 1 AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_articles_ready_for_schedule
    ON articles(id)
    WHERE status = 0 AND ai_processed = TRUE AND deleted_at IS NULL;


-- ============================================================
-- 2. article_tags —— 文章与标签多对多关系（tag cluster 架构基础）
-- ============================================================
CREATE TABLE IF NOT EXISTS article_tags (
    article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id)     ON DELETE CASCADE,
    is_primary  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (article_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_article_tags_tag_time
    ON article_tags(tag_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_article_tags_primary
    ON article_tags(tag_id) WHERE is_primary = TRUE;


-- ============================================================
-- 3. publish_schedule —— 发布排期
--    status: 0=pending 1=published 2=skipped 3=canceled 4=needs_review
-- ============================================================
CREATE TABLE IF NOT EXISTS publish_schedule (
    id                 SERIAL PRIMARY KEY,
    article_id         INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    scheduled_at       TIMESTAMP NOT NULL,
    status             SMALLINT NOT NULL DEFAULT 0,
    skip_code          VARCHAR(32),
    reason             TEXT,
    actual_publish_at  TIMESTAMP,
    retry_count        SMALLINT NOT NULL DEFAULT 0,
    last_error         TEXT,
    -- 乐观锁：worker 处理前占用，处理完清空
    locked_at          TIMESTAMP,
    locked_by          VARCHAR(64),
    -- stable 阶段 10% 随机采样进人工抽查
    sampled_for_review BOOLEAN NOT NULL DEFAULT FALSE,
    reviewed_at        TIMESTAMP,
    reviewed_by        INTEGER,
    created_at         TIMESTAMP NOT NULL DEFAULT now(),
    updated_at         TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_schedule_due
    ON publish_schedule(scheduled_at)
    WHERE status = 0;

-- 同一篇文章同时只能有一个 pending（status=0），防 race 双排
CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_pending_per_article
    ON publish_schedule(article_id)
    WHERE status = 0;

CREATE INDEX IF NOT EXISTS idx_schedule_article
    ON publish_schedule(article_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_schedule_status_time
    ON publish_schedule(status, scheduled_at DESC);

CREATE INDEX IF NOT EXISTS idx_schedule_review_queue
    ON publish_schedule(created_at DESC)
    WHERE status = 4;

CREATE INDEX IF NOT EXISTS idx_schedule_sample_pending
    ON publish_schedule(actual_publish_at DESC)
    WHERE sampled_for_review = TRUE AND reviewed_at IS NULL;


-- ============================================================
-- 4. publish_log —— SEO 模块所有动作时间线
--    action: collect / rewrite / fingerprint / schedule / publish / skip
--            indexnow / sitemap_rebuild / phase_change / kill_switch
--            manual_approve / manual_cancel / error
-- ============================================================
CREATE TABLE IF NOT EXISTS publish_log (
    id          BIGSERIAL PRIMARY KEY,
    action      VARCHAR(32) NOT NULL,
    level       VARCHAR(8)  NOT NULL DEFAULT 'info',   -- info / warn / error
    article_id  INTEGER,
    msg         TEXT,
    payload     JSONB,
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_log_time
    ON publish_log(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_log_action_time
    ON publish_log(action, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_log_article
    ON publish_log(article_id, created_at DESC)
    WHERE article_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_log_error_recent
    ON publish_log(created_at DESC)
    WHERE level = 'error';


-- ============================================================
-- 5. 视图：阶段切换历史（查询便利，不建实体表）
-- ============================================================
CREATE OR REPLACE VIEW v_phase_history AS
SELECT
    id,
    created_at,
    payload ->> 'from'   AS from_phase,
    payload ->> 'to'     AS to_phase,
    payload ->> 'reason' AS reason,
    msg
FROM publish_log
WHERE action = 'phase_change'
ORDER BY created_at DESC;


-- ============================================================
-- 6. settings 种子
--    site 扩展 3 项；seo 新增 18 项；seo_creds 新增 3 项
-- ============================================================
INSERT INTO settings (category, name, value, label) VALUES
    -- site 扩展
    ('site', 'frontend_url', '',                  '前端 URL'),
    ('site', 'launched_at',  '',                  '建站日期 (YYYY-MM-DD)'),
    ('site', 'timezone',     'Asia/Shanghai',     '时区'),

    -- seo 分类 — 只读状态（由 worker 写）
    ('seo', 'enabled',           'false',  '总开关'),
    ('seo', 'current_phase',     'cold',   '当前阶段'),
    ('seo', 'phase_reason',      '',       '阶段判定依据'),
    ('seo', 'age_days',          '0',      '建站天数'),
    ('seo', 'published_cnt',     '0',      '已发布数'),
    ('seo', 'indexed_cnt',       '',       'Google 收录数'),
    ('seo', 'health_score',      '',       '索引健康度'),
    ('seo', 'last_phase_check',  '',       '上次阶段计算'),

    -- seo 分类 — 质量闸（进阶模式可调）
    ('seo', 'quality_min_content_length',    '800',  '文章最少字数'),
    ('seo', 'quality_max_similarity_hamming','10',   'simhash 最大相似距离'),
    ('seo', 'quality_require_ai_processed',  'true', '必须 AI 润色'),
    ('seo', 'quality_require_cover_image',   'false','必须封面图'),
    ('seo', 'quality_max_tag_repeat_7d',     '2',    '同标签 7 天内最多篇数'),

    -- seo 分类 — sitemap
    ('seo', 'sitemap_max_per_file', '5000', 'sitemap 单文件最大 URL 数'),
    ('seo', 'sitemap_refresh_min',  '60',   'sitemap 刷新间隔（分钟）'),

    -- seo_creds 分类
    ('seo_creds', 'indexnow_key',             '', 'IndexNow Key (auto-gen)'),
    ('seo_creds', 'gsc_service_account_json', '', 'GSC Service Account JSON (Phase 2)'),
    ('seo_creds', 'bing_api_key',             '', 'Bing Webmaster API Key (Phase 2)')
ON CONFLICT (category, name) DO NOTHING;


-- ============================================================
-- 7. 菜单 seed —— SEO 中心（顶层 1 个 + 子页 4 个）+ 系统管理下 SEO 配置
-- ============================================================
-- SEO 4 子页归入"内容管理"(id=600) 下；SEO 配置归"系统管理"(id=1)
-- icon 名必须在 admin/src/layouts/default/Sidebar.vue iconMap 里有映射
INSERT INTO menus (id, parent_id, label, slug, path, template_path, icon, sort, type, is_visible, is_cache) VALUES
    (801, 600, 'SEO 总览',     'seo-dashboard', '/seo/dashboard', 'seo/dashboard',      'BarChart3',  20, 1, true, true),
    (802, 600, '发布排期',     'seo-schedule',  '/seo/schedule',  'seo/schedule',       'Activity',   21, 1, true, true),
    (803, 600, '发布日志',     'seo-log',       '/seo/log',       'seo/log',            'FileText',   22, 1, true, true),
    (804, 600, 'sitemap 管理', 'seo-sitemap',   '/seo/sitemap',   'seo/sitemap',        'Folder',     23, 1, true, true),
    (706, 1,   'SEO 配置',     'settings-seo',  '/settings/seo',  'settings/seo/index', 'Settings',   91, 1, true, true)
ON CONFLICT (id) DO NOTHING;

-- 幂等 UPDATE：icon 或 parent 需修正时（v1→v2 升级路径）
UPDATE menus SET parent_id = 600, sort = 20, icon = 'BarChart3' WHERE id = 801 AND (parent_id != 600 OR icon != 'BarChart3');
UPDATE menus SET parent_id = 600, sort = 21, icon = 'Activity'  WHERE id = 802 AND (parent_id != 600 OR icon != 'Activity');
UPDATE menus SET parent_id = 600, sort = 22, icon = 'FileText'  WHERE id = 803 AND (parent_id != 600 OR icon != 'FileText');
UPDATE menus SET parent_id = 600, sort = 23, icon = 'Folder'    WHERE id = 804 AND (parent_id != 600 OR icon != 'Folder');
UPDATE menus SET icon = 'Settings' WHERE id = 706 AND icon NOT IN ('Settings');

-- AI 配置菜单补 icon（id=705 是 Phase 6bd682f commit 加的）
UPDATE menus SET icon = 'Sliders' WHERE id = 705 AND (icon IS NULL OR icon NOT IN ('Sliders'));

-- 删除老的"SEO 中心"顶层（v1 → v2 升级；v1 安装时不存在所以 DELETE 无伤）
DELETE FROM menus WHERE id = 800;
