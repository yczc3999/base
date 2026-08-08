# COMPLETION MANIFEST — WP-00c2-r3 · S3 Provider ClientError Traceback 脱敏

- Work package: `WP-00` 子任务 `WP-00c2-r3`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00c2-r2`（REMEDIATION_REQUIRED，唯一 P1 = PUT `except ClientError` 三分支未显式 `from None`）；本整改接受前 `WP-00d` 继续阻塞
- 规范依据: `serve/docs/tasks/wp-00c2-r3-provider-error-redaction.md`；`serve/docs/v2-implementation-contract.md` §3/§12–§15
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/services/artifact_store/drivers/s3.py` | 修改 | `put_if_absent()` 的 `except ClientError` 内三条向外抛路径显式 `raise ... from None`：409 冲突耗尽、400/501 fail-closed、一般 ClientError 经 `_storage_error()`；412 去重语义与请求次数不变 |
| `serve/tests/trading/test_v2_artifact_s3.py` | 修改 | 114 → 117：新增 3 个完整 traceback 测试（一般 500 / 400 fail-closed / 409 耗尽），每个断言公共异常类型 + `__cause__ is None` + `__suppress_context__ is True` + 无 `TOPSECRET`/`user`/`secret-host` |
| `serve/docs/manifests/wp-00c2-r3-provider-error-redaction.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` | 修改 | 00c2-r3 标 DONE（待审） |
| `serve/docs/tasks/README.md` | 修改 | 当前任务指向 00c2-r3；审查记录 WP-00c2-r2 → REMEDIATION_REQUIRED，追加 00c2-r3 |

范围外未动：config、依赖、`.env.example`、Artifact contracts/service/local、实施合同、数据库、Redis、main/lifespan、V1、既有 task/manifest（已冻结）。**未遇 `BLOCKED_CONTRACT`**：公共 Protocol 与配置字段零改动。

---

## 2. 实现内容

`put_if_absent()` 的 `except ClientError as e:` 块内三条由外部 `ClientError` 转换出的公共异常
全部显式抑制外部异常 context：

1. **409 / `ConditionalRequestConflict` 达到重试上限**：`raise ArtifactStorageError(...) from None`。
2. **400/501 Provider 不支持条件写或 checksum（fail-closed）**：`raise ArtifactStorageError(...) from None`。
3. **其他 `ClientError` 经 `_storage_error()`**：`raise self._storage_error(e, "put_object") from None`。

- 412 / `PreconditionFailed` 仍按既有语义调用 `head(candidate)` 返回去重结果，请求次数不变。
- 未改变 HTTP 状态分类、409 尝试次数、unknown-result reconcile、错误消息、CAS、checksum、
  metadata、body 读取或 endpoint 逻辑；未捕获 `ArtifactError`、`KeyboardInterrupt/SystemExit`、
  测试 Stubber assertion 或应用编程错误；未扩展 catch-all。
- 效果：公共异常文本仅含 HTTP status/code；`__cause__ is None` + `__suppress_context__ is True`
  使 `traceback.format_exc()` 不打印含 endpoint/userinfo/credential/签名/原始消息的外部
  `ClientError`。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests
# → exit 0

# 2) typed config（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_config.py
# → 55 passed in 0.20s

# 3) artifact driver contract（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_driver_contract.py
# → 18 passed in 0.09s

# 4) artifact store service（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_store.py
# → 35 passed in 0.68s

# 5) artifact local driver（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_local.py
# → 24 passed in 0.30s

# 6) artifact S3 driver（新增 3，114 → 117）
.venv/bin/pytest -q tests/trading/test_v2_artifact_s3.py
# → 117 passed in 0.30s

# 7) 定向五项合计
.venv/bin/pytest -q tests/trading/test_v2_config.py \
  tests/trading/test_v2_artifact_driver_contract.py \
  tests/trading/test_v2_artifact_store.py \
  tests/trading/test_v2_artifact_local.py \
  tests/trading/test_v2_artifact_s3.py
# → 249 passed in 1.36s

# 8) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 315 passed in 1.74s

# 9) 全量回归
.venv/bin/pytest -q
# → 526 passed, 1 warning in 3.87s（1 warning = conftest event_loop 弃用告警，同各 WP）

# 10) git diff --check
git diff --check
# → 无输出，exit 0

# 11) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

---

## 4. 关键证据（§6 必测矩阵）

### 4.1 一般 ClientError（HTTP 500）

- `test_put_generic_client_error_traceback_redacted`：注入
  `ClientError("InternalError", 500, message="failed at https://user:TOPSECRET@host")` →
  `ArtifactStorageError`；`__cause__ is None`、`__suppress_context__ is True`；
  `traceback.format_exc()` 无 `TOPSECRET`/`user`/`secret-host`；PUT 调用数 1。

### 4.2 不支持能力 fail-closed（HTTP 400/501）

- `test_put_unsupported_client_error_traceback_redacted`：注入 400 `InvalidRequest` message 含
  `TOPSECRET` → `ArtifactStorageError`；`__cause__ is None`、`__suppress_context__ is True`；
  traceback 无 `TOPSECRET`/`user`；PUT 调用数 1（不降级覆盖写）。

### 4.3 冲突耗尽（HTTP 409）

- `test_put_409_exhaustion_traceback_redacted`：`conditional_retry_limit=1`，注入 409 message 含
  `TOPSECRET` → `ArtifactStorageError`；`__cause__ is None`、`__suppress_context__ is True`；
  traceback 无 `TOPSECRET`/`user`；PUT 调用数 1（attempts=1，首个 409 即耗尽，尝试次数不变）。

### 4.4 回归

- 原 114 个 S3 tests 全部继续通过；config 55、contract 18、store 35、local 24、trading 315、
  全量 526、双前缀 0、`git diff --check` 干净；R3 改动仅落在 `s3.py` + `test_v2_artifact_s3.py`
  两文件。

---

## 5. 未解决 blocker 与 Provider conformance 状态

无阻塞型 blocker。

非阻塞（如实记录，沿用 WP-00c2 系列）：
- **真实 Provider conformance 未执行**：真实 AWS/MinIO/R2 key 不是本任务输入；未来选定
  Provider 时须用同一合同补一次隔离 bucket conformance test；未做真实 Provider 测试不得宣称
  生产批准。默认测试经严格 fake client + Botocore Stubber，无网络、无真实凭据。
- `ARTIFACT_DRIVER` 默认仍为 `local`；S3 接入 main/lifespan 属 WP-00d。
- multipart/streaming upload 未实现（当前对象上限 64 MiB 远低于单次 PutObject 上限；未来另立任务）。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/services/artifact_store/drivers/s3.py \
  serve/tests/trading/test_v2_artifact_s3.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/docs/manifests/wp-00c2-r3-provider-error-redaction.md
```

- 回到 WP-00c2-r2 交付状态；原 00c2/00c2-r1/00c2-r2 completion manifest 与已冻结 R1/R2 不受影响；
  无数据库迁移、secret 或生产对象副作用；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00c2-r3-provider-error-redaction.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
acb0ed5796b9c1b76289c6dd984f5371dcdb70bb97ba8434e0d8b0990495679d
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00c2-r3-provider-error-redaction.md | sha256sum
```
