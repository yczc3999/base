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

## 7. 下游升级自动化合同、只读计划与 receiver

Base 仓库已固化三条自动升级 JSON Schema、只读 campaign CLI 和 GitHub 下游
receiver；中央 Provider 派发器仍属于 T3，当前尚未发布 receiver 基座 tag：

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
`BASE_UPDATES.md` 最后一条记录、默认分支、源/目标 tag 与远端分支/PR 所有权；实际
fleet 派发和批次轮询仍属于 T3。

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
和非空单行 verification result。PR 正文来自受信 Release Manifest 范围，列出完整
cross-version update nodes、目标 tag/计划/同步/发布/原子账本的逐项 PASS evidence，
以及可执行 retry/rollback。

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

operator 依赖统一来自 `scripts/requirements.txt`，后端 dev requirements 复用该文件；
campaign 与 ledger 共用同一个 SemVer/Manifest/parser/redaction 模块。T1 不修改任何
下游分支或账本；单次运行回滚只需删除本地输出 artifact。实现级回滚必须先把 ledger
共享逻辑恢复到原位置，再删除 campaign、batch schema、共享模块及对应依赖/tests，
不能误删仍属 T0 的 registry/result schema。
