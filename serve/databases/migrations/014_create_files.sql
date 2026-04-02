-- ============================================================
-- 文件表
-- ============================================================

CREATE TABLE files (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(200)  NOT NULL,
    original_name VARCHAR(200)  NOT NULL,
    path          VARCHAR(500)  NOT NULL,
    url           VARCHAR(500)  NOT NULL,
    platform      VARCHAR(50)   NOT NULL,
    mime_type     VARCHAR(100),
    size          INTEGER       NOT NULL DEFAULT 0,
    ext           VARCHAR(20),
    is_private    BOOLEAN       NOT NULL DEFAULT FALSE,
    user_id       INTEGER,
    category      VARCHAR(50)   DEFAULT 'default',
    created_at    TIMESTAMP     NOT NULL DEFAULT now()
);

CREATE INDEX idx_files_user_id ON files (user_id);
CREATE INDEX idx_files_category ON files (category);
CREATE INDEX idx_files_created_at ON files (created_at);

COMMENT ON TABLE files IS '文件表';
COMMENT ON COLUMN files.name IS '存储文件名（uuid.ext）';
COMMENT ON COLUMN files.original_name IS '原始文件名';
COMMENT ON COLUMN files.path IS '相对路径';
COMMENT ON COLUMN files.url IS '完整访问URL';
COMMENT ON COLUMN files.platform IS '存储平台（local/aliyun_oss/...）';
COMMENT ON COLUMN files.mime_type IS 'MIME类型';
COMMENT ON COLUMN files.size IS '文件大小（字节）';
COMMENT ON COLUMN files.ext IS '扩展名';
COMMENT ON COLUMN files.is_private IS '是否隐私文件';
COMMENT ON COLUMN files.user_id IS '上传者ID';
COMMENT ON COLUMN files.category IS '分类（avatar/document/...）';
