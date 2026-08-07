-- 030: messages 表重建 — 修复 schema 漂移（recipient 结构 → user_id 结构）
--
-- 背景:
--   messages 表被某次非受控改动手工改成了多接收者事件化设计:
--     recipient_id bigint NOT NULL + recipient_type(varchar, CHECK admin/customer/parttime)
--     + event_id/event_code/biz_type/biz_id/payload/idempotency_key/action_path/
--       read_at/archived_at 等扩展列 + CHECK(recipient_id > 0)
--   但:
--     1. schema_migrations 无任何迁移改过它 (最新 029)
--     2. init.sql 与迁移 013_create_messages.sql 均定义为 user_id 结构
--     3. 整个 serve 后端 0 处代码使用 recipient_* 列 (grep recipient → 0 命中)
--     4. 模型 app/models/message.py 使用 user_id
--     5. 表内 0 行数据 (无迁移成本)
--   → 纯 schema 漂移。dashboard / admin 消息接口查询 Message.user_id 全部抛
--     UndefinedColumn → 全局异常处理器转成 HTTP 200 + code 500 → 前端 showError
--     =false 静默吞掉 → dashboard 数据显示为空。
--
-- 变更:
--   1. DROP 漂移表 (0 数据, 无 FK 依赖, 无代码引用)
--      原结构已备份至 databases/backups/messages_recipient_schema_20260707.sql
--   2. 按 init.sql + 迁移 013 重建 user_id 结构 (与模型完全对齐)
--   3. 重建索引
--
-- 注意: 本表设计为「单一接收者 user_id」(admin 消息), 与 BaseLogic bind_user_column
--       及 MessageLogic 全量代码一致。若未来确需多接收者 (admin/customer/parttime),
--       请走正式迁移 + 同步模型/代码, 不要再手工改表。

DROP TABLE IF EXISTS messages;

CREATE TABLE messages (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER      NOT NULL,
    title       VARCHAR(200) NOT NULL,
    content     TEXT,
    type        SMALLINT     NOT NULL DEFAULT 0,
    is_read     BOOLEAN      NOT NULL DEFAULT FALSE,
    sender_id   INTEGER,
    sender_name VARCHAR(50),
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_messages_user_id ON messages (user_id);
CREATE INDEX idx_messages_is_read ON messages (user_id, is_read);
CREATE INDEX idx_messages_created_at ON messages (created_at);

COMMENT ON TABLE messages IS '系统消息表';
COMMENT ON COLUMN messages.user_id IS '接收者用户ID(admin)';
COMMENT ON COLUMN messages.type IS '消息类型: 0=系统通知 1=审批消息 2=告警消息';
COMMENT ON COLUMN messages.is_read IS '是否已读';
