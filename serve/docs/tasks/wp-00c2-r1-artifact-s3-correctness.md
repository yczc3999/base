# WP-00c2-r1 — S3 Driver 正确性整改

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00c2-r1-artifact-s3-correctness.md`。
> 最后更新：2026-08-08 12:05 EDT。依赖：`WP-00c2` 已交付但审查未接受；本任务接受前
> `WP-00d` 继续阻塞。

## 1. 前置审查与真实状态

WP-00c2 的主体实现有效：config 45、contract 18、store 35、local 24、S3 54、trading 242、全量
453 tests 均通过，completion manifest SHA
`be34bf58dd9778a5670561ece6990bc508edb0386b0265cadcb1cc487a801f06` 一致。但独立探针复现出四组
未被测试覆盖的 P1：

1. **配置没有真正控制 wire 行为**：builder 未显式设置 `signature_version="s3v4"`；并且无论
   `ARTIFACT_S3_MAX_ATTEMPTS` 是多少，Driver 的 409 条件重试上限都固定为 3。实测配置为 1 时
   `driver._retry_limit == 3`。
2. **统一异常边界未成立**：只捕获四种 transport exception，`NoCredentialsError`、SSL/proxy 等
   其他 `BotoCoreError` 会从 `put/head/get/range/exists/health` 原样逃逸。实测 `health()` 直接抛
   `NoCredentialsError: Unable to locate credentials`，既不是 typed health，也可能暴露 Provider 细节。
3. **成功 PUT 可返回不可用 ref**：2xx 后虽 HEAD，但没有要求 HEAD 的 `ContentLength` 等于刚上传
   bytes，也没有要求 `pm-stored-sha256` 等于本次 computed digest。实测上传 10 bytes、HEAD 返回
   11 bytes 时仍得到 `created=True, stored_size=11`。
4. **Range 没有完成身份/内存校验**：range response 完全不校验五项 `pm-*` metadata，也不校验
   `ContentLength` 字段；读取上限错误使用整个 `ref.stored_size+1`，而不是请求长度 `end-start+1`。
   实测 `[2,5)` 在 metadata 为空时仍返回成功，且向 body 请求读取 11 bytes 而不是 4 bytes。

另有同一边界的小缺口：直接构造 `S3ArtifactDriver(prefix="a/../b")` 会绕过 Settings 的 segment
约束。本整改一并关闭，但不得借机扩展新功能。

## 2. 目标与用户价值

只关闭以上确定性缺口，使配置、认证、异常、条件写结果和 Range 真正符合已批准合同。整改后任何
Provider/凭据错误都可观察且 fail-closed，成功写不会产生“现在成功、以后读失败”的 ref，小 Range
也不会被恶意或错误响应放大为整对象读取。

## 3. 必读与冻结输入

1. `serve/docs/tasks/wp-00c2-artifact-s3.md`
2. `serve/docs/manifests/wp-00c2-artifact-s3.md`（冻结，只读）
3. `serve/app/services/artifact_store/drivers/s3.py`
4. `serve/tests/trading/test_v2_artifact_s3.py`
5. Botocore exception hierarchy 与 `Config.signature_version` 的本地已安装版本 API。

保留 WP-00c2 的 single conditional PUT、五项 metadata、checksum、HEAD reconcile、公共 Protocol、
配置字段和全部非目标；不重写设计。

## 4. 允许修改

```text
serve/app/services/artifact_store/drivers/s3.py
serve/tests/trading/test_v2_artifact_s3.py
serve/docs/manifests/wp-00c2-r1-artifact-s3-correctness.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 config/.env/依赖、Artifact contracts/service/local、实施合同、数据库、Redis、main/lifespan、
V1、原 WP-00c2 task/manifest 及清单外文件。

## 5. 精确整改合同

### 5.1 Builder 必须兑现 SigV4 与 attempts

- `Config` 明确设置 `signature_version="s3v4"`，不得依赖区域/Provider 的隐式默认选择。
- `S3ArtifactDriver.conditional_retry_limit` 必须取
  `settings.ARTIFACT_S3_MAX_ATTEMPTS`；同一个值同时控制 Botocore total attempts 和 Driver 显式
  409 条件 PUT 的**总调用上限**。
- `MAX_ATTEMPTS=1` 时首个 409 后立即失败且 PUT 调用总数为 1；N 时不得超过 N。
- 注入 client 与 builder 自建 client 两条路径都保存相同 retry policy；不得硬编码第二套 3。

### 5.2 所有 Botocore 异常必须收敛为公共合同

- 按异常语义分两层：
  1. request 结果可能未知的 connection/HTTP/SSL/proxy transport 异常，PUT 进入现有 HEAD reconcile；
  2. 其余 `BotoCoreError`（至少含 `NoCredentialsError`、参数/签名/credential provider 错误）不做
     假定成功，直接映射为脱敏 `ArtifactStorageError`。
- `head/get/get_range/exists` 不得泄出任何原生 `BotoCoreError`；全部映射为脱敏
  `ArtifactStorageError`。`health()` 对任意 `BotoCoreError` 返回
  `ArtifactHealth(ok=False, driver="s3", detail={"error": <低基数安全分类>})`，不得抛异常。
- 继续保留 ClientError 的 404/409/412/416 业务分类；不得用 catch-all 吞掉
  `ArtifactIntegrityError` 或测试断言错误。
- 错误文本/detail 不包含 endpoint URL、bucket 之外的请求参数、credential、签名、原始异常消息。

### 5.3 2xx 后必须验证本次创建结果

- 只有 PUT 2xx 分支要求 HEAD 的实际 `ContentLength == len(data)`，且
  `pm-stored-sha256 == sha256(data).hexdigest()`；任一不等均抛 `ArtifactIntegrityError`，不得返回
  `created=True`。
- 保持其他四项 metadata 与 candidate 的现有严格校验。
- 412 dedupe 与 transport reconcile 可能命中相同原文的另一 zstd level，因此仍允许实际 stored size/
  stored digest 与本次 candidate 不同；它们必须返回 `created=False`，随后由 Service 读回并验证
  原文，不能套用 created 分支的字节级相等断言。
- ETag 继续只作不透明 Provider 字段，禁止拿它代替 stored SHA。

### 5.4 Range 必须同时验证身份、响应长度与小范围上限

- 非空 range response 在读取 body 前调用现有 metadata 校验：五项 `pm-*` 必须完整并与 ref 匹配；
  缺失/冲突为 `ArtifactIntegrityError`。
- `ContentLength` 必须存在、为非负整数且精确等于 `requested = end-start`；HTTP 206 和精确
  `ContentRange` 规则保持。
- body 只允许 `read(requested+1)`；多 1 byte 用于发现 Provider 忽略/扩大 Range。返回长度必须恰好
  requested，所有成功/失败路径继续 close。
- 空区间仍为零请求；Range 不计算/声称全对象 checksum。

### 5.5 直接构造也不能绕过 prefix 合同

- Driver 构造器对 prefix 执行与已冻结 Settings 等价的结构校验：可空；非空禁止首尾 `/`、空段、
  `.`、`..`、反斜杠、NUL/CR/LF；不 strip、不 normalize。
- 至少测试 `a/../b`、`a/./b`、`a//b` 和合法 `a/b`。S3 key 仍严格 `<prefix>/<locator>`。

## 6. 必测证据

新增并通过：

1. builder Config 的 `signature_version == "s3v4"`；settings attempts=1/2 分别约束真实 PUT 次数。
2. `NoCredentialsError` 在 put/head/get/range/exists 全部变为 `ArtifactStorageError`，health 返回 false；
   至少一个 SSL/proxy/HTTP transport PUT 会走 HEAD reconcile；错误输出不含注入的 endpoint/secret。
3. PUT 2xx 后 HEAD size 不同、stored SHA 为另一个合法 64-hex 时分别 fail-closed；正常 created、
   412 跨 level 和 timeout reconcile 不回归。
4. Range metadata 缺失/冲突、ContentLength 缺失/非数值/不等全部拒绝；`[2,5)` 的 read spy 精确记录
   `read(4)`，多余 body 被发现，异常也 close。
5. 直接构造 prefix 的非法/合法矩阵；原 54 个 S3 测试及全部回归继续通过。

## 7. 验收命令

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests
.venv/bin/pytest -q tests/trading/test_v2_artifact_s3.py
.venv/bin/pytest -q tests/trading/test_v2_config.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_driver_contract.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_store.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_local.py
.venv/bin/pytest -q tests/trading
.venv/bin/pytest -q
git diff --check
```

## 8. 交付、风险与回滚

创建且只创建 `serve/docs/manifests/wp-00c2-r1-artifact-s3-correctness.md`。Manifest 必须记录四组
P1 及 prefix 缺口的测试、真实命令/数量、blocker、回滚和可复现 SHA；更新两个索引为
`DONE（待审）`，保持 R1 为当前任务，等待用户再次说“完成”。不得创建 00d、提交或推送。

风险只在 Botocore exception 分类与 created/dedupe 两种 HEAD 语义分离；用明确分支和 fault injection
证明，禁止以大 catch-all、放宽 metadata、取消 checksum 或删除旧测试通过。回滚只恢复本任务允许的
Driver/测试/索引并删除 R1 manifest；原 WP-00c2 交付保持，且无数据库、secret 或生产对象副作用。

## 9. 非目标

- 不实现 multipart/streaming/delete/retention、VersionId pinning、DB、HTTP API、main/lifespan。
- 不新增配置、不改公共 Protocol、不选择真实 Provider、不读取真实 key。
- 不重构整个 Driver，不加入新 SDK、sleep/backoff 框架或业务日志。
- 不用 TODO、skip、吞异常、无条件 PUT 或只改 manifest 文字完成整改。
