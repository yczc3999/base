-- ============================================================
-- 025: 系统监控页 — monitor 菜单 + 权限点种子
--
-- 变更:
--   1. 系统管理(1) 下挂「系统监控」菜单(id=135)
--   2. 权限点 admin:monitor:list
--   3. admin 角色绑定
-- ============================================================

BEGIN;

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 135, 1, 1, 'monitor', '系统监控', 'DataLine', '/system/monitor', 'system/monitor/index', 11, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 135);

INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 135, 2, 'monitor-list', '查看监控', 'admin:monitor:list', 1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:monitor:list');

INSERT INTO role_menus (role_id, menu_id)
SELECT 1, id FROM menus
WHERE (id = 135 OR parent_id = 135)
  AND NOT EXISTS (SELECT 1 FROM role_menus WHERE role_id = 1 AND menu_id = menus.id);

COMMIT;
