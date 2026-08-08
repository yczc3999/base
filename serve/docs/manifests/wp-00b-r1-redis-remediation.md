# COMPLETION MANIFEST — WP-00b-r1 · Redis 基础不变量整改

- Work package: `WP-00` 整改子任务 `WP-00b-r1`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00b`（审查 REMEDIATION_REQUIRED，4 项缺陷）
- 规范依据: `serve/docs/performance-cache-database-design.md` §5/§8.2/§11 → `serve/docs/v2-implementation-contract.md` §3/§13–§15 → `serve/docs/manifests/wp-00b-redis.md` → `serve/docs/tasks/wp-00b-r1-redis-foundation-remediation.md`
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/services/redis_keys.py` | **新增** | 唯一共享 key 编码原语 `encode_key_segment` / `build_redis_key` / `decode_key_segment` |
| `serve/app/services/redis_control.py` | 修改 | `_key` 走共享编码器；CAS Lua 区分 EXPECT_MISSING/EXPECT_VALUE；lease TTL 毫秒校验 |
| `serve/app/services/redis_cache.py` | 修改 | `_key` 走共享编码器；移除裸 `pipeline()`，新增 typed `BatchSet`/`BatchDelete` + `execute_batch`；`effective_ttl` 半开区间 |
| `serve/tests/trading/test_v2_redis_keys.py` | **新增** | 9 个纯函数编码测试 |
| `serve/tests/trading/test_v2_redis_control.py` | 修改 | cleanup 用 `build_redis_key`；CAS 四路径；sub-ms lease 拒绝；共享编码器断言 |
| `serve/tests/trading/test_v2_redis_cache.py` | 修改 | cleanup 用 `build_redis_key`；batch 测试；半开 jitter；无 raw pipeline 断言 |
| `serve/docs/manifests/wp-00b-r1-redis-remediation.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` | 修改 | 00b-r1 标 DONE（待审） |
| `serve/docs/tasks/README.md` | 修改 | 审查记录追加 00b-r1 DONE（任务保持当前，不推进依赖链） |

范围外未动：`config.py`、`.env.example`、Base `services/redis.py`、queue/worker、Artifact、`main.py`、V1 文件。

---

## 2. 实现内容

### 2.1 `redis_keys.py` — 唯一 key 编码（缺陷 1 关闭）

- `encode_key_segment(value)`：可读百分号编码。安全字符 `[A-Za-z0-9._-]` 保留；其余字符按 UTF-8 字节逐一 `%XX`（大写 hex）。`:` → `%3A`、`%` → `%25`、空格 → `%20`、斜杠 → `%2F`、Unicode 逐字节编码。
- 构造性无碰撞：编码段不含裸 `:`，段间用 `:` 分隔，解码按 `:` 切分可无歧义还原；`%` 必被编码，不存在转义歧义。
- `build_redis_key(namespace, *segments)`：namespace 与全部动态 segment 统一编码后 `:` 拼接。
- `decode_key_segment`：逆编码（测试/审计用）。

### 2.2 `redis_control.py`（缺陷 1/3/4 关闭）

- `_key(*parts)` → `build_redis_key(namespace, *parts)`；Control/Cache 无复制逻辑。
- **CAS Lua**：新增 `expect_missing` 显式参数。`expected=None` → `expect_missing='1'` 只匹配键不存在；`expected=""` → `expect_missing='0'` + `expected=''` 正确比较已有空串；普通值同 EXPECT_VALUE 路径。不再用空串哨兵。
- **lease TTL**：`acquire_lease`/`renew_lease` 在 `int(ttl_s*1000) < 1` 时、发 Redis 命令前抛 `ValueError`（"lease TTL must be >= 1ms"）；`ttl_s<=0` 仍拒绝。

### 2.3 `redis_cache.py`（缺陷 1/2/4 关闭）

- `_key(version, name)` → `build_redis_key(namespace, version, name)`。
- **移除裸 `pipeline()`**；新增 frozen typed 操作 `BatchSet(name, value, version, ttl_s=None)`、`BatchDelete(name, version)`（NamedTuple）与 `execute_batch(ops) -> list[bool]`：
  - 每个 SET 在加入内部 pipeline 前完成 canonical JSON、versioned key、有限 TTL+jitter 校验；程序错误（TypeError/ValueError）立即抛出，无部分执行；
  - 调用方无法取得底层 pipeline/client；
  - 连接故障按 bypass 返回 `[False]*len(ops)`；bypass=False 抛 `RedisError`。
- CAS 继续原子执行，使用相同 `_key` 编码 + 有限 TTL。
- **`effective_ttl(base, jitter)` 半开区间**：`jitter=0` → base；否则 `base + random.randrange(jitter)` ∈ `[base, base+jitter)`（不含上界）。

---

## 3. 命令与真实结果

```bash
# 1) redis-cli ping
redis-cli ping
# → PONG

# 2) compileall
python3 -m compileall -q app tests
# → exit 0，无输出

# 3) key 编码测试（纯函数）
.venv/bin/pytest -q tests/trading/test_v2_redis_keys.py
# → 9 passed in 0.01s

# 4) Control Redis 测试
.venv/bin/pytest -q tests/trading/test_v2_redis_control.py
# → 13 passed in 0.32s

# 5) Cache Redis 测试
.venv/bin/pytest -q tests/trading/test_v2_redis_cache.py
# → 22 passed in 0.13s

# 6) tests/trading（含 WP-00a 31 + WP-00b 26 + WP-00b-r1 18）
.venv/bin/pytest -q tests/trading
# → 75 passed in 0.51s

# 7) 全量回归（Base + V2）
.venv/bin/pytest -q
# → 286 passed, 1 warning in 2.66s
#   （1 warning = tests/conftest.py 既有 event_loop 重定义弃用警告）

# 8) git diff --check
git diff --check
# → 无输出，exit 0

# 9) 测试 key 残留
redis-cli --scan --pattern 'pm:it:*' | wc -l
# → 0
```

---

## 4. 四项缺陷逐项关闭证据

### 缺陷 1（P1，key 可碰撞）— 已关闭

- 审查复现用例：`build_redis_key(ns, "a:b", "c") != build_redis_key(ns, "a", "b:c")`（`test_collision_regression_exact_review_case`，以及 Control/Cache 各一条 `_key` 级断言）。
- 段拆分穷举无碰撞：多组冒号/空串/百分号/空格/斜杠/中文/组合字符段，两两拼接无重复 key。
- Unicode 无碰撞：`("市场") != ("市","场")`；组合字符 `e+U+0301 != é`。
- `%` 必被编码：`encode("a%3Ab") == "a%253Ab"`；编码后任意 `%` 后必为两位合法 hex。
- 解码可逆：8 类样本 `decode(encode(s)) == s`。
- Control/Cache 均经 `build_redis_key`（`test_control_uses_shared_encoder` / `test_cache_uses_shared_encoder`），无复制逻辑。

### 缺陷 2（P1，裸 pipeline）— 已关闭

- `test_no_raw_pipeline_api`：`not hasattr(c, "pipeline")`，`execute_batch` 存在，`BatchSet/BatchDelete` 字段冻结。
- `test_batch_set_delete_order_and_results`：SET 两个 → `[True,True]`，可读回；DELETE 两个 → `[True,True]`，读回 None。
- `test_batch_enforces_finite_ttl_and_versioned_key`：`ttl_s=0` → `ValueError`（永久 TTL 禁止）；`set` 对象 → `TypeError`；写前抛错无残留；同名不同 version 隔离。
- `test_batch_bypass_on_error`：连接故障返回 `[False, False]`（与操作数量一致）。
- `test_batch_rejects_unknown_op_type`：裸 tuple 抛 `TypeError`。
- `test_batch_set_applies_ttl_jitter`：pttl ∈ `[base, base+jitter)` 秒级。

### 缺陷 3（P1，CAS 空串歧义）— 已关闭

`test_cas_four_paths` 覆盖四条路径：
1. `expected=None` 键不存在 → 成功；
2. `expected="v1"` 当前是 v1 → 成功；
3. 冲突（当前非 v1）→ 失败；
4. `expected=None` 但键已存在 → 失败；
5. 已有空串被 `expected=""` 正确比较（先写空串，再 `compare_and_swap(name, "", ...)` 成功）。

### 缺陷 4（P2，时间边界不一致）— 已关闭

- `test_effective_ttl_bounds_and_validation`：200 次 `100 <= t < 130`（严格不含上界）；`jitter=0 → 60`；非法值抛 `ValueError`。
- `test_effective_ttl_half_open_never_hits_upper_bound`：`effective_ttl(10, 5)` 2000 次 ∈ `[10,15)`，15 从未出现。
- `test_lease_sub_ms_ttl_rejected_before_network`：`acquire_lease(..., 0.0001)` 与 `renew_lease(..., 0.0001)` 在发 Redis 命令前抛 `ValueError`（"1ms"）；`0.001`（1ms 边界）正常获取。
- 真实 Redis `pttl` 断言同步为半开区间（`300_000 <= pttl < 330_000` 等）。

### 回归

- WP-00a/00b 原测试全部保留并通过（tests/trading 75 = 31 + 26 + 18）。
- 全量 Base 回归 286 passed 无退化。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞说明：
- Control/Cache `_key` 现为编码 key，调试可读性下降属预期；`decode_key_segment` 可还原。
- `execute_batch` 仅支持 SET/DELETE（任务要求"至少"）；后续 WP 如需可扩展 GET 等 typed op。
- 模块级单例仍未接入 `main.py`（lifespan 留 WP-00d）。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/services/redis_control.py serve/app/services/redis_cache.py \
  serve/tests/trading/test_v2_redis_control.py serve/tests/trading/test_v2_redis_cache.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/app/services/redis_keys.py serve/tests/trading/test_v2_redis_keys.py \
  serve/docs/manifests/wp-00b-r1-redis-remediation.md
```

- 回到 `WP-00b` 已提交状态（redis_keys.py 新增、client 移除 pipeline 等全部撤销）。
- 无 schema、无密钥、无生产数据改动；测试只写随机 namespace 且残留为 0，回滚无副作用。
- 若已提交，先 `git reset --hard <WP-00b 提交 SHA>` 再执行上述删除（按需）。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00b-r1-redis-remediation.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算，与存储值无关）：

```text
bdb27c6de1079ea6a8383c01b495b79072d9d91588f72c1b87a00cdfd469cf32
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00b-r1-redis-remediation.md | sha256sum
```
