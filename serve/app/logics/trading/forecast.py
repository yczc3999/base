"""Forecast Logic（WP-02 Checkpoint A）：G6 blind forecast gate 与原子不可变提交。

- 从冻结 episode/component/world-schema/prior/evidence 材料构建 ``forecast_input_manifest``。
- 用 domain.probability 确定性校验 ``Q/U`` coherence、push-forward ``μ``、``V``/bounds、
  Bernoulli ``p_blind``；LLM 输出不能直接成为 projection 或 PASS Gate（任务 §2.4）。
- G6 PASS、submission、全部 spec×token projection、checks、lease、Gate、workflow event/outbox
  同一 UoW 原子提交；任何一项失败 → BLIND_COMMITTED 数=0（任务 §2.8）。
- 提交后 submission/episode 不可变；纯 quote/depth/cost/position 变化不得使 lease 失效
  （架构 §4.4）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.domain.trading.probability import (
    bernoulli_p_blind,
    expected_payout,
    normalize_q,
    payout_bounds,
    push_forward_mu,
    validate_u,
)
from app.outbox.contracts import create_envelope
from app.outbox.repository import OutboxRepository
from app.repositories.trading.forecast import ForecastRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.schemas.trading.forecast import (
    CoherenceCheckInput,
    ForecastLeaseInput,
    ForecastSubmissionInput,
    PayoutProjectionInput,
)

G6_PASS = "PASS"
G6_FAIL = "FAIL"

G6_REASON_EPISODE = "g6_episode_not_routed"
G6_REASON_COGNITION = "g6_cognition_not_evidence_ready"
G6_REASON_MANIFEST = "g6_manifest_hash_mismatch"
G6_REASON_Q = "g6_q_incoherent"
G6_REASON_U = "g6_u_incoherent"
G6_REASON_Q_NOT_IN_U = "g6_q_not_in_u"
G6_REASON_EMPTY_U = "g6_u_empty"
G6_REASON_SPEC = "g6_spec_missing"
G6_REASON_TOKEN = "g6_token_projection_incomplete"
G6_REASON_PROJECTION = "g6_projection_recompute_mismatch"
G6_REASON_LEASE = "g6_lease_missing"
G6_REASON_MEMBER = "g6_component_member_invalid"

# 输入 manifest 中声明变量（输入乱序不改变 hash —— Logic 内排序）
_MANIFEST_KEYS = (
    "evidence_bundle_hash",
    "contract_spec_set_hash",
    "world_schema_hash",
    "prior_hash",
    "taxonomy_hash",
    "model_binding_hash",
    "prompt_hash",
    "code_hash",
)

_BLIND_COMMIT_TOPIC = "trading.blind_commit.v1"


@dataclass(frozen=True)
class InputManifestMaterial:
    """G6 需要的 frozen AI/策略版本 hash（由调用方从 release/binding/prompt 解析）。"""

    taxonomy_hash: str
    model_binding_hash: str
    prompt_hash: str
    code_hash: str


@dataclass(frozen=True)
class G6Result:
    ok: bool
    reason: str | None = None
    manifest_id: int | None = None
    manifest_hash: str | None = None
    submission_id: int | None = None
    committed: bool = False
    committed_count: int = 0
    projection_count: int = 0
    coherence_failures: list[str] | None = None


class ForecastLogic:
    def __init__(
        self,
        forecast: ForecastRepository,
        workflow: WorkflowRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._forecast = forecast
        self._workflow = workflow or WorkflowRepository()
        self._outbox = outbox or OutboxRepository()

    # ---------------- G6：blind forecast + atomic immutable commit ----------------

    async def run_g6(
        self,
        uow: UnitOfWork,
        *,
        episode_id: int,
        submission: ForecastSubmissionInput,
        material: InputManifestMaterial,
        lease: ForecastLeaseInput,
        version_manifest_id: int,
        policy_hash: str,
    ) -> G6Result:
        """校验 Q/U coherence + 重算全部 projection，然后同一 UoW 原子 commit。

        任何 hard check 失败：不生成 committed submission，episode 进入 PRE_COMMIT_TERMINAL
        （调用方负责 terminal 标记）。成功路径原子写 submission/projections/checks/lease/Gate/
        workflow event/outbox 并推进 episode 到 BLIND_COMMITTED。
        """
        chain = await self._forecast.episode_cognition_chain(uow.session, episode_id)
        if chain is None or chain["status"] != "ROUTED":
            return G6Result(False, G6_REASON_EPISODE)
        if chain["cognition_status"] != "EVIDENCE_READY":
            return G6Result(False, G6_REASON_COGNITION)

        # ---- 冻结材料：active prior + frozen bundle + component members ----
        prior = await self._forecast.get_active_prior(uow.session, episode_id)
        if prior is None:
            return G6Result(False, "g6_prior_missing")
        bundle = await self._forecast.get_frozen_bundle(
            uow.session, episode_id=episode_id
        )
        if bundle is None:
            return G6Result(False, "g6_bundle_missing")
        members = await self._forecast.spec_members_for_component(
            uow.session, chain["component_version_id"]
        )
        if not members:
            return G6Result(False, G6_REASON_SPEC)

        # ---- Q/U coherence（确定性；fail-closed 不写任何事实）----
        coherence_failures: list[str] = []
        q_values = submission.Q.values
        try:
            q_dec = normalize_q(q_values)
        except ValueError as exc:
            return G6Result(False, G6_REASON_Q, coherence_failures=[str(exc)])
        try:
            u_members = validate_u(
                [member.values for member in submission.U], q=q_dec
            )
        except ValueError as exc:
            reason = G6_REASON_Q_NOT_IN_U if "q" in str(exc) else G6_REASON_U
            return G6Result(False, reason, coherence_failures=[str(exc)])

        # ---- 输入 manifest（乱序不改变 hash；纯计算，失败不落库）----
        contract_spec_set_hash = canonical_hash(
            {"specs": sorted(member["spec_hash"] for member in members)}
        )
        manifest_dict = {
            "episode_key": chain["episode_key"],
            "evidence_bundle_hash": bundle["bundle_hash"],
            "contract_spec_set_hash": contract_spec_set_hash,
            "world_schema_hash": chain["world_schema_hash"],
            "prior_hash": prior["content_hash"],
            "taxonomy_hash": material.taxonomy_hash,
            "model_binding_hash": material.model_binding_hash,
            "prompt_hash": material.prompt_hash,
            "code_hash": material.code_hash,
        }
        manifest_hash = canonical_hash({key: manifest_dict[key] for key in _MANIFEST_KEYS})
        manifest_key = f"manifest:{chain['episode_key']}:{manifest_hash[:12]}"

        # ---- 每个 member spec×token 的确定性 projection ----
        checks: list[CoherenceCheckInput] = []
        projections: list[dict[str, Any]] = []
        world_state_ids = sorted(q_dec.keys())
        for member in members:
            spec_id = member["contract_spec_id"]
            h_c = member["h_c"]
            if not isinstance(h_c, dict):
                return G6Result(False, G6_REASON_MEMBER)
            # h_c 必须对全部 world states total
            missing_hc = [ws for ws in world_state_ids if ws not in h_c]
            if missing_hc:
                return G6Result(False, f"{G6_REASON_MEMBER}:hc_not_total:{spec_id}")
            payouts = await self._forecast.payouts_for_spec(uow.session, spec_id)
            if not payouts:
                return G6Result(False, f"{G6_REASON_TOKEN}:{spec_id}")
            for payout in payouts:
                token_id = payout["pm_token_id"]
                payout_ir = payout["function_ir"]
                try:
                    mu = push_forward_mu(q_dec, h_c=h_c, payout_ir=payout_ir)
                    v = expected_payout(mu)
                    lower, upper = payout_bounds(
                        u_members, h_c=h_c, payout_ir=payout_ir
                    )
                    p_blind = bernoulli_p_blind(mu)
                except ValueError as exc:
                    return G6Result(False, f"{G6_REASON_PROJECTION}:{spec_id}", coherence_failures=[str(exc)])
                algorithm_hash = canonical_hash(
                    {
                        "algorithm": "push-forward/v1",
                        "q_hash": canonical_hash(q_values),
                        "h_c_hash": canonical_hash(h_c),
                        "g_hash": payout["content_hash"],
                    }
                )
                projections.append(
                    {
                        "contract_spec_id": spec_id,
                        "pm_token_id": token_id,
                        "mu": {key: format(val.normalize(), "f") for key, val in mu.items()},
                        "v": str(v.normalize()),
                        "u_lower": str(lower.normalize()),
                        "u_upper": str(upper.normalize()),
                        "p_blind": str(p_blind.normalize()) if p_blind is not None else None,
                        "algorithm_hash": algorithm_hash,
                        "h_c_hash": canonical_hash(h_c),
                        "g_hash": payout["content_hash"],
                    }
                )
            checks.append(
                CoherenceCheckInput(
                    check_name=f"projection_complete:{spec_id}",
                    passed=True,
                    severity="hard",
                )
            )
        checks.append(CoherenceCheckInput(check_name="q_nonneg_total", passed=True, severity="hard"))
        checks.append(CoherenceCheckInput(check_name="u_contains_q", passed=True, severity="hard"))

        # ---- manifest + submission + projections + checks + lease（同一 UoW）----
        contract_schema_prior_evidence_hash = canonical_hash(
            {
                "contract_spec_set_hash": contract_spec_set_hash,
                "world_schema_hash": chain["world_schema_hash"],
                "prior_hash": prior["content_hash"],
                "evidence_bundle_hash": bundle["bundle_hash"],
            }
        )
        manifest_id = await self._forecast.insert_forecast_input_manifest(
            uow.session,
            episode_id=episode_id,
            manifest_key=manifest_key,
            manifest_hash=manifest_hash,
            evidence_bundle_hash=bundle["bundle_hash"],
            contract_spec_set_hash=contract_spec_set_hash,
            world_schema_hash=chain["world_schema_hash"],
            prior_hash=prior["content_hash"],
            taxonomy_hash=material.taxonomy_hash,
            model_binding_hash=material.model_binding_hash,
            prompt_hash=material.prompt_hash,
            code_hash=material.code_hash,
            content=manifest_dict,
        )
        submission_id = await self._forecast.insert_forecast_submission(
            uow.session,
            episode_id=episode_id,
            submission_key=submission.submission_key,
            Q=submission.Q.values,
            U=[member.values for member in submission.U],
            forecast_input_manifest_id=manifest_id,
            contract_schema_prior_evidence_hash=contract_schema_prior_evidence_hash,
            algorithm_hash=canonical_hash(
                {
                    "algorithm": "blind-commit/v1",
                    "q_algorithm": "normalize/v1",
                    "u_algorithm": "validate/v1",
                    "projection_algorithm": "push-forward/v1",
                }
            ),
        )
        await self._forecast.insert_payout_projections(
            uow.session, submission_id=submission_id, rows=projections
        )
        await self._forecast.insert_coherence_checks(
            uow.session,
            submission_id=submission_id,
            rows=[check.model_dump(mode="json") for check in checks],
        )
        await self._forecast.insert_forecast_lease(
            uow.session,
            submission_id=submission_id,
            valid_until=lease.valid_until,
            invalidation_conditions=lease.invalidation_conditions,
            evidence_hash=lease.evidence_hash,
            schema_hash=lease.schema_hash,
            spec_hash=lease.spec_hash,
        )

        # ---- Gate G6 PASS + workflow event + outbox（同一 UoW）----
        committed_at = datetime.now(timezone.utc)
        await self._workflow.insert_gate_decision(
            uow.session,
            gate="G6",
            target_kind="episode",
            target_id=episode_id,
            input_hash=manifest_hash,
            policy_hash=policy_hash,
            version_manifest_id=version_manifest_id,
            result=G6_PASS,
            reason_code=None,
            committed_at=committed_at,
        )
        submission_business_key = f"{chain['episode_key']}:{submission.submission_key}"
        event = create_envelope(
            topic=_BLIND_COMMIT_TOPIC,
            schema_version=1,
            aggregate_type="forecast_submission",
            aggregate_id=submission_business_key,
            idempotency_key=f"blind-commit:{submission_business_key}",
            priority=100,
            payload={
                "episode_key": chain["episode_key"],
                "submission_key": submission.submission_key,
                "manifest_hash": manifest_hash,
            },
            release_manifest_id=version_manifest_id,
        )
        await self._outbox.enqueue(uow.session, event)

        # ---- 原子封账：submission + episode 同时终态 ----
        committed = await self._forecast.commit_submission(
            uow.session, submission_id, committed_at=committed_at
        )
        if not committed:
            return G6Result(False, "g6_commit_conflict", coherence_failures=coherence_failures)
        if not await self._forecast.mark_episode_committed(
            uow.session, episode_id, committed_at=committed_at
        ):
            return G6Result(False, "g6_episode_commit_conflict", coherence_failures=coherence_failures)
        committed_count = await self._forecast.submission_count_committed(
            uow.session, episode_id
        )
        return G6Result(
            True,
            manifest_id=manifest_id,
            manifest_hash=manifest_hash,
            submission_id=submission_id,
            committed=True,
            committed_count=committed_count,
            projection_count=len(projections),
            coherence_failures=[],
        )
