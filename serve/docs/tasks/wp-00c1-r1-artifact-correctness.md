# WP-00c1-r1 — Artifact Store 有界读取、Range 与持久化整改

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00c1-r1-artifact-correctness.md`。
> 最后更新：2026-08-08 14:10 EDT。依赖：`WP-00c1` 已交付且审查结论为
> `REMEDIATION_REQUIRED`；本整改接受前 `WP-00c2` 保持阻塞。

## 1. 前置审查结论

`WP-00c1` 的主体结构、Redis P2 修正和 59 个目标测试均通过；全量回归为 `336 passed`，
manifest SHA-256 `be062516eac9a64a888f3e30852e760508b68e4268872dc77f4bd6bc2123df19`
一致。但交付暂不接受，原因是以下四项可复现的 P1 正确性问题：

1. **解压上限在分配后才生效**：`ZstdDecompressor.decompress(..., max_output_size=N)` 对带
   content-size 的 frame 会忽略 `N`。审查实测 `N=1024` 时仍先返回 10,000,000 bytes；当前代码
   随后才报超限，不能防止 OOM。
2. **Range 绕过 Driver 且越界静默截断**：Service 调用 `driver.get()` 全量读取，不调用
   `driver.get_range()`；`[10,11)`、`[0,999)` 对 10-byte 对象分别返回空值和完整对象，而不是拒绝。
   这既违反半开区间合同，也会让后续 S3 Range 失效。
3. **同内容/同 codec 在压缩配置变更后不能去重**：同一原文先以 zstd level 1 写入，再以 level
   22 写入，同一 locator 因压缩体长度不同抛 `ArtifactStorageError`。同时 Local Driver 对 zstd
   `ArtifactHead.original_size` 错报为 stored size，公共元数据语义不成立。
4. **持久化错误被吞掉，交付证据不实**：directory fsync 的任意 `OSError` 被忽略并返回
   `created=True`；原任务和 manifest 声称覆盖 write/fsync/publish failure injection，但现有 Local
   测试没有这些用例。

## 2. 目标与用户价值

在进入 S3 Driver 前把 CAS 公共合同一次修正到可复用状态：任何读取都有真实内存上限，Range
不会退化成全量下载，压缩参数变化不破坏内容去重，持久化失败不会伪装成功。整改只修当前
Artifact Store，不开发新功能。

## 3. 必读文档

1. `/code/pollymarket/v2/AGENTS.md`
2. `serve/docs/tasks/wp-00c1-artifact-local.md`
3. `serve/docs/manifests/wp-00c1-artifact-local.md`（已冻结，只读）
4. `serve/docs/v2-implementation-contract.md` §3、§13–§15
5. `serve/docs/performance-cache-database-design.md` §6.2、§11、§14、§15

## 4. 允许修改

```text
serve/app/services/artifact_store/__init__.py
serve/app/services/artifact_store/contracts.py
serve/app/services/artifact_store/service.py
serve/app/services/artifact_store/drivers/local.py
serve/tests/trading/test_v2_artifact_driver_contract.py
serve/tests/trading/test_v2_artifact_store.py
serve/tests/trading/test_v2_artifact_local.py
serve/docs/manifests/wp-00c1-r1-artifact-correctness.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 config、依赖、Redis、数据库、main/lifespan、S3、V1、原 `WP-00c1` task/manifest 和
清单外文件。不得顺手实现 `WP-00c2`。

## 5. 精确整改合同

### 5.1 真正有界的读取与解压

- `get_bytes()` 与 `verify()` 在读取 body 前先调用 `driver.head(ref)`，以实际 stored size 做
  fail-closed 预检；head 与 ref 的 stored size 不一致立即 `ArtifactIntegrityError`。
- Local `get()` 必须按 `ref.stored_size + 1` 有界读取并验证恰好等于声明长度，禁止
  `Path.read_bytes()` 对未知大小文件无界分配。读取/stat/I/O 错误统一转换为受控异常。
- zstd 解码不得对不可信 frame 使用一次性 `decompress()` 后再检查长度：
  1. frame 声明 content size 且大于上限时，在分配输出前抛 `ArtifactTooLarge`；
  2. content size 未知时使用 streaming reader，最多读取 `max + 1` bytes；
  3. 超限抛 `ArtifactTooLarge`，损坏/截断/尾随非法数据抛 `ArtifactIntegrityError`。
- `ref.original_size > max` 或 `ref.stored_size > max` 可在任何 I/O 前直接拒绝；不得关闭
  `verify` 来绕过硬上限。

### 5.2 Range 唯一语义

- raw 对象必须满足 `original_size == stored_size`。
- Service 严格校验 `0 <= start <= end <= ref.original_size`；超界、负数或反向全部抛
  `ValueError`，不允许 Python slice 静默截断。
- Service 必须调用 `driver.get_range(ref,start,end)`，不得调用 `driver.get()`；返回长度必须恰好
  为 `end-start`，否则 `ArtifactIntegrityError`。
- Local Driver 同样检查范围与实际文件 size，并只读取请求区间；zstd 仍由 Service 在调用 Driver
  前抛 `ArtifactRangeUnsupported`。

### 5.3 CAS identity、Head 与跨配置去重

- `ArtifactRef` 对所有字段做运行时校验：size 必须是非 bool 整数且非负；mime 非空且无
  CR/LF/NUL；`storage_driver` 仅 `local|s3`；`storage_version` 固定 `cas/v1`；locator 必须与
  `sha256 + compression` 精确匹配规范路径，不接受 `./`、别名路径或错误 suffix；raw size 必须相等。
- 明确 `ArtifactHead`：`stored_size` 必须来自底层实际对象；`original_size` 是 ref/对象元数据，
  不是压缩体大小。Local Driver 不得再把 zstd 的 original size写成 stored size。
- 可调整 `put_if_absent` 参数为传入完整 candidate `ArtifactRef`，使 Driver 获得原文元数据；
  Protocol、Local 和测试必须一次同步，不保留两套接口。
- 已存在 locator 时不得要求“现存压缩体长度等于本次候选压缩体长度”。Driver 返回实际
  stored size；Service 有界读取、解压并校验原文 SHA/original size，成功后返回包含**实际**
  stored size 的 canonical ref。
- 必测：同一原文/同一 locator 先 level 1、再 level 22，第二次成功去重、只保留一个对象且两次
  ref 均可读取；若现存对象解压后 SHA 不符则 fail-closed。
- Service 读取时校验 `ref.storage_driver == driver.driver_name` 与 canonical storage version/locator，
  禁止把 S3 ref 静默交给 Local（反之亦然）。

### 5.4 持久化失败与真实测试证据

- file write/flush/fsync、link publish、stat/read 和 directory fsync 的 `OSError` 都转换为
  `ArtifactStorageError`；不得裸异常或静默成功。
- file fsync/link 失败：最终 target 不存在、temp 清理。directory fsync 在 link 后失败：不得返回
  `created=True`；允许保留已经完整发布的不可变 target，但 temp 必须清理，重试必须通过验证并
  收敛。禁止把完整 target 删除成不确定状态。
- 对已存在 target 返回成功前也要走明确的 durability/verify 路径；并发 loser 不覆盖 winner。
- `health()` 使用唯一临时 probe，不覆盖/删除固定名称的既有文件；`aclose()` 继续幂等。
- 测试必须用 monkeypatch 分别注入 file fsync、`os.link`、directory fsync 失败，并断言返回类型、
  target 状态、temp 清理和重试收敛；并发测试必须收集线程异常，不能只检查成功结果的 set。

## 6. 必测证据

- 带 content-size 与不带 content-size 的 zstd bomb 均在 `max + 1` 边界受控拒绝；实现中无“完整
  解压后才看 len”的路径。
- fake/spy Driver 证明 raw range 只调用 `head + get_range`，`get()` 调用次数为 0；末端等于 size
  合法，末端大于 size 拒绝。
- level 1 → level 22 跨配置重复 put 成功；压缩对象 `head.original_size` 与原文一致、
  `head.stored_size` 与文件一致。
- 非 canonical locator、driver/version 不匹配、raw size 不等、空/控制字符 MIME 全部拒绝。
- 三类故障注入与 8+ 并发 put 无未捕获线程异常、无临时文件、无脏/被覆盖对象。
- 原 WP-00c1 的 Redis、raw/zstd/auto、篡改、路径安全与全量测试无回归。

## 7. 验收命令

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests
.venv/bin/pytest -q tests/trading/test_v2_artifact_driver_contract.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_store.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_local.py
.venv/bin/pytest -q tests/trading/test_v2_redis_keys.py
.venv/bin/pytest -q tests/trading
.venv/bin/pytest -q
git diff --check
```

另在 manifest 中附两条独立探针的真实输出：

1. zstd content-size 大于 max 时，没有返回超限 bytes，直接 `ArtifactTooLarge`；
2. 同内容以 level 1/22 重复写入时第二次成功，locator 相同且实际对象数为 1。

## 8. 交付

创建且只创建 completion manifest：

```text
serve/docs/manifests/wp-00c1-r1-artifact-correctness.md
```

Manifest 必须逐项记录四个 P1 的修复、故障注入、跨 level 去重、bounded decode/range spy 的真实
结果、全部命令、blocker、回滚和可复现 SHA-256。更新 manifests 索引为 `DONE（待审）`；tasks
索引保持 R1 为当前任务，等待用户说“完成”。不得改写旧 manifest，不得创建 00c2，不得提交或
推送 Git。

## 9. 风险与回滚

- 风险集中在公共 Driver Protocol 调整；必须原子修改 Protocol、Local、Service 与全部测试，禁止
  兼容两套签名。S3 尚未实现，因此现在是修正合同的最后低成本窗口。
- bounded streaming 可能改变损坏 zstd 的异常分类；以本任务规定的
  `TooLarge`（容量）/`IntegrityError`（损坏）为唯一语义。
- 整改失败时回滚本任务允许文件到 `WP-00c1` 交付状态；不回滚已完成的 Redis/config/依赖变更，
  不删除旧 completion manifest。无数据库迁移、secret 或生产数据副作用。

## 10. 非目标

- 不实现 S3、multipart、HTTP streaming endpoint、DB artifact 表、retention、archive 或 UI。
- 不把 Local Driver 改成通用文件管理器，不添加 delete API，不降低 SHA/size/路径校验。
- 不用 TODO、mock-only 成功路径、删除测试、吞异常或改 manifest 文字掩盖缺陷。
