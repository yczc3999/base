"""Polygon JSON-RPC / Relayer 公共 wire 类型（WP-06 Checkpoint C）。

只做 Pydantic 解析与规范化，**不发网络请求**。response shape/hex/quantity 严格校验：
- quantity 必须 ``0x`` 前缀偶数 hex；``eth_getCode`` 返回满长字节（非空且偶数 hex）。
- receipt 字段 ``transactionHash/status/blockNumber/blockHash`` 结构严格；status 只能
  ``0x1``(成功) / ``0x0``(失败)；blockNumber/blockHash 非空。
- ``FinalityCheck``：Relayer CONFIRMED 不等于 finality；finalized.number > receipt.blockNumber
  才允许 FINALIZED 记账。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.polymarket.common import PolymarketError

_HEX_QUANTITY_RE = re.compile(r"^0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)$")
_HEX_DATA_RE = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")
_HEX32_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_RELAYER_TRANSACTION_ID_RE = re.compile(r"^[A-Za-z0-9._~-]{1,256}$")


def validate_hex_quantity(value: str, *, path: str) -> str:
    """严格 quantity：0x 前缀 + 非空 hex（driver 层以 PolymarketError 呈现）。"""
    if not isinstance(value, str) or not _HEX_QUANTITY_RE.match(value):
        raise PolymarketError(f"{path}_quantity_invalid")
    return value.lower()


def validate_hex_data(value: str, *, path: str, allow_empty: bool = False) -> str:
    """严格 data：0x 前缀 + 偶数 hex 字节（code 必须非空，允许空串场景由调用方控制）。"""
    if not isinstance(value, str) or not _HEX_DATA_RE.match(value):
        raise PolymarketError(f"{path}_data_invalid")
    if not allow_empty and len(value) == 2:
        raise PolymarketError(f"{path}_data_empty")
    return value.lower()


def validate_address(value: str, *, path: str) -> str:
    if not isinstance(value, str) or not _ADDRESS_RE.match(value):
        raise PolymarketError(f"{path}_address_invalid")
    return value.lower()


def validate_hex32(value: str, *, path: str) -> str:
    if not isinstance(value, str) or not _HEX32_RE.match(value):
        raise PolymarketError(f"{path}_hex32_invalid")
    return value.lower()


def validate_relayer_transaction_id(value: object) -> str:
    """Strict URL-path-safe Relayer transaction identity."""
    if not isinstance(value, str) or not _RELAYER_TRANSACTION_ID_RE.fullmatch(value):
        raise ValueError("relayer_transaction_id_invalid")
    return value


class RpcBlock(BaseModel):
    """``eth_getBlockByNumber`` 结果（minimal：number/hash/timestamp/transactions 计数）。"""

    model_config = ConfigDict(extra="ignore", strict=True, populate_by_name=True)

    number: str
    hash: str
    timestamp: str
    parent_hash: str | None = Field(default=None, alias="parentHash")

    @field_validator("number", "timestamp")
    @classmethod
    def _qty(cls, v: str) -> str:
        return validate_hex_quantity(v, path="block")

    @field_validator("hash", "parent_hash")
    @classmethod
    def _hash(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_hex32(v, path="block")

    @property
    def number_int(self) -> int:
        return int(self.number, 16)


class RpcLog(BaseModel):
    """Receipt log fields relevant to canonical/reorg handling."""

    model_config = ConfigDict(extra="ignore", strict=True)

    removed: bool = False


class RpcReceipt(BaseModel):
    """``eth_getTransactionReceipt`` 结果（strict shape；removed 表示 reorg）。"""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, strict=True)

    transaction_hash: str = Field(alias="transactionHash")
    status: str
    block_number: str = Field(alias="blockNumber")
    block_hash: str = Field(alias="blockHash")
    transaction_index: str = Field(alias="transactionIndex")
    removed: bool = False
    logs: list[RpcLog] = Field(default_factory=list)

    @field_validator("transaction_hash", "block_hash")
    @classmethod
    def _hash32(cls, v: str) -> str:
        return validate_hex32(v, path="receipt")

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in ("0x1", "0x0"):
            raise ValueError("receipt_status_invalid")
        return v

    @field_validator("block_number", "transaction_index")
    @classmethod
    def _qty(cls, v: str) -> str:
        return validate_hex_quantity(v, path="receipt")

    @property
    def success(self) -> bool:
        return self.status == "0x1"

    @property
    def block_number_int(self) -> int:
        return int(self.block_number, 16)

    @property
    def has_removed_log(self) -> bool:
        return self.removed or any(log.removed for log in self.logs)


@dataclass(frozen=True)
class FinalityCheck:
    """canonical receipt + finalized block 核验结果（不落任何 secret）。"""

    receipt: RpcReceipt
    canonical_block_hash: str
    finalized_block_number: int
    finalized_block_hash: str
    finalized_after_receipt: bool


class RpcError(BaseModel):
    """JSON-RPC error 对象（脱敏：code/message 保留，data 丢弃）。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    code: int
    message: str


class JsonRpcResponse(BaseModel):
    """JSON-RPC 响应外壳：``jsonrpc=2.0`` + ``id`` 匹配 + result XOR error。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    jsonrpc: str
    id: int
    result: Any | None = None
    error: RpcError | None = None

    @field_validator("jsonrpc")
    @classmethod
    def _version(cls, v: str) -> str:
        if v != "2.0":
            raise ValueError("jsonrpc_version_invalid")
        return v

    @model_validator(mode="before")
    @classmethod
    def _result_xor_error(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if ("result" in value) == ("error" in value):
            raise ValueError("jsonrpc_result_error_xor_invalid")
        return value

    def ensure_no_error(self, *, path: str) -> None:
        if self.error is not None:
            raise ValueError(f"{path}_rpc_error:{self.error.code}")


_RELAYER_STATES = {
    "STATE_NEW": "NEW",
    "STATE_EXECUTED": "EXECUTED",
    "STATE_MINED": "MINED",
    "STATE_CONFIRMED": "CONFIRMED",
    "STATE_INVALID": "INVALID",
    "STATE_FAILED": "FAILED",
}


def normalize_relayer_state(value: object) -> str:
    if not isinstance(value, str) or value not in _RELAYER_STATES:
        raise ValueError("relayer_state_invalid")
    return _RELAYER_STATES[value]


class RelayerStatus(BaseModel):
    """``GET /v1/account/transactions/{id}`` 响应。CONFIRMED 只是 Relayer 成功终态。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    transaction_id: str
    transaction_hash: str | None = None
    state: str
    error_msg: str | None = None

    @field_validator("state")
    @classmethod
    def _state(cls, v: str) -> str:
        normalize_relayer_state(v)
        return v

    @field_validator("transaction_id")
    @classmethod
    def _transaction_id(cls, v: str) -> str:
        return validate_relayer_transaction_id(v)

    @field_validator("transaction_hash")
    @classmethod
    def _transaction_hash(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        return validate_hex32(v, path="relayer_transaction")

    @field_validator("error_msg")
    @classmethod
    def _redact_error_msg(cls, v: str | None) -> str | None:
        # Provider text can echo request bodies, endpoint credentials, or keys.
        # Preserve only presence; raw fixture artifacts remain separately hashed.
        if v in (None, ""):
            return None
        return "provider_error_present"

    @property
    def normalized_state(self) -> str:
        return normalize_relayer_state(self.state)

    @property
    def is_terminal(self) -> bool:
        return self.normalized_state in ("CONFIRMED", "INVALID", "FAILED")

    @property
    def is_success_terminal(self) -> bool:
        return self.normalized_state == "CONFIRMED"
