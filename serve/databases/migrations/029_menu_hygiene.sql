-- 029: 菜单卫生 — 清理无对应 Base 页面菜单树 + 补齐 Base 缺失的 content/seo/settings 菜单
--
-- 背景:
--   1. 本库菜单存在无对应 Base 页面实现的业务树 (440 / 707 / 300 /
--      400 / 480 五棵业务树), 其 template_path 在 Base 前端
--      views/ 下无对应文件 → 路由生成时被跳过 → 点击即 404。
--   2. base 自己的 content 菜单 (id=600 目录 + 601 article + 602 keyword) 因
--      id 600 被业务菜单占用从未生效 (016 用显式 id 600, 撞重复主键);
--      seo / settings 模块菜单则从未有任何种子。
--
-- 变更:
--   1. 递归删除 5 棵业务菜单树 + 级联清 role_menus (幂等: 不存在则删 0 行)
--   2. 建 content 目录 + article/keyword 页面 + 按钮权限点 (幂等)
--   3. 建 seo 目录 + dashboard/log/sitemap 页面 (幂等)
--   4. 建 settings 目录 + site/sms/storage/notify/payment/ai/seo-settings 页面 (幂等)
--   5. 新菜单绑定 role 1(admin) / 2(editor) (幂等)
--
-- 注意: migrate.py 的语句拆分不识别 DO $$ 块, 故全部用 SELECT WHERE NOT EXISTS
--       保证幂等, 不使用 DO / IF NOT EXISTS / BEGIN / COMMIT。

-- ---------- 1) 递归删除业务菜单树 (含 role_menus 绑定) ----------

DELETE FROM role_menus WHERE menu_id IN (
  WITH RECURSIVE tree AS (
    SELECT id FROM menus WHERE id IN (440, 707, 300, 400, 480)
    UNION ALL
    SELECT m.id FROM menus m JOIN tree t ON m.parent_id = t.id
  )
  SELECT id FROM tree
);

DELETE FROM menus WHERE id IN (
  WITH RECURSIVE tree AS (
    SELECT id FROM menus WHERE id IN (440, 707, 300, 400, 480)
    UNION ALL
    SELECT m.id FROM menus m JOIN tree t ON m.parent_id = t.id
  )
  SELECT id FROM tree
);

-- ---------- 2) content 目录 + article / keyword 页面 ----------

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 600, 0, 0, 'content', '内容管理', 'FolderOpen', NULL, NULL, 10, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 600);

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 601, 600, 1, 'content-article', '文章管理', 'FileText', '/content/article', 'content/article/index', 11, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 601);

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 602, 600, 1, 'content-keyword', '关键词管理', 'Search', '/content/keyword', 'content/keyword/index', 12, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 602);

-- article 按钮权限点 (CrudTable perms="admin:article" 派生 list/create/edit/delete/export)
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 601, 2, 'article-list',   '查看文章', 'admin:article:list',   1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:article:list');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 601, 2, 'article-create', '新增文章', 'admin:article:create', 2
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:article:create');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 601, 2, 'article-edit',   '编辑文章', 'admin:article:edit',   3
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:article:edit');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 601, 2, 'article-delete', '删除文章', 'admin:article:delete', 4
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:article:delete');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 601, 2, 'article-export', '导出文章', 'admin:article:export', 5
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:article:export');

-- keyword 按钮权限点
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 602, 2, 'keyword-list',   '查看关键词', 'admin:keyword:list',   1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:keyword:list');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 602, 2, 'keyword-create', '新增关键词', 'admin:keyword:create', 2
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:keyword:create');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 602, 2, 'keyword-edit',   '编辑关键词', 'admin:keyword:edit',   3
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:keyword:edit');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 602, 2, 'keyword-delete', '删除关键词', 'admin:keyword:delete', 4
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:keyword:delete');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 602, 2, 'keyword-export', '导出关键词', 'admin:keyword:export', 5
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:keyword:export');

-- ---------- 3) seo 目录 + dashboard / log / sitemap 页面 ----------

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 500, 0, 0, 'seo', 'SEO 优化', 'DataLine', NULL, NULL, 11, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 500);

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 501, 500, 1, 'seo-dashboard', 'SEO 概览', 'TrendCharts', '/seo/dashboard', 'seo/dashboard', 1, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 501);

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 502, 500, 1, 'seo-log', '发布日志', 'Document', '/seo/log', 'seo/log', 2, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 502);

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 503, 500, 1, 'seo-sitemap', '站点地图', 'MapLocation', '/seo/sitemap', 'seo/sitemap', 3, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 503);

-- ---------- 4) settings 目录 + 服务商配置页面 ----------

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 510, 0, 0, 'settings', '系统设置', 'Setting', NULL, NULL, 12, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 510);

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 511, 510, 1, 'settings-site',    '站点设置', 'Monitor',    '/settings/site',    'settings/site/index',    1, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 511);
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 512, 510, 1, 'settings-sms',     '短信服务', 'ChatDotRound', '/settings/sms',    'settings/sms/index',     2, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 512);
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 513, 510, 1, 'settings-storage', '存储服务', 'FolderOpened', '/settings/storage', 'settings/storage/index', 3, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 513);
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 514, 510, 1, 'settings-notify',  '通知服务', 'Bell',       '/settings/notify',  'settings/notify/index',  4, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 514);
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 515, 510, 1, 'settings-payment', '支付服务', 'CreditCard', '/settings/payment', 'settings/payment/index', 5, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 515);
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 516, 510, 1, 'settings-ai',      'AI 配置',  'MagicStick', '/settings/ai',      'settings/ai/index',       6, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 516);
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 517, 510, 1, 'settings-seo',     'SEO 配置', 'Search',     '/settings/seo',     'settings/seo/index',      7, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 517);

-- ---------- 5) 新菜单绑定 role 1(admin) / 2(editor) ----------

INSERT INTO role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM roles r CROSS JOIN menus m
WHERE r.id IN (1, 2)
  AND m.id IN (600, 601, 602, 500, 501, 502, 503, 510, 511, 512, 513, 514, 515, 516, 517)
  AND NOT EXISTS (SELECT 1 FROM role_menus rm WHERE rm.role_id = r.id AND rm.menu_id = m.id);
