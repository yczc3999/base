-- ============================================================
-- 管理员登录日志表
-- 记录管理员的每次登录尝试（成功和失败），用于安全审计
-- ============================================================

CREATE TABLE admin_login_logs (
    id              SERIAL PRIMARY KEY,                           -- 主键，自增
    user_id         INTEGER      NOT NULL,                        -- 用户 ID（登录失败且用户不存在时为 0）
    username        VARCHAR(50)  NOT NULL,                        -- 登录时输入的用户名
    ip              VARCHAR(50),                                  -- 登录来源 IP 地址
    user_agent      VARCHAR(500),                                 -- 浏览器 User-Agent
    status          SMALLINT     NOT NULL DEFAULT 1,              -- 登录结果：1=成功 0=失败
    remark          VARCHAR(255),                                 -- 备注（失败原因：密码错误、账号禁用等）
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW()           -- 登录时间

);

CREATE INDEX idx_admin_login_logs_user_id ON admin_login_logs (user_id);
CREATE INDEX idx_admin_login_logs_created_at ON admin_login_logs (created_at);

COMMENT ON TABLE  admin_login_logs IS '管理员登录日志表，记录每次登录尝试，用于安全审计';
COMMENT ON COLUMN admin_login_logs.id IS '主键，自增';
COMMENT ON COLUMN admin_login_logs.user_id IS '用户 ID，登录失败且用户名不存在时为 0';
COMMENT ON COLUMN admin_login_logs.username IS '登录时输入的用户名';
COMMENT ON COLUMN admin_login_logs.ip IS '登录来源 IP 地址';
COMMENT ON COLUMN admin_login_logs.user_agent IS '客户端浏览器 User-Agent 标识';
COMMENT ON COLUMN admin_login_logs.status IS '登录结果：1=成功 0=失败';
COMMENT ON COLUMN admin_login_logs.remark IS '备注信息，失败时记录原因（如密码错误、账号已禁用）';
COMMENT ON COLUMN admin_login_logs.created_at IS '登录尝试时间';
