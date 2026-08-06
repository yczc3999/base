-- ============================================================
-- 027: 密码策略 — admin_users 加 password_changed_at 列
--
-- 背景: P2-2 密码定期过期需要追踪密码最近修改时间。
--       updated_at 会被任意资料编辑刷新, 不能用作密码年龄。
--
-- 变更:
--   1. admin_users 加 password_changed_at (可空, 老数据无记录视为不过期)
-- ============================================================

ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP;
