# Polymarket 接入实现设计

> 状态：首版施工规范。核验日期：2026-08-08。本文规定 V2 如何发现市场、维护行情、
> 下单、对账和结算；业务判断链以 `/code/pollymarket/docs/v2/ARCHITECTURE.md` 为准。
> 热路径、缓存、数据库和容量约束见
> [`performance-cache-database-design.md`](performance-cache-database-design.md)。

## 1. 结论与边界

Polymarket 接入按一个闭环实现：

```text
Gamma 全量发现
→ contract/rules 与 ID 归一
→ CLOB REST 建立订单簿基线
→ Market WS 降低行情延迟
→ 决策绑定可执行 book snapshot
→ 交易前再次校验市场/账户/权限
→ CLOB V2 签名下单
→ User WS 实时跟踪 + REST 对账定案
→ Polygon/CTF 结算与兑换
```

设计原则：

- **Gamma REST 是市场全集权威源**；`new_market` WS 只负责早发现，不能替代全量扫描。
- **CLOB REST 是可交易状态和订单簿基线**；WS 是低延迟增量，断线后不能猜测补齐。
- **本地 PostgreSQL 是决策、订单和账本事实源**；Redis、WS 和日志都不是账本。
- **Data API 只用于仓位/活动交叉核对**，不能替代本地成交与现金账。
- **Polygon/CTF payout 是最终结算事实**；`closed=true` 本身不等于可兑换。
- 首版只接 Polygon Mainnet、CLOB V2 和 pUSD；旧 CLOB V1 不兼容，也不做兼容层。

## 2. 外部接口与 Driver

| Driver | 地址 | 职责 |
|---|---|---|
| `GammaDriver` | `https://gamma-api.polymarket.com` | 事件、市场、规则、状态和 token 映射 |
| `ClobPublicDriver` | `https://clob.polymarket.com` | book、price、tick、fee、server time、市场配置 |
| `ClobTradingDriver` | 同上 | L1/L2 鉴权、下单、撤单、订单与成交查询 |
| `ClobMarketWsDriver` | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | 公共订单簿与市场生命周期事件 |
| `ClobUserWsDriver` | `wss://ws-subscriptions-clob.polymarket.com/ws/user` | 私有订单与成交事件 |
| `DataApiDriver` | `https://data-api.polymarket.com` | 仓位、活动、历史成交的交叉核对 |
| `GeoBlockDriver` | `https://polymarket.com/api/geoblock` | 执行地域权限硬门 |
| `PolygonDriver` | 配置化 RPC | payout、余额、allowance、receipt |
| `RelayerDriver` | `https://relayer-v2.polymarket.com` | Deposit/Safe/Proxy gasless 链上操作 |

Driver 只负责协议、签名、超时、限流和响应归一；业务状态转换在 Logic。每次调用都写
`external_call_attempt`，保存 endpoint、方法、请求/响应 hash、HTTP 状态、延迟、限流头和错误码。
密钥、签名原文和认证头只保存脱敏摘要。

## 3. ID 与权威数据模型

这些 ID 不得混用：

```text
Gamma event.id       1 ── N Gamma market.id
Gamma market.id      1 ── 1 conditionId
conditionId          1 ── 2 outcome/token
outcome index 0      = YES token_id
outcome index 1      = NO token_id
```

- `gamma_event_id`、`gamma_market_id` 是 Gamma 标识。
- `condition_id` 是 CTF 条件 ID，也是 CLOB/WS 字段 `market`。
- `token_id` 是超长十进制字符串，也是 CLOB 字段 `asset_id`；禁止转浮点或 64 位整数。
- Gamma 的 `outcomes`、`outcomePrices`、`clobTokenIds` 是 JSON 字符串数组；保存原值，
  再按同一索引解析。首版要求恰好两个 label 和两个 token，且 index 0/1 为 YES/NO。
- 用 `GET /markets-by-token/{token_id}` 反向核对 primary=YES、secondary=NO；不一致进入
  `ID_MAPPING_CONFLICT`，禁止预测和交易。

数据库硬约束：

```text
UNIQUE(source, gamma_event_id)
UNIQUE(source, gamma_market_id)
UNIQUE(chain_id, condition_id)
UNIQUE(chain_id, token_id)
UNIQUE(contract_spec_id, outcome_index)
UNIQUE(contract_spec_id, token_id)
CHECK(outcome_index IN (0, 1))
```

`contract_snapshot` 保存 Gamma 原始 JSON、问题、规则、resolution source、开始/结束时间、
clarification、状态和内容 hash；`contract_spec` 是解析后的不可变语义版本。任何规则或 token
映射变化创建新版本，旧 forecast 不回写。

## 4. 市场发现与生命周期

### 4.1 全量扫描

每轮创建 `universe_frame`，使用 keyset 分页直至 `next_cursor` 结束：

```http
GET /events/keyset?closed=false&limit=500&after_cursor=...
GET /markets/keyset?closed=false&limit=100&after_cursor=...
```

处理顺序：

1. 保存每页 raw response、cursor、observed_at 和 hash；
2. upsert 实体当前投影，同时追加 `market_version`；
3. 为首次发现市场创建 cohort membership；
4. 对已有市场做状态/rules/token diff；
5. 扫描完成后才提交 frame；中途失败保留 `INCOMPLETE`，不得当作完整 universe；
6. 下一轮重新从首 cursor 扫描，不从失败 cursor 假装完成。

`new_market` WS 事件只触发 `GET /markets/{id}` 定向刷新；下一轮 Gamma 全量扫描仍必须发现它。
`market_resolved` 只触发结算核验，不直接生成最终 label。

### 4.2 状态定义

市场投影至少保存：`active、closed、archived、accepting_orders、enable_order_book、
start_at、end_at、closed_at、neg_risk、updated_at`。

增加敞口的硬条件必须同时满足：

```text
active = true
closed = false
archived != true
accepting_orders = true
enable_order_book = true
now < end_at（若存在）
contract_spec 当前有效
```

任一字段缺失按未知处理，不得把“未失败”当“通过”。`REDUCE/CLOSE/CANCEL` 使用独立的
降险权限，不被增加敞口 Gate 阻断，但仍服从官方 geoblock/cancel-only 状态。

## 5. 行情：REST 基线 + WS 增量

### 5.1 REST

主要接口：

| 接口 | 用途 |
|---|---|
| `GET /book?token_id=` / `POST /books` | 完整订单簿；批量最多 500 |
| `GET /price?token_id=&side=BUY|SELL` | Provider wire：BUY=最高 bid，SELL=最低 ask；不得把参数名直接当作本系统动作方向 |
| `GET /tick-size?token_id=` | 当前 tick |
| `GET /fee-rate?token_id=` | 便捷 fee 参数 |
| `GET /clob-markets/{condition_id}` | min size、tick、fee、delay 等市场配置 |
| `GET /time` | CLOB Unix 时间，检测本机时钟偏差 |

`/book` 保存 `market、asset_id、timestamp、hash、bids、asks、min_order_size、tick_size、
neg_risk、last_trade_price`。所有金额和价格使用 `Decimal`/6 位 base units，禁止 float。

官方资料在 book 排序和 `/price` 侧别描述上曾不一致。2026-08-10 的生产响应与 API Reference
均为 `/price BUY=best bid、SELL=best ask`；实现仍以完整 book 自行计算：

```text
best_bid = max(bids.price)
best_ask = min(asks.price)
```

禁止取数组第一项。`last_trade_price=0.5, side=""` 可能只是“从未成交”的默认值，不得作为报价。

### 5.2 Market WS

订阅：

```json
{"assets_ids":["TOKEN_ID"],"type":"market","initial_dump":true,"level":2,"custom_feature_enabled":true}
```

客户端每 10 秒发送文本 `PING`，必须收到 `PONG`。处理 `book、price_change、
last_trade_price、tick_size_change、best_bid_ask、new_market、market_resolved`；所有原始消息
先进入逻辑 `source_events`（物理实现为压缩 batch + material event index），再更新订单簿投影。

官方 WS 没有 sequence、resume token 或 replay。每个连接使用本地 `connection_epoch +
ingest_seq`，它只证明本地处理顺序，不证明上游无缺口。状态机：

```text
DISCONNECTED → CONNECTING → SYNCING → LIVE → STALE → RECONNECTING
```

- 启动/重连先将相关 token 标为 `SYNCING`；
- 重新订阅 `initial_dump=true`；收到每个 token 的完整 `book` 后原子替换本地簿；
- 超时未收到 snapshot 时用 REST `/book(s)` 建立明确 cutover；
- 在 `SYNCING/STALE` 期间禁止绑定新决策报价和增加敞口；
- 解析错误、交叉簿、非法负数、tick 变化未刷新或 freshness 超限均触发失效与重同步。

纯 quote/depth 变化只触发重新估值和配仓；forecast lease 仍有效时不重新调用 AI。

## 6. 账户、密钥与签名

### 6.1 密钥边界

- `signer_private_key`、L2 `apiKey/secret/passphrase`、RPC/Builder 凭据进入专用加密 vault；
- 通用 settings、Redis、任务 payload、日志、Trace、Admin API 均不得出现明文；
- 只有 execution worker 可解密 signer/L2 凭据；market/research/forecast worker 无权限；
- Shadow 不加载任何交易凭据；Canary 与 Live 使用独立 permission manifest；
- secret 轮换创建新版本，历史记录仅引用 `secret_ref/version`。

### 6.2 L1 与 L2

L1 用 chainId 137 的 `ClobAuth` EIP-712 证明 signer 控制权，然后：

```http
POST /auth/api-key
GET  /auth/derive-api-key
```

私有请求 L2 签名：

```text
message = unix_seconds + UPPERCASE_METHOD + PATH_WITHOUT_QUERY + EXACT_BODY_OR_EMPTY
signature = base64url_pad(HMAC_SHA256(base64decode(secret), message))
```

发送 `POLY_ADDRESS、POLY_SIGNATURE、POLY_TIMESTAMP、POLY_API_KEY、POLY_PASSPHRASE`。
签名必须基于最终发送的同一 body bytes；先调用 `/time`，时钟偏差超过阈值则停止提交。

生产是 CLOB V2。订单 EIP-712 domain 为 `Polymarket CTF Exchange/version=2/chainId=137`，
verifying contract 按 `neg_risk` 选择 Standard 或 NegRisk Exchange。支持钱包类型
`EOA(0)、POLY_PROXY(1)、GNOSIS_SAFE(2)、DEPOSIT_WALLET(3)`。type 3 的 ERC-7739 包装
只使用锁定版本的官方实现，不自行拼接。

首版选择：公共 REST/WS 直接接入；复杂 EIP-712/Deposit Wallet 签名封装在
`ClobTradingDriver` 内使用**锁定版本的官方 SDK**。同时以官方测试向量做独立验签，并记录
SDK 版本、最终 wire body hash 和 order hash，避免 SDK 成为不可观察黑盒。

## 7. 余额、授权与执行权限

启动和每次权限切换检查：

- pUSD 对 Standard Exchange、NegRisk Exchange 的 ERC-20 allowance；
- Conditional Tokens 对两个 Exchange 的 ERC-1155 operator approval；
- 账户 pUSD、token 余额和被 open orders 预留的数量；
- `GET /balance-allowance` 与链上余额一致性；更新授权后调用
  `/balance-allowance/update` 刷新 CLOB cache；
- `GET /api/geoblock` 结果、账户 close-only/cancel-only/post-only 状态；
- permission manifest 的 wallet、mode、authorized capital、类别和全局上限。

任何未知或冲突均 fail closed。系统不绕过地域限制。完全 blocked 时停止新单并按官方能力处理；
close-only 时只允许被证明会降低净敞口的动作。

## 8. 下单前快照与硬 Gate

每个 `economic_intent` 在**签名前**和**真正发送前**各执行一次同事务逻辑校验：

1. intent、decision、forecast submission、contract spec 和 permission 全部版本匹配；
2. forecast lease 未过期，invalidation condition 未触发；
3. 市场满足第 4.2 节全部状态条件；
4. book 为 `LIVE` 且不超过 `quote_ttl_ms`；tick/min size/negRisk/fee 配置仍一致；
5. 按多档深度 walk 得到可成交价格、容量、fee、slippage 和全成本 edge；
6. price/size 使用 Decimal 精确舍入且能通过当前 tick/min size；
7. balance、allowance、reserved amount、capital/risk caps 足够；
8. geoblock、kill switch、heartbeat、用户流和 REST reconciliation 均健康；
9. 同一 `decision_opportunity_id + action_role` 没有活动的增仓 intent；
10. 任何 contract/quote/permission 变化都使旧 preflight hash 失效。

这组 Gate 直接消除 V1 的事故路径：已结束市场、陈旧 quote、同一预测反复重开、
`unreviewed` 被当 pass。禁止“先结算旧仓，再用旧 signal 立即开同一市场”。

## 9. 订单协议与本地状态机

### 9.1 Wire contract

`POST /order` 发送已签名订单、API key owner、TIF 和 post-only。支持：

- GTC：存续至成交/撤单；
- GTD：有到期时间；按官方 60 秒安全阈值，目标寿命 N 秒使用 `now+60+N`；
- FAK：立即成交可得部分，余量取消；
- FOK：立即全部成交，否则零成交；
- post-only 只适用于 GTC/GTD。

批量 `POST /orders` 每批最多 15，逐项成功/失败，**不是原子操作**。默认首版单笔提交；
只有同组件执行计划明确接受 leg risk 时才启用批量。

BUY/SELL 的 maker/taker amount 按官方 V2 规则转换为 6 位整数。订单不携带 fee rate；fee
在 match 时由平台配置应用。预估公式为：

```text
fee = shares × feeSchedule.rate × price × (1-price)
```

maker 不收费；taker fee 取实时市场配置，不能把文档类别费率硬编码为真相。

### 9.2 幂等与未知结果

官方没有 `Idempotency-Key/client_order_id`。本地规则：

```text
client_intent_id UNIQUE
→ 生成 salt/timestamp/signature
→ 在发送前持久化 exact body hash + expected order hash
→ 状态 SUBMITTING
→ 调用 CLOB
```

超时/断连后进入 `SUBMIT_UNKNOWN`，先按 order hash、open orders 和 recent trades 查询；
禁止换 salt/timestamp 盲重发。只有官方明确返回 `order timed out` 且确认未进 book 的情形，
才创建关联的新 attempt。HTTP 200 也必须同时检查 `success` 和 `errorMsg`。

本地订单状态：

```text
APPROVED → SIGNED → SUBMITTING
  ├→ LIVE → CANCELLING → CANCELED
  ├→ MATCHED/DELAYED/UNMATCHED
  ├→ REJECTED
  └→ SUBMIT_UNKNOWN → RECONCILING → 上述确定状态
```

部分成交由 `0 < size_matched < original_size` 推导，未成交余量仍为 live。交易状态按
`MATCHED[_NOT_BROADCASTED] → MINED → CONFIRMED`；`RETRYING` 为暂态，`FAILED` 为终态。
MATCHED 时冻结预期仓位，只有 CONFIRMED 才记最终 position/cash ledger；FAILED 做反向冲销。

撤单接口：`DELETE /order`、`/orders`、`/cancel-market-orders`、`/cancel-all`。批量撤单
首版最多 1000，逐项处理 `canceled/not_canceled`，重复撤单按 REST 实际状态收敛。

## 10. User WS、heartbeat 与故障恢复

User WS 初始订阅：

```json
{"auth":{"apiKey":"…","secret":"…","passphrase":"…"},"type":"user"}
```

省略 `markets` 以接收账户全部订单/成交；客户端每 10 秒 `PING`。原始 `order/trade`
事件 append-only 落库，按外部 order/trade ID 幂等，随后更新投影。

User WS 不提供断线 replay。任何断线后：

1. execution 状态转 `RECONCILING`，暂停增加敞口；
2. 重连订阅，但暂不把 WS 视作完整；
3. 分页拉 `GET /data/orders` 全部 open orders；
4. 按 watermark 窗口拉 `GET /data/trades`，并查询状态未知的单单；
5. 与本地订单、reserved amount、positions、cash ledger 对账；
6. 差异为零且保存 reconciliation manifest 后恢复 `LIVE`。

订单 dead-man switch 与 WS PING 是两回事。当前 V2 使用 `POST /v1/heartbeats`：首次空
`heartbeat_id`，之后回传服务端轮换的最新 ID；每 5 秒发送。心跳失败立即停止新单并触发
cancel/reconcile，不能等进程恢复后假定旧挂单仍在。

进程启动、leader 切换和崩溃恢复都先执行同一 REST reconciliation。一个账户同一时刻只允许
一个 heartbeat leader 和一个 execution leader，以数据库租约/fencing token 保证。

## 11. 结算、NegRisk 与兑换

### 11.1 payout 核验

二元市场的 YES/NO 是 ERC-1155 token；完整一对由 1 pUSD 抵押。正常 payout 为 1/0；
官方异常结果 `Unknown/50-50` 为 0.5/0.5，不使用自行发明的通用 void 语义。

自动标记 `final_admissible/redeemable` 前同时核验：

1. Gamma/CLOB `closed=true && acceptingOrders=false`；
2. CTF 已记录 payout numerators/denominator；
3. Data API position `redeemable=true`；
4. 链上 payout 与 CLOB `winner/is_50_50_outcome` 一致；
5. contract rules/clarification snapshot 已完成 label audit。

不一致进入 `SETTLEMENT_CONFLICT`，不评分、不学习、不自动兑换。

### 11.2 split / merge / redeem

- Standard 使用 `CtfCollateralAdapter`；NegRisk 使用 `NegRiskCtfCollateralAdapter`；
- split：`1 pUSD → 1 YES + 1 NO`；
- merge：等量 YES+NO → pUSD；
- redeem：链上已有 payout 后兑换该钱包该 condition 的全部 outcome 余额；
- redeem 对 `wallet + condition_id` 加互斥锁；只有 CONFIRMED receipt 后更新余额；
- timeout 后先查 relayer transaction、nonce、receipt 和余额，禁止盲重发。

NegRisk 事件仍由多个二元 market 构成。保存 `event_id、negRisk、negRiskAugmented、
named/placeholder/Other`；placeholder 未命名前不交易，首版默认不交易 Other。首版支持其行情、
下单、split/merge/redeem；`NO → 其他 YES` conversion 暂不启用，因为当前官方 Contracts 页只列
deprecated v1 adapter，待官方 V2 SDK/地址形成稳定接口后单独开 capability。

合约地址必须来自版本化 `contract_registry` 并在启动时核对 chainId/code hash，不能散落硬编码。
当前官方基线包括：

| 合约 | 地址 |
|---|---|
| Conditional Tokens | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| CTF Exchange | `0xE111180000d2663C0091e4f400237545B87B996B` |
| NegRisk Exchange | `0xe2222d279d744050d28e00520010520000310F59` |
| pUSD | `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` |
| CtfCollateralAdapter | `0xAdA100Db00Ca00073811820692005400218FcE1f` |
| NegRiskCtfCollateralAdapter | `0xadA2005600Dec949baf300f4C6120000bDB6eAab` |

## 12. Base Platform 文件落点

```text
serve/app/
├── services/polymarket/
│   ├── service.py                    # Driver factory；无业务状态机
│   ├── gamma_driver.py
│   ├── clob_public_driver.py
│   ├── clob_trading_driver.py
│   ├── market_ws_driver.py
│   ├── user_ws_driver.py
│   ├── data_api_driver.py
│   ├── polygon_driver.py
│   └── relayer_driver.py
├── models/trading/                   # 规范化实体、不可变 observation、订单/账本
├── repositories/trading/             # SQL/keyset/CAS；不做业务判断
├── logics/trading/
│   ├── universe.py                   # frame、diff、lifecycle
│   ├── market_data.py                # book 状态机与 quote binding
│   ├── account.py                    # balance/allowance/permission
│   ├── execution.py                  # preflight、intent、order 状态机
│   ├── reconciliation.py             # WS/REST/链上收敛
│   └── settlement.py                 # payout、label、redeem
├── handlers/trading/                 # event handler；一个 UoW 调用
└── runtimes/trading/                 # ingest、execution、reconcile、cognition、evaluation
```

Controller 只做查询、人工操作请求和权限校验；下单、撤单、重新对账必须进入 Logic/Job。
所有 exposure-increasing 操作在服务端检查 `trade:execute` 权限与有效 permission manifest，
隐藏前端按钮不构成授权。

## 13. 必要数据表

| 表 | 关键内容 |
|---|---|
| `pm_universe_frames/pages` | 扫描 cursor、完整性、raw artifact/hash |
| `pm_events/market_versions/token_versions` | Gamma/CLOB ID、状态、原始与解析字段 |
| `pm_connection_epochs/source_event_batches/index` | REST/WS 原始 batch、本地顺序、received_at/hash |
| `pm_book_checkpoints/pm_book_levels` | token、source time、observed time、hash、完整深度 |
| `pm_quote_bindings` | decision/intent 使用的 book、best bid/ask、depth walk、TTL |
| `pm_accounts` | signer/account wallet、wallet type、secret refs、permission |
| `pm_balance_allowance_snapshots` | 链上/CLOB 余额、spender、reserved amount、hash |
| `economic_action_intents` | action、price/size/TTL、preflight hash、唯一幂等键 |
| `exchange_order_attempts/orders/events` | exact body hash、order hash、状态历史、错误/重试 |
| `exchange_trades` | trade ID、order、price/size/fee、链上状态/tx |
| `position_lots/ledger_transactions/ledger_postings` | 仓位、双分录资金/资产、fee、冲销、对账 |
| `account_reconciliations` | WS watermark、REST pages、差异、manifest hash |
| `contract_registry/chain_operations` | 地址版本、split/merge/redeem、receipt/余额差 |
| `settlement_observations` | Gamma/CLOB/CTF/Data 各自结论与 conflict 状态 |

关键唯一约束：`client_intent_id`、`external_order_id`、`external_trade_id`、
`wallet+condition+active_redeem`、`account+heartbeat_leader`。状态转换和 outbox 同一数据库事务；
账本仅用追加 entry/reversal，不更新历史现金流。

## 14. 配置项

以下是 Polymarket 接入配置，不计入策略 66 项；全部 typed、带范围和版本：

| 分区 | 配置 |
|---|---|
| Endpoint | 8 个 base URL、chainId、RPC secret ref、contract registry version |
| Discovery | event/market page size、full scan、terminal refresh、timeout、并发、重试 |
| Market WS | level=2、initial dump、custom feature、ping=10s、stale、重连、subscription batch |
| Book | REST refresh、decision quote TTL、depth levels、crossed-book policy、Decimal precision |
| Account | wallet type、signer/L2 secret refs、clock skew、balance reserve、allowance margin |
| Orders | allowed TIF、default TIF、post-only、ack timeout、GTD lead、batch 15、cancel batch 1000 |
| User stream | ping=10s、reconcile lookback、reconcile interval、unknown-order timeout |
| Heartbeat | enabled、interval=5s、failure action、leader lease/fencing TTL |
| Settlement | poll interval、confirmations、relayer timeout、auto-redeem capability、conflict action |
| Limits | endpoint token buckets、429 backoff/jitter、provider circuit breaker |

官方 IP 限额作为 provider profile 默认值，但运行时同时读取 `429/Retry-After/rate-limit`
信号；运营配置只能比官方限额保守。limit 变更不应要求改代码。

## 15. 可观察、可追溯、可回放

后台必须能从一次 decision 一键展开：

```text
Gamma contract/rules/version
→ token/condition mapping
→ Market WS/REST book snapshot
→ forecast lease + decision + all-in cost
→ preflight 两次结果
→ signed body hash/order hash（不显示 secret/signature）
→ HTTP ack + User WS events + REST reconciliation
→ trade/tx/fee/position/cash ledger
→ resolution/payout/redeem
```

核心告警：universe frame 不完整、WS stale、book 重同步、mapping conflict、terminal-market intent、
quote/forecast 过期、clock skew、heartbeat miss、unknown submit、order/trade 对账差异、余额/账本不平、
settlement conflict、权限或配置漂移。

Shadow 回放使用封存的 raw REST/WS 与固定 clock；模拟成交必须按当时 book depth、延迟、fee 和
保守 fill policy，不把 midpoint 当成交。Canary 才能验证真实 queue/adverse selection；其偏差单列，
不得回写历史 Shadow fill。

## 16. 失败处理矩阵

| 情况 | 行为 |
|---|---|
| Gamma 422 | 配置/游标故障；frame 失败，不跳页 |
| Public REST 404 no book | 标记不可报价，刷新 Gamma 状态，不制造空簿 |
| WS 断线/PONG 超时 | token→SYNCING；全量 snapshot 后恢复 |
| 400 下单校验 | 不重试；记录 reason，重新决策或终止 |
| 401 | 停止私有调用；校时、key/HMAC/body 检查 |
| 425/429 | 尊重 Retry-After，指数退避+jitter |
| 500/网络超时提交 | `SUBMIT_UNKNOWN`，先对账；默认不重发 |
| 503 trading disabled | 停止新单；按官方状态处理撤单 |
| cancel-only/post-only | capability 降级并告警，不伪装正常 |
| heartbeat 失败 | 停止新单、主动撤单、REST 对账 |
| User WS 断线 | 暂停增仓，REST orders/trades 回补 |
| receipt/余额不一致 | 钱包暂停 Live，人工处理 reconciliation |
| settlement 三方冲突 | 不评分、不兑换、不学习 |

## 17. 分模式施工与验收

### Shadow

- 完成 Gamma 全量、ID 映射、REST+WS book、可执行 quote、费率与订单模拟；
- 不配置交易 secret，不调用任何私有下单/链上写接口；
- 断线重放、陈旧 quote、terminal market、重复 intent 等故障 fixture 全通过。

### Canary

- 先在 staging/签名向量验证协议，再以极小真实资本做 conformance；
- 验证 wallet type、allowance、四种 TIF（只启用批准子集）、部分成交、取消、heartbeat、
  unknown-submit、WS 断线 REST 回补和真实 fee；
- Shadow 与 Canary 使用相同经济 intent；执行授权 envelope 单独绑定。

### Live

- 只更换 capital permission，不更换预测/决策逻辑；
- 每次增资前重新验证容量、真实 slippage/fill、账本、回撤和自动回退；
- 任何 geoblock、heartbeat、账本、配置、签名版本或 reconciliation 硬故障自动退回 Shadow。

首版完成证据：固定 fixtures 的 Gamma 分页完整率 100%；两 token 映射冲突 0；WS 重连后 book
与 REST 一致；terminal market 增仓 0；同一 intent 重复订单 0；未知提交全部定案；订单/成交/
仓位/现金账差异 0；settlement conflict 不进入评分；每个外部调用和状态转换均可从后台回放。

## 18. 官方规范入口

- [API 总览](https://docs.polymarket.com/getting-started/api)
- [市场发现](https://docs.polymarket.com/market-data/discover-markets)
- [Market Details](https://docs.polymarket.com/market-data/market-details)
- [Prices and Order Books](https://docs.polymarket.com/market-data/prices-order-books)
- [Market WebSocket](https://docs.polymarket.com/api-reference/wss/market)
- [User WebSocket](https://docs.polymarket.com/api-reference/wss/user)
- [Wallets and Authentication](https://docs.polymarket.com/trading/wallets-auth)
- [Place Orders](https://docs.polymarket.com/trading/place-orders)
- [Manage Orders](https://docs.polymarket.com/trading/manage-orders)
- [Order Lifecycle](https://docs.polymarket.com/concepts/order-lifecycle)
- [Fees](https://docs.polymarket.com/trading/fees)
- [Negative Risk](https://docs.polymarket.com/concepts/negative-risk)
- [Resolution](https://docs.polymarket.com/concepts/resolution)
- [Contracts](https://docs.polymarket.com/resources/contracts)
- [Rate Limits](https://docs.polymarket.com/api-reference/rate-limits)
- [Error Codes](https://docs.polymarket.com/resources/error-codes)
