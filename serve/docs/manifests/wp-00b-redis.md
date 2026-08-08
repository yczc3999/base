# COMPLETION MANIFEST — WP-00b · Control Redis / Cache Redis

- Work package: `WP-00` 子任务 `WP-00b`
- 状态: **DONE**（测试、数据约束、manifest 均通过；不涉及迁移）
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 规范依据: `AGENTS.md` → `serve/docs/performance-cache-database-design.md` §3/§5/§8.2/§11/§12/§14/§15 → `serve/docs/v2-implementation-contract.md` §3/§12–§15 → `serve/docs/manifests/wp-00a-config-database.md`
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/config.py` | 修改（+~180） | 新增 V2 Redis 双角色 typed 端点（`RedisEndpoint`/`ControlRedisEndpoint`/`CacheRedisEndpoint`）+ Settings 字段 + namespace 组装 |
| `serve/.env.example` | 修改 | 新增 V2 namespace / Control / Cache 配置段 |
| `serve/app/services/redis_control.py` | 新增 | Control Redis 客户端：Stream / lease / fencing / CAS / health，fail-closed |
| `serve/app/services/redis_cache.py` | 新增 | Cache Redis 客户端：versioned key / canonical JSON / 强制 TTL+jitter / pipeline / CAS，故障降级 |
| `serve/tests/trading/test_v2_redis_control.py` | 新增 | 11 个真实 Redis 原子性测试 |
| `serve/tests/trading/test_v2_redis_cache.py` | 新增 | 15 个真实 Redis 集成测试 |
| `serve/docs/manifests/wp-00b-redis.md` | 新增 | 本 manifest |
| `serve/docs/manifests/README.md` | 修改 | 索引：WP-00 IN PROGRESS；WP-00b DONE；WP-00c/00d pending；WP-01A 保持 pending |

范围外未动：`serve/app/services/redis.py`（Base）、`serve/app/queue.py`、`serve/app/worker.py`、`app/main.py`（未接入，lifespan 留 WP-00d）、V1 代码、其他业务代码。

---

## 2. 实现内容

### 2.1 `app/config.py` — V2 Redis 双角色配置

- 新增 `RedisEndpoint`（typed）：`url / max_connections / connect_timeout_s / read_timeout_s / health_check_interval_s / namespace`。
- `ControlRedisEndpoint`（fail-closed）与 `CacheRedisEndpoint`（可丢：额外 `default_ttl_s / ttl_jitter_s / bypass_on_error`）继承之。
- Settings 新增字段：
  - `REDIS_ENV=prod`、`REDIS_SCHEMA_VERSION=v2` → `redis_namespace = pm:{schema_version}:{env}`。
  - `REDIS_CONTROL_*`（URL/host/port/db/password/max_connections/connect/read timeout/health-check）默认 DB 0。
  - `REDIS_CACHE_*`（同上 + TTL=300 / jitter=30 / bypass=true）默认 DB 1。
- 属性 `control_redis_endpoint` / `cache_redis_endpoint` 组装独立 endpoint；namespace 分别为 `pm:v2:prod:control` / `pm:v2:prod:cache`（不同 DB **且**不同 namespace 双隔离）。
- 字段约束：`max_connections≥1`、timeouts>0、`default_ttl_s≥1`、`jitter≥0`。

### 2.2 `app/services/redis_control.py` — Control Redis（fail-closed）

- 独立连接池：`redis.asyncio.Redis.from_url` 带 `max_connections / socket_connect_timeout / socket_timeout / health_check_interval`，`decode_responses=True`。
- **Lua 原子原语**（服务端原子，无 TOCTOU）：
  - `_LEASE_ACQUIRE`：`EXISTS` 检查 + `INCR` 单调 fencing + `SET PX`（带 owner:fence）。
  - `_LEASE_RENEW` / `_LEASE_RELEASE`：`string.match` 校验 owner+token，非 owner 返回 0，已过期返回 -1。
  - `_CAS`：GET 比较（`expected=None` 空串哨兵=期望"不存在"），匹配则 `SET [EX]`。
- `LeaseHandle`（name/owner/token/ttl_s）；`acquire_lease / renew_lease / release_lease / fencing_token`。
- `compare_and_swap(name, expected, new, ttl_s=0)`：ttl_s<=0 不过期。
- Streams：`stream_add / stream_read / stream_len / stream_trim`（xadd/xread/xlen/xtrim）。
- `health()`：故障只上报 `ok=False` 不抛（监控用）；其余操作故障抛 `RedisError` —— **fail-closed**。
- `aclose()` 幂等。无 `scan / delete_pattern` API。

### 2.3 `app/services/redis_cache.py` — Cache Redis（故障降级）

- 独立连接池 + 独立 namespace。
- **versioned key**：`{namespace}:{version}:{name}`，`get/set/delete/cas` 均显式带 version。
- **canonical JSON**：`sort_keys + separators=(",",":") + ensure_ascii=False`，**不传 default=**——非 JSON 可序列化（set/datetime/自定义对象）抛 `TypeError`（程序错误，不吞）。
- **强制有限 TTL**：无 TTL 用 `default_ttl_s`；`ttl<=0` 抛 `ValueError`（永久 TTL 禁止）；`effective_ttl(base,jitter)=base+U[0,jitter)`。
- `pipeline()` 返回 redis-py pipeline；`cas()` Lua 原子 CAS（canonical JSON 比较）。
- **故障降级**：`RedisError/OSError` 在 `bypass_on_error=True` 时吞掉（get→None、写→False）；程序错误（TypeError/ValueError）仍抛。
- `aclose()` 幂等。无 `scan / delete_pattern` API。

### 2.4 测试（26 个，真实 Redis）

- **control**（11）：pool 隔离、namespace 隔离（同 DB 跨角色）、lease 竞争、非 owner renew/release 拒绝、错误 token 拒绝、fencing 单调递增、lease 过期重取（fencing 递增）、CAS 成功/冲突、Stream 写读、fail-closed（连接被拒抛 RedisError + health ok=False）、aclose 幂等、无 SCAN API。
- **cache**（15）：get/set/delete 往返、versioned key 隔离、canonical JSON 稳定、TTL 必填（默认 300+jitter 界内）、永久 TTL 拒绝、`effective_ttl` 边界、非 JSON 拒绝（set/datetime/object）、namespace 隔离（同 DB）、DB 隔离（同 namespace）、pipeline、CAS 成功/冲突、bypass 降级、no-bypass 抛错、aclose 幂等、无 SCAN API、pool 隔离。
- 每个测试随机 namespace + 只删自己创建的精确 key；验收后 `pm:it:*` 残留为 0。

---

## 3. 命令与真实结果

```bash
# 0) 环境：真实 Redis 已运行（redis-py 8.1.0 / redis-server 8.10.0 / venv 已建）

# 1) redis-cli ping
redis-cli ping
# → PONG

# 2) compileall
python3 -m compileall -q app tests
# → exit 0，无输出

# 3) Control Redis 测试
.venv/bin/pytest -q tests/trading/test_v2_redis_control.py
# → 11 passed in 0.32s

# 4) Cache Redis 测试
.venv/bin/pytest -q tests/trading/test_v2_redis_cache.py
# → 15 passed in 0.12s

# 5) tests/trading（含 WP-00a 31 + WP-00b 26）
.venv/bin/pytest -q tests/trading
# → 57 passed in 0.48s

# 6) 全量回归（Base + V2）
.venv/bin/pytest -q
# → 268 passed, 1 warning in 2.64s
#   （1 warning = tests/conftest.py 既有 event_loop 重定义弃用警告，非本次引入）

# 7) git diff --check
git diff --check
# → 无输出，exit 0

# 8) 测试 key 残留检查
redis-cli --scan --pattern 'pm:it:*' | wc -l
# → 0
```

---

## 4. Redis 原子性及隔离证据

### 4.1 原子性（Lua 服务端执行）

- **lease**：acquire/renew/release 单条 Lua；`fencing` 由 `INCR` 严格单调。证据：三个 owner 依次取得 token `[1,2,3]` 且 `== sorted(tokens)`；A 持有期间 B 拿不到；B/错误 token 无法 renew/release；过期后 renew/release 均 False，重新获取 token 更大。
- **CAS**：单条 Lua 比较后写；`expected=None` 空串哨兵支持"期望不存在"。证据：首次创建成功、冲突失败、匹配成功。
- **Stream**：xadd→xread 往返 id 与内容一致；trim 后 len=0。

### 4.2 隔离

| 维度 | 配置 | 证据 |
|---|---|---|
| 连接池 | control 与 cache 各自 `from_url` | `control.pool is not cache.pool` |
| DB | control db=0 / cache db=1 | 同 namespace 不同 DB，`get` 互不可见 |
| namespace | `pm:v2:prod:control` vs `pm:v2:prod:cache` | 同 DB 不同 namespace，`get` 互不可见 |
| versioned key | cache `{ns}:{version}:{name}` | 同 name 不同 version 互不可见 |

### 4.3 故障语义

- **Control fail-closed**：指向 `127.0.0.1:1`（拒绝连接）的客户端，`ping/acquire_lease/cas/stream_add` 均抛 `RedisError`；`health()` 只返回 `ok=False` 不抛 → 调用方必须禁止增仓。
- **Cache bypass**：同地址 + `bypass_on_error=True`，`get→None / set/delete/cas→False`，不抛 → 回源降级；`bypass=False` 时抛 `RedisError`。
- 程序错误不吞：非法 TTL 抛 `ValueError`；非 JSON 对象抛 `TypeError`。

### 4.4 强制约束（禁止项均有测试断言）

- 永久 TTL → `ValueError`；`json.dumps(default=str)` 不存在（canonical JSON 无 default）；无 `scan/delete_pattern` API；不保存 secret（无 secret 字段/API）；不保存订单/账本/资金/权限/策略权威状态（客户端仅基础设施原语）；Redis ACK 不视为业务完成（Stream 仅原语，无 ack→完成逻辑）。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞说明：
- `redis_control.control_redis` / `redis_cache.cache_redis` 模块级单例已建，但按 WP-00b 要求**未接入 `main.py`**；lifespan 初始化/释放统一留 WP-00d。
- Base `app/services/redis.py` 未改动、未使用；V2 热路径只走新 control/cache 客户端。
- 本机 Redis 为开发共享实例（DBSIZE 28，含 Base 既有 key）；测试用随机 namespace 且只删自己 key，验证后 `pm:it:*` 残留 0。
- `tests/conftest.py` 的 `event_loop` 重定义弃用警告为既有代码，不在允许文件内。

---

## 6. 回滚方式

```bash
# 已提交时回退到上一提交
git checkout -- serve/app/config.py serve/.env.example
git rm -f serve/app/services/redis_control.py serve/app/services/redis_cache.py \
  serve/tests/trading/test_v2_redis_control.py serve/tests/trading/test_v2_redis_cache.py
git checkout -- serve/docs/manifests/README.md   # 或按索引手工还原
git rm -f serve/docs/manifests/wp-00b-redis.md
```

- 无 schema 变更、无真实密钥、无生产数据写入（测试只写随机 namespace 且已清理），回滚无副作用。
- 配置回滚会移除 `REDIS_CONTROL_*`/`REDIS_CACHE_*` 字段；Base 的 `REDIS_*` 保留不受影响。
- 若后续接入 lifespan 后回滚，需同步撤掉 `main.py` 中对 control/cache 的引用（本次未接入，无此问题）。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00b-redis.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算，与存储值无关）：

```text
fd1ff118254c440deb1f62c33dec5542e7e130c743d4f0106a96c258211775ef
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00b-redis.md | sha256sum
```
