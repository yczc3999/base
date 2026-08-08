# WP-00c1-r2 — Artifact Store 最终边界整改

> 状态：READY。执行模型：DeepSeek V4 Flash。完成 manifest 固定为
> `serve/docs/manifests/wp-00c1-r2-artifact-final-boundaries.md`。
> 最后更新：2026-08-08 14:35 EDT。依赖：`WP-00c1-r1` 已交付但审查未接受；本任务接受前
> `WP-00c2` 继续阻塞。

## 1. 前置审查与真实状态

R1 的主要整改有效：83 个目标测试、149 个 trading 测试、360 个全量测试通过，manifest SHA
`119e0710e20468e3c7b5a18a915fad50eac3831ce4b2179007091dd0f339cc85` 一致；跨 level
不同压缩长度探针也成功去重。但审查复现出三个尚未关闭的 P1：

1. **压缩后 stored size 没有写前上限检查**：max=1024、原文恰好 1024 bytes、显式 zstd 后
   1034 bytes，`put_bytes()` 仍落盘并返回 ref；该 ref 随后无法读取。
2. **未知 content-size 的截断 frame 没有 EOF 校验**：截掉末尾 1 byte 后，stream reader 可返回
   `b""` 且不报错；当前 `_decode()` 将其当完成。`verify=False` 时可直接返回损坏结果。
3. **directory fsync 失败后的重试没有真正收敛 durability**：已有 target 和并发 EEXIST 分支
   直接返回 `created=False`，完全不调用 directory fsync。注入探针显示重试 `fsync_calls=0`。

## 2. 目标与用户价值

仅关闭上述三个确定性边界，使 Artifact Store 不会创建“写得进、读不出”的对象，截断 zstd
永远 fail-closed，并让 directory fsync 失败后的重试真正完成耐久化。完成后才能冻结公共合同并
进入 S3 Driver。

## 3. 必读与确认决策

1. `serve/docs/tasks/wp-00c1-r1-artifact-correctness.md`
2. `serve/docs/manifests/wp-00c1-r1-artifact-correctness.md`（冻结，只读）
3. `serve/app/services/artifact_store/{service.py,drivers/local.py}`

确认决策：保留 R1 的 canonical ref、Driver Protocol、bounded streaming、异常分类和 no-replace
设计；本任务不再改公共类型或扩大功能。

## 4. 允许修改

```text
serve/app/services/artifact_store/service.py
serve/app/services/artifact_store/drivers/local.py
serve/tests/trading/test_v2_artifact_store.py
serve/tests/trading/test_v2_artifact_local.py
serve/docs/manifests/wp-00c1-r2-artifact-final-boundaries.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

禁止修改 contracts/__init__、config、依赖、Redis、数据库、S3、main/lifespan、V1、旧 task/manifest
及清单外文件。

## 5. 精确实现合同

### 5.1 写前双尺寸硬上限

- `_encode()` 返回后、构造 Driver 调用前，检查 `len(stored) <= ARTIFACT_MAX_OBJECT_BYTES`。
- 原文或 stored 任一超限都抛 `ArtifactTooLarge`；Driver `put_if_absent` 调用次数必须为 0，磁盘
  无最终对象、无 temp。
- 使用确定性不可压缩 1024-byte fixture，先断言真实 zstd 长度大于 1024，再证明写入被拒绝。
- raw、auto 和跨 level 去重行为不得变化。

### 5.2 bounded decode 后必须证明完整 frame EOF

- 保留 R1 的 declared-size 预检和 `max+1` bounded streaming；不得重新引入已知 content-size
  frame 的无界分配。
- bounded pass 未超限后，必须使用能够证明完整 frame 结束的 zstd API/状态检查；只有确认 EOF
  才返回 bytes。若需要第二次校验，必须先由 bounded pass 证明输出不超过 max，再执行。
- 完整 frame 输出超过 max → `ArtifactTooLarge`；截断、损坏、未到 EOF、非法尾随数据 →
  `ArtifactIntegrityError`。异常分类不得靠易变错误字符串猜测。
- 对 `write_content_size=False` 的完整 frame 保持正常 roundtrip；分别截掉末尾 1、2、5 bytes，
  即使调用 `get_bytes(ref, verify=False)` 也必须抛 `ArtifactIntegrityError`。

### 5.3 existing/EEXIST 必须完成 directory durability

- `target.exists()` 快路径和 `os.link(...)=EEXIST` 并发 loser 分支，在返回 `created=False` 前都必须
  对 target 的 parent 执行 `_fsync_dir()`；失败抛 `ArtifactStorageError`，不得报告成功。
- 首次 link 后 directory fsync 失败允许保留完整 target；移除故障后重试必须实际调用 directory
  fsync、随后返回 `created=False`，再由 Service 做内容验证。
- 测试必须统计 fsync 调用，而不是仅凭 target 存在认定“收敛”；已有对象分支与 EEXIST 分支各有
  独立测试。并发 winner/loser、temp 清理和不覆盖语义不得回归。

## 6. 验收证据

必须新增并通过：

1. `stored_size > max` 的真实 zstd 与 spy Driver 测试：调用数 0、对象数 0。
2. 无 content-size 完整 frame roundtrip；截尾 1/2/5 bytes 在 `verify=False` 下均
   `ArtifactIntegrityError`；同类 bomb 仍为 `ArtifactTooLarge`。
3. directory fsync 首次失败后，第二次 put 的 directory fsync 计数增加并成功返回
   `created=False`；普通 existing 与强制 EEXIST loser 的 fsync 失败都不得返回成功。
4. R1 全部测试、trading、全量回归、manifest SHA 与 `git diff --check` 通过。

## 7. 验收命令

```bash
cd /code/pollymarket/v2/serve
python3 -m compileall -q app tests
.venv/bin/pytest -q tests/trading/test_v2_artifact_store.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_local.py
.venv/bin/pytest -q tests/trading/test_v2_artifact_driver_contract.py
.venv/bin/pytest -q tests/trading
.venv/bin/pytest -q
git diff --check
```

## 8. 交付、风险与回滚

创建且只创建 `serve/docs/manifests/wp-00c1-r2-artifact-final-boundaries.md`。Manifest 必须记录
三个 P1 的真实测试与探针、全部命令、blocker、回滚和可复现 SHA；更新两个索引为
`DONE（待审）`，保持 R2 为当前任务，等待用户再次说“完成”。不得创建 00c2、提交或推送。

风险仅在 zstd 完整帧判定与每次 dedup 多一次 directory fsync 的延迟；正确性优先，性能留后续
基准衡量，不得用吞异常换速度。整改失败时只回滚本任务允许的两个生产文件与测试/索引，保留
R1 交付和所有冻结 manifest；无数据库、secret 或生产数据副作用。

## 9. 非目标

- 不再审计或重构其他 Artifact 设计，不实现 S3/DB/HTTP/retention/UI。
- 不修改 max 含义、不放宽 verify、不以 checksum 缺失为由接受未到 EOF 的 frame。
- 不用错误字符串、TODO、skip、吞异常或只改 manifest 文字通过验收。
