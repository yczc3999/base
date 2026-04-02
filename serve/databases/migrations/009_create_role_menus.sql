-- ============================================================
-- 角色菜单关联表
-- ============================================================

CREATE TABLE role_menus (
    role_id  INTEGER NOT NULL,
    menu_id  INTEGER NOT NULL,
    PRIMARY KEY (role_id, menu_id)
);

COMMENT ON TABLE role_menus IS '角色菜单关联表';
