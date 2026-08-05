-- ============================================================
-- 024: Migration 管理 — migration 菜单 + 权限点种子
--
-- 变更:
--   1. 系统管理(1) 下挂「Migration 管理」菜单(id=134)
--   2. 权限点 admin:migration:list(查看) + admin:migration:run(执行)
--   3. admin 角色绑定
-- ============================================================

BEGIN;

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 134, 1, 1, 'migration', 'Migration 管理', 'Files', '/system/migration', 'system/migration/index', 10, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 134);

INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 134, 2, 'migration-list', '查看迁移', 'admin:migration:list', 1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:migration:list');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 134, 2, 'migration-run', '执行迁移', 'admin:migration:run', 2
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:migration:run');

INSERT INTO role_menus (role_id, menu_id)
SELECT 1, id FROM menus
WHERE (id = 134 OR parent_id = 134)
  AND NOT EXISTS (SELECT 1 FROM role_menus WHERE role_id = 1 AND menu_id = menus.id);

COMMIT;
