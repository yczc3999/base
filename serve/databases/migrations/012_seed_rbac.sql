-- ============================================================
-- RBAC 初始数据：默认菜单树 + 默认角色 + 关联
-- ============================================================

-- ==================== 菜单 ====================

-- 系统管理（目录）
INSERT INTO menus (id, parent_id, type, slug, label, icon, redirect, sort) VALUES
(1, 0, 0, 'system', '系统管理', 'Settings', '/system/user', 1);

-- 用户管理
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort) VALUES
(2, 1, 1, 'admin-user', '用户管理', 'Users', '/system/user', 'system/user/index', 1);

INSERT INTO menus (id, parent_id, type, slug, label, perms, sort) VALUES
(10, 2, 2, 'admin-user-list',   '查看', 'admin:user:list',   1),
(11, 2, 2, 'admin-user-create', '新增', 'admin:user:create', 2),
(12, 2, 2, 'admin-user-edit',   '编辑', 'admin:user:edit',   3),
(13, 2, 2, 'admin-user-delete', '删除', 'admin:user:delete', 4);

-- 角色管理
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort) VALUES
(3, 1, 1, 'role', '角色管理', 'Shield', '/system/role', 'system/role/index', 2);

INSERT INTO menus (id, parent_id, type, slug, label, perms, sort) VALUES
(20, 3, 2, 'role-list',   '查看',     'admin:role:list',       1),
(21, 3, 2, 'role-create', '新增',     'admin:role:create',     2),
(22, 3, 2, 'role-edit',   '编辑',     'admin:role:edit',       3),
(23, 3, 2, 'role-delete', '删除',     'admin:role:delete',     4),
(24, 3, 2, 'role-assign', '分配权限', 'admin:role:assignMenu', 5);

-- 菜单管理
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort) VALUES
(4, 1, 1, 'menu', '菜单管理', 'Menu', '/system/menu', 'system/menu/index', 3);

INSERT INTO menus (id, parent_id, type, slug, label, perms, sort) VALUES
(30, 4, 2, 'menu-list',   '查看', 'admin:menu:list',   1),
(31, 4, 2, 'menu-create', '新增', 'admin:menu:create', 2),
(32, 4, 2, 'menu-edit',   '编辑', 'admin:menu:edit',   3),
(33, 4, 2, 'menu-delete', '删除', 'admin:menu:delete', 4);

-- 系统配置
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort) VALUES
(5, 1, 1, 'setting', '系统配置', 'Sliders', '/system/setting', 'system/setting/index', 4);

INSERT INTO menus (id, parent_id, type, slug, label, perms, sort) VALUES
(40, 5, 2, 'setting-get', '查看', 'admin:setting:get', 1),
(41, 5, 2, 'setting-set', '修改', 'admin:setting:set', 2);

-- 日志管理（目录）
INSERT INTO menus (id, parent_id, type, slug, label, icon, sort) VALUES
(6, 1, 0, 'log', '日志管理', 'FileText', 5);

-- 操作日志
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort) VALUES
(7, 6, 1, 'operation-log', '操作日志', 'Activity', '/system/log/operation', 'system/log/operation/index', 1);

INSERT INTO menus (id, parent_id, type, slug, label, perms, sort) VALUES
(50, 7, 2, 'operation-log-list', '查看', 'admin:log:operation:list', 1);

-- 登录日志
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort) VALUES
(8, 6, 1, 'login-log', '登录日志', 'LogIn', '/system/log/login', 'system/log/login/index', 2);

INSERT INTO menus (id, parent_id, type, slug, label, perms, sort) VALUES
(60, 8, 2, 'login-log-list', '查看', 'admin:log:login:list', 1);

-- 仪表盘（首页固定标签）
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, is_affix, sort) VALUES
(100, 0, 1, 'dashboard', '仪表盘', 'LayoutDashboard', '/dashboard', 'dashboard/index', TRUE, 0);

-- 个人中心（不在侧边栏显示）
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, is_visible, sort) VALUES
(110, 0, 1, 'profile', '个人中心', 'UserCircle', '/profile', 'profile/index', FALSE, 99);

INSERT INTO menus (id, parent_id, type, slug, label, perms, sort) VALUES
(111, 110, 2, 'profile-edit', '修改资料', 'admin:profile:edit', 1),
(112, 110, 2, 'profile-password', '修改密码', 'admin:profile:password', 2);

-- 系统消息（不在侧边栏显示，通过铃铛图标进入）
INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, is_visible, sort) VALUES
(120, 0, 1, 'message', '系统消息', 'Bell', '/message', 'message/index', FALSE, 98);

INSERT INTO menus (id, parent_id, type, slug, label, perms, sort) VALUES
(121, 120, 2, 'message-list', '查看', 'admin:message:list', 1),
(122, 120, 2, 'message-read', '标记已读', 'admin:message:read', 2),
(123, 120, 2, 'message-delete', '删除', 'admin:message:delete', 3);

-- 重置序列
SELECT setval('menus_id_seq', (SELECT MAX(id) FROM menus));

-- ==================== 角色 ====================

INSERT INTO roles (id, name, label, sort) VALUES
(1, 'admin', '管理员', 1),
(2, 'editor', '编辑员', 2);

SELECT setval('roles_id_seq', (SELECT MAX(id) FROM roles));

-- ==================== 角色菜单关联（admin 角色拥有全部菜单）====================

INSERT INTO role_menus (role_id, menu_id)
SELECT 1, id FROM menus;

-- ==================== 超级管理员绑定 admin 角色 ====================

INSERT INTO admin_user_roles (admin_user_id, role_id) VALUES (1, 1);
