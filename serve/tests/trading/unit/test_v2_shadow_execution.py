"""Shadow execution Logic 单测（WP-03 Checkpoint C）。

用 fake ExecutionRepository + LedgerRepository，纯内存断言；
postings_balanced 强制为 False 的分支用 monkeypatch 覆盖（不改生产代码）。
"""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.logics.trading import ShadowExecutionLogic
from app.schemas.trading.execution import ShadowFillInput


class FakeUoW:
    def __init__(self):
        self.session = SimpleNamespace()


class FakeExecutionRepository:
    def __init__(self):
        self.calls = []
        self.position = None

    def _record(self, method, **kwargs):
        self.calls.append((method, kwargs))

    def calls_for(self, method):
        return [kwargs for name, kwargs in self.calls if name == method]

    async def insert_execution(self, session, **kwargs):
        self._record("insert_execution", **kwargs)
        return 1

    async def terminalize_execution(self, session, execution_id, **kwargs):
        self._record("terminalize_execution", execution_id=execution_id, **kwargs)
        return True

    async def get_position(self, session, **kwargs):
        self._record("get_position", **kwargs)
        return self.position

    async def upsert_position(self, session, **kwargs):
        self._record("upsert_position", **kwargs)

    async def insert_position_lot(self, session, **kwargs):
        self._record("insert_position_lot", **kwargs)


class FakeLedgerRepository:
    def __init__(self):
        self.calls = []
        self.postings = None

    def _record(self, method, **kwargs):
        self.calls.append((method, kwargs))

    def calls_for(self, method):
        return [kwargs for name, kwargs in self.calls if name == method]

    async def insert_transaction(self, session, **kwargs):
        self._record("insert_transaction", **kwargs)
        return 500

    async def insert_postings(self, session, **kwargs):
        self._record("insert_postings", **kwargs)
        self.postings = kwargs["postings"]

    async def mark_posted(self, session, transaction_id, *, posted_at):
        self._record("mark_posted", transaction_id=transaction_id, posted_at=posted_at)
        return True


def _fill(**over):
    base = dict(
        execution_key="exe-1",
        economic_action_intent_id=1,
        action_set_leg_id=1,
        contract_spec_id=1,
        token_id=2,
        fill_role="open",
        quantity=Decimal("100"),
        side="buy",
        depth_levels=[[Decimal("0.5"), Decimal("100")]],
        taker_fee_bps=Decimal("0"),
        portfolio_namespace="ns",
    )
    base.update(over)
    return ShadowFillInput(**base)


async def test_shadow_fill_buy_full_depth_filled():
    exec_repo = FakeExecutionRepository()
    led_repo = FakeLedgerRepository()
    logic = ShadowExecutionLogic(exec_repo, led_repo)
    result = await logic.shadow_fill(
        FakeUoW(), fill=_fill(), portfolio_namespace="ns", cash_asset_key="usdc"
    )
    assert result.ok is True
    assert result.status == "FILLED"
    assert result.filled_quantity == Decimal("100")
    assert result.vwap == Decimal("0.5")
    assert result.fee == Decimal("0")
    # position：get_position 空 → new_quantity = 0 + 100
    assert len(exec_repo.calls_for("upsert_position")) == 1
    upsert = exec_repo.calls_for("upsert_position")[0]
    assert upsert["quantity"] == Decimal("100")
    assert upsert["cost_basis"] == Decimal("50")
    # lot 落库（fill_quantity > 0）
    assert len(exec_repo.calls_for("insert_position_lot")) == 1
    # ledger：BUY 至少 4 postings，cash 与 token 各自归零
    postings_calls = led_repo.calls_for("insert_postings")
    assert len(postings_calls) == 1
    postings = postings_calls[0]["postings"]
    assert len(postings) == 4
    cash_sum = sum(Decimal(p["amount"]) for p in postings if p["asset_type"] == "CASH")
    token_sum = sum(Decimal(p["amount"]) for p in postings if p["asset_type"] == "TOKEN")
    assert cash_sum == Decimal("0")
    assert token_sum == Decimal("0")
    assert len(led_repo.calls_for("mark_posted")) == 1


async def test_shadow_fill_buy_partial_depth():
    exec_repo = FakeExecutionRepository()
    led_repo = FakeLedgerRepository()
    logic = ShadowExecutionLogic(exec_repo, led_repo)
    result = await logic.shadow_fill(
        FakeUoW(),
        fill=_fill(quantity=Decimal("100"), depth_levels=[[Decimal("0.5"), Decimal("60")]]),
        portfolio_namespace="ns", cash_asset_key="usdc",
    )
    assert result.ok is True
    assert result.status == "PARTIAL"
    assert result.filled_quantity == Decimal("60")
    term = exec_repo.calls_for("terminalize_execution")[0]
    assert term["status"] == "PARTIAL"
    assert term["filled_quantity"] == Decimal("60")
    assert term["unfilled_reason"] == "insufficient_depth"


async def test_shadow_fill_empty_depth_rejected():
    exec_repo = FakeExecutionRepository()
    led_repo = FakeLedgerRepository()
    logic = ShadowExecutionLogic(exec_repo, led_repo)
    result = await logic.shadow_fill(
        FakeUoW(),
        # 唯一 level size=0 → 无可成交数量（保持 schema min_length=1 合法）
        fill=_fill(quantity=Decimal("100"), depth_levels=[[Decimal("0.5"), Decimal("0")]]),
        portfolio_namespace="ns", cash_asset_key="usdc",
    )
    assert result.ok is True
    assert result.status == "REJECTED"
    assert result.filled_quantity == Decimal("0")
    assert len(exec_repo.calls_for("insert_position_lot")) == 0
    # 无成交 → 不写 postings（fill_quantity=0）
    assert len(led_repo.calls_for("insert_postings")) == 0


async def test_shadow_fill_unbalanced_postings_fails(monkeypatch):
    import app.logics.trading.execution as exec_module

    exec_repo = FakeExecutionRepository()
    led_repo = FakeLedgerRepository()
    monkeypatch.setattr(exec_module, "postings_balanced", lambda postings: False)
    logic = ShadowExecutionLogic(exec_repo, led_repo)
    result = await logic.shadow_fill(
        FakeUoW(), fill=_fill(), portfolio_namespace="ns", cash_asset_key="usdc"
    )
    assert result.ok is False
    assert result.reason == "ledger_postings_unbalanced"
    assert len(led_repo.calls_for("insert_postings")) == 0
    assert len(led_repo.calls_for("mark_posted")) == 0
