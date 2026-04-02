-- ============================================================
-- 角色表
-- ============================================================

CREATE TABLE roles (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(50)  NOT NULL,
    label       VARCHAR(50)  NOT NULL,
    remark      VARCHAR(255),
    sort        INTEGER      NOT NULL DEFAULT 0,
    status      SMALLINT     NOT NULL DEFAULT 1,
    created_at  TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT roles_name_unique UNIQUE (name)
);

COMMENT ON TABLE roles IS '角色表';
COMMENT ON COLUMN roles.name IS '角色标识（英文，如 admin、editor）';
COMMENT ON COLUMN roles.label IS '显示名称';
COMMENT ON COLUMN roles.sort IS '排序';
COMMENT ON COLUMN roles.status IS '状态：0=禁用 1=正常';
