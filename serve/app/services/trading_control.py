"""
交易 bot 控制文件同步 + Telegram 告警

反向通道：rb_strategies 表 → control.json（tmp + rename 原子写），
bot 侧每 10s 重读。只写 control.json，账本目录其余文件一律只读。
"""

import json
import os
from pathlib import Path

LEDGER_DIR = Path("/code/postgrad-signal-lab/data/results/bsc-rb-paper-v1")
CONTROL_FILE = LEDGER_DIR / "control.json"

# control.json schema 中 mode 之外的参数键（存于 rb_strategies.params）
CONTROL_PARAM_KEYS = [
    "max_position_usd",
    "max_open_positions",
    "daily_loss_breaker_usd",
    "slippage_bps",
    "gas_price_cap_gwei",
]

VALID_MODES = ("PAPER", "SHADOW", "LIVE")


def build_control_payload(strategy: dict) -> dict:
    """rb_strategies 行 → control.json 内容"""
    params = strategy.get("params") or {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}
    payload = {"mode": strategy.get("mode") or "PAPER"}
    for key in CONTROL_PARAM_KEYS:
        if key in params:
            payload[key] = params[key]
    return payload


def write_control_file(payload: dict) -> None:
    """原子写 control.json（tmp + rename），失败抛异常由调用方处理"""
    tmp = CONTROL_FILE.with_name(CONTROL_FILE.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, CONTROL_FILE)


def read_control_file() -> dict | None:
    """读取当前 control.json（不存在或损坏返回 None）"""
    try:
        return json.loads(CONTROL_FILE.read_text())
    except Exception:
        return None


async def send_telegram(db, title: str, content: str) -> None:
    """
    Telegram 告警（settings 表 notify/telegram 未配置时静默跳过，不抛异常）
    """
    try:
        from app.services.notify import notify_service

        await notify_service.send_by(db, "telegram", title, content)
    except Exception:
        pass
