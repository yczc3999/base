# COMPLETION MANIFEST — WP-00c1-r2 · Artifact Store 最终边界整改

- Work package: `WP-00` 子任务 `WP-00c1-r2`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00c1-r1`（REMEDIATION_REQUIRED，三个剩余 P1）；本整改接受前 `WP-00c2` 保持阻塞
- 规范依据: `serve/docs/tasks/wp-00c1-r2-artifact-final-boundaries.md`；`serve/docs/v2-implementation-contract.md` §3/§13–§15；`serve/docs/performance-cache-database-design.md` §6.2/§11/§14/§15
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/services/artifact_store/service.py` | 修改 | P1-1 `put_bytes` 写前 stored 硬上限（任何 Driver 调用前拒绝膨胀对象）；P1-2 `_decode` 增加完整 frame EOF 证明（bounded pass 证明 ≤max 后，`decompressobj().eof` + `unused_data/unconsumed_tail` 确认 frame 完整） |
| `serve/app/services/artifact_store/drivers/local.py` | 修改 | P1-3 `target.exists()` 快路径与 `os.link` EEXIST loser 分支返回 `created=False` 前都执行 `_fsync_dir(parent)`，失败抛 `ArtifactStorageError` |
| `serve/tests/trading/test_v2_artifact_store.py` | 修改 | 新增 stored 上限拒绝（spy 调用 0 次）、无 content-size 完整 roundtrip、截尾 1/2/5 verify=False fail-closed、checksum 截尾 EOF fail-closed |
| `serve/tests/trading/test_v2_artifact_local.py` | 修改 | 新增 existing/EEXIST 分支 directory fsync 计数与失败注入；改写 R1 收敛测试为"移除故障后 fsync 计数增加" |
| `serve/docs/manifests/wp-00c1-r2-artifact-final-boundaries.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` | 修改 | 00c1-r2 标 DONE（待审） |
| `serve/docs/tasks/README.md` | 修改 | 审查记录 WP-00c1-r1 → REMEDIATION_REQUIRED，追加 00c1-r2（当前任务保持） |

范围外未动：contracts/__init__、config、依赖、Redis、数据库、S3、main/lifespan、V1、旧 task/manifest（冻结）。

---

## 2. 实现内容

### 2.1 P1-1 写前双尺寸硬上限

- `_encode()` 返回后、构造 Driver 调用前，检查 `len(stored) <= ARTIFACT_MAX_OBJECT_BYTES`；
  原文或 stored 任一超限都抛 `ArtifactTooLarge`，Driver `put_if_absent` 调用次数为 0，
  磁盘无最终对象、无 temp。
- 确定性不可压缩 fixture：`b"".join(sha256(f"seed-{i}") for i in range(32))`（1024 bytes），
  真实 zstd 长度 1034 > 1024，证明膨胀对象被拒绝。
- raw、auto、跨 level 去重行为不变（既有测试回归通过）。

### 2.2 P1-2 bounded decode 后证明完整 frame EOF

- 保留 R1 的 declared-size 预检与 `max+1` bounded streaming；不重新引入已知 content-size
  frame 的无界分配。
- bounded pass 未超限后，**EOF pass**：`decompressobj().decompress(data)` 后检查
  `eof` 必须为 True（否则截断/未到帧尾 → `ArtifactIntegrityError`）且
  `unused_data`/`unconsumed_tail` 必须为空（尾随非法数据 → `ArtifactIntegrityError`）。
- 实测确认：`decompressobj().eof` 对 content-size / 无 content-size / 带 checksum 的
  frame 在完整时 True、截尾 1/2/5 bytes 时 False；带 checksum 的截尾 frame 即使 stream
  层已产出全量内容，eof 仍为 False。EOF pass 的内存有界：bounded pass 已证明输出 ≤ max，
  二次解码同输入同输出，最多分配 max。
- 异常分类不依赖易变错误字符串：超限 `ArtifactTooLarge`；截断/损坏/未到 EOF/尾随
  `ArtifactIntegrityError`（来自 eof/unused_data 状态或 `ZstdError` 类型）。
- 无 content-size 的完整 frame 保持正常 roundtrip（`verify=True/False` 均可读）；截掉
  末尾 1、2、5 bytes 即使 `verify=False` 也抛 `ArtifactIntegrityError`；同类 bomb 仍为
  `ArtifactTooLarge`（bounded pass 先触发）。

### 2.3 P1-3 existing/EEXIST 完成 directory durability

- `target.exists()` 快路径在返回 `created=False` 前对 `target.parent` 执行 `_fsync_dir()`。
- `os.link` 触发 `FileExistsError`（并发 loser）时同样先 `_fsync_dir(parent)` 再返回
  `created=False`。
- 两分支的 directory fsync 失败均抛 `ArtifactStorageError`，不得报告成功。
- 首次 link 后 directory fsync 失败：保留已完整发布的不可变 target、temp 清理；移除故障后
  重试**实际调用** directory fsync（计数增加）并成功返回 `created=False`，由 Service 做内容
  验证。测试统计 fsync 调用而非仅凭 target 存在认定"收敛"。
- 并发 winner/loser、temp 清理、不覆盖语义无回归。

---

## 3. 命令与真实结果

```bash
# 1) compileall
python3 -m compileall -q app tests
# → exit 0

# 2) artifact store service
.venv/bin/pytest -q tests/trading/test_v2_artifact_store.py
# → 35 passed in 0.80s

# 3) artifact local driver
.venv/bin/pytest -q tests/trading/test_v2_artifact_local.py
# → 24 passed in 0.32s

# 4) artifact driver contract（无改动回归）
.venv/bin/pytest -q tests/trading/test_v2_artifact_driver_contract.py
# → 18 passed in 0.07s

# 5) redis keys（原 WP-00c1 回归）
.venv/bin/pytest -q tests/trading/test_v2_redis_keys.py
# → 16 passed in 0.01s

# 6) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 159 passed in 1.48s

# 7) 全量回归
.venv/bin/pytest -q
# → 370 passed, 1 warning in 3.63s（1 warning = conftest event_loop 弃用告警，同 R1）

# 8) git diff --check
git diff --check
# → 无输出，exit 0

# 9) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

### §7 三条独立探针（真实输出）

```text
PROBE1 OK: 0 driver calls, 0 objects -> stored object 1034 bytes exceeds ARTIFACT_MAX_OBJECT_BYTES 1024
PROBE2 OK: complete_roundtrip=True, trunc-1 verify=False -> ArtifactIntegrityError
PROBE2b OK: no-content-size bomb still ArtifactTooLarge
PROBE3 OK: existing retry created=False, dir_fsync_calls=1
```

- 探针 1：原文 1024、zstd 膨胀 1034 > max 1024 → `put_bytes` 写前拒绝，spy 显示
  `put_if_absent` 调用 0 次、磁盘对象 0。
- 探针 2：无 content-size 完整 frame 正常 roundtrip；截掉末尾 1 byte 后在
  `verify=False` 下抛 `ArtifactIntegrityError`；同类 bomb 仍为 `ArtifactTooLarge`。
- 探针 3：已有对象重试走 existing 分支，`created=False` 且 directory fsync 计数 = 1
  （真正完成耐久化）。

---

## 4. 关键证据

### 4.1 P1-1 写前 stored 上限

- `test_stored_size_ceiling_rejected_before_driver`：确定性 1024→1034 膨胀 fixture，
  spy 断言 `put_if_absent` 调用 0 次、磁盘无对象无 temp。
- `test_max_object_boundary` 等既有测试证明 raw/auto 边界行为不变。

### 4.2 P1-2 完整 frame EOF

- `test_nocontent_size_complete_roundtrip`：无 content-size 完整 frame
  `verify=True/False` 均可读回原文。
- `test_nocontent_size_truncated_fails_closed[1/2/5]`：截尾 1/2/5 bytes，
  `get_bytes/verify/get_bytes(verify=False)` 全部 `ArtifactIntegrityError`。
- `test_checksummed_truncated_eof_fails_closed`：带 checksum 的 content-size frame 截掉
  末尾 1 byte，stream 层可产出全量内容，EOF pass 仍判 `ArtifactIntegrityError`。
- `test_zstd_bomb_without_content_size_rejected_bounded` /
  `test_zstd_bomb_declared_size_rejected_before_allocation` /
  `test_compression_bomb_rejected_on_read`：同类 bomb 仍为 `ArtifactTooLarge`，bounded pass
  先触发，EOF pass 不产生二次分配。

### 4.3 P1-3 existing/EEXIST directory durability

- `test_existing_branch_calls_dir_fsync`：普通 existing 分支 `created=False` 前实际调用
  directory fsync（计数 ≥1）。
- `test_existing_branch_dir_fsync_failure_no_success`：existing 分支 fsync 失败抛
  `ArtifactStorageError`，target 未被改动。
- `test_eexist_branch_calls_dir_fsync`：强制 EEXIST loser 分支同样调用 directory fsync
  （计数 ≥1），temp 清理、target 完整。
- `test_eexist_branch_dir_fsync_failure_no_success`：EEXIST 分支 fsync 失败抛
  `ArtifactStorageError`。
- `test_dir_fsync_failure_keeps_published_target_and_converges`（改写）：首次 dir fsync
  失败保留完整 target；移除故障后重试 fsync 计数增加（≥1）且 `created=False` 收敛。

### 4.4 回归

- 原 WP-00c1 / R1 的 Redis、raw/zstd/auto、篡改、路径安全、跨 level 去重、bounded bomb、
  range spy、线程异常收集全部无回归：store 35、local 24、contract 18、redis 16、trading
  159、全量 370、双前缀 0、`git diff --check` 干净。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞说明：
- zstd 完整帧判定采用"bounded pass（≤max 证明）→ EOF pass（eof/unused_data 证明）"
  两阶段解码，对每个 zstd 对象读取多一次解码；正确性优先，性能留后续基准衡量
  （任务 §8 明确）。
- S3 driver、DB artifact 表、retention 等属后续 WP，未实现。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/services/artifact_store/service.py \
  serve/app/services/artifact_store/drivers/local.py \
  serve/tests/trading/test_v2_artifact_store.py \
  serve/tests/trading/test_v2_artifact_local.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -f serve/docs/manifests/wp-00c1-r2-artifact-final-boundaries.md
```

- 回到 WP-00c1-r1 交付状态；保留 R1 交付与所有冻结 manifest；无数据库、secret 或生产数据
  副作用；`git diff --check` 干净。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00c1-r2-artifact-final-boundaries.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
40cfd5f7be09ff368ecad9737f5febaccde96a2b3314309cc6d30b0c379b3220
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00c1-r2-artifact-final-boundaries.md | sha256sum
```
