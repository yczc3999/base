-- ============================================================
-- 系统配置表
-- 以 category + name 为键的 KV 配置存储
-- 支持全量缓存，一次加载所有配置
-- ============================================================

CREATE TABLE settings (
    id              SERIAL PRIMARY KEY,                           -- 主键，自增
    category        VARCHAR(50)  NOT NULL,                        -- 配置类别（如 site、sms、payment）
    name            VARCHAR(100) NOT NULL,                        -- 配置键名（如 title、provider）
    label           VARCHAR(100),                                 -- 显示名称（后台管理界面展示用）
    value           TEXT         NOT NULL,                        -- 配置值（JSON 序列化存储，支持任意类型）
    remark          VARCHAR(255),                                 -- 备注说明
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),          -- 创建时间
    updated_at      TIMESTAMP    NOT NULL DEFAULT NOW(),          -- 更新时间

    CONSTRAINT settings_category_name_unique UNIQUE (category, name) -- 类别 + 键名联合唯一
);

COMMENT ON TABLE  settings IS '系统配置表，以 category + name 为键的 KV 配置存储';
COMMENT ON COLUMN settings.id IS '主键，自增';
COMMENT ON COLUMN settings.category IS '配置类别，如 site（站点）、sms（短信）、payment（支付）';
COMMENT ON COLUMN settings.name IS '配置键名，如 title、provider、api_key';
COMMENT ON COLUMN settings.label IS '显示名称，后台管理界面展示用';
COMMENT ON COLUMN settings.value IS '配置值，JSON 序列化存储，支持字符串、数字、数组、对象等任意类型';
COMMENT ON COLUMN settings.remark IS '备注说明，如使用提示、可选值范围等';
COMMENT ON COLUMN settings.created_at IS '记录创建时间';
COMMENT ON COLUMN settings.updated_at IS '记录更新时间';
