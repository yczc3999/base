"""Shadow execution typed DTO（WP-03 Checkpoint C）。

只表达 typed 输入；不持有状态、不做业务判断。
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ShadowFillInput(BaseModel):
    """请求执行一个已经冻结的 action-set leg。

    除 ``execution_key`` / intent / leg identity 外的字段仅为旧事件兼容提示；执行层不把
    caller 提供的 token、数量、方向、depth、fee 或 namespace 当作事实。它们全部从
    intent → action set → decision → quote binding / frozen execution spec 读取。
    """

    model_config = ConfigDict(extra="forbid")

    execution_key: str = Field(min_length=1)
    economic_action_intent_id: int = Field(gt=0)
    action_set_leg_id: int = Field(gt=0)
    contract_spec_id: int | None = Field(default=None, gt=0)
    token_id: int | None = Field(default=None, gt=0)
    fill_role: str | None = Field(default=None, pattern="^(open|close|reduce)$")
    quantity: Decimal | None = Field(default=None, gt=0)
    side: str | None = Field(default=None, pattern="^(buy|sell)$")
    depth_levels: list[list[Decimal | int]] | None = None
    taker_fee_bps: Decimal | None = Field(default=None, ge=0)
    portfolio_namespace: str | None = Field(default=None, min_length=1)


class PositionUpdateInput(BaseModel):
    """position 重建（乐观锁）所需参数。"""

    model_config = ConfigDict(extra="forbid")

    portfolio_namespace: str = Field(min_length=1)
    contract_spec_id: int = Field(gt=0)
    token_id: int = Field(gt=0)
    market_id: int | None = None
    component_id: int | None = None


class AccountInput(BaseModel):
    """type-3 执行账户 typed 输入（WP-05）。

    只表达 typed 输入；signer/L2 凭据一律以 secret ref + version 引用，绝不接受明文。
    """

    model_config = ConfigDict(extra="forbid")

    account_key: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    chain_id: int = Field(gt=0)
    identity_type: str = Field(pattern="^(FIXTURE_ONLY|CANARY|LIVE)$")
    funder_address: str | None = None
    maker_address: str | None = None
    signing_identity: str | None = None
    wallet_type: str = Field(pattern="^(deposit_wallet|privately_owned)$")
    signature_type: str = Field(default="3", pattern="^[0-3]$")
    signer_secret_entry_id: int | None = Field(default=None, gt=0)
    signer_secret_version_id: int | None = Field(default=None, gt=0)
    l2_secret_entry_id: int | None = Field(default=None, gt=0)
    l2_secret_version_id: int | None = Field(default=None, gt=0)
    release_manifest_id: int = Field(gt=0)
    capital_permission_manifest_id: int = Field(gt=0)
    network_mode: str = Field(pattern="^(mainnet|matic_mumbai|amoy|fixture)$")
    status: str = Field(default="active", pattern="^(active|disabled)$")


class FundsUpsertInput(BaseModel):
    """funds CAS projection 写入输入（可用恒等式由 DB CHECK 强制）。"""

    model_config = ConfigDict(extra="forbid")

    account_id: int = Field(gt=0)
    asset_key: str = Field(min_length=1)
    confirmed: Decimal = Field(ge=0)
    provider_reserved: Decimal = Field(default=Decimal("0"), ge=0)
    local_reserved: Decimal = Field(default=Decimal("0"), ge=0)
    available: Decimal = Field(ge=0)
    source_snapshot_id: int = Field(gt=0)
    reconcile_watermark: int = Field(ge=0)


class ReservationInput(BaseModel):
    """资金预留 typed 输入（金额 base-unit integer）。"""

    model_config = ConfigDict(extra="forbid")

    reservation_key: str = Field(min_length=1)
    intent_id: int = Field(gt=0)
    account_id: int = Field(gt=0)
    asset_key: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)
    idempotency_key: str = Field(min_length=1)


class ReservationAdvanceInput(BaseModel):
    """reservation 状态推进输入；状态机合法转移由 DB trigger 强制。"""

    model_config = ConfigDict(extra="forbid")

    reservation_id: int = Field(gt=0)
    new_status: str = Field(
        pattern="^(HELD|UNKNOWN|PROVIDER_BOUND|CONSUMED|RELEASED)$"
    )


class LeaseAcquireInput(BaseModel):
    """执行/heartbeat leader 租约获取输入。"""

    model_config = ConfigDict(extra="forbid")

    account_id: int = Field(gt=0)
    lease_role: str = Field(pattern="^(EXECUTION|HEARTBEAT)$")
    owner: str = Field(min_length=1)
    ttl_s: float = Field(gt=0)


class LeaseRenewInput(BaseModel):
    """租约续期输入；fencing token 必须与当前值一致（CAS）。"""

    model_config = ConfigDict(extra="forbid")

    account_id: int = Field(gt=0)
    lease_role: str = Field(pattern="^(EXECUTION|HEARTBEAT)$")
    owner: str = Field(min_length=1)
    fencing_token: int = Field(gt=0)
    ttl_s: float = Field(gt=0)


class FenceAssertInput(BaseModel):
    """side effect 前的 fencing 校验输入。"""

    model_config = ConfigDict(extra="forbid")

    account_id: int = Field(gt=0)
    lease_role: str = Field(pattern="^(EXECUTION|HEARTBEAT)$")
    fencing_token: int = Field(gt=0)


class EnvelopeInput(BaseModel):
    """authorization envelope 创建输入（WP-05 Checkpoint C）。

    绑定 intent/account/release/execution spec/permission/fencing/preflight hash。
    不含任何 secret/signature；authority 仅 FAKE_CONFORMANCE。
    """

    model_config = ConfigDict(extra="forbid")

    envelope_key: str = Field(min_length=1)
    intent_id: int = Field(gt=0)
    account_id: int = Field(gt=0)
    release_manifest_id: int = Field(gt=0)
    execution_spec_version_id: int = Field(gt=0)
    capital_permission_manifest_id: int = Field(gt=0)
    authority: str = Field(pattern="^FAKE_CONFORMANCE$")
    idempotency_key: str = Field(min_length=1)
    fencing_token: int = Field(gt=0)
    intent_hash: str = Field(pattern="^[0-9a-f]{64}$")
    preflight_hash1: str = Field(pattern="^[0-9a-f]{64}$")
    preflight_hash2: str = Field(pattern="^[0-9a-f]{64}$")


class SubmitOrderInput(BaseModel):
    """submit 输入：引用 envelope + 订单参数。salt/timestamp 由 Logic 冻结。"""

    model_config = ConfigDict(extra="forbid")

    envelope_id: int = Field(gt=0)
    account_id: int = Field(gt=0)
    fencing_token: int = Field(gt=0)
    token_id: str = Field(min_length=1)
    side: str = Field(pattern="^(BUY|SELL)$")
    price: Decimal = Field(gt=0, le=1)
    size: Decimal = Field(gt=0)
    post_only: bool = False


class CancelOrderInput(BaseModel):
    """cancel 输入：按 account + external_order_id 撤单。"""

    model_config = ConfigDict(extra="forbid")

    account_id: int = Field(gt=0)
    fencing_token: int = Field(gt=0)
    external_order_id: str = Field(min_length=1)


class ReconcileInput(BaseModel):
    """对账输入：账户 + 触发原因 + REST cursor 边界。"""

    model_config = ConfigDict(extra="forbid")

    reconciliation_key: str = Field(min_length=1)
    account_id: int = Field(gt=0)
    fencing_token: int = Field(gt=0)
    trigger_reason: str = Field(min_length=1)
    ws_watermark: int = Field(ge=0)
    rest_cursor: str | None = None
