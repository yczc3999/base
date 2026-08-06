"""数据库备份逻辑 — pg_dump 定时/手动 + 校验 + 保留策略 + 下载.

设计约束（用户决策 2026-08-05: 只做备份不做恢复）:
  - 备份本体 pg_dump custom 格式 → serve/storage/backups/
  - Redis 锁防并发（定时任务与手动按钮同时触发时只跑一个）
  - 备份后 pg_restore --list 校验 dump 完整性（pg_restore 缺失则跳过）
  - 保留策略: 最近 7 天每日 + 最近 4 周每周, 其余删除（文件 + DB 记录）
"""
import asyncio
import logging
import os
import subprocess
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logics.base import BaseLogic, BizError
from app.models.db_backup import DbBackup
from app.services.redis import get_redis

logger = logging.getLogger("logic")

# 与 file 逻辑共用 storage 根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_BACKUPS_ROOT = os.path.join(_PROJECT_ROOT, "storage", "backups")

_PG_DUMP_TIMEOUT = 600     # 10 分钟
_RETENTION_DAYS = 7        # 每日备份保留天数
_RETENTION_WEEKS = 4       # 周备份保留周数

# Lua 脚本：仅当锁值匹配时才删除（原子操作，防误删他人锁）
_DEL_IF_MATCH = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _compute_retention_keep(records: list[DbBackup]) -> set[int]:
    """保留策略纯函数: 返回应保留的备份 id 集合.

    规则: 最近 7 天全部保留; 4 个周窗口 (7-14/14-21/21-28/28-35 天前)
    各保留窗口内最新一条。超过 35 天全部删除。
    """
    if not records:
        return set()
    now = datetime.now()
    ordered = sorted(records, key=lambda b: b.created_at)

    keep: set[int] = set()
    for b in ordered:
        if b.created_at >= now - timedelta(days=_RETENTION_DAYS):
            keep.add(b.id)

    # L2 修复: range(7, 7*(WEEKS+1), 7) = [7,14,21,28] 补齐 4 个周窗口 (28-35 天)
    for offset in range(_RETENTION_DAYS, _RETENTION_DAYS * (_RETENTION_WEEKS + 1), _RETENTION_DAYS):
        window_start = now - timedelta(days=offset + _RETENTION_DAYS)
        window_end = now - timedelta(days=offset)
        in_window = [b for b in ordered if window_start <= b.created_at < window_end]
        if in_window:
            keep.add(max(in_window, key=lambda b: b.created_at).id)
    return keep


class DbBackupLogic(BaseLogic):
    model = DbBackup
    cache_prefix = "db_backup"

    def allowed_filters(self):
        return ["id", "status"]

    def allowed_sorts(self):
        return ["id", "created_at", "file_size"]

    def before_create(self, data: dict) -> dict:
        # S2 修复: 备份记录只读, 只能由 do_backup 内部 db.add() 创建
        raise BizError("备份记录只能由备份任务创建，不支持手动新增")

    def before_edit(self, data: dict) -> dict:
        # S2 修复: 备份记录只读
        raise BizError("备份记录只读，不支持手动编辑")

    @staticmethod
    def _backup_file_path(filename: str) -> str:
        # basename 防路径穿越
        return os.path.join(_BACKUPS_ROOT, os.path.basename(filename))

    # ---- 备份执行 ----

    async def do_backup(self, db: AsyncSession) -> dict:
        """执行一次完整备份（定时任务与手动按钮共用）."""
        r = await get_redis()
        lock_key = f"{settings.APP_NAME}:backup:lock"
        token = uuid.uuid4().hex
        # L4 修复: token 锁 + finally 释放, TTL 300s 仅兜底异常中断
        if not await r.set(lock_key, token, ex=300, nx=True):
            raise BizError("已有备份任务正在进行")

        try:
            return await self._do_backup_locked(db)
        finally:
            # 释放锁（DEL-IF-MATCH: 只删自己持有的锁）
            try:
                await r.eval(_DEL_IF_MATCH, 1, lock_key, token)
            except Exception:
                logger.warning("backup lock release failed")

    async def _do_backup_locked(self, db: AsyncSession) -> dict:
        """持锁状态下的备份主体（成功/失败均记录 + 清理失败文件）."""
        started = datetime.now()
        # 微秒精度防同秒并发撞 UNIQUE(filename)（L4 锁释放后同秒两次备份可行）
        filename = f"backup_{started.strftime('%Y%m%d_%H%M%S_%f')}.dump"
        output = self._backup_file_path(filename)

        record = DbBackup(filename=filename, status=DbBackup.Status.OK, started_at=started)
        db.add(record)
        await db.flush()

        try:
            os.makedirs(_BACKUPS_ROOT, exist_ok=True)
            await asyncio.to_thread(self._run_pg_dump, output)

            # 完整性校验（pg_restore 缺失时跳过, 不阻断备份）
            verify_ok = await asyncio.to_thread(self._verify_dump, output)
            if verify_ok is False:
                raise RuntimeError("dump 完整性校验失败")

            record.file_size = os.path.getsize(output)
            record.finished_at = datetime.now()
            await db.commit()
        except Exception as e:
            # S5 修复: 详细错误进日志, DB/API 只返回通用消息
            logger.error(f"DB backup failed ({filename}): {e}", exc_info=True)
            await db.rollback()
            record.status = DbBackup.Status.FAILED
            record.error_msg = "备份失败，详见服务端日志"
            record.finished_at = datetime.now()
            db.add(record)
            await db.commit()
            # 清理失败的 dump 文件
            if os.path.exists(output):
                try:
                    os.remove(output)
                except OSError:
                    pass
            raise BizError("备份失败，详见服务端日志")

        # 保留策略清理（不影响本次备份结果）
        try:
            await self._apply_retention(db)
        except Exception:
            logger.warning("retention cleanup failed", exc_info=True)

        return {"filename": filename, "file_size": record.file_size}

    def _run_pg_dump(self, output: str):
        env = os.environ.copy()
        env["PGPASSWORD"] = settings.DATABASE_PASSWORD
        cmd = [
            "pg_dump",
            "-h", settings.DATABASE_HOST,
            "-p", str(settings.DATABASE_PORT),
            "-U", settings.DATABASE_USER,
            "-d", settings.DATABASE_NAME,
            "--format=custom",
            "--file", output,
        ]
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=_PG_DUMP_TIMEOUT)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "").strip()[:400] or "pg_dump 退出码非 0")

    def _verify_dump(self, output: str) -> bool | None:
        """pg_restore --list 校验. 返回 True=有效 / False=损坏 / None=无法校验."""
        try:
            proc = subprocess.run(
                ["pg_restore", "--list", output],
                capture_output=True, text=True, timeout=60,
            )
        except FileNotFoundError:
            return None  # pg_restore 未安装, 跳过校验
        return proc.returncode == 0

    # ---- 保留策略 ----

    async def _apply_retention(self, db: AsyncSession):
        stmt = select(DbBackup).order_by(DbBackup.created_at)
        records = (await db.execute(stmt)).scalars().all()

        ok_records = [b for b in records if b.status == DbBackup.Status.OK]
        keep_ids = _compute_retention_keep(ok_records)

        cutoff = datetime.now() - timedelta(days=_RETENTION_DAYS)
        for b in records:
            if b.status == DbBackup.Status.FAILED:
                # L3 修复: 失败记录（失败时文件已清理, 仅剩 DB 记录）保留 7 天后删除
                if b.created_at and b.created_at >= cutoff:
                    continue
                await db.delete(b)
                continue
            if b.id in keep_ids:
                continue
            self._delete_file(b.filename)
            await db.delete(b)
        await db.commit()

    def _delete_file(self, filename: str):
        path = self._backup_file_path(filename)
        if os.path.exists(path):
            os.remove(path)

    # ---- 管理操作 ----

    async def do_delete(self, db: AsyncSession, ids: list[int]):
        """删除备份（DB 记录 + 磁盘文件）."""
        for pk in ids:
            record = await self.get_detail(db, pk)
            if record:
                self._delete_file(record["filename"])
        await super().do_delete(db, ids)

    async def delete_backup(self, db: AsyncSession, backup_id: int):
        """删除备份（DB 记录 + 磁盘文件）."""
        record = await self.get_detail(db, backup_id)
        if not record:
            raise BizError("备份不存在")
        self._delete_file(record["filename"])
        await self.do_delete(db, [backup_id])

    async def get_download(self, db: AsyncSession, filename: str) -> str:
        """校验记录存在 + 文件存在, 返回下载路径."""
        record = await self.get_by_field(db, "filename", filename)
        if not record:
            raise BizError("备份记录不存在")
        path = self._backup_file_path(filename)
        if not os.path.exists(path):
            raise BizError("备份文件不存在")
        return path


db_backup_logic = DbBackupLogic()
