# WP-00d1-r2 — 敏感文本覆盖最终整改

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00d1-r2-sensitive-text-coverage.md`。
> 最后更新：2026-08-08 15:22 EDT。依赖：`WP-00d1-r1` 已交付但审查未接受；本任务接受前
> `WP-00d2` 继续阻塞。

## 1. 审查结论

R1 已关闭坏 `__str__` 日志和 provider A→B 生命周期，基础回归通过；独立实际计数为 20 log、
38 trace、93 config、33 metric、444 trading、655 full，manifest SHA
`a8e6ce006f8859c3c1af820428ea2d174820a7b3387eaeb454b80453ff86cffd` 一致（R1 manifest 把 log/trace
分别写成 19/39，合计 58 不变；本任务须按真实 collect 数记录）。唯一生产 P1 是敏感文本覆盖仍窄：

```text
private_key=ZXCV1234
response_body=MODEL_RESPONSE_789
tool_output=TOOL_RESULT_456
raw_payload=RAW_DATA_321
token=TOKEN_VALUE_999
Set-Cookie: opaque=COOKIE_VALUE_123
```

以上值经允许 span key（如 `model`）会被 exporter 原样收到；`Set-Cookie` 正则实际只匹配
`Cookie`，与 R1 §4.2 明列边界不符。

## 2. 目标与允许范围

补齐日志与 span 共用的敏感字符串清洗器，并用完整矩阵证明任何明列 secret 形态不会进入日志或
exporter。本任务不再修改 provider 生命周期或增加观测功能。

允许修改：

```text
serve/app/observability/logging.py
serve/app/observability/tracing.py
serve/tests/trading/test_v2_log_redaction.py
serve/tests/trading/test_v2_trace_context.py
serve/docs/manifests/wp-00d1-r2-sensitive-text-coverage.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 config、metrics、依赖、`__init__.py`、main/lifespan、数据库、Redis、Artifact、V1、既有
task/manifest。不得建立 tracing 专用第二套敏感规则。

## 3. 精确整改合同

1. 共用字符串清洗器必须识别大小写及 `-`/`_` 变体的赋值、JSON 和 header 形态：
   `password/passwd、secret、api key、authorization、cookie/set-cookie、passphrase、private key、
   signature、access/refresh/id token、credential、prompt、request/response body、tool input/output、
   raw payload、payload、token`。
2. 必须识别并整段脱敏 PEM 私钥块（RSA/EC/OPENSSH/PRIVATE KEY）；不得只清洗 `private_key=`。
3. `Cookie:` 与 `Set-Cookie:` 的 header value 均整体脱敏，保留 header 名；不得把 opaque cookie
   名称当作安全值。换行后其他 header 仍保留。
4. Bearer/Basic 与 `http(s)://userinfo@host` 保持现有清洗；非敏感文本如 `side=YES`、
   `content_type=json`、普通模型名不得误伤。
5. 清洗器须有固定输入长度上限；超限内容先有界截断再清洗，不能让日志/span 复制无限字符串。
6. Span scalar sequence 必须限制最大 64 项，且同质判断使用 `type(v) is first_type`；特别是
   `[1, True]` 必须拒绝，避免 bool 作为 int 混入。
7. `configure_tracing` 在 enabled/disabled 两条路径改变旧 provider 前都先校验 ratio 与 timeout；
   `enabled=False, ratio=-1` 或 `timeout=0` 必须保留旧 provider，不得 shutdown。其余生命周期语义不变。
8. 不输出原始 secret 到异常消息、测试失败参数、manifest 或日志；测试使用固定 marker 并只断言不存在。

## 4. 必测证据

- 日志与 exporter 使用同一表驱动矩阵覆盖第 1–3 条全部形态；上面 6 个独立复现必须关闭。
- PEM 多行、Cookie/Set-Cookie 多 header、大小写和 hyphen/underscore 变体均覆盖。
- 非敏感反例至少 8 个，证明不过度清洗。
- 64/65 项序列、`[1, True]`、超长字符串边界；原输入不修改。
- active provider 下 disabled+坏 ratio/timeout：抛 ValueError、旧 provider shutdown=0 且继续导出。
- 原 20 log、38 trace、93 config、33 metric 与全部 WP-00 回归继续通过。

## 5. 验收命令

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

## 6. 交付、回滚与非目标

创建且只创建 `serve/docs/manifests/wp-00d1-r2-sensitive-text-coverage.md`；记录完整敏感/非敏感矩阵、
真实 collect/执行数量、blocker、回滚及可复现 SHA。更新两个索引为 `DONE（待审）`，保持 R2 为当前
任务并等待用户再次说“完成”。不得创建 00d2、提交或推送。

回滚恢复允许代码/测试/索引并删除 R2 manifest；无迁移、网络或业务数据副作用。本任务不接
main/lifespan/metrics endpoint，不重构 logger/provider，不增加依赖或 allowlist。
