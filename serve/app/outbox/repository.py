"""Outbox Repository（WP-01A-02，Checkpoint D）。

所有写方法接收现有 Session/UoW，**绝不 commit**；enqueue 与业务事实同一事务。
claim 使用 ``FOR UPDATE SKIP LOCKED``；状态转换以 expected state/lease token/fencing
条件更新，受影响行数不为 1 即返回 False（调用方映射为固定 conflict reason）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

CONFLICT_NOT_OWNED = "outbox_not_owned_or_wrong_state"
CONFLICT_NOT_PENDING = "outbox_not_pending"
CONFLICT_NO_SUCH_ROW = "outbox_no_such_row"
CONFLICT_DUPLICATE_COMPLETION = "outbox_duplicate_completion"
CONFLICT_IDEMPOTENCY_KEY_REUSED = "outbox_idempotency_key_reused"

# status 常量（与模型 CHECK 一致）
STATUS_PENDING = "PENDING"
STATUS_DISPATCHED = "DISPATCHED"
STATUS_COMPLETED = "COMPLETED"
STATUS_DEAD = "DEAD"


class OutboxConflictError(RuntimeError):
    """固定 conflict reason；不含 DSN / secret。"""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def new_lease_token() -> str:
    return uuid.uuid4().hex


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class OutboxRepository:
    """基于 SQLAlchemy core 的 Outbox 存储操作。"""

    # ---- 写（业务事务内 enqueue）----

    async def enqueue(self, session: AsyncSession, env) -> None:
        """enqueue 与业务事实同一事务：绝不 commit。"""
        env.validate()
        claim = await session.execute(
            text(
                "INSERT INTO trading.idempotency_claims (scope, key, owner) "
                "VALUES ('outbox', :key, :owner) "
                "ON CONFLICT (scope, key) DO NOTHING RETURNING owner"
            ),
            {"key": env.idempotency_key, "owner": env.event_id},
        )
        claimed_owner = claim.scalar_one_or_none()
        if claimed_owner is None:
            claimed_owner = (
                await session.execute(
                    text(
                        "SELECT owner FROM trading.idempotency_claims "
                        "WHERE scope='outbox' AND key=:key FOR UPDATE"
                    ),
                    {"key": env.idempotency_key},
                )
            ).scalar_one()
        if claimed_owner != env.event_id:
            raise OutboxConflictError(CONFLICT_IDEMPOTENCY_KEY_REUSED)

        # 同一 envelope 的事务重试是幂等 no-op；同 key 不同内容已由 claim 拒绝。
        await session.execute(
            text(
                "INSERT INTO trading.transactional_outbox "
                "(event_id, topic, schema_version, aggregate_type, aggregate_id, "
                " idempotency_key, release_manifest_id, priority, payload, artifact_ref, "
                " status, attempt, available_at, deadline) "
                "VALUES (:event_id, :topic, :schema_version, :aggregate_type, :aggregate_id, "
                " :idempotency_key, :release_manifest_id, :priority, :payload, :artifact_ref, "
                " 'PENDING', 0, :available_at, :deadline) "
                "ON CONFLICT (event_id) DO NOTHING"
            ).bindparams(bindparam("payload", type_=JSONB())),
            {
                "event_id": env.event_id,
                "topic": env.topic,
                "schema_version": env.schema_version,
                "aggregate_type": env.aggregate_type,
                "aggregate_id": env.aggregate_id,
                "idempotency_key": env.idempotency_key,
                "release_manifest_id": env.release_manifest_id,
                "priority": env.priority,
                "payload": env.payload,
                "artifact_ref": env.artifact_ref,
                "available_at": env.available_at or datetime.now(timezone.utc),
                "deadline": env.deadline,
            },
        )

    async def outbox_exists(self, session: AsyncSession, event_id: str) -> bool:
        row = (await session.execute(
            text("SELECT 1 FROM trading.transactional_outbox WHERE event_id=:e"),
            {"e": event_id},
        )).first()
        return row is not None

    # ---- claim（publisher：短事务内锁行 + 设 lease）----

    async def claim(
        self,
        session: AsyncSession,
        owner: str,
        batch_size: int,
        visibility_seconds: int,
    ) -> list[dict[str, Any]]:
        """``FOR UPDATE SKIP LOCKED`` 认领 PENDING 且 available 的行并设 lease。

        返回带 lease_owner/lease_token/visibility_deadline 的行 dict；调用方负责 commit
        后对外发布，发布失败时行保持 PENDING + lease（visibility 过期后可被重新认领）。
        """
        if isinstance(batch_size, bool) or batch_size <= 0:
            raise ValueError("outbox_batch_size_invalid")
        if isinstance(visibility_seconds, bool) or visibility_seconds <= 0:
            raise ValueError("outbox_visibility_invalid")
        result = await session.execute(
            text(
                "SELECT id, event_id, topic, schema_version, aggregate_type, aggregate_id, "
                "       idempotency_key, priority, payload, artifact_ref, release_manifest_id, "
                "       deadline, available_at, attempt "
                "FROM trading.transactional_outbox "
                "WHERE status = 'PENDING' AND available_at <= now() "
                "  AND (deadline IS NULL OR deadline > now()) "
                "  AND (visibility_deadline IS NULL OR visibility_deadline < now()) "
                "ORDER BY available_at, id "
                "LIMIT :batch FOR UPDATE SKIP LOCKED"
            ),
            {"batch": batch_size},
        )
        rows = _rows(result)
        vis = datetime.now(timezone.utc) + timedelta(seconds=visibility_seconds)
        for row in rows:
            token = new_lease_token()
            res = await session.execute(
                text(
                    "UPDATE trading.transactional_outbox "
                    "SET lease_owner=:owner, lease_token=:token, visibility_deadline=:vis "
                    "WHERE id=:id AND status='PENDING'"
                ),
                {"owner": owner, "token": token, "vis": vis, "id": row["id"]},
            )
            row["lease_owner"] = owner
            row["lease_token"] = token
            row["visibility_deadline"] = vis
        return rows

    # ---- 状态转换（条件更新，影响行数 !=1 → False）----

    async def mark_dispatched(
        self, session: AsyncSession, outbox_id: int, owner: str, token: str
    ) -> bool:
        """PENDING → DISPATCHED（校验 lease owner/token）。"""
        res = await session.execute(
            text(
                "UPDATE trading.transactional_outbox "
                "SET status='DISPATCHED' "
                "WHERE id=:id AND status='PENDING' AND lease_owner=:o AND lease_token=:t"
            ),
            {"id": outbox_id, "o": owner, "t": token},
        )
        return res.rowcount == 1

    async def requeue(
        self,
        session: AsyncSession,
        outbox_id: int,
        owner: str,
        token: str,
        next_available: datetime,
    ) -> bool:
        """把超 visibility 的已认领/已投递行放回 PENDING 并递增 attempt。"""
        res = await session.execute(
            text(
                "UPDATE trading.transactional_outbox "
                "SET status='PENDING', attempt=attempt+1, available_at=:na, "
                "    lease_owner=NULL, lease_token=NULL, visibility_deadline=NULL, "
                "    error_reason='visibility_expired' "
                "WHERE id=:id AND lease_owner=:o AND lease_token=:t "
                "  AND status IN ('PENDING','DISPATCHED')"
            ),
            {"id": outbox_id, "o": owner, "t": token, "na": next_available},
        )
        return res.rowcount == 1

    async def mark_dead(self, session: AsyncSession, outbox_id: int, reason: str) -> bool:
        res = await session.execute(
            text(
                "UPDATE trading.transactional_outbox "
                "SET status='DEAD', error_reason=:reason, error_at=now() "
                "WHERE id=:id AND status IN ('PENDING','DISPATCHED')"
            ),
            {"id": outbox_id, "reason": reason},
        )
        return res.rowcount == 1

    async def complete(self, session: AsyncSession, outbox_id: int) -> bool:
        """热表收敛：DISPATCHED/PENDING → COMPLETED。"""
        res = await session.execute(
            text(
                "UPDATE trading.transactional_outbox "
                "SET status='COMPLETED', lease_owner=NULL, lease_token=NULL "
                "WHERE id=:id AND status IN ('PENDING','DISPATCHED')"
            ),
            {"id": outbox_id},
        )
        return res.rowcount == 1

    # ---- completion / history（consumer 单事务）----

    async def has_completion(self, session: AsyncSession, consumer: str, idempotency_key: str) -> bool:
        row = (await session.execute(
            text(
                "SELECT 1 FROM trading.job_completions "
                "WHERE consumer=:c AND idempotency_key=:k"
            ),
            {"c": consumer, "k": idempotency_key},
        )).first()
        return row is not None

    async def lock_completion_key(
        self, session: AsyncSession, consumer: str, idempotency_key: str
    ) -> None:
        """事务级 DB fencing：同一逻辑 handler/key 只有一个事务可执行业务 effect。

        Redis lease 只用于削峰；正确性由本 advisory-xact lock + completion UNIQUE 保证。
        """
        lock_key = f"{consumer}\x1f{idempotency_key}"
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": lock_key},
        )

    async def insert_completion(
        self, session: AsyncSession, consumer: str, idempotency_key: str, outcome: str
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.job_completions (consumer, idempotency_key, outcome) "
                "VALUES (:c, :k, :o)"
            ),
            {"c": consumer, "k": idempotency_key, "o": outcome},
        )

    async def insert_history(
        self,
        session: AsyncSession,
        *,
        outbox_event_id: str,
        outbox_id: int,
        status: str,
        consumer: str,
        attempt: int,
        error_reason: str | None,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.outbox_delivery_history "
                "(outbox_event_id, outbox_id, completed_at, status, consumer, attempt, error_reason) "
                "VALUES (:e, :oid, now(), :s, :c, :a, :r)"
            ),
            {
                "e": outbox_event_id,
                "oid": outbox_id,
                "s": status,
                "c": consumer,
                "a": attempt,
                "r": error_reason,
            },
        )

    # ---- consumer / sweeper 补充 ----

    async def get_by_event_id(
        self, session: AsyncSession, event_id: str, *, for_update: bool = False
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        result = await session.execute(
            text(
                "SELECT id, event_id, topic, schema_version, aggregate_type, aggregate_id, "
                "       idempotency_key, priority, payload, artifact_ref, release_manifest_id, "
                "       deadline, available_at, attempt, status, lease_owner, lease_token, "
                "       visibility_deadline "
                "FROM trading.transactional_outbox WHERE event_id=:e" + suffix
            ),
            {"e": event_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def retry_by_event_id(
        self, session: AsyncSession, event_id: str, next_available: datetime
    ) -> bool:
        """consumer 处理失败：放回 PENDING 并递增 attempt（不依赖 publisher lease）。"""
        res = await session.execute(
            text(
                "UPDATE trading.transactional_outbox "
                "SET status='PENDING', attempt=attempt+1, available_at=:na, "
                "    lease_owner=NULL, lease_token=NULL, visibility_deadline=NULL, "
                "    error_reason='handler_failure' "
                "WHERE event_id=:e AND status IN ('PENDING','DISPATCHED')"
            ),
            {"e": event_id, "na": next_available},
        )
        return res.rowcount == 1

    async def dead_by_event_id(
        self, session: AsyncSession, event_id: str, reason: str
    ) -> bool:
        """置 DEAD 并记录本次失败为一次 attempt。"""
        res = await session.execute(
            text(
                "UPDATE trading.transactional_outbox "
                "SET status='DEAD', attempt=attempt+1, error_reason=:reason, error_at=now(), "
                "    lease_owner=NULL, lease_token=NULL, visibility_deadline=NULL "
                "WHERE event_id=:e AND status IN ('PENDING','DISPATCHED')"
            ),
            {"e": event_id, "reason": reason},
        )
        return res.rowcount == 1

    async def claim_expired(
        self,
        session: AsyncSession,
        batch_size: int,
        next_available_offset_s: int,
        max_attempts: int,
    ) -> list[dict[str, Any]]:
        """sweeper：认领 visibility 已过期且未 completion 的行（PENDING/DISPATCHED）。

        返回行；调用方按 attempt 决策 requeue/dead。``FOR UPDATE SKIP LOCKED``。
        """
        result = await session.execute(
            text(
                "SELECT id, event_id, topic, attempt, lease_owner, lease_token, deadline "
                "FROM trading.transactional_outbox "
                "WHERE status IN ('PENDING','DISPATCHED') "
                "  AND ((visibility_deadline IS NOT NULL AND visibility_deadline < now() "
                "        AND available_at <= now()) "
                "       OR (status='PENDING' AND deadline IS NOT NULL AND deadline <= now())) "
                "ORDER BY COALESCE(visibility_deadline, deadline), id "
                "LIMIT :batch FOR UPDATE SKIP LOCKED"
            ),
            {"batch": batch_size},
        )
        return _rows(result)

    async def count_pending(self, session: AsyncSession) -> int:
        row = (await session.execute(
            text(
                "SELECT count(*) FROM trading.transactional_outbox WHERE status='PENDING'"
            )
        )).first()
        return int(row[0])

    async def count_status(self, session: AsyncSession, status: str) -> int:
        row = (await session.execute(
            text("SELECT count(*) FROM trading.transactional_outbox WHERE status=:s"),
            {"s": status},
        )).first()
        return int(row[0])
