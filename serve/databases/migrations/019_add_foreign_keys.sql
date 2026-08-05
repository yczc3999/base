-- ============================================================
-- 019: 关联表补 FK + ON DELETE CASCADE（引用完整性）
--
-- 背景: role_menus / admin_user_roles 是裸 Integer 主键，无外键，
--       删 menu/user 会留孤儿行（应用层守卫可被绕过，DB 层不阻止）。
--       article_keywords 已有 FK + CASCADE（015 已建），本迁移不动。
--
-- 策略: 关联表用 ON DELETE CASCADE（junction table 无独立生命周期，
--       主记录消失时关联本就该消失）。
--
-- 注意: migrate.py 的语句拆分不识别 DO $$ 块，故不用 DO/IF NOT EXISTS；
--       幂等性由 schema_migrations 记录保证（每条迁移只执行一次）。
-- ============================================================

-- 1) 防御性孤儿清理（幂等：无孤儿则无操作；避免 ADD CONSTRAINT 因孤儿行失败）
DELETE FROM role_menus WHERE menu_id NOT IN (SELECT id FROM menus);
DELETE FROM role_menus WHERE role_id NOT IN (SELECT id FROM roles);
DELETE FROM admin_user_roles WHERE admin_user_id NOT IN (SELECT id FROM admin_users);
DELETE FROM admin_user_roles WHERE role_id NOT IN (SELECT id FROM roles);

-- 2) role_menus: 双向 FK + CASCADE
ALTER TABLE role_menus ADD CONSTRAINT role_menus_role_id_fkey
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE;
ALTER TABLE role_menus ADD CONSTRAINT role_menus_menu_id_fkey
    FOREIGN KEY (menu_id) REFERENCES menus(id) ON DELETE CASCADE;

-- 3) admin_user_roles: 双向 FK + CASCADE
ALTER TABLE admin_user_roles ADD CONSTRAINT admin_user_roles_admin_user_id_fkey
    FOREIGN KEY (admin_user_id) REFERENCES admin_users(id) ON DELETE CASCADE;
ALTER TABLE admin_user_roles ADD CONSTRAINT admin_user_roles_role_id_fkey
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE;
