-- 016: 交易机器人管理后台模块（BSC rb-paper bot）
--
-- 设计文档: serve/docs/trading-module.md
--
-- 数据来源: /code/postgrad-signal-lab/data/results/bsc-rb-paper-v1/ 下的
--   create-only JSONL 账本（signals/trades/executions）与 run.log HEARTBEAT，
--   由 app/tasks/strategy_bridge.py 桥接落库（幂等 upsert，dedupe_key 去重）。
-- 反向通道: rb_strategies 表 → 原子写 control.json，bot 每 10s 重读。
--
-- 全部 rb_ 前缀，避免与 base 平台表混淆。可重复执行（IF NOT EXISTS / ON CONFLICT）。

BEGIN;

-- ============================================================
-- 平仓交易（round-trip）
-- ============================================================
CREATE TABLE IF NOT EXISTS rb_trades (
    id            BIGSERIAL PRIMARY KEY,
    run_id        VARCHAR(64)  NOT NULL DEFAULT '',
    signal_id     VARCHAR(128),
    dedupe_key    VARCHAR(64)  NOT NULL,
    pool_address  VARCHAR(64),
    token_address VARCHAR(64),
    token_symbol  VARCHAR(64),
    direction     VARCHAR(8)   NOT NULL DEFAULT 'long',
    entry_time    TIMESTAMPTZ,
    exit_time     TIMESTAMPTZ,
    entry_price   NUMERIC(38, 18),
    exit_price    NUMERIC(38, 18),
    amount_usd    NUMERIC(20, 8),
    pnl_usd       NUMERIC(20, 8),
    pnl_pct       NUMERIC(12, 6),
    exit_reason   VARCHAR(32),
    source        VARCHAR(8)   NOT NULL DEFAULT 'PAPER',  -- PAPER/SHADOW/LIVE
    raw           JSONB,
    created_at    TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT rb_trades_dedupe_unique UNIQUE (dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_rb_trades_entry_time ON rb_trades (entry_time);
CREATE INDEX IF NOT EXISTS idx_rb_trades_source ON rb_trades (source);
CREATE INDEX IF NOT EXISTS idx_rb_trades_pool ON rb_trades (pool_address);

COMMENT ON TABLE rb_trades IS '交易bot平仓 round-trip 记录（桥接自 trades.jsonl）';
COMMENT ON COLUMN rb_trades.dedupe_key IS 'raw 行 sha256，幂等 upsert 用';

-- ============================================================
-- 当前持仓（桥接任务维护：signal 开仓 / trade 平仓）
-- ============================================================
CREATE TABLE IF NOT EXISTS rb_positions (
    id            BIGSERIAL PRIMARY KEY,
    run_id        VARCHAR(64)  NOT NULL DEFAULT '',
    signal_id     VARCHAR(128),
    dedupe_key    VARCHAR(64)  NOT NULL,
    pool_address  VARCHAR(64),
    token_address VARCHAR(64),
    token_symbol  VARCHAR(64),
    direction     VARCHAR(8)   NOT NULL DEFAULT 'long',
    entry_time    TIMESTAMPTZ,
    entry_price   NUMERIC(38, 18),
    amount_usd    NUMERIC(20, 8),
    status        VARCHAR(8)   NOT NULL DEFAULT 'open',   -- open/closed
    pnl_usd       NUMERIC(20, 8),                          -- 浮盈（无报价源时为空）
    raw           JSONB,
    created_at    TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at    TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT rb_positions_dedupe_unique UNIQUE (dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_rb_positions_status ON rb_positions (status);

COMMENT ON TABLE rb_positions IS '交易bot持仓（桥接自 signals.jsonl，trades.jsonl 平仓联动）';

-- ============================================================
-- 策略配置（单行 per name；保存即下发 control.json）
-- ============================================================
CREATE TABLE IF NOT EXISTS rb_strategies (
    id         BIGSERIAL PRIMARY KEY,
    name       VARCHAR(64) NOT NULL,
    mode       VARCHAR(8)  NOT NULL DEFAULT 'PAPER',      -- PAPER/SHADOW/LIVE
    params     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP   NOT NULL DEFAULT now(),
    updated_at TIMESTAMP   NOT NULL DEFAULT now(),
    CONSTRAINT rb_strategies_name_unique UNIQUE (name)
);

COMMENT ON TABLE rb_strategies IS '交易bot策略配置，doEdit 后原子同步到 control.json';

INSERT INTO rb_strategies (name, mode, params) VALUES
('bsc-rb-paper', 'PAPER', '{
  "max_position_usd": 400,
  "max_open_positions": 3,
  "daily_loss_breaker_usd": 200,
  "slippage_bps": 50,
  "gas_price_cap_gwei": 5
}'::jsonb)
ON CONFLICT (name) DO NOTHING;

-- ============================================================
-- bot 心跳（桥接自 run.log HEARTBEAT 行）
-- ============================================================
CREATE TABLE IF NOT EXISTS rb_heartbeats (
    id            BIGSERIAL PRIMARY KEY,
    run_id        VARCHAR(64)  NOT NULL DEFAULT '',
    ts            TIMESTAMPTZ  NOT NULL,
    block         BIGINT,
    pools         INTEGER,
    open_count    INTEGER,
    signals_total INTEGER,
    trades_total  INTEGER,
    cum_pnl_usd   NUMERIC(20, 8),
    dedupe_key    VARCHAR(64)  NOT NULL,
    created_at    TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT rb_heartbeats_dedupe_unique UNIQUE (dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_rb_heartbeats_ts ON rb_heartbeats (ts);

COMMENT ON TABLE rb_heartbeats IS '交易bot心跳（run.log HEARTBEAT 行）';
COMMENT ON COLUMN rb_heartbeats.dedupe_key IS 'block:chain_ts，幂等';

-- ============================================================
-- 执行记录（桥接自 executor/ledger/executions.jsonl，可空）
-- ============================================================
CREATE TABLE IF NOT EXISTS rb_executions (
    id         BIGSERIAL PRIMARY KEY,
    run_id     VARCHAR(64) NOT NULL DEFAULT '',
    ts         TIMESTAMPTZ,
    kind       VARCHAR(32),
    dedupe_key VARCHAR(64) NOT NULL,
    payload    JSONB,
    created_at TIMESTAMP   NOT NULL DEFAULT now(),
    CONSTRAINT rb_executions_dedupe_unique UNIQUE (dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_rb_executions_ts ON rb_executions (ts);

COMMENT ON TABLE rb_executions IS '交易bot执行记录（executor/ledger/executions.jsonl 原文）';

-- ============================================================
-- 菜单 seed（模板路径对应 admin/src/views/trading/）
-- ============================================================
INSERT INTO menus (id, parent_id, type, slug, label, icon, redirect, sort) VALUES
(800, 0, 0, 'trading', '交易Bot', 'TrendCharts', '/trading/dashboard', 90)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO menus (id, parent_id, type, slug, label, icon, path, template_path, sort) VALUES
(801, 800, 1, 'trading-dashboard', '交易看板', 'DataAnalysis', '/trading/dashboard', 'trading/dashboard/index', 1),
(802, 800, 1, 'trading-trade',     '交易记录', 'List',         '/trading/trade',     'trading/trade/index',     2),
(803, 800, 1, 'trading-position',  '当前持仓', 'Wallet',       '/trading/position',  'trading/position/index',  3),
(804, 800, 1, 'trading-strategy',  '策略控制', 'Switch',       '/trading/strategy',  'trading/strategy/index',  4)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO menus (id, parent_id, type, slug, label, perms, sort) VALUES
(810, 802, 2, 'trading-trade-list',     '查看',     'admin:trading:list',   1),
(811, 802, 2, 'trading-trade-export',   '导出',     'admin:trading:export', 2),
(812, 804, 2, 'trading-strategy-edit',  '修改策略', 'admin:trading:edit',   1)
ON CONFLICT (slug) DO NOTHING;

COMMIT;
