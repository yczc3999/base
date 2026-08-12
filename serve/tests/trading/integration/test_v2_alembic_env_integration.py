"""
WP-01A-00 Alembic 执行基础 —— 真 PostgreSQL 集成验收（任务 §6.2）。

前置：`V2_TEST_ADMIN_DATABASE_URL`（默认 `postgresql+psycopg:///postgres`）存在，
否则整模块 skip。fixture 在独立 `pm_v2_test_*` 临时库上执行，从不在管理/业务库跑
downgrade；本模块绝不修改仓库 revision 文件（故意失败 revision 用临时 script location）。

覆盖：
1. `upgrade head → downgrade base → upgrade head`，每步 revision 正确，
   `public.alembic_version` 且无其他 version table；
2. 两个并发 migration 在同一临时库被 advisory lock 串行化：无双执行、无残留锁；
3. 故意 revision 异常整体回滚且 version 不前进（临时 script location 注入）。
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
BASELINE_REVISION = "cdabba1e3903"
# WP-02 加入 b1000021 / WP-04 加入 b1000040 / b1000051 后 head 前进；探针 revision
# 须挂在 head 之下，否则形成多 head 使 `upgrade head` 歧义。
HEAD_REVISION = "b1000071"

# 并发探针 revision：在持锁 migration 内制造稳定重叠窗口。无全局 advisory
# lock 时两进程会同时看到 baseline，其中一个必然在 CREATE TABLE 冲突；有锁时
# 第二个进程只在第一个 commit 后读到新 head 并 no-op。
LOCK_PROBE_REVISION = "c0000001"
LOCK_PROBE_REVISION_SRC = '''"""advisory lock serialization probe"""
from alembic import op

revision = "c0000001"
down_revision = "b1000071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SELECT pg_sleep(0.75)")
    op.execute("CREATE TABLE migration_lock_probe (id integer PRIMARY KEY)")
    op.execute("INSERT INTO migration_lock_probe(id) VALUES (1)")


def downgrade() -> None:
    op.execute("DROP TABLE migration_lock_probe")
'''

# 整次 run rollback 探针：先让一个 revision 成功，再由后续 revision 失败。
# 只有外层单事务才会把前一 revision 的 DDL/版本推进也一并撤销。
SUCCESS_REVISION_SRC = '''"""successful first half of whole-run rollback probe"""
from alembic import op

revision = "f0000001"
down_revision = "b1000071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE run_success_table (id integer)")


def downgrade() -> None:
    op.execute("DROP TABLE run_success_table")
'''

FAILING_REVISION_SRC = '''"""failing second half of whole-run rollback probe"""
from alembic import op

revision = "f0000002"
down_revision = "f0000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE partial_created_table (id integer)")
    raise RuntimeError("intentional-failure")


def downgrade() -> None:
    pass
'''


def _run(cmd, revision, db_url, script_location=None):
    """以注入 connection 的方式执行 alembic 命令（复用真实 env.py 的 connection 语义）。"""
    cfg = Config()
    cfg.set_main_option("script_location", str(script_location or ALEMBIC_DIR))
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
        with engine.connect() as c:
            return c.execute(text(sql), params or {}).fetchall()
    finally:
        engine.dispose()


def _version_rows(db_url):
    return _query(db_url, "SELECT version_num FROM public.alembic_version")


def _non_system_tables(db_url):
    return _query(
        db_url,
        "SELECT schemaname||'.'||tablename FROM pg_tables "
        "WHERE schemaname NOT IN ('pg_catalog', 'information_schema') ORDER BY 1",
    )


def _temporary_script(script_dir: Path, revisions: dict[str, str]) -> Path:
    """复制真 env/baseline 到临时 script location，附加仅本测试使用的 revision。"""
    (script_dir / "versions").mkdir(parents=True)
    shutil.copy(ALEMBIC_DIR / "env.py", script_dir / "env.py")
    for v in (ALEMBIC_DIR / "versions").glob("*.py"):
        shutil.copy(v, script_dir / "versions" / v.name)
    for filename, source in revisions.items():
        (script_dir / "versions" / filename).write_text(source)
    return script_dir


# ---------------- 1. upgrade → downgrade → upgrade ----------------

def test_upgrade_downgrade_upgrade_roundtrip(temp_pg_db):
    url = temp_pg_db.url

    # 测试库 URL 只能替换 database，不得丢掉管理连接的 origin/身份/查询参数。
    admin = make_url(os.environ["V2_TEST_ADMIN_DATABASE_URL"])
    target = make_url(url)
    assert target.drivername == admin.drivername
    assert target.username == admin.username
    assert target.password == admin.password
    assert target.host == admin.host
    assert target.port == admin.port
    assert target.query == admin.query
    assert target.database == temp_pg_db.name

    _run(command.upgrade, "head", url)
    assert _version_rows(url) == [(HEAD_REVISION,)]
    # public.alembic_version 且无其他 version table；public 用户表仅 alembic_version
    # （v2_0002 引入 trading schema，不属 public）
    vts = _query(
        url,
        "SELECT table_schema||'.'||table_name FROM information_schema.tables "
        "WHERE table_name LIKE '%version%' "
        "AND table_schema = 'public'",
    )
    assert vts == [("public.alembic_version",)]
    public_tables = [r[0] for r in _non_system_tables(url) if r[0].startswith("public.")]
    assert public_tables == ["public.alembic_version"]

    _run(command.downgrade, "base", url)
    # alembic 保留 version 表但清空行（online 模式不 DROP version 表）
    assert _query(url, "SELECT count(*) FROM public.alembic_version") == [(0,)]

    _run(command.upgrade, "head", url)
    assert _version_rows(url) == [(HEAD_REVISION,)]


# ---------------- 2. 并发 migration 被 advisory lock 串行化 ----------------

# alembic 的 `alembic.context` proxy 是进程级共享状态，线程并发跑 env.py 会互相覆盖
# （`_remove_proxy` KeyError）。并发迁移必须以独立进程呈现——与生产迁移形态一致。
_CONCURRENT_RUNNER = '''\
import sys, time, pathlib
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

url, script_location, ready, go = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
pathlib.Path(ready).touch()
deadline = time.time() + 30
while not pathlib.Path(go).exists():
    if time.time() > deadline:
        raise SystemExit("barrier timeout")
    time.sleep(0.005)
cfg = Config()
cfg.set_main_option("script_location", script_location)
engine = create_engine(url, poolclass=NullPool)
conn = engine.connect()
cfg.attributes["connection"] = conn
try:
    command.upgrade(cfg, "head")
finally:
    conn.close()
    engine.dispose()
'''


def _spawn_upgrade(db_url, ready, go, script_location=ALEMBIC_DIR):
    return subprocess.Popen(
        [sys.executable, "-c", _CONCURRENT_RUNNER, db_url, str(script_location), ready, go],
        cwd=str(SERVE_DIR),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_concurrent_upgrades_serialized_by_advisory_lock(temp_pg_db, tmp_path):
    """两个独立进程同时执行非 no-op revision；无锁时本 fixture 必然冲突。"""
    url = temp_pg_db.url
    _run(command.upgrade, "head", url)
    assert _version_rows(url) == [(HEAD_REVISION,)]

    script_dir = _temporary_script(
        tmp_path / "lock_alembic",
        {"c0000001_lock_probe.py": LOCK_PROBE_REVISION_SRC},
    )
    r1, r2 = str(tmp_path / "r1"), str(tmp_path / "r2")
    go = str(tmp_path / "go")
    p1 = _spawn_upgrade(url, r1, go, script_dir)
    p2 = _spawn_upgrade(url, r2, go, script_dir)
    # 等双方都 READY 再放行，保证几乎同时到达 pg_advisory_xact_lock
    deadline = time.time() + 30
    while not (os.path.exists(r1) and os.path.exists(r2)):
        assert time.time() < deadline, "subprocess ready barrier timeout"
        time.sleep(0.005)
    Path(go).touch()

    out1, err1 = p1.communicate(timeout=60)
    out2, err2 = p2.communicate(timeout=60)
    assert p1.returncode == 0, f"proc1: {err1[-2000:]}"
    assert p2.returncode == 0, f"proc2: {err2[-2000:]}"

    # 只执行一次：第二进程获锁后看到新 head 而 no-op。
    assert _version_rows(url) == [(LOCK_PROBE_REVISION,)]
    assert _query(url, "SELECT id FROM migration_lock_probe") == [(1,)]
    # 进程结束后同 key 可立即再获取，无残留 xact lock。
    assert _query(
        url, "SELECT pg_try_advisory_xact_lock(5786375870084826445)"
    ) == [(True,)]


# ---------------- 3. 故意 revision 异常整体回滚 ----------------

def test_failing_revision_rolls_back_and_version_not_advanced(temp_pg_db, tmp_path):
    url = temp_pg_db.url
    # 先升到 v2_0001（成功）
    _run(command.upgrade, "head", url)
    assert _version_rows(url) == [(HEAD_REVISION,)]

    # 同一 run 中先成功一个 revision，后续 revision 再失败。
    script_dir = _temporary_script(
        tmp_path / "rollback_alembic",
        {
            "f0000001_success.py": SUCCESS_REVISION_SRC,
            "f0000002_fail.py": FAILING_REVISION_SRC,
        },
    )

    with pytest.raises(RuntimeError, match="intentional-failure"):
        _run(command.upgrade, "head", url, script_location=str(script_dir))

    # version 不前进，仍为 v2_0001（已提交）；前一个已成功 revision 与失败
    # revision 的 DDL 均不存在，证明回滚的是整次 run，不是只回滚最后一个 revision。
    assert _version_rows(url) == [(HEAD_REVISION,)]
    leftover = _query(
        url,
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name IN ('run_success_table', 'partial_created_table')",
    )
    assert leftover == [(0,)]
