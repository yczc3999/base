-- ============================================================
-- 菜单/权限表（两级菜单 + 按钮权限）
-- type: 0=目录  1=菜单页面  2=按钮权限
-- ============================================================

CREATE TABLE menus (
    id            SERIAL PRIMARY KEY,
    parent_id     INTEGER     NOT NULL DEFAULT 0,
    type          SMALLINT    NOT NULL DEFAULT 0,
    slug          VARCHAR(50) NOT NULL,
    label         VARCHAR(50) NOT NULL,
    icon          VARCHAR(100),
    path          VARCHAR(200),
    template_path VARCHAR(200),
    redirect      VARCHAR(200),
    perms         VARCHAR(100),
    link          VARCHAR(500),
    link_target   VARCHAR(10)  DEFAULT '_self',
    is_cache      BOOLEAN      NOT NULL DEFAULT TRUE,
    is_affix      BOOLEAN      NOT NULL DEFAULT FALSE,
    is_visible    BOOLEAN      NOT NULL DEFAULT TRUE,
    badge         VARCHAR(20),
    sort          INTEGER      NOT NULL DEFAULT 0,
    status        SMALLINT     NOT NULL DEFAULT 1,
    remark        VARCHAR(255),
    created_at    TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at    TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT menus_slug_unique UNIQUE (slug)
);

CREATE INDEX idx_menus_parent_id ON menus (parent_id);
CREATE INDEX idx_menus_sort ON menus (sort);

COMMENT ON TABLE menus IS '菜单/权限表';
COMMENT ON COLUMN menus.parent_id IS '父级ID（0=顶级）';
COMMENT ON COLUMN menus.type IS '类型：0=目录 1=菜单页面 2=按钮权限';
COMMENT ON COLUMN menus.slug IS '唯一标识（英文，URL友好）';
COMMENT ON COLUMN menus.label IS '显示名称';
COMMENT ON COLUMN menus.icon IS '图标标识';
COMMENT ON COLUMN menus.path IS '前端路由路径';
COMMENT ON COLUMN menus.template_path IS '前端组件路径';
COMMENT ON COLUMN menus.redirect IS '重定向地址（目录类型用）';
COMMENT ON COLUMN menus.perms IS '权限标识（按钮类型，如 admin:user:list）';
COMMENT ON COLUMN menus.link IS '外链地址';
COMMENT ON COLUMN menus.link_target IS '外链打开方式：_self/_blank';
COMMENT ON COLUMN menus.is_cache IS '页面是否缓存（keep-alive）';
COMMENT ON COLUMN menus.is_affix IS '是否固定在标签栏';
COMMENT ON COLUMN menus.is_visible IS '是否在菜单中显示';
COMMENT ON COLUMN menus.badge IS '徽标（如 NEW、HOT）';
COMMENT ON COLUMN menus.sort IS '排序（升序）';
COMMENT ON COLUMN menus.status IS '状态：0=禁用 1=正常';
