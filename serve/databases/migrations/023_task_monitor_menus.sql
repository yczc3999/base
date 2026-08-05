-- ============================================================
-- 023: 任务/队列监控 — task_monitor 菜单 + 权限点种子
--
-- 变更:
--   1. 系统管理(1) 下挂「任务监控」菜单(id=132)
--   2. 权限点 admin:task_monitor:list(查看) + admin:task_monitor:trigger(手动触发)
--   3. admin 角色绑定
-- ============================================================

BEGIN;

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 132, 1, 1, 'task-monitor', '任务监控', 'Timer', '/system/task_monitor', 'system/task_monitor/index', 8, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 132);

INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 132, 2, 'task-monitor-list',    '查看任务/队列', 'admin:task_monitor:list',    1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:task_monitor:list');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 132, 2, 'task-monitor-trigger', '手动触发', 'admin:task_monitor:trigger', 2
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:task_monitor:trigger');

INSERT INTO role_menus (role_id, menu_id)
SELECT 1, id FROM menus
WHERE (id = 132 OR parent_id = 132)
  AND NOT EXISTS (SELECT 1 FROM role_menus WHERE role_id = 1 AND menu_id = menus.id);

COMMIT;
