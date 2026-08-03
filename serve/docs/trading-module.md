# 交易 Bot 管理后台模块

BSC 链上交易 bot（postgrad-signal-lab 项目，账本目录
`/code/postgrad-signal-lab/data/results/bsc-rb-paper-v1/`）的管理后台。
正向把 bot 的 create-only JSONL 账本与心跳落库展示，反向把策略配置
原子下发到 bot 轮询的 control.json。

## 表结构（迁移 016_trading_module.sql，全部 rb_ 前缀）

| 表 | 说明 | 关键列 |
| --- | --- | --- |
| rb_trades | 平仓 round-trip | signal_id、pool/token、direction、entry/exit_time、entry/exit_price、amount_usd、pnl_usd、pnl_pct、exit_reason、source(PAPER/SHADOW/LIVE)、raw(JSONB)、dedupe_key |
| rb_positions | 持仓（signal 开仓 / trade 平仓联动） | entry_time、entry_price、amount_usd、status(open/closed)、pnl_usd(浮盈，无报价源时为空) |
| rb_strategies | 策略配置（单行 per name） | name、mode(PAPER/SHADOW/LIVE)、params(JSONB)、updated_at |
| rb_heartbeats | bot 心跳 | ts、block、pools、open_count、signals_total、trades_total、cum_pnl_usd |
| rb_executions | 执行记录原文 | ts、kind、payload(JSONB) |

所有桥接写入以 `dedupe_key`（raw 行 sha256，心跳为整行哈希）唯一约束 +
`ON CONFLICT DO NOTHING` 保证幂等。迁移可重复执行（IF NOT EXISTS /
ON CONFLICT DO NOTHING），同时 seed 菜单（id 800+）与默认策略行
`bsc-rb-paper`。

## 桥接机制（app/tasks/strategy_bridge.py，interval 15s）

正向：
- 发现 `run-*/signals.jsonl`、`run-*/trades.jsonl`、
  `executor/ledger/executions.jsonl` 与 `run.log`；文件不存在空转不报错。
- 每个文件按偏移量增量读取（只处理完整行，末尾半行下轮再读），偏移量
  持久化在 Redis hash `base:trading:bridge:offsets`，崩溃续传。
  文件截断/轮替（偏移 > 文件大小）时从头重读，靠 dedupe_key 保持幂等。
- run.log 的 `HEARTBEAT block=… chain_ts=…` 行解析落 rb_heartbeats。
- 只读 postgrad-signal-lab 的文件，除 control.json 外不写。

反向：
- 每轮读 rb_strategies 第一行，与 control.json 内容比较，不一致则
  tmp + rename 原子重写（bot 每 10s 重读）。
- 后台策略页 doEdit 落库后立即同步一次（保存即下发），不等桥接轮。

告警（Telegram，settings 表 category=notify / name=telegram 配
`{"bot_token","chat_id"}`，未配置静默跳过）：
- 策略 mode 变更（doEdit 时）；
- 最新心跳 cum_pnl_usd 击穿 params.daily_loss_breaker_usd（每日一次）；
- 桥接连续 3 轮失败（每小时最多一次，Redis 计数
  `base:trading:bridge:consecutive_failures`）。

## 接口（/api/admin，perms_prefix admin:trading）

- `trading/trade/*`、`trading/position/*`、`trading/strategy/*`、
  `trading/heartbeat/*` — crud_router 标准 getList/getDetail/doEdit/doDelete
- `trading/dashboard/stats` — 今日/累计信号、交易、胜率、PnL、
  熔断状态、最新心跳
- `trading/dashboard/equity` — 按出场时间的累计 PnL 资金曲线

## 前端页面（admin/src/views/trading/，菜单 id 800+）

| 菜单 | template_path | 说明 |
| --- | --- | --- |
| 交易看板 | trading/dashboard/index | 统计卡（今日/累计、胜率、PnL、熔断）、echarts 资金曲线、最新心跳 |
| 交易记录 | trading/trade/index | CrudTable：时间、池、token、方向、金额、PnL、退出原因、来源筛选，只读 + 导出 |
| 当前持仓 | trading/position/index | 默认过滤 status=open，持仓数/金额/浮盈汇总卡 |
| 策略控制 | trading/strategy/index | mode 三档切换 + 五个核心参数 + JsonEditor 全量参数，保存即下发 control.json（LIVE 需二次确认） |

## 启动

```bash
# 迁移
psql -h localhost -U base_user -d base -f serve/databases/migrations/016_trading_module.sql

# 后端 + worker（桥接任务随 worker 自动加载）
./start.sh        # 或分别：uvicorn app.main:app / python -m app.worker

# 前端
cd admin && npm run dev
```

echarts 已加入 admin 依赖（按需引入 LineChart + Grid/Tooltip/MarkLine）。
