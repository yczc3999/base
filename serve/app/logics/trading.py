"""交易 bot 管理模块 Logic"""
import json
from decimal import Decimal
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.trading import RbTrade, RbPosition, RbStrategy, RbHeartbeat
from app.logics.base import BaseLogic, BizError
from app.services.trading_control import (
    VALID_MODES, build_control_payload, write_control_file, send_telegram,
)


def _decimal_to_float(data: dict) -> dict:
    for k, v in data.items():
        if isinstance(v, Decimal):
            data[k] = float(v)
    return data


class RbTradeLogic(BaseLogic):
    model = RbTrade

    def allowed_filters(self):
        return [
            "id", "run_id", "signal_id", "pool_address", "token_address",
            "token_symbol", "direction", "exit_reason", "source",
            "entry_time", "exit_time",
        ]

    def allowed_sorts(self):
        return ["id", "entry_time", "exit_time", "pnl_usd", "amount_usd", "created_at"]

    def keyword_fields(self):
        return ["token_symbol", "pool_address", "token_address"]

    def format_data(self, record) -> dict:
        return _decimal_to_float(super().format_data(record))

    def export_header_map(self):
        return {
            "id": "ID", "run_id": "运行批次", "signal_id": "信号ID",
            "pool_address": "池地址", "token_symbol": "Token",
            "direction": "方向", "entry_time": "入场时间", "exit_time": "出场时间",
            "entry_price": "入场价", "exit_price": "出场价",
            "amount_usd": "金额(USD)", "pnl_usd": "PnL(USD)", "pnl_pct": "PnL(%)",
            "exit_reason": "退出原因", "source": "来源",
        }


class RbPositionLogic(BaseLogic):
    model = RbPosition

    def allowed_filters(self):
        return [
            "id", "run_id", "signal_id", "pool_address", "token_symbol",
            "direction", "status",
        ]

    def allowed_sorts(self):
        return ["id", "entry_time", "amount_usd", "created_at", "updated_at"]

    def keyword_fields(self):
        return ["token_symbol", "pool_address", "token_address"]

    def format_data(self, record) -> dict:
        return _decimal_to_float(super().format_data(record))


class RbStrategyLogic(BaseLogic):
    model = RbStrategy

    def allowed_filters(self):
        return ["id", "name", "mode"]

    def allowed_sorts(self):
        return ["id", "name", "updated_at", "created_at"]

    def format_save_data(self, data: dict, is_update: bool = False) -> dict:
        data = super().format_save_data(data, is_update)
        # 前端 JsonEditor 传 JSON 字符串，落库前转 dict（JSONB）
        params = data.get("params")
        if isinstance(params, str):
            try:
                data["params"] = json.loads(params) if params.strip() else {}
            except json.JSONDecodeError:
                raise BizError("参数不是合法 JSON")
        mode = data.get("mode")
        if mode is not None and mode not in VALID_MODES:
            raise BizError(f"mode 必须是 {'/'.join(VALID_MODES)}")
        return data

    async def modify(self, db: AsyncSession, pk_value: Any, data: dict) -> dict:
        old = await self.get_detail(db, pk_value)
        if old is None:
            raise BizError("策略不存在")
        result = await super().modify(db, pk_value, data)

        # 落库后立即原子同步 control.json（保存即下发）
        try:
            write_control_file(build_control_payload(result))
        except Exception as e:
            raise BizError(f"配置已入库，但 control.json 同步失败：{e}")

        # mode 变更 → Telegram 告警
        if result.get("mode") != old.get("mode"):
            await send_telegram(
                db,
                "策略模式变更",
                f"{result.get('name')}: {old.get('mode')} → {result.get('mode')}",
            )
        return result


class RbHeartbeatLogic(BaseLogic):
    model = RbHeartbeat

    def allowed_filters(self):
        return ["id", "run_id"]

    def allowed_sorts(self):
        return ["id", "ts", "block", "created_at"]

    def format_data(self, record) -> dict:
        return _decimal_to_float(super().format_data(record))


rb_trade_logic = RbTradeLogic()
rb_position_logic = RbPositionLogic()
rb_strategy_logic = RbStrategyLogic()
rb_heartbeat_logic = RbHeartbeatLogic()
