"""
B1 数据库备份 — 保留策略纯函数 + do_backup (mock subprocess) + 文件管理

覆盖:
  1. _compute_retention_keep 保留策略 (7 天每日 + 4 周每周)
  2. do_backup 成功路径 (mock pg_dump → 记录 + 文件)
  3. do_backup 失败路径 (mock pg_dump 失败 → status=failed, 文件清理)
  4. do_delete 删记录同时删文件
  5. get_download 校验
  6. _verify_dump: pg_restore 缺失 → None (跳过校验)
"""

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.logics.db_backup as db_backup_mod
from app.config import settings
from app.models.base import Base
from app.models.db_backup import DbBackup
from app.logics.base import BizError


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[DbBackup.__table__])
        )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session


@pytest_asyncio.fixture
def backups_root(tmp_path, monkeypatch):
    """把备份目录指到临时目录"""
    root = tmp_path / "backups"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(db_backup_mod, "_BACKUPS_ROOT", str(root))
    return root


def _mock_pg_dump_success(monkeypatch, root):
    """mock subprocess.run: pg_dump 写文件, pg_restore 校验通过"""
    def fake_run(cmd, **kwargs):
        if cmd[0] == "pg_dump":
            out = cmd[cmd.index("--file") + 1]
            Path(out).write_bytes(b"FAKE_DUMP_DATA")
            return SimpleNamespace(returncode=0, stderr="", stdout="")
        if cmd[0] == "pg_restore":
            return SimpleNamespace(returncode=0, stderr="", stdout="")
        raise AssertionError(f"unexpected cmd: {cmd}")
    monkeypatch.setattr(subprocess, "run", fake_run)


def _mock_pg_dump_fail(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[0] == "pg_dump":
            return SimpleNamespace(returncode=1, stderr="connection refused", stdout="")
        return SimpleNamespace(returncode=0, stderr="", stdout="")
    monkeypatch.setattr(subprocess, "run", fake_run)


# ==================== 保留策略纯函数 ====================

def _mk_record(backup_id: int, created_at: datetime) -> DbBackup:
    return DbBackup(id=backup_id, filename=f"b{backup_id}.dump", status="ok", created_at=created_at)


def test_retention_keeps_all_recent():
    """7 天内全部保留"""
    now = datetime.now()
    records = [_mk_record(1, now - timedelta(hours=1)), _mk_record(2, now - timedelta(days=2))]
    keep = db_backup_mod._compute_retention_keep(records)
    assert keep == {1, 2}


def test_retention_keeps_weekly_newest():
    """旧备份: 各周窗口只留最新一条 (L2: 补齐 4 个周窗口)"""
    now = datetime.now()
    # 10 天前 (落在 7-14 天窗口): 两条, 应留较新的
    old_a = _mk_record(1, now - timedelta(days=10, hours=2))
    old_b = _mk_record(2, now - timedelta(days=10))
    # 20 天前 (落在 14-21 窗口)
    old_c = _mk_record(3, now - timedelta(days=20))
    # 40 天前 (超窗口, 应删)
    old_d = _mk_record(4, now - timedelta(days=40))
    # 30 天前 (落在 28-35 窗口, L2 修复后应保留)
    old_e = _mk_record(5, now - timedelta(days=30))

    keep = db_backup_mod._compute_retention_keep([old_a, old_b, old_c, old_d, old_e])
    # old_a 被同窗口更新的 old_b 顶掉; old_d 超期删除; old_e 第 4 窗口保留
    assert keep == {2, 3, 5}


def test_retention_empty():
    assert db_backup_mod._compute_retention_keep([]) == set()


# ==================== do_backup ====================

@pytest.mark.asyncio
async def test_do_backup_success(db, mock_redis, backups_root, monkeypatch):
    _mock_pg_dump_success(monkeypatch, backups_root)
    result = await db_backup_mod.db_backup_logic.do_backup(db)
    assert result["filename"].endswith(".dump")
    assert result["file_size"] > 0
    # 文件真实存在
    assert (backups_root / result["filename"]).exists()
    # DB 记录
    detail = await db_backup_mod.db_backup_logic.get_by_field(db, "filename", result["filename"])
    assert detail["status"] == "ok"


@pytest.mark.asyncio
async def test_do_backup_failure(db, mock_redis, backups_root, monkeypatch):
    _mock_pg_dump_fail(monkeypatch)
    with pytest.raises(BizError):
        await db_backup_mod.db_backup_logic.do_backup(db)
    # 失败记录保留, status=failed; S5 修复: error_msg 用通用消息不泄露 stderr 细节
    from sqlalchemy import select
    records = (await db.execute(select(DbBackup))).scalars().all()
    assert len(records) == 1
    assert records[0].status == "failed"
    assert "connection refused" not in (records[0].error_msg or "")
    assert "服务端日志" in (records[0].error_msg or "")
    # 失败文件被清理
    assert list(backups_root.iterdir()) == []


@pytest.mark.asyncio
async def test_do_backup_lock_prevents_concurrent(db, mock_redis, backups_root, monkeypatch):
    """Redis 锁被占用时拒绝再次备份"""
    _mock_pg_dump_success(monkeypatch, backups_root)
    lock_key = f"{settings.APP_NAME}:backup:lock"
    await mock_redis.set(lock_key, "1", ex=3600, nx=True)
    with pytest.raises(BizError, match="已有备份任务"):
        await db_backup_mod.db_backup_logic.do_backup(db)


# ==================== 文件管理 ====================

@pytest.mark.asyncio
async def test_do_delete_removes_file_and_record(db, mock_redis, backups_root, monkeypatch):
    _mock_pg_dump_success(monkeypatch, backups_root)
    result = await db_backup_mod.db_backup_logic.do_backup(db)
    file_path = backups_root / result["filename"]
    assert file_path.exists()

    detail = await db_backup_mod.db_backup_logic.get_by_field(db, "filename", result["filename"])
    await db_backup_mod.db_backup_logic.do_delete(db, [detail["id"]])
    # 记录删除 + 文件删除
    assert not file_path.exists()
    from sqlalchemy import select, func
    count = (await db.execute(select(func.count()).select_from(DbBackup))).scalar()
    assert count == 0


@pytest.mark.asyncio
async def test_get_download_validates(db, mock_redis, backups_root, monkeypatch):
    _mock_pg_dump_success(monkeypatch, backups_root)
    result = await db_backup_mod.db_backup_logic.do_backup(db)
    path = await db_backup_mod.db_backup_logic.get_download(db, result["filename"])
    assert Path(path).exists()
    # 不存在的文件名 → BizError
    with pytest.raises(BizError):
        await db_backup_mod.db_backup_logic.get_download(db, "nope.dump")


# ==================== 修复回归测试 ====================

def test_db_backup_readonly():
    """S2 修复: 备份记录只读, 手动 create/edit 被拒"""
    logic = db_backup_mod.db_backup_logic
    with pytest.raises(BizError):
        logic.before_create({"filename": "fake.dump"})
    with pytest.raises(BizError):
        logic.before_edit({"filename": "fake.dump"})


@pytest.mark.asyncio
async def test_apply_retention_cleans_old_failed(db, mock_redis):
    """L3 修复: 失败记录保留 7 天后删除"""
    old_failed = DbBackup(filename="fail_old.dump", status="failed",
                          created_at=datetime.now() - timedelta(days=30))
    new_failed = DbBackup(filename="fail_new.dump", status="failed",
                          created_at=datetime.now() - timedelta(days=2))
    db.add_all([old_failed, new_failed])
    await db.commit()

    await db_backup_mod.db_backup_logic._apply_retention(db)

    from sqlalchemy import select
    remaining = (await db.execute(select(DbBackup))).scalars().all()
    assert [b.filename for b in remaining] == ["fail_new.dump"]


@pytest.mark.asyncio
async def test_backup_lock_released_after_success(db, mock_redis, backups_root, monkeypatch):
    """L4 修复: 备份完成后锁释放, 可立即再次触发"""
    _mock_pg_dump_success(monkeypatch, backups_root)
    await db_backup_mod.db_backup_logic.do_backup(db)
    # 锁已释放
    lock_key = f"{settings.APP_NAME}:backup:lock"
    assert await mock_redis.get(lock_key) is None
    # 可立即再次备份
    result = await db_backup_mod.db_backup_logic.do_backup(db)
    assert result["filename"]


# ==================== 校验 ====================

def test_verify_dump_missing_restore(monkeypatch):
    """pg_restore 未安装 → 返回 None (跳过校验, 不阻断)"""
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("pg_restore not found")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert db_backup_mod.db_backup_logic._verify_dump("/tmp/x.dump") is None


def test_verify_dump_corrupt(monkeypatch):
    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stderr="", stdout="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert db_backup_mod.db_backup_logic._verify_dump("/tmp/x.dump") is False
