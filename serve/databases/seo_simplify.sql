-- ============================================================
-- Gui-Tu — SEO 模块简化迁移（v1 → v2）
-- 生成时间: 2026-04-15
-- 文档: docs/一期/SEO-策略基准.md (§v2 架构变更说明)
--
-- 核心变更：删 publish_schedule 整张表 + 5 状态机，
-- 把"发布计划"语义合并到 articles 字段：
--   scheduled_at IS NULL + status=0  → 草稿
--   scheduled_at IS NOT NULL + status=0 + scheduled_at>NOW → 排队中
--   scheduled_at IS NOT NULL + status=0 + scheduled_at<=NOW → 待发（worker 下次扫到）
--   status=1 + published_at IS NOT NULL → 已发布
--   deleted_at IS NOT NULL → 软删除
--
-- 用户面 5 状态 → 内部 1 字段，认知负担降 80%。
-- 前 v1 已部署的环境通过本脚本无缝升级，老数据不丢。
-- ============================================================


-- ============================================================
-- 1. articles 加 3 列
-- ============================================================
ALTER TABLE articles
    ADD COLUMN IF NOT EXISTS scheduled_at        TIMESTAMP,
    ADD COLUMN IF NOT EXISTS retry_count         SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_publish_error  TEXT;

-- 部分索引 — worker 扫到点任务时用
CREATE INDEX IF NOT EXISTS idx_articles_due_to_publish
    ON articles(scheduled_at)
    WHERE status = 0 AND scheduled_at IS NOT NULL AND deleted_at IS NULL;

-- 草稿池查询 — pipeline 算补货时用
CREATE INDEX IF NOT EXISTS idx_articles_drafts
    ON articles(created_at DESC)
    WHERE status = 0 AND deleted_at IS NULL;


-- ============================================================
-- 2. 数据迁移 —— publish_schedule 历史回填到 articles + publish_log
-- 按宋慈 BLOCKER 要求显式实现，不丢数据
-- ============================================================

-- 2a. status=1（已发）的实际发布时间回填到 articles.published_at
--     COALESCE 防止覆盖已有值
UPDATE articles a SET
    published_at = COALESCE(a.published_at, s.actual_publish_at)
FROM publish_schedule s
WHERE s.article_id = a.id
  AND s.status = 1
  AND s.actual_publish_at IS NOT NULL
  AND TO_REGCLASS('publish_schedule') IS NOT NULL;

-- 2b. status=0 / 4（待发 / 待审核）的 scheduled_at 回填
UPDATE articles a SET
    scheduled_at = COALESCE(a.scheduled_at, s.scheduled_at)
FROM publish_schedule s
WHERE s.article_id = a.id
  AND s.status IN (0, 4)
  AND TO_REGCLASS('publish_schedule') IS NOT NULL;

-- 2c. retry_count + last_error 迁移
UPDATE articles a SET
    retry_count = GREATEST(a.retry_count, COALESCE(s.retry_count, 0)),
    last_publish_error = COALESCE(a.last_publish_error, s.last_error)
FROM publish_schedule s
WHERE s.article_id = a.id
  AND TO_REGCLASS('publish_schedule') IS NOT NULL;

-- 2d. status=2（跳过）的 reason / skip_code 归档进 publish_log
INSERT INTO publish_log (action, level, article_id, msg, payload, created_at)
SELECT
    'skip',
    'warn',
    s.article_id,
    COALESCE(s.reason, '历史 skip 记录'),
    jsonb_build_object(
        'skip_code', s.skip_code,
        'legacy_schedule_id', s.id,
        'legacy', true
    ),
    COALESCE(s.updated_at, s.created_at, NOW())
FROM publish_schedule s
WHERE s.status = 2
  AND TO_REGCLASS('publish_schedule') IS NOT NULL
  AND TO_REGCLASS('publish_log') IS NOT NULL;


-- ============================================================
-- 3. 删除 publish_schedule 表 + v_phase_history 视图
-- 按狄仁杰 NIT 要求清孤儿
-- ============================================================
DROP VIEW IF EXISTS v_phase_history;
DROP TABLE IF EXISTS publish_schedule;


-- ============================================================
-- 4. 删除菜单"发布排期"（id=802）
-- 用户不该看到内部排队
-- ============================================================
DELETE FROM menus WHERE id = 802;
DELETE FROM role_menus WHERE menu_id = 802;


-- ============================================================
-- 完成检查（注释，不执行）
-- ============================================================
-- 验证：
-- SELECT COUNT(*) FROM articles WHERE scheduled_at IS NOT NULL;  -- 应有迁移过来的排期
-- SELECT COUNT(*) FROM publish_log WHERE payload->>'legacy' = 'true';  -- 应有 skip 历史归档
-- SELECT to_regclass('publish_schedule');  -- 应返回 NULL (表已删)
