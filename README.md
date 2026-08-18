# Base Platform

通用后台基础平台，供多个下游项目复用。

## 强制边界

**本仓库只维护通用基础能力。任何具体项目必须 FORK 或 CLONE 后，在自己的仓库中开发；禁止直接在本仓库上开发具体项目。**

具体项目的业务模型、业务接口、品牌、页面、第三方业务接入、提示词、数据、迁移、测试 fixture 和部署配置，都必须放在 fork/clone 后的项目仓库中。

## 数据库边界

本 Base checkout 的唯一 PostgreSQL 身份是
`base_platform_app@base_platform`，并通过数据库 ACL 隔离。任何下游项目都必须
使用自己的专属 database/role，严禁连接、迁移、清理或测试 `base_platform`。
完整合同见 `serve/docs/database-boundary.md`。

## 目录

- `serve/`：FastAPI + PostgreSQL + Redis 通用后端基础设施。
- `admin/`：Vue 3 + TypeScript + Element Plus 通用后台前端。
- `serve/docs/`：通用架构与能力文档。
- `VERSION`：当前 Base 发布版本。
- `CHANGELOG.md`：每个 Base 版本的变更账本。
- `UPSTREAM.md`：下游 Fork/Clone 项目的同步合同。
- `releases/base-vX.Y.Z.json`：每个版本逐节点、逐文件的机器更新清单。
- `serve/docs/base-update-ledger.md`：下游当前版本、更新计划和追加历史合同。

## 开发方式

```bash
git clone <BASE_REPOSITORY_URL> <PROJECT_DIRECTORY>
cd <PROJECT_DIRECTORY>
git remote rename origin upstream
git remote add origin <PROJECT_REPOSITORY_URL>
scripts/bootstrap-project.sh PROJECT_SLUG "Project Name"
```

该命令自动创建项目专属 database/role、环境文件、依赖、完整 schema/migration、
测试/build 与 `PROJECT.md` 账本。进入 fork/clone 后的项目目录再添加业务代码；
本仓库保持可被多个项目直接复用的纯基础状态。

## 必读文件

1. `AGENTS.md`
2. `CLAUDE.md`
3. `VERSION`、`CHANGELOG.md`、`UPSTREAM.md`
4. `serve/README.md` 或 `admin/README.md`
5. 与当前通用改动相关的 `serve/docs/` 文档
