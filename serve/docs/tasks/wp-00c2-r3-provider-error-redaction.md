# WP-00c2-r3 — S3 Provider ClientError Traceback 脱敏整改

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00c2-r3-provider-error-redaction.md`。
> 最后更新：2026-08-08 12:50 EDT。依赖：`WP-00c2-r2` 已交付但审查未接受；本任务接受前
> `WP-00d` 继续阻塞。

## 1. 审查结论与真实问题

R2 的 StreamingBody、HTTP transport、响应类型、endpoint origin 和 `exists()` 整改均通过；独立
复验为 114 S3、55 config、18 contract、35 store、24 local、312 trading、523 full tests，manifest
SHA `081126a9ddc31322c2d45bfd75bd1622461c436bce7eeb7ee5a58fb8b0774d7a` 一致。

唯一 P1 位于 `serve/app/services/artifact_store/drivers/s3.py:286-306`：`put_if_absent()` 的
`except ClientError` 中，409 重试耗尽、400/501 拒绝和一般错误三个转换分支没有显式
`raise ... from None`。注入消息含 `https://user:TOPSECRET@host` 的 HTTP 500 `ClientError` 时，公共
异常文本虽已脱敏，但 `traceback.format_exc()` 仍通过隐式 context 打印 `TOPSECRET`，违反 R2 §5.1。

## 2. 目标与用户价值

关闭 S3 Driver 最后一个 Provider 异常泄密路径，使所有由外部 `ClientError` 转换出的公共异常在
完整 traceback 中也不包含 endpoint、userinfo、credential、签名或 Provider 原始消息。本任务仅做
该边界修复，不新增功能、不重构 Driver。

## 3. 必读

1. `serve/docs/tasks/wp-00c2-r2-artifact-stream-endpoint.md`
2. `serve/docs/manifests/wp-00c2-r2-artifact-stream-endpoint.md`（冻结，只读）
3. `serve/app/services/artifact_store/drivers/s3.py`
4. `serve/tests/trading/test_v2_artifact_s3.py`

## 4. 允许修改

```text
serve/app/services/artifact_store/drivers/s3.py
serve/tests/trading/test_v2_artifact_s3.py
serve/docs/manifests/wp-00c2-r3-provider-error-redaction.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 config、依赖、Artifact contracts/service/local、`.env.example`、实施合同、数据库、Redis、
main/lifespan、V1，以及任何既有 task/manifest。不得顺手处理 Stubber 测试内部异常或扩展 catch-all。

## 5. 精确实现合同

1. `put_if_absent()` 的 `except ClientError` 内所有向外抛出路径必须显式抑制外部异常 context：
   - 409 / `ConditionalRequestConflict` 达到重试上限；
   - 400/501 Provider 不支持条件写或 checksum；
   - 其他 `ClientError` 经 `_storage_error()` 转换。
2. 412 / `PreconditionFailed` 仍按既有语义调用 `head(candidate)` 并返回去重结果；不得改变请求次数。
3. 不改变 HTTP 状态分类、409 尝试次数、unknown-result reconcile、错误消息、CAS、checksum、metadata、
   body 读取或 endpoint 逻辑。
4. 不捕获 `ArtifactError`、`KeyboardInterrupt/SystemExit`、测试 Stubber assertion 或应用编程错误。
5. 新测试必须格式化完整 traceback，并同时断言公共异常类型、`__cause__ is None`、
   `__suppress_context__ is True`，以及 secret/endpoint/Provider 原始消息均未出现。

## 6. 必测矩阵

至少覆盖三个独立 `ClientError` 分支：

| 分支 | 注入 | 期望 |
|---|---|---|
| 一般错误 | HTTP 500 `InternalError`，message 含 `TOPSECRET` endpoint | `ArtifactStorageError`，完整 traceback 脱敏 |
| 不支持能力 | HTTP 400 或 501，message 含 secret | fail-closed `ArtifactStorageError`，完整 traceback 脱敏 |
| 冲突耗尽 | HTTP 409 连续达到配置 attempts，message 含 secret | 尝试次数不变，`ArtifactStorageError`，完整 traceback 脱敏 |

原 114 个 S3 tests 必须全部继续通过。

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

创建且只创建 `serve/docs/manifests/wp-00c2-r3-provider-error-redaction.md`，记录修改文件、三个
ClientError 分支证据、真实命令/数量、blocker、回滚及可复现 SHA；更新两个索引为 `DONE（待审）`，
保持 R3 为当前任务，等待用户再次说“完成”。不得创建 WP-00d、提交或推送。

风险仅为异常链语义。回滚只恢复本任务允许的代码/测试/索引并删除 R3 manifest；无数据库、secret
或生产对象副作用。

## 9. 非目标

- 不新增 Provider 能力、重试或网络请求。
- 不修改错误分类、公开 Protocol、配置或依赖。
- 不修改冻结 manifest，不用 TODO、skip 或只改文档完成整改。
