# Base 下游升级自动化合同

**状态**：✅ T0～T5 已完成；receiver 基座、bootstrap patches、operator dispatch
与 workflow-path 修正已分别以不可变 `base/v3.3.0`～`base/v3.4.1` 发布，并通过
3 个真实非生产下游的成功与幂等 campaign。
**关联 TODO**：`serve/docs/todo-downstream-upgrade-automation-2026-08-18.md`
**已发布基线**：`base/v3.3.0`（`abc907682edd55d0b58624c6b0fc78c73b8e41e1`）
**已发布 bootstrap PATCH**：`base/v3.3.1`、`base/v3.3.2`
**已发布 campaign**：`base/v3.4.0`、`base/v3.4.1`
（`081cd1407fb902aebbd344c1518cf361e1ec9587`）

本文档定义 Base 下游自动升级流程的**数据合同、只读计划、下游执行与 operator
派发边界**。已固化 registry、单项目 result、batch 和 evidence 四种 JSON 格式，
并提供 registry 校验、只读计划、结果汇总、GitHub receiver 与 T3 dispatch CLI。

---

## 1. 文件层与职责边界

自动升级能力涉及三类文件，必须严格区分：

| 层 | 位置 | 内容 | 归属 |
|---|---|---|---|
| Base 通用合同与工具 | `scripts/schemas/*.schema.json`、`scripts/base-upgrade-campaign.py`、`scripts/run-base-upgrade.sh`、`.github/workflows/base-upgrade-receiver.yml`、`examples/github-actions/base-upgrade-campaign.yml`、`scripts/lib/base_release.py`、本文档 | schema、计划/派发/批次汇总、下游 receiver、operator 模板、共享 release/脱敏边界 | Base 仓库，产品无关 |
| operator 实例配置 | 私有 operator/ops 仓库（例如 `fleet/projects.json`） | 真实项目清单、channel、enabled 开关、Provider secret、campaign workflow/evidence | operator 仓库，不进入 Base |
| 下游运行文件 | 各下游仓库 `PROJECT.md`、`BASE_UPDATES.md` | 真实 Base 版本、同步历史、验证记录 | 下游仓库，不进入 Base |

Base 仓库不保存真实项目名称、仓库地址、Token、数据库信息或任何下游运行产物。

---

## 2. Registry schema（下游项目清单）

**文件**：`scripts/schemas/base-downstream-registry.schema.json`

Registry 是**声明式项目清单**，只描述“哪些下游项目可参与升级、从哪个默认分支
读取版本、走哪个 channel”。

顶层结构：

```json
{
  "schema_version": 1,
  "projects": [
    {
      "project_id": "fixture_project_alpha",
      "repository": "fixture-owner/fixture-repo-alpha",
      "default_branch": "main",
      "enabled": true,
      "channel": "stable",
      "provider": "github"
    }
  ]
}
```

字段语义：

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | integer | 固定为 `1`；未来不兼容变更递增 |
| `project_id` | string | operator 分配的稳定、非敏感关联标识；只允许字母数字、`.`、`_`、`-` |
| `repository` | string | GitHub v1 固定 canonical `OWNER/REPO`；OWNER 最长 39、REPO 最长 100 |
| `default_branch` | string | 读取 `PROJECT.md` 与创建升级分支的安全 Git ref 基线 |
| `enabled` | boolean | `false` 时该项目不参与派发 |
| `channel` | string | 更新渠道标识（例如 `stable`），小写字母数字和连字符 |
| `provider` | string | 当前只允许 `github`（GitHub v1） |

约束：

- 顶层与每个项目对象都使用 `additionalProperties: false`，**未知字段一律失败**；
- **不接受 `current_version` 或任何版本覆盖字段**：`BASE_UPSTREAM_VERSION`
  必须从下游 `PROJECT.md` 发现，registry 不是版本权威；
- **不接受 secret/Token 字段**：`token`、`access_token`、`secret`、
  `password`、`database_*` 等一律失败；
- `projects` 拒绝完全重复的项目对象；T1 runtime 另外强制 `project_id` 唯一，
  并按大小写归一化后强制 `repository` 唯一；
- `repository` 只接受 canonical `OWNER/REPO`，允许 REPO 为 `.github`：
  - 合法：`fixture-owner/fixture-repo-alpha`
  - 非法：`https://github.com/OWNER/REPO`、`git@github.com:OWNER/REPO.git`、
    `OWNER/REPO.git`（大小写任意）、`user:pass@...`、`OWNER/group/REPO`、
    `OWNER/.`、`OWNER/..` 或任何换行输入；
- `default_branch` 和 result 中的非空 `branch` 拒绝隐藏 path segment、`..`、
  `//`、`@{`、`.lock` 后缀和换行等不安全 Git ref 形态。

---

## 3. Result schema（单项目升级结果）

**文件**：`scripts/schemas/base-upgrade-result.schema.json`

每个项目在每次 campaign 中产生一个结果对象。这是**单项目结果**，不是整个批次
的汇总状态。所有字段都必须稳定存在；不适用的标量字段显式写
`null`，`conflict_files` 不适用时写空数组，不能省略。

```json
{
  "campaign_id": "campaign-2026-08-18-001",
  "project_id": "fixture_project_alpha",
  "source_version": "3.1.0",
  "target_version": "3.2.0",
  "status": "pr_opened",
  "branch": "chore/base-v3.2.0",
  "pr_url": "https://github.com/fixture-owner/fixture-repo-alpha/pull/1",
  "failed_stage": null,
  "conflict_files": [],
  "verification_summary": "pytest PASS, lint PASS, build PASS",
  "retry_command": "gh workflow run base-upgrade-receiver.yml --repo fixture-owner/fixture-repo-alpha -f target_version=3.2.0 -f campaign_id=campaign-2026-08-18-001 -f allow_major=false",
  "rollback_command": "gh pr close 1 --repo fixture-owner/fixture-repo-alpha --delete-branch"
}
```

字段语义：

| 字段 | 类型 | 说明 |
|---|---|---|
| `campaign_id` | string | 一次 campaign 的关联 ID；必填 |
| `project_id` | string | 与 registry 中 `project_id` 一致 |
| `source_version` | string/null | 从 `PROJECT.md` 读到的 `BASE_UPSTREAM_VERSION`；未知时为 `null` |
| `target_version` | string | 本次升级目标 SemVer，例如 `3.2.0` |
| `status` | string enum | 见下方状态枚举 |
| `branch` | string/null | 自动化分支名；未创建时为 `null` |
| `pr_url` | string/null | 升级 PR URL；未创建时为 `null` |
| `failed_stage` | string/null | 失败阶段；未失败时为 `null` |
| `conflict_files` | array | 冲突文件列表；无冲突时为空数组 |
| `verification_summary` | string/null | 验证摘要（pytest/lint/build/diff 等）；未执行为 `null` |
| `retry_command` | string | 非空的继续/重试指令；必填，无需动作的状态可显式写明 |
| `rollback_command` | string | 非空的回滚指令；必填，未产生可回滚变更时可显式写明 |

状态语义与 schema 强制的字段不变量：

| `status` | 语义 | schema 强制的不变量 |
|---|---|---|
| `planned` | 已纳入批次，尚未派发 | `branch`/`pr_url`/`failed_stage=null`，冲突列表为空 |
| `dispatched` | 已派发到下游，等待执行或轮询 | 同 `planned` |
| `up_to_date` | 项目已在目标版本，无需 PR | 同 `planned`；`verification_summary` 可记录无需更新的根据 |
| `pr_opened` | 升级分支与开放 PR 已创建或更新；新 PR 为 Draft | `branch` 符合 `chore/base-vX.Y.Z`，`pr_url` 为无 credential 的 GitHub PR URL，`failed_stage=null`，冲突列表为空，验证摘要非空 |
| `conflict` | 合并冲突，未推送半成品 | `branch`/`pr_url=null`，`failed_stage=merge`，冲突文件非空且唯一 |
| `verification_failed` | 同步后验证失败，未推送半成品 | `branch`/`pr_url=null`，`failed_stage` 非空，冲突列表为空 |
| `blocked` | 版本漂移、降级、跨 MAJOR 未批准、账本缺失等前置条件未满足 | 同 `verification_failed` |
| `dispatch_failed` | 派发或 Provider 调用失败 | 同 `verification_failed` |

约束：

- `additionalProperties: false`，未知字段一律失败；
- 上表 12 个字段全部必填；
- 不接受任何 secret/Token 字段；
- `status` 必须是上述枚举之一；
- 版本字段必须是 canonical core SemVer `X.Y.Z`，每段禁止前导零，不接受
  `v` 前缀、预发布/构建后缀或换行（`source_version` 可为 `null`）；
- `failed_stage`、`conflict_files` 项、`verification_summary`、`retry_command`、
  `rollback_command` 在非空时必须非全空白且为单行，并受 schema 长度上限
  约束；`verification_summary`、`retry_command`、`rollback_command` 还拒绝 URL
  authority 中带 userinfo 的 credential-bearing URL；
- `conflict_files` 在所有状态下都不接受重复项，每项必须是规范的
  repository-relative path；拒绝 POSIX/Windows 绝对路径、反斜杠、空或尾随
  segment、`.`/`..` segment、NUL、C0/C1 和 DEL 控制字符；
- `pr_opened` 必须同时包含固定命名的升级分支、GitHub PR URL 和非空验证摘要，且
  `failed_stage` 必须为 `null`、冲突文件必须为空；
- `conflict` 必须包含至少一个唯一冲突文件、`failed_stage=merge`，且不得声称已创建
  分支或 PR；
- `verification_failed`、`blocked`、`dispatch_failed` 必须包含失败阶段，且不得
  携带分支或 PR；
- `pr_url` 只接受无 credential 的
  `https://github.com/OWNER/REPO/pull/NUMBER`。

`retry_command` 和 `rollback_command` 在 T0 schema 中只能静态验证为非空字符串；
后续生产者必须按状态输出可执行命令，或在确实无任何动作时输出明确的
`no action required` 类指令，不得伪造已执行或已回滚的证据。

### 3.1 Batch schema（批次结果）

**文件**：`scripts/schemas/base-upgrade-batch.schema.json`

批次对象固定 `schema_version=1`、单一 `campaign_id`、单一 `target_version` 和
非空 `results`。其中每项通过 `$ref` 复用单项目 result schema；campaign runtime
进一步强制：

- results 按 `project_id` 稳定排序且项目 ID 不重复；
- 每项 `campaign_id`、`target_version` 与批次顶层一致；
- `up_to_date` 的 source/target 必须相等；任何非空升级分支中的版本必须等于
  `target_version`；
- 传入 `--registry` 汇总时，每个 result 项目必须存在于 registry，且非空 PR URL
  的 OWNER/REPO 必须与 registry repository 大小写归一化后相同。

`summarize` 可读取一个 batch 对象，也可读取同一 campaign/target 的非空单项目
result 数组；数组会按 `project_id` 规范化为 batch。T3 `dispatch` 必须提供
registry，不存在绕过跨文件关联校验的生产路径。

### 3.2 Evidence schema（私有 operator 运行证据）

**文件**：`scripts/schemas/base-upgrade-evidence.schema.json`

evidence 不嵌入 result 或 repository 字段，而是为实际获得 receiver run ID 的
派发保存最小交叉验证事实。顶层全部必填：

| 字段 | 语义 |
|---|---|
| `schema_version` | 固定为 `1` |
| `campaign_id` / `target_version` | 必须与 batch 和 workflow input 一致 |
| `operator_commit` | 实际执行 campaign 的 Base tools checkout，必须为小写 40-hex commit |
| `generated_at` | canonical UTC 秒级时间 |
| `entries` | 只包含已取得 run ID 的项目；未派发或已是目标版本时可为空 |

每个 entry 固定包含 `project_id`、`run_id`、`run_url`、`artifact_name`、
`artifact_sha256`、`dispatched_at`、`completed_at`、`result_sha256`、
`final_status` 和 `failure_stage`。`failure_stage=null` 时 completion/artifact/result/status
字段必须全部完整；得到 run locator 后发生 poll、cancel 或 artifact 回收失败时，
依然保留 entry，将未取得字段设为 `null` 并填写 `failure_stage`。runtime 额外
强制 project ID 有序且唯一、run ID 唯一、run URL 与 registry repository 相关联、
timestamp 按 dispatch → completion → generation 顺序，以及 entry 与 batch result 状态一致。

`project_id` 是 operator 在 registry 中预分配的稳定、非敏感 opaque ID；工具不会
将一个敏感 ID 自动匿名化。而 `run_url` 本身仍包含 `OWNER/REPO`，所以即使
schema 没有独立 repository 字段，真实 evidence 也只能写入私有 operator/ops 仓库或
其受保护 CI artifact，不得提交到 Base 或下游产品仓库。

### 3.3 Schema 外的运行时关联校验

JSON Schema Draft-07 负责单个 JSON 对象的形状与状态不变量，不会独立证明跨文件、
跨记录或 Provider 远端事实。T1 实现 registry/batch 层能本地证明的关联，T2 已实现
下游本地账本、target tag、升级分支与 PR 的执行时关联：

- registry 内 `project_id` 和 `repository` 分别唯一（repository 按大小写归一化），
  避免同一仓库被别名重复派发；使用 registry 汇总时，result 的 `project_id`
  实际存在于当次 registry；
- `source_version` 与执行时下游默认分支 `PROJECT.md` 一致；发现失败时为
  `null` 并产生相应失败结果，不得猜测；
- `up_to_date` 时 `source_version` 必须非 `null` 且等于 `target_version`；
- 任意非空 `branch` 中的版本与 `target_version` 相同；
- 使用 registry 汇总时，非空 `pr_url` 的 OWNER/REPO 与 registry 的
  `repository` 相同；
- Provider 上的远端分支、PR 和目标 Base tag 确实存在，且幂等键没有指向
  其他运行所有的分支。

前三类 registry/batch 关联由 T1 实现；T2 receiver 校验 source version 与默认分支
账本、最后一条 `BASE_UPDATES.md` 的 version/commit、目标 tag、精确两父原子 merge、
固定远端分支和开放 PR。T3 实现再将 dispatch run、artifact/result、开放 PR/孤立
分支所有权与 operator registry 的跨仓库事实绑定；该实现在发布前仍需通过 T4
完整验证。

---

## 4. T1 只读 campaign CLI

**文件**：`scripts/base-upgrade-campaign.py`

CLI 包含三个子命令：

```bash
# schema + project_id/repository 唯一性
python3 scripts/base-upgrade-campaign.py validate-registry \
  --registry fleet/projects.json

# 离线、确定性只读计划；fixture 只能与 --dry-run 同用
python3 scripts/base-upgrade-campaign.py plan \
  --registry serve/tests/fixtures/base-downstream-registry.json \
  --project-state-fixture serve/tests/fixtures/base-project-states.json \
  --target-version 3.2.0 --channel stable --dry-run

# 校验、脱敏并输出机器可读 JSON 与 Markdown
python3 scripts/base-upgrade-campaign.py summarize \
  --results serve/tests/fixtures/base-upgrade-results.json \
  --registry serve/tests/fixtures/base-downstream-registry.json \
  --json-output /tmp/base-upgrade-results.json \
  --markdown-output /tmp/base-upgrade-summary.md
```

`plan` 只选择 `enabled=true` 且 channel 匹配的项目，并按 `project_id` 稳定排序；
单项目发现失败生成 `blocked/project_discovery`，不会中止其他项目。目标版本必须在
当前 Base checkout 有 Release Manifest。版本选择与 ledger CLI 共用
`scripts/lib/base_release.py` 的 canonical core SemVer 和 `(source, target]`
Manifest 算法；合法 source 不要求自身有 Manifest。

离线模式要求同时提供 `--dry-run` 与 `--project-state-fixture`，fixture 仅映射
`OWNER/REPO` 到模拟 `PROJECT.md` 文本；未给 `--campaign-id` 时使用确定性的
`plan-vTARGET_VERSION`。Live Provider 模式禁止 fixture 且要求显式
`--campaign-id`。所有模式都只生成计划/artifact，不创建分支、不 push、不派发、
不开 PR。

计划结果也保持真实语义：`blocked` 的 `retry_command` 要求先修复报告的问题再重跑
只读计划；`planned`/`up_to_date` 明确无需重试；所有 T1 结果的
`rollback_command` 都说明只读计划没有创建可回滚变更。

跨 MAJOR 默认生成 `blocked/version_gate`；只有显式传入 `--allow-major` 才进入
Manifest 计划计算。该开关只是只读计划授权，不执行升级；降级始终 blocked。

### 4.1 GitHub v1 只读 Provider 边界

- 从 registry 指定 default branch 的 GitHub Contents API 读取 `PROJECT.md`；
- Token 只读 `BASE_UPGRADE_GITHUB_TOKEN` 环境变量，不接受 CLI token 参数；
- 单请求 timeout 15 秒，最多 3 次尝试；5xx、暂时性网络错误和
  `http.client.HTTPException` 协议中断（已覆盖 `IncompleteRead`、
  `BadStatusLine`）按 1 秒、2 秒有界退避；
- 403/429 只在 `Retry-After` 或 `X-RateLimit-Reset` 可用时按 Provider 指示重试，
  单次等待封顶 60 秒；普通 4xx 与无速率头的 403/429 不重试；
- Provider 原始响应上限 2 MiB，解码后的 `PROJECT.md` 上限 1 MiB；只接受
  Contents API 的 file/base64 形状与 UTF-8 内容；
- 请求固定使用 GitHub API version `2026-03-10`，并以有界
  `Response.read(size)` 读取响应；
- 每个成功响应或读取中断的 response 都在 `finally` 关闭；每个 `HTTPError` 也在
  提取 status/header 后、决定重试前显式关闭，cleanup 异常不覆盖原 Provider 错误；
- `PROJECT.md` 必须恰有一个 `BASE_UPSTREAM_VERSION`；Provider 路径使用 strict
  parser 拒绝任何重复 key。

### 4.2 共享依赖与集中脱敏

operator 脚本依赖集中在 `scripts/requirements.txt`（当前为
`jsonschema>=4.18`）；`serve/requirements-dev.txt` 通过 `-r` 复用该文件，避免
CLI 与合同测试声明两份依赖。

campaign 与 ledger 共用 `scripts/lib/base_release.py`：canonical SemVer、Manifest
验证/选择、`PROJECT.md` parser 与递归 `redact()` 只有一套实现。输出在写 stdout、
stderr、JSON 或 Markdown artifact 前统一脱敏：当前环境 token、Authorization、
Bearer token68、URL userinfo 和常见敏感赋值都会替换为 `[REDACTED]`。若 token
值与 batch/result 结构字段、字段名或生成 JSON 的结构字面值重叠，命令 fail closed
且不写出无效 artifact；脱敏并精确序列化后再次解析，执行 batch 与可选 registry
关联校验。Markdown 表格还会实体化不可信标点，避免 result
文本注入 HTML、反引号、链接或表格结构。JSON/Markdown 使用同目录临时文件加
`os.replace` 原子写入；输出之间或输出与 registry/result/fixture 输入发生同路径、
symlink 或 hardlink 碰撞时，命令在覆盖前停止。

### 4.3 T2 GitHub 下游 receiver

`.github/workflows/base-upgrade-receiver.yml` 只接受 `project_id`、canonical
`target_version`、`campaign_id` 和默认 `false` 的 `allow_major`，不接受 command、
remote、branch、token 或验证覆盖。所有 dispatch input 先进入 step environment，再由
shell 读取，禁止把不可信表达式直接插入 `run`。workflow 固定 Python 3.12、Node 22、
30 分钟上限、最小 `contents: write`/`pull-requests: write` 权限，并以 repository 级
`queue: max` 串行且不取消在途升级；checkout/setup-python/setup-node/upload-artifact
使用当前官方 v7 release 对应的 immutable commit SHA，不跟随可移动 major tag。

`scripts/run-base-upgrade.sh` 的固定接口为：

```bash
scripts/run-base-upgrade.sh \
  --project-id PROJECT_ID \
  --target-version X.Y.Z \
  --campaign-id CAMPAIGN_ID \
  --allow-major true|false \
  --result-file RESULT.json \
  --summary-file SUMMARY.md
```

runner 从默认分支 strict 解析 `PROJECT.md`，要求 `project_id=PROJECT_SLUG`，并校验
`BASE_UPSTREAM_VERSION/TAG/COMMIT/LAST_SYNCED_AT` 与 `BASE_UPDATES.md` 最后一条
entry 一致。timestamp 必须是 canonical UTC 秒级格式；最后 entry 必须恰有一个匹配
heading、Base commit、Synced at 与非空单行 verification result。
`BASE_UPSTREAM_REPOSITORY=OWNER/REPO` 由 bootstrap/ledger 从真实 remote 规范化写入；
receiver 不接受 URL 覆盖，只恢复公开的 `https://github.com/OWNER/REPO.git`，清除
Actions auth header 后匿名 fetch 证明 Base upstream 公开可读。Base 仓库在启用该
合同前必须保持 PUBLIC。

目标 tag 必须是 source tag 后代；降级始终 blocked，跨 MAJOR 默认 blocked。更新固定
调用 `./scripts/sync-base-release.sh TARGET --install-deps`：merge 目标 tree 后创建
fresh `.venv`，从目标 requirements/lock 安装依赖，再执行 route、pytest、lint/build、
database boundary 与 diff checks，最后把代码和两个 ledger 写成同一个两父 merge
commit。

成功后只 non-force push `chore/base-vTARGET`。若远端分支已存在，tip 必须精确满足：
第一父为当前默认分支 baseline、第二父为目标 Base commit，且 `PROJECT.md` 与最后一条
`BASE_UPDATES.md` 精确记录 target version/commit；证明不足即 blocked。新 PR 始终为
Draft；已有开放 PR 只更新标题/正文并保留其 Draft 或 Ready 状态，不自动合并。

runner 在 result/summary 写入前复用集中脱敏，使用原子临时文件，并同时执行 Draft-07
和 runtime identity/repository 关联校验。workflow `if: always()` 先发布 job summary、
上传唯一 JSON artifact，最后才依据 `pr_opened`/`up_to_date` 或失败状态决定 run 结果。
冲突文件以 NUL-safe Git 输出读取并直接用 result schema 的 canonical path 约束验证；
冲突或验证失败先持久化 result/summary，再 abort，绝不 push 或创建 PR。

若 checkout、Python/Node setup、commit identity、contract dependency 安装或 runner
在合法 inputs 下未产出 result，独立 `if: always()` fallback 生成 schema-valid
`dispatch_failed`，按第一个失败 outcome 填写 `failed_stage`；已有 runner result 为权威，
fallback 不覆盖。fallback 之后仍按固定顺序发布 summary、上传 artifact、最后失败。

PR body 明示 CURRENT/TARGET，并从 `(source,target]` 受信 Release Manifests 渲染每个
版本的全部 update nodes、migrations、conflict hotspots、downstream actions 和
release verification；另列出 target ancestry、Manifest plan、完整 sync、non-force
publication、原子 ledger 的逐项 **PASS**，以及 retry/rollback。Manifest 文本被
实体化为不可执行 Markdown 数据，不进入 shell 求值。runner 单独追踪 `new_branch_pushed` 与
`new_pr_created`：rollback 只删除本次新推分支或关闭本次新建 PR；已有分支/PR 即使被
幂等复用或更新也不自动删除。

fixture mode 只允许非 CI 动态本地仓库，不能在 `CI`/`GITHUB_ACTIONS` 中启用；它不
接受任意 shell，仍调用固定 sync 脚本。`scripts/test-base-upgrade-e2e.sh` 在 `/tmp`
动态建立 bare Base/downstream repositories，覆盖 clean、重复运行、dirty、缺 tag、
祖先漂移、冲突、验证失败、首次采用依赖恢复与 secret 不泄漏。

最终本地验收证据：T0～T2 组合定向 272 passed，T2 workflow/E2E/bootstrap/ledger
四文件 78 passed，后端完整 563 passed，动态 Git E2E 9/9；Route Manifest 159 routes、
1 mount，Alembic、release/database boundary、bootstrap、frontend lint/build、runner
`bash -n` 与 `git diff --check` 全部 PASS。T2.5 发布提交为
`abc907682edd55d0b58624c6b0fc78c73b8e41e1`，已推送 annotated `base/v3.3.0` 且远端
解引用到同一 commit；receiver workflow 已 active，Base repository 为 PUBLIC。

### 4.4 v3.2.0 → v3.3.0 首次安装依赖恢复

首次 receiver 只能随正式 `base/v3.3.0` tag 进入下游。v3.2.0 的旧 sync 脚本还不
识别 `--install-deps`，因此先运行 `./scripts/sync-base-release.sh 3.3.0`。若目标 tree
已经 merge、但旧路径因 fresh 环境缺目标依赖而在验证阶段停止，保留同一工作区的
`MERGE_HEAD`，再用目标 tree 中的新脚本执行：

```bash
./scripts/sync-base-release.sh 3.3.0 --continue --install-deps
```

这会验证 `MERGE_HEAD`、安装目标依赖并重跑完整检查，随后原子记录两个 ledger。已经
abort 或销毁的 CI 工作区不具备 `--continue` 条件，必须从干净默认分支重新开始。

### 4.5 T3 GitHub operator dispatch（已发布并完成试点）

`dispatch` 是一次有界、按项目故障隔离的生产路径：

```bash
python3 scripts/base-upgrade-campaign.py dispatch \
  --registry fleet/projects.json \
  --target-version X.Y.Z \
  --campaign-id CAMPAIGN_ID \
  --channel stable \
  --json-output /tmp/batch.json \
  --markdown-output /tmp/summary.md \
  --evidence-output /tmp/evidence.json
```

目标必须有本 checkout 可验证的 Release Manifest，registry 必须存在至少一个匹配
channel 的 enabled 项目，Token 只从 `BASE_UPGRADE_GITHUB_TOKEN` 读取。
`operator_commit` 优先取 `BASE_UPGRADE_OPERATOR_COMMIT`，否则从实际 Base tools checkout
`git rev-parse HEAD` 发现；只接受小写 40-hex commit。batch、summary、evidence 三个输出
路径必须两两不同，且不得与 registry 发生解析路径、symlink 或 hardlink 碰撞。

v1 按 `project_id` 稳定顺序串行处理项目。每个 run 最多轮询 30 分钟；超时对
精确 run ID 发起一次 cancel（`202` 或竞态 `409`），再最多等待 5 分钟进入终态。
operator 示例 workflow 的总上限是 120 分钟，因此一次匹配的 enabled 项目硬上限
为 3；超过 3 时在任何 Provider 调用前 fail closed。大 fleet 必须以 channel 分批，
每批最多 3 项。进入项目循环后不 fail-fast；单项目发现、版本 gate、preflight、
dispatch、poll/cancel 或 artifact 失败只记录当前结果，其余项目继续。

派发前先从默认分支 strict 解析 `PROJECT.md`，执行降级/跨 MAJOR gate 和
Manifest 范围校验，再查询精确 `chore/base-vTARGET` head + default base 的开放 PR。
最多允许一个匹配 PR，且必须是目标 repository 自身的 head/ref/SHA/base/URL。没有
开放 PR 时，若远端分支存在，必须独立获取并解引用目标 Base tag，校验分支
`PROJECT.md`/`BASE_UPDATES.md`、精确两父 merge（第一父为当前默认 tip，第二父为
目标 Base commit）。任一所有权证据不足时 `blocked`，不派发、不 force-push。

GitHub API 固定 `X-GitHub-Api-Version: 2026-03-10`。workflow dispatch POST 请求只包含
default `ref` 与 `project_id`/`target_version`/`campaign_id`/`allow_major` inputs，
不发送已移除的 `return_run_details`。只接受 `200` 且 body 必须直接返回正确
`workflow_run_id`/`run_url`/`html_url`。正常路径以返回 run ID 为权威；响应丢失、
5xx 或 malformed 200 先按唯一 run-name + event + ref + bounded time window 恢复，匹配
多个立即 fail closed，0 个才有界重试，总 POST 次数最多 2。普通 4xx 不重试；
403/429 只在 `Retry-After` 或 remaining/reset 头可用时有界等待。

轮询只接受同 repository、`workflow_dispatch` event、default branch、receiver workflow
路径的精确 run。回收时要求该 run 唯一、未过期的
`base-upgrade-result-CAMPAIGN_ID` artifact，artifact metadata 必须绑定 run ID 且 archive URL
必须绑定 registry repository。API 下载端点的 redirect 手工跟随，对允许的签名
download host 不发送 Authorization/Bearer。archive 与 metadata SHA-256 一致，ZIP 只能
包含单一精确 `base-upgrade-result.json`，且拒绝 path traversal、backslash/NUL、symlink、
encrypted entry、超限大小/压缩率、非 UTF-8、重复 JSON key 与非终态 result。最后同时
校验 schema、campaign/project/target/source 和已有 PR identity；仅允许竞态期间下游
已被其他 campaign 升到 target 时，`up_to_date` 的 source 等于 target。

`examples/github-actions/base-upgrade-campaign.yml` 必须复制到私有 operator/ops 仓库，
不在 Base 中直接运行。该仓库预先配置 `fleet/projects.json`、
`BASE_UPGRADE_GITHUB_TOKEN` secret、`BASE_PLATFORM_REPOSITORY` managed variable 和指向已
发布不可变 tag/commit 的 `BASE_PLATFORM_REF`。workflow checkout 私有 ops 仓库根目录，
再以 `persist-credentials: false` checkout Base tools 到 `.base-operator`，安装该 checkout
的 `scripts/requirements.txt`，并把实际 HEAD 注入 `BASE_UPGRADE_OPERATOR_COMMIT`。它只接受
人工 workflow inputs，权限为 `contents: read`，同 campaign 串行且不取消在途运行。
`if: always()` 终止门要求 batch/summary/evidence 三份 artifact 完整，重新验证 schema、
workflow identity、实际 operator commit、排序/唯一性、时间顺序和 result/evidence 对应，
再以固定名 `base-upgrade-campaign-evidence` 上传30天。

### 4.6 T3/T4 验收证据

- T0～T3 组合定向 `322 passed`，其中 campaign + dispatch `95 passed`、receiver
  workflow `23 passed`；动态 Git E2E `9/9 PASS`；
- 后端完整回归 `613 passed in 14.46s`；Alembic upgrade 与 Route Manifest
  `159 routes, 1 mount` PASS；
- frontend `npm ci`/lint/build PASS（保留 2 个既有 lint warning 与 npm audit 报告的
  9 个既有 vulnerability，均不由 T3 引入）；
- release check PASS（v3.4.0 发布时 9 个 Manifest）；database boundary、bootstrap plan、
  `git diff --check` 全部 PASS。

### 4.7 T5 前置：v3.3.1 receiver bootstrap patch

GitHub `workflow_dispatch` 执行的 receiver/runner 来自下游默认分支当前已采用版本。
因此直接从 v3.3.0 派发 v3.4.0 时，实际运行的 v3.3.0 runner 还不知道 T3
新增的 migrations、conflict hotspots、downstream actions 与 release verification
四组 PR 正文分节。目标 tree 中包含新 runner 不能修正本次 workflow 已启动时选择
的旧执行逻辑。

T5 必须先从已发布 `base/v3.3.0` 创建严格 PATCH backport：只包含
`scripts/run-base-upgrade.sh` PR 正文增强、`serve/tests/test_base_upgrade_workflow.py`
回归与标准 v3.3.1 发布元数据/文档；明确不包含 dispatcher、evidence schema、
campaign example workflow 或 dispatch fixtures/tests。完整验证并发布不可变
`base/v3.3.1` 后，又以 `base/v3.3.2` 修复真实外层 no-commit 同步中 E2E 对
`MERGE_HEAD` 的相对路径误读。3 个试点必须手工采用 v3.3.2；主线 T3 基于该 tag
发布 `base/v3.4.0`。首轮 v3.3.2→v3.4.0 演练证明，下游 `GITHUB_TOKEN` 会拒绝
push 目标 tree 新增的 workflow 文件；三个项目均 `blocked@push`，未留下分支或 PR。
`base/v3.4.1` 因此将 onboarding 示例移到
`examples/github-actions/base-upgrade-campaign.yml`，不扩大 receiver 权限；随后
v3.3.2→v3.4.1 的三个 Draft PR 全部成功，第二次不同 campaign ID 重跑全部复用原
分支和 PR，三个默认分支均保持不变。

---

## 5. PROJECT.md 是版本唯一权威

- registry **不接受** `current_version` 字段，也不维护可能漂移的版本副本；
- 自动升级执行时，必须从每个下游默认分支的 `PROJECT.md` 读取
  `BASE_UPSTREAM_VERSION` 作为真实当前版本；
- T1 在默认分支只读发现 `PROJECT.md`；文件缺失、版本非法或重复 key 时，该项目
  记为 `blocked`，不得猜测版本；
- T1 不读取 `BASE_UPDATES.md`；T2 在下游本地验证 `PROJECT.md` 与
  `BASE_UPDATES.md` 最后一条 entry 的 version/commit 一致；
- 下游必须继续遵守 `serve/docs/base-update-ledger.md` 的账本合同。

---

## 6. Secret 与安全边界

- Provider token 只从 `BASE_UPGRADE_GITHUB_TOKEN` environment 读取，不进入
  registry、CLI 参数、日志、报告或 artifact；
- registry 和 result schema 通过未知字段禁止 secret/Token/数据库字段，并从
  `verification_summary`、`retry_command`、`rollback_command` 中拒绝 authority
  带 userinfo 的 credential-bearing URL；空 userinfo、仅 username 或 username/password
  均拒绝，普通 path/query/fragment 中的 `@` 仍合法；这不等于能穷举或识别
  所有 secret 字符串；
- T1 不写任何分支；T2 receiver 只允许 non-force 写
  `chore/base-vTARGET_VERSION` 命名空间，默认分支禁止直接 push；
- 下游数据库 identity 继续通过 `scripts/check-database-boundary.py` 验证；
  Base 不缓存、不代理、不集中保存任何下游数据库 secret；
- T1～T3 对日志、自由文本、summary 和 artifact 复用共享集中脱敏；
  secret 与 batch/evidence 结构事实碰撞时 fail closed，不改写关联字段或写出无效
  artifact；
- T3 artifact 下载把 GitHub API 认证端点与签名 download host 分开，手工检查
  redirect，不向签名 host 转发 Bearer token；
- evidence 的 `project_id` 必须由 operator 预分配为非敏感 opaque ID；工具不负责
  自动匿名化。`run_url` 会暴露 repository identity，真实 evidence 只进入私有
  operator/ops 仓库或受保护 artifact。

---

## 7. Rollback 边界

- T1 计划器默认只读，未修改下游仓库；单次运行的回滚只是删除本地 JSON/Markdown
  artifact。
- 回滚 T1 实现时删除 `scripts/base-upgrade-campaign.py`、batch schema、T1 fixtures/
  tests 与 `scripts/requirements.txt`，从 `serve/requirements-dev.txt` 移除共享依赖；
  将 `base-update-ledger.py` 中的共享 release/parser 引用恢复为原实现，再删除
  `scripts/lib/base_release.py`。T0 的两个 schema 与合同测试必须保留，除非连 T0
  一并回滚；
- 每个 result 必须携带非空 `rollback_command`；有变更时指向可重复回滚，
  无变更时明确记录无需动作；
- T2 receiver 的回滚边界：
  - 未合并的同步：`git merge --abort`；
  - 已合并：revert 整个同步 merge commit，`BASE_UPDATES.md` 只追加不删除；
  - 本次新推分支才删除，本次新建 PR 才关闭；既有分支/PR 不自动删除；
  - PR 创建失败但本次已新推分支时，仅删除该分支；默认分支不受影响；
  - 不得删除已写入下游 `BASE_UPDATES.md` 的既有事实。

receiver 在冲突时必须先捕获冲突文件并写出 result，再在临时 CI
工作区销毁前执行 `git merge --abort`；这类结果的重试入口是重新派发。
`--continue` 只适用于尚保留原 `MERGE_HEAD` 的同一本地工作区，不得写成已经
abort 或已销毁 CI 工作区的恢复命令。

T3 operator 本身不写下游默认分支；停止 campaign workflow 或撤销 Provider token
即停止新派发。批次失败后保留已定位 run 的 partial evidence，只删除本地/私有
ops artifact 不会回滚下游事实。下游分支、PR 和 ledger 继续严格由 receiver 的资源
所有权规则处理，禁止 operator 在证据不足时自动删除或 force-push。

---

## 8. 完成状态

- T3/T4 实现、fixture/mock 合同、完整回归与故障矩阵已完成；
- `base/v3.3.1` receiver 正文 PATCH、`base/v3.3.2` 嵌套 E2E Git-dir PATCH、
  `base/v3.4.0` operator campaign 与 `base/v3.4.1` workflow-path PATCH 均已发布为
  不可变 tag；v3.4.1 commit 为
  `081cd1407fb902aebbd344c1518cf361e1ec9587`；
- 三项目首轮 v3.4.0 campaign 均 `blocked@push` 且 0 branch/PR；v3.4.1 成功轮
  `pr_opened=3`；幂等轮复用 3 个原分支/PR，默认分支和 Draft 状态不变；
- batch、summary、evidence 的 SHA-256、opaque operator run ID 和私有 evidence
  commit 已记录在关联 TODO；真实 repository、run/PR URL 与 credential 未进入 Base；
- T0～T5 全部完成，保留每个 channel 最多 3 个 enabled 项目的 v1 硬上限。

---

## 9. 复用性说明

本合同只描述 Base release tag、通用下游账本、标准 Git 分支和升级结果，不包含
任何产品业务规则。真实 fleet registry 留在 operator 仓库，真实 secrets 留在
各自 CI secret configuration；因此同一套能力可服务多个下游项目，且不会把某个
项目的部署信息或业务实现带回 Base。
