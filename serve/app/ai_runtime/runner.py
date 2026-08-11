"""AI attempt runner：single-claim lifecycle、CAS Artifact 证据链与 blind 边界。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_runtime.cache import CacheHit, cache_key
from app.ai_runtime.redaction import (
    detect_taint,
    redact_for_storage,
    requires_quarantine,
)
from app.ai_runtime.validator import OutputValidator, ValidatorResult
from app.domain.trading.hashing import canonical_bytes
from app.repositories.trading.market_stream import MarketStreamRepository
from app.services.artifact_store import ArtifactRef, ArtifactStore
from app.services.model_gateway.contracts import (
    ALLOWED_PROVIDERS,
    ModelRequest,
    ModelResponse,
    NETWORK_NONE,
    ProviderError,
)
from app.services.model_gateway.registry import resolve
from app.services.model_gateway.service import ModelGatewayService


COGNITION_ROLES = frozenset(
    {"planner_prior", "researcher", "verifier", "joint_forecaster"}
)
OFFLINE_BLIND_ROLES = frozenset({"planner_prior", "joint_forecaster"})
BLIND_CONTEXT_CLASSES = frozenset({"CONTRACT", "PRIOR", "EVIDENCE"})
TOKEN_RATE_KEYS = (
    "input_per_1m",
    "cache_per_1m",
    "output_per_1m",
    "reasoning_per_1m",
)
CALL_RATE_KEYS = ("tool_per_call", "search_per_call")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,15}$")
ARTIFACT_REF_CACHE_LIMIT = 4096


def normalize_pricing_snapshot(snapshot: dict[str, Any]) -> tuple[dict, str | None, str]:
    """Return canonical base-unit pricing and its honest initial reconciliation state."""
    if not isinstance(snapshot, dict):
        raise ValueError("pricing_snapshot_not_object")
    if snapshot.get("status") == "UNPRICED":
        return (
            {
                "status": "UNPRICED",
                "currency": None,
                **{key: None for key in TOKEN_RATE_KEYS},
                **{key: 0 for key in CALL_RATE_KEYS},
            },
            None,
            "UNPRICED",
        )
    currency = snapshot.get("currency")
    if not isinstance(currency, str) or not _CURRENCY_RE.fullmatch(currency):
        raise ValueError("pricing_snapshot_currency_invalid")
    rates: dict[str, int] = {}
    for key in TOKEN_RATE_KEYS:
        value = snapshot.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"pricing_snapshot_{key}_invalid")
        rates[key] = value
    for key in CALL_RATE_KEYS:
        value = snapshot.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"pricing_snapshot_{key}_invalid")
        rates[key] = value
    return {"status": "PRICED", "currency": currency, **rates}, currency, "PENDING"


def estimate_response_cost(
    snapshot: dict[str, Any], response: ModelResponse
) -> tuple[int, str | None, str]:
    """Deterministically estimate integer base-unit cost from frozen rates and usage."""
    if snapshot.get("status") != "PRICED":
        return 0, None, "UNPRICED"
    currency = snapshot.get("currency")
    if response.input_tokens is None or response.output_tokens is None:
        return 0, currency, "UNPRICED"
    cache_tokens = max(int(response.cache_tokens or 0), 0)
    input_tokens = max(int(response.input_tokens), 0)
    uncached_input = max(input_tokens - cache_tokens, 0)
    token_numerator = (
        uncached_input * int(snapshot["input_per_1m"])
        + cache_tokens * int(snapshot["cache_per_1m"])
        + max(int(response.output_tokens), 0) * int(snapshot["output_per_1m"])
        + max(int(response.reasoning_tokens or 0), 0)
        * int(snapshot["reasoning_per_1m"])
    )
    token_cost = (token_numerator + 999_999) // 1_000_000
    tool_count = len(response.tool_receipts)
    search_count = sum(
        receipt.tool_type in {"web_search", "search_url"}
        for receipt in response.tool_receipts
    )
    call_cost = (
        tool_count * int(snapshot["tool_per_call"])
        + search_count * int(snapshot["search_per_call"])
    )
    return token_cost + call_cost, currency, "ESTIMATED"


@dataclass(frozen=True)
class AttemptOutcome:
    invocation_id: int
    lifecycle_state: str
    result: str | None = None
    terminal_reason: str | None = None
    accepted: bool = False
    cache_hit: bool = False
    source_invocation_id: int | None = None


@dataclass
class _RequestEvidence:
    request: ArtifactRef
    prompt: ArtifactRef
    schema: ArtifactRef

    def all_refs(self) -> list[ArtifactRef]:
        return [self.request, self.prompt, self.schema]


@dataclass
class _ArtifactEvidence(_RequestEvidence):
    raw: ArtifactRef
    parsed: ArtifactRef
    normalized: ArtifactRef
    tool_arguments: dict[int, ArtifactRef] = field(default_factory=dict)
    tool_results: dict[int, ArtifactRef] = field(default_factory=dict)
    validator_details: dict[str, ArtifactRef] = field(default_factory=dict)

    def all_refs(self) -> list[ArtifactRef]:
        refs = [
            self.request,
            self.prompt,
            self.schema,
            self.raw,
            self.parsed,
            self.normalized,
            *self.tool_arguments.values(),
            *self.tool_results.values(),
            *self.validator_details.values(),
        ]
        return list({(ref.sha256, ref.compression, ref.storage_driver): ref for ref in refs}.values())


class AIRunner:
    """一次 attempt 的唯一执行者；实例不保存业务状态。"""

    def __init__(
        self,
        gateway: ModelGatewayService,
        validator: OutputValidator | None = None,
        artifacts: ArtifactStore | None = None,
    ) -> None:
        self._gateway = gateway
        self._validator = validator or OutputValidator()
        self._artifacts = artifacts
        self._artifact_catalog = MarketStreamRepository()
        self._artifact_ref_cache: OrderedDict[
            tuple[str, str], ArtifactRef
        ] = OrderedDict()
        self._artifact_puts: dict[
            tuple[str, str], asyncio.Task[ArtifactRef]
        ] = {}

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
        effort: str | None = None,
        cache_key_hash: str | None = None,
        prompt_version: str | None = None,
        schema_version: str | None = None,
        allowed_tools: list | None = None,
        allowed_domains: list | None = None,
        taint_report: dict | None = None,
        parent_invocation_id: int | None = None,
        retry_of_invocation_id: int | None = None,
        fallback_of_invocation_id: int | None = None,
        causation_event_id: str | None = None,
    ) -> int:
        """写入唯一 PLANNED attempt；跨分区幂等由 DB trigger/claim 强制。"""
        tools = sorted(set(allowed_tools or []))
        domains = sorted(set(allowed_domains or []))
        if role in COGNITION_ROLES:
            if context_class not in BLIND_CONTEXT_CLASSES:
                raise ValueError(f"blind_context_forbidden:{context_class}")
            hits = detect_taint(input_manifest)
            if hits:
                raise ValueError(f"blind_input_taint:{hits[0]}")
        if role in OFFLINE_BLIND_ROLES and (
            network_policy != NETWORK_NONE or tools or domains
        ):
            raise ValueError("blind_network_forbidden")
        if network_policy == NETWORK_NONE and (
            not isinstance(cache_key_hash, str)
            or len(cache_key_hash) != 64
            or any(char not in "0123456789abcdef" for char in cache_key_hash)
        ):
            raise ValueError("offline_cache_key_hash_required")
        normalized_pricing, cost_currency, cost_reconciliation = (
            normalize_pricing_snapshot(pricing_snapshot)
        )

        at = occurred_at or datetime.now(timezone.utc)
        result = await session.execute(
            text(
                "INSERT INTO trading.ai_invocations "
                "(occurred_at, invocation_key, episode_id, stage, role, attempt_no, "
                " experiment_variant, parent_invocation_id, retry_of_invocation_id, "
                " fallback_of_invocation_id, causation_event_id, requested_provider, "
                " requested_route, requested_model, network_policy, allowed_tools, "
                " allowed_domains, context_class, taint_report, prompt_version, schema_version, "
                " input_manifest, input_manifest_hash, model_role_binding_id, release_manifest_id, "
                " strategy_version_id, config_version_id, git_sha, db_revision, pricing_snapshot, "
                " sampling, seed, effort, cache_key_hash, lifecycle_state, queued_at, "
                " cost_estimated, cost_currency, cost_reconciliation) "
                "VALUES (:occurred_at, :invocation_key, :episode_id, :stage, :role, :attempt_no, "
                " :experiment_variant, :parent, :retry_of, :fallback_of, :causation, "
                " :requested_provider, :requested_route, :requested_model, :network_policy, "
                " :allowed_tools, :allowed_domains, :context_class, :taint_report, "
                " :prompt_version, :schema_version, :input_manifest, :input_manifest_hash, "
                " :model_role_binding_id, :release_manifest_id, :strategy_version_id, "
                " :config_version_id, :git_sha, :db_revision, :pricing_snapshot, :sampling, "
                " :seed, :effort, :cache_key_hash, 'PLANNED', :queued_at, 0, "
                " :cost_currency, :cost_reconciliation) RETURNING id"
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
                "parent": parent_invocation_id,
                "retry_of": retry_of_invocation_id,
                "fallback_of": fallback_of_invocation_id,
                "causation": causation_event_id,
                "requested_provider": requested_provider,
                "requested_route": requested_route,
                "requested_model": requested_model,
                "network_policy": network_policy,
                "allowed_tools": tools,
                "allowed_domains": domains,
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
                "pricing_snapshot": normalized_pricing,
                "sampling": sampling or {},
                "seed": seed,
                "effort": effort,
                "cache_key_hash": cache_key_hash,
                "cost_currency": cost_currency,
                "cost_reconciliation": cost_reconciliation,
                "queued_at": at,
            },
        )
        return int(result.scalar_one())

    async def _claim_started(
        self, session: AsyncSession, invocation_id: int, occurred_at: datetime
    ) -> dict[str, Any] | None:
        """唯一 PLANNED→STARTED claim；未 claim 到的 caller 绝不访问 provider。"""
        result = await session.execute(
            text(
                "UPDATE trading.ai_invocations SET lifecycle_state='STARTED', started_at=:t "
                "WHERE id=:id AND lifecycle_state='PLANNED' "
                "RETURNING id, occurred_at, episode_id, stage, role, attempt_no, "
                "experiment_variant, requested_provider, requested_route, requested_model, "
                "network_policy, allowed_tools, allowed_domains, context_class, "
                "input_manifest, input_manifest_hash, model_role_binding_id, git_sha, effort, cache_key_hash, "
                "pricing_snapshot, sampling, seed"
            ),
            {"id": invocation_id, "t": occurred_at},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None

    async def mark_started(
        self, session: AsyncSession, invocation_id: int, occurred_at: datetime
    ) -> bool:
        """兼容入口；返回是否真正取得唯一 claim。"""
        return await self._claim_started(session, invocation_id, occurred_at) is not None

    @staticmethod
    def request_cache_key(request: ModelRequest, code_hash: str) -> str:
        """Canonical exact cache identity persisted on every offline attempt."""
        prompt_hash = hashlib.sha256(
            str(redact_for_storage(request.prompt_text)).encode("utf-8")
        ).hexdigest()
        schema_hash = hashlib.sha256(
            (request.schema_text or "{}").encode("utf-8")
        ).hexdigest()
        return cache_key(
            role=request.role,
            input_manifest_hash=request.input_manifest_hash,
            provider=request.requested_provider,
            route=request.requested_route,
            model=request.requested_model,
            prompt_hash=prompt_hash,
            schema_hash=schema_hash,
            code_hash=code_hash,
            network_policy=request.network_policy,
            tools=request.allowed_tools,
            domains=request.allowed_domains,
            sampling=request.sampling,
            seed=request.seed,
            effort=request.effort,
            max_tokens=request.max_tokens,
        )

    @staticmethod
    def _assert_claim_matches_request(
        claim: dict[str, Any],
        *,
        model_role_binding_id: int,
        request: ModelRequest,
    ) -> None:
        checks = {
            "episode_id": request.episode_id,
            "stage": request.stage,
            "role": request.role,
            "attempt_no": request.attempt_no,
            "experiment_variant": request.experiment_variant,
            "requested_provider": request.requested_provider,
            "requested_route": request.requested_route,
            "requested_model": request.requested_model,
            "network_policy": request.network_policy,
            "input_manifest_hash": request.input_manifest_hash,
            "model_role_binding_id": model_role_binding_id,
        }
        for field_name, value in checks.items():
            if claim[field_name] != value:
                raise ValueError(f"ai_attempt_{field_name}_mismatch")
        if set(claim["allowed_tools"] or []) != set(request.allowed_tools):
            raise ValueError("ai_attempt_allowed_tools_mismatch")
        if set(claim["allowed_domains"] or []) != set(request.allowed_domains):
            raise ValueError("ai_attempt_allowed_domains_mismatch")
        if claim["input_manifest"] != request.input_manifest:
            raise ValueError("ai_attempt_input_manifest_mismatch")
        if claim["effort"] != request.effort:
            raise ValueError("ai_attempt_effort_mismatch")
        if (claim["sampling"] or {}) != request.sampling:
            raise ValueError("ai_attempt_sampling_mismatch")
        if claim["seed"] != request.seed:
            raise ValueError("ai_attempt_seed_mismatch")
        if request.network_policy == NETWORK_NONE:
            code_hash = claim["git_sha"]
            if not isinstance(code_hash, str):
                raise ValueError("ai_attempt_code_hash_missing")
            expected_cache_key = AIRunner.request_cache_key(request, code_hash)
            if claim["cache_key_hash"] != expected_cache_key:
                raise ValueError("ai_attempt_cache_key_mismatch")

    @staticmethod
    def _assert_blind_request(claim: dict[str, Any], request: ModelRequest) -> None:
        if request.role not in COGNITION_ROLES:
            return
        if claim["context_class"] not in BLIND_CONTEXT_CLASSES:
            raise ValueError("blind_context_forbidden")
        hits = detect_taint(
            {
                "input_manifest": request.input_manifest,
                "prompt": request.prompt_text,
            }
        )
        if hits:
            raise ValueError(f"blind_input_taint:{hits[0]}")
        if request.role in OFFLINE_BLIND_ROLES:
            request.assert_blind_context(claim["context_class"])

    async def run(
        self,
        session: AsyncSession,
        *,
        invocation_id: int,
        model_role_binding_id: int,
        model_request: ModelRequest,
        blind_context: bool,
    ) -> AttemptOutcome:
        """执行一个 attempt；caller 的 ``blind_context=False`` 不能降低角色边界。"""
        started = datetime.now(timezone.utc)
        claim = await self._claim_started(session, invocation_id, started)
        await session.commit()
        if claim is None:
            return await self._already_claimed_outcome(session, invocation_id)

        try:
            self._assert_claim_matches_request(
                claim,
                model_role_binding_id=model_role_binding_id,
                request=model_request,
            )
            # 四个 cognition role 一律 taint 检查；参数只允许把其他 role 收紧，不能放松。
            if blind_context or model_request.role in COGNITION_ROLES:
                self._assert_blind_request(claim, model_request)
        except ValueError as exc:
            return await self._terminal_rejected(session, invocation_id, str(exc))

        if self._artifacts is None:
            return await self._terminal_unknown(
                session, invocation_id, "artifact_store_missing"
            )
        try:
            request_evidence = await self._write_request_artifacts(
                invocation_id=invocation_id,
                request=model_request,
            )
            await self._persist_request_evidence(
                session,
                invocation_id=invocation_id,
                evidence=request_evidence,
            )
            await session.commit()
        except asyncio.CancelledError:
            await session.rollback()
            await asyncio.shield(
                self._terminal_cancelled(
                    session, invocation_id, "cancelled_before_provider"
                )
            )
            raise
        except Exception:
            await session.rollback()
            return await self._terminal_unknown(
                session, invocation_id, "request_evidence_persistence_failed"
            )

        try:
            response = await self._gateway.execute(
                session,
                model_role_binding_id=model_role_binding_id,
                model_request=model_request,
            )
        except ProviderError as exc:
            return await self._terminal_failure(session, invocation_id, exc)
        except asyncio.CancelledError:
            await asyncio.shield(
                self._terminal_cancelled(session, invocation_id, "provider_cancelled")
            )
            raise
        except ValueError as exc:
            return await self._terminal_rejected(session, invocation_id, str(exc))
        except Exception:
            return await self._terminal_unknown(
                session, invocation_id, "crash_after_response"
            )

        binding_failure: str | None = None
        try:
            resolve(
                response.returned_provider,
                response.returned_route,
                response.returned_model,
            )
            if (
                response.returned_provider != model_request.requested_provider
                or response.returned_route != model_request.requested_route
                or response.returned_model != model_request.requested_model
            ):
                raise ValueError("returned_model_binding_drift")
        except ValueError as exc:
            # A provider response is evidence even when its returned binding drifts.
            # Persist the raw/parsed/normalized payload, tools, and validators first;
            # only then terminalize it REJECTED.
            binding_failure = str(exc)

        parsed: dict | None = response.parsed_output
        if parsed is None:
            try:
                decoded = json.loads(response.raw_text)
                parsed = decoded if isinstance(decoded, dict) else None
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = None
        normalized = response.normalized_output
        if normalized is None and isinstance(parsed, dict):
            normalized = parsed

        results = await self._validator.validate(
            raw_response=response.raw_text,
            parsed_output=parsed,
            normalized_output=normalized,
            blind_context=blind_context or model_request.role in COGNITION_ROLES,
            network_policy=model_request.network_policy,
        )
        hard_failed = [
            result
            for result in results
            if not result.passed and result.severity == "hard"
        ]

        try:
            evidence = await self._write_artifacts(
                invocation_id=invocation_id,
                request=model_request,
                response=response,
                parsed=parsed,
                normalized=normalized,
                validators=results,
                request_evidence=request_evidence,
            )
            await self._persist_response_evidence(
                session,
                invocation_id=invocation_id,
                invocation_occurred_at=claim["occurred_at"],
                request=model_request,
                response=response,
                evidence=evidence,
                validators=results,
                hard_failed=hard_failed,
                binding_failure=binding_failure,
                pricing_snapshot=claim["pricing_snapshot"],
            )
            await session.commit()
        except asyncio.CancelledError:
            await session.rollback()
            await asyncio.shield(
                self._terminal_unknown(
                    session, invocation_id, "cancelled_after_response"
                )
            )
            raise
        except Exception:
            await session.rollback()
            return await self._terminal_unknown(
                session, invocation_id, "artifact_or_evidence_persistence_failed"
            )

        if binding_failure or hard_failed:
            return AttemptOutcome(
                invocation_id,
                "REJECTED",
                "rejected",
                binding_failure or hard_failed[0].reason_code,
            )
        return AttemptOutcome(
            invocation_id, "ACCEPTED", "accepted", accepted=True
        )

    async def _already_claimed_outcome(
        self, session: AsyncSession, invocation_id: int
    ) -> AttemptOutcome:
        row = (
            await session.execute(
                text(
                    "SELECT lifecycle_state, result, terminal_reason, "
                    "accepted_output_binding FROM trading.ai_invocations WHERE id=:id"
                ),
                {"id": invocation_id},
            )
        ).mappings().one_or_none()
        await session.rollback()
        if row is None:
            raise ValueError("ai_invocation_missing")
        return AttemptOutcome(
            invocation_id,
            row["lifecycle_state"],
            row["result"],
            row["terminal_reason"] or "attempt_already_claimed",
            accepted=row["lifecycle_state"] == "ACCEPTED",
        )

    async def _put(self, data: bytes, mime: str) -> ArtifactRef:
        assert self._artifacts is not None
        key = (hashlib.sha256(data).hexdigest(), mime)
        cached = self._artifact_ref_cache.get(key)
        if cached is not None:
            self._artifact_ref_cache.move_to_end(key)
            return cached
        task = self._artifact_puts.get(key)
        if task is None:
            task = asyncio.create_task(
                asyncio.to_thread(
                    self._artifacts.put_bytes, data, mime, "none"
                )
            )
            self._artifact_puts[key] = task

            def finish(completed: asyncio.Task[ArtifactRef]) -> None:
                if self._artifact_puts.get(key) is completed:
                    self._artifact_puts.pop(key, None)
                if not completed.cancelled() and completed.exception() is None:
                    self._remember_artifact_ref(key, completed.result())

            task.add_done_callback(finish)
        return await asyncio.shield(task)

    def _remember_artifact_ref(
        self, key: tuple[str, str], ref: ArtifactRef
    ) -> None:
        """Bounded technical LRU; request artifacts are unique and must not grow memory."""
        self._artifact_ref_cache[key] = ref
        self._artifact_ref_cache.move_to_end(key)
        while len(self._artifact_ref_cache) > ARTIFACT_REF_CACHE_LIMIT:
            self._artifact_ref_cache.popitem(last=False)

    async def _register_artifacts(
        self, session: AsyncSession, refs: list[ArtifactRef]
    ) -> dict[str, int]:
        """Register/dedupe an Artifact batch in two DB roundtrips, then verify metadata."""
        unique = {
            (ref.sha256, ref.compression, ref.storage_driver, ref.storage_version): ref
            for ref in refs
        }
        items = [
            {
                "sha": ref.sha256,
                "orig": ref.original_size,
                "stored": ref.stored_size,
                "mime": ref.mime,
                "codec": ref.compression,
                "driver": ref.storage_driver,
                "version": ref.storage_version,
                "locator": ref.locator,
            }
            for ref in unique.values()
        ]
        if not items:
            return {}
        bind = bindparam("items", type_=JSONB())
        await session.execute(
            text(
                "INSERT INTO trading.artifact_objects "
                "(sha256,original_size,stored_size,mime,compression,storage_driver,"
                "storage_version,locator) "
                "SELECT x.sha,x.orig,x.stored,x.mime,x.codec,x.driver,x.version,x.locator "
                "FROM jsonb_to_recordset(:items) AS x("
                "sha text,orig bigint,stored bigint,mime text,codec text,driver text,"
                "version text,locator text) "
                "ON CONFLICT (sha256,compression,storage_driver,storage_version) DO NOTHING"
            ).bindparams(bind),
            {"items": items},
        )
        rows = (
            await session.execute(
                text(
                    "SELECT a.id,a.sha256,a.original_size,a.stored_size,a.mime,"
                    "a.compression,a.storage_driver,a.storage_version,a.locator "
                    "FROM trading.artifact_objects a "
                    "JOIN jsonb_to_recordset(:items) AS x("
                    "sha text,codec text,driver text,version text) "
                    "ON a.sha256=x.sha AND a.compression=x.codec "
                    "AND a.storage_driver=x.driver AND a.storage_version=x.version"
                ).bindparams(bind),
                {"items": items},
            )
        ).mappings().all()
        observed = {
            (
                row["sha256"],
                row["compression"],
                row["storage_driver"],
                row["storage_version"],
            ): row
            for row in rows
        }
        result: dict[str, int] = {}
        for key, ref in unique.items():
            row = observed.get(key)
            if row is None or (
                int(row["original_size"]) != ref.original_size
                or int(row["stored_size"]) != ref.stored_size
                or row["mime"] != ref.mime
                or row["locator"] != ref.locator
            ):
                raise RuntimeError("artifact_catalog_conflict")
            result[ref.sha256] = int(row["id"])
        return result

    async def _write_request_artifacts(
        self,
        *,
        invocation_id: int,
        request: ModelRequest,
    ) -> _RequestEvidence:
        """Persistable, replay-exact request envelope with secrets recursively redacted."""
        request_payload = redact_for_storage(
            {
                "invocation_id": invocation_id,
                "role": request.role,
                "stage": request.stage,
                "episode_id": request.episode_id,
                "attempt_no": request.attempt_no,
                "experiment_variant": request.experiment_variant,
                "requested_provider": request.requested_provider,
                "requested_route": request.requested_route,
                "requested_model": request.requested_model,
                "network_policy": request.network_policy,
                "allowed_tools": sorted(request.allowed_tools),
                "allowed_domains": sorted(request.allowed_domains),
                "prompt_text": request.prompt_text,
                "prompt_hash": request.prompt_hash,
                "schema_text": request.schema_text,
                "schema_hash": request.schema_hash,
                "input_manifest": request.input_manifest,
                "input_manifest_hash": request.input_manifest_hash,
                "sampling": request.sampling,
                "seed": request.seed,
                "effort": request.effort,
                "max_tokens": request.max_tokens,
                "timeout_seconds": request.timeout_seconds,
            }
        )

        request_ref, prompt_ref, schema_ref = await asyncio.gather(
            self._put(canonical_bytes(request_payload), "application/json"),
            self._put(
                str(redact_for_storage(request.prompt_text)).encode("utf-8"),
                "text/markdown",
            ),
            self._put(
                (request.schema_text or "{}").encode("utf-8"),
                "application/schema+json",
            ),
        )
        return _RequestEvidence(request_ref, prompt_ref, schema_ref)

    async def _persist_request_evidence(
        self,
        session: AsyncSession,
        *,
        invocation_id: int,
        evidence: _RequestEvidence,
    ) -> None:
        await self._register_artifacts(session, evidence.all_refs())
        updated = await session.execute(
            text(
                "UPDATE trading.ai_invocations SET request_artifact_ref=:request_ref, "
                "prompt_artifact_ref=:prompt_ref, schema_artifact_ref=:schema_ref, "
                "prompt_version=COALESCE(prompt_version,'inline/v1'), "
                "schema_version=COALESCE(schema_version,'inline/v1') "
                "WHERE id=:id AND lifecycle_state='STARTED'"
            ),
            {
                "id": invocation_id,
                "request_ref": evidence.request.sha256,
                "prompt_ref": evidence.prompt.sha256,
                "schema_ref": evidence.schema.sha256,
            },
        )
        if updated.rowcount != 1:
            raise RuntimeError("ai_invocation_request_evidence_claim_lost")

    async def _write_artifacts(
        self,
        *,
        invocation_id: int,
        request: ModelRequest,
        response: ModelResponse,
        parsed: dict | None,
        normalized: dict | None,
        validators: list[ValidatorResult],
        request_evidence: _RequestEvidence,
    ) -> _ArtifactEvidence:
        quarantined = requires_quarantine(response.raw_text) or requires_quarantine(
            parsed
        )
        safe_raw: Any = (
            redact_for_storage(response.raw_text)
            if quarantined
            else response.raw_text
        )
        safe_parsed: Any = redact_for_storage(parsed) if quarantined else parsed
        safe_normalized: Any = (
            redact_for_storage(normalized) if quarantined else normalized
        )
        parsed_payload = (
            safe_parsed if isinstance(safe_parsed, dict) else {"_parse_error": True}
        )
        normalized_payload = (
            safe_normalized
            if isinstance(safe_normalized, dict)
            else {"_normalization_error": True}
        )

        raw_ref, parsed_ref, normalized_ref = await asyncio.gather(
            self._put(str(safe_raw).encode("utf-8"), "application/json"),
            self._put(canonical_bytes(parsed_payload), "application/json"),
            self._put(canonical_bytes(normalized_payload), "application/json"),
        )
        evidence = _ArtifactEvidence(
            request=request_evidence.request,
            prompt=request_evidence.prompt,
            schema=request_evidence.schema,
            raw=raw_ref,
            parsed=parsed_ref,
            normalized=normalized_ref,
        )
        if len({receipt.ordinal for receipt in response.tool_receipts}) != len(
            response.tool_receipts
        ):
            raise ValueError("duplicate_tool_receipt_ordinal")
        tool_puts = []
        for receipt in response.tool_receipts:
            tool_puts.extend(
                [
                    self._put(
                        canonical_bytes(redact_for_storage(receipt.arguments)),
                        "application/json",
                    ),
                    self._put(
                        canonical_bytes(
                            redact_for_storage(
                                {
                                    "result_text": receipt.result_text,
                                    "source_urls": receipt.source_urls,
                                    "published_at": receipt.published_at,
                                    "observed_at": receipt.observed_at,
                                    "provider_tool_call_id": receipt.provider_tool_call_id,
                                }
                            )
                        ),
                        "application/json",
                    ),
                ]
            )
        tool_refs = await asyncio.gather(*tool_puts)
        for index, receipt in enumerate(response.tool_receipts):
            evidence.tool_arguments[receipt.ordinal] = tool_refs[index * 2]
            evidence.tool_results[receipt.ordinal] = tool_refs[index * 2 + 1]

        if len({result.validator_name for result in validators}) != len(validators):
            raise ValueError("duplicate_validator_name")
        validator_refs = await asyncio.gather(
            *(
                self._put(
                    canonical_bytes(
                        {
                            "validator_name": result.validator_name,
                            "validator_version": result.validator_version,
                            "passed": result.passed,
                            "severity": result.severity,
                            "reason_code": result.reason_code,
                            "details": redact_for_storage(result.details or {}),
                        }
                    ),
                    "application/json",
                )
                for result in validators
            )
        )
        for result, ref in zip(validators, validator_refs, strict=True):
            evidence.validator_details[result.validator_name] = ref
        return evidence

    async def _persist_response_evidence(
        self,
        session: AsyncSession,
        *,
        invocation_id: int,
        invocation_occurred_at: datetime,
        request: ModelRequest,
        response: ModelResponse,
        evidence: _ArtifactEvidence,
        validators: list[ValidatorResult],
        hard_failed: list[ValidatorResult],
        binding_failure: str | None,
        pricing_snapshot: dict[str, Any],
    ) -> None:
        artifact_ids = await self._register_artifacts(session, evidence.all_refs())

        invocation_ref = f"ai-invocation:{invocation_id}"
        edges = [
            (evidence.request, evidence.raw, "READS"),
            (evidence.prompt, evidence.raw, "READS"),
            (evidence.schema, evidence.parsed, "VALIDATES"),
            (evidence.raw, evidence.parsed, "PRODUCES"),
            (evidence.parsed, evidence.normalized, "PRODUCES"),
        ]
        edges.extend(
            (evidence.raw, ref, "PRODUCES")
            for ref in evidence.tool_results.values()
        )
        edges.extend(
            (evidence.normalized, ref, "VALIDATES")
            for ref in evidence.validator_details.values()
        )
        edge_rows = [
            {
                "f": artifact_ids[from_ref.sha256],
                "t": artifact_ids[to_ref.sha256],
                "r": relation,
                "i": invocation_ref,
            }
            for from_ref, to_ref, relation in edges
            if from_ref.sha256 != to_ref.sha256
        ]
        if edge_rows:
            await session.execute(
                text(
                    "INSERT INTO trading.artifact_lineage_edges "
                    "(from_artifact_id,to_artifact_id,relation,invocation_ref) "
                    "VALUES (:f,:t,:r,:i) ON CONFLICT DO NOTHING"
                ),
                edge_rows,
            )

        validator_rows = [
            {
                "at": invocation_occurred_at,
                "id": invocation_id,
                "inv_at": invocation_occurred_at,
                "name": result.validator_name,
                "version": result.validator_version,
                "passed": result.passed,
                "severity": result.severity,
                "reason": result.reason_code,
                "hash": evidence.validator_details[result.validator_name].sha256,
            }
            for result in validators
        ]
        if validator_rows:
            await session.execute(
                text(
                    "INSERT INTO trading.ai_validation_results "
                    "(occurred_at, invocation_id, invocation_occurred_at, validator_name, "
                    " validator_version, passed, severity, reason_code, details_artifact_hash) "
                    "VALUES (:at,:id,:inv_at,:name,:version,:passed,:severity,:reason,:hash)"
                ),
                validator_rows,
            )

        tool_rows = [
            {
                "at": invocation_occurred_at,
                "id": invocation_id,
                "inv_at": invocation_occurred_at,
                "ordinal": receipt.ordinal,
                "tool_type": receipt.tool_type,
                "tool_version": receipt.tool_version,
                "arguments": redact_for_storage(receipt.arguments),
                "arg_ref": evidence.tool_arguments[receipt.ordinal].sha256,
                "started": response.received_at,
                "completed": response.received_at,
                "result_ref": evidence.tool_results[receipt.ordinal].sha256,
                "urls": receipt.source_urls,
                "published": receipt.published_at,
                "observed": receipt.observed_at,
                "tool_id": receipt.provider_tool_call_id,
            }
            for receipt in response.tool_receipts
        ]
        if tool_rows:
            await session.execute(
                text(
                    "INSERT INTO trading.ai_tool_calls "
                    "(occurred_at, invocation_id, invocation_occurred_at, ordinal, tool_type, "
                    " tool_version, arguments, arguments_artifact_ref, started_at, completed_at, "
                    " status, result_artifact_ref, source_urls, published_at, observed_at, "
                    " provider_tool_call_id) "
                    "VALUES (:at,:id,:inv_at,:ordinal,:tool_type,:tool_version,:arguments,:arg_ref,"
                    ":started,:completed,'COMPLETED',:result_ref,:urls,:published,:observed,:tool_id)"
                ).bindparams(
                    bindparam("arguments", type_=JSONB()),
                    bindparam("urls", type_=JSONB()),
                ),
                tool_rows,
            )

        rejected = bool(binding_failure or hard_failed)
        terminal_state = "REJECTED" if rejected else "ACCEPTED"
        result_value = "rejected" if rejected else "accepted"
        terminal_reason = (
            binding_failure
            or (hard_failed[0].reason_code if hard_failed else None)
        )
        returned_provider = (
            response.returned_provider
            if response.returned_provider in ALLOWED_PROVIDERS
            else None
        )
        estimated_cost, cost_currency, cost_reconciliation = estimate_response_cost(
            pricing_snapshot, response
        )
        updated = await session.execute(
            text(
                "UPDATE trading.ai_invocations SET "
                "returned_provider=:rp, returned_route=:rr, returned_model=:rm, "
                "request_artifact_ref=:request_ref, prompt_artifact_ref=:prompt_ref, "
                "schema_artifact_ref=:schema_ref, raw_response_artifact_ref=:raw_ref, "
                "parsed_output_artifact_ref=:parsed_ref, "
                "normalized_output_artifact_ref=:normalized_ref, "
                "accepted_output_binding=:binding, lifecycle_state=:state, result=:result, "
                "terminal_reason=:reason, response_at=:response_at, parsed_at=:response_at, "
                "validated_at=:response_at, accepted_at=:accepted_at, completed_at=:response_at, "
                "input_tokens=:it, cache_tokens=:ct, output_tokens=:ot, reasoning_tokens=:rt, "
                "provider_request_id=:prid, tool_count=:tc, search_count=:sc, "
                "cost_estimated=:cost, cost_currency=:currency, "
                "cost_reconciliation=:reconciliation, "
                "prompt_version=COALESCE(prompt_version,'inline/v1'), "
                "schema_version=COALESCE(schema_version,'inline/v1') "
                "WHERE id=:id AND lifecycle_state='STARTED'"
            ),
            {
                "id": invocation_id,
                "rp": returned_provider,
                "rr": response.returned_route,
                "rm": response.returned_model,
                "request_ref": evidence.request.sha256,
                "prompt_ref": evidence.prompt.sha256,
                "schema_ref": evidence.schema.sha256,
                "raw_ref": evidence.raw.sha256,
                "parsed_ref": evidence.parsed.sha256,
                "normalized_ref": evidence.normalized.sha256,
                "binding": evidence.normalized.sha256 if not rejected else None,
                "state": terminal_state,
                "result": result_value,
                "reason": terminal_reason,
                "response_at": response.received_at,
                "accepted_at": response.received_at if not rejected else None,
                "it": response.input_tokens,
                "ct": response.cache_tokens,
                "ot": response.output_tokens,
                "rt": response.reasoning_tokens,
                "prid": response.provider_request_id,
                "cost": estimated_cost,
                "currency": cost_currency,
                "reconciliation": cost_reconciliation,
                "tc": len(response.tool_receipts),
                "sc": sum(
                    receipt.tool_type in {"web_search", "search_url"}
                    for receipt in response.tool_receipts
                ),
            },
        )
        if updated.rowcount != 1:
            raise RuntimeError("ai_invocation_terminalize_claim_lost")

    async def check_cache(
        self,
        session: AsyncSession,
        *,
        model_request: ModelRequest,
        code_hash: str,
    ) -> CacheHit:
        """Exact lookup；任一 key 维度不可证明时返回 miss。"""
        if (
            model_request.network_policy != NETWORK_NONE
            or self._artifacts is None
            or not isinstance(code_hash, str)
            or len(code_hash) != 64
            or any(char not in "0123456789abcdef" for char in code_hash)
        ):
            return CacheHit(hit=False)
        key = self.request_cache_key(model_request, code_hash)
        row = (
            await session.execute(
                text(
                    "SELECT id, normalized_output_artifact_ref, accepted_output_binding "
                    "FROM trading.ai_invocations WHERE lifecycle_state='ACCEPTED' "
                    "AND network_policy='NONE' AND cache_key_hash=:cache_key_hash "
                    "AND normalized_output_artifact_ref=accepted_output_binding "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"cache_key_hash": key},
            )
        ).mappings().one_or_none()
        if row is None:
            return CacheHit(hit=False)
        output = await self._load_artifact_json(
            session, row["normalized_output_artifact_ref"]
        )
        return CacheHit(
            hit=True,
            cache_key=key,
            source_invocation_id=int(row["id"]),
            cached_output=output,
        )

    async def _load_artifact_json(
        self, session: AsyncSession, sha256: str
    ) -> dict:
        artifact_id = (
            await session.execute(
                text(
                    "SELECT id FROM trading.artifact_objects WHERE sha256=:sha "
                    "ORDER BY id LIMIT 1"
                ),
                {"sha": sha256},
            )
        ).scalar_one()
        ref = await self._artifact_catalog.load_artifact_ref(session, artifact_id)
        assert self._artifacts is not None
        data = await asyncio.to_thread(self._artifacts.get_bytes, ref)
        decoded = json.loads(data)
        if not isinstance(decoded, dict):
            raise ValueError("cached_output_not_object")
        return decoded

    async def record_cache_hit(
        self,
        session: AsyncSession,
        *,
        plan_kwargs: dict[str, Any],
        source_invocation_id: int,
        occurred_at: datetime,
        model_request: ModelRequest,
        code_hash: str,
    ) -> int:
        """Materialize an exact cache hit as a complete, independent ACCEPTED attempt.

        The new attempt owns a replay-exact request Artifact and validator rows. Immutable
        response artifacts are referenced from the exact source invocation; ``parent`` records
        the invocation-level source lineage. No provider is called and all usage/cost is zero.
        """
        hit = await self.check_cache(
            session, model_request=model_request, code_hash=code_hash
        )
        if not hit.hit or hit.source_invocation_id != source_invocation_id:
            raise ValueError("cache_source_not_exact")

        kwargs = {
            **plan_kwargs,
            "occurred_at": occurred_at,
            "parent_invocation_id": source_invocation_id,
        }
        invocation_id = await self.plan(session, **kwargs)
        claim = await self._claim_started(session, invocation_id, occurred_at)
        if claim is None:
            raise RuntimeError("cache_attempt_claim_lost")
        self._assert_claim_matches_request(
            claim,
            model_role_binding_id=int(plan_kwargs["model_role_binding_id"]),
            request=model_request,
        )
        self._assert_blind_request(claim, model_request)

        request_evidence = await self._write_request_artifacts(
            invocation_id=invocation_id,
            request=model_request,
        )
        await self._persist_request_evidence(
            session,
            invocation_id=invocation_id,
            evidence=request_evidence,
        )

        source = (
            await session.execute(
                text(
                    "SELECT returned_provider, returned_route, returned_model, "
                    "raw_response_artifact_ref, parsed_output_artifact_ref, "
                    "normalized_output_artifact_ref, accepted_output_binding "
                    "FROM trading.ai_invocations WHERE id=:id "
                    "AND lifecycle_state='ACCEPTED' AND network_policy='NONE'"
                ),
                {"id": source_invocation_id},
            )
        ).mappings().one_or_none()
        if source is None or (
            source["normalized_output_artifact_ref"]
            != source["accepted_output_binding"]
        ):
            raise ValueError("cache_source_evidence_invalid")
        response_refs = [
            source["raw_response_artifact_ref"],
            source["parsed_output_artifact_ref"],
            source["normalized_output_artifact_ref"],
        ]
        if not all(response_refs):
            raise ValueError("cache_source_evidence_incomplete")

        copied = await session.execute(
            text(
                "INSERT INTO trading.ai_validation_results "
                "(occurred_at, invocation_id, invocation_occurred_at, validator_name, "
                " validator_version, passed, severity, reason_code, details_artifact_hash) "
                "SELECT :at, :new_id, :at, validator_name, validator_version, passed, "
                "severity, reason_code, details_artifact_hash "
                "FROM trading.ai_validation_results WHERE invocation_id=:source_id"
            ),
            {
                "at": claim["occurred_at"],
                "new_id": invocation_id,
                "source_id": source_invocation_id,
            },
        )
        if copied.rowcount < 1:
            raise ValueError("cache_source_validators_missing")
        hard_failures = (
            await session.execute(
                text(
                    "SELECT count(*) FROM trading.ai_validation_results "
                    "WHERE invocation_id=:id AND severity='hard' AND passed IS NOT TRUE"
                ),
                {"id": invocation_id},
            )
        ).scalar_one()
        if hard_failures:
            raise ValueError("cache_source_validator_failed")

        refs = request_evidence.all_refs()
        artifact_ids = await self._register_artifacts(session, refs)
        for sha in response_refs:
            artifact_id = (
                await session.execute(
                    text(
                        "SELECT id FROM trading.artifact_objects WHERE sha256=:sha "
                        "AND compression='none' ORDER BY id LIMIT 1"
                    ),
                    {"sha": sha},
                )
            ).scalar_one_or_none()
            if artifact_id is None:
                raise ValueError("cache_source_artifact_missing")
            artifact_ids[sha] = int(artifact_id)
        for from_sha, to_sha, relation in (
            (request_evidence.request.sha256, response_refs[0], "READS"),
            (request_evidence.prompt.sha256, response_refs[0], "READS"),
            (request_evidence.schema.sha256, response_refs[1], "VALIDATES"),
            (response_refs[0], response_refs[1], "PRODUCES"),
            (response_refs[1], response_refs[2], "PRODUCES"),
        ):
            if from_sha == to_sha:
                continue
            await session.execute(
                text(
                    "INSERT INTO trading.artifact_lineage_edges "
                    "(from_artifact_id,to_artifact_id,relation,invocation_ref) "
                    "VALUES (:f,:t,:r,:i) ON CONFLICT DO NOTHING"
                ),
                {
                    "f": artifact_ids[from_sha],
                    "t": artifact_ids[to_sha],
                    "r": relation,
                    "i": f"ai-invocation:{invocation_id}",
                },
            )

        updated = await session.execute(
            text(
                "UPDATE trading.ai_invocations SET lifecycle_state='ACCEPTED', "
                "result='cache_hit', parent_invocation_id=:source_id, "
                "returned_provider=:provider, returned_route=:route, returned_model=:model, "
                "raw_response_artifact_ref=:raw_ref, parsed_output_artifact_ref=:parsed_ref, "
                "normalized_output_artifact_ref=:normalized_ref, "
                "accepted_output_binding=:binding, response_at=:at, parsed_at=:at, "
                "validated_at=:at, accepted_at=:at, completed_at=:at, "
                "input_tokens=0, cache_tokens=0, output_tokens=0, reasoning_tokens=0, "
                "tool_count=0, search_count=0, cost_estimated=0, "
                "cost_reconciliation='CACHE_HIT' "
                "WHERE id=:id AND lifecycle_state='STARTED'"
            ),
            {
                "id": invocation_id,
                "source_id": source_invocation_id,
                "provider": source["returned_provider"],
                "route": source["returned_route"],
                "model": source["returned_model"],
                "raw_ref": response_refs[0],
                "parsed_ref": response_refs[1],
                "normalized_ref": response_refs[2],
                "binding": response_refs[2],
                "at": occurred_at,
            },
        )
        if updated.rowcount != 1:
            raise RuntimeError("cache_attempt_terminalize_claim_lost")
        return invocation_id

    async def _terminal_failure(
        self, session: AsyncSession, invocation_id: int, exc: ProviderError
    ) -> AttemptOutcome:
        state = "TIMEOUT" if exc.reason.endswith("timeout") else "FAILED"
        await session.execute(
            text(
                "UPDATE trading.ai_invocations SET lifecycle_state=:state, "
                "terminal_reason=:reason, retriable=:retriable, completed_at=:t, "
                "cost_reconciliation='UNPRICED' "
                "WHERE id=:id AND lifecycle_state='STARTED'"
            ),
            {
                "id": invocation_id,
                "state": state,
                "reason": exc.reason,
                "retriable": exc.retriable,
                "t": datetime.now(timezone.utc),
            },
        )
        await session.commit()
        return AttemptOutcome(invocation_id, state, "failed", exc.reason)

    async def _terminal_unknown(
        self, session: AsyncSession, invocation_id: int, reason: str
    ) -> AttemptOutcome:
        await session.execute(
            text(
                "UPDATE trading.ai_invocations SET lifecycle_state='UNKNOWN', "
                "terminal_reason=:reason, completed_at=:t, "
                "cost_reconciliation='UNPRICED' "
                "WHERE id=:id AND lifecycle_state='STARTED'"
            ),
            {"id": invocation_id, "reason": reason, "t": datetime.now(timezone.utc)},
        )
        await session.commit()
        return AttemptOutcome(invocation_id, "UNKNOWN", "unknown", reason)

    async def _terminal_cancelled(
        self, session: AsyncSession, invocation_id: int, reason: str
    ) -> AttemptOutcome:
        await session.execute(
            text(
                "UPDATE trading.ai_invocations SET lifecycle_state='CANCELLED', "
                "terminal_reason=:reason, completed_at=:t, "
                "cost_reconciliation='UNPRICED' "
                "WHERE id=:id AND lifecycle_state='STARTED'"
            ),
            {"id": invocation_id, "reason": reason, "t": datetime.now(timezone.utc)},
        )
        await session.commit()
        return AttemptOutcome(invocation_id, "CANCELLED", "cancelled", reason)

    async def _terminal_rejected(
        self, session: AsyncSession, invocation_id: int, reason: str
    ) -> AttemptOutcome:
        await session.execute(
            text(
                "UPDATE trading.ai_invocations SET lifecycle_state='REJECTED', "
                "result='rejected', terminal_reason=:reason, completed_at=:t, "
                "cost_reconciliation='NOT_INCURRED' "
                "WHERE id=:id AND lifecycle_state='STARTED'"
            ),
            {"id": invocation_id, "reason": reason[:128], "t": datetime.now(timezone.utc)},
        )
        await session.commit()
        return AttemptOutcome(invocation_id, "REJECTED", "rejected", reason)

    async def _terminal_rejected_response(
        self,
        session: AsyncSession,
        invocation_id: int,
        response: ModelResponse,
        reason: str,
    ) -> AttemptOutcome:
        await session.execute(
            text(
                "UPDATE trading.ai_invocations SET lifecycle_state='REJECTED', "
                "result='rejected', terminal_reason=:reason, returned_provider=:rp, "
                "returned_route=:rr, returned_model=:rm, response_at=:at, completed_at=:at "
                "WHERE id=:id AND lifecycle_state='STARTED'"
            ),
            {
                "id": invocation_id,
                "reason": reason[:128],
                "rp": response.returned_provider,
                "rr": response.returned_route,
                "rm": response.returned_model,
                "at": response.received_at,
            },
        )
        await session.commit()
        return AttemptOutcome(invocation_id, "REJECTED", "rejected", reason)
