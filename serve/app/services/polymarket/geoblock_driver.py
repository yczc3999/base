"""Strict fixture-only geoblock boundary used by WP-06 chain writes.

The caller supplies an explicitly marked, deterministic transport.  The response is
typed and freshness checked at this boundary so a free-form event payload can never
authorize a chain operation.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from app.services.polymarket.base import EgressTripwireError

GeoTransport = Callable[[], dict[str, Any] | Awaitable[dict[str, Any]]]
NowProvider = Callable[[], datetime]
_FIXTURE_MARKER = "__pm_fixture_transport__"


def fixture_geoblock_transport(transport: GeoTransport) -> GeoTransport:
    """Mark a deterministic no-egress geoblock transport."""
    setattr(transport, _FIXTURE_MARKER, True)
    return transport


@dataclass(frozen=True, slots=True)
class GeoblockResult:
    allowed: bool
    observed_at: datetime
    source_version: str
    region_code: str | None

    def artifact_material(self) -> dict[str, Any]:
        return {
            "schema": "polymarket-geoblock/v1",
            "allowed": self.allowed,
            "observed_at": self.observed_at.isoformat(),
            "source_version": self.source_version,
            "region_code": self.region_code,
        }


class GeoblockCheckError(RuntimeError):
    """Fail-closed result carrying only bounded, persistence-safe evidence."""

    def __init__(self, reason_code: str, artifact_material: dict[str, Any]) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.artifact_material = artifact_material


class GeoblockDriver:
    """Fail-closed geoblock check with a 30-second authorization window."""

    def __init__(
        self,
        *,
        transport: GeoTransport | None,
        now_provider: NowProvider | None = None,
        fixture_only: bool = True,
        max_age_seconds: int = 30,
    ) -> None:
        self._transport = transport
        self._fixture_only = bool(fixture_only)
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._max_age = int(max_age_seconds)
        self._calls = 0
        if self._max_age != 30:
            raise ValueError("geoblock_max_age_must_be_30")
        if transport is not None and (
            not self._fixture_only
            or not bool(getattr(transport, _FIXTURE_MARKER, False))
        ):
            raise EgressTripwireError()

    @property
    def fixture_only(self) -> bool:
        return self._fixture_only

    @property
    def transport_calls(self) -> int:
        return self._calls

    async def check(self) -> GeoblockResult:
        transport = self._transport
        if (
            transport is None
            or not self._fixture_only
            or not bool(getattr(transport, _FIXTURE_MARKER, False))
        ):
            raise EgressTripwireError()
        self._calls += 1
        try:
            raw = transport()
            if inspect.isawaitable(raw):
                raw = await raw
        except (AssertionError, asyncio.CancelledError, EgressTripwireError):
            raise
        except Exception as exc:
            raise GeoblockCheckError(
                "geoblock_transport_failure",
                self._rejected_material("geoblock_transport_failure", None),
            ) from None
        if not isinstance(raw, dict) or set(raw) - {
            "allowed", "observed_at", "region_code", "source_version"
        }:
            raise GeoblockCheckError(
                "geoblock_response_malformed",
                self._rejected_material("geoblock_response_malformed", raw),
            )
        if type(raw.get("allowed")) is not bool:
            raise GeoblockCheckError(
                "geoblock_allowed_invalid",
                self._rejected_material("geoblock_allowed_invalid", raw),
            )
        source_version = raw.get("source_version")
        if not isinstance(source_version, str) or not source_version.strip() or len(source_version) > 64:
            raise GeoblockCheckError(
                "geoblock_source_version_invalid",
                self._rejected_material("geoblock_source_version_invalid", raw),
            )
        region = raw.get("region_code")
        if region is not None and (
            not isinstance(region, str)
            or len(region) != 2
            or not region.isascii()
            or not region.isalpha()
            or region != region.upper()
        ):
            raise GeoblockCheckError(
                "geoblock_region_code_invalid",
                self._rejected_material("geoblock_region_code_invalid", raw),
            )
        value = raw.get("observed_at")
        try:
            observed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        except Exception:
            raise GeoblockCheckError(
                "geoblock_observed_at_invalid",
                self._rejected_material("geoblock_observed_at_invalid", raw),
            ) from None
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise GeoblockCheckError(
                "geoblock_observed_at_timezone_required",
                self._rejected_material("geoblock_observed_at_timezone_required", raw),
            )
        observed = observed.astimezone(timezone.utc)
        now = self._now().astimezone(timezone.utc)
        if observed > now or now - observed > timedelta(seconds=self._max_age):
            raise GeoblockCheckError(
                "geoblock_evidence_stale",
                self._rejected_material("geoblock_evidence_stale", raw),
            )
        if raw["allowed"] is not True:
            raise GeoblockCheckError(
                "geoblock_denied",
                self._rejected_material("geoblock_denied", raw),
            )
        return GeoblockResult(
            allowed=True,
            observed_at=observed,
            source_version=source_version.strip(),
            region_code=region,
        )

    def _rejected_material(
        self, reason_code: str, raw: Any | None
    ) -> dict[str, Any]:
        """Return provenance without copying arbitrary provider bytes or secrets."""
        safe: dict[str, Any] = {
            "schema": "polymarket-geoblock/v1",
            "result": "REJECTED",
            "reason_code": reason_code,
            "recorded_at": self._now().astimezone(timezone.utc).isoformat(),
        }
        if isinstance(raw, dict):
            safe["response_fields"] = sorted(
                key for key in raw if isinstance(key, str) and len(key) <= 64
            )[:16]
            if type(raw.get("allowed")) is bool:
                safe["allowed"] = raw["allowed"]
            source = raw.get("source_version")
            if isinstance(source, str) and 0 < len(source.strip()) <= 64:
                safe["source_version"] = source.strip()
            region = raw.get("region_code")
            if isinstance(region, str) and len(region) == 2 and region.isascii():
                safe["region_code"] = region
            observed = raw.get("observed_at")
            if isinstance(observed, datetime):
                safe["observed_at"] = observed.isoformat()
            elif isinstance(observed, str) and len(observed) <= 64:
                safe["observed_at"] = observed
        else:
            safe["response_type"] = type(raw).__name__
        return safe
