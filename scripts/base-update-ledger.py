#!/usr/bin/env python3
"""Plan Base upgrades and maintain downstream version/update ledgers."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RELEASES = ROOT / "releases"
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
REQUIRED_LISTS = (
    "nodes",
    "compatibility",
    "migrations",
    "downstream_actions",
    "conflict_hotspots",
    "verify",
    "rollback",
)
NODE_KINDS = {"added", "changed", "fixed", "removed", "security"}


class LedgerError(RuntimeError):
    pass


def version_tuple(value: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(value)
    if not match:
        raise LedgerError(f"invalid stable SemVer: {value!r}")
    return tuple(int(part) for part in match.groups())


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        raise LedgerError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _manifest_paths(ref: str | None) -> list[str]:
    if ref:
        output = git("ls-tree", "-r", "--name-only", ref, "releases")
        return sorted(
            line for line in output.splitlines()
            if re.fullmatch(r"releases/base-v\d+\.\d+\.\d+\.json", line)
        )
    return [str(path.relative_to(ROOT)) for path in sorted(RELEASES.glob("base-v*.json"))]


def _read_manifest(path: str, ref: str | None) -> dict[str, Any]:
    raw = git("show", f"{ref}:{path}") if ref else (ROOT / path).read_text(encoding="utf-8")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LedgerError(f"{path} must contain an object")
    return value


def validate_manifest(manifest: dict[str, Any], path: str) -> None:
    version = manifest.get("version")
    if not isinstance(version, str):
        raise LedgerError(f"{path}: version is required")
    version_tuple(version)
    expected_name = f"releases/base-v{version}.json"
    if path != expected_name:
        raise LedgerError(f"{path}: expected filename {expected_name}")
    if manifest.get("schema_version") != 1:
        raise LedgerError(f"{path}: schema_version must be 1")
    if manifest.get("tag") != f"base/v{version}":
        raise LedgerError(f"{path}: tag does not match version")
    if manifest.get("semver") not in {"MAJOR", "MINOR", "PATCH"}:
        raise LedgerError(f"{path}: semver must be MAJOR/MINOR/PATCH")
    for field in ("released_at", "summary"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise LedgerError(f"{path}: {field} must be a non-empty string")
    for field in REQUIRED_LISTS:
        if not isinstance(manifest.get(field), list):
            raise LedgerError(f"{path}: {field} must be a list")
    if not manifest["nodes"]:
        raise LedgerError(f"{path}: at least one update node is required")
    node_ids: set[str] = set()
    for node in manifest["nodes"]:
        if not isinstance(node, dict):
            raise LedgerError(f"{path}: every node must be an object")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]+", node_id):
            raise LedgerError(f"{path}: invalid node id {node_id!r}")
        if node_id in node_ids:
            raise LedgerError(f"{path}: duplicate node id {node_id}")
        node_ids.add(node_id)
        if node.get("kind") not in NODE_KINDS:
            raise LedgerError(f"{path}: node {node_id} has invalid kind")
        for field in ("scope", "summary"):
            if not isinstance(node.get(field), str) or not node[field].strip():
                raise LedgerError(f"{path}: node {node_id} requires {field}")
        files = node.get("files")
        if not isinstance(files, list) or not files or not all(isinstance(item, str) for item in files):
            raise LedgerError(f"{path}: node {node_id} requires files")
    for field in REQUIRED_LISTS[1:]:
        if not all(isinstance(item, str) and item.strip() for item in manifest[field]):
            raise LedgerError(f"{path}: {field} entries must be non-empty strings")


def load_manifests(ref: str | None = None) -> dict[str, dict[str, Any]]:
    manifests: dict[str, dict[str, Any]] = {}
    for path in _manifest_paths(ref):
        manifest = _read_manifest(path, ref)
        validate_manifest(manifest, path)
        version = manifest["version"]
        if version in manifests:
            raise LedgerError(f"duplicate release manifest for {version}")
        manifests[version] = manifest
    if not manifests:
        raise LedgerError("no Base release manifests found")
    return manifests


def selected_manifests(
    manifests: dict[str, dict[str, Any]],
    from_version: str,
    to_version: str,
    initial: bool = False,
) -> list[dict[str, Any]]:
    start = version_tuple(from_version)
    end = version_tuple(to_version)
    if initial:
        if to_version not in manifests:
            raise LedgerError(f"missing target manifest {to_version}")
        return [manifests[to_version]]
    if start >= end:
        raise LedgerError(f"target version {to_version} must be newer than {from_version}")
    selected = [
        manifest for version, manifest in manifests.items()
        if start < version_tuple(version) <= end
    ]
    selected.sort(key=lambda item: version_tuple(item["version"]))
    if not selected or selected[-1]["version"] != to_version:
        raise LedgerError(f"missing target manifest {to_version}")
    return selected


def changed_files(
    from_version: str,
    ref: str | None,
    manifests: list[dict[str, Any]],
) -> tuple[list[str], bool]:
    if ref and from_version != "0.0.0":
        from_ref = f"refs/tags/base/v{from_version}"
        if git("rev-parse", "--verify", from_ref, check=False):
            output = git("diff", "--name-status", f"{from_ref}..{ref}")
            if output:
                return output.splitlines(), True
    values = {
        f"M\t{file}"
        for manifest in manifests
        for node in manifest["nodes"]
        for file in node["files"]
    }
    return sorted(values), False


def render_plan(
    from_version: str,
    to_version: str,
    manifests: list[dict[str, Any]],
    files: list[str],
    files_are_exact: bool,
    initial: bool = False,
) -> str:
    title = (
        f"Initial Base adoption: v{to_version}"
        if initial else f"Base update: v{from_version} → v{to_version}"
    )
    lines = [f"## {title}", ""]
    for manifest in manifests:
        lines.extend([
            f"### v{manifest['version']} — {manifest['summary']}",
            "",
            "#### Update nodes",
        ])
        for node in manifest["nodes"]:
            lines.append(
                f"- `{node['id']}` [{node['kind']}/{node['scope']}]: {node['summary']}"
            )
            lines.append(f"  Files: {', '.join(f'`{item}`' for item in node['files'])}")
        sections = (
            ("Compatibility", "compatibility"),
            ("Migrations", "migrations"),
            ("Downstream actions", "downstream_actions"),
            ("Conflict hotspots", "conflict_hotspots"),
            ("Verification", "verify"),
            ("Rollback", "rollback"),
        )
        for heading, field in sections:
            lines.extend(["", f"#### {heading}"])
            values = manifest[field] or ["None."]
            lines.extend(f"- {value}" for value in values)
        lines.append("")
    lines.append(
        "### Exact Base tag file changes"
        if files_are_exact else "### Declared Base file scope"
    )
    lines.extend(f"- `{item}`" for item in files or ["None"])
    return "\n".join(lines).rstrip() + "\n"


def parse_project_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def parse_project(path: Path) -> dict[str, str]:
    if not path.exists():
        raise LedgerError(f"missing downstream project ledger: {path}")
    return parse_project_text(path.read_text(encoding="utf-8"))


def update_project(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    if output and output[-1]:
        output.append("")
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def append_history(
    path: Path,
    body: str,
    synced_at: str,
    target_commit: str,
    verification_status: str,
) -> None:
    if path.exists():
        text = path.read_text(encoding="utf-8").rstrip()
    else:
        text = "# Base Upstream Update Ledger\n\nAppend-only downstream record; passwords and product secrets are forbidden."
    entry = (
        f"{body.rstrip()}\n\n"
        f"- Synced at: `{synced_at}`\n"
        f"- Base commit: `{target_commit}`\n"
        f"- Verification result: {verification_status}\n"
    )
    path.write_text(f"{text}\n\n---\n\n{entry}", encoding="utf-8")


def command_validate(args: argparse.Namespace) -> None:
    manifests = load_manifests(args.ref)
    if args.current not in manifests:
        raise LedgerError(f"missing current release manifest {args.current}")
    latest = max(manifests, key=version_tuple)
    if latest != args.current:
        raise LedgerError(f"current version {args.current} is not latest manifest {latest}")
    changelog = (
        git("show", f"{args.ref}:CHANGELOG.md")
        if args.ref else (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    )
    for version, manifest in manifests.items():
        heading = f"## [{version}] - {manifest['released_at']}"
        if heading not in changelog:
            raise LedgerError(f"CHANGELOG.md is missing manifest heading: {heading}")
    if not args.ref:
        tagged_versions = {
            tag.removeprefix("base/v")
            for tag in git("tag", "--list", "base/v*").splitlines()
            if SEMVER.fullmatch(tag.removeprefix("base/v"))
        }
        missing_manifests = tagged_versions - manifests.keys()
        if missing_manifests:
            raise LedgerError(
                f"Base tags without release manifests: {sorted(missing_manifests)}"
            )
        missing_tags = set(manifests) - tagged_versions - {args.current}
        if missing_tags:
            raise LedgerError(
                f"historical release manifests without immutable tags: {sorted(missing_tags)}"
            )
    print(f"Base release manifests OK: {len(manifests)} releases; current=v{args.current}")


def command_plan(args: argparse.Namespace) -> None:
    manifests = load_manifests(args.ref)
    selected = selected_manifests(manifests, args.from_version, args.to_version, args.initial)
    files, files_are_exact = changed_files(args.from_version, args.ref, selected)
    print(
        render_plan(
            args.from_version,
            args.to_version,
            selected,
            files,
            files_are_exact,
            args.initial,
        ),
        end="",
    )


def command_current(args: argparse.Namespace) -> None:
    values = (
        parse_project_text(git("show", f"{args.ref}:{args.project}"))
        if args.ref else parse_project(Path(args.project))
    )
    version = values.get("BASE_UPSTREAM_VERSION")
    if not version:
        raise LedgerError("PROJECT.md has no BASE_UPSTREAM_VERSION")
    version_tuple(version)
    print(version)


def command_record(args: argparse.Namespace) -> None:
    project = Path(args.project)
    history = Path(args.history)
    values = parse_project(project)
    recorded = values.get("BASE_UPSTREAM_VERSION")
    if not args.initial and recorded != args.from_version:
        raise LedgerError(
            f"PROJECT.md records {recorded!r}, expected {args.from_version!r}"
        )
    if not args.verification_status.strip() or "\n" in args.verification_status:
        raise LedgerError("verification status must be one non-empty line")
    manifests = load_manifests(args.ref)
    selected = selected_manifests(manifests, args.from_version, args.to_version, args.initial)
    files, files_are_exact = changed_files(args.from_version, args.ref, selected)
    body = render_plan(
        args.from_version,
        args.to_version,
        selected,
        files,
        files_are_exact,
        args.initial,
    )
    synced_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    target_commit = args.commit or (
        git("rev-parse", f"{args.ref}^{{commit}}") if args.ref else "WORKTREE"
    )
    if target_commit != "WORKTREE" and not re.fullmatch(r"[0-9a-f]{40}", target_commit):
        raise LedgerError(f"invalid Base commit: {target_commit!r}")
    try:
        history_reference = str(history.resolve().relative_to(project.resolve().parent))
    except ValueError:
        history_reference = str(history)
    updates = {
        "BASE_UPSTREAM_VERSION": args.to_version,
        "BASE_UPSTREAM_TAG": f"base/v{args.to_version}",
        "BASE_UPSTREAM_COMMIT": target_commit,
        "BASE_LAST_SYNCED_AT": synced_at,
        "BASE_UPDATE_LEDGER": history_reference,
        "BASE_NEXT_UPDATE_PLAN": (
            f"python3 scripts/base-update-ledger.py plan --from {args.to_version} "
            "--to <TARGET_VERSION> --ref refs/tags/base/v<TARGET_VERSION>"
        ),
        "BASE_NEXT_UPDATE_COMMAND": "./scripts/sync-base-release.sh <TARGET_VERSION>",
    }
    update_project(project, updates)
    append_history(
        history,
        body,
        synced_at,
        target_commit,
        args.verification_status,
    )
    print(f"Downstream ledger updated: v{args.from_version} -> v{args.to_version}")


def command_initialize(args: argparse.Namespace) -> None:
    project = Path(args.project)
    if project.exists():
        raise LedgerError(f"refusing to overwrite existing project ledger: {project}")
    synced_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    project.write_text(
        "\n".join([
            f"# {args.project_name}",
            "",
            f"PROJECT_SLUG={args.project_slug}",
            f"DATABASE_NAME={args.db_name}",
            f"DATABASE_USER={args.db_user}",
            f"BASE_UPSTREAM_VERSION={args.version}",
            f"BASE_UPSTREAM_TAG=base/v{args.version}",
            f"BOOTSTRAPPED_AT={synced_at}",
            "",
        ]),
        encoding="utf-8",
    )
    record_args = argparse.Namespace(
        project=str(project),
        history=args.history,
        from_version="0.0.0",
        to_version=args.version,
        ref=args.ref,
        commit=args.commit,
        initial=True,
        verification_status=args.verification_status,
    )
    command_record(record_args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--current", required=True)
    validate.add_argument("--ref")
    validate.set_defaults(func=command_validate)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--from", dest="from_version", required=True)
    plan.add_argument("--to", dest="to_version", required=True)
    plan.add_argument("--ref")
    plan.add_argument("--initial", action="store_true")
    plan.set_defaults(func=command_plan)

    current = subparsers.add_parser("current")
    current.add_argument("--project", default="PROJECT.md")
    current.add_argument("--ref")
    current.set_defaults(func=command_current)

    record = subparsers.add_parser("record")
    record.add_argument("--project", default="PROJECT.md")
    record.add_argument("--history", default="BASE_UPDATES.md")
    record.add_argument("--from", dest="from_version", required=True)
    record.add_argument("--to", dest="to_version", required=True)
    record.add_argument("--ref")
    record.add_argument("--commit")
    record.add_argument("--initial", action="store_true")
    record.add_argument("--verification-status", default="NOT_RECORDED")
    record.set_defaults(func=command_record)

    initialize = subparsers.add_parser("initialize")
    initialize.add_argument("--project", default="PROJECT.md")
    initialize.add_argument("--history", default="BASE_UPDATES.md")
    initialize.add_argument("--project-slug", required=True)
    initialize.add_argument("--project-name", required=True)
    initialize.add_argument("--db-name", required=True)
    initialize.add_argument("--db-user", required=True)
    initialize.add_argument("--version", required=True)
    initialize.add_argument("--ref")
    initialize.add_argument("--commit")
    initialize.add_argument("--verification-status", default="NOT_RECORDED")
    initialize.set_defaults(func=command_initialize)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.func(args)
    except (LedgerError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
