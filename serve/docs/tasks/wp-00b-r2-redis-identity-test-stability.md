# WP-00b-r2 — Redis identity 与测试稳定性整改

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00b-r2-redis-identity-test-stability.md`。

## 1. 前置审查结论

`WP-00b-r1` 的四项原整改逻辑基本有效。审查复验结果：目标测试 `44 passed`、trading
`75 passed`、全量 `286 passed`，manifest SHA-256
`bdb27c6de1079ea6a8383c01b495b79072d9d91588f72c1b87a00cdfd469cf32` 一致。但进一步做
重复性和残留检查后发现以下必须关闭的问题：

1. **P1 — identity 仍可碰撞**：`encode_key_segment()` 把非字符串静默 `str()`，因此整数
   `1` 与字符串 `"1"` 产生同一 identity；这与“不同输入不碰撞”的合同冲突。
2. **P1 — namespace 被整体编码，残留审计失真**：测试 namespace `pm:it:*` 实际写成
   `pm%3Ait%3A*`，既破坏运维前缀，也让 manifest 的 `pm:it:* → 0` 检查永远看不到真实残留。
3. **P1 — 测试确有残留**：审查发现 9 个编码后的 fencing key；来源包括 1ms lease 测试没有
   finally cleanup/close。审查者已清理，整改后必须证明两种历史前缀均为 0。
4. **P1 — TTL 集成测试不稳定**：连续重复测试第 11 轮复现
   `pttl=99999 < 100000`。Redis 在 SET 与 PTTL 之间自然流逝，测试把理论 TTL 下界当作读取时
   下界，导致随机 CI 失败。

## 2. 目标

把 Redis key identity 变成类型明确、可运维、无歧义的协议；让所有真实 Redis 测试严格清理，
并使 TTL 验收在证明半开 jitter 语义的同时不依赖零耗时假设。本任务仍不开发 Artifact Store。

## 3. 必读文档

1. `/code/pollymarket/v2/AGENTS.md`
2. `serve/docs/tasks/wp-00b-r1-redis-foundation-remediation.md`
3. `serve/docs/manifests/wp-00b-r1-redis-remediation.md`
4. `serve/docs/performance-cache-database-design.md` §5、§11
5. `serve/docs/v2-implementation-contract.md` §3、§13–§15

## 4. 允许修改

```text
serve/app/services/redis_keys.py
serve/app/services/redis_control.py
serve/app/services/redis_cache.py
serve/tests/trading/test_v2_redis_keys.py
serve/tests/trading/test_v2_redis_control.py
serve/tests/trading/test_v2_redis_cache.py
serve/docs/manifests/wp-00b-r2-redis-identity-test-stability.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 config、`.env.example`、Base Redis、queue/worker、Artifact、main.py、V1 和清单外文件。
不得改写已冻结的 R1 manifest。

## 5. 实现合同

### 5.1 Identity 与 namespace

- `encode_key_segment()` 只接受 `str`；任何非字符串立即 `TypeError`，禁止隐式 `str()`。
- namespace 保持可读层级，例如 `pm:v2:prod:cache`，不得编码为 `pm%3Av2...`。
- namespace 必须通过确定性格式校验；使用 namespace 与动态 segment 之间的保留边界标记，确保
  不同 namespace/segment 分割也不会碰撞。保留标记不得出现在合法 namespace 或编码后 segment。
- 动态 segment 继续使用 R1 的可逆百分号编码；空串、Unicode、冒号、百分号、空格、斜杠均
  合法且保持一一映射。
- Control/Cache 继续复用唯一构造器，不复制编码逻辑。

推荐格式（可采用等价、经证明无歧义的格式）：

```text
{validated_namespace}:~:{encoded_segment_1}:{encoded_segment_2}
```

其中 namespace 只允许非空 `[A-Za-z0-9._-]+` 层级以 `:` 分隔，`~` 不合法；动态编码也不得
产生裸 `~` 或 `:`。

### 5.2 测试清理与残留审计

- 所有建立过 Redis 连接或写 key 的测试必须在 `finally` 中删除自己创建的**精确 key**并
  `aclose()`；不得用生产代码的 SCAN/pattern delete。
- 1ms lease 边界测试必须清理 lease 与不会自动过期的 fence counter。
- 测试辅助函数必须使用真实 `build_redis_key()` 计算 cleanup key，禁止手拼旧格式。
- 验收最后同时检查历史编码前缀 `pm%3Ait%3A*` 与新可读前缀 `pm:it:*`，二者都必须为 0。

### 5.3 稳定 TTL 证据

- 半开 jitter 的数学性质继续由纯函数测试证明：返回值始终满足
  `base <= ttl < base+jitter`。
- 真实 Redis PTTL 测试必须允许 SET→PTTL 的正常耗时，不得要求读取值仍 `>= base_ms`；使用
  明确且较小的 elapsed tolerance，仍须验证上界不超过生成 TTL。
- 对原先两个易波动的 PTTL 测试连续运行至少 100 次，必须全过。
- 不得通过删除测试、扩大到无意义区间、sleep 或重试失败用例来掩盖 flake。

## 6. 必测证据

- `encode_key_segment(1)` 和 `build_redis_key(..., 1)` 抛 `TypeError`；字符串 `"1"` 正常。
- key 以原始 namespace 开头，且跨 namespace/segment 分割无碰撞。
- R1 的特殊字符、Unicode、空串、CAS、batch 和 lease 测试继续通过。
- 1ms lease 测试结束后 fence/lease 精确 key 均不存在，client 已关闭。
- 两个 PTTL 测试重复 100 次零失败。
- 全部 target/trading/Base 测试通过；两类测试前缀残留均为 0。

## 7. 验收命令

```bash
cd /code/pollymarket/v2/serve
redis-cli ping
python3 -m compileall -q app tests
.venv/bin/pytest -q tests/trading/test_v2_redis_keys.py \
  tests/trading/test_v2_redis_control.py tests/trading/test_v2_redis_cache.py
for i in $(seq 1 100); do
  .venv/bin/pytest -q \
    tests/trading/test_v2_redis_cache.py::test_set_forces_finite_ttl \
    tests/trading/test_v2_redis_cache.py::test_batch_set_applies_ttl_jitter || exit 1
done
.venv/bin/pytest -q tests/trading
.venv/bin/pytest -q
git diff --check
test "$(redis-cli --scan --pattern 'pm:it:*' | wc -l)" -eq 0
test "$(redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l)" -eq 0
```

## 8. 交付

创建且只创建 completion manifest：

```text
serve/docs/manifests/wp-00b-r2-redis-identity-test-stability.md
```

Manifest 必须记录：修改文件、真实命令结果、四项问题逐项证据、100 次重复测试、两类残留检查、
blocker、回滚和可复现 SHA-256。更新 manifests 索引为 `DONE（待审）`；tasks 索引保持 R2 为
当前任务，等待用户说“完成”。不得自行创建 WP-00c1 文档，不得提交或推送 Git。

## 9. 非目标

- 不新增 Redis 功能，不改 CAS/batch 业务语义，不实现 Artifact/Outbox/OTel。
- 不把测试 helper 放入生产模块，不用 mock 代替真实 Redis。
- 不用 TODO、空壳、跳过测试或放宽 correctness 断言完成任务。
