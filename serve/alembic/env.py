"""V2 Alembic 迁移执行器（WP-01A-00）。

V2 的 DDL 唯一入口。在既有 Base 双迁移系统上（Alembic = schema 演进 /
``python -m app.migrate`` = 种子+菜单）补齐 V2 需要的执行语义：

- schema-aware：``include_schemas`` + ``include_name``/``include_object`` allowlist。
  ``trading`` schema 全域放行；``public`` 只放行 ``alembic_version``；18 张 Base 表由
  ``v2_0001`` revision validator 主动检查兼容合同，其他 schema / 未知 public 表一律
  忽略（autogenerate 不得把它们判为应删除，也不得反射 Base 表）。
- 显式 ``search_path``：``SET LOCAL search_path TO public, pg_catalog``，V2 表一律
  schema-qualified，不依赖隐式搜索顺序。
- 迁移锁：固定 key 的 PostgreSQL transaction advisory lock；一次 Alembic run 使用一个
  外层事务，失败整体回滚。
- 连接所有权：优先复用 ``config.attributes["connection"]`` 注入的既有 sync Connection
  （不 close / 不 dispose）；未注入时才从 typed settings 构造 ``NullPool`` engine，
  成功/异常路径均 dispose 一次。
- 硬预置：dialect 必须为 PostgreSQL，否则以固定 reason code 终止；异常不记录
  DSN / password / Provider message。密码只用于 engine URL，禁止写 log / exception /
  offline SQL / manifest。

模型只管理 ``trading`` schema；``Base.metadata`` 只管理已声明的 ``public`` 表
（legacy ``cdabba1e3903`` no-op baseline 不改写、不重签）。
"""

from logging.config import fileConfig
from urllib.parse import quote_plus

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.config import settings
from app.models import Base

# 这是 Alembic 的 Config 对象，提供 .ini 中访问的值。
config = context.config

# 解释配置文件进行 Python 日志配置；config_file_name 为 None（程序化注入）时跳过。
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---- V2 执行基础（WP-01A-00）----

TRADING_SCHEMA = "trading"
PUBLIC_SCHEMA = "public"
VERSION_TABLE = "alembic_version"
# PostgreSQL transaction advisory lock 固定 key（决策 §2.5）。64 位有符号 bigint 内。
ADVISORY_LOCK_KEY = 5786375870084826445

ALLOWED_SCHEMAS = frozenset({TRADING_SCHEMA, PUBLIC_SCHEMA})
# public 仅允许 alembic_version（由 Alembic 自身管理）。18 张 Base 表由
# ``v2_0001`` revision 的 validator（``app.base_schema_contract``）主动检查兼容合同，
# 不进入 autogenerate 的 create/drop/alter 候选、也不得被反射；未知 public 表同样排除。
PUBLIC_ALLOWED_TABLES = frozenset({VERSION_TABLE})

# 非 PostgreSQL dialect 的固定 reason code：异常 message 不含 DSN/password/Provider 原文。
MIGRATION_DIALECT_REASON = "v2_migration_requires_postgresql_dialect"
MIGRATION_ACTIVE_TX_REASON = "v2_migration_requires_clean_connection"


def _build_sync_url() -> str:
    """同步驱动 URL（psycopg 3），与 app/config.py 同源 env 变量构建。

    密码经 URL 编码，只用于 engine URL；不得写入 log / exception / offline SQL / manifest。
    """
    password = quote_plus(settings.DATABASE_PASSWORD)
    return (
        f"postgresql+psycopg://{settings.DATABASE_USER}:{password}"
        f"@{settings.DATABASE_HOST}:{settings.DATABASE_PORT}/{settings.DATABASE_NAME}"
    )


def _require_postgresql(dialect_name: str) -> None:
    """硬预置：dialect 必须为 PostgreSQL，否则以固定 reason code 终止。

    异常 message 只含固定 code，不含 DSN / password / Provider message。
    """
    if dialect_name != "postgresql":
        raise RuntimeError(MIGRATION_DIALECT_REASON)


def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    """schema / table allowlist（纯函数，可直接单测）。

    - schema：仅 ``trading`` / ``public`` 放行。
    - table：``trading`` schema 全域放行；``public`` 仅放行 ``Base.metadata`` 已声明表
      与 ``alembic_version``；其他 schema / 未知 public 表忽略。
    - 其余对象类型（column / index / constraint / type）在已放行表内不再过滤。
    """
    if type_ == "schema":
        # Alembic 用 None 表示 dialect.default_schema_name（PostgreSQL 默认 public）。
        return name is None or name in ALLOWED_SCHEMAS
    if type_ == "table":
        schema = (parent_names or {}).get("schema_name") or PUBLIC_SCHEMA
        if schema == TRADING_SCHEMA:
            return True
        if schema == PUBLIC_SCHEMA:
            return name in PUBLIC_ALLOWED_TABLES
        return False
    return True


def include_object(object_, name, type_, reflected, compare_to) -> bool:
    """与 ``include_name`` 保持一致的 table 过滤；非 table 对象放行。"""
    if type_ == "table":
        schema = getattr(object_, "schema", None) or PUBLIC_SCHEMA
        return include_name(name, "table", {"schema_name": schema})
    return True


# online / offline 共用的 configure 参数（任务 §5.1，两模式必须完全一致）。
# allowlist 回调必须真正传入 Alembic，只定义函数不会生效。
#
# ``version_table_schema`` 必须为 ``None``（default schema）而非 ``"public"``：
# ``include_schemas=True`` 时 Alembic autogenerate 用 ``None`` 表示数据库 default schema
# （schema.py 把 ``public`` discard 后 ``add(None)``），tables.py 仅当
# ``schema_name == version_table_schema`` 时才把版本表从反射集中排除。若写成 ``"public"``，
# ``alembic check`` / ``revision --autogenerate`` 会把 ``alembic_version`` 误报为待删除表。
# 物理位置不变：search_path=public, pg_catalog 下版本表仍落在 public。
COMMON_CONFIGURE_KWARGS = {
    "target_metadata": Base.metadata,
    "include_schemas": True,
    "compare_type": True,
    "compare_server_default": True,
    "version_table": VERSION_TABLE,
    "version_table_schema": None,
    "transaction_per_migration": False,
    "include_name": include_name,
    "include_object": include_object,
}


def _create_nullpool_engine():
    """从 typed settings 构造 NullPool engine（无连接池，迁移进程一次性使用）。"""
    return create_engine(_build_sync_url(), poolclass=pool.NullPool)


def _begin_online_transaction(connection):
    """外层事务由本次 migration 唯一拥有。

    注入连接若已有事务则 fail-closed；不得用 ``with existing`` 偷偷 commit/
    rollback 调用方的事务，也不得把 transaction advisory lock 遗留给调用方。"""
    if connection.get_transaction() is not None:
        raise RuntimeError(MIGRATION_ACTIVE_TX_REASON)
    return connection.begin()


def _run_online(connection) -> None:
    """在一个外层事务内：search_path → lock_timeout → advisory lock → migrations。

    任何 migration 异常保持原类型传播并整体 rollback（``with`` 退出时回滚）。
    """
    _require_postgresql(connection.dialect.name)
    with _begin_online_transaction(connection):
        connection.execute(text("SET LOCAL search_path TO public, pg_catalog"))
        connection.execute(text("SET LOCAL lock_timeout TO '30s'"))
        connection.execute(text(f"SELECT pg_advisory_xact_lock({ADVISORY_LOCK_KEY})"))
        context.configure(connection=connection, **COMMON_CONFIGURE_KWARGS)
        context.run_migrations()


def run_migrations_online() -> None:
    """online 模式：优先复用注入连接（不 close / 不 dispose），否则自建并 dispose。"""
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_online(connection)
    else:
        engine = _create_nullpool_engine()
        try:
            with engine.connect() as conn:
                _run_online(conn)
        finally:
            engine.dispose()


def run_migrations_offline() -> None:
    """offline 模式：不连网、不实际获锁，但输出必须包含显式 search path、transaction
    boundary 与 advisory-lock SQL，保持与 online 语义一致；password 不写入生成 SQL。"""
    context.configure(
        url=_build_sync_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **COMMON_CONFIGURE_KWARGS,
    )
    _require_postgresql(context.get_context().dialect.name)
    with context.begin_transaction():
        context.execute(text("SET LOCAL search_path TO public, pg_catalog"))
        context.execute(text("SET LOCAL lock_timeout TO '30s'"))
        context.execute(text(f"SELECT pg_advisory_xact_lock({ADVISORY_LOCK_KEY})"))
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
