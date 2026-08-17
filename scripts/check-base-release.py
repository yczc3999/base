#!/usr/bin/env python3
"""Validate the Base release ledger and shared version metadata."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def fail(message: str) -> "NoReturn":
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {path.relative_to(ROOT)}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def main() -> int:
    version_path = ROOT / "VERSION"
    try:
        version = version_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        fail(f"cannot read VERSION: {exc}")
    if not SEMVER.fullmatch(version):
        fail(f"VERSION is not SemVer: {version!r}")

    package = read_json(ROOT / "admin/package.json")
    lock = read_json(ROOT / "admin/package-lock.json")
    if package.get("version") != version:
        fail(f"admin/package.json version {package.get('version')!r} != {version!r}")
    if lock.get("version") != version:
        fail(f"admin/package-lock.json version {lock.get('version')!r} != {version!r}")
    lock_root = lock.get("packages", {}).get("")
    if not isinstance(lock_root, dict) or lock_root.get("version") != version:
        fail("admin/package-lock.json packages[''].version is out of sync")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{version}] - " not in changelog:
        fail(f"CHANGELOG.md has no frozen release entry for {version}")
    if "## [Unreleased]" not in changelog:
        fail("CHANGELOG.md must keep an Unreleased section")

    upstream = (ROOT / "UPSTREAM.md").read_text(encoding="utf-8")
    tag = f"base/v{version}"
    if tag not in upstream:
        fail(f"UPSTREAM.md does not document the current release tag {tag}")

    print(f"Base release metadata OK: v{version}")
    print(f"Expected immutable tag: {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
