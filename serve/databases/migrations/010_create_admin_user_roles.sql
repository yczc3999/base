-- ============================================================
-- 管理员角色关联表
-- ============================================================

CREATE TABLE admin_user_roles (
    admin_user_id  INTEGER NOT NULL,
    role_id        INTEGER NOT NULL,
    PRIMARY KEY (admin_user_id, role_id)
);

COMMENT ON TABLE admin_user_roles IS '管理员角色关联表';
