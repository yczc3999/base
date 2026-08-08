# WP-00d1-r3 — Redactor 解析边界最终整改

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00d1-r3-redactor-parser-boundaries.md`。
> 最后更新：2026-08-08 15:33 EDT。依赖：`WP-00d1-r2` 已交付但审查未接受；本任务接受前
> `WP-00d2` 继续阻塞。

## 1. 审查结论

R2 已关闭六个原复现、Set-Cookie 基本形态、序列与 disabled 校验；61 log、80 trace、527 trading、
738 full tests 通过，manifest SHA `e2d08336a9dff253e53782220c7e17b5a614b510eb934d4e7e8f4eaaff5edf8b`
一致。仍有一个 redactor 解析 P1，包含三个同根边界：

1. 输入先截到 4096，再用必须含 `END PRIVATE KEY` 的正则；长 PEM 的 END 被截掉后，BEGIN 与前
   4096 字节私钥主体原样输出。
2. 敏感 quoted value 的正则遇空格即停止：`prompt="Tell me TOPSECRET now"` 只清洗 `Tell`，后半仍泄露。
3. `Cookie:` 只清洗到第一个分号：`Cookie: a=SECRET1; opaque=SECRET2` 会保留第二个 opaque cookie。

## 2. 允许范围

```text
serve/app/observability/logging.py
serve/tests/trading/test_v2_log_redaction.py
serve/tests/trading/test_v2_trace_context.py
serve/docs/manifests/wp-00d1-r3-redactor-parser-boundaries.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 tracing 实现、config、metrics、依赖、main/lifespan、`__init__.py`、既有 task/manifest、
数据库/Redis/Artifact/V1。Span 必须继续通过现有共用清洗器自动获得修复，不建第二套规则。

## 3. 精确实现合同

1. 保持输入有界，但 PEM 检测必须处理完整与被截断两种块：只要保留窗口内出现合法
   `-----BEGIN ... PRIVATE KEY-----`，从 BEGIN 到匹配 END 或保留窗口末尾全部替换为
   `[REDACTED]`；不得要求 END 存在才清洗。BEGIN 出现在截断边界前的部分标记也不得泄露主体。
2. 敏感 key 的双引号/单引号值必须覆盖其中的 whitespace、转义字符和常见标点，直到配对引号；
   未闭合 quoted value 从起始引号清洗到行尾/保留窗口末尾。无引号 body/prompt/tool/raw payload
   采用保守策略清洗到行尾或明确结构分隔符，宁可整段脱敏。
3. `Cookie:` 与 `Set-Cookie:` 从 header 名后清洗到行尾（不是第一个分号）；下一行其他 header 保留。
   CRLF 与 LF 都要覆盖。
4. 输出上限仍固定且不超过 `MAX_CLEAN_STRING_LEN + len("[TRUNCATED]")`；先做有界副本，所有正则
   只在有界副本运行，防 ReDoS/大内存。
5. 保持现有非敏感反例、URL/Bearer/Basic、KV/JSON、PEM 完整块、序列和 provider 生命周期语义。

## 4. 必测矩阵

- PEM 长度：4095、4096、4097、10000；完整/END 在窗口外/完全无 END；RSA/EC/OPENSSH/通用
  PRIVATE KEY。日志与 exporter 均不得含 BEGIN 后 marker 或主体。
- quoted 值：空格、逗号、分号、escaped quote、未闭合 quote、多行 JSON；上述 prompt/response body
  复现必须全关。
- Cookie/Set-Cookie：多 opaque cookie、属性、LF/CRLF，整行值全红；下一行 `X-Custom: keep` 保留。
- 至少 10 个现有非敏感反例继续原样；输出长度上限断言。
- 原 61 log、80 trace 与全部 WP-00 回归继续通过。

## 5. 验收命令与交付

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests
.venv/bin/pytest -q tests/trading/test_v2_log_redaction.py
.venv/bin/pytest -q tests/trading/test_v2_trace_context.py
.venv/bin/pytest -q tests/trading/test_v2_config.py
.venv/bin/pytest -q tests/trading/test_v2_metric_cardinality.py
.venv/bin/pytest -q tests/trading
.venv/bin/pytest -q
git diff --check
```

创建且只创建 `serve/docs/manifests/wp-00d1-r3-redactor-parser-boundaries.md`，记录真实测试数量、三组
复现、blocker、回滚与可复现 SHA；更新两个索引为 `DONE（待审）`，等待用户再次说“完成”。不得
创建 00d2、提交或推送。回滚恢复允许文件并删除 R3 manifest；无迁移或外部副作用。
