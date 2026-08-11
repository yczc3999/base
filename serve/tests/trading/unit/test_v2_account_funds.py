"""WP-05 funds / reservation logic unit tests（Checkpoint B）。

用忠实 fake repo 验证 funds 恒等式、reservation 状态机、local→provider 原子转移、
UNKNOWN 保留与 release/consume 精确数量。真实 PostgreSQL 并发/回滚由
``integration/test_v2_vault_accounts_funds.py`` 与
``integration/test_v2_execution_reservations_fencing.py`` 覆盖。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.logics.trading.portfolio import PortfolioLogic

ZERO = Decimal("0")


class _FakeExecRepo:
    """忠实模拟 account_funds_current + capital_reservations 的 SQL 语义。"""

    def __init__(self, confirmed=Decimal("100")):
        self.funds = {
            "id": 1, "account_id": 1, "asset_key": "USD", "confirmed": confirmed,
            "provider_reserved": ZERO, "local_reserved": ZERO, "available": confirmed,
            "source_snapshot_id": 1, "reconcile_watermark": 1, "version": 1,
        }
        self.reservations: dict[int, dict] = {}
        self._next_res = 1

    @staticmethod
    def _recompute(f):
        f["available"] = f["confirmed"] - f["provider_reserved"] - f["local_reserved"]

    async def get_funds(self, session, *, account_id, asset_key, for_update=False):
        return dict(self.funds) if self.funds else None

    async def get_reservation_by_idempotency(self, session, *, account_id, asset_key,
                                             idempotency_key, for_update=False):
        for r in self.reservations.values():
            if (r["account_id"], r["asset_key"], r["idempotency_key"]) == (account_id, asset_key, idempotency_key):
                return dict(r)
        return None

    async def get_reservation_by_key(self, session, *, reservation_key):
        for r in self.reservations.values():
            if r["reservation_key"] == reservation_key:
                return dict(r)
        return None

    async def reserve_funds_update(self, session, *, account_id, asset_key, amount):
        f = self.funds
        if f["available"] < amount:
            return False
        f["local_reserved"] += amount
        self._recompute(f)
        f["version"] += 1
        return True

    async def insert_reservation(self, session, *, reservation_key, intent_id, account_id,
                                 asset_key, amount, idempotency_key):
        existing = await self.get_reservation_by_idempotency(
            session, account_id=account_id, asset_key=asset_key, idempotency_key=idempotency_key
        )
        if existing:
            return existing
        r = {
            "id": self._next_res, "reservation_key": reservation_key, "intent_id": intent_id,
            "account_id": account_id, "asset_key": asset_key, "amount": amount,
            "idempotency_key": idempotency_key, "status": "HELD",
        }
        self._next_res += 1
        self.reservations[r["id"]] = r
        return dict(r)

    async def get_reservation(self, session, *, reservation_id, for_update=False):
        r = self.reservations.get(reservation_id)
        return dict(r) if r else None

    async def advance_reservation(self, session, *, reservation_id, new_status):
        r = self.reservations.get(reservation_id)
        if r is None or r["status"] == new_status:
            return False
        old = r["status"]
        allowed = (
            (old == "HELD" and new_status in ("PROVIDER_BOUND", "UNKNOWN", "RELEASED"))
            or (old == "UNKNOWN" and new_status in ("PROVIDER_BOUND", "CONSUMED"))
            or (old == "PROVIDER_BOUND" and new_status in ("CONSUMED", "RELEASED"))
        )
        if not allowed:
            return False
        r["status"] = new_status
        return True

    async def transfer_funds_local_to_provider(self, session, *, account_id, asset_key, amount):
        f = self.funds
        if f["local_reserved"] < amount:
            return False
        f["local_reserved"] -= amount
        f["provider_reserved"] += amount
        self._recompute(f)
        f["version"] += 1
        return True

    async def release_funds_local(self, session, *, account_id, asset_key, amount):
        f = self.funds
        if f["local_reserved"] < amount:
            return False
        f["local_reserved"] -= amount
        self._recompute(f)
        f["version"] += 1
        return True

    async def release_funds_provider(self, session, *, account_id, asset_key, amount):
        f = self.funds
        if f["provider_reserved"] < amount:
            return False
        f["provider_reserved"] -= amount
        self._recompute(f)
        f["version"] += 1
        return True


class _UoW:
    def __init__(self, repo):
        self.session = SimpleNamespace()
        self.repo = repo


def _logic(repo):
    return PortfolioLogic(execution=repo)


def _reserve(logic, uow, *, key="r1", intent=1, amount=Decimal("70"), ik="ik-1"):
    import asyncio

    return asyncio.run(logic.reserve_funds(
        uow, reservation_key=key, intent_id=intent, account_id=1, asset_key="USD",
        amount=amount, idempotency_key=ik,
    ))


def test_funds_identity_holds_after_reserve():
    repo = _FakeExecRepo(confirmed=Decimal("100"))
    uow = _UoW(repo)
    logic = _logic(repo)
    _reserve(logic, uow, amount=Decimal("30"))
    f = repo.funds
    assert f["local_reserved"] == Decimal("30")
    assert f["available"] == Decimal("70")
    assert f["available"] == f["confirmed"] - f["provider_reserved"] - f["local_reserved"]


def test_two_reservations_cannot_exceed_funds():
    repo = _FakeExecRepo(confirmed=Decimal("100"))
    uow = _UoW(repo)
    logic = _logic(repo)
    _reserve(logic, uow, key="r1", amount=Decimal("70"), ik="ik-1")
    with pytest.raises(RuntimeError, match="funds_insufficient"):
        _reserve(logic, uow, key="r2", amount=Decimal("70"), ik="ik-2")
    assert repo.funds["local_reserved"] == Decimal("70")
    assert repo.funds["available"] == Decimal("30")


def test_reservation_idempotent_returns_existing():
    repo = _FakeExecRepo(confirmed=Decimal("100"))
    uow = _UoW(repo)
    logic = _logic(repo)
    r1 = _reserve(logic, uow, key="r1", amount=Decimal("30"), ik="ik-same")
    r2 = _reserve(logic, uow, key="r1", amount=Decimal("30"), ik="ik-same")
    assert r1["id"] == r2["id"]
    # 幂等重试不双计
    assert repo.funds["local_reserved"] == Decimal("30")


def test_unknown_keeps_local_reserved_and_not_releasable():
    repo = _FakeExecRepo(confirmed=Decimal("100"))
    uow = _UoW(repo)
    logic = _logic(repo)
    res = _reserve(logic, uow, amount=Decimal("40"))
    import asyncio

    asyncio.run(logic.mark_reservation_unknown(uow, reservation_id=res["id"]))
    assert repo.reservations[res["id"]]["status"] == "UNKNOWN"
    assert repo.funds["local_reserved"] == Decimal("40")  # UNKNOWN 保留
    with pytest.raises(RuntimeError, match="reservation_unknown_not_releasable"):
        asyncio.run(logic.release_reservation(uow, reservation_id=res["id"]))
    assert repo.funds["local_reserved"] == Decimal("40")


def test_ack_transfers_local_to_provider_no_leak_or_double():
    repo = _FakeExecRepo(confirmed=Decimal("100"))
    uow = _UoW(repo)
    logic = _logic(repo)
    res = _reserve(logic, uow, amount=Decimal("40"))
    import asyncio

    asyncio.run(logic.ack_reservation(uow, reservation_id=res["id"]))
    f = repo.funds
    assert f["local_reserved"] == ZERO
    assert f["provider_reserved"] == Decimal("40")
    assert f["available"] == Decimal("60")
    assert repo.reservations[res["id"]]["status"] == "PROVIDER_BOUND"
    # 恒等式保持：无漏计/双计
    assert f["available"] == f["confirmed"] - f["provider_reserved"] - f["local_reserved"]


def test_release_provider_bound_releases_exact_provider():
    repo = _FakeExecRepo(confirmed=Decimal("100"))
    uow = _UoW(repo)
    logic = _logic(repo)
    res = _reserve(logic, uow, amount=Decimal("25"))
    import asyncio

    asyncio.run(logic.ack_reservation(uow, reservation_id=res["id"]))
    asyncio.run(logic.release_reservation(uow, reservation_id=res["id"]))
    f = repo.funds
    assert f["provider_reserved"] == ZERO
    assert f["local_reserved"] == ZERO
    assert f["available"] == Decimal("100")
    assert repo.reservations[res["id"]]["status"] == "RELEASED"


def test_consume_provider_bound_releases_exact_provider():
    repo = _FakeExecRepo(confirmed=Decimal("100"))
    uow = _UoW(repo)
    logic = _logic(repo)
    res = _reserve(logic, uow, amount=Decimal("60"))
    import asyncio

    asyncio.run(logic.ack_reservation(uow, reservation_id=res["id"]))
    asyncio.run(logic.consume_reservation(uow, reservation_id=res["id"]))
    f = repo.funds
    assert f["provider_reserved"] == ZERO
    assert f["available"] == Decimal("100")
    assert repo.reservations[res["id"]]["status"] == "CONSUMED"


def test_consume_unknown_releases_local_exact():
    repo = _FakeExecRepo(confirmed=Decimal("100"))
    uow = _UoW(repo)
    logic = _logic(repo)
    res = _reserve(logic, uow, amount=Decimal("55"))
    import asyncio

    asyncio.run(logic.mark_reservation_unknown(uow, reservation_id=res["id"]))
    asyncio.run(logic.consume_reservation(uow, reservation_id=res["id"]))
    f = repo.funds
    assert f["local_reserved"] == ZERO
    assert f["available"] == Decimal("100")
    assert repo.reservations[res["id"]]["status"] == "CONSUMED"


def test_release_held_releases_local_exact():
    repo = _FakeExecRepo(confirmed=Decimal("100"))
    uow = _UoW(repo)
    logic = _logic(repo)
    res = _reserve(logic, uow, amount=Decimal("33"))
    import asyncio

    asyncio.run(logic.release_reservation(uow, reservation_id=res["id"]))
    f = repo.funds
    assert f["local_reserved"] == ZERO
    assert f["available"] == Decimal("100")
    assert repo.reservations[res["id"]]["status"] == "RELEASED"


def test_illegal_transition_rejected():
    repo = _FakeExecRepo(confirmed=Decimal("100"))
    uow = _UoW(repo)
    logic = _logic(repo)
    res = _reserve(logic, uow, amount=Decimal("10"))
    import asyncio

    # HELD → CONSUMED 非法（必须先 ACK/UNKNOWN）
    assert asyncio.run(repo.advance_reservation(
        uow.session, reservation_id=res["id"], new_status="CONSUMED")) is False
    # UNKNOWN → RELEASED 非法
    asyncio.run(logic.mark_reservation_unknown(uow, reservation_id=res["id"]))
    assert asyncio.run(repo.advance_reservation(
        uow.session, reservation_id=res["id"], new_status="RELEASED")) is False
    assert repo.reservations[res["id"]]["status"] == "UNKNOWN"
