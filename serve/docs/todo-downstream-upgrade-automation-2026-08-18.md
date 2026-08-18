# Base 下游项目自动升级 TODO

**创建**：2026-08-18
**最后更新**：2026-08-18T11:40:47Z
**状态**：T0～T4 已完成；`base/v3.3.0`～`base/v3.4.0` 已发布；
T5 首轮证明 workflow-path push blocker，正在发布 v3.4.1 并重新 campaign
**发布路径**：已发布 `base/v3.3.0` receiver 基座 → 严格 backport
`base/v3.3.1` PR 正文合同 PATCH → `base/v3.3.2` 嵌套 E2E PATCH →
`base/v3.4.0` campaign（MINOR）→ `base/v3.4.1` workflow-path PATCH

---

## 1. 目标与用户价值

### 目标

在保留现有 `base/vX.Y.Z`、Release Manifest、`PROJECT.md`、
`BASE_UPDATES.md` 和 `scripts/sync-base-release.sh` 合同的前提下，增加一条
**由 Base 发布触发、在各下游仓库内执行、自动创建升级 PR** 的通用流程。

### 用户价值

- 不再逐个进入项目、逐个文件检查 Base 更新；
- 自动识别每个项目真实采用的 Base 版本，并计算 `CURRENT → TARGET` 更新路径；
- 无冲突项目自动产出可评审 PR，人工只处理冲突或验证失败的项目；
- 每个项目继续使用自己的数据库、CI secrets 和验证环境；
- 所有升级都有可复现的计划、验证证据、失败原因和回滚入口。

### 成功指标

首批至少以 3 个无凭据、纯合成 fixture 下游仓库进行演练并达到：

1. 无冲突项目自动创建升级 PR 的成功率为 100%；
2. 单项目无需人工准备升级分支或逐文件比较；
3. 相同项目、相同目标版本重复触发不会产生重复 PR 或重复账本记录；
4. 一个项目失败不阻断其他项目；
5. PR 明确展示源版本、目标版本、更新节点、验证结果和失败后的继续/回滚命令。

---

## 2. 已确认决定

1. **不替换现有同步内核**：单项目升级仍以
   `scripts/sync-base-release.sh TARGET_VERSION` 为唯一写入路径。
2. **先解决重复操作，不做大规模拆包**：本期只做项目清单、批量派发、自动升级
   PR、状态汇总与恢复闭环。
3. **Base 仓库保持产品无关**：真实项目名称、仓库地址、产品配置、数据库信息和
   Token 不进入 `/code/base`。
4. **真实版本以项目账本为准**：从下游默认分支的 `PROJECT.md` 读取
   `BASE_UPSTREAM_VERSION`；项目清单不重复维护一个可能漂移的版本字段。
5. **升级在下游仓库执行**：测试使用该项目自己的 CI secrets 和数据库边界，中央
   派发端不收集项目数据库凭据。
6. **按项目隔离失败**：批次结果允许部分成功；不得因为一个项目冲突而取消其他
   项目的升级。
7. **现有项目渐进接入**：未安装接收工作流的项目继续使用当前手工命令，不阻塞
   Base 发版。
8. **TODO 本身不触发发版**：开始实现并形成可复用能力时再执行 v3.3.0 发布合同。
9. **首次接入只走已发布 Base tag**：不复制未发布 workflow/runner。v3.3.0
   已提供 receiver 基座；T5 试点必须先采用 v3.3.1 正文 backport，再采用修复嵌套 merge 验证的 v3.3.2；
   v3.3.2 receiver 在自动升级到 v3.4.0 时可生成完整 PR 正文并完成全量验证。
10. **D1 托管范围**：首版只支持 GitHub.com；Base upstream 以已提交的
    canonical `OWNER/REPO` 身份恢复，receiver 不接受任意 upstream URL。
11. **D2 PR 策略**：新建 PR 为 Draft，不自动合并；幂等更新已有开放 PR 时保留其
    Draft/Ready 状态。
12. **D3 registry 位置**：真实 fleet registry 和脱敏 campaign evidence 由独立私有
    operator/ops 仓库维护，不进入 Base 或下游产品仓库。
13. **D4 触发方式**：Base 发布后由操作员人工批准一次 workflow dispatch，
    不在 tag 创建后自动开始 fleet campaign。
14. **D5 首批试点**：选择 3 个非生产/低风险下游，先手工采用 receiver
    基座，再验证后续自动升级。
15. **D6 v1 运维容量**：dispatch 按项目串行，每项目最多等待 run 30 分钟、
    cancel 后再等待 5 分钟，示例 workflow 总超时 120 分钟。一次 channel
    最多选中 3 个 enabled 项目；超限在任何 Provider 调用前 fail closed，大 fleet
    使用 channel 分批，每批最多 3 项。

---

## 3. 已确认的产品决定

用户于 2026-08-18 确认全部推荐值，D1～D6 不再阻塞对应任务。

| ID | 决定 | 已确认值 | 影响 | 状态 |
|---|---|---|---|---|
| D1 | 首版代码托管范围 | GitHub-only | 当前 Base remote 为 GitHub；可最小化首版复杂度 | ✅ |
| D2 | PR 是否自动合并 | 不自动合并 | 首版只创建 Draft PR，由项目负责人或现有规则合并 | ✅ |
| D3 | 项目清单由谁维护 | 独立 operator/ops 仓库 | 避免真实项目资料进入 Base | ✅ |
| D4 | Base 发布后如何启动批次 | 人工批准一次 workflow dispatch | 避免发布 tag 后立即影响全部项目 | ✅ |
| D5 | 首批试点项目 | 3 个非生产/低风险下游 | 用真实差异验证幂等、冲突和失败隔离 | ✅ |
| D6 | v1 单批容量 | 串行、每批最多 3 项 | 在 120 分钟 workflow 内容纳每项目 30m + cancel 5m 的最坏边界 | ✅ |

### 2026-08-18 三路研究结论

1. T1 可在 D1～D5 未确认时独立实施；只读发现 `PROJECT.md` 版本，完整
   `PROJECT.md`/`BASE_UPDATES.md` 一致性由 T2 在下游本地验证。
2. T2 必须补齐 fresh checkout 的 Base upstream 身份和目标依赖安装，不能假设 CI
   已有 `upstream` remote、`.venv` 或目标版本依赖。
3. T3 固定 GitHub REST `2026-03-10`：workflow dispatch 不再发送已移除的
   `return_run_details`，而是严格要求 `200` response 直接返回 run ID/URL；run-name
   只做交叉校验与响应丢失恢复，不能替代正常 response 中的 run ID。
4. 首次 receiver 通过正式 Base tag 同步，不复制随机 onboarding 文件。T5 识别出
   bootstrap 顺序约束：v3.3.0 中运行的 runner 不包含 T3 新增的四组 Manifest 分节；
   因此必须先从 v3.3.0 严格 backport 并发布 v3.3.1（只含 PR 正文增强/测试/
   标准发布元数据，不含 dispatcher）→ 真实同步发现嵌套 E2E Git-dir 缺陷并发布
   v3.3.2 PATCH → 3 试点手工采用 v3.3.2 → 主线基于 v3.3.2 发布 v3.4.0 →
   自动 v3.3.2→v3.4.0 campaign。
5. T1～T4 的通用实现属于 Base；真实 registry、Provider credential、三个试点项目、
   PR 和 evidence 属于外部 operator/downstream，T5 需要外部仓库上下文。

---

## 4. 范围与系统边界

### Base 仓库内的通用能力

```text
Base release tag
    → 通用派发合同与 registry schema
    → 下游接收工作流模板
    → 复用 sync-base-release.sh
    → PR 正文/状态报告生成器
```

### Base 仓库外的 operator 配置

```text
operator/ops repository
    ├── fleet/projects.json       # 真实项目仓库清单
    ├── workflow                 # 选择 TARGET_VERSION 并批量派发
    └── secret configuration     # Provider token，仅存 CI secret
```

### 下游执行路径

```text
接收 TARGET_VERSION
    → 校验目标 base tag
    → 从 PROJECT.md 读取 CURRENT_VERSION
    → 创建/复用升级分支
    → scripts/sync-base-release.sh TARGET_VERSION
    → 下游完整 CI
    → 新建 Draft PR / 更新既有开放 PR
    → 返回结构化结果
```

### 安全与权限边界

- registry 只允许声明式字段，不接受任意 shell command；
- Token 只从 CI secret/environment 读取，不进入参数、日志、报告或仓库文件；
- 自动化只写 `chore/base-vTARGET_VERSION` 命名空间的分支；
- 默认分支禁止直接 push；
- PR 权限、代码所有者和现有 branch protection 继续生效；
- 下游数据库 identity 必须继续通过 `scripts/check-database-boundary.py`；
- Base 不缓存、不代理、不集中保存任何下游数据库 secret。

---

## 5. 非目标

- 不在本期把 Base 后端拆成 Python packages；
- 不在本期把 Admin 拆成 npm packages；
- 不建立前端管理面板；首版使用 CI summary 和机器可读 JSON 报告；
- 不自动解决 Git 冲突；只精确报告冲突文件并给出恢复命令；
- 不自动修改下游产品代码、产品 migration 或产品配置；
- 不允许跨 MAJOR 静默升级；目标 MAJOR 不同必须显式批准；
- 不在 Base 仓库提交真实项目 registry、访问 Token、项目截图或运行产物；
- 不改变 `scripts/sync-base-release.sh` 的原子 merge + ledger 语义。

---

## 6. 任务清单

### T0 · 固化升级自动化合同与数据格式

**状态**：✅ 完成（2026-08-18）
**依赖**：无
**预计**：0.5 天

**实现内容**：

1. 定义外部 registry schema；最小字段：
   `schema_version`、`project_id`、`repository`、`default_branch`、`enabled`、
   `channel`、`provider`。GitHub v1 的 `repository` 格式固定为 `OWNER/REPO`，
   禁止 URL、credential 和 `.git` suffix。
2. 明确 `BASE_UPSTREAM_VERSION` 必须从下游 `PROJECT.md` 发现，registry 不接受
   `current_version` 作为权威输入。
3. 定义单项目结果 schema：
   `campaign_id`、`project_id`、`source_version`、`target_version`、`status`、
   `branch`、`pr_url`、`failed_stage`、`conflict_files`、`verification_summary`、
   `retry_command`、`rollback_command`。
4. 定义单项目结果状态：`planned`、`dispatched`、`up_to_date`、`pr_opened`、
   `conflict`、`verification_failed`、`blocked`、`dispatch_failed`。
5. 强制状态不变量：`pr_opened` 需升级分支、GitHub PR URL 和非空验证
   摘要；`conflict` 需 `failed_stage=merge` 与非空唯一冲突列表；其他失败
   状态需非空失败阶段，且所有未推送状态不得携带 branch/PR。
6. 使用 JSON Schema Draft-07 固化 canonical GitHub slug、安全 Git ref、无前导零
   core SemVer、单行非空文本 artifact 和长度上限。

**精确文件**：

- 新建 `scripts/schemas/base-downstream-registry.schema.json`
- 新建 `scripts/schemas/base-upgrade-result.schema.json`
- 更新 `serve/requirements-dev.txt`（加入固定下限的 `jsonschema` 测试依赖）
- 新建 `serve/tests/test_base_upgrade_contract.py`
- 新建 `serve/tests/fixtures/base-downstream-registry.valid.json`
- 新建 `serve/tests/fixtures/base-downstream-registry.invalid.json`
- 新建 `serve/tests/fixtures/base-upgrade-result.valid.json`
- 新建 `serve/tests/fixtures/base-upgrade-result.invalid.json`
- 新建 `serve/docs/downstream-upgrade-automation.md`
- 更新 `UPSTREAM.md`
- 更新 `CLAUDE.md`

**验收证据**：

```bash
cd serve
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/test_base_upgrade_contract.py -q
```

- 正例 fixture 通过；缺 required field、额外字段、非法 enum、URL/credential 形式的
  repository、secret/Token 字段和缺少 rollback/campaign ID 的反例全部失败；
- 每个状态的 branch/PR/失败阶段/冲突列表/验证摘要不变量有独立正反例；
- 文档明确区分 Base 通用文件、operator 实例配置和下游运行文件。

**完成证据（最后验证：2026-08-18T09:11:01Z）**：

- `tests/test_base_upgrade_contract.py`：147 passed；
- 后端完整回归：449 passed；Route Manifest：159 routes、1 mount；
- 上一轮 `check-base-release.py`、`check-database-boundary.py`、`git diff --check` 全部通过；
- 验证阶段额外收紧 result 状态不变量和 GitHub PR URL credential 边界；
- 每个反例声明并断言预期 validator/path，避免因其他错误产生假通过；
- T0 schema 只负责单文档形状；当时保留给 T1～T3 的关联责任中，T1 已实现
  `project_id`/repository 唯一性、batch/result/registry 关联和分支版本一致性；
  T2 已实现下游账本/tag/branch/PR 所有权，T3 仍负责 dispatch 与跨仓库回收事实。

**风险/回滚**：纯新增、未发布合同；回滚为删除两个 schema、合同测试与四个
fixture，移除 `serve/requirements-dev.txt` 的 `jsonschema` 测试依赖，删除合同文档，
并恢复 `UPSTREAM.md`、`CLAUDE.md`。

---

### T1 · 实现通用计划器与批次报告

**状态**：✅ 完成（2026-08-18）
**依赖**：T0
**预计**：1 天

**实现内容**：

1. 新增 `base-upgrade-campaign.py`，首批提供：
   - `validate-registry`：静态校验项目清单；
   - `plan`：读取 registry，发现各项目 `PROJECT.md` 版本并计算升级计划；
   - `summarize`：把各项目结果聚合为 Markdown summary 与 JSON artifact。
2. 版本比较复用 `scripts/base-update-ledger.py` 的 SemVer/Manifest 规则，不复制第二套
   版本算法；需要时将共享逻辑抽到小型公共模块，再由两个 CLI 引用。
3. `plan` 默认只读，不创建分支、不 push、不打开 PR。
4. 单个项目读取失败时记录该项目失败，继续处理其余项目。
5. GitHub v1 通过 Contents API 从 registry 指定的默认分支读取 `PROJECT.md`；认证只
   从 `BASE_UPGRADE_GITHUB_TOKEN` 环境变量读取。单请求 timeout 15 秒、最多 3 次
   尝试；5xx 和暂时性网络错误有界重试，403/429 只有具备 `Retry-After` 或
   `X-RateLimit-Reset` 时按 Provider 指示等待且单次封顶 60 秒，普通 4xx 不重试；
   `http.client.HTTPException` 协议中断（已覆盖 `IncompleteRead`/
   `BadStatusLine`）同网络错误按 1/2 秒、最多 3 次重试；Provider 原始响应限制
   2 MiB，解码后 `PROJECT.md` 限制 1 MiB；成功、
   读取中断和 `HTTPError` response 均显式关闭。
6. 目标版本必须存在于当前 Base checkout 的 Release Manifest；T1 使用已发布
   `base/v3.2.0` 验收，不依赖尚未创建的 v3.3.0 tag。
7. 为离线验收提供仅在 `--dry-run` 下可用的 `--project-state-fixture PATH`；fixture
   按 `OWNER/REPO` 提供模拟的 `PROJECT.md` 内容，生产运行禁止使用该参数。
8. 运行时校验 registry `project_id` 和 `repository` 分别唯一，避免同一仓库被一次
   campaign 重复派发；`up_to_date` 的 `source_version` 必须非空且等于
   `target_version`；汇总 result 时还要校验非空分支中的版本等于目标版本，不得把
   跨记录关联责任错当成 schema 已经完成。
9. T1 计划器与 T2 receiver 共用一个集中脱敏边界，所有日志、自由文本、
   summary 和 JSON artifact 在输出前脱敏；不依赖 schema 穷举 secret 值。
10. `plan` 的 `--campaign-id` 在 live Provider 模式必填；离线 `--dry-run` 未提供时
    使用确定性的 `plan-vTARGET_VERSION`，保证重复运行字节一致。
11. `channel` 默认 `stable`；只处理 `enabled=true` 且 channel 匹配的项目，其他项目
    不进入结果。`summarize --registry` 执行 result↔registry/repository 关联校验，T3
    生产调用时 registry 必填。
12. 新增 batch result schema，固定单一 `campaign_id`、`target_version` 与按
    `project_id` 排序的 results；T1 只验证 `PROJECT.md`，T2 再验证本地两个账本一致。
13. 默认拒绝跨 MAJOR 和所有降级；`--allow-major` 仅批准只读计划跨 MAJOR 计算，
    不触发派发或写入。
14. `scripts/requirements.txt` 统一声明 operator 脚本依赖，后端 dev requirements
    通过 `-r` 复用；共享 release 模块同时承载 canonical SemVer、Manifest
    `(source,target]` 选择、strict `PROJECT.md` parser 与集中递归脱敏。

**精确文件**：

- 新建 `scripts/base-upgrade-campaign.py`
- 新建 `scripts/lib/base_release.py`
- 新建 `scripts/requirements.txt`
- 新建 `scripts/schemas/base-upgrade-batch.schema.json`
- 更新 `serve/requirements-dev.txt`（复用 `scripts/requirements.txt`）
- 更新 `scripts/base-update-ledger.py`
- 新建 `serve/tests/test_base_upgrade_campaign.py`
- 新建 `serve/tests/fixtures/base-downstream-registry.json`
- 新建 `serve/tests/fixtures/base-project-states.json`
- 新建 `serve/tests/fixtures/base-upgrade-results.json`
- 更新 `serve/docs/downstream-upgrade-automation.md`
- 更新 `CLAUDE.md`
- 更新本 TODO 的状态与证据

**验收证据**：

```bash
cd serve
.venv/bin/pytest tests/test_base_update_ledger.py tests/test_base_upgrade_campaign.py -q
cd ..
python3 scripts/base-upgrade-campaign.py plan \
  --registry serve/tests/fixtures/base-downstream-registry.json \
  --project-state-fixture serve/tests/fixtures/base-project-states.json \
  --target-version 3.2.0 \
  --channel stable \
  --dry-run
```

- fixture 同时覆盖：已是目标版本、落后一个版本、跨多个版本、账本缺失、非法版本；
- 重复 `project_id`、未知字段、非法 repository、registry 内出现 Token/secret 字段时
  返回非 0；
- repository 大小写归一化后重复、batch 混合 campaign/target、`up_to_date` 版本
  不一致、branch-target 不一致时返回非 0；
- 输出稳定排序，重复运行结果一致；
- 失败项目不会终止其他项目计划；
- 输出不包含环境变量值、remote credential 或 URL 中的认证信息。

**完成证据（最后验证：2026-08-18T10:04:56Z）**：

- `tests/test_base_upgrade_contract.py` + `tests/test_base_update_ledger.py` +
  `tests/test_base_upgrade_campaign.py`：223 passed；
- `python3 -m py_compile scripts/lib/base_release.py scripts/base-update-ledger.py scripts/base-upgrade-campaign.py`：PASS；
- 上述 dry-run：退出码 0，输出 5 个 stable/enabled 项目并按 `project_id` 排序；
  状态为 `up_to_date=1`、`planned=1`、`blocked=3`；重复运行字节一致；
- selector 手工回归：合法但无 source Manifest 的 `0.9.0 → 1.0.0` 正常选择目标
  Manifest；summarize 手工脱敏回归：Bearer credential 被替换为 `[REDACTED]`，
  JSON artifact 不含原 secret；
- 后端完整回归：519 passed；Route Manifest：159 routes、1 mount；
- release metadata：v3.2.0 / 5 manifests；database boundary、bootstrap plan、
  registry validate、pip dependency dry-run 全部 PASS；
- `git diff --check`：PASS。

**风险/回滚**：计划器默认只读，单次运行只需删除本地 artifact。回滚实现时删除
新 CLI、batch schema、fixture/test 与 `scripts/requirements.txt`，移除 dev
requirements 引用，将共享逻辑恢复到 `base-update-ledger.py` 后删除公共模块；T0
schema/fixtures/tests 保留。

---

### T2 · 建立下游接收与执行工作流

**状态**：✅ 完成（2026-08-18）
**依赖**：T1、D1
**预计**：1 天

**实现内容**：

1. 提供可随 Base tag 同步到下游的接收工作流；接受
   `project_id`、`target_version`、`campaign_id`、`allow_major`，不得接受任意命令；
   `allow_major` 默认 `false`，只能由 operator workflow 显式设为 `true`。
2. 使用项目默认分支作为基线，创建或复用
   `chore/base-vTARGET_VERSION`，并在运行时校验分支名版本等于
   `target_version`。
3. `PROJECT.md` 新增由 bootstrap/ledger 从真实 `upstream` remote 规范化写入的
   `BASE_UPSTREAM_REPOSITORY=OWNER/REPO`；receiver 只从该已提交字段重建
   `upstream`，不接受 dispatch 任意覆盖。首版要求 GitHub.com 且 Base upstream
   可公开读取；字段缺失或与目标 Base 身份不符时 `blocked`。
4. 严格执行 `./scripts/sync-base-release.sh TARGET_VERSION --install-deps`；脚本在 merge
   目标 tag 后、验证前按目标 tree 安装 `scripts/requirements.txt`、后端 dev requirements
   和 `npm ci`，再执行 route/pytest/lint/build/database boundary/diff，保持 merge、
   ledger 和 commit 原子语义。receiver 固定 Python 3.12、兼容 Node，创建 fresh `.venv`。
5. 在下游本地同时验证 `PROJECT.md` 与 `BASE_UPDATES.md`，并校验 receiver
   `project_id` 等于 registry 绑定的项目 ID；失败时生成 `blocked`。
6. 将结果写入 CI job summary，并以 `if: always()` 上传满足 T0 schema 的唯一 JSON
   artifact；最终再按结果决定 workflow 成败。
7. 使用整个 repository 级 concurrency queue，防止不同目标版本并发修改同一项目，
   不主动 cancel 正在执行的升级。
8. `scripts/sync-base-release.sh` 验证失败时不存在可提交的 merge commit，因此不得
   push 半成品分支或创建 PR；工作流只上传 `verification_failed`/`conflict` 结果。
   只有同步原子提交完成后才 push 分支并创建 Draft PR；更新已有开放 PR 时保留其
   Draft/Ready 状态。
9. receiver 遇到冲突时先读取唯一冲突文件列表并生成 `conflict` result，
   再于 job 结束前 `git merge --abort`；result 的 `retry_command` 指向重新派发，
   不得指向即将销毁工作区中的 `--continue`。
10. v3.2.0 首次采用 v3.3.0 时先使用旧命令同步；若目标 tree 已 merge 但因缺目标依赖
    验证停止，在保留 `MERGE_HEAD` 的同一工作区执行
    `./scripts/sync-base-release.sh 3.3.0 --continue --install-deps`。已 abort 或销毁的
    工作区必须从干净默认分支重来。
11. checkout/setup/pip/runner 在合法 inputs 下未产出 result 时，workflow 的
    `if: always()` fallback 生成 `dispatch_failed`；已有 runner result 不覆盖。
12. 账本所有权精确关联 `BASE_LAST_SYNCED_AT`、最后 entry 的 canonical timestamp 与
    非空单行 verification result；PR body 展示跨版本 update nodes、逐项 PASS、retry
    与 rollback。
13. rollback 仅操作本次运行实际创建的 branch/PR；幂等复用的既有 branch/PR 不删除。

**精确文件**：

- 新建 `.github/workflows/base-upgrade-receiver.yml`（D1=GitHub-only 时）
- 新建 `scripts/run-base-upgrade.sh`
- 新建 `scripts/test-base-upgrade-e2e.sh`
- 更新 `scripts/sync-base-release.sh`
- 更新 `scripts/base-update-ledger.py`
- 更新 `scripts/bootstrap-project.sh`
- 新建 `serve/tests/test_base_upgrade_workflow.py`
- 新建 `serve/tests/test_base_upgrade_e2e.py`
- 更新 `serve/tests/test_base_update_ledger.py`
- 更新 `serve/tests/test_project_bootstrap.py`
- 更新 `serve/docs/downstream-upgrade-automation.md`

**验收证据**：

```bash
cd serve
.venv/bin/pytest tests/test_base_upgrade_workflow.py \
  tests/test_base_upgrade_e2e.py tests/test_project_bootstrap.py \
  tests/test_base_update_ledger.py -q
cd ..
scripts/bootstrap-project.sh fixture_project --plan
scripts/test-base-upgrade-e2e.sh
git diff --check
```

`scripts/test-base-upgrade-e2e.sh` 必须在 `/tmp` 动态创建最小 Base/downstream bare
repositories、两个 synthetic release tags、合法 ledger，以及只模拟 route/pytest/npm
成功或失败的无凭据执行环境；退出时清理临时目录。它验证 Git/ledger/workflow
orchestration，不连接真实数据库或 Provider。完整应用验证仍由 T4 的 Base suite 和
真实试点项目自己的 CI 负责。

在动态 fixture clone 中验证：

- clean path 创建单一升级分支并产生包含两个 ledger 的原子 merge commit；
- dirty worktree、目标 tag 不存在、当前 tag 不是祖先时停止；
- 冲突时不修改默认分支、不伪造 PASS 账本；
- 重复 campaign 不产生第二个分支或重复历史记录；
- 日志不存在 secrets。

**风险/回滚**：禁用或删除接收 workflow 即停止自动化；只删除本次新推分支、关闭
本次新建 PR，幂等复用的既有分支/PR 不自动删除，默认分支不受影响。

**完成证据（最后验证：2026-08-18T10:43:11Z）**：

- T0～T2 组合定向测试：`272 passed`；T2 workflow/E2E/bootstrap/ledger 四文件：
  `78 passed`；
- `scripts/test-base-upgrade-e2e.sh`：9/9 动态 Git 场景 PASS（含 v3.2→v3.3
  `--continue --install-deps` 首次采用恢复）；
- 后端完整回归：`563 passed`；Route Manifest：159 routes、1 mount；
- Alembic upgrade、release/database boundary、bootstrap plan、frontend lint/build、
  workflow YAML、runner `bash -n`、官方 GitHub Actions v7 immutable SHA pin 与
  `git diff --check` 全部通过；
- fresh checkout 只从 `BASE_UPSTREAM_REPOSITORY` 恢复公开 Base upstream；固定
  `--install-deps`、跨 MAJOR gate、repository queue、冲突先出 result 再 abort、
  fallback result、严格账本证据、PR body 与资源级 rollback 均有可复现覆盖；
- T2.5 已完成：发布提交
  `abc907682edd55d0b58624c6b0fc78c73b8e41e1` 与 annotated `base/v3.3.0` 已推送，
  远端 tag 解引用到同一 commit；首次下游接入只同步该正式 tag，不复制
  T3 未发布 worktree 文件。

---

### T2.5 · 发布 receiver 基座 v3.3.0

**状态**：✅ 完成（2026-08-18）
**依赖**：T2、D1
**预计**：0.5 天

**目标**：先以不可变 `base/v3.3.0` 发布 T0～T2，使现有下游能通过原有手工同步
合同正式获得 receiver、runner、upstream identity 与目标依赖安装能力；禁止复制
未发布文件进行 onboarding。

**精确发布文件**：

- 更新 `VERSION`、`CHANGELOG.md`
- 更新 `admin/package.json`、`admin/package-lock.json`
- 新建 `releases/base-v3.3.0.json`
- 更新 `UPSTREAM.md`、`CLAUDE.md`

**完成证据（2026-08-18T10:47:12Z）**：完整 release check、563 项后端、
前端 lint/build、9/9 动态 E2E、Alembic、159+1 routes、数据库边界、bootstrap plan
和 diff check 全部通过；发布提交 `abc907682edd55d0b58624c6b0fc78c73b8e41e1`
与 annotated `base/v3.3.0` tag 已推送，远端 tag 解引用到同一提交；GitHub 已识别
receiver workflow 为 active，Base repository 已按确认值保持 PUBLIC。真实下游是否
采用不阻塞 Base tag。T5 自动试点不直接从 v3.3.0 起跑；选定项目必须先
手工同步待发布的严格 backport `base/v3.3.1`。

---

### T3 · 实现批量派发与 Draft PR 幂等更新

**状态**：✅ 完成（2026-08-18）
**依赖**：T1、T2.5、D1～D4
**预计**：1 天

**实现内容**：

1. operator workflow 读取外部 registry，对 `enabled=true` 的项目逐个派发；
2. 每个项目独立返回结果，不采用 fail-fast；
3. PR 标题固定为 `chore(base): update to vTARGET_VERSION`；
4. 相同项目和目标版本已有开放 PR 时，由下游 receiver 使用自己的 `GITHUB_TOKEN`
   更新原 PR，不重复创建；operator 只读查询开放 PR 以判断幂等状态；
5. PR 正文必须包含：CURRENT/TARGET、跨版本 update nodes、migrations、
   conflict hotspots、downstream actions、Release Manifest verification、运行时逐项 PASS 和
   rollback 命令；没有 PR 的 conflict/verification failure 在结果 artifact 中提供
   本地重现和重新派发命令；只有尚保留 `MERGE_HEAD` 的同一本地工作区
   才能提供 `--continue`，receiver 已 abort/已销毁的临时工作区必须重新派发；
6. 首版不直接写默认分支；D2 选择“不自动合并”时始终创建 Draft PR；
7. 批次最终生成项目状态表和 JSON artifact；
8. GitHub v1 调用
   `POST /repos/OWNER/REPO/actions/workflows/base-upgrade-receiver.yml/dispatches`，
   在 `X-GitHub-Api-Version: 2026-03-10` 下不得发送已移除的
   `return_run_details`；严格要求 `200` response 并直接保存返回的 run ID/URL。
   receiver 的 `run-name` 固定包含唯一 `campaign_id + target_version`，只用于人读和
   响应丢失后的歧义恢复。
   派发端按 run ID 轮询并下载唯一 `base-upgrade-result-CAMPAIGN_ID` artifact；单项目
   最长等待 30 分钟，超时先 cancel 该 run 并等待终态，再记为 `dispatch_failed`；
9. 幂等来源是 Provider 中的开放 PR 和远端分支，而不是短期 CI artifact：先按精确
   head `chore/base-vTARGET_VERSION` + base branch 查询开放 PR。已有 PR 时只更新正文；
   只有分支但没有 PR 时先验证其 commit 包含目标 Base tag，无法证明所有权时记为
   `blocked`，禁止 force-push；
10. `campaign_id` 只用于一次运行的关联和结果回收；跨运行幂等键是
    `project_id + target_version`。所有 GitHub API 调用复用 T1 的认证、timeout、重试与
    日志脱敏规则。
11. workflow dispatch POST 不做盲目重试：收到 run ID 即以其为权威；响应丢失时先按
    唯一 run-name + event + ref + 时间窗口恢复，0 个匹配才有界重试，多个匹配则
    `blocked`。识别 403/429 的 `Retry-After`、remaining/reset headers。
12. 新增脱敏 campaign evidence wrapper：顶层 `operator_commit` 绑定实际 Base tools
    checkout；entry 记录 registry 预分配的稳定、非敏感 opaque `project_id`、run
    ID/URL、artifact/archive digest、时间和 result hash。工具不会自动匿名化
    `project_id`；run URL 仍暴露 repository identity，所以真实 evidence 只提交到
    私有 operator/ops repo 或其受保护 CI artifact。
13. evidence 在取得 run ID 后立即建立 entry。后续 poll/cancel/artifact 失败不
    丢弃 run locator；未取得的 completion/artifact/result/status 字段为 `null`，
    `failure_stage` 标记 partial evidence。runtime 强制 project/run 唯一、时间顺序、
    run URL 与 registry repository 一致、entry 与 batch result 一致。
14. artifact 回收必须精确绑定 run/repository 且名称唯一；手工跟随到允许的
    signed download host，不转发 Bearer token；拒绝 digest 漂移、非唯一/过期
    artifact、ZIP traversal/symlink/encryption/bomb、重复 JSON key、非终态或身份不匹配
    result。
15. v1 运维容量按项目串行：每项目 run 轮询上限 30 分钟，cancel 后终态
    等待上限 5 分钟，示例 workflow 上限 120 分钟。因此单次 channel 最多选中
    3 个 enabled 项目；超限在任何 Provider 调用前 fail closed。大 fleet 以 channel
    分批，每批最多 3 项。
16. 示例 workflow 必须在私有 operator 仓库设置 `fleet/projects.json`、
    `BASE_UPGRADE_GITHUB_TOKEN`、`BASE_PLATFORM_REPOSITORY` 和指向已发布不可变
    tag/commit 的 `BASE_PLATFORM_REF`。它双 checkout ops 仓库与 `.base-operator` tools，
    校验实际 operator commit，并以 `if: always()` 终止门复验、上传 batch/
    summary/evidence 三件套。

**Base 精确文件**：

- 更新 `scripts/base-upgrade-campaign.py`
- 新建 `.github/workflows/base-upgrade-campaign-example.yml`
- 新建 `serve/tests/test_base_upgrade_dispatch.py`
- 新建 `serve/tests/fixtures/github-api/README.md`
- 新建 `serve/tests/fixtures/github-api/campaign.json`
- 新建 `scripts/schemas/base-upgrade-evidence.schema.json`
- 更新 `scripts/run-base-upgrade.sh`（PR 正文补全 Manifest 合同分节）
- 更新 `serve/tests/test_base_upgrade_workflow.py`（PR 正文全量/不可执行渲染回归）
- 更新 `serve/docs/downstream-upgrade-automation.md`
- 更新 `UPSTREAM.md`、`CLAUDE.md`
- 更新 `serve/docs/todo-downstream-upgrade-automation-2026-08-18.md`

**operator 仓库合同路径（不在 Base 提交真实内容）**：

- `fleet/projects.json`
- `.github/workflows/base-upgrade-campaign.yml`

**验收证据**：

```bash
cd serve
.venv/bin/pytest tests/test_base_upgrade_campaign.py \
  tests/test_base_upgrade_dispatch.py -q
cd ..
python3 scripts/base-upgrade-campaign.py summarize \
  --results serve/tests/fixtures/base-upgrade-results.json \
  --registry serve/tests/fixtures/base-downstream-registry.json \
  --json-output /tmp/base-upgrade-batch.json \
  --markdown-output /tmp/base-upgrade-summary.md
```

- 合成 fixture campaign 覆盖 `pr_opened`、`up_to_date`、`verification_failed`，
  以及已取得 run locator 后 poll 失败的 partial evidence/`dispatch_failed`；
- 使用不同 `campaign_id` 再次运行相同 `project_id + target_version`，开放 PR 数量
  不增加且原 PR 正文被更新；
- 一个 fixture 失败时其余 fixture 仍完成；
- Summary 中每个失败都有 `failed_stage` 和可执行的 retry/rollback 命令；
- provider API mock 测试证明未向默认分支直接 push。

**完成证据（2026-08-18T11:23:25Z）**：

- T0～T3 组合定向：`322 passed`；
- campaign + dispatch 专项：`95 passed`；receiver workflow 专项：`23 passed`；
- 动态 Git E2E：`9/9 PASS`；
- mock/合成回归覆盖 2026-03-10 的 200 run locator、不发
  `return_run_details`、响应丢失恢复且 POST 最多 2 次、rate limit、轮询/cancel 409
  竞态、PR/孤立分支所有权、artifact redirect/digest/ZIP/result 关联、partial
  evidence、`operator_commit`、三输出路径碰撞和 secret fail-closed；
- PR 正文回归证明每个选中 Manifest 的 nodes、migrations、conflict hotspots、
  downstream actions 和 verify 全部渲染，且不执行 Manifest 文本。

**风险/回滚**：停止 operator workflow 或撤销 dispatch Token；关闭 Draft PR、删除
自动化分支即可。不得删除已写入下游 `BASE_UPDATES.md` 的既有事实。

---

### T4 · 完整工程验证与故障演练

**状态**：✅ 完成（2026-08-18）
**依赖**：T0～T3
**预计**：0.5～1 天

**故障矩阵**：

| 场景 | 预期结果 |
|---|---|
| 项目已是目标版本 | `up_to_date`，不创建 PR |
| CURRENT < TARGET、无冲突 | 创建/更新一个 Draft PR |
| CURRENT > TARGET | `blocked`，禁止降级 |
| 跨 MAJOR 且未显式批准 | `blocked` |
| `PROJECT.md` 缺失/漂移 | `blocked`，提示先建立真实 baseline |
| merge conflict | `conflict`，列出文件并 abort；不 push 分支，默认分支不变 |
| pytest/lint/build 失败 | `verification_failed`，不伪造 PASS |
| provider 限流/短暂失败 | 有界重试后 `dispatch_failed` |
| workflow 重复触发 | 命中 concurrency/idempotency，不重复 PR |
| 单项目 Token 权限不足 | 仅该项目失败，其他项目继续 |

**完整验证命令**：

```bash
cd serve && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest
cd ..
cd serve && alembic upgrade head
cd ..
cd serve && .venv/bin/python -m app.routes check
cd ..
cd admin && npm ci && npm run lint && npm run build
cd ..
python3 scripts/check-database-boundary.py
scripts/bootstrap-project.sh fixture_project --plan
scripts/test-base-upgrade-e2e.sh
git diff --check
```

**验收证据保存位置**：

- 自动化测试：`serve/tests/test_base_upgrade_*.py`；
- fixture：`serve/tests/fixtures/base-*upgrade*.json`；
- CI summary：对应测试 campaign run；
- 真实试点：仅在 operator 仓库提交
  `fleet/evidence/CAMPAIGN_ID.json`，记录时间、目标版本、operator 预分配的
  稳定、非敏感 opaque `project_id`、workflow run/PR locator 和结果 hash。工具不会
  自动匿名化 project ID，run URL 可识别 repository，因此 evidence 必须保留在私有
  operator/ops 仓库或受保护 CI artifact；Base 仓库只在发布说明记录 campaign
  ID 与聚合结果；
- 不向 Base 或下游产品仓库提交真实下游仓库 URL、Token、数据库名称或
  运行日志；必需的 run locator 只存放在私有 operator/ops evidence。

**完成证据（2026-08-18T11:23:25Z）**：

- 后端完整回归：`613 passed in 14.46s`；
- Alembic upgrade PASS；Route Manifest PASS（`159 routes, 1 mount`）；
- `npm ci`、frontend lint/build PASS；lint 保留 2 个既有 warning，npm audit 报告 9 个
  既有 vulnerability，均不由 T3 引入；
- `check-base-release.py` PASS（当前 v3.4.0，9 个 Manifest）；
- database boundary、bootstrap plan、动态 Git E2E `9/9`、`git diff --check` 全部 PASS。

---

### T5 · 试点、发布与下游采用

**状态**：🟡 v3.3.1/v3.3.2 已发布且 3 个试点已手工采用；待 v3.4.0 campaign
**依赖**：T4、D5
**预计**：1 天 + 试点观察

**实现内容**：

1. 从已发布 `base/v3.3.0` 创建严格 backport，只包含
   `scripts/run-base-upgrade.sh` 的 PR 正文 Manifest 分节增强、对应 workflow 测试和
   PATCH 发布元数据；不得将 `base-upgrade-campaign.py dispatch`、evidence schema、
   campaign workflow 或其他 T3 dispatcher 文件带入 v3.3.1；
2. 对 backport 执行完整发布验证，创建并推送不可变 `base/v3.3.1`；不移动
   `base/v3.3.0`；
3. 真实同步发现动态 Git E2E 相对 `.git/MERGE_HEAD` 误读外层 no-commit merge，
   以严格 PATCH 发布 `base/v3.3.2`，并补推历史 Base tags；
4. 选择 3 个非生产/低风险下游，使用原有手工同步合同从 v3.3.0 采用
   `base/v3.3.2`，不复制 workflow/runner；
5. 主线 T3 基于精确 `base/v3.3.2`，以标准 MINOR 合同发布不可变
   `base/v3.4.0`；不得移动任何已发布 tag；
6. 从私有 operator 派发 v3.4.0 campaign；首轮真实证据确认三个项目均在
   non-force push 阶段被 GitHub 拒绝，因为目标新增 `.github/workflows` 示例文件；
7. 以 v3.4.1 PATCH 将示例移动到 `examples/github-actions/`，固定 operator tools 到
   v3.4.1 并用新 campaign ID 重试，验证三个 receiver 自动创建/更新 Draft PR，且
   PR 正文包含 nodes、migrations、conflict hotspots、downstream actions、
   release/runtime verification、retry 和 rollback；
8. 记录成功率、人工耗时、冲突文件和失败阶段；
9. 只将可复用修正回收到 Base，不把试点项目资料提交到 Base；试点发现的 Base
   缺陷进入 `base/v3.4.1`，不得修改或移动已发布 tag；
10. 非试点下游通过原有方式同步至少 v3.3.2 并获得完整 receiver，从下一次 Base 更新开始
   进入自动升级流程。

**v3.3.1 严格 backport 精确文件**：

- 更新 `scripts/run-base-upgrade.sh`
- 更新 `serve/tests/test_base_upgrade_workflow.py`
- 更新 `VERSION`、`CHANGELOG.md`
- 更新 `admin/package.json`、`admin/package-lock.json`
- 更新 `UPSTREAM.md`
- 新建 `releases/base-v3.3.1.json`

backport diff 必须证明不含 `.github/workflows/base-upgrade-campaign-example.yml`、
`scripts/schemas/base-upgrade-evidence.schema.json`、`serve/tests/test_base_upgrade_dispatch.py`、
`serve/tests/fixtures/github-api/` 或 `scripts/base-upgrade-campaign.py` 的 T3 dispatch 变更。
v3.3.1 Manifest 只记录 receiver 合同与发布元数据两个稳定节点：
`downstream.receiver-pr-body-manifest-contract`、`release.v3-3-1-metadata`。

**v3.3.2 嵌套 E2E PATCH 精确文件**：

- 更新 `scripts/test-base-upgrade-e2e.sh`
- 更新 `serve/tests/test_base_upgrade_e2e.py`
- 更新 `VERSION`、`CHANGELOG.md`、`UPSTREAM.md`
- 更新 `admin/package.json`、`admin/package-lock.json`
- 新建 `releases/base-v3.3.2.json`

稳定节点：`downstream.nested-merge-e2e-gitdir`、`release.v3-3-2-metadata`。

**v3.4.0 主线精确发布文件**：

- 更新 `VERSION` 为 `3.4.0`
- 更新 `CHANGELOG.md`
- 更新 `admin/package.json`
- 更新 `admin/package-lock.json`
- 新建 `releases/base-v3.4.0.json`
- 更新 `UPSTREAM.md`、`CLAUDE.md`、`serve/docs/downstream-upgrade-automation.md` 与本 TODO

**Manifest 稳定节点建议**：

- `downstream.fleet-registry-contract`
- `downstream.dispatched-sync-workflow`
- `downstream.upgrade-campaign-reporting`
- `downstream.upgrade-recovery-guards`

**发布验收证据**：

```bash
python3 scripts/check-base-release.py
python3 scripts/check-database-boundary.py
cd serve && .venv/bin/pytest
cd ../admin && npm ci && npm run lint && npm run build
cd .. && git diff --check
```

严格 backport 发布提交完成后先创建并远端复验 PATCH tag：

```bash
git tag -a base/v3.3.1 -m "Base Platform v3.3.1"
git rev-parse --verify 'refs/tags/base/v3.3.1^{commit}'
test "$(git rev-parse 'refs/tags/base/v3.3.1^{commit}')" = "$(git rev-parse HEAD)"
git push origin HEAD
git push origin refs/tags/base/v3.3.1
git ls-remote --exit-code --tags origin refs/tags/base/v3.3.1
```

三个试点手工采用并验证 v3.3.2 后，主线基于该确切 PATCH，完成 T3 MINOR
发布提交并创建不可变 tag：

```bash
git tag -a base/v3.4.0 -m "Base Platform v3.4.0"
git rev-parse --verify 'refs/tags/base/v3.4.0^{commit}'
test "$(git rev-parse 'refs/tags/base/v3.4.0^{commit}')" = "$(git rev-parse HEAD)"
git push origin HEAD
git push origin refs/tags/base/v3.4.0
git ls-remote --exit-code --tags origin refs/tags/base/v3.4.0
```

只有远端 v3.3.1、v3.3.2 与 v3.4.0 tag 都验证成功，且三个试点的默认分支已真实
记录 v3.3.2，才能执行第 5 步真实 campaign；下游
`scripts/sync-base-release.sh` 必须能从 `upstream` fetch 到该 tag。

**完成条件**：代码、文档、测试、Manifest 和试点证据全部成立后，才可将本 TODO
状态改为完成。只有构建通过、没有真实试点 PR，不算完成；若试点暴露 Base 缺陷，
修复版本（例如 v3.4.1）发布并重新试点通过后才算完成。

---

## 7. 依赖与阻塞项

### 工程依赖

- 下游已经提交准确的 `PROJECT.md` 和 `BASE_UPDATES.md`；
- 下游提交 canonical `BASE_UPSTREAM_REPOSITORY`，且该 GitHub Base upstream 可公开读取；
- 下游 CI 具备运行 `.venv/bin/pytest`、`npm ci/build` 和项目数据库验证的环境；
- operator 使用 `BASE_UPGRADE_GITHUB_TOKEN`，最小权限为目标仓库
  Actions read/write、Contents read、Pull requests read；
- receiver workflow 显式声明 `permissions: contents: write, pull-requests: write`，使用
  下游仓库自己的 `GITHUB_TOKEN`；仓库设置必须允许 GitHub Actions 创建 PR；
- 默认分支保护允许 bot 创建 PR，但不允许直接绕过保护规则。

### 当前阻塞项

- D1～D6 已按推荐值确认，产品决策门已解除；
- T5 首先需从 v3.3.0 严格 backport/发布不含 dispatcher 的 v3.3.1 PR 正文合同补丁；
  随后需要 Base 仓库外的真实私有 operator/ops 仓库、Provider credential，以及 3 个
  已手工采用 `base/v3.3.2` receiver 的非生产/低风险下游。具体仓库、registry、
  run/PR/evidence 只在外部私有 operator 仓库选定和记录，不写入 Base；
- 若现有下游未建立 v3.2.0 ledger，必须先按
  `serve/docs/base-update-ledger.md` 建立真实 baseline，禁止猜测版本。

---

## 8. 风险与回滚总表

| 风险 | 控制 | 回滚 |
|---|---|---|
| 错误项目被升级 | registry `enabled` + 人工批准 campaign + 项目 allowlist | 禁用项目条目，关闭 PR |
| 重复 PR/重复账本 | 开放 PR + 固定远端分支；幂等键为 `project_id + target_version` | 关闭重复 PR；账本历史不删除，追加纠正事实 |
| Bot 权限过大 | 最小权限 Token、默认分支保护、只写固定分支前缀 | 撤销 Token、禁用 workflow |
| secrets 泄露 | 不进入 registry/参数；日志脱敏测试 | 立即撤销 Token并清理 CI artifact |
| 错误降级/跨 MAJOR | SemVer gate，默认禁止降级与未批准 MAJOR | 停止项目任务，不创建 PR |
| 合并冲突 | 先捕获冲突列表与 result，再 abort；不推送分支，不持久化 MERGE_HEAD | receiver 重新派发；仅未 abort 且保留原 `MERGE_HEAD` 的同一本地工作区可解决后 `--continue` |
| 验证环境不一致 | 在各下游自己的 CI 中执行 | 不创建半成品 PR；修复环境后重跑 receiver |
| 一个项目拖垮批次 | per-project job、`fail-fast: false` | 单独重试失败项目 |

---

## 9. 执行顺序与进度

```text
T0 合同/schema
    ↓
T1 只读计划与汇总
    ↓
D1 已确认 → T2 receiver → T2.5 发布 v3.3.0 receiver 基座
                             ↓
                 D2/D3/D4 已确认 → T3 派发 + Draft PR
                                      ↓
T4 完整验证/故障演练
    ↓
D5 试点槽位已确认 + 发布 v3.3.1/v3.3.2
    ↓
3 项目手工采用 v3.3.2 → 主线基于 v3.3.2 → 发布 v3.4.0
    ↓
T5 三项目自动 v3.3.2→v3.4.0 升级试点
```

| 任务 | 状态 | 依赖 | 可复现完成证据 |
|---|---|---|---|
| TODO 合同建立 | ✅ 完成 | — | 本文档 |
| T0 合同与 schema | ✅ 完成 | — | 147 contract tests + 449 backend tests |
| T1 计划器与报告 | ✅ 完成 | T0 | 223 targeted tests + deterministic dry-run + redaction regression |
| T2 下游 receiver | ✅ 完成 | T1、D1✅ | 272 combined targeted + 78 T2 + 9/9 E2E + 563 backend |
| T2.5 receiver 基座发布 | ✅ 完成 | T2、D1 | `abc9076` + 远端 `base/v3.3.0` + active workflow |
| T3 派发与 Draft PR | ✅ 完成 | T1、T2.5、D1～D4 | 322 targeted；campaign+dispatch 95；workflow 23；E2E 9/9 |
| T4 验证与故障演练 | ✅ 完成 | T0～T3 | backend 613；Alembic/routes/frontend/release/boundary/bootstrap/E2E/diff PASS |
| T5 试点与发布 | 🟡 v3.4.0 首轮发现 push blocker，v3.4.1 修复中 | T4、D5 | 3 个 v3.3.2→v3.4.1 Draft PR + campaign evidence |

---

## 10. 复用性说明

该能力只处理 Base release tag、通用下游账本、标准 Git 分支和升级结果，不包含任何
产品业务规则。真实 fleet registry 留在 operator 仓库，真实 secrets 留在各自 CI
secret configuration；因此同一套能力可服务多个下游项目，且不会把某个项目的部署
信息或业务实现带回 Base。
