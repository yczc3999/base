"""Shared Base release, downstream ledger, and output-sanitization helpers.

The command-line tools in ``scripts/`` deliberately share this module so that
there is one definition of a released Base version, one manifest selection
algorithm, and one boundary for removing credentials from operator-visible
output.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping


MAX_CORE_SEMVER_LENGTH = 64
MAX_CORE_SEMVER_COMPONENT_LENGTH = 64
CORE_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SEMVER = CORE_SEMVER  # Compatibility for callers that used the ledger constant.
REQUIRED_MANIFEST_LISTS = (
    "nodes",
    "compatibility",
    "migrations",
    "downstream_actions",
    "conflict_hotspots",
    "verify",
    "rollback",
)
NODE_KINDS = {"added", "changed", "fixed", "removed", "security"}


class BaseReleaseError(RuntimeError):
    """A user-facing failure in a Base release or downstream ledger contract."""


def parse_core_semver(value: str) -> tuple[int, int, int]:
    """Parse canonical core SemVer (``X.Y.Z``) without coercion."""

    if not isinstance(value, str):
        raise BaseReleaseError(f"invalid stable SemVer: {value!r}")
    if len(value) > MAX_CORE_SEMVER_LENGTH:
        raise BaseReleaseError(
            "invalid stable SemVer: value exceeds the 64-character limit"
        )
    match = CORE_SEMVER.fullmatch(value)
    if not match:
        raise BaseReleaseError(f"invalid stable SemVer: {value!r}")
    parts = match.groups()
    if any(len(part) > MAX_CORE_SEMVER_COMPONENT_LENGTH for part in parts):
        raise BaseReleaseError(
            "invalid stable SemVer: a component exceeds the 64-digit limit"
        )
    try:
        return tuple(int(part) for part in parts)
    except ValueError as exc:  # Defensive boundary if Python's int rules change.
        raise BaseReleaseError("invalid stable SemVer") from exc


# Keep the original ledger name available to both old and new callers.
version_tuple = parse_core_semver


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise BaseReleaseError(
            result.stderr.strip() or f"git {' '.join(args)} failed"
        )
    return result.stdout.strip()


def _manifest_paths(root: Path, ref: str | None) -> list[str]:
    if ref:
        output = _git(root, "ls-tree", "-r", "--name-only", ref, "releases")
        return sorted(
            line
            for line in output.splitlines()
            if re.fullmatch(r"releases/base-v\d+\.\d+\.\d+\.json", line)
        )
    releases = root / "releases"
    return [str(path.relative_to(root)) for path in sorted(releases.glob("base-v*.json"))]


def _read_manifest(root: Path, path: str, ref: str | None) -> dict[str, Any]:
    try:
        raw = _git(root, "show", f"{ref}:{path}") if ref else (root / path).read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        raise BaseReleaseError(f"cannot read {path}: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BaseReleaseError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BaseReleaseError(f"{path} must contain an object")
    return value


def validate_manifest(manifest: Mapping[str, Any], path: str) -> None:
    """Validate the stable release-manifest contract used by ledger tools."""

    version = manifest.get("version")
    if not isinstance(version, str):
        raise BaseReleaseError(f"{path}: version is required")
    parse_core_semver(version)
    expected_name = f"releases/base-v{version}.json"
    if path != expected_name:
        raise BaseReleaseError(f"{path}: expected filename {expected_name}")
    if manifest.get("schema_version") != 1:
        raise BaseReleaseError(f"{path}: schema_version must be 1")
    if manifest.get("tag") != f"base/v{version}":
        raise BaseReleaseError(f"{path}: tag does not match version")
    if manifest.get("semver") not in {"MAJOR", "MINOR", "PATCH"}:
        raise BaseReleaseError(f"{path}: semver must be MAJOR/MINOR/PATCH")
    for field in ("released_at", "summary"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise BaseReleaseError(f"{path}: {field} must be a non-empty string")
    for field in REQUIRED_MANIFEST_LISTS:
        if not isinstance(manifest.get(field), list):
            raise BaseReleaseError(f"{path}: {field} must be a list")
    if not manifest["nodes"]:
        raise BaseReleaseError(f"{path}: at least one update node is required")
    node_ids: set[str] = set()
    for node in manifest["nodes"]:
        if not isinstance(node, dict):
            raise BaseReleaseError(f"{path}: every node must be an object")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]+", node_id
        ):
            raise BaseReleaseError(f"{path}: invalid node id {node_id!r}")
        if node_id in node_ids:
            raise BaseReleaseError(f"{path}: duplicate node id {node_id}")
        node_ids.add(node_id)
        if node.get("kind") not in NODE_KINDS:
            raise BaseReleaseError(f"{path}: node {node_id} has invalid kind")
        for field in ("scope", "summary"):
            if not isinstance(node.get(field), str) or not node[field].strip():
                raise BaseReleaseError(f"{path}: node {node_id} requires {field}")
        files = node.get("files")
        if (
            not isinstance(files, list)
            or not files
            or not all(isinstance(item, str) for item in files)
        ):
            raise BaseReleaseError(f"{path}: node {node_id} requires files")
    for field in REQUIRED_MANIFEST_LISTS[1:]:
        if not all(
            isinstance(item, str) and item.strip() for item in manifest[field]
        ):
            raise BaseReleaseError(
                f"{path}: {field} entries must be non-empty strings"
            )


def load_manifests(
    root: Path | str | None = None,
    ref: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Load validated manifests from a worktree or from a Git ref.

    ``root`` defaults to the repository containing this module.  Keeping it an
    argument lets fixture repositories reuse the exact production algorithm.
    """

    repository = (
        Path(root).resolve()
        if root is not None
        else Path(__file__).resolve().parents[2]
    )
    manifests: dict[str, dict[str, Any]] = {}
    for path in _manifest_paths(repository, ref):
        manifest = _read_manifest(repository, path, ref)
        validate_manifest(manifest, path)
        version = manifest["version"]
        if version in manifests:
            raise BaseReleaseError(f"duplicate release manifest for {version}")
        manifests[version] = manifest
    if not manifests:
        raise BaseReleaseError("no Base release manifests found")
    return manifests


def select_manifests(
    manifests: Mapping[str, dict[str, Any]],
    from_version: str,
    to_version: str,
    initial: bool = False,
) -> list[dict[str, Any]]:
    """Select the ordered ``(from, to]`` release range.

    Only the target is required to have a manifest.  A downstream may record a
    valid source version predating (or falling between) the manifests retained
    by this checkout; the manifests after that boundary still form its plan.
    """

    start = parse_core_semver(from_version)
    end = parse_core_semver(to_version)
    if initial:
        if to_version not in manifests:
            raise BaseReleaseError(f"missing target manifest {to_version}")
        return [manifests[to_version]]
    if start >= end:
        raise BaseReleaseError(
            f"target version {to_version} must be newer than {from_version}"
        )
    if to_version not in manifests:
        raise BaseReleaseError(f"missing target manifest {to_version}")
    selected = [
        manifest
        for version, manifest in manifests.items()
        if start < parse_core_semver(version) <= end
    ]
    selected.sort(key=lambda item: parse_core_semver(item["version"]))
    if not selected or selected[-1]["version"] != to_version:
        raise BaseReleaseError(f"missing target manifest {to_version}")
    return selected


selected_manifests = select_manifests


def parse_project_text(text: str, *, strict: bool = False) -> dict[str, str]:
    """Parse the committed ``KEY=value`` ledger format.

    Legacy ledger commands retain last-value-wins behavior.  Provider-facing
    callers use ``strict=True`` so duplicate keys cannot hide version drift.
    """

    values: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if strict and key in values:
            raise BaseReleaseError(
                f"PROJECT.md has duplicate key {key!r} at line {line_number}"
            )
        values[key] = value.strip()
    return values


def parse_project(path: Path | str, *, strict: bool = False) -> dict[str, str]:
    project = Path(path)
    if not project.exists():
        raise BaseReleaseError(f"missing downstream project ledger: {project}")
    try:
        text = project.read_text(encoding="utf-8")
    except OSError as exc:
        raise BaseReleaseError(f"cannot read downstream project ledger: {project}") from exc
    return parse_project_text(text, strict=strict)


_AUTHORIZATION = re.compile(
    r"(?i)(\bauthorization\s*[:=]\s*)[^\r\n,;]+"
)
_BEARER = re.compile(r"(?i)\bbearer[ \t]+[A-Za-z0-9._~+/=-]+")
_URL_USERINFO = re.compile(
    r"(?i)(\b[A-Za-z][A-Za-z0-9+.-]*://)[^\s/?#@]+@"
)
_SENSITIVE_NAME = (
    r"(?:[A-Za-z0-9]+[_.-])*"
    r"(?:access[_.-]?token|auth[_.-]?token|github[_.-]?token|token|"
    r"client[_.-]?secret|secret|password|passwd|pwd|api[_.-]?key|"
    r"private[_.-]?key|encryption[_.-]?key|key)"
)
_QUOTED_ASSIGNMENT = re.compile(
    rf"(?i)(?P<prefix>[\"']?{_SENSITIVE_NAME}[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"'])(?P<value>[^\r\n]*?)(?P=quote)"
)
_BARE_ASSIGNMENT = re.compile(
    rf"(?i)(?P<prefix>\b{_SENSITIVE_NAME}\b\s*[:=]\s*)"
    r"(?P<value>[^\s,;]+)"
)
REDACTION = "[REDACTED]"


def _redact_text(value: str, secrets: Iterable[str]) -> str:
    output = value
    known = sorted(
        {secret for secret in secrets if isinstance(secret, str) and secret},
        key=len,
        reverse=True,
    )
    for secret in known:
        output = output.replace(secret, REDACTION)
    output = _AUTHORIZATION.sub(lambda match: match.group(1) + REDACTION, output)
    output = _BEARER.sub(f"Bearer {REDACTION}", output)
    output = _URL_USERINFO.sub(lambda match: match.group(1), output)
    output = _QUOTED_ASSIGNMENT.sub(
        lambda match: (
            match.group("prefix")
            + match.group("quote")
            + REDACTION
            + match.group("quote")
        ),
        output,
    )
    output = _BARE_ASSIGNMENT.sub(
        lambda match: match.group("prefix") + REDACTION,
        output,
    )
    return output


def redact(value: Any, *, secrets: Iterable[str] = ()) -> Any:
    """Recursively redact credentials while preserving the JSON-like shape."""

    if isinstance(value, str):
        return _redact_text(value, secrets)
    if isinstance(value, list):
        return [redact(item, secrets=secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item, secrets=secrets) for item in value)
    if isinstance(value, dict):
        return {
            (redact(key, secrets=secrets) if isinstance(key, str) else key): redact(
                item, secrets=secrets
            )
            for key, item in value.items()
        }
    return value
