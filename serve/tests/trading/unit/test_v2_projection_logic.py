"""WP-04 projection logic —— unit（fake/in-memory repo）。

覆盖：每行 projection_hash 是内容 canonical hash（不含 id/created_at/自身 hash）；
重建幂等（同输入同输出）；keyset cursor 正确；allowlist 拒绝非法 filter/sort；
net_risk_capital / cvar / capital_days 纯函数确定性（Decimal，无 float）。
"""

from __future__ import annotations

import types

import pytest
from datetime import datetime, timezone
from decimal import Decimal

from app.domain.trading.hashing import canonical_hash
from app.logics.trading.projection import (
    ProjectionLogic,
    _risk_metrics,
    capital_days,
    compute_row_hash,
    net_risk_capital,
    worst_loss_cvar,
)

FIXED = datetime(2026, 8, 11, 3, 4, 5, tzinfo=timezone.utc)


class FakeRepo:
    """in-memory repo stub：记录 list 调用并返回 canned keyset 响应。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def list_health_current(self, session, **kwargs):
        self.calls.append(("health_current", kwargs))
        return {"rows": [], "next_cursor": None, "has_more": False}


def _row(metric_value: str = "1", status: str = "ok") -> dict:
    return {
        "metric_name": "trade_decisions",
        "metric_value": Decimal(metric_value),
        "status": status,
        "as_of": FIXED,
        "source_high_watermark": 7,
        "projection_version": 1,
        "id": 123,
        "created_at": FIXED,
        "projection_hash": "ignored",
    }


def test_row_hash_is_canonical_content_hash():
    row = _row()
    h = compute_row_hash(row)
    content = {
        k: v for k, v in row.items()
        if k not in ("id", "created_at", "projection_hash", "projection_version")
    }
    assert h == canonical_hash(content)
    # 排除自身：传入非法 hash 不影响结果
    assert h == compute_row_hash({**row, "projection_hash": "x" * 64})
    # id / created_at / projection_version 变化 → hash 不变（内容 hash 与重建代数无关）
    assert h == compute_row_hash({**row, "id": 999, "created_at": FIXED, "projection_version": 42})
    # 内容变化 → hash 变化
    assert h != compute_row_hash({**row, "metric_value": Decimal("2")})


def test_row_hash_idempotent_same_input_same_output():
    a = compute_row_hash(_row(metric_value="1.50"))
    b = compute_row_hash(_row(metric_value="1.50"))
    assert a == b
    assert a == compute_row_hash({**_row(metric_value="1.50"), "id": 777})


def test_risk_functions_deterministic_decimal():
    rows = [
        {"quantity": Decimal("100"), "cost_basis": Decimal("50")},
        {"quantity": Decimal("200"), "cost_basis": Decimal("75")},
    ]
    assert net_risk_capital(rows) == Decimal("125")
    assert isinstance(net_risk_capital(rows), Decimal)
    # 同输入同输出
    assert net_risk_capital(rows) == net_risk_capital(list(reversed(rows)))
    assert worst_loss_cvar(rows) == Decimal("125")
    assert capital_days(rows) == Decimal("0")
    metrics = _risk_metrics(rows)
    assert metrics["exposure"] == Decimal("125")
    assert metrics["net_risk_capital"] == Decimal("125")
    assert metrics["cvar"] == Decimal("125")
    assert metrics["capital_days"] == Decimal("0")
    assert all(isinstance(v, Decimal) for v in metrics.values())


def test_risk_functions_reject_float():
    with pytest.raises(ValueError, match="projection_float_forbidden"):
        net_risk_capital([{"quantity": Decimal("1"), "cost_basis": 1.0}])
    with pytest.raises(ValueError, match="projection_float_forbidden"):
        worst_loss_cvar([{"quantity": Decimal("1"), "cost_basis": 1.0}])
    with pytest.raises(ValueError, match="projection_float_forbidden"):
        _risk_metrics([{"quantity": 100.0, "cost_basis": Decimal("0")}])
    with pytest.raises(ValueError, match="projection_alpha_out_of_range"):
        worst_loss_cvar([], alpha=Decimal("0"))
    with pytest.raises(ValueError, match="projection_horizon_nonnegative"):
        capital_days([], horizon_days=Decimal("-1"))


def test_risk_exposure_uses_cost_basis_when_positive():
    rows = [
        {"quantity": Decimal("10"), "cost_basis": Decimal("0")},
        {"quantity": Decimal("5"), "cost_basis": Decimal("30")},
    ]
    metrics = _risk_metrics(rows)
    # cost_basis>0 → 用 cost_basis；否则用 abs(quantity)
    assert metrics["exposure"] == Decimal("40")  # 10 + 30
    assert metrics["net_risk_capital"] == Decimal("30")


@pytest.mark.asyncio
async def test_list_allowlist_rejects_invalid():
    logic = ProjectionLogic(FakeRepo())
    with pytest.raises(ValueError, match="unsupported filters"):
        await logic.list(None, "health_current", filters={"bogus": 1})
    with pytest.raises(ValueError, match="unsupported sort"):
        await logic.list(None, "health_current", sorts=["bogus"])
    with pytest.raises(ValueError, match="unknown projection"):
        await logic.list(None, "does_not_exist")
    with pytest.raises(ValueError, match="unsupported sort_ts"):
        await logic.list(None, "health_current", sort_ts="bogus")


@pytest.mark.asyncio
async def test_list_passthrough_and_cursor_shape():
    fake = FakeRepo()
    logic = ProjectionLogic(fake)
    fake_uow = types.SimpleNamespace(session=None)
    result = await logic.list(
        fake_uow, "health_current",
        after_id=42, after_as_of=FIXED, limit=50,
        filters={"metric_name": "executions"},
    )
    assert result == {"rows": [], "next_cursor": None, "has_more": False}
    assert fake.calls == [
        (
            "health_current",
            {
                "after_id": 42,
                "after_as_of": FIXED,
                "limit": 50,
                "sort_ts": "as_of",
                "metric_name": "executions",
            },
        )
    ]
