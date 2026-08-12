"""Dashboard —— 只读 WP-04 五张 projection（WP-07A Checkpoint B）。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from app.deps import AuthInfo, get_admin_read_db, require_all_perms
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.admin.trading.common import get_admin_logic, get_admin_repo
from app.utils.response import ok

router = APIRouter()
_PROJECTIONS = ("ops_health_current", "pipeline_funnel_hourly", "account_risk_current",
                "provider_cost_daily", "latest_chain_summary")


@router.get("/v2/dashboard")
async def dashboard(request: Request, session: AsyncSession = Depends(get_admin_read_db),
                    auth: AuthInfo = Depends(require_all_perms("v2:dashboard:view"))):
    logic = get_admin_logic()
    as_of = (await logic.freeze_as_of(session)).isoformat().replace("+00:00", "Z")
    blocks = {}
    for name in _PROJECTIONS:
        rows = await get_admin_repo().dashboard_projection(session, name)
        row_as_ofs = [_parse_pg_utc(row["as_of"]) for row in rows]
        latest_as_of = max(row_as_ofs) if row_as_ofs else None
        metadata_rows = [row for row in rows if _parse_pg_utc(row["as_of"]) == latest_as_of]
        hashes = sorted(row["projection_hash"] for row in metadata_rows)
        block_hash = _combined_projection_hash(hashes) if hashes else None
        watermarks = [int(row["source_high_watermark"]) for row in metadata_rows]
        versions = [int(row["projection_version"]) for row in metadata_rows]
        blocks[name] = {
            "rows": rows,
            "freshness_status": _freshness_status(latest_as_of),
            "as_of": _utc_iso(latest_as_of) if latest_as_of else as_of,
            "source_high_watermark": str(max(watermarks)) if watermarks else None,
            "projection_version": max(versions) if versions else None,
            "projection_hash": block_hash,
        }
    return ok({
        "blocks": blocks,
        "as_of": as_of,
    })


def _parse_pg_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _freshness_status(latest_as_of: datetime | None) -> str:
    """不因“有行”伪造 fresh；一小时以上未刷新显式 stale。"""
    if latest_as_of is None:
        return "missing"
    age_s = (datetime.now(timezone.utc) - latest_as_of).total_seconds()
    return "fresh" if age_s <= 3600 else "stale"


def _combined_projection_hash(hashes: list[str]) -> str:
    """一块可能有多行；以有序 row hashes 构成稳定块 hash。"""
    import hashlib

    if len(hashes) == 1:
        return hashes[0]
    return hashlib.sha256("".join(hashes).encode("ascii")).hexdigest()
