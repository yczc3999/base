# COMPLETION MANIFEST — WP-00c1-r1 · Artifact Store 有界读取、Range 与持久化整改

- Work package: `WP-00` 子任务 `WP-00c1-r1`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00c1`（REMEDIATION_REQUIRED，四项 P1）；本整改接受前 `WP-00c2` 保持阻塞
- 规范依据: `serve/docs/tasks/wp-00c1-r1-artifact-correctness.md`；`serve/docs/v2-implementation-contract.md` §3/§13–§15；`serve/docs/performance-cache-database-design.md` §6.2/§11/§14/§15
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/services/artifact_store/contracts.py` | 修改 | `ArtifactRef` 全字段运行时校验（size 非 bool 整数且非负 / mime 无 CR·LF·NUL / driver 白名单 local·s3 / version 固定 cas/v1 / locator 精确匹配规范路径 / raw 尺寸相等）；新增 `LOCATOR_VERSION`+`build_locator`；`ArtifactHead` 校验与语义明确；`ArtifactDriver.put_if_absent(candidate, data)` 单签名 |
| `serve/app/services/artifact_store/service.py` | 修改 | head fail-closed 预检；有界 zstd 解码（声明 content-size 预检 + streaming 最多 max+1）；硬上限任何 I/O 前拒绝且 verify=False 不可绕过；range 严格语义只走 `driver.get_range`；跨 level 去重返回**实际** stored size；driver/version/locator 身份校验 |
| `serve/app/services/artifact_store/drivers/local.py` | 修改 | `put_if_absent(candidate, data)`；file write/flush/fsync、link、stat/read、directory fsync 的 OSError 全部转 `ArtifactStorageError`；`get()` 按 `stored_size+1` 有界读取并验证长度；`get_range` 范围与实际 size 校验；`head` original 取元数据、stored 取实际；`health()` 唯一临时 probe |
| `serve/app/services/artifact_store/__init__.py` | 修改 | 导出 `LOCATOR_VERSION`、`build_locator` |
| `serve/tests/trading/test_v2_artifact_driver_contract.py` | 修改 | 18 个合同测试：新校验规则 + `put_if_absent(candidate,data)` 签名锁定 |
| `serve/tests/trading/test_v2_artifact_store.py` | 修改 | 29 个 Service 测试：跨 level 去重、bounded bomb、range spy、head 预检、driver 身份、线程异常收集 |
| `serve/tests/trading/test_v2_artifact_local.py` | 修改 | 20 个 Driver 测试：三类故障注入、有界 get、head 语义、health probe、并发线程异常 |
| `serve/docs/manifests/wp-00c1-r1-artifact-correctness.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` | 修改 | 00c1-r1 标 DONE（待审） |
| `serve/docs/tasks/README.md` | 修改 | 当前任务指向 00c1-r1；审查记录 WP-00c1 → REMEDIATION_REQUIRED，追加 00c1-r1 |

范围外未动：config、依赖、Redis、数据库、main/lifespan、S3、V1、原 `WP-00c1` task/manifest（已冻结）。

---

## 2. 实现内容

### 2.1 P1-1 真正有界的读取与解压

- `get_bytes()`/`verify()`/`get_range()` 在读取 body 前先 `driver.head(ref)`；head 与 ref 的
  stored size 不一致立即 `ArtifactIntegrityError`（fail-closed 预检，不读 body）。
- `ref.original_size > max` 或 `ref.stored_size > max` 在任何 I/O 前直接 `ArtifactTooLarge`；
  硬上限独立于 `verify=` 参数，`verify=False` 不可绕过（有专项测试）。
- Local `get()` 按 `ref.stored_size + 1` 有界读取并验证恰好等于声明长度；stat/read 的
  OSError 转受控异常，长度不符抛 `ArtifactIntegrityError`；禁止 `Path.read_bytes()`。
- zstd 解码**无"完整解压后才看 len"路径**：
  1. `zstd.frame_content_size(data)` 声明 > max → 分配输出前直接 `ArtifactTooLarge`；
  2. content size 未知 → `stream_reader` 分块读取，最多读 `max + 1` bytes（实测对
     10,000,000-byte bomb 只分配请求的块，不整段物化）；
  3. 超限 `ArtifactTooLarge`；`ZstdError`（损坏/尾随非法 frame）→ `ArtifactIntegrityError`；
     声明 content size 但产出不足（截断）→ `ArtifactIntegrityError`。

### 2.2 P1-2 Range 唯一语义

- raw 对象强制 `original_size == stored_size`（contract + Service 双层）。
- Service 严格校验 `0 <= start <= end <= ref.original_size`（int 且非 bool）；负数、反向、
  末端越界全部 `ValueError`，不允许 Python slice 静默截断。
- Service 只调用 `driver.get_range(ref, start, end)`，绝不调用 `driver.get()`；返回长度必须
  恰为 `end - start`，否则 `ArtifactIntegrityError`。spy Driver 证据：`get()` 调用次数为 0。
- Local `get_range` 校验范围在**实际文件 size** 内并只读请求区间；越界 `ArtifactIntegrityError`。
- zstd 仍由 Service 在调用 Driver 前抛 `ArtifactRangeUnsupported`。

### 2.3 P1-3 CAS identity、Head 与跨配置去重

- `ArtifactRef` 构造即全字段校验；locator 与 `sha256+compression` 精确匹配 `build_locator`
  （不接受 `./`、别名路径、错误 suffix）；`storage_driver` 仅 `local|s3`；`storage_version`
  固定 `cas/v1`；size 非 bool 整数且非负；mime 非空无 CR/LF/NUL；raw 尺寸相等。
- `ArtifactHead.stored_size` 来自底层实际对象（stat）；`original_size` 是对象元数据。
  Local Driver 不再把 zstd 的 original size 写成 stored size（有 head 语义测试）。
- `put_if_absent(candidate, data)` 传入完整 candidate ref（Protocol、Local、Service、测试一次
  同步，无两套接口）；签名由 inspect 锁定为 `["self", "candidate", "data"]`。
- 已存在 locator 时不要求现存压缩长度等于本次候选长度：Driver 返回实际 stored size，
  Service 有界读取、解压并校验原文 SHA/original size，成功后返回含**实际** stored size 的
  canonical ref。level 1 → level 22 跨配置重复 put 只保留一个对象、两次 ref 均可读取。
- Service 读取前校验 `ref.storage_driver == driver.driver_name` 与规范 storage version/locator，
  禁止 S3 ref 静默交给 Local（有专项测试）。

### 2.4 P1-4 持久化失败与真实测试证据

- file write/flush/fsync、`os.link`、stat/read、directory fsync 的 OSError 一律转
  `ArtifactStorageError`；无裸异常、无静默成功。
- file fsync / link 失败：target 不存在、temp 已清理。
- directory fsync 在 link 后失败：抛 `ArtifactStorageError`，**不返回 created=True**；已完整
  发布的不可变 target 保留（不删成不确定状态），temp 清理；重试命中已有 target → created=False，
  通过验证收敛（有专项测试）。
- `health()` 用唯一临时 `.health-*.probe`，不覆盖/删除固定名称既有文件；`aclose()` 幂等。
- 并发 put 收集线程异常（不能只检查成功结果 set），8 线程无异常、单对象、无 `.tmp-*` 残留。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests
# → exit 0

# 2) artifact driver contract
.venv/bin/pytest -q tests/trading/test_v2_artifact_driver_contract.py
# → 18 passed in 0.09s

# 3) artifact store service
.venv/bin/pytest -q tests/trading/test_v2_artifact_store.py
# → 29 passed in 0.56s

# 4) artifact local driver
.venv/bin/pytest -q tests/trading/test_v2_artifact_local.py
# → 20 passed in 0.23s

# 5) redis keys（原 WP-00c1 回归）
.venv/bin/pytest -q tests/trading/test_v2_redis_keys.py
# → 16 passed in 0.01s

# 6) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 149 passed in 1.20s

# 7) 全量回归
.venv/bin/pytest -q
# → 360 passed, 1 warning in 3.38s（1 warning = conftest event_loop 弃用告警，同 WP-00b-r2）

# 8) git diff --check
git diff --check
# → 无输出，exit 0

# 9) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

### §7 两条独立探针（真实输出）

```text
PROBE1 OK: ArtifactTooLarge before allocation: zstd frame declares content size 10000000, exceeds max 1024
PROBE2 OK: locator same=True, stored1=22, stored2=22, object_count=1, read_back_ok=True
```

- 探针 1：content-size 10,000,000 > max 1024 时未返回任何超限 bytes，解码前直接
  `ArtifactTooLarge`。
- 探针 2：同一原文 level 1 再 level 22 重复写入，第二次成功去重；locator 相同、stored_size
  均取实际（22）、磁盘对象数 = 1、两次 ref 读回一致。

---

## 4. 关键证据

### 4.1 有界解码（P1-1）

- 声明 content-size bomb（10M > max 1K）：分配输出前 `ArtifactTooLarge`（探针 1 + 测试
  `test_zstd_bomb_declared_size_rejected_before_allocation`）。
- 无 content-size bomb：streaming 有界读取，超 `max + 1` 抛 `ArtifactTooLarge`（测试
  `test_zstd_bomb_without_content_size_rejected_bounded`）；实测只物化请求块，未整段分配。
- `verify=False` 不能绕过硬上限（`test_verify_flag_cannot_bypass_hard_ceiling`）。
- head 与 ref stored size 不一致在读取 body 前直接拒绝
  （`test_get_bytes_rejects_stored_size_mismatch_before_body`）。

### 4.2 Range（P1-2）

- spy Driver：raw range 只调用 `head + get_range`，`get()` 调用次数为 0
  （`test_get_range_uses_driver_get_range_only`）。
- 末端等于 size 合法、末端大于 size 拒绝、负数/反向/bool 边界全部拒绝
  （`test_raw_range` / `test_raw_range_invalid`）。
- Local Driver 范围超过实际文件 size → `ArtifactIntegrityError`（`test_get_range_driver`）。

### 4.3 CAS identity 与跨 level 去重（P1-3）

- 同一原文 level 1 → level 22：第二次成功，locator 相同、stored_size 都取实际、对象数 1、
  两次 ref 可读（`test_cross_level_dedup_returns_actual_stored` + 探针 2）。
- 压缩对象 `head.original_size` 与原文一致、`head.stored_size` 与文件一致
  （`test_cross_level_dedup_stored_size_matches_file` / `test_head_reports_actual_stored_and_original_meta`）。
- 非 canonical locator、driver/version 不匹配、raw size 不等、空/控制字符 MIME 全部拒绝
  （合同测试 `test_ref_rejects_noncanonical_locator` / `test_ref_validates_*` / `test_ref_validates_mime`）。
- S3 ref 交给 local driver → `ArtifactStorageError`（`test_ref_driver_mismatch_rejected`）。

### 4.4 持久化失败注入（P1-4）

- file fsync 失败：`ArtifactStorageError`、target 不存在、无 temp
  （`test_file_fsync_failure_no_target_no_temp`）。
- `os.link` 失败（非 EEXIST）：`ArtifactStorageError`、target 不存在、无 temp
  （`test_link_failure_no_target_no_temp`）。
- directory fsync 失败（按 fd 类型注入，仅目录 fd 抛错）：`ArtifactStorageError`、不返回
  created=True、已发布 target 完整保留、temp 清理、重试 created=False 收敛
  （`test_dir_fsync_failure_keeps_published_target_and_converges`）。
- 8 线程并发 put：无未捕获线程异常、单 created=True、内容一致、无 `.tmp-*`
  （`test_concurrent_put_collects_thread_exceptions` / Service 并发测试同）。
- `health()` 唯一 probe，不覆盖固定名称文件、无 `.probe` 残留
  （`test_health_ok_unique_probe`）。

### 4.5 回归

- 原 WP-00c1 Redis、raw/zstd/auto、篡改、路径安全与全量测试无回归：redis_keys 16、
  tests/trading 149、全量 360、双前缀 0。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞说明：
- `ARTIFACT_DRIVER=s3` 校验放行但 driver 未实现，属 WP-00c2（本任务不实现）。
- 无 content-size 且被截断的 zstd frame，解码层无法单独判定（zstd 无长度信号），但 Service
  的 original_size + SHA 校验必然 fail-closed；常规对象均由本 Service 写入（自带 content size）。
- 非目标逐条遵守：未实现 S3/multipart/HTTP streaming/DB 表/retention/archive/UI；未加 delete API；
  未降校验；未用 TODO/mock/吞异常/改 manifest 掩盖。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/services/artifact_store/contracts.py \
  serve/app/services/artifact_store/service.py \
  serve/app/services/artifact_store/drivers/local.py \
  serve/app/services/artifact_store/__init__.py \
  serve/tests/trading/test_v2_artifact_driver_contract.py \
  serve/tests/trading/test_v2_artifact_store.py \
  serve/tests/trading/test_v2_artifact_local.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/docs/manifests/wp-00c1-r1-artifact-correctness.md
```

- 回到 WP-00c1 交付状态；不回滚已完成的 Redis/config/依赖变更；不删除旧 completion manifest。
- 无数据库迁移、secret 或生产数据副作用；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00c1-r1-artifact-correctness.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
119e0710e20468e3c7b5a18a915fad50eac3831ce4b2179007091dd0f339cc85
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00c1-r1-artifact-correctness.md | sha256sum
```
