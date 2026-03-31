-- ============================================================
-- 管理员用户表
-- 存储后台管理系统的管理员账号信息
-- ============================================================

CREATE TABLE admin_users (
    id              SERIAL PRIMARY KEY,                           -- 主键，自增
    username        VARCHAR(50)  NOT NULL UNIQUE,                 -- 登录用户名，唯一
    password        VARCHAR(255) NOT NULL,                        -- 密码（bcrypt 加密存储）
    nickname        VARCHAR(50),                                  -- 昵称/显示名称
    avatar          VARCHAR(255),                                 -- 头像 URL
    email           VARCHAR(100),                                 -- 邮箱
    phone           VARCHAR(20),                                  -- 手机号
    status          SMALLINT     NOT NULL DEFAULT 1,              -- 状态：1=正常 0=禁用
    is_super_admin  BOOLEAN      NOT NULL DEFAULT FALSE,          -- 是否超级管理员（拥有全部权限，跳过 RBAC 校验）
    token_version   INTEGER      NOT NULL DEFAULT 0,              -- Token 版本号（修改密码/登出时递增，使旧 token 失效）
    last_login_at   TIMESTAMP,                                    -- 最后登录时间
    last_login_ip   VARCHAR(50),                                  -- 最后登录 IP
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),          -- 创建时间
    updated_at      TIMESTAMP    NOT NULL DEFAULT NOW()           -- 更新时间
);

COMMENT ON TABLE  admin_users IS '管理员用户表';
COMMENT ON COLUMN admin_users.id IS '主键，自增';
COMMENT ON COLUMN admin_users.username IS '登录用户名，唯一';
COMMENT ON COLUMN admin_users.password IS '密码，bcrypt 加密存储';
COMMENT ON COLUMN admin_users.nickname IS '昵称/显示名称';
COMMENT ON COLUMN admin_users.avatar IS '头像 URL';
COMMENT ON COLUMN admin_users.email IS '邮箱';
COMMENT ON COLUMN admin_users.phone IS '手机号';
COMMENT ON COLUMN admin_users.status IS '状态：1=正常 0=禁用';
COMMENT ON COLUMN admin_users.is_super_admin IS '是否超级管理员，拥有全部权限，跳过 RBAC 校验';
COMMENT ON COLUMN admin_users.token_version IS 'Token 版本号，修改密码或登出时递增，使旧 token 立即失效';
COMMENT ON COLUMN admin_users.last_login_at IS '最后登录时间';
COMMENT ON COLUMN admin_users.last_login_ip IS '最后登录 IP 地址';
COMMENT ON COLUMN admin_users.created_at IS '记录创建时间';
COMMENT ON COLUMN admin_users.updated_at IS '记录更新时间';
