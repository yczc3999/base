-- ============================================================
-- Base Framework — SEO 自动发布模块
-- 来自 gui-tu S1 同步（v2 简化版，无 publish_schedule 中间表）
--
-- 前置依赖：article.sql 已建（articles 含 simhash/scheduled_at 等字段）
--           tag.sql 已建（tags + article_tags 配套）
-- 文档: docs/SEO-策略基准.md
--
-- 本文件幂等：CREATE / ALTER / INSERT 都带 IF NOT EXISTS / ON CONFLICT
-- ============================================================


-- ============================================================
-- publish_log —— SEO 模块所有动作时间线
-- action: collect / rewrite / fingerprint / schedule / publish / skip
--         indexnow / sitemap_rebuild / phase_change / kill_switch
--         manual_approve / manual_cancel / error
-- ============================================================
CREATE TABLE IF NOT EXISTS publish_log (
    id          BIGSERIAL PRIMARY KEY,
    action      VARCHAR(32) NOT NULL,
    level       VARCHAR(8)  NOT NULL DEFAULT 'info',
    article_id  INTEGER,
    msg         TEXT,
    payload     JSONB,
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_publish_log_time
    ON publish_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_publish_log_action_time
    ON publish_log(action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_publish_log_article
    ON publish_log(article_id, created_at DESC) WHERE article_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_publish_log_error_recent
    ON publish_log(created_at DESC) WHERE level = 'error';


-- ============================================================
-- settings 种子（site 扩展 + seo 18 项 + seo_creds 3 项）
-- ============================================================
INSERT INTO settings (category, name, value, label) VALUES
    -- site 扩展
    ('site', 'frontend_url', '',                  '前端 URL'),
    ('site', 'launched_at',  '',                  '建站日期 (YYYY-MM-DD)'),
    ('site', 'timezone',     'Asia/Shanghai',     '时区'),

    -- seo —— 只读状态（worker 写）
    ('seo', 'enabled',           'false',  '总开关'),
    ('seo', 'current_phase',     'cold',   '当前阶段'),
    ('seo', 'phase_reason',      '',       '阶段判定依据'),
    ('seo', 'age_days',          '0',      '建站天数'),
    ('seo', 'published_cnt',     '0',      '已发布数'),
    ('seo', 'indexed_cnt',       '',       'Google 收录数'),
    ('seo', 'health_score',      '',       '索引健康度'),
    ('seo', 'last_phase_check',  '',       '上次阶段计算'),

    -- seo —— 质量闸（进阶模式可调）
    ('seo', 'quality_min_content_length',    '800',  '文章最少字数'),
    ('seo', 'quality_max_similarity_hamming','10',   'simhash 最大相似距离'),
    ('seo', 'quality_require_ai_processed',  'true', '必须 AI 润色'),
    ('seo', 'quality_require_cover_image',   'false','必须封面图'),
    ('seo', 'quality_max_tag_repeat_7d',     '2',    '同标签 7 天内最多篇数'),

    -- seo —— sitemap
    ('seo', 'sitemap_max_per_file', '5000', 'sitemap 单文件最大 URL 数'),
    ('seo', 'sitemap_refresh_min',  '60',   'sitemap 刷新间隔（分钟）'),

    -- seo_creds
    ('seo_creds', 'indexnow_key',             '', 'IndexNow Key (auto-gen)'),
    ('seo_creds', 'gsc_service_account_json', '', 'GSC Service Account JSON (Phase 2)'),
    ('seo_creds', 'bing_api_key',             '', 'Bing Webmaster API Key (Phase 2)')
ON CONFLICT (category, name) DO NOTHING;


-- ============================================================
-- 菜单 seed —— SEO 子页归"内容管理"，SEO 配置归"系统配置"(base 中是 id=5)
-- ============================================================

-- 先建"内容管理"父菜单（如果还没有）
INSERT INTO menus (parent_id, label, slug, icon, sort, type, is_visible, is_cache) VALUES
    (0, '内容管理', 'content', 'FileText', 5, 0, true, true)
ON CONFLICT (slug) DO NOTHING;

-- SEO 子菜单：用 slug='content' 的真实 id 作 parent
-- 注意 SEO 配置归"系统配置"(slug='setting' 的 id)
INSERT INTO menus (parent_id, label, slug, path, template_path, icon, sort, type, is_visible, is_cache)
SELECT m.id, 'SEO 总览', 'seo-dashboard', '/seo/dashboard', 'seo/dashboard', 'BarChart3', 20, 1, true, true
FROM menus m WHERE m.slug = 'content'
ON CONFLICT (slug) DO NOTHING;

INSERT INTO menus (parent_id, label, slug, path, template_path, icon, sort, type, is_visible, is_cache)
SELECT m.id, '发布日志', 'seo-log', '/seo/log', 'seo/log', 'FileText', 22, 1, true, true
FROM menus m WHERE m.slug = 'content'
ON CONFLICT (slug) DO NOTHING;

INSERT INTO menus (parent_id, label, slug, path, template_path, icon, sort, type, is_visible, is_cache)
SELECT m.id, 'sitemap 管理', 'seo-sitemap', '/seo/sitemap', 'seo/sitemap', 'Folder', 23, 1, true, true
FROM menus m WHERE m.slug = 'content'
ON CONFLICT (slug) DO NOTHING;

-- SEO 配置归系统配置
INSERT INTO menus (parent_id, label, slug, path, template_path, icon, sort, type, is_visible, is_cache)
SELECT m.id, 'SEO 配置', 'settings-seo', '/settings/seo', 'settings/seo/index', 'Settings', 91, 1, true, true
FROM menus m WHERE m.slug = 'setting'
ON CONFLICT (slug) DO NOTHING;
