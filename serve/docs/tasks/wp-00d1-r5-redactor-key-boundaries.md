# WP-00d1-r5 — Redactor 敏感 Key 边界整改

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00d1-r5-redactor-key-boundaries.md`。
> 最后更新：2026-08-08 16:17 EDT。依赖：`WP-00d1-r4` 已交付但审查未接受；本任务接受前
> `WP-00d2` 继续阻塞。

## 1. 审查结论与根因

R4 的确定性 scanner、quote 状态、长度上限和 bytes 边界均通过；独立复验为 101 log、80 trace、
567 trading、778 full，manifest SHA 一致。仍有一个生产 P1：`_parse_key()` 为支持 `private key`
而贪婪吸收任意前导单词和空格，并在非敏感整段 key 上一次跳过，导致内嵌的真实敏感 assignment
永不再检查。以下五项已独立复现为原样泄露：

```text
prefix token=TOPSECRET
failed password=hunter2
note private key=ZXCV1234
x prompt=TOPSECRET
INFO Cookie: a=SECRET1
```

这不是词表缺失，而是候选 key 的起点/跳转规则错误；不得通过继续增加敏感词或前缀词修补。

## 2. 允许范围

```text
serve/app/observability/logging.py
serve/tests/trading/test_v2_log_redaction.py
serve/tests/trading/test_v2_trace_context.py
serve/docs/manifests/wp-00d1-r5-redactor-key-boundaries.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 tracing 实现、config、metrics、依赖、main/lifespan、`__init__.py`、数据库/Redis/Artifact、
V1 或任何既有 task/manifest。Span 继续复用 logging 的唯一清洗器。

## 3. 精确实现合同

1. 删除“把任意空格连接的整段文本先当一个 key、失败后整体 skip”的路径。scanner 必须在每个
   **词法边界**重新尝试敏感 key；一次非敏感候选不得跨过后续边界，使 `prefix token=` 中的
   `token=` 失去检查机会。
2. 新增确定性 `_match_sensitive_key_at`（名称可等价）：只在字符串开头，或前一字符不是
   `A-Za-z0-9_.-` 时开始；对冻结敏感词表做大小写不敏感的精确匹配，输入 key 内仅允许
   `-`、`_`、ASCII space/tab 作为归一分隔，不能跨 CR/LF。支持 `AccessToken`、`access_token`、
   `access-token`、`access token` 以及 JSON/quoted key；完整 key 后只允许匹配的 closing quote、
   space/tab 和 `:`/`=`，因此 `tokenizer=`、`prefixtoken=` 不得误命中。
3. 匹配器使用冻结 trie，或在冻结词表与冻结最大 key span 上做有界比较；原始 key 候选跨度固定
   `MAX_ASSIGNMENT_KEY_SPAN <= 64`。不得回溯、递归、从失败位置跳过未检查文本，复杂度上界必须是
   `O(n * MAX_ASSIGNMENT_KEY_SPAN)`，在冻结常数下为 O(n)，n≤4096。
4. 多个候选可命中时采用能到达 delimiter 的最长精确 key；只替换其 value。保留前导日志文本、
   key、delimiter 和非敏感后缀。Cookie/Set-Cookie、sentence key、quoted/unclosed value 的 R4
   消费规则不变。
5. 非敏感 assignment 不能吞掉其 value 中后续的敏感 assignment；例如
   `meta=keep token=TOPSECRET` 必须保留 `meta=keep` 且清洗 token value。
6. 保持 PEM、Bearer/Basic、URL userinfo、最终输出硬上限、bytes marker、坏 `__str__`、span
   类型/序列、provider 生命周期及调用者对象不变等现有语义。不得新增依赖。

## 4. 必测证据

- 上述五个复现必须在直接 `redact()` 与 in-memory span exporter 两条路径全部关闭。
- 前导矩阵至少覆盖：普通词、时间/level 前缀、`[](){},;` 标点、JSON 前置字段、多个 assignment、
  `private key` / `access token` / `response body` / `Set-Cookie` 等多词 key。
- 精确反例至少覆盖：`prefixtoken=keep`、`tokenizer=keep`、`my_token_hint=keep`、
  `content_type=json`、`side=YES`、没有 delimiter 的普通句子；结果不得误清洗。
- 混合链：`foo=keep token=MARK bar=ok` 只清洗 `MARK`；同一行两个敏感 assignment 均清洗；
  quoted key/value、CRLF/LF、未闭合 quote 继续通过。
- 固定 seed 至少 500 个“随机前导 + 词法边界 + 敏感 assignment + marker”样本：marker=0、
  不抛异常、输出有界；另保留 R4 的 500 fuzz 与 200 prompt 样本。
- 原 101 log、80 trace、config、metric、全部 WP-00 回归继续通过。

## 5. 验收与交付

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

创建且只创建 `serve/docs/manifests/wp-00d1-r5-redactor-key-boundaries.md`，记录匹配边界不变量、
真实测试数量/结果、上述复现、blocker、回滚和可复现 SHA；更新两个索引为 `DONE（待审）`。
不得创建 00d2、提交或推送。回滚恢复允许文件并删除 R5 manifest；无迁移或外部副作用。
