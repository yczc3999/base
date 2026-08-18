#!/usr/bin/env python3
"""Plan Base upgrades and maintain downstream version/update ledgers."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from lib.base_release import (
    BaseReleaseError,
    SEMVER,
    load_manifests as _load_manifests,
    parse_project,
    parse_project_text,
    select_manifests,
    validate_manifest,
    version_tuple,
)


ROOT = Path(__file__).resolve().parents[1]
LedgerError = BaseReleaseError

GITHUB_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}$"
)


def validate_upstream_repository(value: str) -> str:
    """Validate the credential-free GitHub OWNER/REPO ledger identity."""

    if (
        not GITHUB_REPOSITORY.fullmatch(value)
        or value.lower().endswith(".git")
        or value.endswith("/.")
        or value.endswith("/..")
    ):
        raise LedgerError(
            "Base upstream repository must be canonical GitHub OWNER/REPO "
            "without credentials, nested paths, or a .git suffix"
        )
    return value


def normalize_github_remote(value: str) -> str:
    """Normalize a trusted github.com fetch URL to its public OWNER/REPO identity."""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\n" in value
        or "\r" in value
    ):
        raise LedgerError("upstream remote must be a one-line github.com URL")

    path: str
    scp_match = re.match(r"^git@github\.com:(.+)$", value, flags=re.IGNORECASE)
    if scp_match:
        path = scp_match.group(1)
    else:
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise LedgerError("upstream remote is not a valid github.com URL") from exc
        if parsed.scheme not in {"https", "ssh", "git"}:
            raise LedgerError("upstream remote must use https, ssh, or git on github.com")
        if hostname is None or hostname.casefold() != "github.com":
            raise LedgerError("upstream remote must be hosted on github.com")
        if parsed.password is not None:
            raise LedgerError("upstream remote must not contain credentials")
        if parsed.scheme in {"https", "git"} and parsed.username is not None:
            raise LedgerError("upstream remote must not contain credentials")
        if parsed.scheme == "ssh" and parsed.username not in {None, "git"}:
            raise LedgerError("GitHub SSH upstream remote user must be git")
        if port is not None:
            raise LedgerError("upstream remote must use the standard github.com endpoint")
        if parsed.query or parsed.fragment:
            raise LedgerError("upstream remote must not contain query or fragment data")
        path = parsed.path.removeprefix("/")

    if path.lower().endswith(".git"):
        path = path[:-4]
    return validate_upstream_repository(path)


def project_upstream_repository(values: dict[str, str]) -> str | None:
    value = values.get("BASE_UPSTREAM_REPOSITORY")
    return validate_upstream_repository(value) if value is not None else None


def repository_for_project(path: Path) -> Path | None:
    """Return the containing Git worktree, preserving legacy non-Git fixture use."""

    lexical_path = path.absolute()
    result = subprocess.run(
        ["git", "-C", str(lexical_path.parent), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return None
    repository = Path(result.stdout.strip()).resolve()
    try:
        path.resolve().relative_to(repository)
    except ValueError:
        raise LedgerError("PROJECT.md must remain inside its containing Git worktree")
    return repository


def discover_project_upstream_repository(path: Path) -> str | None:
    """Backfill a legacy ledger from its repository's trusted upstream remote."""

    repository = repository_for_project(path)
    if repository is None:
        return None
    result = subprocess.run(
        ["git", "-C", str(repository), "remote", "get-url", "upstream"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode or not result.stdout.strip():
        raise LedgerError(
            "PROJECT.md has no BASE_UPSTREAM_REPOSITORY and its Git repository "
            "has no trusted upstream remote"
        )
    return normalize_github_remote(result.stdout.rstrip("\n"))


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


def load_manifests(ref: str | None = None) -> dict[str, dict[str, Any]]:
    return _load_manifests(ROOT, ref)


def selected_manifests(
    manifests: dict[str, dict[str, Any]],
    from_version: str,
    to_version: str,
    initial: bool = False,
) -> list[dict[str, Any]]:
    return select_manifests(manifests, from_version, to_version, initial)


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
        parse_project_text(git("show", f"{args.ref}:{args.project}"), strict=True)
        if args.ref else parse_project(Path(args.project), strict=True)
    )
    version = values.get("BASE_UPSTREAM_VERSION")
    if not version:
        raise LedgerError("PROJECT.md has no BASE_UPSTREAM_VERSION")
    version_tuple(version)
    project_upstream_repository(values)
    print(version)


def command_record(args: argparse.Namespace) -> None:
    project = Path(args.project)
    history = Path(args.history)
    values = parse_project(project, strict=True)
    recorded_repository = project_upstream_repository(values)
    requested_repository = (
        validate_upstream_repository(args.upstream_repository)
        if args.upstream_repository is not None
        else None
    )
    if recorded_repository is None and requested_repository is None:
        requested_repository = discover_project_upstream_repository(project)
    if (
        recorded_repository
        and requested_repository
        and recorded_repository.casefold() != requested_repository.casefold()
    ):
        raise LedgerError(
            "PROJECT.md Base upstream repository does not match --upstream-repository"
        )
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
    if requested_repository is not None and recorded_repository is None:
        updates["BASE_UPSTREAM_REPOSITORY"] = requested_repository
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
    upstream_repository = (
        validate_upstream_repository(args.upstream_repository)
        if args.upstream_repository is not None
        else None
    )
    repository_line = (
        [f"BASE_UPSTREAM_REPOSITORY={upstream_repository}"]
        if upstream_repository is not None
        else []
    )
    project.write_text(
        "\n".join([
            f"# {args.project_name}",
            "",
            f"PROJECT_SLUG={args.project_slug}",
            f"DATABASE_NAME={args.db_name}",
            f"DATABASE_USER={args.db_user}",
            *repository_line,
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
        upstream_repository=upstream_repository,
    )
    command_record(record_args)


def command_normalize_repository(args: argparse.Namespace) -> None:
    print(normalize_github_remote(args.remote_url))


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
    record.add_argument("--upstream-repository")
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
    initialize.add_argument("--upstream-repository")
    initialize.set_defaults(func=command_initialize)

    normalize_repository = subparsers.add_parser("normalize-repository")
    normalize_repository.add_argument("--remote-url", required=True)
    normalize_repository.set_defaults(func=command_normalize_repository)
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
