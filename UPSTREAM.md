# Base Upstream 同步合同

本文件定义所有以 Base Platform 为基座的 Fork/Clone 项目如何及时、可重复、低冲突地同步更新。

## 1. 固定关系

下游项目必须保留两个 remote：

```bash
git remote rename origin project
git remote add upstream <BASE_REPOSITORY_URL>
git remote -v
```

- `upstream`：只指向本 Base 仓库。
- `project`：指向下游项目自己的仓库。
- 下游业务代码只提交到 `project`，不直接提交到 `upstream`。

## 2. Base 发布合同

每次 Base 更新必须按以下顺序完成：

1. 只修改通用、产品无关的代码和文档。
2. 按 SemVer 更新 `VERSION`。
3. 同步 `admin/package.json` 和 `admin/package-lock.json` 的版本号。
4. 在 `CHANGELOG.md` 新增同版本条目，写清变更、兼容性、迁移、同步和回滚。
5. 新增 `releases/base-vX.Y.Z.json`，为每个更新节点记录稳定 ID、类型、范围、
   文件、兼容性、迁移、下游动作、冲突点、验证与回滚。
6. 运行 `python3 scripts/check-base-release.py`、后端测试、前端 lint/build 和 `git diff --check`。
7. 创建发布提交：`release(base): vX.Y.Z`。
8. 创建不可变 tag：`base/vX.Y.Z`，再推送提交和 tag。

禁止复用旧版本号、移动已发布 tag、覆盖已发布账本或只提交代码不写升级说明。

## 3. 版本号规则

- **MAJOR**：不兼容的 API、数据模型、目录或升级流程变化。
- **MINOR**：向后兼容的通用能力新增或扩展。
- **PATCH**：向后兼容的 bug、安全、性能、文档或测试修复。

任何需要下游手工改业务代码的变化必须升级 MAJOR，或在账本中提供明确的兼容层和分步迁移。

## 4. 下游同步步骤

同步前必须提交或暂存自己的项目变更，保持工作树干净：

```bash
git status --short
git fetch upstream --tags --prune
./scripts/sync-base-release.sh X.Y.Z
```

脚本会检查工作树、获取 tag，并执行：

```bash
git merge --no-ff --no-commit refs/tags/base/vX.Y.Z
```

合并前脚本从目标 tag 聚合 CURRENT→TARGET 之间的所有 Manifest，打印“更新哪些”；
随后执行 route/pytest/lint/build/diff 验证；通过后更新 `PROJECT.md`、追加含 PASS
证据的 `BASE_UPDATES.md`，把代码合并和版本账本放进同一个 merge commit。
发生冲突或验证失败时在账本变更前停止，可修复或 `git merge --abort`。
冲突或验证/commit hook 问题修复并 `git add` 后使用
`./scripts/sync-base-release.sh X.Y.Z --continue`；脚本会核对 MERGE_HEAD、从 merge
前的 HEAD 恢复源版本依据、避免重复历史、重新验证并完成同一个原子 merge commit。

没有脚本时使用上面的命令手动同步。同步完成后：

```bash
python3 scripts/check-base-release.py
cd serve && pytest
cd ../admin && npm run lint && npm run build
cd .. && git diff --check
git push project <PROJECT_BRANCH>
```

### 数据库隔离前置检查

Base 仓库本机保留的 `base_platform_app@base_platform` 不随代码同步给下游使用。
每个下游必须在自己的 secret 配置中设置项目专属 `DATABASE_NAME`、
`DATABASE_USER`、`DATABASE_PASSWORD`，并确认其值均不是 `base_platform`、
`base_platform_app`。下游严禁运行 `scripts/provision-base-database.sh`，因为该脚本
只服务于 Base 仓库自身的固定数据库身份。

新 Fork/Clone 完成 `upstream` remote 后，使用：

```bash
scripts/bootstrap-project.sh PROJECT_SLUG "Project Name"
```

脚本自动完成项目专属 database/role、环境文件、依赖、迁移、测试/build 和
`PROJECT.md` 当前账本和 `BASE_UPDATES.md` 追加历史。已存在的下游项目继续使用
自己的数据库，不重新 bootstrap。

## 5. 迁移与冲突

- 先读目标版本的 `CHANGELOG.md`，再执行任何数据库迁移。
- Base 迁移和下游产品迁移分开执行；禁止用产品迁移替换 Base 迁移。
- 冲突时保留下游产品代码，重新应用 Base 的通用变更；不得用整目录覆盖方式解决冲突。
- 冲突热点必须记录在下游项目自己的同步记录中，不能反向写入 Base。
- 同步失败时保留同步前 tag/分支，修复冲突后重新运行完整验证。

## 6. 下游版本账本

每个下游项目应在自己的仓库记录：

```text
BASE_UPSTREAM_VERSION=3.2.0
BASE_UPSTREAM_TAG=base/v3.2.0
BASE_UPSTREAM_COMMIT=<immutable commit>
BASE_UPSTREAM_REPOSITORY=OWNER/REPO
BASE_LAST_SYNCED_AT=YYYY-MM-DDTHH:MM:SSZ
BASE_UPDATE_LEDGER=BASE_UPDATES.md
BASE_NEXT_UPDATE_COMMAND=./scripts/sync-base-release.sh <TARGET_VERSION>
```

`PROJECT.md` 保存当前状态；每次采用/同步的全部节点、文件差异、迁移、动作、验证
和回滚追加到 `BASE_UPDATES.md`。下游不得修改 Base 的 `CHANGELOG.md`；产品变更写入
下游自己的产品账本。完整格式和 v3.2.0 首次接入见
`serve/docs/base-update-ledger.md`。

## 7. 下游升级自动化合同、receiver 与 operator dispatch

Base 仓库已在不可变 `base/v3.3.0` 固化三条自动升级 JSON Schema、只读
campaign CLI 和 GitHub 下游 receiver，并以 `base/v3.3.1` PATCH 补全 receiver PR
正文合同、以 `base/v3.3.2` PATCH 修复嵌套 merge E2E。T3 operator dispatch 与
第四条 evidence schema 已由 `base/v3.4.0` 发布；operator 示例的 workflow-path
权限修正已由 `base/v3.4.1` 发布并完成三项目真实试点：

- `scripts/schemas/base-downstream-registry.schema.json`：声明式下游项目清单。
  `repository` 固定为 GitHub canonical `OWNER/REPO`，OWNER/REPO 分别最长
  39/100，禁止 URL、credential、`.git` 后缀、嵌套路径和换行；
  不接受 `current_version` 或任何 secret/Token 字段；`BASE_UPSTREAM_VERSION`
  必须从下游 `PROJECT.md` 读取。
- `scripts/schemas/base-upgrade-result.schema.json`：单项目升级结果。
  所有字段都必填，不适用的标量字段用 `null`、冲突列表用空数组；
  `status` 枚举为 `planned`、
  `dispatched`、`up_to_date`、`pr_opened`、`conflict`、`verification_failed`、
  `blocked`、`dispatch_failed`，并按状态约束 branch/PR/失败阶段/冲突列表。
- 三条 schema 均为 JSON Schema Draft-07；registry/result 的 Git ref 拒绝隐藏
  segment、`..`、`//`、`@{`、`.lock` 和换行，版本只接受无前导零的 core
  SemVer `X.Y.Z`，结果文本
  artifact 必须非空白且为单行，冲突文件必须是无控制字符的规范
  repository-relative path。
- `scripts/schemas/base-upgrade-batch.schema.json`：一个 campaign 的批次结果；固定
  单一 `campaign_id`、`target_version` 和非空 results。runtime 进一步强制
  `project_id` 排序/唯一、顶层与子项 campaign/target 一致、`up_to_date` 版本一致、
  分支版本与目标一致；`summarize --registry` 还校验项目和 PR repository 关联。
- `scripts/schemas/base-upgrade-evidence.schema.json`：私有 operator 运行证据。顶层
  `operator_commit` 绑定实际执行的 Base tools checkout；只记录已取得 run ID 的
  派发，并保存 run locator、artifact/archive SHA-256、result SHA-256 与时间。
  poll 或 artifact 回收失败时保留条目，未取得的字段为 `null`，以
  `failure_stage` 标记部分证据。
- 详细合同见 `serve/docs/downstream-upgrade-automation.md`。

只读工具：

```bash
python3 scripts/base-upgrade-campaign.py validate-registry --registry fleet/projects.json
python3 scripts/base-upgrade-campaign.py plan \
  --registry fleet/projects.json --target-version X.Y.Z \
  --campaign-id CAMPAIGN_ID --channel stable
python3 scripts/base-upgrade-campaign.py summarize \
  --results RESULTS.json --registry fleet/projects.json \
  --json-output BATCH.json --markdown-output SUMMARY.md
```

Live `plan` 通过 GitHub Contents API 从 registry default branch 读取 `PROJECT.md`，
Token 只来自 `BASE_UPGRADE_GITHUB_TOKEN`；每请求 15 秒、最多 3 次尝试，5xx/暂时
网络错误及 `http.client.HTTPException` 协议中断（已覆盖
`IncompleteRead`/`BadStatusLine`）按 1/2 秒有界重试；
403/429 仅按有效 rate-limit header 重试且单次等待封顶 60 秒。成功、读取中断及
`HTTPError` response 都显式关闭；
响应/解码文本分别限制 2 MiB/1 MiB。默认拒绝跨 MAJOR，
显式 `--allow-major` 也只生成计划；降级始终停止。离线 fixture 仅允许与
`--dry-run` 同用，且未指定 campaign ID 时生成确定性的
`plan-vTARGET_VERSION`。

真实项目清单、Provider token 和数据库信息只存在于 operator/ops 仓库与各下游
CI secret，禁止写入 Base。Schema 只验证单文档形状，只能拒绝未知
secret 字段和 credential-bearing URL，不能穷举自由文本中的所有 secret。T1 runtime
已校验 registry ID/repository 唯一性和 batch/result 本地关联，并在 stdout、stderr、
JSON/Markdown artifact 前集中脱敏环境 token、Authorization/Bearer、URL userinfo 与
敏感赋值。T2 receiver 进一步在下游 fresh checkout 严格校验 `PROJECT.md` 最后状态、
`BASE_UPDATES.md` 最后一条记录、默认分支、源/目标 tag 与远端分支/PR 所有权；T3
operator 实现再校验 dispatch run、artifact/result 与 registry/PR 的跨仓库事实。

每个下游 `PROJECT.md` 必须提交由 bootstrap/ledger 从真实 remote 规范化得到的：

```text
BASE_UPSTREAM_REPOSITORY=OWNER/REPO
```

该字段只接受无 credential 的 GitHub canonical identity。receiver 不接受 dispatch
覆盖 upstream URL，而是只据此恢复 `https://github.com/OWNER/REPO.git`，并先以匿名
fetch 证明 Base upstream 可公开读取。workflow 固定接收 `project_id`、
`target_version`、`campaign_id`、`allow_major`，使用 repository 级 concurrency queue、
Python 3.12/Node 22 和 fresh `.venv`，最终调用：

```bash
./scripts/sync-base-release.sh TARGET_VERSION --install-deps
```

`--install-deps` 在合并目标 tag 后从目标 tree 安装 operator/backend requirements 和
`npm ci`，再执行 route、pytest、lint/build、database boundary 与 diff checks。成功时
只以 non-force push 写 `chore/base-vTARGET_VERSION`：新 PR 始终为 Draft；已存在且
所有权可由精确原子 merge/账本证明的开放 PR只更新正文并保留原 Draft/Ready 状态。
分支所有权不足时 `blocked`，不 force-push。

冲突或验证失败时，runner 先生成并 Draft-07/runtime 复验唯一 JSON result 与 job
summary，再 abort 临时 merge；不 push、不创建 PR。result 的 retry 指向重新派发，
不是已经销毁工作区的 `--continue`。若 checkout、Python/Node setup、commit identity、
contract dependency 安装或 runner 对合法 inputs 未产出 result，后续
`if: always()` fallback 会生成 `dispatch_failed`，保留准确 `failed_stage` 后再上传；
已有 runner result 永远优先，不被 fallback 覆盖。

账本所有权校验不仅匹配 version/commit：`PROJECT.md` 的
`BASE_LAST_SYNCED_AT` 必须是 canonical UTC 秒级 timestamp，并与 `BASE_UPDATES.md`
最后一条的 `Synced at` 精确相同；最后一条还必须恰有一个匹配 heading、Base commit
和非空单行 verification result。PR 正文来自受信 Release Manifest 范围，列出
CURRENT/TARGET、完整 cross-version update nodes、migrations、conflict hotspots、
downstream actions、release verification，以及目标 tag/计划/同步/non-force
publication/原子账本的逐项 PASS evidence 和可执行 retry/rollback。Manifest 自由
文本先转为惰性 Markdown 字符实体，不会产生标题、链接、HTML、代码或命令执行语义，
也不进入 shell 求值。

rollback 只基于本次运行实际创建的资源：本次新建 PR 才关闭，本次新推分支才删除；
复用的既有分支/PR 永不自动删除。PR 创建失败但本次已新推分支时只删除该新分支。
默认分支始终未被 receiver 直接写入。result/summary 在写出前复用集中脱敏边界。

### v3.2.0 → v3.3.0 首次采用

receiver 必须随正式 `base/v3.3.0` tag 进入下游，禁止复制未发布 workflow/runner。
由于 v3.2.0 的旧同步脚本尚无 `--install-deps`，首次先运行原命令：

```bash
./scripts/sync-base-release.sh 3.3.0
```

若目标 tree 已合并但验证因 fresh 环境缺目标依赖而停止，保留该工作区的
`MERGE_HEAD`，直接使用已经合入的 v3.3.0 脚本恢复：

```bash
./scripts/sync-base-release.sh 3.3.0 --continue --install-deps
```

该恢复仍会重新执行完整验证并把 Base 代码、`PROJECT.md` 与 `BASE_UPDATES.md` 原子
提交；若已 abort 或 CI 工作区已销毁，则必须从干净默认分支重新同步，不能使用
`--continue`。

### T5 试点前的 v3.3.1 bootstrap patch

receiver 执行的 workflow/runner 来自下游默认分支的当前版本。若直接从
v3.3.0 派发 v3.4.0，当次仍使用 v3.3.0 runner，PR 正文不会包含 T3 新增的
migrations、conflict hotspots、downstream actions 和 release verification 分节。
因此真实试点必须按以下顺序：

1. 从已发布 `base/v3.3.0` 严格 backport runner PR 正文增强、对应 workflow 测试
   和标准 PATCH 发布元数据；不带入 dispatcher、evidence schema、campaign workflow
   或 dispatch fixtures/tests；
2. 完整验证后发布不可变 `base/v3.3.1`；
3. 真实同步发现嵌套 E2E 相对 Git-dir 缺陷，以 `base/v3.3.2` PATCH 修复并验证；
4. 三个试点使用手工 `sync-base-release.sh` 合同采用 v3.3.2，不复制文件；
5. 主线 T3 基于该确切 PATCH 后发布不可变 `base/v3.4.0`；
6. 只对账本已记录 v3.3.2 的试点派发 v3.4.0；首轮三个 receiver 均在
   `push` 阶段因目标 tree 新增 workflow 文件而 `blocked`，未创建分支或 PR；
7. 以 `base/v3.4.1` 将 operator onboarding 示例移至
   `examples/github-actions/base-upgrade-campaign.yml`，保持 receiver 最小权限；
8. 三项目 v3.3.2→v3.4.1 全部创建 Draft PR；使用不同 campaign ID 重跑后全部
   复用同一 `chore/base-v3.4.1` 分支和 PR，默认分支保持不变。

### T3 operator campaign（`base/v3.4.1`）

operator/ops 仓库必须保持私有，并在自己的默认分支提交
`fleet/projects.json`。将 `examples/github-actions/base-upgrade-campaign.yml` 复制到该
仓库后，配置：

- managed variable `BASE_PLATFORM_REPOSITORY=OWNER/REPO`；
- managed variable `BASE_PLATFORM_REF=base/vX.Y.Z` 或确切 40-hex commit，必须是已发布的
  不可变 Base tools ref；
- secret `BASE_UPGRADE_GITHUB_TOKEN`，对选中下游具有 Actions read/write、Contents
  read 和 Pull requests read；不放入 registry 或 workflow input。

模板先 checkout 私有 ops 仓库，再以 `persist-credentials: false` checkout Base tools
到 `.base-operator`，将实际 HEAD 记为 `BASE_UPGRADE_OPERATOR_COMMIT`，安装
`.base-operator/scripts/requirements.txt`，然后执行：

```bash
python3 .base-operator/scripts/base-upgrade-campaign.py dispatch \
  --registry fleet/projects.json \
  --target-version X.Y.Z \
  --campaign-id CAMPAIGN_ID \
  --channel CHANNEL \
  --json-output "$OUTPUT_DIR/batch.json" \
  --markdown-output "$OUTPUT_DIR/summary.md" \
  --evidence-output "$OUTPUT_DIR/evidence.json"
```

跨 MAJOR 必须另加 `--allow-major`。dispatch 固定使用 GitHub REST
`X-GitHub-Api-Version: 2026-03-10`；POST body 只包含 `ref` 和 receiver inputs，
不发送该 API 版本已移除的 `return_run_details`。成功响应必须为 `200`
并返回与目标 repository 匹配的 run ID/API URL/web URL。遇到响应丢失、5xx 或
非法 200 body 时，先以唯一 run-name + `workflow_dispatch` event + default ref +
时间窗口恢复；唯一恢复为 0 时才有界重试，匹配多个则 fail closed，总 POST
次数不超过 2。普通 4xx 不重试；403/429 只按有效 rate-limit headers 有界等待。

派发前 operator 先精确查询 `chore/base-vTARGET` 的开放 PR。若只有分支，
必须以目标 Base tag/commit、默认分支 tip、两父 merge 和两个 ledger 证明所有权；
不足时 `blocked`，禁止 force-push。定位 run 后按其 ID 轮询，只接受该
repository/default ref/receiver workflow 的运行，并下载唯一且未过期的
`base-upgrade-result-CAMPAIGN_ID` artifact。artifact redirect 手工跟随到允许的签名
host，不将 Bearer token 转发给该 host；ZIP 必须仅含单一精确命名 JSON，并通过
digest、大小/压缩率、路径、重复 key、schema 与 campaign/project/source/target/PR 关联校验。

v1 批次按项目串行；每项目最多等待 run 30 分钟，超时 cancel 后再最多等待
5 分钟，operator 示例 workflow 总超时为 120 分钟。因此一次 channel 选中项目
硬上限为 3；超过 3 时在任何 Provider 调用前 fail closed。大 fleet 必须以 channel
分批，每批最多 3 个 enabled 项目。一个项目失败只生成该项目的
`blocked`/`dispatch_failed`，不阻断其余项目。

batch、summary 与 evidence 是同一次运行的固定三件套。`project_id` 必须是
registry 预分配的稳定、非敏感 opaque ID，工具不会自动匿名化。evidence 的
run URL 仍包含下游 repository identity，因此真实文件只能进入私有 operator/ops
仓库或其受保护 CI artifact。取得 run ID 后立即建立 evidence entry；后续 poll/
artifact 回收失败也保留 run locator 和已知时间，以 nullable collection 字段加
`failure_stage` 表示部分证据。模板在 `if: always()` 终止门中重新校验三份
artifact、`operator_commit`、identity、唯一性、时间顺序与 result/evidence 对应，
然后上传固定名 `base-upgrade-campaign-evidence`。

### T5 三项目试点证据（2026-08-18）

`base/v3.4.1` 已发布到 commit
`081cd1407fb902aebbd344c1518cf361e1ec9587`，本地与远端不可变 tag 均解析到该
commit。Base 只记录 campaign ID、聚合结果、SHA-256 和私有 evidence commit；真实
repository、run/PR URL 与 Provider token 仍只存在于私有 operator/ops evidence。

| opaque operator run | campaign | receiver 聚合 | 私有 evidence commit |
|---|---|---|---|
| `operator-run-01`（success） | `pilot-v340-20260818-01` | `blocked@push=3`，branch=0，PR=0 | `9910df80b59c06c7caf9e645ef13c0e146b67f2d` |
| `operator-run-02`（success） | `pilot-v341-20260818-02` | `pr_opened=3`，3 个 Draft PR，默认分支未变 | `0bc04ccaa8d88c3edb9f6f5b3f8117c3e8fdfed7` |
| `operator-run-03`（success） | `pilot-v341-20260818-03` | `pr_opened=3`，同一分支/PR 全部复用，默认分支未变 | `8d2849bc6ee33db4ccda66f7b49bea5c6a4e6099` |

私有归档中的 batch/summary/evidence 三件套 SHA-256：

| campaign | batch | summary | evidence |
|---|---|---|---|
| `pilot-v340-20260818-01` | `6091dff3ff30fb8406a37f6912f53bd93a3df70f73e10d16c86d0bdd2dd75097` | `aa10507d1e1acaba3c46c3ddc25a20bb9472a27745aee2c0de901d1221d88781` | `81184da71460f29c5ee88b888eb777801a0188624f34fbce853cb566ce379b6e` |
| `pilot-v341-20260818-02` | `ae3a88e070b3ef50264f840466cc5e697642922e60336edce0e725c052380b39` | `af049286a5b33ab28cbbb846a5aef4701193f347e504630fb271d4c88bfef8e6` | `ba140881c6a0dd61a4a3645693820277db4e26140a5ac6d27e90de6b3a363ebf` |
| `pilot-v341-20260818-03` | `9c4ecc3539cdc52e9bc086921a783af25a8ef22662dd284bf9e219210679ca48` | `94594ef02b55d79ce6cfd9096d72157e4da763df2757fe698e89b84ca13fd98e` | `3fbe8740da1f8aecb31ca2a9133f588240f9b9b2f9a440c7ffec3304c0b21a79` |

第三轮另保存脱敏 Provider state 的 before/after 快照；两者 SHA-256 均为
`bc91103a898b23c2a386131df875f7af4c3ee3d3b5af5b0b1129ce0106da3eda`，证明三个
PR identity、升级分支 head、Draft 状态与默认分支 SHA 全部未变化。三轮 operator
workflow 均完成，未自动合并任何 PR；T5 至此完成。

operator 依赖统一来自 `scripts/requirements.txt`，后端 dev requirements 复用该文件；
campaign 与 ledger 共用同一个 SemVer/Manifest/parser/redaction 模块。T1 不修改任何
下游分支或账本；单次运行回滚只需删除本地输出 artifact。实现级回滚必须先把 ledger
共享逻辑恢复到原位置，再删除 campaign、batch schema、共享模块及对应依赖/tests，
不能误删仍属 T0 的 registry/result schema。

### v3.3.0 → v3.3.1 PR 正文修复

v3.3.1 是无 migration 的 PATCH，只增强 receiver 产生的 Draft PR 正文。v3.3.0
下游执行：

```bash
./scripts/sync-base-release.sh 3.3.1 --install-deps
```

同步后使用原有 dispatch inputs 重新派发即可。receiver 会在新 Draft PR 或已有开放
PR 中显示所有选中 Manifest 的完整升级合同；分支、PR 所有权、验证、
result 与 rollback 行为不变。仅从不可变 `base/v3.3.1` tag 同步，不单独
复制 runner 或测试文件。


### v3.3.2 外层 merge E2E 路径修复

`base/v3.3.2` 是无 migration 的 PATCH。它把动态 Git E2E 的 `MERGE_HEAD` 检查绑定到每个
fixture repository 的 absolute Git dir，避免在 `sync-base-release.sh` 外层 no-commit merge
中误读调用方 `.git/MERGE_HEAD`。v3.3.0/v3.3.1 下游只从不可变 tag 同步：

```bash
./scripts/sync-base-release.sh 3.3.2 --install-deps
```

若旧尝试已 abort，从干净默认分支重试；只有同一工作区仍保留目标 merge 时才使用
`--continue`。
