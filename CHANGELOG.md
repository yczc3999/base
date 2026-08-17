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
