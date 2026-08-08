# WP-00b-r1 — Redis 基础不变量整改

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00b-r1-redis-remediation.md`。

## 1. 前置审查结论

审查已复验 `WP-00b`：真实 Redis 测试 `26 passed`，completion manifest SHA-256 为
`fd1ff118254c440deb1f62c33dec5542e7e130c743d4f0106a96c258211775ef`。交付没有数据丢失，
但以下基础接口在进入 Artifact/Outbox 前必须整改：

1. **P1 — Redis key 可碰撞**：当前拼接使 `version="a:b", name="c"` 与
   `version="a", name="b:c"` 生成同一 key；Control 动态 name 也未编码。
2. **P1 — Cache 暴露原始 pipeline**：调用方可绕过 namespace、version、有限 TTL 和故障
   降级，直接执行任意 Redis 命令。
3. **P1 — Control CAS 空串歧义**：`expected=None` 和已有值 `""` 使用同一 Lua 哨兵，无法
   正确比较合法空字符串。
4. **P2 — 边界不一致**：TTL jitter 文档为 `[0,jitter)`，实现却含上界；小于 1ms 的正 lease
   TTL 会被截断成 `PX 0`。

## 2. 目标与用户价值

封闭 Redis 基础接口，保证不同业务键永不因分隔符碰撞，批量缓存不能绕过有限 TTL，CAS 对
所有字符串值语义明确，并让时间边界与文档一致。此任务只整改 WP-00b，不开发 Artifact Store。

## 3. 必读文档

1. `/code/pollymarket/v2/AGENTS.md`
2. `serve/docs/performance-cache-database-design.md` §5、§8.2、§11
3. `serve/docs/v2-implementation-contract.md` §3、§13–§15
4. `serve/docs/manifests/wp-00b-redis.md`
5. `serve/docs/tasks/README.md`

## 4. 允许修改

```text
serve/app/services/redis_keys.py
serve/app/services/redis_control.py
serve/app/services/redis_cache.py
serve/tests/trading/test_v2_redis_control.py
serve/tests/trading/test_v2_redis_cache.py
serve/tests/trading/test_v2_redis_keys.py
serve/docs/manifests/wp-00b-r1-redis-remediation.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 config、`.env.example`、Base `services/redis.py`、queue/worker、Artifact、main.py、V1
及上述清单之外文件。

## 5. 实现合同

### 5.1 唯一 key 编码

- `redis_keys.py` 提供唯一共享的 `encode_key_segment(value)` 和
  `build_redis_key(namespace, *segments)`；每个动态 segment 必须一一映射，支持 Unicode、冒号、
  `%`、空格和斜杠，不得靠禁止正常字符逃避碰撞。
- Control/Cache 的 `_key()` 都必须调用该函数；相同输入稳定、不同 segment tuple 不得碰撞。
- 禁止在两个客户端复制编码逻辑。

### 5.2 受控批量缓存

- 删除公开的原始 `pipeline()`。
- 定义 frozen typed operation（至少 SET/DELETE）和 `execute_batch()`；SET 必须先完成 canonical
  JSON、versioned key、有限 TTL+jitter 校验，再加入内部 pipeline。
- 调用方不得取得底层 pipeline/client；连接故障遵循 cache bypass：返回与操作数量一致的失败
  结果，程序错误继续抛出。
- CAS 继续原子执行，并使用相同 key 编码与有限 TTL。

### 5.3 CAS 与时间边界

- Control CAS 的 Lua 参数必须显式区分 `EXPECT_MISSING` 与 `EXPECT_VALUE`；已有空字符串可被
  `expected=""` 正确比较，`expected=None` 只匹配不存在。
- `effective_ttl(base,jitter)` 严格返回 `[base, base+jitter)`；`jitter=0` 返回 base。
- lease TTL 转毫秒后必须 `>=1`，否则在发 Redis 命令前抛 `ValueError`。

## 6. 必测证据

- 复现用例修复：`("a:b","c") != ("a","b:c")`。
- Unicode、`:`、`%`、空格、斜杠 segment 无碰撞且稳定。
- Control 和 Cache 都使用共享编码器。
- raw pipeline API 不存在；typed batch 不能创建永久 TTL 或非 versioned key。
- batch SET/DELETE 顺序、返回值、故障 bypass、JSON 错误均正确。
- Control CAS：missing、已有空串、普通值、冲突四条路径。
- jitter 运行多次始终满足半开区间；sub-ms lease 在网络调用前拒绝。
- 原 WP-00a/00b 测试与全量 Base 回归不得退化；真实 Redis 测试后无本任务 key 残留。

## 7. 验收命令

```bash
cd /code/pollymarket/v2/serve
redis-cli ping
python3 -m compileall -q app tests
.venv/bin/pytest -q tests/trading/test_v2_redis_keys.py
.venv/bin/pytest -q tests/trading/test_v2_redis_control.py
.venv/bin/pytest -q tests/trading/test_v2_redis_cache.py
.venv/bin/pytest -q tests/trading
.venv/bin/pytest -q
git diff --check
redis-cli --scan --pattern 'pm:it:*' | wc -l
```

最后一条必须为 `0`。不得用 mock 代替 lease/CAS/batch 的真实 Redis 验收。

## 8. 交付与索引更新

完成时创建且只创建：

```text
serve/docs/manifests/wp-00b-r1-redis-remediation.md
```

内容必须包含修改文件、真实命令结果、四项缺陷逐项关闭证据、blocker、回滚和“去除纯 64 位
哈希行”口径的 SHA-256。更新 manifests 索引，把 `00b-r1` 标为 `DONE`（待审）；tasks 索引仍
保持当前任务，等待用户说“完成”后由审查者决定 `ACCEPTED`。不得自行创建 WP-00c1 任务文档。

## 9. 非目标

- 不实现 Artifact/S3、Outbox、数据库模型、OTel 或 lifespan。
- 不重构 Base Redis，不新增业务队列，不提交或推送 Git。
- 不用 TODO、空壳、删除测试或放宽断言通过验收。
