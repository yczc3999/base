"""Settlement / label handler（WP-04 Checkpoint C）。

Handler 只解析一个 typed event、调用一个 Logic/UoW、返回 completion。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.db.uow import UnitOfWork
from app.logics.trading.settlement import ChainSettlementLogic, SettlementLogic
from app.schemas.trading.settlement import ChainSettlementEvidenceInput, LabelRevisionInput


@dataclass(frozen=True)
class SettlementEvent:
    kind: str  # label_revision | create_cluster | check_split_integrity
    payload: dict | None = None


@dataclass(frozen=True)
class HandlerResult:
    ok: bool
    result: Any | None = None
    reason: str | None = None


class SettlementHandler:
    """解析 settlement event 并调用 SettlementLogic；一个 event 一次 UoW。"""

    def __init__(
        self,
        logic: SettlementLogic,
        chain_logic: ChainSettlementLogic | None = None,
    ) -> None:
        self._logic = logic
        self._chain = chain_logic

    async def handle(
        self, uow: UnitOfWork, event: SettlementEvent
    ) -> HandlerResult:
        kind = event.kind
        if kind == "label_revision":
            if event.payload is None:
                return HandlerResult(False, reason="settlement_event_incomplete")
            input_ = LabelRevisionInput(**event.payload)
            result = await self._logic.audit_label_revision(uow, input_=input_)
            return HandlerResult(result.ok, result, result.reason)
        if kind == "create_cluster":
            if event.payload is None:
                return HandlerResult(False, reason="settlement_event_incomplete")
            payload = event.payload
            mapping_payload = payload.get("contract_token_ids")
            mapping = None
            if mapping_payload is not None:
                if not isinstance(mapping_payload, dict):
                    return HandlerResult(False, reason="cluster_token_mapping_invalid")
                mapping = {
                    int(spec_id): [int(value) for value in token_ids]
                    for spec_id, token_ids in mapping_payload.items()
                }
            result = await self._logic.create_cluster(
                uow,
                split=str(payload["split"]),
                time_block_start=datetime.fromisoformat(payload["time_block_start"]),
                time_block_end=datetime.fromisoformat(payload["time_block_end"]),
                horizon=str(payload["horizon"]),
                contract_spec_ids=(
                    [int(v) for v in payload["contract_spec_ids"]]
                    if mapping is None and "contract_spec_ids" in payload
                    else None
                ),
                token_ids=(
                    [int(v) for v in payload["token_ids"]]
                    if mapping is None and "token_ids" in payload
                    else None
                ),
                contract_token_ids=mapping,
            )
            return HandlerResult(result.ok, result, result.reason)
        if kind == "check_split_integrity":
            result = await self._logic.check_split_integrity(uow)
            return HandlerResult(result.ok, result, result.reason)
        if kind == "chain_record_evidence":
            if self._chain is None or event.payload is None:
                return HandlerResult(False, reason="chain_settlement_logic_required")
            set_key = await self._chain.record_settlement_evidence(
                uow, evidence=ChainSettlementEvidenceInput(**event.payload)
            )
            return HandlerResult(True, {"settlement_set_key": set_key})
        raise ValueError(f"settlement_event_unknown:{kind}")
