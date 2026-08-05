-- ============================================================
-- 026: 在线会话 + 缓存管理 — session/cache 菜单 + 权限点
--
-- 变更:
--   1. 系统管理(1) 下挂「在线会话」(136) + 「缓存管理」(137)
--   2. 权限点 admin:session:list/kick, admin:cache:stats/clear
--   3. admin 角色绑定
-- ============================================================

BEGIN;

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 136, 1, 1, 'session', '在线会话', 'Connection', '/system/session', 'system/session/index', 12, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 136);

INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 136, 2, 'session-list', '查看会话', 'admin:session:list', 1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:session:list');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 136, 2, 'session-kick', '踢下线', 'admin:session:kick', 2
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:session:kick');

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 137, 1, 1, 'cache', '缓存管理', 'Coin', '/system/cache', 'system/cache/index', 13, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 137);

INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 137, 2, 'cache-stats', '查看缓存', 'admin:cache:stats', 1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:cache:stats');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 137, 2, 'cache-clear', '清理缓存', 'admin:cache:clear', 2
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:cache:clear');

INSERT INTO role_menus (role_id, menu_id)
SELECT 1, id FROM menus
WHERE (id IN (136, 137) OR parent_id IN (136, 137))
  AND NOT EXISTS (SELECT 1 FROM role_menus WHERE role_id = 1 AND menu_id = menus.id);

COMMIT;
