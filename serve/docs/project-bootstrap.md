# 下游项目一键初始化合同

## 目标

Fork/Clone 完成后，用一条命令生成项目专属运行身份、安装依赖、初始化数据库、
执行迁移和完整验证。整个过程不连接、不授权、不迁移 Base 的
`base_platform_app@base_platform`。

## 前置条件

- Python 3、Node.js/npm、OpenSSL、PostgreSQL client 已安装；
- 本机 PostgreSQL 正在运行；当前用户可通过 `sudo -n -u postgres` 管理本机实例；
- 仓库已按 `CLAUDE.md` 将 Base remote 命名为 `upstream`，并配置不同 URL 的
  项目 `origin`（或 `project`）remote。任一缺失或两者相同时脚本立即停止，
  以防在 Base 原仓库直接创建产品项目。

## 一条命令

```bash
scripts/bootstrap-project.sh PROJECT_SLUG "Project Name"
```

`PROJECT_SLUG` 只允许小写字母、数字和下划线，必须以字母开头，长度 2–31。
例如 `acme_portal` 生成：

| 项 | 值 |
|---|---|
| Database | `acme_portal` |
| PostgreSQL role | `acme_portal_app` |
| Backend app name | `acme_portal` |
| Admin title | `Project Name` |

`base`、`base_platform`、`base_platform_app` 为保留标识，任何下游初始化都会拒绝。

## 自动执行顺序

1. 验证项目标识，以及互不相同的 Base upstream/project remote；
2. 创建 `serve/.venv`，安装 `requirements-dev.txt`；
3. 在 `admin/` 执行 `npm ci`；
4. 生成随机数据库密码，创建项目专属 database/role；
5. 撤销 PUBLIC 和其他普通角色权限，只保留项目 role；
6. 安装完整 Base schema，运行 SQL migration 与 Alembic；
7. 写入 Git 忽略且 `0600` 的 `serve/.env`、`admin/.env`；
8. 执行 route check、后端全量测试、前端 lint/build；
9. 验证通过后生成不含密码、应提交到下游仓库的 `PROJECT.md` 当前版本账本，
   以及包含验证结果的 append-only `BASE_UPDATES.md` 采用历史。

密码不打印、不进入进程参数、不写入 `PROJECT.md` 或 Git 跟踪文件。

## 预览与 CI

```bash
scripts/bootstrap-project.sh acme_portal "Acme Portal" --plan
```

`--plan` 只显示派生 database/role，不创建文件或数据库。`BOOTSTRAP_SKIP_INSTALL=1`
和 `BOOTSTRAP_SKIP_CHECKS=1` 只供已有依赖的 CI/fixture 使用；正常首次初始化不要设置。

## 幂等与停止条件

- 空数据库安装完整 schema；已有且 owner/迁移账本匹配的项目库只轮换密码、重申
  ACL 并执行待处理迁移。
- 已有非空库若缺少 `schema_migrations`，立即停止，不收编未知数据库。
- 已有数据库 owner 不匹配项目 role，立即停止。
- 初始化失败不切换到 Base 数据库，也不使用其他项目数据库兜底。

## 回滚

首次初始化失败时，只删除本项目生成的 `<project>` database、`<project>_app`
role、`serve/.env` 和 `admin/.env`。`PROJECT.md`、`BASE_UPDATES.md` 仅在确认这是
失败的首次初始化且尚未提交时删除。不得删除或修改 `base_platform`、其他项目
数据库或 Base 发布账本。
