# Base 下游升级自动化合同

**状态**：T0 数据合同、T1 只读计划器/批次报告与 T2 下游 receiver 已实现并通过
本地验收；T2.5 元数据已准备但尚未 commit/tag/push，T3～T5 尚未实现。
**关联 TODO**：`serve/docs/todo-downstream-upgrade-automation-2026-08-18.md`
**计划发布**：`base/v3.3.0`（尚未发布；本文件不构成发布 Manifest）

本文档定义 Base 下游自动升级流程的**数据合同、只读计划与下游执行边界**。当前已
固化 registry、单项目结果和批次结果三种 JSON 格式，提供 registry 校验、升级计划、
结果汇总 CLI 与 GitHub receiver；中央 Provider 派发器仍未实现。

---

## 1. 文件层与职责边界

自动升级能力涉及三类文件，必须严格区分：

| 层 | 位置 | 内容 | 归属 |
|---|---|---|---|
| Base 通用合同与工具 | `scripts/schemas/*.schema.json`、`scripts/base-upgrade-campaign.py`、`scripts/run-base-upgrade.sh`、`.github/workflows/base-upgrade-receiver.yml`、`scripts/lib/base_release.py`、本文档 | schema、只读计划、批次汇总、下游 receiver、共享 release/脱敏边界 | Base 仓库，产品无关 |
| operator 实例配置 | operator/ops 仓库（例如 `fleet/projects.json`） | 真实项目清单、channel、enabled 开关 | operator 仓库，不进入 Base |
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
result 数组；数组会按 `project_id` 规范化为 batch。T3 的生产调用必须同时提供
`--registry`，不能省略跨文件关联校验。

### 3.2 Schema 外的运行时关联校验

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
固定远端分支和开放 PR。T3 仍负责 dispatch run、artifact 回收及 result 与 operator
registry 的跨仓库事实关联。

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

PR body 从 `(source,target]` 受信 Release Manifests 渲染全部 update nodes，并列出
target ancestry、Manifest plan、完整 sync、non-force publication、原子 ledger 的逐项
**PASS**，以及 retry/rollback。runner 单独追踪 `new_branch_pushed` 与
`new_pr_created`：rollback 只删除本次新推分支或关闭本次新建 PR；已有分支/PR 即使被
幂等复用或更新也不自动删除。

fixture mode 只允许非 CI 动态本地仓库，不能在 `CI`/`GITHUB_ACTIONS` 中启用；它不
接受任意 shell，仍调用固定 sync 脚本。`scripts/test-base-upgrade-e2e.sh` 在 `/tmp`
动态建立 bare Base/downstream repositories，覆盖 clean、重复运行、dirty、缺 tag、
祖先漂移、冲突、验证失败、首次采用依赖恢复与 secret 不泄漏。

最终本地验收证据：T0～T2 组合定向 272 passed，T2 workflow/E2E/bootstrap/ledger
四文件 78 passed，后端完整 563 passed，动态 Git E2E 9/9；Route Manifest 159 routes、
1 mount，Alembic、release/database boundary、bootstrap、frontend lint/build、runner
`bash -n` 与 `git diff --check` 全部 PASS。T2.5 仍等待 commit/tag/push。

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
- T1 与 T2 receiver 对日志、自由文本、summary 和 artifact 复用共享集中脱敏；
  secret 与结构字段碰撞时 fail closed，不写无效 artifact。

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

---

## 8. 当前未实现内容

- 未创建中央 Provider dispatcher（T3）；
- 未执行 T3 Provider 派发端到端演练与真实下游试点（T4、T5）；
- 未修改 `VERSION`、`CHANGELOG.md` 或 release Manifest；
- 未创建 `base/v3.3.0` tag。

---

## 9. 复用性说明

本合同只描述 Base release tag、通用下游账本、标准 Git 分支和升级结果，不包含
任何产品业务规则。真实 fleet registry 留在 operator 仓库，真实 secrets 留在
各自 CI secret configuration；因此同一套能力可服务多个下游项目，且不会把某个
项目的部署信息或业务实现带回 Base。
