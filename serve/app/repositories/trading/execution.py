"""Shadow execution Repository（WP-03 Checkpoint C）。

只拥有 SQL：execution 生命周期、position projection（乐观锁）、position lots。
绝不 commit、不调用网络、不做业务判断。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


def _assert_exact_material(
    actual: dict[str, Any],
    expected: dict[str, Any],
    *,
    error_prefix: str,
    decimal_fields: frozenset[str] = frozenset(),
) -> None:
    """Reject an idempotency-key collision with different immutable material."""
    for field, value in expected.items():
        actual_value = actual[field]
        if field in decimal_fields:
            actual_value = Decimal(str(actual_value))
            value = Decimal(str(value))
        if actual_value != value:
            raise RuntimeError(f"{error_prefix}_idempotency_mismatch:{field}")


class ExecutionRepository:
    """shadow execution SQL；不持有状态。"""

    async def insert_execution(
        self,
        session: AsyncSession,
        *,
        execution_key: str,
        economic_action_intent_id: int,
        action_set_leg_id: int,
        contract_spec_id: int,
        token_id: int,
        fill_role: str,
        quantity: Any,
        portfolio_namespace: str,
        quote_checkpoint_id: int | None = None,
        account_id: int | None = None,
        envelope_id: int | None = None,
        order_id: int | None = None,
        trade_id: int | None = None,
    ) -> dict[str, Any]:
        """Claim an execution key and exact-match an existing retry.

        The returned mapping contains ``inserted``.  A reused key with different immutable
        material is rejected instead of being rebound to the old execution.
        WP-05 Checkpoint C：真实 CLOB fill 允许 ``quote_checkpoint_id`` 为 NULL，并写入
        account/envelope/order/trade lineage。
        """
        result = await session.execute(
            text(
                "INSERT INTO trading.executions "
                "(execution_key, economic_action_intent_id, action_set_leg_id, contract_spec_id, "
                " token_id, fill_role, quantity, portfolio_namespace, quote_checkpoint_id, "
                " account_id, envelope_id, order_id, trade_id) "
                "VALUES (:k, :i, :leg, :cs, :tk, :fr, :q, :ns, :checkpoint, "
                " :acct, :env, :o, :t) "
                "ON CONFLICT (execution_key) DO NOTHING RETURNING *"
            ),
            {"k": execution_key, "i": economic_action_intent_id, "leg": action_set_leg_id,
             "cs": contract_spec_id, "tk": token_id, "fr": fill_role, "q": quantity,
             "ns": portfolio_namespace, "checkpoint": quote_checkpoint_id,
             "acct": account_id, "env": envelope_id, "o": order_id, "t": trade_id},
        )
        rows = _rows(result)
        if rows:
            rows[0]["inserted"] = True
            return rows[0]
        existing_result = await session.execute(
            text("SELECT * FROM trading.executions WHERE execution_key=:k FOR UPDATE"),
            {"k": execution_key},
        )
        existing_rows = _rows(existing_result)
        if not existing_rows:
            raise RuntimeError("execution_claim_lost")
        existing = existing_rows[0]
        expected = {
            "economic_action_intent_id": economic_action_intent_id,
            "action_set_leg_id": action_set_leg_id,
            "contract_spec_id": contract_spec_id,
            "token_id": token_id,
            "fill_role": fill_role,
            "quantity": Decimal(str(quantity)),
            "portfolio_namespace": portfolio_namespace,
            "quote_checkpoint_id": quote_checkpoint_id,
            "account_id": account_id,
            "envelope_id": envelope_id,
            "order_id": order_id,
            "trade_id": trade_id,
        }
        _assert_exact_material(
            existing,
            expected,
            error_prefix="execution",
            decimal_fields=frozenset({"quantity"}),
        )
        existing["inserted"] = False
        return existing

    async def fill_material(
        self,
        session: AsyncSession,
        *,
        economic_action_intent_id: int,
        action_set_leg_id: int,
    ) -> dict[str, Any] | None:
        """Load the complete immutable execution chain; no payload economics are trusted."""
        result = await session.execute(
            text(
                "SELECT i.id AS intent_id, i.status AS intent_status, i.ttl_at, "
                "       i.trade_decision_id AS intent_decision_id, i.action_set_id, "
                "       a.trade_decision_id, a.disposition, "
                "       l.id AS leg_id, l.contract_spec_id, l.token_id, l.leg_role, "
                "       l.quantity, l.signed_quantity, "
                "       d.status AS decision_status, d.experiment_variant, d.release_manifest_id, "
                "       pt.token_id AS external_token_id, pt.market_id, fcv.component_id, "
                "       qb.checkpoint_id, qb.checkpoint_received_at, qb.stale_at, "
                "       bc.completeness AS checkpoint_complete, bc.validity AS checkpoint_validity, "
                "       es.status AS execution_spec_status, es.content AS execution_spec, "
                "       cp.status AS permission_status, cp.mode AS permission_mode, "
                "       cp.authorized_capital, cp.kill_switch, cp.capability, "
                "       oc.content AS objective_content "
                "FROM trading.economic_action_intents i "
                "JOIN trading.action_sets a ON a.id=i.action_set_id "
                "JOIN trading.action_set_legs l ON l.action_set_id=a.id AND l.id=:leg "
                "JOIN trading.trade_decisions d ON d.id=a.trade_decision_id "
                "JOIN trading.pm_tokens pt ON pt.id=l.token_id "
                "JOIN trading.forecast_episodes fe ON fe.id=d.episode_id "
                "JOIN trading.forecast_component_versions fcv ON fcv.id=fe.component_version_id "
                "JOIN trading.pm_quote_bindings qb "
                "  ON qb.trade_decision_id=d.id AND qb.token_id=pt.token_id "
                "JOIN trading.pm_book_checkpoints bc "
                "  ON bc.id=qb.checkpoint_id AND bc.received_at=qb.checkpoint_received_at "
                " AND bc.token_id=qb.token_id "
                "JOIN trading.execution_spec_versions es ON es.id=d.execution_spec_version_id "
                "JOIN trading.capital_permission_manifests cp "
                "  ON cp.id=d.capital_permission_manifest_id "
                "JOIN trading.strategy_objective_contracts oc ON oc.id=d.objective_contract_id "
                "WHERE i.id=:intent"
            ),
            {"intent": economic_action_intent_id, "leg": action_set_leg_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def checkpoint_levels(
        self,
        session: AsyncSession,
        *,
        checkpoint_id: int,
        checkpoint_received_at: datetime,
        side: str,
        max_levels: int,
    ) -> list[tuple[Any, Any]]:
        book_side = "ask" if side == "buy" else "bid"
        result = await session.execute(
            text(
                "SELECT price,size FROM trading.pm_book_levels "
                "WHERE checkpoint_id=:checkpoint AND received_at=:received AND side=:side "
                "ORDER BY ordinal, price LIMIT :limit"
            ),
            {"checkpoint": checkpoint_id, "received": checkpoint_received_at,
             "side": book_side, "limit": max_levels},
        )
        return [(row[0], row[1]) for row in result.fetchall()]

    async def acquire_position_lock(
        self,
        session: AsyncSession,
        *,
        portfolio_namespace: str,
        component_id: int | None,
        market_id: int | None,
        contract_spec_id: int,
        token_id: int,
    ) -> None:
        key = f"position:{portfolio_namespace}:{component_id or 0}:{market_id or 0}:{contract_spec_id}:{token_id}"
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )

    async def acquire_execution_lock(
        self, session: AsyncSession, *, economic_action_intent_id: int, action_set_leg_id: int
    ) -> None:
        key = f"execution:{economic_action_intent_id}:{action_set_leg_id}"
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )

    async def execution_for_leg(
        self, session: AsyncSession, *, economic_action_intent_id: int, action_set_leg_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.executions WHERE economic_action_intent_id=:intent "
                "AND action_set_leg_id=:leg ORDER BY id LIMIT 1 FOR UPDATE"
            ),
            {"intent": economic_action_intent_id, "leg": action_set_leg_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def terminalize_execution(
        self,
        session: AsyncSession,
        execution_id: int,
        *,
        status: str,
        filled_quantity: Any,
        vwap: Any,
        fee: Any,
        unfilled_reason: str | None,
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.executions SET status=:s, filled_quantity=:fq, vwap=:vw, "
                "fee=:fee, unfilled_reason=:ur WHERE id=:e AND status='PENDING'"
            ),
            {"e": execution_id, "s": status, "fq": filled_quantity, "vw": vwap,
             "fee": fee, "ur": unfilled_reason},
        )
        return result.rowcount == 1

    async def get_execution(
        self, session: AsyncSession, execution_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.executions WHERE id=:e"),
            {"e": execution_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_execution_by_key(
        self, session: AsyncSession, execution_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.executions WHERE execution_key=:key"),
            {"key": execution_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def insert_position_lot(
        self,
        session: AsyncSession,
        *,
        execution_id: int,
        portfolio_namespace: str,
        contract_spec_id: int,
        token_id: int,
        quantity: Any,
        entry_vwap: Any,
        fill_role: str,
        account_id: int | None = None,
        order_id: int | None = None,
        trade_id: int | None = None,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.position_lots "
                "(execution_id, portfolio_namespace, contract_spec_id, token_id, quantity, "
                " entry_vwap, fill_role, account_id, order_id, trade_id) "
                "VALUES (:e, :ns, :cs, :tk, :q, :vw, :fr, :acct, :o, :t)"
            ),
            {"e": execution_id, "ns": portfolio_namespace, "cs": contract_spec_id,
             "tk": token_id, "q": quantity, "vw": entry_vwap, "fr": fill_role,
             "acct": account_id, "o": order_id, "t": trade_id},
        )

    async def get_position(
        self,
        session: AsyncSession,
        *,
        portfolio_namespace: str,
        contract_spec_id: int,
        token_id: int,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.positions "
                "WHERE portfolio_namespace=:ns AND contract_spec_id=:cs AND token_id=:tk" +
                (" FOR UPDATE" if for_update else "")
            ),
            {"ns": portfolio_namespace, "cs": contract_spec_id, "tk": token_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def upsert_position(
        self,
        session: AsyncSession,
        *,
        portfolio_namespace: str,
        contract_spec_id: int,
        token_id: int,
        market_id: int | None,
        component_id: int | None,
        quantity: Any,
        cost_basis: Any,
        account_id: int | None = None,
        envelope_id: int | None = None,
        order_id: int | None = None,
    ) -> None:
        """乐观锁 upsert：不存在则插入；存在则按 version 条件更新（不覆盖并发写）。"""
        existing = await self.get_position(
            session, portfolio_namespace=portfolio_namespace,
            contract_spec_id=contract_spec_id, token_id=token_id, for_update=True,
        )
        if existing is None:
            await session.execute(
                text(
                    "INSERT INTO trading.positions "
                    "(portfolio_namespace, contract_spec_id, token_id, market_id, component_id, "
                    " quantity, cost_basis, account_id, envelope_id, order_id) "
                    "VALUES (:ns, :cs, :tk, :m, :cmp, :q, :cb, :acct, :env, :o)"
                ),
                {"ns": portfolio_namespace, "cs": contract_spec_id, "tk": token_id,
                 "m": market_id, "cmp": component_id, "q": quantity, "cb": cost_basis,
                 "acct": account_id, "env": envelope_id, "o": order_id},
            )
            return
        if (existing["market_id"] is not None and market_id is not None
                and existing["market_id"] != market_id):
            raise RuntimeError("position_market_mismatch")
        if (existing["component_id"] is not None and component_id is not None
                and existing["component_id"] != component_id):
            raise RuntimeError("position_component_mismatch")
        result = await session.execute(
            text(
                "UPDATE trading.positions SET quantity=:q, cost_basis=:cb, "
                "market_id=COALESCE(market_id,:m), component_id=COALESCE(component_id,:cmp), "
                "version=version+1 "
                "WHERE portfolio_namespace=:ns AND contract_spec_id=:cs AND token_id=:tk "
                "AND version=:old_version"
            ),
            {"ns": portfolio_namespace, "cs": contract_spec_id, "tk": token_id,
             "q": quantity, "cb": cost_basis, "m": market_id, "cmp": component_id,
             "old_version": existing["version"]},
        )
        if result.rowcount != 1:
            raise RuntimeError("position_version_conflict")

    async def position_lots_for(
        self,
        session: AsyncSession,
        *,
        portfolio_namespace: str,
        contract_spec_id: int,
        token_id: int,
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT lot.quantity,lot.entry_vwap,lot.fill_role,lot.created_at,lot.id "
                "FROM trading.position_lots lot WHERE lot.portfolio_namespace=:ns "
                "AND lot.contract_spec_id=:cs AND lot.token_id=:tk "
                "ORDER BY lot.created_at,lot.id"
            ),
            {"ns": portfolio_namespace, "cs": contract_spec_id, "tk": token_id},
        )
        return _rows(result)

    async def positions_for_namespace(
        self, session: AsyncSession, portfolio_namespace: str
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT * FROM trading.positions WHERE portfolio_namespace=:ns "
                "ORDER BY contract_spec_id, token_id"
            ),
            {"ns": portfolio_namespace},
        )
        return _rows(result)

    # ---- WP-05 execution readiness: accounts / funds / reservations / leases ----

    async def insert_account(
        self,
        session: AsyncSession,
        *,
        account_key: str,
        provider: str,
        chain_id: int,
        identity_type: str,
        funder_address: str | None,
        maker_address: str | None,
        signing_identity: str | None,
        wallet_type: str,
        signature_type: str,
        signer_secret_entry_id: int | None,
        signer_secret_version_id: int | None,
        l2_secret_entry_id: int | None,
        l2_secret_version_id: int | None,
        release_manifest_id: int,
        capital_permission_manifest_id: int,
        network_mode: str,
        status: str = "active",
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "INSERT INTO trading.pm_accounts "
                "(account_key, provider, chain_id, identity_type, funder_address, maker_address, "
                " signing_identity, wallet_type, signature_type, signer_secret_entry_id, "
                " signer_secret_version_id, l2_secret_entry_id, l2_secret_version_id, "
                " release_manifest_id, capital_permission_manifest_id, network_mode, status) "
                "VALUES (:k, :provider, :chain, :it, :funder, :maker, :signing, :wallet, "
                " :sigtype, :se, :sev, :le, :lev, :release, :cap, :net, :status) RETURNING *"
            ),
            {
                "k": account_key, "provider": provider, "chain": chain_id, "it": identity_type,
                "funder": funder_address, "maker": maker_address, "signing": signing_identity,
                "wallet": wallet_type, "sigtype": signature_type, "se": signer_secret_entry_id,
                "sev": signer_secret_version_id, "le": l2_secret_entry_id,
                "lev": l2_secret_version_id, "release": release_manifest_id,
                "cap": capital_permission_manifest_id, "net": network_mode, "status": status,
            },
        )
        rows = _rows(result)
        if not rows:
            raise RuntimeError("account_insert_lost")
        return rows[0]

    async def get_account(
        self, session: AsyncSession, *, account_id: int, for_update: bool = False
    ) -> dict[str, Any] | None:
        sql = "SELECT * FROM trading.pm_accounts WHERE id=:a"
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(text(sql), {"a": account_id})
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_account_by_key(
        self, session: AsyncSession, *, account_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.pm_accounts WHERE account_key=:k"),
            {"k": account_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def insert_balance_snapshot(
        self,
        session: AsyncSession,
        *,
        account_id: int,
        asset_key: str,
        spender: str | None,
        balance: Any,
        allowance: Any,
        provider_reserved: Any,
        observed_at: datetime,
        request_hash: str,
        fencing_token: int,
        completeness: str,
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "INSERT INTO trading.pm_balance_allowance_snapshots "
                "(account_id, asset_key, spender, balance, allowance, provider_reserved, "
                " observed_at, request_hash, fencing_token, completeness) "
                "VALUES (:a, :k, :spender, :balance, :allowance, :reserved, :obs, :req, "
                " :fence, :complete) RETURNING *"
            ),
            {
                "a": account_id, "k": asset_key, "spender": spender, "balance": balance,
                "allowance": allowance, "reserved": provider_reserved, "obs": observed_at,
                "req": request_hash, "fence": fencing_token, "complete": completeness,
            },
        )
        rows = _rows(result)
        if not rows:
            raise RuntimeError("balance_snapshot_insert_lost")
        return rows[0]

    async def get_funds(
        self, session: AsyncSession, *, account_id: int, asset_key: str, for_update: bool = False
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT id, account_id, asset_key, confirmed, provider_reserved, local_reserved, "
            "available, source_snapshot_id, reconcile_watermark, version, updated_at, created_at "
            "FROM trading.account_funds_current WHERE account_id=:a AND asset_key=:k"
        )
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(text(sql), {"a": account_id, "k": asset_key})
        rows = _rows(result)
        return rows[0] if rows else None

    async def create_funds(
        self,
        session: AsyncSession,
        *,
        account_id: int,
        asset_key: str,
        confirmed: Any,
        provider_reserved: Any,
        local_reserved: Any,
        available: Any,
        source_snapshot_id: int,
        reconcile_watermark: int,
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "INSERT INTO trading.account_funds_current "
                "(account_id, asset_key, confirmed, provider_reserved, local_reserved, available, "
                " source_snapshot_id, reconcile_watermark) "
                "VALUES (:a, :k, :c, :pr, :lr, :av, :ss, :wm) RETURNING *"
            ),
            {
                "a": account_id, "k": asset_key, "c": confirmed, "pr": provider_reserved,
                "lr": local_reserved, "av": available, "ss": source_snapshot_id, "wm": reconcile_watermark,
            },
        )
        rows = _rows(result)
        if not rows:
            raise RuntimeError("funds_create_lost")
        return rows[0]

    async def upsert_funds_current(
        self,
        session: AsyncSession,
        *,
        account_id: int,
        asset_key: str,
        confirmed: Any,
        provider_reserved: Any,
        local_reserved: Any,
        available: Any,
        source_snapshot_id: int,
        reconcile_watermark: int,
        expected_version: int,
    ) -> bool:
        """CAS 更新 funds 投影；version 不匹配返回 False（不覆盖并发写）。"""
        result = await session.execute(
            text(
                "UPDATE trading.account_funds_current SET "
                "confirmed=:c, provider_reserved=:pr, local_reserved=:lr, available=:av, "
                "source_snapshot_id=:ss, reconcile_watermark=:wm, version=version+1 "
                "WHERE account_id=:a AND asset_key=:k AND version=:old"
            ),
            {
                "a": account_id, "k": asset_key, "c": confirmed, "pr": provider_reserved,
                "lr": local_reserved, "av": available, "ss": source_snapshot_id,
                "wm": reconcile_watermark, "old": expected_version,
            },
        )
        return result.rowcount == 1

    async def reserve_funds_update(
        self, session: AsyncSession, *, account_id: int, asset_key: str, amount: Any
    ) -> bool:
        """条件 UPDATE：仅当 available >= amount 才原子占用 local_reserved。"""
        result = await session.execute(
            text(
                "UPDATE trading.account_funds_current SET "
                "local_reserved = local_reserved + :amt, "
                "available = confirmed - provider_reserved - (local_reserved + :amt), "
                "version = version + 1 "
                "WHERE account_id=:a AND asset_key=:k "
                "AND (confirmed - provider_reserved - local_reserved) >= :amt"
            ),
            {"a": account_id, "k": asset_key, "amt": amount},
        )
        return result.rowcount == 1

    async def transfer_funds_local_to_provider(
        self, session: AsyncSession, *, account_id: int, asset_key: str, amount: Any
    ) -> bool:
        """ACK：同一语句把等额 local_reserved 转入 provider_reserved（恒等式保持）。"""
        result = await session.execute(
            text(
                "UPDATE trading.account_funds_current SET "
                "local_reserved = local_reserved - :amt, "
                "provider_reserved = provider_reserved + :amt, "
                "available = confirmed - (provider_reserved + :amt) - (local_reserved - :amt), "
                "version = version + 1 "
                "WHERE account_id=:a AND asset_key=:k AND local_reserved >= :amt"
            ),
            {"a": account_id, "k": asset_key, "amt": amount},
        )
        return result.rowcount == 1

    async def release_funds_local(
        self, session: AsyncSession, *, account_id: int, asset_key: str, amount: Any
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.account_funds_current SET "
                "local_reserved = local_reserved - :amt, "
                "available = confirmed - provider_reserved - (local_reserved - :amt), "
                "version = version + 1 "
                "WHERE account_id=:a AND asset_key=:k AND local_reserved >= :amt"
            ),
            {"a": account_id, "k": asset_key, "amt": amount},
        )
        return result.rowcount == 1

    async def release_funds_provider(
        self, session: AsyncSession, *, account_id: int, asset_key: str, amount: Any
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.account_funds_current SET "
                "provider_reserved = provider_reserved - :amt, "
                "available = confirmed - (provider_reserved - :amt) - local_reserved, "
                "version = version + 1 "
                "WHERE account_id=:a AND asset_key=:k AND provider_reserved >= :amt"
            ),
            {"a": account_id, "k": asset_key, "amt": amount},
        )
        return result.rowcount == 1

    async def consume_funds_local(
        self, session: AsyncSession, *, account_id: int, asset_key: str, amount: Any
    ) -> bool:
        """Consume a confirmed fill held in the local UNKNOWN bucket.

        Confirmed and reserved decrease together, so spendable ``available`` does not jump
        back up after an economic effect.
        """
        result = await session.execute(
            text(
                "UPDATE trading.account_funds_current SET "
                "confirmed = confirmed - :amt, local_reserved = local_reserved - :amt, "
                "available = (confirmed - :amt) - provider_reserved "
                "            - (local_reserved - :amt), version = version + 1 "
                "WHERE account_id=:a AND asset_key=:k "
                "AND confirmed >= :amt AND local_reserved >= :amt"
            ),
            {"a": account_id, "k": asset_key, "amt": amount},
        )
        return result.rowcount == 1

    async def consume_funds_provider(
        self, session: AsyncSession, *, account_id: int, asset_key: str, amount: Any
    ) -> bool:
        """Consume a confirmed fill held in the provider-bound bucket."""
        result = await session.execute(
            text(
                "UPDATE trading.account_funds_current SET "
                "confirmed = confirmed - :amt, provider_reserved = provider_reserved - :amt, "
                "available = (confirmed - :amt) - (provider_reserved - :amt) "
                "            - local_reserved, version = version + 1 "
                "WHERE account_id=:a AND asset_key=:k "
                "AND confirmed >= :amt AND provider_reserved >= :amt"
            ),
            {"a": account_id, "k": asset_key, "amt": amount},
        )
        return result.rowcount == 1

    async def insert_reservation(
        self,
        session: AsyncSession,
        *,
        reservation_key: str,
        intent_id: int,
        account_id: int,
        asset_key: str,
        amount: Any,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """幂等插入；冲突时返回既有 reservation（funds 由调用方在锁内先判定）。"""
        result = await session.execute(
            text(
                "INSERT INTO trading.capital_reservations "
                "(reservation_key, intent_id, account_id, asset_key, amount, idempotency_key) "
                "VALUES (:rk, :i, :a, :k, :amt, :ik) "
                "ON CONFLICT (account_id, asset_key, idempotency_key) DO NOTHING RETURNING *"
            ),
            {
                "rk": reservation_key, "i": intent_id, "a": account_id, "k": asset_key,
                "amt": amount, "ik": idempotency_key,
            },
        )
        rows = _rows(result)
        if rows:
            rows[0]["inserted"] = True
            return rows[0]
        existing = await session.execute(
            text(
                "SELECT * FROM trading.capital_reservations "
                "WHERE account_id=:a AND asset_key=:k AND idempotency_key=:ik FOR UPDATE"
            ),
            {"a": account_id, "k": asset_key, "ik": idempotency_key},
        )
        existing_rows = _rows(existing)
        if not existing_rows:
            raise RuntimeError("reservation_idempotency_lost")
        existing_row = existing_rows[0]
        expected = {
            "reservation_key": reservation_key,
            "intent_id": intent_id,
            "account_id": account_id,
            "asset_key": asset_key,
            "amount": Decimal(str(amount)),
            "idempotency_key": idempotency_key,
        }
        for field, value in expected.items():
            actual = existing_row[field]
            if field == "amount":
                actual = Decimal(str(actual))
            if actual != value:
                raise RuntimeError(f"reservation_idempotency_mismatch:{field}")
        return existing_row

    async def get_reservation(
        self, session: AsyncSession, *, reservation_id: int, for_update: bool = False
    ) -> dict[str, Any] | None:
        sql = "SELECT * FROM trading.capital_reservations WHERE id=:r"
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(text(sql), {"r": reservation_id})
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_reservation_by_key(
        self, session: AsyncSession, *, reservation_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.capital_reservations WHERE reservation_key=:k"),
            {"k": reservation_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_reservation_by_idempotency(
        self, session: AsyncSession, *, account_id: int, asset_key: str,
        idempotency_key: str, for_update: bool = False,
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT * FROM trading.capital_reservations "
            "WHERE account_id=:a AND asset_key=:k AND idempotency_key=:ik"
        )
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(
            text(sql), {"a": account_id, "k": asset_key, "ik": idempotency_key}
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def advance_reservation(
        self,
        session: AsyncSession,
        *,
        reservation_id: int,
        new_status: str,
        consumed_delta: Any = 0,
        released_delta: Any = 0,
        expected_status: str | None = None,
    ) -> bool:
        """CAS reservation state/accounting advance; the DB guard owns legal shapes."""
        status_predicate = "status <> :ns"
        if expected_status is not None:
            status_predicate = "status = :expected"
        result = await session.execute(
            text(
                "UPDATE trading.capital_reservations SET status=:ns, "
                "consumed_amount=consumed_amount+:consumed, "
                "released_amount=released_amount+:released, updated_at=now() "
                f"WHERE id=:r AND {status_predicate}"
            ),
            {
                "r": reservation_id,
                "ns": new_status,
                "consumed": consumed_delta,
                "released": released_delta,
                "expected": expected_status,
            },
        )
        return result.rowcount == 1

    async def get_lease(
        self, session: AsyncSession, *, account_id: int, lease_role: str, for_update: bool = False
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT id, account_id, lease_role, owner, lease_until, fencing_token, "
            "latest_heartbeat_id, latest_heartbeat_hash, version, updated_at, created_at "
            "FROM trading.execution_leases WHERE account_id=:a AND lease_role=:r"
        )
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(text(sql), {"a": account_id, "r": lease_role})
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_active_lease_fence(
        self,
        session: AsyncSession,
        *,
        account_id: int,
        lease_role: str,
        owner: str,
        fencing_token: int,
        for_update: bool = True,
    ) -> dict[str, Any] | None:
        """Return only the exact current, unexpired lease identity.

        ``for_update=True`` holds the lease row through an economic-effect transaction so a
        takeover cannot interleave between validation and commit.
        """
        sql = (
            "SELECT id, account_id, lease_role, owner, lease_until, fencing_token, "
            "latest_heartbeat_id, latest_heartbeat_hash, version, updated_at, created_at "
            "FROM trading.execution_leases WHERE account_id=:a AND lease_role=:r "
            "AND owner=:o AND fencing_token=:tok "
            "AND lease_until > statement_timestamp()"
        )
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(
            text(sql),
            {"a": account_id, "r": lease_role, "o": owner, "tok": fencing_token},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def insert_lease(
        self, session: AsyncSession, *, account_id: int, lease_role: str, owner: str,
        lease_until: datetime,
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "INSERT INTO trading.execution_leases "
                "(account_id, lease_role, owner, lease_until, fencing_token) "
                "VALUES (:a, :r, :o, :until, 1) "
                "ON CONFLICT (account_id, lease_role) DO NOTHING RETURNING *"
            ),
            {"a": account_id, "r": lease_role, "o": owner, "until": lease_until},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def renew_lease(
        self, session: AsyncSession, *, account_id: int, lease_role: str, owner: str,
        lease_until: datetime, fencing_token: int,
    ) -> bool:
        """续期（token 不变）；owner/token/未过期 任一不符返回 False。"""
        result = await session.execute(
            text(
                "UPDATE trading.execution_leases SET lease_until=:until, "
                "updated_at=now(), version=version+1 "
                "WHERE account_id=:a AND lease_role=:r AND owner=:o "
                "AND fencing_token=:tok AND lease_until > statement_timestamp()"
            ),
            {"a": account_id, "r": lease_role, "o": owner, "until": lease_until, "tok": fencing_token},
        )
        return result.rowcount == 1

    async def takeover_lease(
        self, session: AsyncSession, *, account_id: int, lease_role: str, owner: str,
        lease_until: datetime, expected_version: int,
    ) -> bool:
        """过期接管：fencing token 单调 +1（CAS on version）。"""
        result = await session.execute(
            text(
                "UPDATE trading.execution_leases SET owner=:o, lease_until=:until, "
                "fencing_token=fencing_token+1, updated_at=now(), version=version+1 "
                "WHERE account_id=:a AND lease_role=:r AND version=:old "
                "AND lease_until <= statement_timestamp()"
            ),
            {"a": account_id, "r": lease_role, "o": owner, "until": lease_until, "old": expected_version},
        )
        return result.rowcount == 1

    async def release_lease(
        self, session: AsyncSession, *, account_id: int, lease_role: str, owner: str,
        fencing_token: int,
    ) -> bool:
        """释放租约：置过期而非 DELETE，保留单调 fencing 历史。"""
        result = await session.execute(
            text(
                "UPDATE trading.execution_leases SET lease_until=statement_timestamp(), "
                "updated_at=now(), version=version+1 "
                "WHERE account_id=:a AND lease_role=:r AND owner=:o AND fencing_token=:tok"
            ),
            {"a": account_id, "r": lease_role, "o": owner, "tok": fencing_token},
        )
        return result.rowcount == 1


# ---------------- WP-05 Checkpoint C：authorization envelopes / orders / attempts / events / trades / reconcile ----------------

    async def insert_envelope(
        self,
        session: AsyncSession,
        *,
        envelope_key: str,
        intent_id: int,
        account_id: int,
        release_manifest_id: int,
        execution_spec_version_id: int,
        capital_permission_manifest_id: int,
        authority: str,
        idempotency_key: str,
        fencing_token: int,
        intent_hash: str,
        preflight_hash1: str,
        preflight_hash2: str,
        envelope_hash: str,
    ) -> dict[str, Any]:
        """Idempotently insert an envelope and exact-match immutable retry material."""
        result = await session.execute(
            text(
                "INSERT INTO trading.execution_authorization_envelopes "
                "(envelope_key, intent_id, account_id, release_manifest_id, "
                " execution_spec_version_id, capital_permission_manifest_id, authority, "
                " idempotency_key, fencing_token, intent_hash, preflight_hash1, "
                " preflight_hash2, envelope_hash) "
                "VALUES (:k, :intent, :acct, :rel, :spec, :cap, :auth, :ik, :fence, "
                " :ih, :pf1, :pf2, :eh) "
                "ON CONFLICT (envelope_key) DO NOTHING RETURNING *"
            ),
            {
                "k": envelope_key, "intent": intent_id, "acct": account_id,
                "rel": release_manifest_id, "spec": execution_spec_version_id,
                "cap": capital_permission_manifest_id, "auth": authority, "ik": idempotency_key,
                "fence": fencing_token, "ih": intent_hash, "pf1": preflight_hash1,
                "pf2": preflight_hash2, "eh": envelope_hash,
            },
        )
        rows = _rows(result)
        if rows:
            rows[0]["inserted"] = True
            return rows[0]
        existing = await self.get_envelope_by_key(session, envelope_key=envelope_key)
        if existing is None:
            raise RuntimeError("envelope_claim_lost")
        _assert_exact_material(
            existing,
            {
                "intent_id": intent_id,
                "account_id": account_id,
                "release_manifest_id": release_manifest_id,
                "execution_spec_version_id": execution_spec_version_id,
                "capital_permission_manifest_id": capital_permission_manifest_id,
                "authority": authority,
                "idempotency_key": idempotency_key,
                "fencing_token": fencing_token,
                "intent_hash": intent_hash,
                "preflight_hash1": preflight_hash1,
                "preflight_hash2": preflight_hash2,
                "envelope_hash": envelope_hash,
            },
            error_prefix="envelope",
        )
        existing["inserted"] = False
        return existing

    async def get_envelope(
        self, session: AsyncSession, *, envelope_id: int, for_update: bool = False
    ) -> dict[str, Any] | None:
        sql = "SELECT * FROM trading.execution_authorization_envelopes WHERE id=:e"
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(text(sql), {"e": envelope_id})
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_envelope_by_key(
        self, session: AsyncSession, *, envelope_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.execution_authorization_envelopes WHERE envelope_key=:k"),
            {"k": envelope_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_envelope_by_idempotency(
        self, session: AsyncSession, *, idempotency_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.execution_authorization_envelopes "
                "WHERE idempotency_key=:k"
            ),
            {"k": idempotency_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def advance_envelope_status(
        self, session: AsyncSession, *, envelope_id: int, new_status: str
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.execution_authorization_envelopes SET status=:ns "
                "WHERE id=:e AND status <> :ns"
            ),
            {"e": envelope_id, "ns": new_status},
        )
        return result.rowcount == 1

    async def next_attempt_no(
        self, session: AsyncSession, *, envelope_id: int
    ) -> int:
        result = await session.execute(
            text(
                "SELECT COALESCE(max(attempt_no), 0) + 1 "
                "FROM trading.exchange_order_attempts WHERE envelope_id=:e"
            ),
            {"e": envelope_id},
        )
        return int(result.scalar_one())

    async def insert_attempt(
        self,
        session: AsyncSession,
        *,
        attempt_key: str,
        envelope_id: int,
        attempt_no: int,
        body_hash: str,
        expected_order_hash: str,
        sdk_manifest_hash: str,
        salt: int,
        timestamp: int,
        fencing_token: int,
        state_event_id: int | None,
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "INSERT INTO trading.exchange_order_attempts "
                "(attempt_key, envelope_id, attempt_no, body_hash, expected_order_hash, "
                " sdk_manifest_hash, salt, timestamp, fencing_token, state_event_id) "
                "VALUES (:k, :e, :no, :bh, :oh, :sdk, :salt, :ts, :fence, :se) "
                "ON CONFLICT (attempt_key) DO NOTHING RETURNING *"
            ),
            {
                "k": attempt_key, "e": envelope_id, "no": attempt_no, "bh": body_hash,
                "oh": expected_order_hash, "sdk": sdk_manifest_hash, "salt": salt,
                "ts": timestamp, "fence": fencing_token, "se": state_event_id,
            },
        )
        rows = _rows(result)
        if rows:
            rows[0]["inserted"] = True
            return rows[0]
        existing_result = await session.execute(
            text("SELECT * FROM trading.exchange_order_attempts WHERE attempt_key=:k"),
            {"k": attempt_key},
        )
        existing_rows = _rows(existing_result)
        if not existing_rows:
            raise RuntimeError("attempt_claim_lost")
        existing = existing_rows[0]
        _assert_exact_material(
            existing,
            {
                "envelope_id": envelope_id,
                "attempt_no": attempt_no,
                "body_hash": body_hash,
                "expected_order_hash": expected_order_hash,
                "sdk_manifest_hash": sdk_manifest_hash,
                "salt": salt,
                "timestamp": timestamp,
                "fencing_token": fencing_token,
            },
            error_prefix="attempt",
        )
        existing["inserted"] = False
        return existing

    async def get_attempt(
        self, session: AsyncSession, *, attempt_id: int, for_update: bool = False
    ) -> dict[str, Any] | None:
        sql = "SELECT * FROM trading.exchange_order_attempts WHERE id=:a"
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(text(sql), {"a": attempt_id})
        rows = _rows(result)
        return rows[0] if rows else None

    async def list_submitted_attempts_for_recovery(
        self,
        session: AsyncSession,
        *,
        account_id: int,
        limit: int = 100,
        for_update: bool = True,
    ) -> list[dict[str, Any]]:
        """List persisted-before-send attempts whose provider result is still unknown.

        The default row lock lets one recovery worker claim a deterministic batch inside its
        transaction.  ``SKIP LOCKED`` prevents duplicate concurrent recovery work.
        """
        sql = (
            "SELECT a.*, e.account_id, o.id AS order_id, o.order_key, "
            "o.external_order_id, o.status AS order_status "
            "FROM trading.exchange_order_attempts a "
            "JOIN trading.execution_authorization_envelopes e ON e.id=a.envelope_id "
            "LEFT JOIN trading.exchange_orders o ON o.attempt_id=a.id "
            "WHERE e.account_id=:account AND a.result='SUBMITTED' "
            "ORDER BY a.id LIMIT :limit"
        )
        if for_update:
            sql += " FOR UPDATE OF a SKIP LOCKED"
        result = await session.execute(
            text(sql),
            {"account": account_id, "limit": limit},
        )
        return _rows(result)

    async def get_attempt_by_expected_order_hash(
        self,
        session: AsyncSession,
        *,
        account_id: int,
        expected_order_hash: str,
        for_update: bool = True,
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT a.* FROM trading.exchange_order_attempts a "
            "JOIN trading.execution_authorization_envelopes e ON e.id=a.envelope_id "
            "WHERE e.account_id=:account AND a.expected_order_hash=:expected "
            "ORDER BY a.id DESC LIMIT 1"
        )
        if for_update:
            sql += " FOR UPDATE OF a"
        result = await session.execute(
            text(sql),
            {"account": account_id, "expected": expected_order_hash},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_attempt_by_body_hash(
        self,
        session: AsyncSession,
        *,
        account_id: int,
        body_hash: str,
        for_update: bool = True,
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT a.* FROM trading.exchange_order_attempts a "
            "JOIN trading.execution_authorization_envelopes e ON e.id=a.envelope_id "
            "WHERE e.account_id=:account AND a.body_hash=:body "
            "ORDER BY a.id DESC LIMIT 1"
        )
        if for_update:
            sql += " FOR UPDATE OF a"
        result = await session.execute(
            text(sql),
            {"account": account_id, "body": body_hash},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def advance_attempt_result(
        self, session: AsyncSession, *, attempt_id: int, result: str,
        state_event_id: int | None = None,
    ) -> bool:
        if state_event_id is not None:
            result_set = (
                "UPDATE trading.exchange_order_attempts SET result=:r, state_event_id=:se "
                "WHERE id=:a AND result='SUBMITTED'"
            )
        else:
            result_set = (
                "UPDATE trading.exchange_order_attempts SET result=:r "
                "WHERE id=:a AND result='SUBMITTED'"
            )
        res = await session.execute(
            text(result_set), {"a": attempt_id, "r": result, "se": state_event_id}
        )
        return res.rowcount == 1

    async def insert_order(
        self,
        session: AsyncSession,
        *,
        order_key: str,
        account_id: int,
        token_id: str,
        side: str,
        price: Any,
        size: Any,
    ) -> dict[str, Any]:
        """创建 order 投影（status=OPEN，external_order_id 未知可为 NULL）。"""
        result = await session.execute(
            text(
                "INSERT INTO trading.exchange_orders "
                "(order_key, account_id, token_id, side, price, size) "
                "VALUES (:k, :acct, :tk, :side, :price, :size) "
                "ON CONFLICT (order_key) DO NOTHING RETURNING *"
            ),
            {"k": order_key, "acct": account_id, "tk": token_id, "side": side,
             "price": price, "size": size},
        )
        rows = _rows(result)
        if rows:
            rows[0]["inserted"] = True
            return rows[0]
        existing = await self.get_order_by_key(session, order_key=order_key)
        if existing is None:
            raise RuntimeError("order_claim_lost")
        _assert_exact_material(
            existing,
            {
                "account_id": account_id,
                "token_id": token_id,
                "side": side,
                "price": price,
                "size": size,
            },
            error_prefix="order",
            decimal_fields=frozenset({"price", "size"}),
        )
        existing["inserted"] = False
        return existing

    async def get_order(
        self, session: AsyncSession, *, order_id: int, for_update: bool = False
    ) -> dict[str, Any] | None:
        sql = "SELECT * FROM trading.exchange_orders WHERE id=:o"
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(text(sql), {"o": order_id})
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_order_by_key(
        self, session: AsyncSession, *, order_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.exchange_orders WHERE order_key=:k"),
            {"k": order_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_order_by_external(
        self, session: AsyncSession, *, account_id: int, external_order_id: str,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT * FROM trading.exchange_orders "
            "WHERE account_id=:a AND external_order_id=:e"
        )
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(text(sql), {"a": account_id, "e": external_order_id})
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_order_by_expected_order_hash(
        self,
        session: AsyncSession,
        *,
        account_id: int,
        expected_order_hash: str,
        for_update: bool = True,
    ) -> dict[str, Any] | None:
        """Resolve a provider order hash through the persisted send attempt lineage."""
        sql = (
            "SELECT o.* FROM trading.exchange_orders o "
            "JOIN trading.exchange_order_attempts a ON a.id=o.attempt_id "
            "JOIN trading.execution_authorization_envelopes e ON e.id=a.envelope_id "
            "WHERE o.account_id=:account AND e.account_id=o.account_id "
            "AND a.expected_order_hash=:expected ORDER BY o.id DESC LIMIT 1"
        )
        if for_update:
            sql += " FOR UPDATE OF o"
        result = await session.execute(
            text(sql),
            {"account": account_id, "expected": expected_order_hash},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def list_orders_for_account(
        self, session: AsyncSession, *, account_id: int, status: str | None = None,
        for_update: bool = False,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM trading.exchange_orders WHERE account_id=:a"
        params: dict[str, Any] = {"a": account_id}
        if status is not None:
            sql += " AND status=:s"
            params["s"] = status
        sql += " ORDER BY id"
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(text(sql), params)
        return _rows(result)

    async def advance_order(
        self,
        session: AsyncSession,
        *,
        order_id: int,
        new_status: str,
        filled_size: Any | None = None,
        external_order_id: str | None = None,
        expected_status: str,
    ) -> bool:
        """CAS 推进 order 投影；guard 校验合法 transition/filled 单调。"""
        if filled_size is None:
            sql = (
                "UPDATE trading.exchange_orders SET status=:s, "
                "external_order_id=COALESCE(:ext, external_order_id), updated_at=now() "
                "WHERE id=:o AND status=:old"
            )
        else:
            sql = (
                "UPDATE trading.exchange_orders SET status=:s, filled_size=:fs, "
                "external_order_id=COALESCE(:ext, external_order_id), updated_at=now() "
                "WHERE id=:o AND status=:old"
            )
        res = await session.execute(
            text(sql), {"o": order_id, "s": new_status, "fs": filled_size,
                       "ext": external_order_id, "old": expected_status},
        )
        return res.rowcount == 1

    async def insert_order_state_event(
        self,
        session: AsyncSession,
        *,
        event_key: str,
        order_id: int,
        event_type: str,
        transition_from: str,
        transition_to: str,
        event_payload: dict,
        event_hash: str,
        fence_token: int,
    ) -> dict[str, Any]:
        from sqlalchemy import bindparam
        from sqlalchemy.dialects.postgresql import JSONB

        result = await session.execute(
            text(
                "INSERT INTO trading.order_state_events "
                "(event_key, order_id, event_type, transition_from, transition_to, "
                " event_payload, event_hash, fence_token) "
                "VALUES (:k, :o, :et, :frm, :to, :payload, :eh, :fence) "
                "ON CONFLICT (event_key) DO NOTHING RETURNING *"
            ).bindparams(bindparam("payload", type_=JSONB())),
            {
                "k": event_key, "o": order_id, "et": event_type, "frm": transition_from,
                "to": transition_to, "payload": event_payload, "eh": event_hash,
                "fence": fence_token,
            },
        )
        rows = _rows(result)
        if rows:
            rows[0]["inserted"] = True
            return rows[0]
        existing = await session.execute(
            text("SELECT * FROM trading.order_state_events WHERE event_key=:k"),
            {"k": event_key},
        )
        existing_rows = _rows(existing)
        if not existing_rows:
            raise RuntimeError("order_state_event_claim_lost")
        existing_row = existing_rows[0]
        _assert_exact_material(
            existing_row,
            {
                "order_id": order_id,
                "event_type": event_type,
                "transition_from": transition_from,
                "transition_to": transition_to,
                "event_payload": event_payload,
                "event_hash": event_hash,
                "fence_token": fence_token,
            },
            error_prefix="order_state_event",
        )
        existing_row["inserted"] = False
        return existing_row

    async def insert_trade(
        self,
        session: AsyncSession,
        *,
        trade_key: str,
        order_id: int,
        account_id: int,
        external_trade_id: str,
        side: str,
        price: Any,
        size: Any,
        fee: Any,
        trade_time: datetime,
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "INSERT INTO trading.exchange_trades "
                "(trade_key, order_id, account_id, external_trade_id, side, price, size, "
                " fee, trade_time) "
                "VALUES (:k, :o, :a, :et, :side, :price, :size, :fee, :tt) "
                "ON CONFLICT (account_id, external_trade_id) DO NOTHING RETURNING *"
            ),
            {
                "k": trade_key, "o": order_id, "a": account_id, "et": external_trade_id,
                "side": side, "price": price, "size": size, "fee": fee, "tt": trade_time,
            },
        )
        rows = _rows(result)
        if rows:
            rows[0]["inserted"] = True
            return rows[0]
        existing = await session.execute(
            text(
                "SELECT * FROM trading.exchange_trades "
                "WHERE account_id=:a AND external_trade_id=:et"
            ),
            {"a": account_id, "et": external_trade_id},
        )
        existing_rows = _rows(existing)
        if not existing_rows:
            raise RuntimeError("trade_claim_lost")
        existing_row = existing_rows[0]
        _assert_exact_material(
            existing_row,
            {
                "trade_key": trade_key,
                "order_id": order_id,
                "account_id": account_id,
                "external_trade_id": external_trade_id,
                "side": side,
                "price": price,
                "size": size,
                "fee": fee,
                "trade_time": trade_time,
            },
            error_prefix="trade",
            decimal_fields=frozenset({"price", "size", "fee"}),
        )
        existing_row["inserted"] = False
        return existing_row

    async def get_trades_for_order(
        self, session: AsyncSession, *, order_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT * FROM trading.exchange_trades WHERE order_id=:o ORDER BY id"
            ),
            {"o": order_id},
        )
        return _rows(result)

    async def insert_reconciliation(
        self,
        session: AsyncSession,
        *,
        reconciliation_key: str,
        account_id: int,
        trigger_reason: str,
        ws_watermark: int,
        rest_page_cursor: dict,
        rest_page_hash: str,
        unknown_queries: dict,
        input_manifest_hash: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        from sqlalchemy import bindparam
        from sqlalchemy.dialects.postgresql import JSONB

        result = await session.execute(
            text(
                "INSERT INTO trading.account_reconciliations "
                "(reconciliation_key, account_id, trigger_reason, ws_watermark, "
                " rest_page_cursor, rest_page_hash, unknown_queries, input_manifest_hash, "
                " differences, fencing_token) "
                "VALUES (:k, :a, :tr, :wm, :cursor, :rh, :uq, :ih, '[]'::jsonb, :fence) "
                "ON CONFLICT (reconciliation_key) DO NOTHING RETURNING *"
            ).bindparams(
                bindparam("cursor", type_=JSONB()), bindparam("uq", type_=JSONB()),
            ),
            {
                "k": reconciliation_key, "a": account_id, "tr": trigger_reason,
                "wm": ws_watermark, "cursor": rest_page_cursor, "rh": rest_page_hash,
                "uq": unknown_queries, "ih": input_manifest_hash, "fence": fencing_token,
            },
        )
        rows = _rows(result)
        if rows:
            rows[0]["inserted"] = True
            return rows[0]
        existing = await self.get_reconciliation_by_key(
            session, reconciliation_key=reconciliation_key
        )
        if existing is None:
            raise RuntimeError("reconciliation_claim_lost")
        _assert_exact_material(
            existing,
            {
                "account_id": account_id,
                "trigger_reason": trigger_reason,
                "ws_watermark": ws_watermark,
                "rest_page_cursor": rest_page_cursor,
                "rest_page_hash": rest_page_hash,
                "unknown_queries": unknown_queries,
                "input_manifest_hash": input_manifest_hash,
                "fencing_token": fencing_token,
            },
            error_prefix="reconciliation",
        )
        existing["inserted"] = False
        return existing

    async def has_active_reconciliation(
        self, session: AsyncSession, *, account_id: int
    ) -> bool:
        """Fence exposure for in-flight runs and FAILED runs without a later completion."""
        result = await session.execute(
            text(
                "SELECT EXISTS ("
                " SELECT 1 FROM trading.account_reconciliations r "
                " WHERE r.account_id=:account AND (r.status='RECONCILING' OR ("
                "   r.status='FAILED' AND NOT EXISTS ("
                "     SELECT 1 FROM trading.account_reconciliations recovered "
                "     WHERE recovered.account_id=r.account_id "
                "       AND recovered.status='COMPLETED' AND recovered.id > r.id"
                "   )"
                " )))"
            ),
            {"account": account_id},
        )
        return bool(result.scalar_one())

    async def get_reconciliation(
        self, session: AsyncSession, *, reconciliation_id: int, for_update: bool = False
    ) -> dict[str, Any] | None:
        sql = "SELECT * FROM trading.account_reconciliations WHERE id=:r"
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(text(sql), {"r": reconciliation_id})
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_reconciliation_by_key(
        self, session: AsyncSession, *, reconciliation_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.account_reconciliations "
                "WHERE reconciliation_key=:k"
            ),
            {"k": reconciliation_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def complete_reconciliation(
        self,
        session: AsyncSession,
        *,
        reconciliation_id: int,
        output_manifest_hash: str,
        differences: list,
        new_status: str,
        completed_at: datetime | None,
    ) -> bool:
        from sqlalchemy import bindparam
        from sqlalchemy.dialects.postgresql import JSONB

        result = await session.execute(
            text(
                "UPDATE trading.account_reconciliations SET "
                "output_manifest_hash=:oh, differences=:diff, status=:s, completed_at=:ca "
                "WHERE id=:r AND status='RECONCILING'"
            ).bindparams(bindparam("diff", type_=JSONB())),
            {
                "r": reconciliation_id, "oh": output_manifest_hash, "diff": differences,
                "s": new_status, "ca": completed_at,
            },
        )
        if result.rowcount == 1:
            return True
        existing = await self.get_reconciliation(
            session, reconciliation_id=reconciliation_id
        )
        if existing is None or existing["status"] == "RECONCILING":
            return False
        _assert_exact_material(
            existing,
            {
                "output_manifest_hash": output_manifest_hash,
                "differences": differences,
                "status": new_status,
                "completed_at": completed_at,
            },
            error_prefix="reconciliation_completion",
        )
        return True

    async def latest_open_order_external_ids(
        self, session: AsyncSession, *, account_id: int
    ) -> list[str]:
        result = await session.execute(
            text(
                "SELECT external_order_id FROM trading.exchange_orders "
                "WHERE account_id=:a AND external_order_id IS NOT NULL "
                "AND status IN ('OPEN','ACK','PARTIAL','UNKNOWN') ORDER BY id"
            ),
            {"a": account_id},
        )
        return [row[0] for row in result.fetchall()]

    async def all_external_order_ids(
        self, session: AsyncSession, *, account_id: int
    ) -> list[str]:
        result = await session.execute(
            text(
                "SELECT external_order_id FROM trading.exchange_orders "
                "WHERE account_id=:a AND external_order_id IS NOT NULL ORDER BY id"
            ),
            {"a": account_id},
        )
        return [row[0] for row in result.fetchall()]

    async def all_external_trade_ids(
        self, session: AsyncSession, *, account_id: int
    ) -> list[str]:
        result = await session.execute(
            text(
                "SELECT external_trade_id FROM trading.exchange_trades "
                "WHERE account_id=:a ORDER BY id"
            ),
            {"a": account_id},
        )
        return [row[0] for row in result.fetchall()]

    async def per_asset_ledger_sums(
        self, session: AsyncSession, *, account_id: int
    ) -> list[dict[str, Any]]:
        """每 asset 的 signed ledger 合计（TOKEN/CASH），供对账硬断言。"""
        result = await session.execute(
            text(
                "SELECT p.asset_type, p.asset_key, COALESCE(sum(p.amount), 0) AS signed_sum "
                "FROM trading.ledger_postings p "
                "JOIN trading.ledger_transactions t ON t.id = p.transaction_id "
                "WHERE t.account_id = :a AND t.status = 'POSTED' "
                "GROUP BY p.asset_type, p.asset_key ORDER BY p.asset_type, p.asset_key"
            ),
            {"a": account_id},
        )
        return _rows(result)


    async def resolve_intent_leg(
        self, session: AsyncSession, *, intent_id: int, external_token_id: str
    ) -> dict[str, Any] | None:
        """从 envelope 的 intent 解析真实 CLOB fill 所需的内部链（leg/contract_spec/token/
        decision/release/objective）。不信任 caller 提供的 token/quantity。"""
        result = await session.execute(
            text(
                "SELECT leg.id AS leg_id, leg.contract_spec_id, leg.token_id AS internal_token_id, "
                "       leg.leg_role, leg.quantity AS leg_quantity, "
                "       t.token_id AS external_token_id, t.market_id, "
                "       i.trade_decision_id, d.release_manifest_id, d.experiment_variant, "
                "       oc.content->>'units' AS cash_asset_key "
                "FROM trading.economic_action_intents i "
                "JOIN trading.action_sets a ON a.id = i.action_set_id "
                "JOIN trading.action_set_legs leg ON leg.action_set_id = a.id "
                "JOIN trading.pm_tokens t ON t.id = leg.token_id "
                "JOIN trading.trade_decisions d ON d.id = i.trade_decision_id "
                "JOIN trading.strategy_objective_contracts oc ON oc.id = d.objective_contract_id "
                "WHERE i.id = :intent AND t.token_id = :ext_token "
                "ORDER BY leg.id LIMIT 1"
            ),
            {"intent": intent_id, "ext_token": external_token_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_reservation_by_intent(
        self, session: AsyncSession, *, account_id: int, intent_id: int,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT * FROM trading.capital_reservations "
            "WHERE account_id=:a AND intent_id=:i ORDER BY id LIMIT 1"
        )
        if for_update:
            sql += " FOR UPDATE"
        result = await session.execute(text(sql), {"a": account_id, "i": intent_id})
        rows = _rows(result)
        return rows[0] if rows else None

    async def positions_for_account(
        self, session: AsyncSession, *, account_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT pos.*, t.token_id AS external_token_id "
                "FROM trading.positions pos "
                "LEFT JOIN trading.pm_tokens t ON t.id = pos.token_id "
                "WHERE pos.account_id=:a "
                "ORDER BY pos.contract_spec_id, pos.token_id"
            ),
            {"a": account_id},
        )
        return _rows(result)

    async def position_lots_for_account(
        self, session: AsyncSession, *, account_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT * FROM trading.position_lots WHERE account_id=:a "
                "ORDER BY id"
            ),
            {"a": account_id},
        )
        return _rows(result)

    async def ledger_transactions_for_account(
        self, session: AsyncSession, *, account_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT * FROM trading.ledger_transactions WHERE account_id=:a ORDER BY id"
            ),
            {"a": account_id},
        )
        return _rows(result)


    async def get_permission(
        self, session: AsyncSession, *, permission_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, name, mode, capability, limits, evaluation_capital, "
                "authorized_capital, kill_switch, content_hash, status "
                "FROM trading.capital_permission_manifests WHERE id=:p"
            ),
            {"p": permission_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_release(
        self, session: AsyncSession, *, release_manifest_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, release_name, config_version_id, strategy_version_id, "
                "execution_spec_version_id, capital_permission_manifest_id, status "
                "FROM trading.release_manifests WHERE id=:r"
            ),
            {"r": release_manifest_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_intent(
        self, session: AsyncSession, *, intent_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, intent_key, intent_hash, trade_decision_id, action_set_id, "
                "status, ttl_at, preflight FROM trading.economic_action_intents WHERE id=:i"
            ),
            {"i": intent_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None
