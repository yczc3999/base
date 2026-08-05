-- ============================================================
-- 020: 数据库备份 — db_backups 表 + 菜单种子
--
-- 背景: base 无运行期数据保护, 数据只能靠手动 psql 装库恢复。
--       本迁移建 db_backups 记录表 + 后台入口 (恢复功能待决策, 暂不做)。
--
-- 变更:
--   1. db_backups 表 (filename 唯一, 记录备份元信息)
--   2. 系统管理(1) 下挂「数据库备份」菜单(id=133)
--   3. 权限点 admin:db_backup:list / create / delete
--   4. admin 角色绑定
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS db_backups (
    id          SERIAL PRIMARY KEY,
    filename    VARCHAR(255) NOT NULL UNIQUE,
    file_size   BIGINT       NOT NULL DEFAULT 0,
    status      VARCHAR(16)  NOT NULL DEFAULT 'ok',   -- ok / failed
    started_at  TIMESTAMP,
    finished_at TIMESTAMP,
    error_msg   VARCHAR(500),
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 133, 1, 1, 'db-backup', '数据库备份', 'FolderOpened', '/system/db_backup', 'system/db_backup/index', 9, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 133);

INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 133, 2, 'db-backup-list',   '查看备份', 'admin:db_backup:list',   1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:db_backup:list');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 133, 2, 'db-backup-create', '手动备份', 'admin:db_backup:create', 2
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:db_backup:create');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 133, 2, 'db-backup-delete', '删除备份', 'admin:db_backup:delete', 3
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:db_backup:delete');

INSERT INTO role_menus (role_id, menu_id)
SELECT 1, id FROM menus
WHERE (id = 133 OR parent_id = 133)
  AND NOT EXISTS (SELECT 1 FROM role_menus WHERE role_id = 1 AND menu_id = menus.id);

COMMIT;
