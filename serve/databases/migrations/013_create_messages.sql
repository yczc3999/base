-- ============================================================
-- 系统消息表
-- ============================================================

CREATE TABLE messages (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER      NOT NULL,
    title       VARCHAR(200) NOT NULL,
    content     TEXT,
    type        SMALLINT     NOT NULL DEFAULT 0,
    is_read     BOOLEAN      NOT NULL DEFAULT FALSE,
    sender_id   INTEGER,
    sender_name VARCHAR(50),
    created_at  TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP    NOT NULL DEFAULT now()
);

CREATE INDEX idx_messages_user_id ON messages (user_id);
CREATE INDEX idx_messages_is_read ON messages (user_id, is_read);
CREATE INDEX idx_messages_created_at ON messages (created_at);

COMMENT ON TABLE messages IS '系统消息表';
COMMENT ON COLUMN messages.user_id IS '接收者用户ID';
COMMENT ON COLUMN messages.title IS '消息标题';
COMMENT ON COLUMN messages.content IS '消息内容';
COMMENT ON COLUMN messages.type IS '消息类型：0=系统通知 1=审批消息 2=告警消息';
COMMENT ON COLUMN messages.is_read IS '是否已读';
COMMENT ON COLUMN messages.sender_id IS '发送者ID（系统消息为空）';
COMMENT ON COLUMN messages.sender_name IS '发送者名称';
