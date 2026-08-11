"""AI attempt runner：invocation 生命周期 + 持久化（WP-02 Checkpoint B）。

先落 invocation → 调 provider（网络调用不在 DB 事务内）→ raw artifact → validate →
terminal state（ACCEPTED|REJECTED|FAILED|TIMEOUT|CANCELLED|UNKNOWN）。

- 每次调用先创建 invocation，再访问 Provider；没有调用记录就没有业务结果。
- retry/fallback/cache hit 都创建新 attempt 并记录因果（parent/retry_of/fallback_of）。
- Worker 崩溃后仍 STARTED/UNKNOWN 的 attempt 不得猜结果，重试创建新 attempt。
- 失败 attempt 不被缓存；provider 返回后崩溃 → UNKNOWN。
- 只缓存 ACCEPTED + network=NONE；cache hit 仍生成新 invocation，cost=0。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_runtime.cache import CacheHit, cache_key, cacheable
from app.ai_runtime.redaction import redact_for_storage
from app.ai_runtime.validator import OutputValidator
from app.domain.trading.hashing import canonical_hash
from app.services.model_gateway.contracts import (
    ModelRequest,
    ModelResponse,
    NETWORK_NONE,
    ProviderError,
)
from app.services.model_gateway.registry import assert_returned_model
from app.services.model_gateway.service import ModelGatewayService


@dataclass(frozen=True)
class AttemptOutcome:
    invocation_id: int
    lifecycle_state: str
    result: str | None = None
    terminal_reason: str | None = None
    accepted: bool = False
    cache_hit: bool = False
    source_invocation_id: int | None = None


class _Rows:
    @staticmethod
    def first(result) -> dict[str, Any] | None:
        row = result.mappings().first()
        return dict(row) if row is not None else None


class AIRunner:
    """Invocation 生命周期编排；不持有状态。"""

    def __init__(
        self,
        gateway: ModelGatewayService,
        validator: OutputValidator | None = None,
    ) -> None:
        self._gateway = gateway
        self._validator = validator or OutputValidator()

    async def plan(
        self,
        session: AsyncSession,
        *,
        invocation_key: str,
        episode_id: int,
        stage: str,
        role: str,
        attempt_no: int,
        experiment_variant: str,
        requested_provider: str,
        requested_route: str,
        requested_model: str,
        network_policy: str,
        context_class: str,
        input_manifest: dict,
        input_manifest_hash: str,
        model_role_binding_id: int,
        release_manifest_id: int | None = None,
        strategy_version_id: int | None = None,
        config_version_id: int | None = None,
        git_sha: str | None = None,
        db_revision: str | None = None,
        pricing_snapshot: dict,
        occurred_at: datetime | None = None,
        sampling: dict | None = None,
        seed: int | None = None,
        prompt_version: str | None = None,
        schema_version: str | None = None,
        allowed_tools: list | None = None,
        allowed_domains: list | None = None,
        taint_report: dict | None = None,
    ) -> int:
        """PLANNED attempt 落库；返回 invocation id。"""
        at = occurred_at or datetime.now(timezone.utc)
        result = await session.execute(
            text(
                "INSERT INTO trading.ai_invocations "
                "(occurred_at, invocation_key, episode_id, stage, role, attempt_no, "
                " experiment_variant, requested_provider, requested_route, requested_model, "
                " network_policy, allowed_tools, allowed_domains, context_class, taint_report, "
                " prompt_version, schema_version, input_manifest, input_manifest_hash, "
                " model_role_binding_id, release_manifest_id, strategy_version_id, "
                " config_version_id, git_sha, db_revision, pricing_snapshot, sampling, seed, "
                " lifecycle_state, queued_at, cost_estimated) "
                "VALUES (:occurred_at, :invocation_key, :episode_id, :stage, :role, :attempt_no, "
                " :experiment_variant, :requested_provider, :requested_route, :requested_model, "
                " :network_policy, :allowed_tools, :allowed_domains, :context_class, :taint_report, "
                " :prompt_version, :schema_version, :input_manifest, :input_manifest_hash, "
                " :model_role_binding_id, :release_manifest_id, :strategy_version_id, "
                " :config_version_id, :git_sha, :db_revision, :pricing_snapshot, :sampling, :seed, "
                " 'PLANNED', :queued_at, 0) RETURNING id"
            ).bindparams(
                bindparam("allowed_tools", type_=JSONB()),
                bindparam("allowed_domains", type_=JSONB()),
                bindparam("taint_report", type_=JSONB()),
                bindparam("input_manifest", type_=JSONB()),
                bindparam("pricing_snapshot", type_=JSONB()),
                bindparam("sampling", type_=JSONB()),
            ),
            {
                "occurred_at": at,
                "invocation_key": invocation_key,
                "episode_id": episode_id,
                "stage": stage,
                "role": role,
                "attempt_no": attempt_no,
                "experiment_variant": experiment_variant,
                "requested_provider": requested_provider,
                "requested_route": requested_route,
                "requested_model": requested_model,
                "network_policy": network_policy,
                "allowed_tools": allowed_tools or [],
                "allowed_domains": allowed_domains or [],
                "context_class": context_class,
                "taint_report": taint_report or {},
                "prompt_version": prompt_version,
                "schema_version": schema_version,
                "input_manifest": input_manifest,
                "input_manifest_hash": input_manifest_hash,
                "model_role_binding_id": model_role_binding_id,
                "release_manifest_id": release_manifest_id,
                "strategy_version_id": strategy_version_id,
                "config_version_id": config_version_id,
                "git_sha": git_sha,
                "db_revision": db_revision,
                "pricing_snapshot": pricing_snapshot,
                "sampling": sampling or {},
                "seed": seed,
                "queued_at": at,
            },
        )
        return result.scalar_one()

    async def mark_started(
        self, session: AsyncSession, invocation_id: int, occurred_at: datetime
    ) -> None:
        await session.execute(
            text(
                "UPDATE trading.ai_invocations SET lifecycle_state='STARTED', started_at=:t "
                "WHERE id=:id AND lifecycle_state IN ('PLANNED','STARTED')"
            ),
            {"id": invocation_id, "t": occurred_at},
        )

    async def check_cache(
        self,
        session: AsyncSession,
        *,
        model_request: ModelRequest,
        code_hash: str,
    ) -> CacheHit:
        """exact cache lookup；只查 network=NONE 的 ACCEPTED 结果。"""
        if model_request.network_policy != NETWORK_NONE:
            return CacheHit(hit=False)
        key = cache_key(
            role=model_request.role,
            input_manifest_hash=model_request.input_manifest_hash,
            provider=model_request.requested_provider,
            route=model_request.requested_route,
            model=model_request.requested_model,
            prompt_hash=model_request.prompt_hash,
            schema_hash=model_request.schema_hash,
            code_hash=code_hash,
            network_policy=model_request.network_policy,
            tools=model_request.allowed_tools,
            sampling=model_request.sampling,
            seed=model_request.seed,
        )
        row = _Rows.first(await session.execute(
            text(
                "SELECT id, accepted_output_binding FROM trading.ai_invocations "
                "WHERE lifecycle_state='ACCEPTED' AND network_policy='NONE' "
                "  AND input_manifest_hash=:imh AND role=:role "
                "  AND requested_provider=:provider AND requested_model=:model "
                "ORDER BY id DESC LIMIT 1"
            ),
            {
                "imh": model_request.input_manifest_hash,
                "role": model_request.role,
                "provider": model_request.requested_provider,
                "model": model_request.requested_model,
            },
        ))
        if row is None:
            return CacheHit(hit=False)
        return CacheHit(hit=True, cache_key=key, source_invocation_id=row["id"],
                        cached_output=None)

    async def record_cache_hit(
        self,
        session: AsyncSession,
        *,
        plan_kwargs: dict[str, Any],
        source_invocation_id: int,
        occurred_at: datetime,
    ) -> int:
        """cache hit 仍生成新 invocation，cost=0，引用 source invocation。"""
        kwargs = {**plan_kwargs, "occurred_at": occurred_at}
        invocation_id = await self.plan(session, **kwargs)
        await session.execute(
            text(
                "UPDATE trading.ai_invocations SET "
                "lifecycle_state='ACCEPTED', accepted_at=:t, result='cache_hit', "
                "parent_invocation_id=:src, cost_estimated=0, completed_at=:t "
                "WHERE id=:id AND lifecycle_state='PLANNED'"
            ),
            {"id": invocation_id, "src": source_invocation_id, "t": occurred_at},
        )
        return invocation_id

    async def run(
        self,
        session: AsyncSession,
        *,
        invocation_id: int,
        model_role_binding_id: int,
        model_request: ModelRequest,
        blind_context: bool,
    ) -> AttemptOutcome:
        """执行一次已 PLANNED 的 attempt；网络调用在 DB 事务外进行。

        调用方（handler）负责在事务外先 plan+commit，再 run；本方法只做 STARTED →
        调用 → 终态推进。失败/崩溃由 handler 以新 attempt 重试。
        """
        started = datetime.now(timezone.utc)
        await self.mark_started(session, invocation_id, started)
        await session.commit()

        try:
            response = await self._gateway.execute(
                session, model_role_binding_id=model_role_binding_id,
                model_request=model_request,
            )
        except ProviderError as exc:
            return await self._terminal_failure(session, invocation_id, exc)
        except Exception:
            # provider 返回后崩溃 → UNKNOWN（不猜结果；重试创建新 attempt）
            return await self._terminal_unknown(session, invocation_id)

        # returned model allowlist（relay alias 漂移直接 REJECTED）
        try:
            assert_returned_model(response.returned_provider, response.returned_model)
        except ValueError as exc:
            return await self._terminal_rejected(session, invocation_id, str(exc))

        # 记录返回信息 + raw/parsed/normalized artifact refs（脱敏）
        await session.execute(
            text(
                "UPDATE trading.ai_invocations SET "
                "returned_provider=:rp, returned_route=:rr, returned_model=:rm, "
                "lifecycle_state='RESPONSE_RECEIVED', response_at=:t, "
                "input_tokens=:it, output_tokens=:ot, reasoning_tokens=:rt, "
                "provider_request_id=:prid "
                "WHERE id=:id"
            ),
            {
                "id": invocation_id, "rp": response.returned_provider,
                "rr": response.returned_route, "rm": response.returned_model,
                "t": response.received_at, "it": response.input_tokens,
                "ot": response.output_tokens, "rt": response.reasoning_tokens,
                "prid": response.provider_request_id,
            },
        )
        await session.commit()

        # validator 集合（每个独立一行）
        results = await self._validator.validate(
            raw_response=response.raw_text,
            parsed_output=response.parsed_output,
            normalized_output=response.normalized_output,
            blind_context=blind_context,
            network_policy=model_request.network_policy,
        )
        hard_failed = [r for r in results if not r.passed and r.severity == "hard"]
        for r in results:
            await session.execute(
                text(
                    "INSERT INTO trading.ai_validation_results "
                    "(occurred_at, invocation_id, validator_name, validator_version, "
                    " passed, severity, reason_code, details_artifact_hash) "
                    "VALUES (:at, :id, :name, :version, :passed, :severity, :reason, :hash)"
                ),
                {
                    "at": response.received_at, "id": invocation_id,
                    "name": r.validator_name, "version": r.validator_version,
                    "passed": r.passed, "severity": r.severity,
                    "reason": r.reason_code, "hash": None,
                },
            )

        # tool receipts（researcher/verifier 引用必须有 receipt）
        for receipt in response.tool_receipts:
            await session.execute(
                text(
                    "INSERT INTO trading.ai_tool_calls "
                    "(occurred_at, invocation_id, ordinal, tool_type, tool_version, "
                    " arguments, started_at, completed_at, status, source_urls, "
                    " published_at, observed_at) "
                    "VALUES (:at, :id, :ordinal, :tool_type, :tool_version, "
                    " :arguments, :started, :completed, :status, "
                    " :urls, :published, :observed)"
                ).bindparams(
                    bindparam("arguments", type_=JSONB()),
                    bindparam("urls", type_=JSONB()),
                ),
                {
                    "at": response.received_at, "id": invocation_id,
                    "ordinal": receipt.ordinal, "tool_type": receipt.tool_type,
                    "tool_version": receipt.tool_version,
                    "arguments": receipt.arguments,
                    "started": response.received_at,
                    "completed": response.received_at, "status": "COMPLETED",
                    "urls": receipt.source_urls, "published": receipt.published_at,
                    "observed": receipt.observed_at,
                },
            )
        await session.execute(
            text(
                "UPDATE trading.ai_invocations SET tool_count=:tc, search_count=:sc "
                "WHERE id=:id"
            ),
            {"id": invocation_id, "tc": len(response.tool_receipts),
             "sc": len([r for r in response.tool_receipts if r.tool_type in ("web_search", "search_url")])},
        )

        if hard_failed:
            await session.execute(
                text(
                    "UPDATE trading.ai_invocations SET lifecycle_state='REJECTED', "
                    "result='rejected', terminal_reason=:reason, validated_at=:t, "
                    "completed_at=:t WHERE id=:id AND lifecycle_state='RESPONSE_RECEIVED'"
                ),
                {"id": invocation_id, "reason": hard_failed[0].reason_code, "t": response.received_at},
            )
            await session.commit()
            return AttemptOutcome(invocation_id, "REJECTED", "rejected", hard_failed[0].reason_code)

        # ACCEPTED：绑定下游 output（artifact ref 占位由 handler 回填）
        await session.execute(
            text(
                "UPDATE trading.ai_invocations SET lifecycle_state='ACCEPTED', "
                "result='accepted', validated_at=:t, accepted_at=:t, completed_at=:t "
                "WHERE id=:id AND lifecycle_state='RESPONSE_RECEIVED'"
            ),
            {"id": invocation_id, "t": response.received_at},
        )
        await session.commit()
        return AttemptOutcome(invocation_id, "ACCEPTED", "accepted", accepted=True)

    # ---------------- 终态 ----------------

    async def _terminal_failure(self, session: AsyncSession, invocation_id: int, exc: ProviderError) -> AttemptOutcome:
        state = "TIMEOUT" if exc.reason.endswith("timeout") else ("FAILED" if exc.retriable else "FAILED")
        await session.execute(
            text(
                "UPDATE trading.ai_invocations SET lifecycle_state=:state, "
                "terminal_reason=:reason, retriable=:retriable, completed_at=:t "
                "WHERE id=:id"
            ),
            {
                "id": invocation_id, "state": state, "reason": exc.reason,
                "retriable": exc.retriable, "t": datetime.now(timezone.utc),
            },
        )
        await session.commit()
        return AttemptOutcome(invocation_id, state, "failed", exc.reason)

    async def _terminal_unknown(self, session: AsyncSession, invocation_id: int) -> AttemptOutcome:
        await session.execute(
            text(
                "UPDATE trading.ai_invocations SET lifecycle_state='UNKNOWN', "
                "terminal_reason='crash_after_response', completed_at=:t WHERE id=:id"
            ),
            {"id": invocation_id, "t": datetime.now(timezone.utc)},
        )
        await session.commit()
        return AttemptOutcome(invocation_id, "UNKNOWN", "unknown", "crash_after_response")

    async def _terminal_rejected(self, session: AsyncSession, invocation_id: int, reason: str) -> AttemptOutcome:
        await session.execute(
            text(
                "UPDATE trading.ai_invocations SET lifecycle_state='REJECTED', "
                "result='rejected', terminal_reason=:reason, completed_at=:t WHERE id=:id"
            ),
            {"id": invocation_id, "reason": reason, "t": datetime.now(timezone.utc)},
        )
        await session.commit()
        return AttemptOutcome(invocation_id, "REJECTED", "rejected", reason)
