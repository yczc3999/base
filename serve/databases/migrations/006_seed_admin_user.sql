-- ============================================================
-- 初始化数据：创建超级管理员账号
-- 默认密码：admin123（bcrypt 加密）
-- ============================================================

INSERT INTO admin_users (username, password, nickname, is_super_admin, status)
VALUES (
    'admin',
    '$2b$10$l41wfewhCQNc6t/EKNwCJOL.ugSbSYJf2k5ZosRGEx8mQsGgTIXlu',
    '超级管理员',
    TRUE,
    1
);
