# WP-00c2-r2 — S3 StreamingBody 与 Endpoint 最终整改

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00c2-r2-artifact-stream-endpoint.md`。
> 最后更新：2026-08-08 12:27 EDT。依赖：`WP-00c2-r1` 已交付但审查未接受；本任务接受前
> `WP-00d` 继续阻塞。

## 1. 前置审查与真实状态

R1 的 SigV4、attempts、API 调用异常、created 写后验证、Range metadata/读取上限和 prefix 整改均
有效；91 S3、279 trading、490 full tests 通过，manifest SHA
`10f4008387fac5696f0a1cce438189dae5392c4e94c5fff3d25a7732bca705c4` 一致。仍有三个合同边界：

1. **StreamingBody 读取异常仍会原样泄出**：`get_object()` 成功返回后，`body.read()` 位于
   Botocore 捕获范围外。独立探针让 `get()`/`get_range()` 的 body 抛
   `ReadTimeoutError(endpoint_url="https://user:TOPSECRET@host")`，原生异常和 `TOPSECRET` 直接泄出。
2. **endpoint 不是严格 origin**：当前 validator 未访问 `urlsplit(...).port`，也只用 truthiness
   判断 userinfo/query/fragment。实测错误接受 `https://host:abc`、`https://host:99999`、
   `https://exa mple.com`、`https://@host`、`https://host?`、`https://host#`。
3. **`exists()` 隐藏对象元数据损坏**：它直接 HEAD 后返回 `True`，不运行现有五项 metadata 校验。
   因而存在但非本 CAS 对象的 key 会被报告为健康存在，而非 `ArtifactIntegrityError`。

同时关闭同一路径的三个响应/异常缺口：基础 `HTTPClientError` 未进入 PUT 的 unknown-result HEAD
reconcile；`ContentLength=3.9` 会被 `int(...)` 截断后误当 3 接受；malformed `ChecksumSHA256` 或
非字符串 stored SHA 会以 `TypeError` 逃出公共合同。现有 `raise ... from provider_error` 还会让标准
traceback 重新打印已脱敏异常的 secret endpoint，本任务必须一起关闭。

## 2. 目标与用户价值

让 S3 Driver 最外层和流式 body 都遵守同一受控错误边界，确保 endpoint 在启动前确定性拒绝畸形
输入，并让 `exists()` 表示“该 ref 对应的有效 CAS 对象存在”，而不是“同名 key 随便存在”。本次只
做最终边界收口，不新增功能。

## 3. 必读与冻结输入

1. `serve/docs/tasks/wp-00c2-r1-artifact-s3-correctness.md`
2. `serve/docs/manifests/wp-00c2-r1-artifact-s3-correctness.md`（冻结，只读）
3. `serve/app/services/artifact_store/drivers/s3.py`
4. `serve/app/config.py`
5. `serve/tests/trading/{test_v2_artifact_s3.py,test_v2_config.py}`

## 4. 允许修改

```text
serve/app/services/artifact_store/drivers/s3.py
serve/app/config.py
serve/tests/trading/test_v2_artifact_s3.py
serve/tests/trading/test_v2_config.py
serve/docs/manifests/wp-00c2-r2-artifact-stream-endpoint.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 `.env.example`、依赖、Artifact contracts/service/local、实施合同、数据库、Redis、
main/lifespan、V1、原 00c2/00c2-r1 task/manifest 和清单外文件。

## 5. 精确整改合同

### 5.1 StreamingBody 的异常也属于 Provider 边界

- 提取一个供 full GET 与 Range 共用的有界读取 helper，输入 body、读取上限和安全 operation label。
- `body.read(limit)` 抛 `_TRANSPORT_ERRORS` 或其他 `BotoCoreError` 时，统一转换为脱敏
  `ArtifactStorageError`；不得包含原始异常消息、endpoint、userinfo、credential 或签名。
- unknown-result transport 分类必须覆盖 Botocore 的 HTTP/connection 基类；至少用基础
  `HTTPClientError` 注入证明 PUT 会做一次 HEAD reconcile，用 `ResponseStreamingError` 证明 body
  read 会收敛为 StorageError。
- 不捕获 `ArtifactIntegrityError`、`KeyboardInterrupt/SystemExit` 或任意应用编程错误；finally 仍在
  成功/失败路径关闭 body。
- full GET 继续最多读 `stored_size+1`，Range 继续最多读 `requested+1`；不得为修异常改回无界读取。
- 从外部 Provider 异常转换公共异常时禁止保留会被 traceback 打印的原始 cause/context；格式化完整
  traceback 也不得出现注入的 endpoint/secret。内部 Artifact 异常链不受影响。

### 5.2 Provider 响应类型必须 fail-closed

- `pm-stored-sha256` 必须先确认是 `str` 且精确匹配 64 位小写 hex，再执行 regex。
- S3 `ContentLength` 在 HEAD/full GET/Range 三条路径都必须是 `type(value) is int`（拒绝 bool、float、
  numeric string），且非负；Range 还必须精确等于 requested。不得用 `int(...)` 截断/转换 Provider
  响应后再接受。
- `ChecksumSHA256` 若存在，只允许标准 base64 字符串/bytes；使用严格 base64 校验。非字符串、非法
  alphabet/padding 或解码后不是 SHA-256 digest，统一 `ArtifactIntegrityError`，不得泄出 TypeError。
- 保持 checksum 缺失时仍由 `pm-stored-sha256` 校验 stored bytes 的现有语义。

### 5.3 Endpoint 必须是可解析的严格 origin

- 输入不得含首尾/内部 whitespace、ASCII control、`?`、`#` 或 `@`；不能依赖解析后空字符串的
  truthiness 判断分隔符是否出现。
- scheme 精确为 `http|https`，hostname 非空；path 仅允许空或 `/`。
- 必须访问并验证 parsed `port`：非数字、空 port、0 或 >65535 均在 Settings 构造时转为
  `ValueError/ValidationError`，不得延迟到 boto client。
- 合法覆盖：`https://s3.example.com`、`https://s3.example.com:9000`、
  `http://127.0.0.1:9000`（仍要求 `ALLOW_INSECURE_HTTP=true`）及合法 bracketed IPv6 origin。
- 不 normalize、不静默删除字符；原 endpoint 仍原样交给 boto3。

### 5.4 `exists()` 必须验证 CAS 身份

- 用现有 `head(ref)`/等价共享路径完成一次 HEAD + 五项 metadata + ContentLength 校验；有效才返回
  `True`，确定 404 才返回 `False`。
- metadata 缺失/冲突或 size 非法必须抛 `ArtifactIntegrityError`；403/5xx/BotoCoreError 继续为
  `ArtifactStorageError`，不得伪装成 `False`。
- 仍只发一次 HEAD，不增加 GET 或第二次请求。

## 6. 必测证据

1. full GET 与 Range 的 body 分别注入包含 secret endpoint 的 `ReadTimeoutError` 和
   `ResponseStreamingError`：均为脱敏 `ArtifactStorageError`，body 已 close，read limit 仍精确；
   PUT 注入基础 `HTTPClientError` 后只做一次 HEAD reconcile，不进行无条件 PUT。
2. `ChecksumSHA256` 的 int/object、非法字符、错误 padding，以及 metadata stored SHA 的非字符串，
   全部 `ArtifactIntegrityError`；HEAD/GET/Range 的 float/string/bool ContentLength 全部拒绝。
   对上述 Provider 异常格式化完整 traceback，断言 secret/endpoint 不出现且外部 cause 被抑制。
3. 上述 6 个已复现非法 endpoint，加空 port、port 0、whitespace/control；合法 DNS+port、HTTP
   opt-in、IPv6 全通过。
4. `exists()` 的有效/404/metadata 缺失/metadata 冲突/403/NoCredentials 矩阵；每例 HEAD 调用数 1。
5. 原 91 个 S3 tests、45 config tests 和所有回归继续通过。

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

创建且只创建 `serve/docs/manifests/wp-00c2-r2-artifact-stream-endpoint.md`。Manifest 必须记录三组
边界及响应类型测试、真实命令/数量、blocker、回滚和可复现 SHA；更新两个索引为 `DONE（待审）`，
保持 R2 为当前任务，等待用户再次说“完成”。不得创建 00d、提交或推送。

风险只在 URL 边界和流式异常分类；用表驱动测试锁定，不通过扩大 catch-all、放宽 metadata、吞
close/read 异常或修改冻结 manifest 处理。回滚只恢复本任务允许的四个代码/测试文件与索引并删除
R2 manifest；无数据库、secret 或生产对象副作用。

## 9. 非目标

- 不实现 multipart/streaming upload/delete/retention、DB、HTTP API、main/lifespan。
- 不新增配置/依赖，不改公共 Protocol，不做真实 Provider conformance。
- 不重构整个 Driver，不修改已通过的条件写/重试/created/dedupe 语义。
- 不用 TODO、skip、无条件 PUT 或只改 manifest 文字完成整改。
