# WP-00c1 — Local Content-addressed Artifact Store

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00c1-artifact-local.md`。

## 1. 目标与价值

建立 V2 的不可变大对象底座：对原始来源、AI 输入输出、完整订单簿和回放文件按原文 SHA-256
寻址、去重、校验和安全读取。本任务完成协议、Service 和本地 Driver；不实现 S3、数据库表或
retention 状态机。

前置 `WP-00b-r2` 已审查接受。审查保留一个不影响 Redis 正确性的 P2：
`build_redis_key()` 当前通过 `join` 生成 `namespace:~::segment`，与约定格式
`namespace:~:segment` 多一个冒号。本任务开始时先做一行修正和精确格式测试。

## 2. 必读文档

1. `/code/pollymarket/v2/AGENTS.md`
2. `serve/docs/v2-implementation-contract.md` §3、§12–§15
3. `serve/docs/performance-cache-database-design.md` §1、§6.2、§11、§14、§15
4. `serve/docs/ai-observability-replay-design.md` §3.4、§3.5、§7
5. `serve/docs/tasks/README.md`
6. `serve/docs/manifests/wp-00b-r2-redis-identity-test-stability.md`

## 3. 允许修改

```text
serve/app/services/redis_keys.py
serve/app/config.py
serve/.env.example
serve/requirements.txt
serve/app/services/artifact_store/__init__.py
serve/app/services/artifact_store/contracts.py
serve/app/services/artifact_store/service.py
serve/app/services/artifact_store/drivers/local.py
serve/tests/trading/test_v2_redis_keys.py
serve/tests/trading/test_v2_artifact_driver_contract.py
serve/tests/trading/test_v2_artifact_store.py
serve/tests/trading/test_v2_artifact_local.py
serve/docs/manifests/wp-00c1-artifact-local.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

共 8 个生产/配置文件，符合单任务上限。禁止修改现有 `app/services/storage/`、数据库
Model/Alembic、Redis client、main.py、V1 和清单外文件。

## 4. 强制前置修正

- 将 Redis key 精确格式修正为 `{namespace}:~:{encoded_segment...}`，不得出现 `:~::`。
- `build_redis_key(namespace)` 零动态段行为必须明确且测试；建议返回 `{namespace}:~:`。
- 保持 R2 的 namespace 校验、类型严格、编码无碰撞性质不变。
- `test_v2_redis_keys.py` 增加 exact-string 断言；全量 Redis 回归必须继续通过。

## 5. Artifact 公共合同

`contracts.py` 只定义 immutable typed contract 和受控异常：

- frozen `ArtifactRef`：`sha256、original_size、stored_size、mime、compression、
  storage_driver、locator、storage_version`。
- frozen `ArtifactHead` 与 `PutResult(created, head)`。
- `ArtifactDriver` Protocol：`put_if_absent、get、get_range、head、exists、health、aclose`。
- 受控异常至少包括：`ArtifactNotFound、ArtifactTooLarge、ArtifactIntegrityError、
  ArtifactRangeUnsupported、ArtifactPathError、ArtifactStorageError`。

约束：SHA 为 64 位小写 hex；size 非负；locator 必须是相对安全对象名；compression 只允许
`none|zstd`；不得返回模糊 bool 代替错误原因。

## 6. ArtifactStore Service

公开接口固定为：

```text
put_bytes(data, mime, compression="auto") -> ArtifactRef
get_bytes(ref, verify=None) -> bytes
get_range(ref, start, end) -> bytes
verify(ref) -> ArtifactHead
health() -> typed status
aclose() -> None
```

行为：

1. SHA-256 永远计算**未压缩原文**；禁止用 locator、压缩体或 Python hash 代替。
2. locator 固定为：
   - `cas/v1/sha256/ab/cd/<sha>.raw`
   - `cas/v1/sha256/ab/cd/<sha>.zst`
3. `compression=auto|none|zstd`：auto 仅在达到阈值且 zstd 后确实更小时使用；zstd level
   配置化。同一配置与输入必须得到相同 ref/locator。
4. 相同内容/codec 并发 put 必须去重；目标已存在时读取并验证，绝不静默覆盖冲突内容。
5. `get_bytes/verify` 按配置检查 original size、stored size 和 SHA；任何篡改 fail-closed。
6. `get_range` 的 `start` inclusive、`end` exclusive。Service 只对 `compression=none` 提供原文
   range；zstd 明确抛 `ArtifactRangeUnsupported`，不得假装随机访问压缩原文。
7. 对象超过 `ARTIFACT_MAX_OBJECT_BYTES` 在写入和解压读取时都拒绝，防止压缩炸弹。
8. Service 不选择 retention、不判断 artifact 是否可供交易、不访问 DB/Redis/env/global settings；
   Driver 和配置通过构造参数注入。

## 7. Local Driver

- root 由构造参数注入并在启动时 resolve；只接受合同生成的相对 locator。
- 拒绝绝对路径、`..`、NUL、root 外路径以及任一已有 symlink 路径分量。
- 临时文件必须与目标同文件系统：写入 → flush → file fsync → **no-replace 原子发布** →
  directory fsync。不得使用会覆盖并发胜者的裸 `os.replace()`。
- 推荐以 `os.link(temp,target)` 实现 Linux 原子 no-replace；`EEXIST` 返回 `created=False`，其他
  错误清理 temp 后抛受控异常。
- 对象不可变，不暴露 delete 业务 API；`get_range` 支持 stored bytes 的合法半开区间。
- 本地 Driver 仅用于开发、测试和有界 spool；`health()` 必须报告 root/可写性但不泄漏 secret；
  `aclose()` 幂等。

## 8. 配置与依赖

在 typed Settings 与 `.env.example` 增加：

```text
ARTIFACT_DRIVER=local
ARTIFACT_LOCAL_ROOT=./storage/v2-artifacts
ARTIFACT_INLINE_THRESHOLD_BYTES=16384
ARTIFACT_COMPRESSION_THRESHOLD_BYTES=16384
ARTIFACT_ZSTD_LEVEL=6
ARTIFACT_MAX_OBJECT_BYTES=67108864
ARTIFACT_VERIFY_ON_READ=true
```

阈值必须交叉校验：`0 <= inline <= compression <= max`；zstd level 使用库支持的安全范围。
`requirements.txt` 增加有上界的 `zstandard` 运行时依赖。不得加入 S3 SDK或未使用依赖。

## 9. 必测证据

- `ArtifactRef` frozen 与所有字段校验。
- 相同原文产生相同 SHA；不同原文不同；相同内容/codec 单线程与并发均只发布一个对象。
- raw/zstd/auto 往返；auto 对不可压缩数据保持 raw，对可压缩大对象选择 zstd。
- 空文件、阈值边界、最大值、超限和解压炸弹拒绝。
- raw range 正常、空范围、越界/反向范围拒绝；zstd Service range 明确拒绝。
- 修改 stored bytes 后 `verify/get` 抛 `ArtifactIntegrityError`。
- `../`、绝对路径、NUL、symlink escape 全部拒绝。
- 模拟写/fsync/publish 失败后：无最终脏对象、无临时文件；并发胜者内容不被覆盖。
- health、not found、`aclose()` 幂等；公共 Service/Driver 均不存在 delete API。
- 所有测试使用 `tmp_path`，不得写默认 artifact root。

## 10. 验收命令

```bash
cd /code/pollymarket/v2/serve
.venv/bin/pip install -r requirements-dev.txt
python3 -m compileall -q app tests
.venv/bin/pytest -q tests/trading/test_v2_redis_keys.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_driver_contract.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_store.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_local.py
.venv/bin/pytest -q tests/trading
.venv/bin/pytest -q
git diff --check
```

## 11. 交付

创建且只创建 completion manifest：

```text
serve/docs/manifests/wp-00c1-artifact-local.md
```

Manifest 必须记录修改文件、真实命令结果、Redis P2 关闭证据、原子 no-replace/并发去重、
完整性/路径安全/压缩上限证据、blocker、回滚和可复现 SHA-256。更新 manifests 索引为
`DONE（待审）`；tasks 索引保持 00c1 为当前任务，等待用户说“完成”。不得自行创建 00c2，
不得提交或推送 Git。

## 12. 非目标

- 不实现 S3/multipart、数据库 artifact 表、retention/archive、HTTP artifact API 或 UI。
- 不接入 main/lifespan，不改 Base Storage，不保存 secret，不使用 pickle。
- 不用 TODO、空壳、删除测试或放宽完整性断言通过验收。
