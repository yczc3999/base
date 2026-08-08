# COMPLETION MANIFEST — WP-00d1-r5 · Redactor 敏感 Key 边界整改

- Work package: `WP-00` 子任务 `WP-00d1-r5`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行: Codex `/root`
- 前置: `WP-00d1-r4`（REMEDIATION_REQUIRED：普通前导文本可吞并敏感 key）
- 规范依据: `serve/docs/tasks/wp-00d1-r5-redactor-key-boundaries.md`

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/observability/logging.py` | 修改 | 删除 `_parse_key` 的贪婪整段 key/skip 语义；新增 lexical-boundary + 冻结敏感词表前向匹配；候选跨度硬上限 64；失败只前进一字符 |
| `serve/tests/trading/test_v2_log_redaction.py` | 修改 | 增加前导文本复现、多词/quoted 变体、同行多 assignment、精确负例和固定 seed 500 组前导 fuzz；共用矩阵自动覆盖 span exporter |
| `serve/docs/manifests/wp-00d1-r5-redactor-key-boundaries.md` | 新增 | 本 manifest |
| `serve/docs/manifests/README.md` / `serve/docs/tasks/README.md` | 修改 | 登记 R5 交付状态 |

未修改 tracing、config、metrics、依赖、main/lifespan、数据库、Redis、Artifact、V1 或既有
task/manifest；Span 继续使用 logging 的唯一清洗器。

## 2. 实现与不变量

1. `_match_sensitive_key_at` 只在字符串开头或前一字符不属于 `A-Za-z0-9_.-` 的 lexical
   boundary 尝试匹配；非匹配不再返回跨 token 的 skip 位置。
2. 冻结敏感词表按首字符、长度降序形成确定性候选表；`_match_canonical_key` 仅忽略 key 内
   `-`/`_`/space/tab，禁止跨行，完整 key 后只接受 matching quote、水平空白与 `:`/`=`。
3. 原始候选跨度 `MAX_ASSIGNMENT_KEY_SPAN=64`；失败时主游标只前进一字符。冻结词表与固定跨度下
   时间为 O(n)，额外输出空间 O(n)，无递归或回溯。
4. 成功后才复用 R4 `_consume_value`；普通 prefix、key、delimiter、非敏感前后缀保留。同行多个
   single-key assignment 可分别清洗；Cookie/sentence/quoted/unclosed 语义保持不变。
5. PEM、Bearer/Basic、URL userinfo、4096 最终硬上限、bytes marker、坏 `__str__`、OTel provider
   生命周期及非敏感精确反例均由原回归继续锁定。

## 3. 命令与真实结果

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests
# exit 0

.venv/bin/pytest -q tests/trading/test_v2_log_redaction.py
# 134 passed in 0.16s

.venv/bin/pytest -q tests/trading/test_v2_trace_context.py
# 88 passed in 0.16s

.venv/bin/pytest -q tests/trading/test_v2_config.py
# 93 passed in 0.23s

.venv/bin/pytest -q tests/trading/test_v2_metric_cardinality.py
# 33 passed in 0.04s

.venv/bin/pytest -q tests/trading
# 608 passed in 2.00s

.venv/bin/pytest -q
# 819 passed, 1 warning in 4.22s

git diff --check
# 无输出，exit 0
```

唯一 warning 是既有 `tests/conftest.py` 自定义 pytest-asyncio `event_loop` 的弃用提示；与 R5 无关。

## 4. 验收证据

- 五个独立复现全部变为保留 prefix/key、value=`[REDACTED]`；人工探针与表驱动日志测试均通过。
- `SENSITIVE_MATRIX` 新增 8 个带普通/时间/JSON 前缀案例；同一矩阵由 trace exporter 测试复用，
  因而日志与 span 两条路径均验证。
- 12 个 `private key/access token/response body` 的 space/underscore/hyphen/CamelCase + quoted key
  变体全部通过。
- `foo=keep token=R5_ONE password=R5_TWO tail=ok` 只清洗两个 marker，并保留首尾非敏感字段。
- `prefixtoken/tokenizer/my_token_hint/content_type/side` 与无 delimiter 普通句子均保持原文。
- 固定 seed 500 个随机前导 + lexical boundary + 敏感 surface + quoted/unquoted value 样本 marker
  泄漏数为 0；R4 原 500 fuzz + 200 prompt 样本继续通过。
- 全量 819、长度边界、PEM/Cookie、quote 奇偶反斜杠、bytes、provider 生命周期回归全部通过。

## 5. Blocker、风险与回滚

未发现 R5 范围内 blocker。剩余 lifespan/health/metrics endpoint/Artifact factory 属于 `WP-00d2`。

回滚：

```bash
git checkout -- serve/app/observability/logging.py \
  serve/tests/trading/test_v2_log_redaction.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/docs/manifests/wp-00d1-r5-redactor-key-boundaries.md
```

无迁移、网络、Redis、Provider 或业务数据副作用。

## 6. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00d1-r5-redactor-key-boundaries.md`
- SHA-256（删除恰好为 64 位十六进制的哈希行后计算）：

```text
4b1928363da625035fa714018e99ffec175c30c978fabb7d294e92f878753d3e
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00d1-r5-redactor-key-boundaries.md | sha256sum
```
