#!/usr/bin/env python3
"""Read-only planning and deterministic reporting for Base upgrade campaigns."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sys
import tempfile
import time
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from jsonschema import Draft7Validator

try:
    from referencing import Registry, Resource
except ImportError:  # jsonschema<4.18 compatibility for bootstrap/operator hosts.
    Registry = Resource = None
    from jsonschema import RefResolver

from lib.base_release import (
    BaseReleaseError,
    load_manifests,
    parse_core_semver,
    parse_project_text,
    redact,
    select_manifests,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "scripts" / "schemas"
REGISTRY_SCHEMA = SCHEMAS / "base-downstream-registry.schema.json"
RESULT_SCHEMA = SCHEMAS / "base-upgrade-result.schema.json"
BATCH_SCHEMA = SCHEMAS / "base-upgrade-batch.schema.json"
PROJECT_VERSION_KEY = "BASE_UPSTREAM_VERSION"
DEFAULT_TIMEOUT = 15
MAX_PROVIDER_ATTEMPTS = 3
MAX_PROVIDER_RETRY_DELAY = 60.0
MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PROJECT_MD_BYTES = 1024 * 1024
UPGRADE_BRANCH = re.compile(r"^chore/base-v(.+)$")
CAMPAIGN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CHANNEL = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class CampaignError(RuntimeError):
    """A safe, user-facing campaign validation or provider error."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CampaignError(f"cannot read {path}") from exc
    except json.JSONDecodeError as exc:
        raise CampaignError(
            f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}"
        ) from exc


def _validator(path: Path) -> Draft7Validator:
    schema = _load_json(path)
    Draft7Validator.check_schema(schema)
    result_schema = _load_json(RESULT_SCHEMA)
    if Registry is not None and Resource is not None:
        schema_registry = Registry().with_resource(
            result_schema["$id"], Resource.from_contents(result_schema)
        )
        return Draft7Validator(schema, registry=schema_registry)
    return Draft7Validator(  # type: ignore[name-defined]
        schema,
        resolver=RefResolver.from_schema(  # type: ignore[name-defined]
            schema, store={result_schema["$id"]: result_schema}
        ),
    )


def _validate(document: Any, path: Path, label: str) -> None:
    errors = sorted(
        _validator(path).iter_errors(document),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if not errors:
        return
    error = errors[0]
    location = ".".join(str(item) for item in error.absolute_path) or "<root>"
    raise CampaignError(
        f"{label} validation failed at {location} ({error.validator})"
    )


def load_registry(path: Path) -> dict[str, Any]:
    registry = _load_json(path)
    _validate(registry, REGISTRY_SCHEMA, "registry")
    project_ids: set[str] = set()
    repositories: set[str] = set()
    for project in registry["projects"]:
        project_id = project["project_id"]
        repository = project["repository"].casefold()
        if project_id in project_ids:
            raise CampaignError(f"duplicate registry project_id: {project_id}")
        if repository in repositories:
            raise CampaignError(
                f"duplicate registry repository after case normalization: "
                f"{project['repository']}"
            )
        project_ids.add(project_id)
        repositories.add(repository)
    return registry


def _retry_delay(
    headers: Mapping[str, str] | None,
    *,
    now: Callable[[], float] = time.time,
) -> float | None:
    def bounded(value: float) -> float | None:
        if not math.isfinite(value):
            return None
        return min(MAX_PROVIDER_RETRY_DELAY, max(0.0, value))

    normalized = {
        str(key).casefold(): str(value) for key, value in (headers or {}).items()
    }
    retry_after = normalized.get("retry-after")
    if retry_after:
        value = retry_after.strip()
        if value.isdigit():
            # Compare digit strings before integer conversion so an arbitrarily
            # large provider value cannot hit Python's integer digit limit.
            digits = value.lstrip("0") or "0"
            cap = str(int(MAX_PROVIDER_RETRY_DELAY))
            if len(digits) > len(cap) or (
                len(digits) == len(cap) and digits > cap
            ):
                return MAX_PROVIDER_RETRY_DELAY
            return float(int(digits))
        try:
            retry_at = parsedate_to_datetime(value).timestamp()
        except (TypeError, ValueError, OverflowError, OSError):
            pass
        else:
            return bounded(retry_at - now())
    reset = normalized.get("x-ratelimit-reset")
    if reset:
        try:
            return bounded(float(reset) - now())
        except ValueError:
            return None
    return None


def _open_response(
    request: Request,
    *,
    http_open: Callable[..., Any],
    timeout: int,
) -> bytes:
    response = http_open(request, timeout=timeout)
    try:
        raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
        if not isinstance(raw, (bytes, bytearray)):
            raise CampaignError("GitHub Contents API returned a non-bytes response")
        if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
            raise CampaignError("GitHub Contents API response exceeds the size limit")
        return bytes(raw)
    finally:
        close = getattr(response, "close", None)
        if close:
            close()


def read_github_project_md(
    project: Mapping[str, Any],
    *,
    token: str | None,
    http_open: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    timeout: int = DEFAULT_TIMEOUT,
    max_attempts: int = MAX_PROVIDER_ATTEMPTS,
) -> str:
    """Read PROJECT.md from GitHub Contents API with injectable transport/waits."""

    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise CampaignError("provider max_attempts must be a positive integer")
    if max_attempts < 1:
        raise CampaignError("provider max_attempts must be a positive integer")
    # This argument exists for deterministic transport tests, not as a way to
    # weaken the production retry bound.
    attempt_limit = min(max_attempts, MAX_PROVIDER_ATTEMPTS)

    owner, repository = project["repository"].split("/", 1)
    branch = quote(project["default_branch"], safe="")
    url = (
        "https://api.github.com/repos/"
        f"{quote(owner, safe='')}/{quote(repository, safe='')}/contents/PROJECT.md"
        f"?ref={branch}"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "base-upgrade-campaign/1",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(attempt_limit):
        request = Request(url, headers=headers, method="GET")
        try:
            raw = _open_response(request, http_open=http_open, timeout=timeout)
        except CampaignError:
            raise
        except HTTPError as exc:
            try:
                status = exc.code
                provider_headers = (
                    dict(exc.headers.items()) if exc.headers is not None else {}
                )
            finally:
                try:
                    exc.close()
                except Exception:
                    # Cleanup failures must not mask the bounded provider error.
                    pass
            provider_delay = _retry_delay(provider_headers, now=now)
            retryable = status >= 500 or (
                status in {403, 429} and provider_delay is not None
            )
            if not retryable or attempt + 1 >= attempt_limit:
                raise CampaignError(
                    f"GitHub Contents API returned HTTP {status} for "
                    f"{project['repository']}"
                ) from exc
            sleep(provider_delay if provider_delay is not None else float(2**attempt))
            continue
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            if attempt + 1 >= attempt_limit:
                raise CampaignError(
                    f"GitHub Contents API network failure for {project['repository']}"
                ) from exc
            sleep(float(2**attempt))
            continue
        except Exception as exc:
            raise CampaignError(
                f"GitHub Contents API unexpected failure for {project['repository']}"
            ) from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("unexpected Contents API document type")
            if payload.get("type") != "file" or payload.get("encoding") != "base64":
                raise ValueError("unexpected Contents API shape")
            content = payload.get("content")
            if not isinstance(content, str):
                raise ValueError("Contents API content must be a string")
            encoded = content.replace("\n", "")
            decoded = base64.b64decode(encoded, validate=True)
            if len(decoded) > MAX_PROJECT_MD_BYTES:
                raise ValueError("PROJECT.md exceeds the decoded size limit")
            return decoded.decode("utf-8")
        except Exception as exc:
            raise CampaignError(
                f"invalid GitHub Contents API response for {project['repository']}"
            ) from exc
    raise AssertionError("provider retry loop exhausted unexpectedly")


def _load_project_fixture(path: Path) -> dict[str, str]:
    value = _load_json(path)
    if isinstance(value, dict) and set(value) == {"repositories"}:
        value = value["repositories"]
    if not isinstance(value, dict) or not all(
        isinstance(repository, str) and isinstance(text, str)
        for repository, text in value.items()
    ):
        raise CampaignError(
            "project state fixture must map OWNER/REPO to PROJECT.md text"
        )
    normalized: dict[str, str] = {}
    for repository, text in value.items():
        key = repository.casefold()
        if key in normalized:
            raise CampaignError(
                "project state fixture contains a duplicate repository after "
                "case normalization"
            )
        normalized[key] = text
    return normalized


def _project_version(project_text: str) -> str:
    if not isinstance(project_text, str):
        raise CampaignError("PROJECT.md content must be UTF-8 text")
    occurrences = [
        line
        for line in project_text.splitlines()
        if not line.lstrip().startswith("#")
        and line.partition("=")[0].strip() == PROJECT_VERSION_KEY
        and "=" in line
    ]
    if len(occurrences) != 1:
        raise CampaignError(
            "PROJECT.md must contain exactly one BASE_UPSTREAM_VERSION"
        )
    values = parse_project_text(project_text, strict=True)
    version = values.get(PROJECT_VERSION_KEY)
    if not version:
        raise CampaignError("PROJECT.md has no BASE_UPSTREAM_VERSION")
    if len(version) > 64:
        raise CampaignError("PROJECT.md contains an overlong Base version")
    parse_core_semver(version)
    return version


def _validate_plan_inputs(
    *, target_version: str, campaign_id: str, channel: str
) -> None:
    if not isinstance(target_version, str) or len(target_version) > 64:
        raise CampaignError("target version must be canonical core SemVer")
    try:
        parse_core_semver(target_version)
    except (BaseReleaseError, ValueError) as exc:
        raise CampaignError("target version must be canonical core SemVer") from exc
    if (
        not isinstance(campaign_id, str)
        or not 1 <= len(campaign_id) <= 128
        or CAMPAIGN_ID.fullmatch(campaign_id) is None
    ):
        raise CampaignError("campaign ID is invalid")
    if (
        not isinstance(channel, str)
        or not 1 <= len(channel) <= 64
        or CHANNEL.fullmatch(channel) is None
    ):
        raise CampaignError("channel is invalid")


def _base_result(
    *,
    campaign_id: str,
    project_id: str,
    source_version: str | None,
    target_version: str,
    status: str,
    failed_stage: str | None = None,
    verification_summary: str | None = None,
) -> dict[str, Any]:
    if status == "blocked":
        retry_command = (
            "rerun read-only campaign planning after resolving the reported failure"
        )
    else:
        retry_command = "no retry required; read-only campaign planning completed"
    return {
        "campaign_id": campaign_id,
        "project_id": project_id,
        "source_version": source_version,
        "target_version": target_version,
        "status": status,
        "branch": None,
        "pr_url": None,
        "failed_stage": failed_stage,
        "conflict_files": [],
        "verification_summary": verification_summary,
        "retry_command": retry_command,
        "rollback_command": (
            "no rollback required; read-only campaign planning created no changes"
        ),
    }


def plan_campaign(
    registry: Mapping[str, Any],
    *,
    target_version: str,
    campaign_id: str,
    channel: str = "stable",
    project_states: Mapping[str, str] | None = None,
    token: str | None = None,
    project_reader: Callable[..., str] = read_github_project_md,
    allow_major: bool = False,
) -> dict[str, Any]:
    _validate_plan_inputs(
        target_version=target_version, campaign_id=campaign_id, channel=channel
    )
    selected_projects = sorted(
        (
            project
            for project in registry["projects"]
            if project["enabled"] and project["channel"] == channel
        ),
        key=lambda project: project["project_id"],
    )
    if not selected_projects:
        raise CampaignError(f"no enabled projects match channel {channel}")

    manifests = load_manifests()
    if target_version not in manifests:
        raise CampaignError(f"missing target release manifest {target_version}")

    results: list[dict[str, Any]] = []
    for project in selected_projects:
        source_version: str | None = None
        try:
            if project_states is None:
                project_text = project_reader(project, token=token)
            else:
                repository = project["repository"].casefold()
                if repository not in project_states:
                    raise CampaignError("PROJECT.md fixture is missing")
                project_text = project_states[repository]
            source_version = _project_version(project_text)
        except Exception:
            result = _base_result(
                campaign_id=campaign_id,
                project_id=project["project_id"],
                source_version=None,
                target_version=target_version,
                status="blocked",
                failed_stage="project_discovery",
                verification_summary="PROJECT.md version discovery failed",
            )
        else:
            source_tuple = parse_core_semver(source_version)
            target_tuple = parse_core_semver(target_version)
            if source_tuple == target_tuple:
                result = _base_result(
                    campaign_id=campaign_id,
                    project_id=project["project_id"],
                    source_version=source_version,
                    target_version=target_version,
                    status="up_to_date",
                    verification_summary="PROJECT.md already records the target version",
                )
            elif source_tuple > target_tuple:
                result = _base_result(
                    campaign_id=campaign_id,
                    project_id=project["project_id"],
                    source_version=source_version,
                    target_version=target_version,
                    status="blocked",
                    failed_stage="version_gate",
                    verification_summary="target is older than the recorded Base version",
                )
            elif source_tuple[0] != target_tuple[0] and not allow_major:
                result = _base_result(
                    campaign_id=campaign_id,
                    project_id=project["project_id"],
                    source_version=source_version,
                    target_version=target_version,
                    status="blocked",
                    failed_stage="version_gate",
                    verification_summary=(
                        "cross-MAJOR upgrade requires explicit --allow-major"
                    ),
                )
            else:
                try:
                    selected = select_manifests(
                        manifests, source_version, target_version
                    )
                except BaseReleaseError:
                    result = _base_result(
                        campaign_id=campaign_id,
                        project_id=project["project_id"],
                        source_version=source_version,
                        target_version=target_version,
                        status="blocked",
                        failed_stage="version_gate",
                        verification_summary="no complete Base release path was found",
                    )
                else:
                    node_count = sum(len(manifest["nodes"]) for manifest in selected)
                    result = _base_result(
                        campaign_id=campaign_id,
                        project_id=project["project_id"],
                        source_version=source_version,
                        target_version=target_version,
                        status="planned",
                        verification_summary=(
                            f"{len(selected)} release manifest(s), "
                            f"{node_count} update node(s)"
                        ),
                    )
        _validate(result, RESULT_SCHEMA, f"result {project['project_id']}")
        results.append(result)

    batch = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "target_version": target_version,
        "results": results,
    }
    validate_batch(batch)
    return batch


def _repository_from_pr_url(pr_url: str) -> str:
    parts = urlsplit(pr_url)
    path = parts.path.strip("/").split("/")
    if len(path) != 4 or path[2] != "pull":
        raise CampaignError("PR URL is not a canonical GitHub pull request URL")
    return f"{path[0]}/{path[1]}"


def validate_batch(
    batch: Mapping[str, Any],
    *,
    registry: Mapping[str, Any] | None = None,
) -> None:
    raw_results = batch.get("results") if isinstance(batch, Mapping) else None
    if isinstance(raw_results, list):
        raw_ids = [
            item.get("project_id")
            for item in raw_results
            if isinstance(item, Mapping)
        ]
        if len(raw_ids) == len(raw_results) and len(raw_ids) != len(set(raw_ids)):
            raise CampaignError("batch results contain duplicate project_id values")
    _validate(batch, BATCH_SCHEMA, "batch")
    results = batch["results"]
    ordered = [result["project_id"] for result in results]
    if ordered != sorted(ordered):
        raise CampaignError("batch results must be sorted by project_id")
    if len(ordered) != len(set(ordered)):
        raise CampaignError("batch results contain duplicate project_id values")

    registry_by_id = None
    if registry is not None:
        registry_by_id = {
            project["project_id"]: project for project in registry["projects"]
        }
    for result in results:
        project_id = result["project_id"]
        if result["campaign_id"] != batch["campaign_id"]:
            raise CampaignError(f"result {project_id} has a different campaign_id")
        if result["target_version"] != batch["target_version"]:
            raise CampaignError(f"result {project_id} has a different target_version")
        if result["status"] == "up_to_date" and (
            result["source_version"] is None
            or result["source_version"] != result["target_version"]
        ):
            raise CampaignError(
                f"up_to_date result {project_id} must have matching versions"
            )
        if result["branch"] is not None:
            match = UPGRADE_BRANCH.fullmatch(result["branch"])
            if not match or match.group(1) != result["target_version"]:
                raise CampaignError(
                    f"result {project_id} branch version does not match target_version"
                )
        if registry_by_id is not None:
            project = registry_by_id.get(project_id)
            if project is None:
                raise CampaignError(f"result project_id is absent from registry: {project_id}")
            if result["pr_url"] is not None:
                pr_repository = _repository_from_pr_url(result["pr_url"])
                if pr_repository.casefold() != project["repository"].casefold():
                    raise CampaignError(
                        f"result {project_id} PR repository does not match registry"
                    )


def load_results(path: Path) -> dict[str, Any]:
    document = _load_json(path)
    if isinstance(document, dict):
        validate_batch(document)
        return dict(document)
    if not isinstance(document, list) or not document:
        raise CampaignError("results input must be a non-empty result list or batch")
    for index, result in enumerate(document):
        _validate(result, RESULT_SCHEMA, f"result {index}")
    campaign_ids = {result["campaign_id"] for result in document}
    target_versions = {result["target_version"] for result in document}
    if len(campaign_ids) != 1 or len(target_versions) != 1:
        raise CampaignError("results mix campaign_id or target_version values")
    batch = {
        "schema_version": 1,
        "campaign_id": next(iter(campaign_ids)),
        "target_version": next(iter(target_versions)),
        "results": sorted(document, key=lambda result: result["project_id"]),
    }
    validate_batch(batch)
    return batch


def render_markdown(batch: Mapping[str, Any]) -> str:
    counts: dict[str, int] = {}
    for result in batch["results"]:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    lines = [
        f"# Base upgrade campaign `{batch['campaign_id']}`",
        "",
        f"- Target: `base/v{batch['target_version']}`",
        f"- Projects: {len(batch['results'])}",
        "- Status: "
        + (", ".join(f"{status}={counts[status]}" for status in sorted(counts)) or "none"),
        "",
        "| Project | Source | Status | Failed stage | PR |",
        "|---|---:|---|---|---|",
    ]
    for result in batch["results"]:
        values = [
            result["project_id"],
            result["source_version"] or "unknown",
            result["status"],
            result["failed_stage"] or "—",
            result["pr_url"] or "—",
        ]
        lines.append("| " + " | ".join(_markdown_cell(value) for value in values) + " |")
    return "\n".join(lines) + "\n"


def _markdown_cell(value: str) -> str:
    """Render untrusted result text without creating Markdown or raw HTML."""

    return "".join(
        character
        if character.isascii() and (character.isalnum() or character in " .,-/")
        else f"&#x{ord(character):X};"
        for character in value
    )


def _redacted(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        return redact(value, secrets=secrets)
    if isinstance(value, list):
        return [_redacted(item, secrets) for item in value]
    if isinstance(value, dict):
        return {
            key: _redacted(item, secrets)
            for key, item in value.items()
        }
    return value


def _sanitize_batch(
    batch: Mapping[str, Any],
    secrets: Sequence[str],
    *,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    structural_batch_fields = ("schema_version", "campaign_id", "target_version")
    structural_result_fields = (
        "campaign_id",
        "project_id",
        "source_version",
        "target_version",
        "status",
        "branch",
        "pr_url",
        "failed_stage",
        "conflict_files",
    )
    structural_names = (*structural_batch_fields, "results", *structural_result_fields)
    if any(
        redact(field, secrets=secrets) != field for field in structural_names
    ):
        raise CampaignError("a secret overlaps a structural field name")
    sanitized = _redacted(dict(batch), secrets)
    if any(sanitized[field] != batch[field] for field in structural_batch_fields):
        raise CampaignError("a secret overlaps a structural batch field")
    for original, redacted_result in zip(batch["results"], sanitized["results"]):
        if any(
            redacted_result[field] != original[field]
            for field in structural_result_fields
        ):
            raise CampaignError("a secret overlaps a structural result field")
    validate_batch(sanitized, registry=registry)
    return sanitized


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _validated_json_text(
    batch: Mapping[str, Any],
    *,
    registry: Mapping[str, Any] | None = None,
    secrets: Sequence[str] = (),
) -> str:
    """Serialize and revalidate the exact JSON artifact representation."""

    output = _json_text(batch)
    try:
        artifact = json.loads(output)
    except json.JSONDecodeError as exc:  # Defensive: json.dumps output must parse.
        raise CampaignError("generated JSON artifact is invalid") from exc
    if artifact != batch:
        raise CampaignError("generated JSON artifact changed the batch structure")
    validate_batch(artifact, registry=registry)
    if any(secret and secret in output for secret in secrets):
        raise CampaignError("a secret overlaps the generated JSON artifact")
    return output


def _text(value: str, secrets: Sequence[str]) -> str:
    return redact(value, secrets=secrets)


def _write(path: Path, content: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, path)
    except OSError as exc:
        raise CampaignError(f"cannot write output artifact: {path}") from exc
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _output_paths_collide(first: str, second: str) -> bool:
    try:
        first_path = Path(first).resolve()
        second_path = Path(second).resolve()
        if first_path == second_path:
            return True
        if first_path.exists() and second_path.exists():
            return os.path.samefile(first_path, second_path)
    except (OSError, RuntimeError) as exc:
        raise CampaignError("cannot validate output artifact paths") from exc
    return False


def _reject_output_path_collisions(
    outputs: Sequence[str | None],
    protected_inputs: Sequence[str | None] = (),
) -> None:
    populated_outputs = [path for path in outputs if path]
    for index, first in enumerate(populated_outputs):
        for second in populated_outputs[index + 1 :]:
            if _output_paths_collide(first, second):
                raise CampaignError("output artifact paths must be different")
        for protected in protected_inputs:
            if protected and _output_paths_collide(first, protected):
                raise CampaignError("output artifact path collides with an input")


def command_validate_registry(args: argparse.Namespace, secrets: Sequence[str]) -> None:
    registry = load_registry(Path(args.registry))
    message = f"registry OK: {len(registry['projects'])} project(s)"
    print(_text(message, secrets))


def command_plan(args: argparse.Namespace, secrets: Sequence[str]) -> None:
    if args.project_state_fixture and not args.dry_run:
        raise CampaignError("--project-state-fixture is only allowed with --dry-run")
    if args.dry_run and not args.project_state_fixture:
        raise CampaignError("--dry-run requires --project-state-fixture")
    campaign_id = args.campaign_id
    if not campaign_id:
        if not args.dry_run:
            raise CampaignError("--campaign-id is required in live Provider mode")
        campaign_id = f"plan-v{args.target_version}"
    _validate_plan_inputs(
        target_version=args.target_version,
        campaign_id=campaign_id,
        channel=args.channel,
    )
    _reject_output_path_collisions(
        (args.output,), (args.registry, args.project_state_fixture)
    )
    registry = load_registry(Path(args.registry))
    states = (
        _load_project_fixture(Path(args.project_state_fixture))
        if args.project_state_fixture
        else None
    )
    batch = plan_campaign(
        registry,
        target_version=args.target_version,
        campaign_id=campaign_id,
        channel=args.channel,
        project_states=states,
        token=os.environ.get("BASE_UPGRADE_GITHUB_TOKEN"),
        allow_major=args.allow_major,
    )
    sanitized = _sanitize_batch(batch, secrets, registry=registry)
    output = _validated_json_text(sanitized, registry=registry, secrets=secrets)
    if args.output:
        _write(Path(args.output), output)
    else:
        print(output, end="")


def command_summarize(args: argparse.Namespace, secrets: Sequence[str]) -> None:
    _reject_output_path_collisions(
        (args.json_output, args.markdown_output), (args.results, args.registry)
    )
    batch = load_results(Path(args.results))
    registry = load_registry(Path(args.registry)) if args.registry else None
    validate_batch(batch, registry=registry)
    sanitized = _sanitize_batch(batch, secrets, registry=registry)
    json_output = _validated_json_text(
        sanitized, registry=registry, secrets=secrets
    )
    markdown_output = render_markdown(sanitized)
    if any(secret and secret in markdown_output for secret in secrets):
        raise CampaignError("a secret overlaps the generated Markdown artifact")
    if args.json_output:
        _write(Path(args.json_output), json_output)
    if args.markdown_output:
        _write(Path(args.markdown_output), markdown_output)
    if not args.json_output and not args.markdown_output:
        print(markdown_output, end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate-registry", help="validate registry schema and uniqueness"
    )
    validate_parser.add_argument("--registry", required=True)
    validate_parser.set_defaults(handler=command_validate_registry)

    plan_parser = subparsers.add_parser("plan", help="build a read-only upgrade plan")
    plan_parser.add_argument("--registry", required=True)
    plan_parser.add_argument("--target-version", required=True)
    plan_parser.add_argument("--channel", default="stable")
    plan_parser.add_argument("--campaign-id")
    plan_parser.add_argument("--allow-major", action="store_true")
    plan_parser.add_argument("--dry-run", action="store_true")
    plan_parser.add_argument("--project-state-fixture")
    plan_parser.add_argument("--output")
    plan_parser.set_defaults(handler=command_plan)

    summarize_parser = subparsers.add_parser(
        "summarize", help="validate and summarize per-project results"
    )
    summarize_parser.add_argument("--results", required=True)
    summarize_parser.add_argument("--registry")
    summarize_parser.add_argument("--json-output")
    summarize_parser.add_argument("--markdown-output")
    summarize_parser.set_defaults(handler=command_summarize)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    token = os.environ.get("BASE_UPGRADE_GITHUB_TOKEN", "")
    secrets = (token,) if token else ()
    try:
        args.handler(args, secrets)
    except (CampaignError, BaseReleaseError) as exc:
        print(_text(f"ERROR: {exc}", secrets), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
