-- ============================================================
-- 前端用户表
-- 存储面向 C 端/用户后台的用户账号信息
-- ============================================================

CREATE TABLE users (
    id              SERIAL PRIMARY KEY,                           -- 主键，自增
    username        VARCHAR(50)  NOT NULL UNIQUE,                 -- 登录用户名，唯一
    password        VARCHAR(255) NOT NULL,                        -- 密码（bcrypt 加密存储）
    nickname        VARCHAR(50),                                  -- 昵称/显示名称
    avatar          VARCHAR(255),                                 -- 头像 URL
    email           VARCHAR(100),                                 -- 邮箱
    phone           VARCHAR(20),                                  -- 手机号
    status          SMALLINT     NOT NULL DEFAULT 1,              -- 状态：1=正常 0=禁用
    token_version   INTEGER      NOT NULL DEFAULT 0,              -- Token 版本号
    last_login_at   TIMESTAMP,                                    -- 最后登录时间
    last_login_ip   VARCHAR(50),                                  -- 最后登录 IP
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),          -- 创建时间
    updated_at      TIMESTAMP    NOT NULL DEFAULT NOW()           -- 更新时间
);

COMMENT ON TABLE  users IS '前端用户表';
COMMENT ON COLUMN users.id IS '主键，自增';
COMMENT ON COLUMN users.username IS '登录用户名，唯一';
COMMENT ON COLUMN users.password IS '密码，bcrypt 加密存储';
COMMENT ON COLUMN users.nickname IS '昵称/显示名称';
COMMENT ON COLUMN users.avatar IS '头像 URL';
COMMENT ON COLUMN users.email IS '邮箱';
COMMENT ON COLUMN users.phone IS '手机号';
COMMENT ON COLUMN users.status IS '状态：1=正常 0=禁用';
COMMENT ON COLUMN users.token_version IS 'Token 版本号，修改密码或登出时递增';
COMMENT ON COLUMN users.last_login_at IS '最后登录时间';
COMMENT ON COLUMN users.last_login_ip IS '最后登录 IP 地址';
COMMENT ON COLUMN users.created_at IS '记录创建时间';
COMMENT ON COLUMN users.updated_at IS '记录更新时间';
