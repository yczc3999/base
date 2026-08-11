"""P2 decision 共享 fixture helper（WP-03 Checkpoint A）。

- ``load_p2_spec``：读取冻结的 ``p_execution_spec_v1.json``。
- ``freeze_p2_release``：通过真实 DB 创建 active ``execution_spec_versions`` 与
  ``SHADOW_REFERENCE`` ``capital_permission_manifests``，再由 release manifest 精确引用
  （禁止只在 Python 常量里假装冻结）。
- ``p2_spec_hash``：spec JSON 的 canonical SHA-256（写入 manifest 的 P_EXECUTION_SPEC_MANIFEST）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import text

FIXTURE_DIR = Path(__file__).resolve().parent
P2_SPEC_PATH = FIXTURE_DIR / "p_execution_spec_v1.json"


def load_p2_spec() -> dict:
    with open(P2_SPEC_PATH, encoding="utf-8") as f:
        return json.load(f)


def p2_spec_sha256() -> str:
    with open(P2_SPEC_PATH, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


async def freeze_p2_release(session, *, objective_contract_id: int, strategy_version_id: int) -> dict:
    """创建 active exec spec + SHADOW_REFERENCE capital permission + release manifest。

    返回 {execution_spec_version_id, capital_permission_manifest_id, release_manifest_id}。
    """
    spec = load_p2_spec()
    exec_content = {
        "execution_spec_key": spec["execution"]["execution_spec_key"],
        "execution_spec_version": spec["execution"]["execution_spec_version"],
        "hold_to_resolution": spec["execution"]["hold_to_resolution"],
        "short_sell_to_open": spec["execution"]["short_sell_to_open"],
        "execution_mode": spec["execution"]["execution_mode"],
        "authorized_capital": spec["execution"]["authorized_capital"],
        "price_convention": spec["execution"]["price_convention"],
        "depth_walk": spec["execution"]["depth_walk"],
        "fee": spec["execution"]["fee"],
        "slippage": spec["execution"]["slippage"],
        "latency": spec["execution"]["latency"],
        "staleness": spec["execution"]["staleness"],
        "capacity": spec["execution"]["capacity"],
        "cost_accounting": spec["execution"]["cost_accounting"],
        "risk_limits": spec["execution"]["risk_limits"],
        "rounding": spec["execution"]["rounding"],
    }
    exec_hash = hashlib.sha256(
        json.dumps(exec_content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    exec_id = (
        await session.execute(
            text(
                "INSERT INTO trading.execution_spec_versions "
                "(spec_key, version_no, content, schema_version, content_hash, status) "
                "VALUES (:key, :v, CAST(:content AS jsonb), 1, :hash, 'active') RETURNING id"
            ),
            {
                "key": spec["execution"]["execution_spec_key"],
                "v": spec["execution"]["execution_spec_version"],
                "content": json.dumps(exec_content),
                "hash": exec_hash,
            },
        )
    ).scalar_one()

    perm = spec["capital_permission"]
    cap_content = {
        "name": perm["name"], "mode": perm["mode"],
        "evaluation_capital": perm["evaluation_capital"],
        "authorized_capital": perm["authorized_capital"],
        "kill_switch": perm["kill_switch"],
        "capability": perm["capability"],
        "limits": perm["limits"],
    }
    cap_hash = hashlib.sha256(
        json.dumps(cap_content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    cap_id = (
        await session.execute(
            text(
                "INSERT INTO trading.capital_permission_manifests "
                "(name, mode, capability, limits, evaluation_capital, authorized_capital, "
                " kill_switch, content_hash, status) "
                "VALUES (:name, :mode, CAST(:capability AS jsonb), CAST(:limits AS jsonb), "
                " :eval, :auth, :kill, :hash, 'active') RETURNING id"
            ),
            {
                "name": perm["name"], "mode": "shadow",
                "capability": json.dumps(perm["capability"]),
                "limits": json.dumps(perm["limits"]),
                "eval": perm["evaluation_capital"], "auth": perm["authorized_capital"],
                "kill": perm["kill_switch"], "hash": cap_hash,
            },
        )
    ).scalar_one()

    # objective/strategy 必须 active 才能被 release 引用；由调用方保证。
    release = spec["release"]
    release_hash = hashlib.sha256(
        json.dumps(
            {"release_name": release["release_name"], "db_revision": release["db_revision"]},
            sort_keys=True, separators=(",", ":"),
        ).encode()
    ).hexdigest()
    rel_id = (
        await session.execute(
            text(
                "INSERT INTO trading.release_manifests "
                "(release_name, config_version_id, strategy_version_id, execution_spec_version_id, "
                " capital_permission_manifest_id, git_sha, image_digest, db_revision, total_hash, status) "
                "VALUES (:name, :cfg, :strat, :exec, :cap, :git, :img, :db, :hash, 'active') RETURNING id"
            ),
            {
                "name": release["release_name"],
                "cfg": 1,  # runtime_config_versions seed 由调用方提供 id=1
                "strat": strategy_version_id,
                "exec": exec_id,
                "cap": cap_id,
                "git": release["git_sha"], "img": release["image_digest"],
                "db": release["db_revision"], "hash": release_hash,
            },
        )
    ).scalar_one()
    return {
        "execution_spec_version_id": exec_id,
        "capital_permission_manifest_id": cap_id,
        "release_manifest_id": rel_id,
    }
