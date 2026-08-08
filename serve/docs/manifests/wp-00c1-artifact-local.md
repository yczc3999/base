# COMPLETION MANIFEST — WP-00c1 · Local Content-addressed Artifact Store

- Work package: `WP-00` 子任务 `WP-00c1`
- 状态: **DONE（待审）**
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 前置: `WP-00b-r2`（ACCEPTED）+ P2 强制前置修正
- 规范依据: `serve/docs/v2-implementation-contract.md` §3/§12–§15 → `serve/docs/performance-cache-database-design.md` §1/§6.2/§11/§14/§15 → `serve/docs/ai-observability-replay-design.md` §3.4/§3.5/§7
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/services/redis_keys.py` | 修改 | **P2 修正**：`build_redis_key` 精确格式 `{namespace}:~:{encoded...}`，无 `:~::`；零动态段返回 `{namespace}:~:` |
| `serve/app/config.py` | 修改 | 新增 `ARTIFACT_*` typed 字段 + 阈值交叉校验（inline≤compression≤max；driver∈{local,s3}） |
| `serve/.env.example` | 修改 | 新增 Artifact Store 配置段（7 项） |
| `serve/requirements.txt` | 修改 | 新增 `zstandard>=0.22,<1`（有上界运行时依赖） |
| `serve/app/services/artifact_store/__init__.py` | **新增** | 包导出（contracts + service） |
| `serve/app/services/artifact_store/contracts.py` | **新增** | frozen `ArtifactRef/ArtifactHead/PutResult`、`ArtifactDriver` Protocol、7 类受控异常 |
| `serve/app/services/artifact_store/service.py` | **新增** | `ArtifactStore`：put_bytes/get_bytes/get_range/verify/health/aclose |
| `serve/app/services/artifact_store/drivers/local.py` | **新增** | `LocalArtifactDriver`：路径安全 + 原子 no-replace 发布 |
| `serve/tests/trading/test_v2_redis_keys.py` | 修改 | 新增 exact-string 格式断言 + 零段行为测试 |
| `serve/tests/trading/test_v2_artifact_driver_contract.py` | **新增** | 10 个合同测试 |
| `serve/tests/trading/test_v2_artifact_store.py` | **新增** | 20 个 Service 测试 |
| `serve/tests/trading/test_v2_artifact_local.py` | **新增** | 13 个 Local Driver 测试 |
| `serve/docs/manifests/wp-00c1-artifact-local.md` | **新增** | 本 manifest |
| `serve/docs/manifests/README.md` | 修改 | 00c1 标 DONE（待审） |
| `serve/docs/tasks/README.md` | 修改 | 审查记录追加 00c1（当前任务保持） |

范围外未动：现有 `app/services/storage/`、数据库 Model/Alembic、Redis client、main.py、V1 文件。

---

## 2. 实现内容

### 2.1 Redis P2 强制前置修正（§4）

- `build_redis_key` 修正为 `f"{ns}:~:" + ":".join(encoded)`，产出 `pm:v2:prod:cache:~:a:b`（无 `:~::`）。
- 零动态段 → 返回 `{namespace}:~:`（显式定义并测试）。
- 保持 R2 的 namespace 校验、类型严格、编码无碰撞性质。

### 2.2 `contracts.py`（§5）

- frozen `ArtifactRef`：`sha256(64 小写 hex)/original_size/stored_size/mime/compression(none|zstd)/storage_driver/locator/storage_version`，`__post_init__` 全量校验。
- frozen `ArtifactHead`、`PutResult(created, head)`。
- `ArtifactDriver` Protocol（runtime_checkable）：`put_if_absent/get/get_range/head/exists/health/aclose`。
- 受控异常：`ArtifactError` 基类 + `ArtifactNotFound/ArtifactTooLarge/ArtifactIntegrityError/ArtifactRangeUnsupported/ArtifactPathError/ArtifactStorageError`。

### 2.3 `service.py`（§6）

- SHA-256 恒对**未压缩原文**（`sha256_hex`）；locator 固定 `cas/v1/sha256/ab/cd/<sha>.raw|.zst`。
- `put_bytes(data, mime, compression=auto|none|zstd)`：auto 仅在达阈值且压缩后确实更小时选 zstd；写入超 `ARTIFACT_MAX_OBJECT_BYTES` 抛 `ArtifactTooLarge`。
- 去重：Driver `put_if_absent` no-replace；命中已有对象时**读取并验证内容 sha**，绝不静默覆盖冲突内容。
- `get_bytes(ref, verify=None)` / `verify(ref)`：按配置校验 original/stored size + SHA，任何篡改 `ArtifactIntegrityError`；解压超限 `ArtifactTooLarge`。
- `get_range(ref, start, end)`：半开区间；仅 `compression=none` 支持，zstd 抛 `ArtifactRangeUnsupported`。
- Service 不选 retention、不判断业务有效性、不访问 DB/Redis/env/global settings；Driver 与配置经构造注入。

### 2.4 `drivers/local.py`（§7）

- root 构造 resolve；`_resolve_locator` 拒绝绝对路径/`..`/NUL/root 外路径/任一 symlink 路径分量。
- **原子 no-replace 发布**：temp 同目录 → 写入 → flush → `os.fsync` → `os.link(temp,target)` → directory `os.fsync`；`EEXIST` 返回 `created=False`（并发胜者不被覆盖），其他错误清理 temp 后抛受控异常。
- 无 delete API；`get_range` 支持 stored bytes 半开区间；`health()` 报告 root/可写性不泄漏 secret；`aclose()` 幂等。

---

## 3. 命令与真实结果

```bash
# 1) redis-cli ping
redis-cli ping
# → PONG

# 2) compileall
python3 -m compileall -q app tests
# → exit 0

# 3) redis_keys（含 P2 修正）
.venv/bin/pytest -q tests/trading/test_v2_redis_keys.py
# → 16 passed in 0.01s

# 4) artifact driver contract
.venv/bin/pytest -q tests/trading/test_v2_artifact_driver_contract.py
# → 10 passed in 0.07s

# 5) artifact store service
.venv/bin/pytest -q tests/trading/test_v2_artifact_store.py
# → 20 passed in 0.40s

# 6) artifact local driver
.venv/bin/pytest -q tests/trading/test_v2_artifact_local.py
# → 13 passed in 0.15s

# 7) tests/trading（含全部 WP-00）
.venv/bin/pytest -q tests/trading
# → 125 passed in 0.97s

# 8) 全量回归
.venv/bin/pytest -q
# → 336 passed, 1 warning in 3.11s

# 9) git diff --check
git diff --check
# → 无输出，exit 0

# 10) 双前缀残留
redis-cli --scan --pattern 'pm:it:*' | wc -l        # → 0
redis-cli --scan --pattern 'pm%3Ait%3A*' | wc -l    # → 0
```

---

## 4. 关键证据

### 4.1 Redis P2 关闭

- `build_redis_key("pm:v2:prod:cache","a","b") == "pm:v2:prod:cache:~:a:b"`；`:~::` 不出现；`count(":~:") == 1`。
- 零段：`build_redis_key("pm:v2:prod:cache") == "pm:v2:prod:cache:~:"`。
- R2 全量 keys/control/cache 测试继续通过（redis_keys 16，control/cache 无回归）。

### 4.2 原子 no-replace / 并发去重

- `os.link(temp,target)`：首次 `created=True`，重复 `created=False` 且内容不被覆盖。
- 8 线程并发 put 相同内容 → 唯一 locator，读回完整一致。
- 无 `.tmp-*` 残留。

### 4.3 完整性 / 压缩炸弹 / 范围

- 篡改 stored 字节 → `verify/get_bytes` 抛 `ArtifactIntegrityError`。
- 写入超限 → `ArtifactTooLarge`；手工构造的压缩炸弹（stored 小、解压 100000 > max 1024）读取与 verify 均抛 `ArtifactTooLarge`。
- raw 半开区间正确；空范围；负/反向范围拒绝；zstd range 抛 `ArtifactRangeUnsupported`。

### 4.4 路径安全

- 绝对路径 / `..` / NUL / symlink 分量逃逸全部抛 `ArtifactPathError`；合法相对 locator 正常发布/读取。

### 4.5 约束

- 公共 Service/Driver 均无 delete API；`health()` 不泄漏 secret；`aclose()` 幂等；所有测试用 `tmp_path`，未写默认 artifact root。

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞说明：
- `ARTIFACT_DRIVER` 当前仅 `local` 有效（`s3` 校验放行但 driver 未实现，属 WP-00c2）。
- `ArtifactStore` 未接入 main/lifespan（留 WP-00d）。
- `LocalArtifactDriver._head` 对压缩对象记录的 original_size 即 stored_size（driver 无解码能力）；Service 层 verify 以解码后原文为准，语义正确。

---

## 6. 回滚方式

```bash
git checkout -- serve/app/services/redis_keys.py serve/app/config.py serve/.env.example \
  serve/requirements.txt serve/tests/trading/test_v2_redis_keys.py \
  serve/docs/manifests/README.md serve/docs/tasks/README.md
git rm -rf serve/app/services/artifact_store \
  serve/tests/trading/test_v2_artifact_driver_contract.py \
  serve/tests/trading/test_v2_artifact_store.py \
  serve/tests/trading/test_v2_artifact_local.py \
  serve/docs/manifests/wp-00c1-artifact-local.md
```

- 回到 WP-00b-r2 已提交状态；无 schema/密钥/生产数据改动，回滚无副作用。
- `zstandard` 依赖保留或按需移除（无业务影响）。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00c1-artifact-local.md`
- SHA-256（口径：删除本文件"恰好为 64 位十六进制"的哈希行后计算）：

```text
be062516eac9a64a888f3e30852e760508b68e4268872dc77f4bd6bc2123df19
```

验证：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00c1-artifact-local.md | sha256sum
```
