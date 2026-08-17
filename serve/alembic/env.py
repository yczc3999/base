from logging.config import fileConfig
from urllib.parse import quote_plus

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---- Base Platform 集成 ----
# Alembic 只负责 schema 演进；种子数据 + 菜单/权限等业务迁移仍走
# `python -m app.migrate`（databases/migrations/*.sql + schema_migrations 表）。
# 两者互不干扰：Alembic 用 alembic_version 表，migrate.py 用 schema_migrations 表。

# 同步驱动 URL：应用用 asyncpg（postgresql+asyncpg://），Alembic 需要同步驱动。
# 这里用 psycopg 3（postgresql+psycopg://），从与 app/config.py 相同的 env 变量构建，
# 确保 alembic 与运行时连的是同一个库。
from app.config import settings
from app.models import Base

_sync_url = (
    f"postgresql+psycopg://{settings.DATABASE_USER}:{quote_plus(settings.DATABASE_PASSWORD)}"
    f"@{settings.DATABASE_HOST}:{settings.DATABASE_PORT}/{settings.DATABASE_NAME}"
)
# 注：不用 config.set_main_option() 写回 ini —— 密码可能含 %，configparser 插值会报错。
# 改为在 run_migrations_* 里直接把 _sync_url 传给引擎。

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = _sync_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        url=_sync_url,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
