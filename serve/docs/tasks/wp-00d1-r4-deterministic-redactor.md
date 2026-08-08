# WP-00d1-r4 — 确定性有界 Redactor

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00d1-r4-deterministic-redactor.md`。
> 最后更新：2026-08-08 15:52 EDT。依赖：`WP-00d1-r3` 已交付但审查未接受；本任务接受前
> `WP-00d2` 继续阻塞。

## 1. 审查结论与根因

R3 已关闭长 PEM、带空格配对 quote 和多 Cookie；91 log、80 trace、557 trading、768 full 与
manifest SHA 通过。剩余 P1 仍来自用多个正则拼接解析 quoted value：

```text
prompt="don't reveal TOPSECRET now
prompt='say "hello" then TOPSECRET now
prompt="escaped \" quote then TOPSECRET now
```

前两项会原样输出，第三项只清洗前段；相反引号/转义引号使 `_UNCLOSED_QUOTED_RE` 提前失配。继续
叠加正则只会产生新边界，本任务必须把敏感 assignment/header 解析改为单次确定性扫描器。

## 2. 允许范围

```text
serve/app/observability/logging.py
serve/tests/trading/test_v2_log_redaction.py
serve/tests/trading/test_v2_trace_context.py
serve/docs/manifests/wp-00d1-r4-deterministic-redactor.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 tracing 实现、config、metrics、依赖、main/lifespan、`__init__.py`、数据库/Redis/Artifact、
V1 或既有 task/manifest。Span 继续复用 logging 的唯一清洗器。

## 3. 精确实现合同

1. 用一个**有界、单向、确定性 scanner**处理敏感 key assignment；不得继续用互相叠加的
   quoted/unclosed/KV 正则猜语法。允许保留简单 Bearer、URL userinfo 和 PEM/header marker 正则，
   但 assignment quote 状态必须由 scanner 处理。
2. scanner 在最多 `MAX_CLEAN_STRING_LEN` 的副本上工作：识别允许字符组成的 key、可选引号、
   `:`/`=`、空白与 value。key 按大小写和 `-/_/space` 归一后精确匹配冻结敏感词表，不用任意子串。
3. quoted value：只把与起始 quote 同类且未被奇数个反斜杠转义的 quote 当结束；相反 quote 是普通
   字符。找到结束则只替换 value；未找到则从 value 起点清洗到行尾/窗口末尾。CRLF/LF 正确。
4. sentence key（prompt/body/tool/raw payload/payload）的 unquoted value 清洗到行尾；single key
   清洗到明确分隔符。Cookie/Set-Cookie 从 header value 到行尾全红；PEM BEGIN 到匹配 END 或窗口末尾。
5. 输出最终再次硬裁剪，长度不得超过 `MAX_CLEAN_STRING_LEN + len("[TRUNCATED]")`；重复 1000 个
   `token=x` 或 Cookie 行也不能扩张。bytes/bytearray 不输出内容，只输出固定 `<bytes length=N>`。
6. 算法不得回溯或递归解析字符串；复杂度 O(n)、额外空间 O(n)，n≤4096。不得新增依赖。
7. 保持日志坏 `__str__`、span 类型/序列、provider 生命周期和非敏感反例现有语义。

## 4. 必测证据

- quote 状态表：单双引号、相反引号、escaped quote、偶/奇反斜杠、配对/未闭合、CRLF/LF、多行、
  nested JSON；上面三个复现必须在日志与 exporter 全关。
- repeated markers 输出长度硬上限；4096/4097/10000 输入；bytes/bytearray 不含内容 marker。
- Cookie、PEM、Bearer、userinfo、27 个敏感矩阵与 10 个非敏感反例全部继续通过。
- 固定 seed 生成至少 500 个包含 quote/backslash/delimiter 的字符串：不抛异常、输出有界；嵌入
  `prompt=` + marker 的样本不得保留 marker。
- 原 91 log、80 trace 与全部 WP-00 回归继续通过。

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

创建且只创建 `serve/docs/manifests/wp-00d1-r4-deterministic-redactor.md`，记录 scanner 不变量、真实
测试数量/结果、复现、blocker、回滚和可复现 SHA；更新两个索引为 `DONE（待审）`。不得创建 00d2、
提交或推送。回滚恢复允许文件并删除 R4 manifest；无迁移或外部副作用。
