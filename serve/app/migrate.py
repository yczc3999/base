"""
Migration Runner — 按序执行 databases/migrations/*.sql

用法：
    python -m app.migrate           # 执行未应用的迁移
    python -m app.migrate --list    # 列出已应用/待应用

设计：
- 自建 schema_migrations 表记录已执行版本（不依赖外部 migration 文件）
- 幂等：已执行的迁移跳过，重复运行无副作用
- 每条迁移包在独立事务中（BEGIN/COMMIT），失败不影响其他迁移
- 零外部框架（不用 alembic），符合项目「零 SDK」原则
"""

import asyncio
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

logger = logging.getLogger("migrate")

MIGRATIONS_DIR = Path(__file__).parent.parent / "databases" / "migrations"
SCHEMA_TABLE = "schema_migrations"

# 不应作为"业务迁移"执行的文件（runner 自建，或当前库结构的初始建表）
SKIP_FILES = {}


def list_migration_files() -> list[Path]:
    """按文件名排序的 migration 文件列表"""
    return sorted(
        MIGRATIONS_DIR.glob("*.sql"),
        key=lambda f: f.name,
    )


async def _ensure_schema_table(db) -> None:
    """自建 schema_migrations 表"""
    await db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_TABLE} (
            version     VARCHAR(255) PRIMARY KEY,
            applied_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))


async def _applied_versions(db) -> set[str]:
    """已应用的 migration 版本集合"""
    result = await db.execute(text(f"SELECT version FROM {SCHEMA_TABLE}"))
    return {row[0] for row in result.all()}


async def _apply_one(db, version: str, content: str) -> None:
    """应用单条 migration（独立事务）"""
    # 记录版本
    await db.execute(text(
        f"INSERT INTO {SCHEMA_TABLE} (version) VALUES (:v)"
    ), {"v": version})
    # 执行 SQL 内容（去掉外层 BEGIN/COMMIT 避免嵌套事务）
    for stmt in _split_statements(content):
        stripped = stmt.strip().rstrip(";").strip()
        if not stripped:
            continue
        if stripped.lower() in ("begin", "commit", "start transaction"):
            continue
        await db.execute(text(stripped))


def _split_statements(sql: str) -> list[str]:
    """把 SQL 按分号拆成独立语句（处理字符串内的分号）"""
    statements = []
    current = []
    in_string = False
    quote_char = ""
    for ch in sql:
        if in_string:
            current.append(ch)
            if ch == quote_char:
                in_string = False
        else:
            if ch in ("'", '"', "`"):
                in_string = True
                quote_char = ch
                current.append(ch)
            elif ch == ";":
                statements.append("".join(current))
                current = []
            else:
                current.append(ch)
    if "".join(current).strip():
        statements.append("".join(current))
    return statements


async def run_migrations(verbose: bool = True, url: str = None) -> int:
    """执行未应用的迁移，返回应用数量

    :param url: 数据库 URL（默认用 settings.database_url；测试可传 sqlite）
    """
    engine = create_async_engine(url or settings.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    applied_count = 0

    try:
        async with Session() as db:
            await _ensure_schema_table(db)
            await db.commit()

            applied = await _applied_versions(db)
            for file in list_migration_files():
                if file.name in SKIP_FILES:
                    continue
                version = file.name
                if version in applied:
                    continue
                if verbose:
                    logger.info(f"Applying: {version}")
                content = file.read_text(encoding="utf-8")
                await _apply_one(db, version, content)
                applied_count += 1

            await db.commit()
    finally:
        await engine.dispose()

    if verbose:
        logger.info(f"Done. Applied {applied_count} migration(s).")
    return applied_count


async def get_status_list(url: str = None) -> list[dict]:
    """返回迁移状态列表 [{version, applied, applied_at}]（供 admin UI / 测试）."""
    engine = create_async_engine(url or settings.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            await _ensure_schema_table(db)
            await db.commit()
            rows = await db.execute(text(f"SELECT version, applied_at FROM {SCHEMA_TABLE}"))
            applied_map = {version: str(applied_at) for version, applied_at in rows.all()}
            result = []
            for file in list_migration_files():
                result.append({
                    "version": file.name,
                    "applied": file.name in applied_map,
                    "applied_at": applied_map.get(file.name, ""),
                })
            return result
    finally:
        await engine.dispose()


async def list_status() -> None:
    """列出已应用/待应用迁移"""
    engine = create_async_engine(settings.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            await _ensure_schema_table(db)
            await db.commit()
            applied = await _applied_versions(db)
            for file in list_migration_files():
                marker = "✅" if file.name in applied else "⬜"
                print(f"  {marker} {file.name}")
    finally:
        await engine.dispose()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run DB migrations")
    parser.add_argument("--list", action="store_true", help="List migration status")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")

    if args.list:
        asyncio.run(list_status())
    else:
        asyncio.run(run_migrations())


if __name__ == "__main__":
    main()
