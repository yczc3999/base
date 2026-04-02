-- ============================================================
-- admin_users 表 email 字段加唯一索引（支持邮箱登录）
-- ============================================================

CREATE UNIQUE INDEX admin_users_email_unique ON admin_users (email) WHERE email IS NOT NULL AND email != '';
