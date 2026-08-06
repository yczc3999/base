-- ============================================================
-- 028: 回收站 — trash 菜单 + 权限点
--
-- 变更:
--   1. 系统管理(1) 下挂「回收站」菜单(id=138)
--   2. 权限点 admin:trash:list/restore/purge
--   3. admin 角色绑定
-- ============================================================

BEGIN;

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 138, 1, 1, 'trash', '回收站', 'Delete', '/system/trash', 'system/trash/index', 14, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 138);

INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 138, 2, 'trash-list',    '查看回收站', 'admin:trash:list',    1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:trash:list');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 138, 2, 'trash-restore', '恢复', 'admin:trash:restore', 2
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:trash:restore');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 138, 2, 'trash-purge',   '彻底删除', 'admin:trash:purge', 3
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:trash:purge');

INSERT INTO role_menus (role_id, menu_id)
SELECT 1, id FROM menus
WHERE (id = 138 OR parent_id = 138)
  AND NOT EXISTS (SELECT 1 FROM role_menus WHERE role_id = 1 AND menu_id = menus.id);

COMMIT;
