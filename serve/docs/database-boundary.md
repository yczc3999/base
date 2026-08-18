# Base Platform 数据库边界合同

## 唯一身份

本 Base 仓库自身只使用以下 PostgreSQL 身份：

| 项 | 固定值 |
|---|---|
| Database | `base_platform` |
| Login/Owner | `base_platform_app` |
| Schema | `public` |
| Secret source | 仅本机、Git 忽略且权限为 `0600` 的 `serve/.env` |

`serve/app/config.py` 与 `serve/.env.example` 必须保持上述默认值。Base 的运行时、
迁移、健康检查和本地验收都指向同一身份，不使用 `base`、`base_verify`、
`base_user` 或任何下游项目数据库。

## PostgreSQL 强制隔离

`scripts/provision-base-database.sh` 固定且不接受 database/role 名称覆盖。它执行：

```sql
REVOKE ALL ON DATABASE base_platform FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE base_platform TO base_platform_app;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO base_platform_app;
```

- `base_platform_app` 为 `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS`，且不得授予任何其他普通角色。
- 数据库及其对象归 `base_platform_app`；普通角色不具备 `CONNECT`、角色继承或
  `SET ROLE` 路径。
- PostgreSQL superuser 仍保留数据库管理能力，这是 PostgreSQL 的管理边界，
  不属于应用或下游项目身份。
- 密码不进入 Git、日志、示例配置、命令输出或通用 settings。

## 下游项目硬规则

下游 Fork/Clone 严禁连接、迁移、清理、备份或测试 `base_platform`，也不得复用
`base_platform_app`。创建下游项目后，必须立即在下游自己的 `.env` 中设置独立的
`DATABASE_NAME`、`DATABASE_USER` 和随机密码，例如 `<project>_app@<project>`；
这些值只记录在下游仓库自己的部署账本中。

Base 发布只交付 schema/migration 代码。下游同步 Base tag 时，仅在下游项目专属
数据库执行迁移；不得把 Base 本机数据库作为共享库或验证库。

## 建库、验证与回滚

```bash
cd /code/base
export BASE_PLATFORM_DB_PASSWORD="$(openssl rand -base64 36)"
scripts/provision-base-database.sh
python3 scripts/check-database-boundary.py
```

脚本在空库中安装完整 Base schema；已存在库只轮换专属角色密码、重申 ACL、
验证 schema/migration 完整性，不覆盖数据。回滚应用版本不切换数据库身份；如需
恢复数据，只从 `base_platform` 的专属备份恢复到同名隔离库。

## 验收查询

```sql
SELECT datname, pg_get_userbyid(datdba), datacl
FROM pg_database WHERE datname = 'base_platform';

SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication,
       rolbypassrls
FROM pg_roles WHERE rolname = 'base_platform_app';
```

预期：owner 为 `base_platform_app`，ACL 不含 PUBLIC CONNECT，角色的五项特权均
为 false，且 `pg_auth_members` 中不存在授予 `base_platform_app` 的成员关系。
