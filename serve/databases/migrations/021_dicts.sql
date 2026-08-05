-- ============================================================
-- 021: 数据字典 — dicts + dict_items 两表 + 菜单种子
--
-- 背景: base 无集中管理枚举/常量的底座, 前端 DictTag 无数据源。
--       业务里性别/状态/类型等硬编码在各模块, 新增类型要改代码。
--
-- 变更:
--   1. dicts 表 (type_name 唯一) + dict_items 表 (dict_id FK CASCADE)
--   2. 系统管理(1) 下挂「数据字典」菜单(id=130) + 按钮权限点
--   3. admin 角色(role_id=1) 绑定新菜单
--   4. 幂等: IF NOT EXISTS / WHERE NOT EXISTS
-- ============================================================

BEGIN;

-- ---------- 1) dicts ----------

CREATE TABLE IF NOT EXISTS dicts (
    id          SERIAL PRIMARY KEY,
    type_name   VARCHAR(50)  NOT NULL UNIQUE,
    description VARCHAR(200),
    status      SMALLINT     NOT NULL DEFAULT 1,   -- 0 禁用 / 1 启用
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------- 2) dict_items ----------

CREATE TABLE IF NOT EXISTS dict_items (
    id         SERIAL PRIMARY KEY,
    dict_id    INTEGER      NOT NULL REFERENCES dicts(id) ON DELETE CASCADE,
    value      VARCHAR(100) NOT NULL,
    label      VARCHAR(100) NOT NULL,
    sort       INTEGER      NOT NULL DEFAULT 0,
    status     SMALLINT     NOT NULL DEFAULT 1,   -- 0 禁用 / 1 启用
    created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_dict_items_dict_value UNIQUE (dict_id, value)
);

CREATE INDEX IF NOT EXISTS idx_dict_items_dict_id ON dict_items (dict_id);

-- ---------- 3) 菜单: 数据字典 (挂在系统管理 id=1 下) ----------

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort, is_visible, is_cache)
SELECT 130, 1, 1, 'dict', '数据字典', 'Collection', '/system/dict', 'system/dict/index', 6, true, true
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE id = 130);

-- 按钮权限点 (type=2)
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 130, 2, 'dict-list',   '查看字典', 'admin:dict:list',   1
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:dict:list');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 130, 2, 'dict-detail', '查看详情', 'admin:dict:detail', 2
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:dict:detail');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 130, 2, 'dict-create', '新增字典', 'admin:dict:create', 3
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:dict:create');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 130, 2, 'dict-edit',   '编辑字典', 'admin:dict:edit',   4
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:dict:edit');
INSERT INTO menus (parent_id, type, slug, label, perms, sort)
SELECT 130, 2, 'dict-delete', '删除字典', 'admin:dict:delete', 5
WHERE NOT EXISTS (SELECT 1 FROM menus WHERE perms = 'admin:dict:delete');

-- ---------- 4) admin 角色绑定 (页面 + 权限点) ----------

INSERT INTO role_menus (role_id, menu_id)
SELECT 1, id FROM menus
WHERE (id = 130 OR parent_id = 130)
  AND NOT EXISTS (SELECT 1 FROM role_menus WHERE role_id = 1 AND menu_id = menus.id);

COMMIT;
