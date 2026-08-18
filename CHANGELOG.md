# Base Platform 变更账本

本文件是 Base Platform 的发布账本。`VERSION`、`admin/package.json`、
`admin/package-lock.json` 和本文件的最新发布标题必须保持同一版本号。

## 发布规则

- 每一次可被下游同步的更新都必须创建新的 SemVer 版本。
- 每个发布版本都必须有唯一的 Git tag：`base/vX.Y.Z`。
- 已发布版本的账本内容冻结；修正内容必须进入下一个版本。
- 每个版本必须写清：变更范围、兼容性、迁移命令、下游同步方式和回滚方式。
- `Unreleased` 只记录尚未发布的内容，不作为下游同步目标。

## [Unreleased]

暂无。

## [3.4.1] - 2026-08-18

### 变更范围

- 将 operator campaign 示例从 GitHub 自动识别的 `.github/workflows/` 移到
  `examples/github-actions/`。真实 v3.4.0 试点证明，下游 `GITHUB_TOKEN` 对包含新增
  workflow 文件的升级分支会拒绝 push；示例只属于私有 ops onboarding，不应进入下游
  workflow 路径。
- 更新模板路径测试与 operator 文档；dispatcher、evidence schema、receiver inputs/result
  和同步内核不变。

### 兼容性（PATCH）

- 无 HTTP API、数据库 schema 或运行时数据变化。
- v3.3.2 下游应直接以 `base/v3.4.1` 为目标；最终目标 tree 不含新增的下游 workflow
  文件，因此现有最小 `GITHUB_TOKEN` 可 non-force push 升级分支。

### 下游操作

- 私有 operator/ops 仓库继续使用已复制的 campaign workflow；新安装从
  `examples/github-actions/base-upgrade-campaign.yml` 复制。
- 将 `BASE_PLATFORM_REF` 固定到不可变 `base/v3.4.1`，以新的 campaign ID 对
  v3.3.2 下游派发 3.4.1。

### 验证

- `cd serve && .venv/bin/pytest tests/test_base_upgrade_dispatch.py -q`。
- 后端完整 pytest、Route Manifest、Alembic、前端 lint/build、release/database boundary、
  bootstrap plan、动态 Git E2E、shell syntax 与 `git diff --check`。

### 回滚

- 停止 operator workflow 或撤销 token；未合并 PR/本次新建分支按 receiver 资源事实回滚。
- 不移动 `base/v3.4.0`；本修复使用新的不可变 `base/v3.4.1` tag。

## [3.4.0] - 2026-08-18

### 变更范围

- 新增 GitHub fleet operator `dispatch`：按 registry/channel 发现下游真实
  Base 版本，预检查开放 PR/远端分支所有权，派发 receiver，按确切
  run ID 轮询并回收结果。
- 新增私有 operator evidence Draft-07 合同和人工批准的 campaign workflow
  模板；证据绑定实际 operator commit、run/artifact/result digest、时间与
  registry 身份。
- 强化 dispatch 状态机：响应丢失恢复、有界限流/超时取消、孤立分支
  两父 merge 证明、artifact 重定向/digest/ZIP/JSON 防护、失败隔离与集中
  脱敏。
- campaign 固定使用 GitHub REST `2026-03-10`；workflow dispatch 不发送
  已移除的 `return_run_details`，只接受返回 run locator 的 `200`。

### 兼容性（MINOR）

- HTTP API 与数据库 schema 不变，无数据库 migration。
- 下游必须先采用 `base/v3.3.2`；v3.3.1 补全 source runner 的 PR Manifest 合同，
  v3.3.2 修复其在真实外层 no-commit 同步中的嵌套 E2E 路径。
- v1 campaign 串行执行，每个 channel 最多 3 个 enabled 项目；超限在
  Provider 调用前停止，大 fleet 按 channel/多 campaign 分批。

### 迁移与下游操作

- 无数据库或运行时数据迁移。
- 在私有 operator/ops 仓库提交 `fleet/projects.json`，复制
  `.github/workflows/base-upgrade-campaign-example.yml`，配置指向不可变
  `base/v3.4.0` 的 `BASE_PLATFORM_REPOSITORY`/`BASE_PLATFORM_REF`。
- 只在 operator 仓库保存 `BASE_UPGRADE_GITHUB_TOKEN` 与真实 evidence；
  registry 使用稳定、非敏感 opaque `project_id`，run URL 不进入 Base。
- 由操作员人工触发 campaign；默认只创建/更新 Draft PR，不自动合并。

### 验证

- T0～T3 组合定向 322 passed；campaign/dispatch 95 passed；workflow
  23 passed。
- 后端完整 613 passed；Route Manifest 159 routes + 1 mount；Alembic upgrade
  head 通过。
- 动态 Git E2E 9/9；前端 `npm ci`/lint/build；release/database boundary、
  bootstrap plan、shell syntax 与 `git diff --check` 全部通过。

### 回滚

- 停用私有 operator campaign workflow 或撤销 operator Token 即停止新派发。
- 未合并的升级只需关闭本次创建的 Draft PR，并删除本次新推送的
  `chore/base-vTARGET` 分支；复用资源不自动删除。
- 已合并 v3.4.0 时 revert 整个同步 merge commit，并向
  `BASE_UPDATES.md` 追加回滚事实；不移动已发布 tag。

## [3.3.2] - 2026-08-18

### 变更范围

- 修复动态 Git E2E 在外层 `sync-base-release.sh` 的 no-commit merge 中误读调用方
  `.git/MERGE_HEAD` 的问题；所有子 fixture merge 状态都从各自 absolute Git dir 检查。
- 新增外层 merge marker 回归，证明 harness 不读取或修改调用方 merge 状态。

### 兼容性（PATCH）

- HTTP API、数据库 schema、receiver inputs/result、PR 行为均不变；无数据库 migration。
- 修复只影响验证 harness 的路径定位，使 v3.3.x 下游能在真实 no-commit 同步中完成全量 pytest。

### 下游同步

- v3.3.0/v3.3.1 下游执行 `./scripts/sync-base-release.sh 3.3.2 --install-deps`。
- 若先前 v3.3.1 尝试仍保留正确 `MERGE_HEAD`，可在同一工作区从目标 tree 执行
  `./scripts/sync-base-release.sh 3.3.2 --continue --install-deps`；已 abort 时从干净默认分支重试。

### 验证

- `cd serve && .venv/bin/pytest tests/test_base_upgrade_e2e.py -q`。
- `scripts/test-base-upgrade-e2e.sh`、`python3 scripts/check-base-release.py`、
  `python3 scripts/check-database-boundary.py`、`bash -n scripts/test-base-upgrade-e2e.sh`
  与 `git diff --check`。

### 回滚

- 未完成的 no-commit 同步使用 `git merge --abort`；已合并 v3.3.2 时 revert 整个同步
  merge commit，并向 `BASE_UPDATES.md` 追加事实。
- 不移动 `base/v3.3.0`/`base/v3.3.1`；本修复使用新的不可变 `base/v3.3.2` tag。

## [3.3.1] - 2026-08-18

### 变更范围

- 修复 receiver Draft PR 正文的跨版本变更计划：在现有 current/target、update
  nodes、验证证据、retry 和 rollback 之外，完整渲染每个选中 Release Manifest
  的 migrations、conflict hotspots、downstream actions 与 release verification。
- Manifest 文本通过严格 Markdown 中性化后写入 PR；换行、标题、链接、HTML、
  反引号与 shell-like 文本均不会变成 Markdown 语法或被执行。
- 新增回归测试，覆盖 3.1.0→3.3.0 跨版本合同的全部分节以及恶意 Manifest
  文本的惰性渲染。

### 兼容性（PATCH）

- HTTP API、数据库 schema、receiver dispatch inputs、result schema、分支与 PR 所有权
  规则不变；无数据库 migration。
- 该修复只增强 Draft PR 正文的审阅信息与文本边界，不改变下游同步结果。

### 迁移

- 无数据库 migration，无运行时数据迁移。

### 下游同步

- v3.3.0 下游执行 `./scripts/sync-base-release.sh 3.3.1 --install-deps`。
- 同步后重新派发原 campaign，receiver 将创建新 Draft PR 或幂等更新已有
  开放 PR 的正文；无需修改 dispatch payload。
- 冲突热点：`scripts/run-base-upgrade.sh`。

### 验证

- `cd serve && .venv/bin/pytest tests/test_base_upgrade_workflow.py -q`。
- `python3 scripts/check-base-release.py`、`python3 scripts/check-database-boundary.py`、
  `scripts/bootstrap-project.sh fixture_project --plan`、`bash -n scripts/run-base-upgrade.sh`
  与 `git diff --check`。

### 回滚

- 未合并的 receiver PR 按原所有权规则关闭/删除；本修复不改变该逻辑。
- 已合并 v3.3.1 时 revert 整个同步 merge commit，并在 `BASE_UPDATES.md`
  追加回滚事实；无数据库需要回滚。
- 已发布 `base/v3.3.0` tag 不移动；本修复使用新的不可变
  `base/v3.3.1` tag。

## [3.3.0] - 2026-08-18

### 变更范围

- 新增下游升级 registry、单项目 result 与 batch 三条 Draft-07 JSON Schema，以及
  `base-upgrade-campaign.py` 的 registry 校验、GitHub `PROJECT.md` 只读发现、跨版本
  计划和脱敏 JSON/Markdown 汇总；一个项目失败不会中止其他项目。
- campaign、ledger 与 receiver 复用 canonical core SemVer、Release Manifest 选择、
  strict `PROJECT.md` parser 和集中脱敏；Provider token、Authorization/Bearer、URL
  userinfo 与敏感赋值不会进入日志或 artifact。
- `PROJECT.md` 新增 canonical `BASE_UPSTREAM_REPOSITORY=OWNER/REPO`。bootstrap 与
  ledger 只从真实 GitHub remote 规范化写入；receiver 不接受 dispatch URL 覆盖，并
  以匿名 fetch 验证公开 Base upstream。
- 新增 `.github/workflows/base-upgrade-receiver.yml` 与
  `scripts/run-base-upgrade.sh`：fresh 默认分支 checkout、repository 级串行、默认拒绝
  跨 MAJOR、固定 `chore/base-vTARGET`、non-force push，以及成功后新建 Draft PR 或
  幂等更新已有开放 PR；默认分支从不直接写入。
- receiver 严格校验项目身份、两个下游账本、源/目标 tag、原子 merge parents 与已有
  分支/PR 所有权。冲突或验证失败先生成 schema-valid result/summary，再 abort，不
  push 半成品；输出复验结构关联并集中脱敏。
- checkout、toolchain setup、依赖安装或 runner 在合法输入下未产出 result 时，
  workflow 的 `if: always()` fallback 生成 schema-valid `dispatch_failed` 再上传；
  runner 同时精确核对账本 timestamp 与单行 verification evidence。
- PR 正文列出完整跨版本 update nodes、逐项 **PASS** 验证、retry 与 rollback。回滚
  只操作本次运行实际创建的分支/PR；复用的既有分支或 PR 永不自动删除。
- `sync-base-release.sh` 新增 `--install-deps`，在 merge 目标 tree 后安装 operator/
  backend requirements 与 `npm ci`，再执行 route、pytest、lint/build、database
  boundary 和 diff checks。新增动态 bare-repository E2E，覆盖 clean、幂等、dirty、
  缺 tag、祖先漂移、冲突、验证失败、首次依赖恢复及 secret 不泄漏。

### 兼容性（MINOR）

- HTTP API、数据库 schema 与运行配置不变；无数据库 migration。
- 现有下游可继续手工同步；自动 receiver 仅支持 GitHub.com，且 Base upstream 必须
  公开可读。启用 receiver 前，`PROJECT.md` 必须包含准确的
  `BASE_UPSTREAM_REPOSITORY`，并与 `BASE_UPDATES.md` 最后一条记录一致。
- `sync-base-release.sh TARGET_VERSION` 保持兼容；fresh checkout/CI 推荐显式增加
  `--install-deps`。降级始终拒绝，跨 MAJOR 必须显式 `allow_major=true`。

### 迁移

- 无数据库 migration。
- 无运行时数据迁移；v3.3.0 同步提交会从下游现有真实 `upstream` remote 发现并写入
  `BASE_UPSTREAM_REPOSITORY`，不得手工猜测或写入 credential-bearing URL。

### 下游同步

- 推荐源版本：`base/v3.3.0`。首次 receiver 接入必须同步正式 tag，禁止复制未发布
  workflow、runner 或脚本。
- v3.3.0 及以后标准更新：
  `./scripts/sync-base-release.sh TARGET_VERSION --install-deps`。
- v3.2.0 首次同步先运行 `./scripts/sync-base-release.sh 3.3.0`。若目标 tree 已 merge
  但验证因 fresh 环境缺依赖而停止，必须保留同一工作区的 `MERGE_HEAD`，再运行
  `./scripts/sync-base-release.sh 3.3.0 --continue --install-deps`；已 abort 或销毁的
  工作区从干净默认分支重来。
- 同步后确认 `PROJECT.md`/`BASE_UPDATES.md` 与 v3.3.0 commit 一致，再由下游自己的
  GitHub Actions 权限启用 receiver。中央 fleet dispatcher 仍属于后续版本。
- 冲突热点：`.github/workflows/`、`scripts/base-update-ledger.py`、
  `scripts/sync-base-release.sh`、`scripts/bootstrap-project.sh`、`PROJECT.md`、
  `BASE_UPDATES.md`、`serve/requirements-dev.txt`、`CLAUDE.md`、`UPSTREAM.md`。

### 验证

- T0～T2 组合定向测试：272 passed；T2 workflow/E2E/bootstrap/ledger 四文件：
  78 passed；动态 Git E2E：9/9 PASS；后端完整测试：563 passed。
- Route Manifest：159 routes、1 mount；Alembic upgrade、release check、database
  boundary、bootstrap plan、frontend lint/build、runner `bash -n` 与
  `git diff --check` 全部通过。
- GitHub Actions 使用官方 v7 release 的 immutable SHA；fallback result、严格账本
  evidence、PR body 与资源所有权 rollback 均有回归覆盖。

### 回滚

- receiver 本次未创建远端资源时删除 result/summary 即可；冲突/验证失败已 abort，
  不存在远端半成品。
- 本次新建但未合并的 PR 才自动关闭，本次新推的 `chore/base-vTARGET` 才自动删除；
  幂等复用的既有分支/PR 不自动删除，默认分支不受影响。
- 下游已合并 v3.3.0：revert 整个同步 merge commit，并向 `BASE_UPDATES.md` 追加回滚
  事实，不删除既有历史；无数据库需要回滚。
- 禁用 receiver workflow 即停止自动接收；已发布 `base/v3.3.0` tag 不移动，修正通过
  新 SemVer 发布。

## [3.2.0] - 2026-08-18

### 变更范围

- 新增 `releases/base-vX.Y.Z.json` 发布 Manifest；每个版本把变化拆成稳定更新
  node，逐项记录类型、范围、文件、兼容性、迁移、下游动作、冲突点、验证与回滚。
- 回填 v1.0.0、v2.0.0、v3.0.0、v3.1.0 Manifest；新发布缺 Manifest、节点为空、
  版本/日期与 CHANGELOG 不一致时，`check-base-release.py` 阻止发布。
- 新增 `scripts/base-update-ledger.py`：可在合并前聚合 CURRENT→TARGET 跨过的每个
  Base 版本，输出完整节点和两个 Base tag 之间的精确文件差异。
- 下游 `PROJECT.md` 现在记录准确 Base version/tag/commit、最后同步时间、历史账本
  位置、下次计划命令和更新命令；`BASE_UPDATES.md` 追加保存每次采用/更新详情。
- `scripts/sync-base-release.sh` 改为更新前打印计划，使用 `--no-commit` 合并，随后
  运行 route/pytest/lint/build/diff 验证，再将代码、`PROJECT.md` 和含 PASS 证据的
  `BASE_UPDATES.md` 原子提交为同一个 merge commit；`--continue` 可从冲突、验证或
  commit hook 失败恢复，且不会重复追加历史。
- bootstrap 自动建立两个下游账本；新增完整设计合同与 6 项账本/同步测试。

### 兼容性（MINOR）

- HTTP、schema 与运行配置不变；已有下游可以继续运行。
- v3.2.0 起下游同步合同新增两个必须提交的版本账本。v3.1.0 项目首次合并
  v3.2.0 后按 `serve/docs/base-update-ledger.md` 执行一次 ledger record/amend；
  后续版本由新 sync 脚本自动维护。

### 迁移

- 无数据库 migration。
- 账本迁移（v3.1.0 → v3.2.0）：
  ```bash
  python3 scripts/base-update-ledger.py record \
    --from 3.1.0 --to 3.2.0 --ref refs/tags/base/v3.2.0
  git add PROJECT.md BASE_UPDATES.md
  git commit --amend --no-edit
  ```

### 下游同步

- 推荐源版本：`base/v3.2.0`。
- 更新前方案：
  `python3 scripts/base-update-ledger.py plan --from CURRENT --to 3.2.0 --ref refs/tags/base/v3.2.0`。
- 标准更新：`./scripts/sync-base-release.sh 3.2.0`。
- 冲突热点：`scripts/sync-base-release.sh`、`scripts/bootstrap-project.sh`、
  `PROJECT.md`、`BASE_UPDATES.md`、`CHANGELOG.md`。

### 回滚

- 合并未提交：`git merge --abort`。
- 已提交：revert 整个同步 merge commit，并向 `BASE_UPDATES.md` 追加回滚事实，
  不删除已有历史。
- 无数据库变更；已发布 Base tag 不移动。

## [3.1.0] - 2026-08-18

### 变更范围

- 新增 `scripts/bootstrap-project.sh`：下游 Fork/Clone 一条命令完成项目标识、
  专属 PostgreSQL database/role、随机密码、ACL、环境文件、依赖安装、完整
  schema/migration、路由检查、后端测试和前端 lint/build。
- 新增共享 `scripts/lib/provision-postgres-database.sh`；Base 专属 provision 与
  下游 bootstrap 复用同一初始化内核，消除数据库安装/ACL/验收逻辑重复。
- bootstrap 强制要求 URL 不同的 Base upstream/project remotes，拒绝 Base 保留
  标识；未知非空数据库、owner 不匹配或迁移账本缺失时立即停止，不使用
  Base/其他项目数据库兜底。
- 自动生成 Git 忽略且 `0600` 的前后端 runtime env，以及不含密码、供下游提交的
  `PROJECT.md` 同步账本。
- 修正备份锁/任务锁测试对 `base:` Redis 前缀的硬编码，改为跟随
  `settings.APP_NAME`，确保下游项目名下全量测试仍成立。
- 新增 `serve/docs/project-bootstrap.md`、静态发布门禁与 4 项自动测试。

### 兼容性（MINOR）

- 新能力向后兼容；现有 Base 与下游数据库身份、HTTP API 和 schema 不变。
- 已存在的下游项目无需重新 bootstrap，继续按自己的环境执行同步和迁移。

### 迁移

- 无新增数据库 migration。
- 新项目：
  ```bash
  scripts/bootstrap-project.sh PROJECT_SLUG "Project Name"
  ```
- 仅预览派生身份：
  `scripts/bootstrap-project.sh PROJECT_SLUG "Project Name" --plan`。

### 下游同步

- 推荐源版本：`base/v3.1.0`。
- 冲突热点：`scripts/`、`README.md`、`CLAUDE.md`、`UPSTREAM.md`。
- 同步后继续使用下游现有专属数据库，运行完整 release/test/lint/build 验证。

### 回滚

- 已存在项目回滚只需撤销 bootstrap/common-library 文件，不改变数据库身份。
- 新项目首次初始化失败时，只删除该项目生成的 database/role、ignored env 和
  `PROJECT.md`；严禁操作 Base 或其他项目数据库。
- 已发布 Base tag 不移动；修正通过新的 SemVer 发布。

## [3.0.0] - 2026-08-18

### 变更范围

- Base 仓库本机数据库身份统一为固定的
  `base_platform_app@base_platform`；`app.config`、`.env.example`、运行时、
  SQL migration、Alembic 和健康检查不再使用共享 `base_user@base`。
- 新增 `scripts/provision-base-database.sh`：只允许创建/维护上述固定身份，强制
  `PUBLIC` 无 database CONNECT/schema 权限，专属角色无 superuser/createdb/
  createrole/replication/bypassrls 且不得授予其他普通角色。
- 新增 `scripts/check-database-boundary.py` 与测试，并接入发布门禁；默认配置、
  示例配置、建库脚本和数据库边界文档发生漂移时发布直接失败。
- 新增迁移 `028_1_normalize_legacy_menu_seeds.sql`，在新装库执行 029 菜单卫生
  迁移前，事务化清理 SEO/settings 的已知旧种子 ID，解决唯一 slug 冲突。
- 新增 `serve/docs/database-boundary.md`；AGENTS、CLAUDE、README、后端文档、
  UPSTREAM 和问题账本同步记录同一边界。

### 兼容性（MAJOR）

- 未显式配置数据库的运行环境，其默认目标由 `base_user@base` 改为
  `base_platform_app@base_platform`，属于运行配置不兼容变更，因此发布 MAJOR。
- 已正确使用项目专属 `DATABASE_*` 的下游运行时不受默认值变化影响。
- 下游不得使用 Base 专属 database/role，也不得运行 Base 本机 provision 脚本。

### 迁移

- Base 仓库本机：
  ```bash
  export BASE_PLATFORM_DB_PASSWORD="$(openssl rand -base64 36)"
  scripts/provision-base-database.sh
  python3 scripts/check-database-boundary.py
  ```
- 下游：同步前确认 `.env` 指向下游项目专属 database/role，再在该专属库执行
  `cd serve && .venv/bin/python -m app.migrate && .venv/bin/alembic upgrade head`。
- 新迁移只删除精确匹配旧 Base 种子的菜单 ID/slug 组合及其权限子项；使用事务，
  不匹配的下游菜单不受影响。

### 下游同步

- 推荐源版本：`base/v3.0.0`。
- 冲突热点：`serve/app/config.py`、`serve/.env.example`、
  `serve/databases/migrations/`、`scripts/check-base-release.py`、`UPSTREAM.md`。
- 同步后验证：
  ```bash
  python3 scripts/check-base-release.py
  cd serve && pytest
  cd ../admin && npm run lint && npm run build
  cd .. && git diff --check
  ```

### 回滚

- 应用代码可回滚到同步前提交，但不得把下游切到 Base 的 `base_platform`。
- 迁移回滚时从下游自己的备份恢复被清理的已知旧菜单种子；Base 的数据库身份和
  ACL 不回退到共享账户。
- 已发布 Base tag 不移动；修正通过新的 SemVer 发布。

## [2.0.0] - 2026-08-17

### 变更范围

- 引入集中式路由注册表（`serve/app/routes/`），`main.py` 仅保留
  `app.routes.register_routes(app)` 唯一入口。
- 新增 Laravel 风格 DSL：`RouteRegistry.group()` / `get/post/put/patch/delete/
  options/head/match/any/fallback/mount/crud` 与 `RouteBuilder` 链式元数据。
- 27 个 Controller 移除 `APIRouter`、路由装饰器与 `include_router()`，
  只保留未装饰 Handler；鉴权/权限策略移至 Route Manifest。
- 新增 `python -m app.routes list|check|json` CLI 全局路由目录。
- 新增编译阶段强制校验（重复路径、Route ID、operationId、fallback 遮蔽、
  access 边界、any()/CORS 保护、mount 重复等）。
- 新增 `current_auth` / `current_auth_optional`（`serve/app/deps.py`），
  鉴权策略由 Route middleware 写入 `request.state.auth`。
- 补齐测试环境直接依赖 `aiosqlite`，保证全量测试可在新建开发环境中离线运行。
- `serve/app/controllers/crud.py` 拆分 `CrudController`，`register_legacy_crud()`
  生成 5 契约端点，`controllers.base.crud_router` 保留兼容 re-export。

### 删除范围

- 删除 `controllers/admin/log.py` 与 `controllers/admin/dict.py`
  （其 CRUD 直接在 Manifest 中声明）。

### 兼容性（MAJOR）

- 拆除各 Controller 模块对外暴露的 `router` / `file_proxy_router` 对象，
  Python 集成面不兼容，本版本升级为 MAJOR SemVer。
- `controllers.base.crud_router` 保留为兼容层并发出 DeprecationWarning；
  移除兼容层属于 MAJOR 变更，不在本版本执行。
- HTTP/Method/path/operationId/tags/response schema 与 v1 完全一致
  （159 operations / 159 paths 零差异，见 `serve/tests/fixtures/route-catalog-v1.json`）。
- 路由契约快照 `route-catalog-v1.json` 作为不可变基线。

### 迁移

- 后端：`cd serve && pip install -r requirements-dev.txt && pytest`
  （本版本不包含 DB migration）。
- 前端：`cd admin && npm ci && npm run lint && npm run build`。
- 路由专项验证：
  ```bash
  cd serve
  .venv/bin/python -m app.routes check
  .venv/bin/python -m app.routes json > /tmp/base-routes.json
  ```
- 下游若直接引用 Controller 的 router 对象，需改为
  `app.routes.register_routes(app)` 或跟随 Base 的 Route Manifest。

### 下游同步

- 推荐源版本：`base/v2.0.0`。
- 冲突热点：`serve/app/main.py`、`serve/app/controllers/`、`serve/app/deps.py`、
  `serve/app/routes/`。
- 同步后验证：
  ```bash
  python3 scripts/check-base-release.py
  cd serve && pytest
  cd admin && npm run lint && npm run build
  git diff --check
  ```

### 回滚

- 切换前 Tag/提交作为回滚点；若新 Registry 实施失败，整体回退
  `main.py`、Controller、`deps.py` 与新 `routes/` 目录，不做半切换。
- 本重构不包含 DB migration，回滚不涉及数据修复。
- 已发布 Base Tag 不移动；回滚以新 SemVer 修复版本发布。

## [1.0.0] - 2026-08-17

### 变更范围

- 建立干净的、可复用的 Base Platform 基线。
- 明确本仓库只能作为通用基础项目维护；具体项目必须 Fork/Clone 后开发。
- 恢复通用 FastAPI 后端、Vue Admin、CRUD、RBAC、队列、存储、通知、SMS、SEO、导出和文件管理能力。

### 删除范围

- 删除所有 Polymarket V2 业务代码、迁移、测试、文档、页面、fixture、运行时和产品配置。
- 删除与 Base 无关的本地 PHP 项目和数据库备份。

### 兼容性与迁移

- 本版本不包含产品业务迁移。
- 下游项目必须先确认自己的工作树干净，再按 `UPSTREAM.md` 同步。
- 同步后执行后端测试、前端 lint/build 和 `git diff --check`。

### 下游同步

```bash
git fetch upstream --tags --prune
git merge --no-ff refs/tags/base/v1.0.0 -m "chore(sync): update Base to v1.0.0"
```

后续版本优先使用：

```bash
./scripts/sync-base-release.sh X.Y.Z
```

### 回滚

- 同步合并尚未推送时：回到同步前的分支或撤销同步 merge commit。
- 已推送后：在下游项目创建明确的回滚提交，不修改已发布的 Base tag。

## 后续版本条目模板

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added / Changed / Fixed
- TBD

### 兼容性
- TBD

### 迁移
- 命令：
- 顺序：
- 是否需要停机：

### 下游同步
- 推荐源版本：
- 冲突热点文件：
- 同步后验证：

### 回滚
- TBD
```
