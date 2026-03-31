-- ============================================================
-- 管理员操作日志表
-- 记录管理员在后台的所有操作行为，用于审计追踪
-- 写多读少，不建缓存
-- ============================================================

CREATE TABLE admin_operation_logs (
    id              SERIAL PRIMARY KEY,                           -- 主键，自增
    user_id         INTEGER      NOT NULL,                        -- 操作人 ID（关联 admin_users.id）
    username        VARCHAR(50)  NOT NULL,                        -- 操作人用户名（冗余存储，方便查询展示）
    module          VARCHAR(100) NOT NULL,                        -- 操作模块（Controller 类名，如 AdminUserController）
    action          VARCHAR(100) NOT NULL,                        -- 操作方法（如 doEdit、doDelete、login）
    method          VARCHAR(10)  NOT NULL,                        -- HTTP 请求方法（GET/POST/PUT/DELETE）
    url             VARCHAR(500) NOT NULL,                        -- 请求完整 URL
    params          TEXT,                                         -- 请求参数（JSON 格式，敏感字段已脱敏为 ***）
    ip              VARCHAR(50),                                  -- 请求来源 IP 地址
    user_agent      VARCHAR(500),                                 -- 浏览器 User-Agent
    status_code     INTEGER      NOT NULL DEFAULT 0,              -- 响应业务状态码（0=成功，非0=失败）
    duration        INTEGER      NOT NULL DEFAULT 0,              -- 请求处理耗时（毫秒）
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW()           -- 记录创建时间
);

CREATE INDEX idx_admin_operation_logs_user_id ON admin_operation_logs (user_id);
CREATE INDEX idx_admin_operation_logs_created_at ON admin_operation_logs (created_at);

COMMENT ON TABLE  admin_operation_logs IS '管理员操作日志表，记录后台所有操作行为，用于审计追踪';
COMMENT ON COLUMN admin_operation_logs.id IS '主键，自增';
COMMENT ON COLUMN admin_operation_logs.user_id IS '操作人 ID，关联 admin_users 表';
COMMENT ON COLUMN admin_operation_logs.username IS '操作人用户名，冗余存储避免关联查询';
COMMENT ON COLUMN admin_operation_logs.module IS '操作模块，即 Controller 类名';
COMMENT ON COLUMN admin_operation_logs.action IS '操作方法名，如 doEdit、doDelete、getList';
COMMENT ON COLUMN admin_operation_logs.method IS 'HTTP 请求方法：GET、POST、PUT、DELETE';
COMMENT ON COLUMN admin_operation_logs.url IS '请求完整 URL 路径';
COMMENT ON COLUMN admin_operation_logs.params IS '请求参数，JSON 格式，密码等敏感字段已替换为 ***';
COMMENT ON COLUMN admin_operation_logs.ip IS '请求来源 IP 地址';
COMMENT ON COLUMN admin_operation_logs.user_agent IS '客户端浏览器 User-Agent 标识';
COMMENT ON COLUMN admin_operation_logs.status_code IS '响应业务状态码，0 表示成功，非 0 表示失败';
COMMENT ON COLUMN admin_operation_logs.duration IS '请求处理耗时，单位毫秒';
COMMENT ON COLUMN admin_operation_logs.created_at IS '日志记录时间';
