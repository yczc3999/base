-- ============================================================
-- 016: 补齐 content 目录 + article/file/keyword/seo 菜单与权限点种子
--
-- 背景: article.sql / seo.sql / 015 引用 content 父菜单(id=600) 与
--       content-keyword, 但 init.sql + 012 只种了 system 系列菜单,
--       content 目录及文章/关键词/SEO 的按钮权限点从未创建,
--       导致非超管在这些模块整体 403 (G-R4) + 菜单悬空 (G6)。
--
-- 变更:
--   1. 创建 content 目录(id=600) + 子菜单 article(601)/keyword(602)/file(603)
--   2. 为每个模块补 type=2 按钮权限点 (list/detail/create/edit/delete/export)
--   3. admin 角色(role_id=1) 绑定全部新菜单与权限点
--   4. 幂等: 已存在则跳过
-- ============================================================

BEGIN;

-- ---------- 1) content 目录 + 子菜单 ----------

INSERT INTO menus (id, parent_id, type, slug, label, icon, sort, is_visible, is_cache)
SELECT 600, 0, 0, 'content', '内容管理', 'FolderOpen', 10, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 600);

-- 文章管理
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 601, 600, 1, 'content-article', '文章管理', 'FileText', '/content/article', 'content/article/index', 11, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 601);

-- 关键词管理 (若 015 已建则不动)
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 602, 600, 1, 'content-keyword', '关键词管理', 'Search', '/content/keyword', 'content/keyword/index', 12, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE slug = 'content-keyword');

-- 文件管理 (FileManager 挂载点)
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 603, 600, 1, 'content-file', '文件管理', 'Folder', '/content/file', 'content/file/index', 13, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 603);

-- ---------- 2) 按钮权限点 (type=2) ----------

-- 文章: admin:article:list/detail/create/edit/delete/export
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 601, 2, 'article-list',   '查看文章', 'admin:article:list',   1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:article:list');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 601, 2, 'article-detail', '查看详情', 'admin:article:detail', 2
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:article:detail');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 601, 2, 'article-create', '新增文章', 'admin:article:create', 3
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:article:create');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 601, 2, 'article-edit',   '编辑文章', 'admin:article:edit',   4
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:article:edit');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 601, 2, 'article-delete', '删除文章', 'admin:article:delete', 5
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:article:delete');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 601, 2, 'article-export', '导出文章', 'admin:article:export', 6
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:article:export');

-- 关键词: admin:keyword:list/detail/create/edit/delete/export
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 602, 2, 'keyword-list',   '查看关键词', 'admin:keyword:list',   1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:keyword:list');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 602, 2, 'keyword-detail', '查看详情', 'admin:keyword:detail', 2
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:keyword:detail');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 602, 2, 'keyword-create', '新增关键词', 'admin:keyword:create', 3
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:keyword:create');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 602, 2, 'keyword-edit',   '编辑关键词', 'admin:keyword:edit',   4
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:keyword:edit');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 602, 2, 'keyword-delete', '删除关键词', 'admin:keyword:delete', 5
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:keyword:delete');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 602, 2, 'keyword-export', '导出关键词', 'admin:keyword:export', 6
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:keyword:export');

-- 文件: admin:file:list/detail/create/edit/delete/export
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 603, 2, 'file-list',   '查看文件', 'admin:file:list',   1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:file:list');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 603, 2, 'file-detail', '查看详情', 'admin:file:detail', 2
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:file:detail');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 603, 2, 'file-create', '上传文件', 'admin:file:create', 3
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:file:create');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 603, 2, 'file-edit',   '编辑文件', 'admin:file:edit',   4
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:file:edit');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 603, 2, 'file-delete', '删除文件', 'admin:file:delete', 5
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:file:delete');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 603, 2, 'file-export', '导出文件', 'admin:file:export', 6
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:file:export');

-- SEO: admin:seo:list/detail/create/edit/delete/export (挂在 SEO 总览 801 下)
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 801, 2, 'seo-list',   '查看SEO', 'admin:seo:list',   1
WHERE EXISTS (SELECT 1 FROM menus WHERE id = 801)
  AND NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:seo:list');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 801, 2, 'seo-detail', '查看详情', 'admin:seo:detail', 2
WHERE EXISTS (SELECT 1 FROM menus WHERE id = 801)
  AND NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:seo:detail');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 801, 2, 'seo-create', '新增', 'admin:seo:create', 3
WHERE EXISTS (SELECT 1 FROM menus WHERE id = 801)
  AND NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:seo:create');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 801, 2, 'seo-edit',   '编辑', 'admin:seo:edit',   4
WHERE EXISTS (SELECT 1 FROM menus WHERE id = 801)
  AND NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:seo:edit');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 801, 2, 'seo-delete', '删除', 'admin:seo:delete', 5
WHERE EXISTS (SELECT 1 FROM menus WHERE id = 801)
  AND NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:seo:delete');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 801, 2, 'seo-export', '导出', 'admin:seo:export', 6
WHERE EXISTS (SELECT 1 FROM menus WHERE id = 801)
  AND NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:seo:export');

-- ---------- 3) admin 角色绑定全部新菜单 ----------

INSERT INTO role_menus (role_id, menu_id)
SELECT 1, id FROM menus
WHERE id >= 600
  AND NOT EXISTS (SELECT 1 FROM role_menus WHERE role_id = 1 AND menu_id = menus.id);

-- ---------- 4) 更新内容目录下已存在菜单的父节点 (015 建的 content-keyword 若挂错则纠正) ----------

UPDATE menus SET parent_id = 600 WHERE slug = 'content-keyword' AND parent_id != 600;

COMMIT;
