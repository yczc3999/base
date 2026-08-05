-- ============================================================
-- 022: 前端用户管理 — client_user 菜单 + 权限点种子
--
-- 背景: users 表此前无 admin 端管理入口 (查/禁/重置密码/踢下线)。
--
-- 变更:
--   1. 系统管理(1) 下挂「前端用户」菜单(id=131)
--   2. type=2 按钮权限点 admin:client_user:list/detail/create/edit/delete
--   3. admin 角色(role_id=1) 绑定新菜单
--   4. 幂等: WHERE NOT EXISTS
-- ============================================================

BEGIN;

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 131, 1, 1, 'client-user', '前端用户', 'User', '/system/client_user', 'system/client_user/index', 7, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 131);

INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 131, 2, 'client-user-list',   '查看用户', 'admin:client_user:list',   1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:client_user:list');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 131, 2, 'client-user-detail', '查看详情', 'admin:client_user:detail', 2
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:client_user:detail');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 131, 2, 'client-user-create', '新增用户', 'admin:client_user:create', 3
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:client_user:create');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 131, 2, 'client-user-edit',   '编辑/重置密码', 'admin:client_user:edit',   4
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:client_user:edit');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 131, 2, 'client-user-delete', '删除用户', 'admin:client_user:delete', 5
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:client_user:delete');

-- 重置密码 / 踢下线复用 admin:client_user:edit 权限点, 不单开

INSERT INTO role_menus (role_id, menu_id)
SELECT 1, id FROM menus
WHERE (id = 131 OR parent_id = 131)
  AND NOT EXISTS (SELECT 1 FROM role_menus WHERE role_id = 1 AND menu_id = menus.id);

COMMIT;
