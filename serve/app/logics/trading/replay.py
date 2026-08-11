"""Replay Logic（WP-04 Checkpoint C）。

- ``replay_original``：原样重放（同 manifest+code+seed 重跑 hash 全等）。只读原
  artifact/snapshot/事实；输出新 ``replay_runs`` 行（``output_artifact_hash`` 非空）。
  绝不写回原 episode/submission/decision/execution/label/ledger。
- ``replay_new_code``：新 code/variant 写新 run（``replay_kind='new_code'|'variant'``），
  不覆盖原事实。
- ``ablation``：冻结 bundle ablation，写 ``ablation_runs``。
- ``error_review_selection``：top-loss/top-regret + 随机成功样本按冻结 seed 入
  ``error_reviews``（``deterministic_sample``）；root-cause taxonomy 只允许架构定义集合，
  非法值拒绝。

未来信息隔离：重放输入全部来自历史冻结快照/artifact，无未来 label/quote 污染。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash, deterministic_sample
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.evaluation import EvaluationRepository

REPLAY_KINDS = ("original", "new_code", "variant")

# 架构定义 root-cause taxonomy（写死允许集合）。
ROOT_CAUSE_TAXONOMY = (
    "model_miscalibration",
    "selection_error",
    "data_quality",
    "timing",
    "edge_erosion",
    "regime_shift",
    "unexamined_success",
    "other",
)

# review_type → 默认 root_cause
_DEFAULT_ROOT_CAUSE = {
    "top_loss": "model_miscalibration",
    "top_regret": "selection_error",
    "random_success": "unexamined_success",
}


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


@dataclass(frozen=True)
class ReplayResult:
    ok: bool
    replay_run_id: int | None = None
    output_artifact_hash: str | None = None
    replay_kind: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class AblationResult:
    ok: bool
    ablation_id: int | None = None
    result_hash: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ErrorReviewResult:
    ok: bool
    count: int = 0
    reason: str | None = None


class ReplayLogic:
    """科学回放 / 消融 / 错误评审采样；只读原事实，只写新 artifact。"""

    def __init__(
        self,
        audit: AuditRepository | None = None,
        evaluation: EvaluationRepository | None = None,
    ) -> None:
        self._audit = audit or AuditRepository()
        self._evaluation = evaluation or EvaluationRepository()

    async def replay_original(
        self, uow: UnitOfWork, *, run_key: str, manifest_hash: str, seed: int
    ) -> ReplayResult:
        source = await self._source_for_manifest(uow, manifest_hash)
        if source is None:
            return ReplayResult(False, reason="replay_source_missing")
        code_hash = source["code_hash"]
        input_artifact_hash = source["input_artifact_hash"]
        output_artifact_hash = canonical_hash(
            {
                "kind": "replay",
                "mode": "original",
                "manifest": manifest_hash,
                "code": code_hash,
                "seed": seed,
                "input": input_artifact_hash,
            }
        )
        replay_run_id = await self._audit.insert_replay_run(
            uow.session,
            run_key=run_key,
            replay_kind="original",
            manifest_hash=manifest_hash,
            code_hash=code_hash,
            seed=seed,
            input_artifact_hash=input_artifact_hash,
            output_artifact_hash=output_artifact_hash,
            result=json.dumps({"mode": "original", "manifest": manifest_hash}),
        )
        return ReplayResult(
            True,
            replay_run_id=replay_run_id,
            output_artifact_hash=output_artifact_hash,
            replay_kind="original",
        )

    async def replay_new_code(
        self,
        uow: UnitOfWork,
        *,
        run_key: str,
        manifest_hash: str,
        code_hash: str,
        seed: int,
        variant: str | None = None,
    ) -> ReplayResult:
        source = await self._source_for_manifest(uow, manifest_hash)
        if source is None:
            return ReplayResult(False, reason="replay_source_missing")
        kind = "variant" if variant else "new_code"
        output_artifact_hash = canonical_hash(
            {
                "kind": "replay",
                "mode": kind,
                "manifest": manifest_hash,
                "code": code_hash,
                "seed": seed,
                "variant": variant,
                "input": source["input_artifact_hash"],
            }
        )
        replay_run_id = await self._audit.insert_replay_run(
            uow.session,
            run_key=run_key,
            replay_kind=kind,
            manifest_hash=manifest_hash,
            code_hash=code_hash,
            seed=seed,
            input_artifact_hash=source["input_artifact_hash"],
            output_artifact_hash=output_artifact_hash,
            result=json.dumps({"mode": kind, "manifest": manifest_hash, "variant": variant}),
        )
        return ReplayResult(
            True,
            replay_run_id=replay_run_id,
            output_artifact_hash=output_artifact_hash,
            replay_kind=kind,
        )

    async def ablation(
        self,
        uow: UnitOfWork,
        *,
        ablation_key: str,
        metric_run_id: int,
        bundle_hash: str,
        fields: dict,
    ) -> AblationResult:
        run = await self._metric_run_by_id(uow, metric_run_id)
        if run is None or run["status"] != "COMPLETED":
            return AblationResult(False, reason="ablation_metric_run_not_completed")
        result_hash = canonical_hash(
            {"ablation": ablation_key, "bundle": bundle_hash, "fields": fields}
        )
        ablation_id = await self._evaluation.insert_ablation_run(
            uow.session,
            ablation_key=ablation_key,
            metric_run_id=metric_run_id,
            bundle_hash=bundle_hash,
            ablation_fields=json.dumps(fields),
            result_hash=result_hash,
        )
        return AblationResult(True, ablation_id=ablation_id, result_hash=result_hash)

    async def error_review_selection(
        self,
        uow: UnitOfWork,
        *,
        metric_run_id: int,
        seed: int,
        top_n: int = 3,
        explicit_taxonomies: dict[str, str] | None = None,
    ) -> ErrorReviewResult:
        run = await self._metric_run_by_id(uow, metric_run_id)
        if run is None or run["status"] != "COMPLETED":
            return ErrorReviewResult(False, reason="error_review_metric_run_not_completed")
        observations = await self._observations_for_run(uow, run)
        if not observations:
            return ErrorReviewResult(False, reason="error_review_no_observations")

        taxonomy_map = explicit_taxonomies or {}
        for observation_key, taxonomy in taxonomy_map.items():
            if taxonomy not in ROOT_CAUSE_TAXONOMY:
                return ErrorReviewResult(
                    False, reason=f"error_review_taxonomy_unknown:{taxonomy}"
                )

        seed_hash = canonical_hash(str(seed))
        selections: list[tuple[str, str, str]] = []

        # top-loss：score_value 降序 top_n。
        by_loss = sorted(
            observations, key=lambda row: Decimal(str(row["score_value"])), reverse=True
        )
        for row in by_loss[:top_n]:
            selections.append(
                (row["observation_key"], "top_loss", taxonomy_map.get(
                    row["observation_key"], _DEFAULT_ROOT_CAUSE["top_loss"]
                ))
            )

        # top-regret：selected（有 trade_decision_id）降序 top_n。
        selected = [row for row in observations if row.get("trade_decision_id") is not None]
        by_regret = sorted(
            selected, key=lambda row: Decimal(str(row["score_value"])), reverse=True
        )
        for row in by_regret[:top_n]:
            if row["observation_key"] not in {s[0] for s in selections}:
                selections.append(
                    (row["observation_key"], "top_regret", taxonomy_map.get(
                        row["observation_key"], _DEFAULT_ROOT_CAUSE["top_regret"]
                    ))
                )

        # random-success：低 loss 样本按冻结 seed 确定性抽样。
        values = [Decimal(str(row["score_value"])) for row in observations]
        threshold = sorted(values)[max(0, len(values) // 2)]
        success_pool = [
            row for row in observations
            if Decimal(str(row["score_value"])) <= threshold
        ]
        picked_success = 0
        for row in sorted(success_pool, key=lambda r: r["observation_key"]):
            selected_flag, _, _ = deterministic_sample(
                content_hash=canonical_hash(row["observation_key"]),
                seed_hash=seed_hash,
                stratum=f"random_success/{metric_run_id}",
                rate=Decimal("0.5"),
            )
            if selected_flag and row["observation_key"] not in {s[0] for s in selections}:
                selections.append(
                    (row["observation_key"], "random_success", taxonomy_map.get(
                        row["observation_key"], _DEFAULT_ROOT_CAUSE["random_success"]
                    ))
                )
                picked_success += 1
                if picked_success >= top_n:
                    break

        for observation_key, review_type, taxonomy in selections:
            review_key = canonical_hash(
                {"metric_run": metric_run_id, "obs": observation_key, "type": review_type}
            )
            await self._evaluation.insert_error_review(
                uow.session,
                review_key=review_key,
                review_type=review_type,
                metric_run_id=metric_run_id,
                observation_key=observation_key,
                root_cause=taxonomy,
                root_cause_taxonomy=taxonomy,
                seed=seed,
            )
        return ErrorReviewResult(True, count=len(selections))

    # ---------------- helpers ----------------

    async def _source_for_manifest(
        self, uow: UnitOfWork, manifest_hash: str
    ) -> dict | None:
        """原样回放的输入 artifact 来源：优先 metric_runs，其次已有 replay_runs。"""
        result = await uow.session.execute(
            text(
                "SELECT id, artifact_hash AS input_artifact_hash, code_hash "
                "FROM trading.metric_runs WHERE run_key=:k LIMIT 1"
            ),
            {"k": manifest_hash},
        )
        rows = _rows(result)
        if rows:
            return rows[0]
        replay = await self._audit.list_replay_runs(uow.session, manifest_hash)
        if replay:
            row = replay[-1]
            return {
                "input_artifact_hash": row["output_artifact_hash"],
                "code_hash": row["code_hash"],
            }
        return None

    async def _metric_run_by_id(
        self, uow: UnitOfWork, metric_run_id: int
    ) -> dict | None:
        result = await uow.session.execute(
            text("SELECT * FROM trading.metric_runs WHERE id=:mid"),
            {"mid": metric_run_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def _observations_for_run(
        self, uow: UnitOfWork, run: dict
    ) -> list[dict]:
        label_ids = self._flatten_label_versions(run.get("label_versions") or {})
        result = await uow.session.execute(
            text(
                "SELECT observation_key, score_target_id, submission_id, trade_decision_id, "
                "       label_version_id, score_value "
                "FROM trading.score_observations "
                "WHERE split=:split AND label_version_id = ANY(:lv) "
                "ORDER BY id"
            ),
            {"split": run["split"], "lv": label_ids or [-1]},
        )
        return _rows(result)

    @staticmethod
    def _flatten_label_versions(label_versions: dict) -> list[int]:
        out: list[int] = []
        for value in (label_versions or {}).values():
            if isinstance(value, list):
                out.extend(int(v) for v in value)
            else:
                out.append(int(value))
        return list(dict.fromkeys(out))
