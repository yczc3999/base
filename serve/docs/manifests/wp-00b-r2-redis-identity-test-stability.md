# COMPLETION MANIFEST — WP-00b-r2 · Redis identity 与测试稳定性整改

- Work package: `WP-00` 整改子任务 `WP-00b-r2`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00b-r1`（审查 REMEDIATION_REQUIRED，4 项缺陷）
- 规范依据: `serve/docs/tasks/wp-00b-r1-redis-foundation-remediation.md` → `serve/docs/manifests/wp-00b-r1-redis-remediation.md` → `serve/docs/performance-cache-database-design.md` §5/§11 → `serve/docs/v2-implementation-contract.md` §3/§13–§15
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/services/redis_keys.py` | 修改 | identity 协议：`encode_key_segment` 只接受 str；新增 `validate_namespace`；`build_redis_key` 用 `:~:` 保留边界，namespace 不编码 |
| `serve/app/services/redis_control.py` | 修改 | 无（`_key` 复用 `build_redis_key`，签名不变；仅测试补 cleanup） |
| `serve/app/services/redis_cache.py` | 修改 | 无（`_key` 复用；仅测试补 tolerance/cleanup） |
| `serve/tests/trading/test_v2_redis_keys.py` | 修改 | 重写：类型严格、namespace 可读、边界唯一、跨 ns 无碰撞 |
| `serve/tests/trading/test_v2_redis_control.py` | 修改 | 1ms lease 测试 finally 清理 lease+fence+aclose；纯构造测试补 aclose |
| `serve/tests/trading/test_v2_redis_cache.py` | 修改 | 两个 PTTL 测试改 elapsed tolerance + 上界校验；纯构造测试补 aclose |
| `serve/docs/manifests/wp-00b-r2-redis-identity-test-stability.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` | 修改 | 00b-r2 标 DONE（待审） |
| `serve/docs/tasks/README.md` | 修改 | 审查记录追加 00b-r2 DONE（当前任务保持） |

范围外未动：`config.py`、`.env.example`、Base Redis、queue/worker、Artifact、`main.py`、V1 文件。R1 manifest 未改写（已冻结）。

---

## 2. 实现内容

### 2.1 `redis_keys.py` — identity 协议（缺陷 1/2 关闭）

- **`encode_key_segment(value)` 只接受 `str`**：非字符串立即抛 `TypeError`（"callers must str() explicitly to keep identity type-bound"），删除 R1 的隐式 `str()`。整数 `1` 与字符串 `"1"` 不再碰撞。
- **`validate_namespace(namespace)`**：非 str 抛 `TypeError`；空/含 `~`/空层/层含 `[A-Za-z0-9._-]` 外字符抛 `ValueError`。
- **`build_redis_key(namespace, *segments)`**：
  - namespace 保持可读层级（**不编码**），如 `pm:v2:prod:cache`；
  - 动态 segment 继续 R1 可逆百分号编码（`:`→`%3A`、`%`→`%25`、空格→`%20`、`~`→`%7E`、斜杠→`%2F`、Unicode 逐字节）；
  - 用 `BOUNDARY = ":~:"` 分隔 namespace 与动态段。namespace 无 `~`、段无裸 `~`/`:` → 边界唯一，无碰撞。
- `decode_key_segment` 保留（测试/审计还原）。

### 2.2 测试清理与残留审计（缺陷 3 关闭）

- **1ms lease 边界测试** `test_lease_sub_ms_ttl_rejected_before_network` 新增 `finally`：删除精确 `lease:msbound` + `fence:msbound` key（fence counter 不自动过期），并 `aclose()`；1ms 边界获取后先 `release_lease` 再清理。
- 纯构造测试（`test_no_scan_or_pattern_delete_api`、`test_*_uses_shared_encoder`、`test_no_raw_pipeline_api`）统一补 `aclose()`。
- 全部 cleanup key 用真实 `build_redis_key(c.namespace, ...)` 计算，无手拼旧格式。
- 验收同时检查历史编码前缀 `pm%3Ait%3A*` 与新可读前缀 `pm:it:*`，二者均为 0。

### 2.3 稳定 TTL 证据（缺陷 4 关闭）

- 半开 jitter 数学性质由纯函数测试证明（`100 <= t < 130` 等，R1 保留）。
- 真实 Redis PTTL 测试改为 **elapsed tolerance + 上界校验**：
  - `test_set_forces_finite_ttl`：`pttl <= 330000` 且 `pttl >= 300000 - 2000`；显式 5s 同理 `<=35000 / >=5000-2000`。
  - `test_batch_set_applies_ttl_jitter`：`pttl <= 110000` 且 `pttl >= 100000 - 2000`。
  - `ELAPSED_TOLERANCE_MS = 2000`（明确、较小；不依赖零耗时假设）。
- 不删除测试、不扩大无意义区间、不 sleep、不重试掩盖 flake。

---

## 3. 命令与真实结果

```bash
# 1) redis-cli ping
redis-cli ping
# → PONG

# 2) compileall
python3 -m compileall -q app tests
# → exit 0，无输出

# 3) 三个 redis 测试文件
.venv/bin/pytest -q tests/trading/test_v2_redis_keys.py \
  tests/trading/test_v2_redis_control.py tests/trading/test_v2_redis_cache.py
# → 49 passed in 0.46s

# 4) 100 次重复 TTL 测试（两个易波动用例）
for i in $(seq 1 100); do
  .venv/bin/pytest -q \
    tests/trading/test_v2_redis_cache.py::test_set_forces_finite_ttl \
    tests/trading/test_v2_redis_cache.py::test_batch_set_applies_ttl_jitter >/dev/null 2>&1 || exit 1
done
# → 100x repeat: ALL PASSED

# 5) tests/trading
.venv/bin/pytest -q tests/trading
# → 80 passed in 0.52s

# 6) 全量回归
.venv/bin/pytest -q
# → 291 passed, 1 warning in 2.65s
#   （1 warning = conftest 既有 event_loop 弃用警告）

# 7) git diff --check
git diff --check
# → 无输出，exit 0

# 8) 两类前缀残留（均必须为 0）
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

---

## 4. 四项问题逐项关闭证据

### 缺陷 1（P1，identity 仍可碰撞）— 已关闭

- `encode_key_segment(1)` / `encode_key_segment(1.5)` / `encode_key_segment(None)` / `build_redis_key(NS, "a", 1)` 均抛 `TypeError`；字符串 `"1"` 正常（`test_non_str_segment_rejected` / `test_str_segment_accepted`）。
- `test_numeric_values_not_silently_coerced`：int 不隐式 str，`str(12)` 与 `str(1),str(2)` 不同。

### 缺陷 2（P1，namespace 整体编码）— 已关闭

- `test_namespace_readable_preserved`：`build_redis_key("pm:v2:prod:cache", ...)` 以 `pm:v2:prod:cache:~:` 开头，无 `pm%3Av2...`。
- `test_namespace_validation`：非 str/空/含 `~`/空层/含空格层均拒绝。
- `test_boundary_is_reserved_and_unique`：`BOUNDARY == ":~:"`；段内 `~` 编码为 `%7E`，`k.count(":~:") == 1`。
- `test_collision_free_across_namespaces`：7 组 ns/段输入，唯一 key 数 == 唯一输入数（含 ns 与段重新分割 `("pm","a:x")` vs `("pm:a","x")`）。

### 缺陷 3（P1，测试残留）— 已关闭

- 1ms lease 测试 finally 清理精确 `lease`/`fence` key + `aclose`。
- 纯构造测试全部 `aclose()`。
- 验收实测：`pm:it:*` → 0、`pm%3Ait%3A*` → 0（见 §3 第 8 项）。

### 缺陷 4（P1，TTL 集成测试不稳定）— 已关闭

- 两个 PTTL 测试改 elapsed tolerance（±2000ms）+ 上界校验，不依赖零耗时。
- **100 次连续重复全过**（见 §3 第 4 项，复现原 flake 的 `pttl=99999<100000` 不再出现）。

### 回归

- R1 的 14 个 keys + 13 个 control + 22 个 cache 测试全部保留并适配通过；tests/trading 80 = 31(00a) + 26(00b) + 18(R1) + 5(R2 新增 net)。
- 全量 Base 291 passed 无退化。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞说明：
- `encode_key_segment` 现在要求调用方显式 `str()` 数值段；当前 V2 客户端所有段均为 str，无调用点受影响。
- 动态段仍为百分号编码（R1 设计），namespace 段可读；运维 SCAN 前缀现为真实 `pm:it:*`。
- 模块级单例仍未接入 `main.py`（lifespan 留 WP-00d）。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/services/redis_keys.py \
  serve/tests/trading/test_v2_redis_keys.py serve/tests/trading/test_v2_redis_control.py \
  serve/tests/trading/test_v2_redis_cache.py serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/docs/manifests/wp-00b-r2-redis-identity-test-stability.md
```

- 回到 R1 已提交状态（`build_redis_key` 恢复整体编码、`encode_key_segment` 恢复隐式 str()、PTTL 断言恢复零耗时下界）。
- 无 schema、无密钥、无生产数据改动；测试双前缀残留为 0，回滚无副作用。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00b-r2-redis-identity-test-stability.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
8f406ad4cee49fbc81383858613c4ade25c0afeaf2584b31194f5706c46b02a6
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00b-r2-redis-identity-test-stability.md | sha256sum
```
