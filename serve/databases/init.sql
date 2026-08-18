-- ============================================================
-- Base Platform — 数据库初始化脚本
--
-- 数据库: PostgreSQL 14+
-- 用法:
--   BASE_PLATFORM_DB_PASSWORD='<secret>' scripts/provision-base-database.sh
--
-- 包含: 11 张表 + 索引 + 初始管理员 + RBAC 菜单/角色
-- 默认管理员: admin / admin123
-- ============================================================

BEGIN;

-- ==================== 1. 管理员用户 ====================

CREATE TABLE admin_users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(50)  NOT NULL UNIQUE,
    password        VARCHAR(255) NOT NULL,
    nickname        VARCHAR(50),
    avatar          VARCHAR(255),
    email           VARCHAR(100),
    phone           VARCHAR(20),
    status          SMALLINT     NOT NULL DEFAULT 1,
    is_super_admin  BOOLEAN      NOT NULL DEFAULT FALSE,
    token_version   INTEGER      NOT NULL DEFAULT 0,
    last_login_at   TIMESTAMP,
    last_login_ip   VARCHAR(50),
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX admin_users_email_unique ON admin_users (email) WHERE email IS NOT NULL AND email != '';

-- ==================== 2. 前端用户 ====================

CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(50)  NOT NULL UNIQUE,
    password        VARCHAR(255) NOT NULL,
    nickname        VARCHAR(50),
    avatar          VARCHAR(255),
    email           VARCHAR(100),
    phone           VARCHAR(20),
    status          SMALLINT     NOT NULL DEFAULT 1,
    token_version   INTEGER      NOT NULL DEFAULT 0,
    last_login_at   TIMESTAMP,
    last_login_ip   VARCHAR(50),
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- ==================== 3. 系统配置 ====================

CREATE TABLE settings (
    id              SERIAL PRIMARY KEY,
    category        VARCHAR(50)  NOT NULL,
    name            VARCHAR(100) NOT NULL,
    label           VARCHAR(100),
    value           TEXT         NOT NULL DEFAULT '',
    remark          VARCHAR(255),
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    CONSTRAINT settings_category_name_unique UNIQUE (category, name)
);

-- ==================== 4. 菜单/权限 ====================

CREATE TABLE menus (
    id            SERIAL PRIMARY KEY,
    parent_id     INTEGER     NOT NULL DEFAULT 0,
    type          SMALLINT    NOT NULL DEFAULT 0,
    slug          VARCHAR(50) NOT NULL,
    label         VARCHAR(50) NOT NULL,
    icon          VARCHAR(100),
    path          VARCHAR(200),
    template_path VARCHAR(200),
    redirect      VARCHAR(200),
    perms         VARCHAR(100),
    link          VARCHAR(500),
    link_target   VARCHAR(10)  DEFAULT '_self',
    is_cache      BOOLEAN      NOT NULL DEFAULT TRUE,
    is_affix      BOOLEAN      NOT NULL DEFAULT FALSE,
    is_visible    BOOLEAN      NOT NULL DEFAULT TRUE,
    badge         VARCHAR(20),
    sort          INTEGER      NOT NULL DEFAULT 0,
    status        SMALLINT     NOT NULL DEFAULT 1,
    remark        VARCHAR(255),
    created_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    CONSTRAINT menus_slug_unique UNIQUE (slug)
);

CREATE INDEX idx_menus_parent_id ON menus (parent_id);
CREATE INDEX idx_menus_sort ON menus (sort);

-- ==================== 5. 角色 ====================

CREATE TABLE roles (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(50)  NOT NULL,
    label       VARCHAR(50)  NOT NULL,
    remark      VARCHAR(255),
    sort        INTEGER      NOT NULL DEFAULT 0,
    status      SMALLINT     NOT NULL DEFAULT 1,
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    CONSTRAINT roles_name_unique UNIQUE (name)
);

-- ==================== 6. 角色菜单关联 ====================

CREATE TABLE role_menus (
    role_id  INTEGER NOT NULL,
    menu_id  INTEGER NOT NULL,
    PRIMARY KEY (role_id, menu_id)
);

-- ==================== 7. 管理员角色关联 ====================

CREATE TABLE admin_user_roles (
    admin_user_id  INTEGER NOT NULL,
    role_id        INTEGER NOT NULL,
    PRIMARY KEY (admin_user_id, role_id)
);

-- ==================== 8. 操作日志 ====================

CREATE TABLE admin_operation_logs (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER      NOT NULL,
    username        VARCHAR(50)  NOT NULL,
    module          VARCHAR(100) NOT NULL,
    action          VARCHAR(100) NOT NULL,
    method          VARCHAR(10)  NOT NULL,
    url             VARCHAR(500) NOT NULL,
    params          TEXT,
    ip              VARCHAR(50),
    user_agent      VARCHAR(500),
    status_code     INTEGER      NOT NULL DEFAULT 0,
    duration        INTEGER      NOT NULL DEFAULT 0,
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_admin_operation_logs_user_id ON admin_operation_logs (user_id);
CREATE INDEX idx_admin_operation_logs_created_at ON admin_operation_logs (created_at);

-- ==================== 9. 登录日志 ====================

CREATE TABLE admin_login_logs (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER      NOT NULL,
    username        VARCHAR(50)  NOT NULL,
    ip              VARCHAR(50),
    user_agent      VARCHAR(500),
    status          SMALLINT     NOT NULL DEFAULT 1,
    remark          VARCHAR(255),
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_admin_login_logs_user_id ON admin_login_logs (user_id);
CREATE INDEX idx_admin_login_logs_created_at ON admin_login_logs (created_at);

-- ==================== 10. 系统消息 ====================

CREATE TABLE messages (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER      NOT NULL,
    title       VARCHAR(200) NOT NULL,
    content     TEXT,
    type        SMALLINT     NOT NULL DEFAULT 0,
    is_read     BOOLEAN      NOT NULL DEFAULT FALSE,
    sender_id   INTEGER,
    sender_name VARCHAR(50),
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_messages_user_id ON messages (user_id);
CREATE INDEX idx_messages_is_read ON messages (user_id, is_read);
CREATE INDEX idx_messages_created_at ON messages (created_at);

-- ==================== 11. 文件 ====================

CREATE TABLE files (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(200)  NOT NULL,
    original_name VARCHAR(200)  NOT NULL,
    path          VARCHAR(500)  NOT NULL,
    url           VARCHAR(500)  NOT NULL,
    platform      VARCHAR(50)   NOT NULL,
    mime_type     VARCHAR(100),
    size          INTEGER       NOT NULL DEFAULT 0,
    ext           VARCHAR(20),
    is_private    BOOLEAN       NOT NULL DEFAULT FALSE,
    user_id       INTEGER,
    category      VARCHAR(50)   DEFAULT 'default',
    created_at    TIMESTAMP     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_files_user_id ON files (user_id);
CREATE INDEX idx_files_category ON files (category);
CREATE INDEX idx_files_created_at ON files (created_at);


-- ============================================================
-- 初始数据
-- ============================================================

-- 超级管理员（密码: admin123）
INSERT INTO admin_users (username, password, nickname, is_super_admin, status)
VALUES ('admin', '$2b$10$l41wfewhCQNc6t/EKNwCJOL.ugSbSYJf2k5ZosRGEx8mQsGgTIXlu', '超级管理员', TRUE, 1);

-- ==================== 菜单树 ====================

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

-- admin 角色拥有全部菜单
INSERT INTO role_menus (role_id, menu_id) SELECT 1, id FROM menus;

-- 超级管理员绑定 admin 角色
INSERT INTO admin_user_roles (admin_user_id, role_id) VALUES (1, 1);

COMMIT;
