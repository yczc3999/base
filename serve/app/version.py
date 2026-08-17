"""Single source of truth for the Base Platform release version."""

from __future__ import annotations

import re
from pathlib import Path


_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_VERSION_PATH = Path(__file__).resolve().parents[2] / "VERSION"


def _read_version() -> str:
    version = _VERSION_PATH.read_text(encoding="utf-8").strip()
    if not _SEMVER.fullmatch(version):
        raise RuntimeError(f"Invalid Base Platform VERSION: {version!r}")
    return version


BASE_VERSION = _read_version()
