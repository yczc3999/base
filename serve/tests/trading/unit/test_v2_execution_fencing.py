"""WP-05 execution/heartbeat leader fencing unit tests（Checkpoint B）。

用忠实 fake repo 验证：双 leader（EXECUTION + HEARTBEAT）、lease expiry/takeover、
每个 side effect fencing、旧 owner economic effect=0、迟到 ack/heartbeat 只抛
STALE_FENCE_REJECTED 且不改 current 状态。真实 PostgreSQL 并发由
``integration/test_v2_execution_reservations_fencing.py`` 覆盖。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.logics.trading.execution import (
    ExecutionLeaseLogic,
    LeaseError,
    StaleFenceError,
)


class _FakeLeaseRepo:
    """忠实模拟 execution_leases 表语义（含 fencing 单调 + 版本 CAS）。"""

    def __init__(self):
        self.leases: dict[tuple[int, str], dict] = {}
        self._next = 1

    async def insert_lease(self, session, *, account_id, lease_role, owner, lease_until):
        key = (account_id, lease_role)
        if key in self.leases:
            return None
        lease = {
            "id": self._next, "account_id": account_id, "lease_role": lease_role,
            "owner": owner, "lease_until": lease_until, "fencing_token": 1, "version": 1,
        }
        self._next += 1
        self.leases[key] = lease
        return dict(lease)

    async def get_lease(self, session, *, account_id, lease_role, for_update=False):
        lease = self.leases.get((account_id, lease_role))
        return dict(lease) if lease else None

    async def get_active_lease_fence(
        self,
        session,
        *,
        account_id,
        lease_role,
        owner,
        fencing_token,
        for_update=True,
    ):
        lease = self.leases.get((account_id, lease_role))
        if (
            lease is None
            or lease["owner"] != owner
            or lease["fencing_token"] != fencing_token
            or lease["lease_until"] <= datetime.now(timezone.utc)
        ):
            return None
        return dict(lease)

    async def renew_lease(self, session, *, account_id, lease_role, owner, lease_until,
                          fencing_token):
        lease = self.leases.get((account_id, lease_role))
        if lease is None or lease["owner"] != owner or lease["fencing_token"] != fencing_token:
            return False
        if lease["lease_until"] <= datetime.now(timezone.utc):
            return False
        lease["lease_until"] = lease_until
        lease["version"] += 1
        return True

    async def takeover_lease(self, session, *, account_id, lease_role, owner, lease_until,
                             expected_version):
        lease = self.leases.get((account_id, lease_role))
        if lease is None or lease["version"] != expected_version:
            return False
        if lease["lease_until"] > datetime.now(timezone.utc):
            return False
        lease["owner"] = owner
        lease["lease_until"] = lease_until
        lease["fencing_token"] += 1
        lease["version"] += 1
        return True

    async def release_lease(self, session, *, account_id, lease_role, owner, fencing_token):
        lease = self.leases.get((account_id, lease_role))
        if lease is None or lease["owner"] != owner or lease["fencing_token"] != fencing_token:
            return False
        lease["lease_until"] = datetime.now(timezone.utc)
        lease["version"] += 1
        return True


class _UoW:
    def __init__(self, repo):
        self.session = SimpleNamespace()
        self.repo = repo


def _logic(repo):
    return ExecutionLeaseLogic(execution=repo)


def _acquire(logic, uow, *, owner, role="EXECUTION", ttl=60):
    import asyncio

    return asyncio.run(logic.acquire_lease(
        uow, account_id=1, lease_role=role, owner=owner, ttl_s=ttl,
    ))


def test_dual_leader_execution_and_heartbeat():
    repo = _FakeLeaseRepo()
    uow = _UoW(repo)
    logic = _logic(repo)
    ex = _acquire(logic, uow, owner="leader-A", role="EXECUTION")
    hb = _acquire(logic, uow, owner="leader-B", role="HEARTBEAT")
    assert ex["fencing_token"] == 1
    assert hb["fencing_token"] == 1
    assert repo.leases[(1, "EXECUTION")]["owner"] == "leader-A"
    assert repo.leases[(1, "HEARTBEAT")]["owner"] == "leader-B"


def test_second_execution_leader_busy():
    repo = _FakeLeaseRepo()
    uow = _UoW(repo)
    logic = _logic(repo)
    _acquire(logic, uow, owner="leader-A", role="EXECUTION")
    with pytest.raises(LeaseError, match="lease_busy"):
        _acquire(logic, uow, owner="leader-B", role="EXECUTION")


def test_same_owner_renew_keeps_token():
    repo = _FakeLeaseRepo()
    uow = _UoW(repo)
    logic = _logic(repo)
    lease = _acquire(logic, uow, owner="leader-A", role="EXECUTION", ttl=60)
    import asyncio

    # 手动续期（token 不变）
    ok = asyncio.run(repo.renew_lease(
        uow.session, account_id=1, lease_role="EXECUTION", owner="leader-A",
        lease_until=datetime.now(timezone.utc) + timedelta(seconds=60),
        fencing_token=lease["fencing_token"],
    ))
    assert ok
    assert repo.leases[(1, "EXECUTION")]["fencing_token"] == 1


def test_lease_expiry_takeover_increments_fence():
    repo = _FakeLeaseRepo()
    uow = _UoW(repo)
    logic = _logic(repo)
    _acquire(logic, uow, owner="leader-A", role="EXECUTION", ttl=60)
    # 让 leader-A 的租约过期
    repo.leases[(1, "EXECUTION")]["lease_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    lease_b = _acquire(logic, uow, owner="leader-B", role="EXECUTION", ttl=60)
    assert lease_b["owner"] == "leader-B"
    assert lease_b["fencing_token"] == 2  # 单调递增
    assert repo.leases[(1, "EXECUTION")]["fencing_token"] == 2


def test_stale_owner_fence_rejected_after_takeover():
    repo = _FakeLeaseRepo()
    uow = _UoW(repo)
    logic = _logic(repo)
    lease_a = _acquire(logic, uow, owner="leader-A", role="EXECUTION", ttl=60)
    repo.leases[(1, "EXECUTION")]["lease_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    _acquire(logic, uow, owner="leader-B", role="EXECUTION", ttl=60)
    import asyncio

    # 旧 owner A 的 fence 校验必须失败
    with pytest.raises(StaleFenceError, match="stale_fence_rejected"):
        asyncio.run(logic.assert_fence(
            uow,
            account_id=1,
            lease_role="EXECUTION",
            owner="leader-A",
            token=lease_a["fencing_token"],
        ))
    # 新 owner B 的 fence 校验通过
    asyncio.run(logic.assert_fence(
        uow, account_id=1, lease_role="EXECUTION", owner="leader-B", token=2,
    ))


def test_old_owner_renew_is_stale_and_no_effect():
    repo = _FakeLeaseRepo()
    uow = _UoW(repo)
    logic = _logic(repo)
    lease_a = _acquire(logic, uow, owner="leader-A", role="EXECUTION", ttl=60)
    repo.leases[(1, "EXECUTION")]["lease_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    _acquire(logic, uow, owner="leader-B", role="EXECUTION", ttl=60)
    before = dict(repo.leases[(1, "EXECUTION")])
    import asyncio

    with pytest.raises(StaleFenceError, match="stale_fence_rejected"):
        asyncio.run(logic.renew_lease(
            uow, account_id=1, lease_role="EXECUTION", owner="leader-A",
            fencing_token=lease_a["fencing_token"], ttl_s=60,
        ))
    after = repo.leases[(1, "EXECUTION")]
    assert after["owner"] == before["owner"] == "leader-B"
    assert after["fencing_token"] == before["fencing_token"] == 2
    # 旧 owner economic effect = 0：current 状态未被修改


def test_late_ack_and_heartbeat_only_stale_fence_rejected():
    repo = _FakeLeaseRepo()
    uow = _UoW(repo)
    logic = _logic(repo)
    lease_a = _acquire(logic, uow, owner="leader-A", role="HEARTBEAT", ttl=60)
    repo.leases[(1, "HEARTBEAT")]["lease_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    _acquire(logic, uow, owner="leader-B", role="HEARTBEAT", ttl=60)
    before = dict(repo.leases[(1, "HEARTBEAT")])
    import asyncio

    # 迟到 ack / heartbeat 只记录 STALE_FENCE_REJECTED，不覆盖 latest heartbeat/current 状态
    with pytest.raises(StaleFenceError, match="stale_fence_rejected"):
        asyncio.run(logic.renew_lease(
            uow, account_id=1, lease_role="HEARTBEAT", owner="leader-A",
            fencing_token=lease_a["fencing_token"], ttl_s=60,
        ))
    after = repo.leases[(1, "HEARTBEAT")]
    assert after["owner"] == before["owner"] == "leader-B"
    assert after["lease_until"] == before["lease_until"]
    assert after["fencing_token"] == before["fencing_token"]


def test_release_lease_expires_not_deletes_preserving_fence():
    repo = _FakeLeaseRepo()
    uow = _UoW(repo)
    logic = _logic(repo)
    lease = _acquire(logic, uow, owner="leader-A", role="EXECUTION", ttl=60)
    import asyncio

    ok = asyncio.run(repo.release_lease(
        uow.session, account_id=1, lease_role="EXECUTION",
        owner="leader-A", fencing_token=lease["fencing_token"],
    ))
    assert ok
    # 行保留、过期；下一 leader 必须 takeover（token 递增），而非从 1 重新开始
    assert repo.leases[(1, "EXECUTION")]["lease_until"] <= datetime.now(timezone.utc)
    lease_b = _acquire(logic, uow, owner="leader-B", role="EXECUTION", ttl=60)
    assert lease_b["fencing_token"] == 2
