"""
策略桥接任务（interval 15s）

正向：tail postgrad-signal-lab 的 create-only JSONL 账本 + run.log HEARTBEAT
      → 幂等落库 rb_trades / rb_positions / rb_executions / rb_heartbeats。
      偏移量持久化在 Redis hash，崩溃续传；dedupe_key（raw 行 sha256）去重。
反向：rb_strategies 表 → 原子写 control.json（bot 每 10s 重读）。

只读 postgrad-signal-lab 的文件，除 control.json 外不写。
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.config import settings
from app.services.database import async_session
from app.services.redis import get_redis
from app.services.trading_control import (
    LEDGER_DIR, build_control_payload, write_control_file, read_control_file,
    send_telegram,
)
from app.tasks.base import BaseTask

logger = logging.getLogger("task")

PREFIX = settings.APP_NAME
OFFSETS_KEY = f"{PREFIX}:trading:bridge:offsets"
FAILURES_KEY = f"{PREFIX}:trading:bridge:consecutive_failures"
FAIL_ALERT_KEY = f"{PREFIX}:trading:bridge:fail_alerted"
BREAKER_ALERT_KEY = f"{PREFIX}:trading:bridge:breaker_alerted"

HEARTBEAT_RE = re.compile(
    r"HEARTBEAT block=(?P<block>\d+) chain_ts=(?P<chain_ts>\d+)"
    r" pools=(?P<pools>\d+) window_swaps=(?P<window_swaps>\d+)"
    r" open=(?P<open>\d+) signals=(?P<signals>\d+) trades=(?P<trades>\d+)"
    r" cum_pnl_usd=(?P<cum_pnl>[+-]?[\d.]+)"
)

INSERT_TRADE_SQL = text("""
    INSERT INTO rb_trades (
        run_id, signal_id, dedupe_key, pool_address, token_address, token_symbol,
        direction, entry_time, exit_time, entry_price, exit_price,
        amount_usd, pnl_usd, pnl_pct, exit_reason, source, raw
    ) VALUES (
        :run_id, :signal_id, :dedupe_key, :pool_address, :token_address, :token_symbol,
        :direction, :entry_time, :exit_time, :entry_price, :exit_price,
        :amount_usd, :pnl_usd, :pnl_pct, :exit_reason, :source, :raw
    ) ON CONFLICT (dedupe_key) DO NOTHING
""")

INSERT_POSITION_SQL = text("""
    INSERT INTO rb_positions (
        run_id, signal_id, dedupe_key, pool_address, token_address, token_symbol,
        direction, entry_time, entry_price, amount_usd, status, raw
    ) VALUES (
        :run_id, :signal_id, :dedupe_key, :pool_address, :token_address, :token_symbol,
        :direction, :entry_time, :entry_price, :amount_usd, 'open', :raw
    ) ON CONFLICT (dedupe_key) DO NOTHING
""")

CLOSE_POSITION_SQL = text("""
    UPDATE rb_positions
    SET status = 'closed', pnl_usd = :pnl_usd, updated_at = now()
    WHERE signal_id = :signal_id AND status = 'open'
""")

INSERT_EXECUTION_SQL = text("""
    INSERT INTO rb_executions (run_id, ts, kind, dedupe_key, payload)
    VALUES (:run_id, :ts, :kind, :dedupe_key, :payload)
    ON CONFLICT (dedupe_key) DO NOTHING
""")

INSERT_HEARTBEAT_SQL = text("""
    INSERT INTO rb_heartbeats (
        run_id, ts, block, pools, open_count, signals_total, trades_total,
        cum_pnl_usd, dedupe_key
    ) VALUES (
        :run_id, :ts, :block, :pools, :open_count, :signals_total, :trades_total,
        :cum_pnl_usd, :dedupe_key
    ) ON CONFLICT (dedupe_key) DO NOTHING
""")


def _dedupe_key(raw_line: str) -> str:
    return hashlib.sha256(raw_line.encode()).hexdigest()


def _parse_ts(value):
    """ISO 字符串或 epoch 秒 → aware datetime；无法解析返回 None"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _pick(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _run_id_of(path: Path) -> str:
    """run-<utc>/xxx.jsonl → run-<utc>；账本根目录下的文件为 ''"""
    parent = path.parent.name
    return parent if parent.startswith("run-") else ""


class StrategyBridgeTask(BaseTask):
    name = "strategy_bridge"
    interval = 15

    async def run(self):
        try:
            async with async_session() as db:
                counts = await self._ingest(db)
                await self._sync_control(db)
                await self._check_breaker(db)
            await self._reset_failures()
            if any(counts.values()):
                logger.info(f"[{self.name}] ingested: {counts}")
        except Exception as e:
            await self._on_failure(error=e)

    # ==================== 正向：JSONL / run.log → DB ====================

    async def _ingest(self, db) -> dict:
        r = await get_redis()
        offsets = await r.hgetall(OFFSETS_KEY)
        counts = {"signals": 0, "trades": 0, "executions": 0, "heartbeats": 0}

        for path in sorted(LEDGER_DIR.glob("run-*/signals.jsonl")):
            counts["signals"] += await self._tail_jsonl(
                db, r, offsets, path, self._handle_signal
            )
        for path in sorted(LEDGER_DIR.glob("run-*/trades.jsonl")):
            counts["trades"] += await self._tail_jsonl(
                db, r, offsets, path, self._handle_trade
            )
        exec_file = LEDGER_DIR / "executor" / "ledger" / "executions.jsonl"
        if exec_file.exists():
            counts["executions"] += await self._tail_jsonl(
                db, r, offsets, exec_file, self._handle_execution
            )
        run_log = LEDGER_DIR / "run.log"
        if run_log.exists():
            counts["heartbeats"] += await self._tail_log(
                db, r, offsets, run_log
            )
        return counts

    async def _read_new_lines(self, r, offsets: dict, path: Path) -> tuple[list[str], int]:
        """按持久化偏移量增量读取完整行；文件被截断（轮替）时从头重读"""
        key = str(path)
        try:
            offset = int(offsets.get(key, 0))
        except (TypeError, ValueError):
            offset = 0
        size = path.stat().st_size
        if offset > size:
            offset = 0  # 文件轮替/截断，从头重读（dedupe_key 保证幂等）
        if offset == size:
            return [], offset
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
        if data.endswith(b"\n"):
            consumed = len(data)
            chunks = data.split(b"\n")[:-1]
        else:
            # 末尾半行不处理，下轮再读
            chunks = data.split(b"\n")
            tail = chunks.pop()
            consumed = len(data) - len(tail)
        complete = [c.decode("utf-8", errors="replace") for c in chunks if c.strip()]
        return complete, offset + consumed

    async def _save_offset(self, r, path: Path, offset: int):
        await r.hset(OFFSETS_KEY, str(path), offset)

    async def _tail_jsonl(self, db, r, offsets, path: Path, handler) -> int:
        lines, new_offset = await self._read_new_lines(r, offsets, path)
        processed = 0
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(f"[{self.name}] bad json line in {path.name}: {line[:120]}")
                continue
            await handler(db, _run_id_of(path), _dedupe_key(line), record)
            processed += 1
        await db.commit()
        await self._save_offset(r, path, new_offset)
        return processed

    async def _tail_log(self, db, r, offsets, path: Path) -> int:
        lines, new_offset = await self._read_new_lines(r, offsets, path)
        processed = 0
        for line in lines:
            m = HEARTBEAT_RE.search(line)
            if not m:
                continue
            g = m.groupdict()
            await db.execute(INSERT_HEARTBEAT_SQL, {
                "run_id": "",
                "ts": datetime.fromtimestamp(int(g["chain_ts"]), tz=timezone.utc),
                "block": int(g["block"]),
                "pools": int(g["pools"]),
                "open_count": int(g["open"]),
                "signals_total": int(g["signals"]),
                "trades_total": int(g["trades"]),
                "cum_pnl_usd": g["cum_pnl"],
                "dedupe_key": _dedupe_key(line),
            })
            processed += 1
        await db.commit()
        await self._save_offset(r, path, new_offset)
        return processed

    async def _handle_signal(self, db, run_id: str, dedupe_key: str, d: dict):
        await db.execute(INSERT_POSITION_SQL, {
            "run_id": run_id,
            "signal_id": _pick(d, "signal_id", "id"),
            "dedupe_key": dedupe_key,
            "pool_address": _pick(d, "pool_address", "pool"),
            "token_address": _pick(d, "token_address", "token", "token_mint"),
            "token_symbol": _pick(d, "token_symbol", "symbol"),
            "direction": _pick(d, "direction", "side") or "long",
            "entry_time": _parse_ts(_pick(d, "entry_time", "entry_ts", "ts", "opened_at")),
            "entry_price": _pick(d, "entry_price", "price"),
            "amount_usd": _pick(d, "amount_usd", "notional_usd"),
            "raw": json.dumps(d),
        })

    async def _handle_trade(self, db, run_id: str, dedupe_key: str, d: dict):
        signal_id = _pick(d, "signal_id", "id")
        pnl_usd = _pick(d, "pnl_usd", "pnl")
        await db.execute(INSERT_TRADE_SQL, {
            "run_id": run_id,
            "signal_id": signal_id,
            "dedupe_key": dedupe_key,
            "pool_address": _pick(d, "pool_address", "pool"),
            "token_address": _pick(d, "token_address", "token", "token_mint"),
            "token_symbol": _pick(d, "token_symbol", "symbol"),
            "direction": _pick(d, "direction", "side") or "long",
            "entry_time": _parse_ts(_pick(d, "entry_time", "entry_ts", "opened_at")),
            "exit_time": _parse_ts(_pick(d, "exit_time", "exit_ts", "closed_at")),
            "entry_price": _pick(d, "entry_price"),
            "exit_price": _pick(d, "exit_price"),
            "amount_usd": _pick(d, "amount_usd", "notional_usd"),
            "pnl_usd": pnl_usd,
            "pnl_pct": _pick(d, "pnl_pct"),
            "exit_reason": _pick(d, "exit_reason", "reason"),
            "source": _pick(d, "source", "mode") or "PAPER",
            "raw": json.dumps(d),
        })
        if signal_id is not None:
            await db.execute(CLOSE_POSITION_SQL, {
                "signal_id": str(signal_id), "pnl_usd": pnl_usd,
            })

    async def _handle_execution(self, db, run_id: str, dedupe_key: str, d: dict):
        await db.execute(INSERT_EXECUTION_SQL, {
            "run_id": run_id,
            "ts": _parse_ts(_pick(d, "ts", "time", "executed_at")),
            "kind": _pick(d, "kind", "type", "action"),
            "dedupe_key": dedupe_key,
            "payload": json.dumps(d),
        })

    # ==================== 反向：strategies → control.json ====================

    async def _sync_control(self, db):
        result = await db.execute(
            text("SELECT id, name, mode, params FROM rb_strategies ORDER BY id LIMIT 1")
        )
        row = result.mappings().first()
        if row is None:
            return
        payload = build_control_payload(dict(row))
        if read_control_file() != payload:
            write_control_file(payload)
            logger.info(f"[{self.name}] control.json synced: {payload}")

    # ==================== 熔断检测 ====================

    async def _check_breaker(self, db):
        row = (
            await db.execute(
                text("SELECT params FROM rb_strategies ORDER BY id LIMIT 1")
            )
        ).first()
        if row is None:
            return
        params = row[0] or {}
        breaker = params.get("daily_loss_breaker_usd")
        if breaker is None:
            return
        hb = (
            await db.execute(
                text("SELECT cum_pnl_usd FROM rb_heartbeats ORDER BY ts DESC LIMIT 1")
            )
        ).first()
        if hb is None or hb[0] is None:
            return
        if float(hb[0]) > -float(breaker):
            return
        r = await get_redis()
        alerted = await r.set(BREAKER_ALERT_KEY, "1", ex=86400, nx=True)
        if alerted:
            await send_telegram(
                db,
                "熔断触发",
                f"累计 PnL {float(hb[0]):.2f} USD 已击穿日亏损熔断线 "
                f"-{float(breaker):.2f} USD，请检查 bot 状态。",
            )

    # ==================== 失败计数告警 ====================

    async def _on_failure(self, error: Exception):
        logger.exception(f"[{self.name}] run failed: {error}")
        try:
            r = await get_redis()
            failures = await r.incr(FAILURES_KEY)
            if failures >= 3:
                alerted = await r.set(FAIL_ALERT_KEY, "1", ex=3600, nx=True)
                if alerted:
                    async with async_session() as session:
                        await send_telegram(
                            session,
                            "策略桥接连续失败",
                            f"strategy_bridge 已连续 {failures} 轮失败：{error}",
                        )
        except Exception:
            pass

    async def _reset_failures(self):
        try:
            r = await get_redis()
            await r.delete(FAILURES_KEY, FAIL_ALERT_KEY)
        except Exception:
            pass
