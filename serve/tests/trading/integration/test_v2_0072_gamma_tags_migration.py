"""WP tag catalog —— 0072 Gamma tags migration（真 PostgreSQL）。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V71 = "b1000071"
V72 = "b1000072"


def _run(cmd, revision, db_url):
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    engine = create_engine(db_url, poolclass=NullPool)
    conn = engine.connect()
    cfg.attributes["connection"] = conn
    try:
        cmd(cfg, revision)
    finally:
        conn.close()
        engine.dispose()


def _query(db_url, sql, params=None):
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).fetchall()
    finally:
        engine.dispose()


def test_0072_creates_tag_tables_and_roundtrips(temp_pg_db):
    url = temp_pg_db.url
    _run(command.upgrade, V72, url)
    tables = {
        row[0]
        for row in _query(
            url,
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname='trading' AND tablename IN ('pm_tags','pm_event_tags')",
        )
    }
    assert tables == {"pm_tags", "pm_event_tags"}
    cols = {
        row[0]
        for row in _query(
            url,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='trading' AND table_name='pm_tags'",
        )
    }
    assert "disposition" in cols
    _run(command.downgrade, V71, url)
    leftover = _query(
        url,
        "SELECT tablename FROM pg_tables "
        "WHERE schemaname='trading' AND tablename IN ('pm_tags','pm_event_tags')",
    )
    assert leftover == []
    _run(command.upgrade, V72, url)
