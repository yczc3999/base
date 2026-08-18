#!/usr/bin/env python3
"""Read-only planning and deterministic reporting for Base upgrade campaigns."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

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
EVIDENCE_SCHEMA = SCHEMAS / "base-upgrade-evidence.schema.json"
PROJECT_VERSION_KEY = "BASE_UPSTREAM_VERSION"
DEFAULT_TIMEOUT = 15
MAX_PROVIDER_ATTEMPTS = 3
MAX_PROVIDER_RETRY_DELAY = 60.0
MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PROJECT_MD_BYTES = 1024 * 1024
MAX_ARTIFACT_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_RESULT_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_UNCOMPRESSED_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_COMPRESSION_RATIO = 100
MAX_DISPATCH_ATTEMPTS = 2
# The v1 operator is intentionally serial. Keep one campaign within the
# example workflow's bounded runtime and the three-project pilot contract.
MAX_DISPATCH_PROJECTS = 3
MAX_RUN_WAIT_SECONDS = 30 * 60
MAX_CANCEL_WAIT_SECONDS = 5 * 60
RUN_POLL_SECONDS = 5.0
RUN_RECOVERY_WINDOW_SECONDS = 5 * 60
RECEIVER_WORKFLOW = "base-upgrade-receiver.yml"
UPGRADE_BRANCH = re.compile(r"^chore/base-v(.+)$")
CAMPAIGN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CHANNEL = re.compile(r"^[a-z0-9][a-z0-9-]*$")
GITHUB_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}$"
)


class CampaignError(RuntimeError):
    """A safe, user-facing campaign validation or provider error."""


class ProviderHTTPError(CampaignError):
    """A bounded GitHub response carrying only status and safe headers."""

    def __init__(self, status: int, headers: Mapping[str, str] | None = None):
        super().__init__(f"GitHub API returned HTTP {status}")
        self.status = status
        self.headers = dict(headers or {})


class ProviderNetworkError(CampaignError):
    """A transport failure whose original message must never reach artifacts."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPEN = build_opener(_NoRedirectHandler()).open


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


def _provider_rate_limit_delay(
    status: int,
    headers: Mapping[str, str] | None,
    *,
    now: Callable[[], float] = time.time,
) -> float | None:
    """Recognize explicit throttling without retrying an ordinary HTTP 403."""

    normalized = {
        str(key).casefold(): str(value).strip()
        for key, value in (headers or {}).items()
    }
    if "retry-after" in normalized:
        return _retry_delay(headers, now=now)
    remaining = normalized.get("x-ratelimit-remaining")
    if "x-ratelimit-reset" in normalized and (
        remaining == "0" or status == 429
    ):
        return _retry_delay(headers, now=now)
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


def _utc_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_operator_commit(value: str | None = None) -> str:
    candidate = value or os.environ.get("BASE_UPGRADE_OPERATOR_COMMIT", "")
    if not candidate:
        try:
            candidate = subprocess.check_output(
                ["git", "rev-parse", "--verify", "HEAD"],
                cwd=ROOT,
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise CampaignError("operator commit could not be discovered") from exc
    if re.fullmatch(r"[0-9a-f]{40}", candidate) is None:
        raise CampaignError("operator commit must be an immutable 40-hex commit")
    return candidate


def _parse_utc_timestamp(value: Any) -> float:
    if not isinstance(value, str):
        raise CampaignError("GitHub API timestamp is invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise CampaignError("GitHub API timestamp is invalid") from exc
    return parsed.timestamp()


def _read_response_bytes(response: Any, limit: int, label: str) -> bytes:
    try:
        raw = response.read(limit + 1)
        if not isinstance(raw, (bytes, bytearray)):
            raise CampaignError(f"{label} returned a non-bytes response")
        if len(raw) > limit:
            raise CampaignError(f"{label} exceeds the size limit")
        return bytes(raw)
    finally:
        close = getattr(response, "close", None)
        if close:
            close()


def _http_error_details(exc: HTTPError) -> tuple[int, dict[str, str]]:
    try:
        return exc.code, dict(exc.headers.items()) if exc.headers is not None else {}
    finally:
        try:
            exc.close()
        except Exception:
            pass


class GitHubDispatchClient:
    """Bounded GitHub REST transport for one operator campaign."""

    def __init__(
        self,
        token: str,
        *,
        http_open: Callable[..., Any] = _NO_REDIRECT_OPEN,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        if not token:
            raise CampaignError("BASE_UPGRADE_GITHUB_TOKEN is required for dispatch")
        self.token = token
        self.http_open = http_open
        self.sleep = sleep
        self.now = now
        self.timeout = timeout

    def _api_request_once(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        expected: Sequence[int] = (200,),
    ) -> tuple[int, dict[str, str], bytes]:
        if not path.startswith("/") or "\r" in path or "\n" in path:
            raise CampaignError("GitHub API path is invalid")
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "base-upgrade-campaign/1",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"https://api.github.com{path}", headers=headers, data=data, method=method
        )
        try:
            response = self.http_open(request, timeout=self.timeout)
        except HTTPError as exc:
            if exc.code in expected:
                response_headers = (
                    dict(exc.headers.items()) if exc.headers is not None else {}
                )
                raw = _read_response_bytes(
                    exc, MAX_PROVIDER_RESPONSE_BYTES, "GitHub API response"
                )
                return exc.code, response_headers, raw
            status, response_headers = _http_error_details(exc)
            raise ProviderHTTPError(status, response_headers) from exc
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            raise ProviderNetworkError("GitHub API network failure") from exc
        except Exception as exc:
            raise ProviderNetworkError("GitHub API unexpected transport failure") from exc
        status_value = getattr(response, "status", None)
        if status_value is None:
            status_value = response.getcode()
        status = int(status_value)
        response_headers = dict(getattr(response, "headers", {}) or {})
        raw = _read_response_bytes(
            response, MAX_PROVIDER_RESPONSE_BYTES, "GitHub API response"
        )
        if status not in expected:
            raise ProviderHTTPError(status, response_headers)
        return status, response_headers, raw

    def api_json(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        expected: Sequence[int] = (200,),
        allow_not_found: bool = False,
        max_attempts: int = MAX_PROVIDER_ATTEMPTS,
    ) -> Any:
        attempt_limit = min(max(1, max_attempts), MAX_PROVIDER_ATTEMPTS)
        for attempt in range(attempt_limit):
            try:
                _, _, raw = self._api_request_once(
                    method, path, payload=payload, expected=expected
                )
            except ProviderHTTPError as exc:
                if allow_not_found and exc.status == 404:
                    return None
                delay = _provider_rate_limit_delay(
                    exc.status, exc.headers, now=self.now
                )
                retryable = exc.status >= 500 or (
                    exc.status in {403, 429} and delay is not None
                )
                if not retryable or attempt + 1 >= attempt_limit:
                    raise
                self.sleep(delay if delay is not None else float(2**attempt))
                continue
            except ProviderNetworkError:
                if attempt + 1 >= attempt_limit:
                    raise
                self.sleep(float(2**attempt))
                continue
            if not raw:
                return None
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CampaignError("GitHub API returned invalid JSON") from exc
        raise AssertionError("GitHub API retry loop exhausted unexpectedly")

    def contents_path_text(
        self, project: Mapping[str, Any], ref: str, filename: str
    ) -> str:
        if filename not in {"PROJECT.md", "BASE_UPDATES.md"}:
            raise CampaignError("unsupported downstream ledger path")
        owner, repository = project["repository"].split("/", 1)
        path = (
            f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
            f"/contents/{filename}?{urlencode({'ref': ref})}"
        )
        payload = self.api_json("GET", path)
        if (
            not isinstance(payload, dict)
            or payload.get("type") != "file"
            or payload.get("encoding") != "base64"
            or not isinstance(payload.get("content"), str)
        ):
            raise CampaignError("GitHub Contents API response is invalid")
        try:
            raw = base64.b64decode(payload["content"].replace("\n", ""), validate=True)
            if len(raw) > MAX_PROJECT_MD_BYTES:
                raise ValueError("PROJECT.md exceeds size limit")
            return raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise CampaignError("GitHub Contents API response is invalid") from exc

    def contents_text(self, project: Mapping[str, Any], ref: str) -> str:
        return self.contents_path_text(project, ref, "PROJECT.md")

    def open_upgrade_prs(
        self, project: Mapping[str, Any], target_version: str
    ) -> list[dict[str, Any]]:
        owner, repository = project["repository"].split("/", 1)
        branch = f"chore/base-v{target_version}"
        query = urlencode(
            {
                "state": "open",
                "base": project["default_branch"],
                "head": f"{owner}:{branch}",
                "per_page": "100",
            }
        )
        rows = self.api_json(
            "GET",
            f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/pulls?{query}",
        )
        if not isinstance(rows, list):
            raise CampaignError("GitHub pull request preflight returned invalid data")
        expected_url = re.compile(
            rf"^https://github\.com/{re.escape(project['repository'])}/pull/[1-9][0-9]*$",
            flags=re.IGNORECASE,
        )
        validated: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise CampaignError("GitHub pull request preflight returned invalid data")
            head = row.get("head")
            base = row.get("base")
            if (
                row.get("state") != "open"
                or not isinstance(row.get("number"), int)
                or isinstance(row.get("number"), bool)
                or not isinstance(head, dict)
                or head.get("ref") != branch
                or not isinstance(head.get("repo"), dict)
                or str(head["repo"].get("full_name", "")).casefold()
                != project["repository"].casefold()
                or re.fullmatch(r"[0-9a-f]{40}", str(head.get("sha", ""))) is None
                or not isinstance(base, dict)
                or base.get("ref") != project["default_branch"]
                or not isinstance(row.get("html_url"), str)
                or expected_url.fullmatch(row["html_url"]) is None
            ):
                raise CampaignError("GitHub pull request preflight returned invalid data")
            validated.append(row)
        if len(validated) > 1:
            raise CampaignError("multiple open pull requests claim the upgrade branch")
        return validated

    def verify_existing_branch(
        self,
        project: Mapping[str, Any],
        target_version: str,
        base_repository: str,
    ) -> bool:
        owner, repository = project["repository"].split("/", 1)
        prefix = f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
        branch = f"chore/base-v{target_version}"
        ref = self.api_json(
            "GET",
            f"{prefix}/git/ref/heads/{quote(branch, safe='')}",
            allow_not_found=True,
        )
        if ref is None:
            return False
        if (
            not isinstance(ref, dict)
            or ref.get("ref") != f"refs/heads/{branch}"
            or not isinstance(ref.get("object"), dict)
            or ref["object"].get("type") != "commit"
            or re.fullmatch(r"[0-9a-f]{40}", str(ref["object"].get("sha", ""))) is None
        ):
            raise CampaignError("existing upgrade branch metadata is invalid")
        branch_sha = ref["object"]["sha"]
        if GITHUB_REPOSITORY.fullmatch(base_repository) is None:
            raise CampaignError("default-branch Base upstream identity is invalid")
        base_owner, base_name = base_repository.split("/", 1)
        base_prefix = (
            f"/repos/{quote(base_owner, safe='')}/{quote(base_name, safe='')}"
        )
        target_ref = self.api_json(
            "GET",
            f"{base_prefix}/git/ref/tags/{quote(f'base/v{target_version}', safe='')}",
        )
        target_object = target_ref.get("object") if isinstance(target_ref, dict) else None
        if (
            not isinstance(target_ref, dict)
            or target_ref.get("ref") != f"refs/tags/base/v{target_version}"
            or not isinstance(target_object, dict)
        ):
            raise CampaignError("target Base tag metadata is invalid")
        if target_object.get("type") == "tag":
            tag_sha = str(target_object.get("sha", ""))
            if re.fullmatch(r"[0-9a-f]{40}", tag_sha) is None:
                raise CampaignError("target Base annotated tag identity is invalid")
            tag = self.api_json("GET", f"{base_prefix}/git/tags/{tag_sha}")
            target_object = tag.get("object") if isinstance(tag, dict) else None
        if (
            not isinstance(target_object, dict)
            or target_object.get("type") != "commit"
            or re.fullmatch(r"[0-9a-f]{40}", str(target_object.get("sha", "")))
            is None
        ):
            raise CampaignError("target Base tag does not resolve to one commit")
        target_commit = target_object["sha"]
        branch_project_text = self.contents_text(project, branch)
        branch_values = parse_project_text(branch_project_text, strict=True)
        synced_at = branch_values.get("BASE_LAST_SYNCED_AT", "")
        if (
            branch_values.get("BASE_UPSTREAM_REPOSITORY") != base_repository
            or branch_values.get("BASE_UPSTREAM_VERSION") != target_version
            or branch_values.get("BASE_UPSTREAM_TAG") != f"base/v{target_version}"
            or branch_values.get("BASE_UPSTREAM_COMMIT") != target_commit
            or branch_values.get("BASE_UPDATE_LEDGER") != "BASE_UPDATES.md"
            or re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
                synced_at,
            )
            is None
        ):
            raise CampaignError("existing upgrade branch ledger is invalid")
        _parse_utc_timestamp(synced_at)
        history = self.contents_path_text(project, branch, "BASE_UPDATES.md")
        entry = re.split(r"^---\s*$", history, flags=re.MULTILINE)[-1]
        headings = re.findall(r"^## (.+)$", entry, re.MULTILINE)
        target_heading = re.compile(
            rf"Base update: v(?:0|[1-9][0-9]*)\."
            rf"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*) → v{re.escape(target_version)}"
        )
        if (
            len(headings) != 1
            or target_heading.fullmatch(headings[0]) is None
            or re.findall(r"^- Base commit: `([0-9a-f]{40})`$", entry, re.MULTILINE)
            != [target_commit]
            or re.findall(r"^- Synced at: `([^`\r\n]+)`$", entry, re.MULTILINE)
            != [synced_at]
            or len(
                re.findall(
                    r"^- Verification result: (\S[^\r\n]*)$", entry, re.MULTILINE
                )
            )
            != 1
        ):
            raise CampaignError("existing upgrade branch history is invalid")
        default_ref = self.api_json(
            "GET",
            f"{prefix}/git/ref/heads/{quote(project['default_branch'], safe='')}",
        )
        default_object = (
            default_ref.get("object") if isinstance(default_ref, dict) else None
        )
        default_sha = (
            str(default_object.get("sha", ""))
            if isinstance(default_object, dict)
            and default_object.get("type") == "commit"
            else ""
        )
        if re.fullmatch(r"[0-9a-f]{40}", default_sha) is None:
            raise CampaignError("default branch tip metadata is invalid")
        commit = self.api_json("GET", f"{prefix}/git/commits/{branch_sha}")
        parents = commit.get("parents") if isinstance(commit, dict) else None
        if (
            commit.get("sha") != branch_sha
            or not isinstance(parents, list)
            or len(parents) != 2
            or not all(isinstance(item, dict) for item in parents)
            or parents[0].get("sha") != default_sha
            or parents[1].get("sha") != target_commit
        ):
            raise CampaignError("existing upgrade branch ownership could not be proven")
        return True

    def _recover_dispatch_run(
        self,
        project: Mapping[str, Any],
        *,
        campaign_id: str,
        target_version: str,
        dispatched_after: float,
    ) -> dict[str, Any] | None:
        owner, repository = project["repository"].split("/", 1)
        query = urlencode(
            {
                "event": "workflow_dispatch",
                "branch": project["default_branch"],
                "per_page": "100",
            }
        )
        expected_name = f"Base upgrade {campaign_id} to v{target_version}"
        for recovery_attempt in range(MAX_PROVIDER_ATTEMPTS):
            payload = self.api_json(
                "GET",
                f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
                f"/actions/workflows/{RECEIVER_WORKFLOW}/runs?{query}",
            )
            rows = payload.get("workflow_runs") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise CampaignError("workflow run recovery returned invalid data")
            matches = []
            for row in rows:
                if not isinstance(row, dict):
                    raise CampaignError("workflow run recovery returned invalid data")
                try:
                    created_at = _parse_utc_timestamp(row.get("created_at"))
                except CampaignError:
                    continue
                if (
                    row.get("event") == "workflow_dispatch"
                    and row.get("head_branch") == project["default_branch"]
                    and row.get("display_title") == expected_name
                    and dispatched_after - 5 <= created_at
                    <= dispatched_after + RUN_RECOVERY_WINDOW_SECONDS
                ):
                    matches.append(self._validate_run_identity(project, row))
            if len(matches) > 1:
                raise CampaignError("workflow dispatch recovery is ambiguous")
            if matches:
                return matches[0]
            if recovery_attempt + 1 < MAX_PROVIDER_ATTEMPTS:
                self.sleep(float(2**recovery_attempt))
        return None

    def _validate_run_identity(
        self,
        project: Mapping[str, Any],
        run: Any,
    ) -> dict[str, Any]:
        if not isinstance(run, dict):
            raise CampaignError("workflow run data is invalid")
        run_id = run.get("id")
        expected_url = (
            f"https://github.com/{project['repository']}/actions/runs/{run_id}"
        )
        if (
            not isinstance(run_id, int)
            or isinstance(run_id, bool)
            or run_id < 1
            or run.get("html_url") != expected_url
            or run.get("url")
            != f"https://api.github.com/repos/{project['repository']}/actions/runs/{run_id}"
            or run.get("event") != "workflow_dispatch"
            or run.get("head_branch") != project["default_branch"]
            or re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha", ""))) is None
            or not isinstance(run.get("workflow_id"), int)
            or isinstance(run.get("workflow_id"), bool)
            or run["workflow_id"] < 1
            or run.get("path")
            not in {
                f".github/workflows/{RECEIVER_WORKFLOW}",
                f".github/workflows/{RECEIVER_WORKFLOW}@{project['default_branch']}",
            }
            or not isinstance(run.get("repository"), dict)
            or str(run["repository"].get("full_name", "")).casefold()
            != project["repository"].casefold()
        ):
            raise CampaignError("workflow run identity is invalid")
        return run

    def dispatch_receiver(
        self,
        project: Mapping[str, Any],
        *,
        campaign_id: str,
        target_version: str,
        allow_major: bool,
    ) -> tuple[dict[str, Any], str]:
        owner, repository = project["repository"].split("/", 1)
        repository_prefix = (
            f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
        )
        path = (
            repository_prefix
            +
            f"/actions/workflows/{RECEIVER_WORKFLOW}/dispatches"
        )
        body = {
            "ref": project["default_branch"],
            "inputs": {
                "project_id": project["project_id"],
                "target_version": target_version,
                "campaign_id": campaign_id,
                "allow_major": allow_major,
            },
        }
        dispatched_at = self.now()
        for attempt in range(MAX_DISPATCH_ATTEMPTS):
            try:
                _, _, raw = self._api_request_once(
                    "POST", path, payload=body, expected=(200,)
                )
            except ProviderHTTPError as exc:
                delay = _provider_rate_limit_delay(
                    exc.status, exc.headers, now=self.now
                )
                if exc.status in {403, 429}:
                    if delay is None or attempt + 1 >= MAX_DISPATCH_ATTEMPTS:
                        raise
                    self.sleep(delay)
                    continue
                if exc.status < 500:
                    raise
                recovered = self._recover_dispatch_run(
                    project,
                    campaign_id=campaign_id,
                    target_version=target_version,
                    dispatched_after=dispatched_at,
                )
                if recovered is not None:
                    return recovered, _utc_timestamp(dispatched_at)
                if attempt + 1 >= MAX_DISPATCH_ATTEMPTS:
                    raise
                self.sleep(float(2**attempt))
                continue
            except ProviderNetworkError:
                recovered = self._recover_dispatch_run(
                    project,
                    campaign_id=campaign_id,
                    target_version=target_version,
                    dispatched_after=dispatched_at,
                )
                if recovered is not None:
                    return recovered, _utc_timestamp(dispatched_at)
                if attempt + 1 >= MAX_DISPATCH_ATTEMPTS:
                    raise
                self.sleep(float(2**attempt))
                continue
            try:
                response = json.loads(raw.decode("utf-8"))
                if not isinstance(response, dict):
                    raise ValueError("not an object")
                run_id = response.get("workflow_run_id")
                expected_api_url = (
                    f"https://api.github.com/repos/{project['repository']}"
                    f"/actions/runs/{run_id}"
                )
                expected_html_url = (
                    f"https://github.com/{project['repository']}/actions/runs/{run_id}"
                )
                if (
                    not isinstance(run_id, int)
                    or isinstance(run_id, bool)
                    or run_id < 1
                    or response.get("run_url") != expected_api_url
                    or response.get("html_url") != expected_html_url
                ):
                    raise ValueError("invalid locator")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                recovered = self._recover_dispatch_run(
                    project,
                    campaign_id=campaign_id,
                    target_version=target_version,
                    dispatched_after=dispatched_at,
                )
                if recovered is not None:
                    return recovered, _utc_timestamp(dispatched_at)
                if attempt + 1 >= MAX_DISPATCH_ATTEMPTS:
                    raise CampaignError("workflow dispatch returned invalid run details")
                self.sleep(float(2**attempt))
                continue
            locator = {"id": run_id, "html_url": expected_html_url}
            try:
                run = self.api_json(
                    "GET",
                    f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
                    f"/actions/runs/{run_id}",
                )
                run = self._validate_run_identity(project, run)
            except Exception:
                # The 200 response is authoritative. Preserve its run locator;
                # wait_for_run will retry the exact run ID and evidence keeps it
                # even if that later polling phase fails.
                run = locator
            return run, _utc_timestamp(dispatched_at)
        raise AssertionError("workflow dispatch retry loop exhausted unexpectedly")

    def wait_for_run(
        self,
        project: Mapping[str, Any],
        run_id: int,
        *,
        max_wait_seconds: int = MAX_RUN_WAIT_SECONDS,
    ) -> dict[str, Any]:
        owner, repository = project["repository"].split("/", 1)
        prefix = f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
        start = self.now()
        while self.now() - start <= max_wait_seconds:
            run = self._validate_run_identity(
                project, self.api_json("GET", f"{prefix}/actions/runs/{run_id}")
            )
            if run.get("status") == "completed":
                _parse_utc_timestamp(run.get("updated_at"))
                conclusion = run.get("conclusion")
                if conclusion not in {
                    "success", "failure", "cancelled", "skipped", "timed_out",
                    "action_required", "neutral", "stale", "startup_failure",
                }:
                    raise CampaignError("completed workflow run has invalid conclusion")
                return run
            if run.get("status") not in {
                "requested", "queued", "in_progress", "pending", "waiting"
            }:
                raise CampaignError("workflow run returned an invalid status")
            self.sleep(RUN_POLL_SECONDS)

        self.api_json(
            "POST", f"{prefix}/actions/runs/{run_id}/cancel", expected=(202, 409),
            max_attempts=1,
        )
        cancel_start = self.now()
        while self.now() - cancel_start <= MAX_CANCEL_WAIT_SECONDS:
            run = self._validate_run_identity(
                project, self.api_json("GET", f"{prefix}/actions/runs/{run_id}")
            )
            if run.get("status") == "completed":
                conclusion = run.get("conclusion")
                if not isinstance(conclusion, str) or not conclusion:
                    raise CampaignError("cancelled workflow run has no conclusion")
                raise CampaignError("workflow run exceeded 30 minutes and was cancelled")
            self.sleep(RUN_POLL_SECONDS)
        raise CampaignError("cancelled workflow run did not reach a terminal state")

    def _artifact_redirect(self, archive_url: str) -> str:
        parsed = urlsplit(archive_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.fragment
        ):
            raise CampaignError("artifact archive URL is invalid")
        request = Request(
            archive_url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "base-upgrade-campaign/1",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            method="GET",
        )
        try:
            response = self.http_open(request, timeout=self.timeout)
        except HTTPError as exc:
            status = exc.code
            headers = dict(exc.headers.items()) if exc.headers is not None else {}
            location = headers.get("Location") or headers.get("location")
            try:
                exc.close()
            except Exception:
                pass
            if status not in {301, 302, 303, 307, 308} or not location:
                raise ProviderHTTPError(status, headers) from exc
            return location
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            raise ProviderNetworkError("artifact redirect request failed") from exc
        else:
            close = getattr(response, "close", None)
            if close:
                close()
            raise CampaignError("artifact archive endpoint did not return a redirect")

    def _download_signed_artifact(self, location: str) -> bytes:
        parsed = urlsplit(location)
        host = (parsed.hostname or "").casefold()
        allowed = (
            host == "objects.githubusercontent.com"
            or host.endswith(".actions.githubusercontent.com")
            or host.endswith(".blob.core.windows.net")
        )
        if (
            parsed.scheme != "https"
            or not allowed
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.fragment
            or not parsed.query
        ):
            raise CampaignError("artifact redirect target is not an approved signed URL")
        # Deliberately omit Authorization: signed storage hosts must never receive
        # the operator's GitHub Bearer token.
        request = Request(location, headers={"User-Agent": "base-upgrade-campaign/1"})
        try:
            response = self.http_open(request, timeout=self.timeout)
        except HTTPError as exc:
            status, headers = _http_error_details(exc)
            raise ProviderHTTPError(status, headers) from exc
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            raise ProviderNetworkError("artifact download failed") from exc
        status_value = getattr(response, "status", None)
        if status_value is None:
            status_value = response.getcode()
        status = int(status_value)
        if status != 200:
            close = getattr(response, "close", None)
            if close:
                close()
            raise ProviderHTTPError(status, getattr(response, "headers", {}))
        return _read_response_bytes(
            response, MAX_ARTIFACT_ARCHIVE_BYTES, "artifact archive"
        )

    def collect_result_artifact(
        self,
        project: Mapping[str, Any],
        *,
        run_id: int,
        campaign_id: str,
        target_version: str,
        expected_source_version: str,
        expected_pr_url: str | None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        owner, repository = project["repository"].split("/", 1)
        prefix = f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
        payload = self.api_json(
            "GET", f"{prefix}/actions/runs/{run_id}/artifacts?per_page=100"
        )
        rows = payload.get("artifacts") if isinstance(payload, dict) else None
        total_count = payload.get("total_count") if isinstance(payload, dict) else None
        expected_name = f"base-upgrade-result-{campaign_id}"
        matches = (
            [row for row in rows if isinstance(row, dict) and row.get("name") == expected_name]
            if isinstance(rows, list)
            else []
        )
        if (
            not isinstance(rows, list)
            or not isinstance(total_count, int)
            or isinstance(total_count, bool)
            or total_count != len(rows)
            or len(matches) != 1
        ):
            raise CampaignError("workflow run did not publish one exact result artifact")
        artifact = matches[0]
        digest = artifact.get("digest")
        archive_url = artifact.get("archive_download_url")
        if (
            artifact.get("expired") is not False
            or not isinstance(artifact.get("id"), int)
            or isinstance(artifact.get("id"), bool)
            or not isinstance(archive_url, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            or not isinstance(artifact.get("workflow_run"), dict)
            or artifact["workflow_run"].get("id") != run_id
        ):
            raise CampaignError("result artifact metadata is invalid")
        expected_archive_url = (
            f"https://api.github.com/repos/{project['repository']}"
            f"/actions/artifacts/{artifact['id']}/zip"
        )
        if archive_url != expected_archive_url:
            raise CampaignError("result artifact download URL is not repository-bound")
        location = self._artifact_redirect(expected_archive_url)
        archive = self._download_signed_artifact(location)
        archive_sha256 = hashlib.sha256(archive).hexdigest()
        if digest != f"sha256:{archive_sha256}":
            raise CampaignError("result artifact digest does not match metadata")
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                entries = bundle.infolist()
                if len(entries) != 1:
                    raise CampaignError("result artifact must contain exactly one file")
                entry = entries[0]
                parts = Path(entry.filename).parts
                ratio = entry.file_size / max(1, entry.compress_size)
                if (
                    entry.is_dir()
                    or entry.flag_bits & 0x1
                    or ((entry.external_attr >> 16) & 0o170000) == 0o120000
                    or entry.filename != "base-upgrade-result.json"
                    or "\\" in entry.filename
                    or "\x00" in entry.filename
                    or Path(entry.filename).is_absolute()
                    or any(part in {"", ".", ".."} for part in parts)
                    or entry.file_size > MAX_ARTIFACT_RESULT_BYTES
                    or sum(item.file_size for item in entries)
                    > MAX_ARTIFACT_UNCOMPRESSED_BYTES
                    or ratio > MAX_ARTIFACT_COMPRESSION_RATIO
                ):
                    raise CampaignError("result artifact archive is unsafe")
                result_bytes = bundle.read(entry)
        except CampaignError:
            raise
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise CampaignError("result artifact archive is invalid") from exc
        if len(result_bytes) > MAX_ARTIFACT_RESULT_BYTES:
            raise CampaignError("result artifact exceeds the size limit")
        try:
            def reject_duplicate_keys(pairs):
                output = {}
                for key, value in pairs:
                    if key in output:
                        raise ValueError("duplicate JSON key")
                    output[key] = value
                return output

            result = json.loads(
                result_bytes.decode("utf-8"), object_pairs_hook=reject_duplicate_keys
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise CampaignError("result artifact is not valid UTF-8 JSON") from exc
        _validate(result, RESULT_SCHEMA, f"result {project['project_id']}")
        if (
            result["campaign_id"] != campaign_id
            or result["project_id"] != project["project_id"]
            or result["target_version"] != target_version
            or (
                result["source_version"] != expected_source_version
                and not (
                    result["status"] == "up_to_date"
                    and result["source_version"] == target_version
                )
            )
        ):
            raise CampaignError("result artifact identity does not match the dispatch")
        if result["status"] in {"planned", "dispatched"}:
            raise CampaignError("receiver artifact contains a non-terminal result status")
        if (
            expected_pr_url is not None
            and result["status"] == "pr_opened"
            and result.get("pr_url") != expected_pr_url
        ):
            raise CampaignError("receiver artifact does not preserve the existing PR identity")
        validate_batch(
            {
                "schema_version": 1,
                "campaign_id": campaign_id,
                "target_version": target_version,
                "results": [result],
            },
            registry={"projects": [dict(project)]},
        )
        return result, {
            "artifact_name": expected_name,
            "artifact_sha256": archive_sha256,
            "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        }


def _operator_failure_result(
    project: Mapping[str, Any],
    *,
    campaign_id: str,
    source_version: str | None,
    target_version: str,
    status: str,
    failed_stage: str,
    summary: str,
    allow_major: bool = False,
) -> dict[str, Any]:
    if status not in {"blocked", "dispatch_failed"}:
        raise CampaignError("operator failure status is invalid")
    repository = project["repository"]
    result = {
        "campaign_id": campaign_id,
        "project_id": project["project_id"],
        "source_version": source_version,
        "target_version": target_version,
        "status": status,
        "branch": None,
        "pr_url": None,
        "failed_stage": failed_stage,
        "conflict_files": [],
        "verification_summary": summary,
        "retry_command": (
            "gh workflow run base-upgrade-receiver.yml "
            f"--repo {repository} -f project_id={project['project_id']} "
            f"-f target_version={target_version} -f campaign_id={campaign_id} "
            f"-f allow_major={'true' if allow_major else 'false'}"
        ),
        "rollback_command": (
            "no automatic rollback; operator dispatch does not write the default branch"
        ),
    }
    _validate(result, RESULT_SCHEMA, f"result {project['project_id']}")
    return result


def _up_to_date_dispatch_result(
    project: Mapping[str, Any], *, campaign_id: str, target_version: str
) -> dict[str, Any]:
    result = {
        "campaign_id": campaign_id,
        "project_id": project["project_id"],
        "source_version": target_version,
        "target_version": target_version,
        "status": "up_to_date",
        "branch": None,
        "pr_url": None,
        "failed_stage": None,
        "conflict_files": [],
        "verification_summary": "PROJECT.md already records the target Base version",
        "retry_command": "no retry required; project is already at the target version",
        "rollback_command": "no rollback required; operator made no changes",
    }
    _validate(result, RESULT_SCHEMA, f"result {project['project_id']}")
    return result


def dispatch_campaign(
    registry: Mapping[str, Any],
    *,
    target_version: str,
    campaign_id: str,
    channel: str,
    allow_major: bool,
    client: GitHubDispatchClient,
    operator_commit: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Dispatch enabled projects independently and collect authoritative artifacts."""

    _validate_plan_inputs(
        target_version=target_version, campaign_id=campaign_id, channel=channel
    )
    projects = sorted(
        (
            item
            for item in registry["projects"]
            if item["enabled"] and item["channel"] == channel
        ),
        key=lambda item: item["project_id"],
    )
    if not projects:
        raise CampaignError(f"no enabled projects match channel {channel}")
    if len(projects) > MAX_DISPATCH_PROJECTS:
        raise CampaignError(
            f"serial dispatch selects {len(projects)} projects; "
            f"the v1 campaign limit is {MAX_DISPATCH_PROJECTS}"
        )
    manifests = load_manifests()
    if target_version not in manifests:
        raise CampaignError(f"missing target release manifest {target_version}")

    results: list[dict[str, Any]] = []
    evidence_entries: list[dict[str, Any]] = []
    for project in projects:
        source_version: str | None = None
        base_repository = ""
        try:
            project_text = client.contents_text(project, project["default_branch"])
            source_version = _project_version(project_text)
            project_values = parse_project_text(project_text, strict=True)
            base_repository = project_values.get("BASE_UPSTREAM_REPOSITORY", "")
            if GITHUB_REPOSITORY.fullmatch(base_repository) is None:
                raise CampaignError("PROJECT.md Base upstream repository is invalid")
        except Exception:
            results.append(
                _operator_failure_result(
                    project,
                    campaign_id=campaign_id,
                    source_version=None,
                    target_version=target_version,
                    status="blocked",
                    failed_stage="project_discovery",
                    summary="PROJECT.md version discovery failed",
                    allow_major=allow_major,
                )
            )
            continue

        source_tuple = parse_core_semver(source_version)
        target_tuple = parse_core_semver(target_version)
        if source_tuple == target_tuple:
            results.append(
                _up_to_date_dispatch_result(
                    project, campaign_id=campaign_id, target_version=target_version
                )
            )
            continue
        if source_tuple > target_tuple or (
            source_tuple[0] != target_tuple[0] and not allow_major
        ):
            reason = (
                "target is older than the recorded Base version"
                if source_tuple > target_tuple
                else "cross-MAJOR upgrade requires explicit --allow-major"
            )
            results.append(
                _operator_failure_result(
                    project,
                    campaign_id=campaign_id,
                    source_version=source_version,
                    target_version=target_version,
                    status="blocked",
                    failed_stage="version_gate",
                    summary=reason,
                    allow_major=allow_major,
                )
            )
            continue
        try:
            select_manifests(manifests, source_version, target_version)
            open_prs = client.open_upgrade_prs(project, target_version)
            if not open_prs:
                client.verify_existing_branch(
                    project, target_version, base_repository
                )
        except Exception:
            results.append(
                _operator_failure_result(
                    project,
                    campaign_id=campaign_id,
                    source_version=source_version,
                    target_version=target_version,
                    status="blocked",
                    failed_stage="provider_preflight",
                    summary="open PR or upgrade branch ownership preflight failed",
                    allow_major=allow_major,
                )
            )
            continue

        existing_pr_url = open_prs[0]["html_url"] if open_prs else None
        try:
            run, dispatched_at = client.dispatch_receiver(
                project,
                campaign_id=campaign_id,
                target_version=target_version,
                allow_major=allow_major,
            )
        except Exception:
            results.append(
                _operator_failure_result(
                    project,
                    campaign_id=campaign_id,
                    source_version=source_version,
                    target_version=target_version,
                    status="dispatch_failed",
                    failed_stage="workflow_dispatch",
                    summary="workflow dispatch failed before a run was identified",
                    allow_major=allow_major,
                )
            )
            continue

        run_id = run["id"]
        evidence_entry = {
            "project_id": project["project_id"],
            "run_id": run_id,
            "run_url": run["html_url"],
            "artifact_name": None,
            "artifact_sha256": None,
            "dispatched_at": dispatched_at,
            "completed_at": None,
            "result_sha256": None,
            "final_status": None,
            "failure_stage": None,
        }
        evidence_entries.append(evidence_entry)
        try:
            completed_run = client.wait_for_run(project, run_id)
            completed_at = completed_run.get("updated_at")
            _parse_utc_timestamp(completed_at)
            evidence_entry["completed_at"] = completed_at
        except Exception:
            evidence_entry["failure_stage"] = "run_poll"
            results.append(
                _operator_failure_result(
                    project,
                    campaign_id=campaign_id,
                    source_version=source_version,
                    target_version=target_version,
                    status="dispatch_failed",
                    failed_stage="run_poll",
                    summary="workflow run polling or bounded cancellation failed",
                    allow_major=allow_major,
                )
            )
            continue

        try:
            result, artifact = client.collect_result_artifact(
                project,
                run_id=run_id,
                campaign_id=campaign_id,
                target_version=target_version,
                expected_source_version=source_version,
                expected_pr_url=existing_pr_url,
            )
        except Exception:
            evidence_entry["failure_stage"] = "artifact_collection"
            results.append(
                _operator_failure_result(
                    project,
                    campaign_id=campaign_id,
                    source_version=source_version,
                    target_version=target_version,
                    status="dispatch_failed",
                    failed_stage="artifact_collection",
                    summary="workflow result artifact collection or validation failed",
                    allow_major=allow_major,
                )
            )
            continue

        results.append(result)
        evidence_entry.update(
            {
                "artifact_name": artifact["artifact_name"],
                "artifact_sha256": artifact["artifact_sha256"],
                "result_sha256": artifact["result_sha256"],
                "final_status": result["status"],
                "failure_stage": None,
            }
        )

    batch = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "target_version": target_version,
        "results": results,
    }
    validate_batch(batch, registry=registry)
    evidence = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "target_version": target_version,
        "operator_commit": _resolve_operator_commit(operator_commit),
        "generated_at": _utc_timestamp(client.now()),
        "entries": evidence_entries,
    }
    if EVIDENCE_SCHEMA.is_file():
        _validate(evidence, EVIDENCE_SCHEMA, "evidence")
    return batch, evidence


def validate_evidence(
    evidence: Mapping[str, Any],
    *,
    batch: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> None:
    _validate(evidence, EVIDENCE_SCHEMA, "evidence")
    if evidence["campaign_id"] != batch["campaign_id"]:
        raise CampaignError("evidence campaign_id does not match batch")
    if evidence["target_version"] != batch["target_version"]:
        raise CampaignError("evidence target_version does not match batch")
    results = {item["project_id"]: item for item in batch["results"]}
    projects = {item["project_id"]: item for item in registry["projects"]}
    ids = [item["project_id"] for item in evidence["entries"]]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise CampaignError("evidence entries must have unique sorted project IDs")
    run_ids = [item["run_id"] for item in evidence["entries"]]
    if len(run_ids) != len(set(run_ids)):
        raise CampaignError("evidence entries must have unique workflow run IDs")
    generated_at = _parse_utc_timestamp(evidence["generated_at"])
    for entry in evidence["entries"]:
        project_id = entry["project_id"]
        project = projects.get(project_id)
        result = results.get(project_id)
        if project is None or result is None:
            raise CampaignError("evidence project is absent from registry or batch")
        expected_url = (
            f"https://github.com/{project['repository']}/actions/runs/{entry['run_id']}"
        )
        if entry["run_url"].casefold() != expected_url.casefold():
            raise CampaignError("evidence run URL does not match registry repository")
        dispatched_at = _parse_utc_timestamp(entry["dispatched_at"])
        completed_at = (
            _parse_utc_timestamp(entry["completed_at"])
            if entry["completed_at"] is not None
            else None
        )
        if dispatched_at > generated_at + 5 or (
            completed_at is not None
            and (
                completed_at < dispatched_at - 5
                or completed_at > generated_at + 5
            )
        ):
            raise CampaignError("evidence timestamps are not chronologically valid")
        if entry["failure_stage"] is None:
            if entry["final_status"] != result["status"]:
                raise CampaignError("evidence final status does not match result")
        else:
            if result["status"] != "dispatch_failed":
                raise CampaignError("failed evidence must map to dispatch_failed result")
            if entry["failure_stage"] != result["failed_stage"]:
                raise CampaignError(
                    "evidence failure stage does not match batch result"
                )
            retained_collection_fields = (
                "artifact_name",
                "artifact_sha256",
                "result_sha256",
                "final_status",
            )
            if any(entry[field] is not None for field in retained_collection_fields):
                raise CampaignError(
                    "failed evidence must not retain artifact or final-result fields"
                )
            if entry["failure_stage"] == "run_poll" and completed_at is not None:
                raise CampaignError(
                    "run_poll evidence must not record a completion timestamp"
                )


def _sanitize_evidence(
    evidence: Mapping[str, Any],
    secrets: Sequence[str],
    *,
    batch: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    structural = (
        "schema_version", "campaign_id", "target_version", "operator_commit",
        "generated_at", "entries",
        "project_id", "run_id", "run_url", "artifact_name", "artifact_sha256",
        "dispatched_at", "completed_at", "result_sha256", "final_status",
        "failure_stage",
    )
    if any(redact(field, secrets=secrets) != field for field in structural):
        raise CampaignError("a secret overlaps an evidence structural field name")
    sanitized = _redacted(dict(evidence), secrets)
    if sanitized != evidence:
        # Evidence fields are all structural facts. Any mutation would break
        # correlation or conceal a token leak, so fail rather than rewrite.
        raise CampaignError("a secret overlaps campaign evidence")
    validate_evidence(sanitized, batch=batch, registry=registry)
    return sanitized


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


def _write_artifact_set(artifacts: Sequence[tuple[Path, str]]) -> None:
    """Publish related artifacts together, removing the whole set on failure.

    Every artifact is staged in its destination directory before publishing
    begins.  Cross-directory replacement cannot be made transactionally, so a
    failed replacement is handled fail-closed: every destination in this set
    (including a pre-existing artifact from an earlier invocation) is removed
    rather than leaving a mixture of old and new campaign evidence.

    A single artifact retains the established ``_write`` behavior.
    """

    if not artifacts:
        return
    if len(artifacts) == 1:
        path, content = artifacts[0]
        _write(path, content)
        return

    staged: list[tuple[Path, Path]] = []
    publishing_started = False
    try:
        for path, content in artifacts:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                staged.append((temporary, path))
                handle.write(content)

        publishing_started = True
        for temporary, path in staged:
            os.replace(temporary, path)
    except OSError as exc:
        if publishing_started:
            for path, _ in artifacts:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    # Continue removing the other members of the artifact set.
                    pass
        raise CampaignError("cannot write output artifact set") from exc
    finally:
        for temporary, _ in staged:
            try:
                temporary.unlink(missing_ok=True)
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
    artifacts = []
    if args.json_output:
        artifacts.append((Path(args.json_output), json_output))
    if args.markdown_output:
        artifacts.append((Path(args.markdown_output), markdown_output))
    _write_artifact_set(artifacts)
    if not args.json_output and not args.markdown_output:
        print(markdown_output, end="")


def command_dispatch(args: argparse.Namespace, secrets: Sequence[str]) -> None:
    _validate_plan_inputs(
        target_version=args.target_version,
        campaign_id=args.campaign_id,
        channel=args.channel,
    )
    _reject_output_path_collisions(
        (args.json_output, args.markdown_output, args.evidence_output),
        (args.registry,),
    )
    token = os.environ.get("BASE_UPGRADE_GITHUB_TOKEN", "")
    if not token:
        raise CampaignError("BASE_UPGRADE_GITHUB_TOKEN is required for dispatch")
    registry = load_registry(Path(args.registry))
    client = GitHubDispatchClient(token)
    batch, evidence = dispatch_campaign(
        registry,
        target_version=args.target_version,
        campaign_id=args.campaign_id,
        channel=args.channel,
        allow_major=args.allow_major,
        client=client,
        operator_commit=os.environ.get("BASE_UPGRADE_OPERATOR_COMMIT"),
    )
    sanitized_batch = _sanitize_batch(batch, secrets, registry=registry)
    sanitized_evidence = _sanitize_evidence(
        evidence, secrets, batch=sanitized_batch, registry=registry
    )
    json_output = _validated_json_text(
        sanitized_batch, registry=registry, secrets=secrets
    )
    markdown_output = render_markdown(sanitized_batch)
    evidence_output = _json_text(sanitized_evidence)
    if any(secret and secret in markdown_output for secret in secrets):
        raise CampaignError("a secret overlaps the generated Markdown artifact")
    if any(secret and secret in evidence_output for secret in secrets):
        raise CampaignError("a secret overlaps the generated evidence artifact")
    _write_artifact_set(
        [
            (Path(args.json_output), json_output),
            (Path(args.markdown_output), markdown_output),
            (Path(args.evidence_output), evidence_output),
        ]
    )


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

    dispatch_parser = subparsers.add_parser(
        "dispatch", help="dispatch and collect one bounded receiver campaign"
    )
    dispatch_parser.add_argument("--registry", required=True)
    dispatch_parser.add_argument("--target-version", required=True)
    dispatch_parser.add_argument("--campaign-id", required=True)
    dispatch_parser.add_argument("--channel", default="stable")
    dispatch_parser.add_argument("--allow-major", action="store_true")
    dispatch_parser.add_argument("--json-output", required=True)
    dispatch_parser.add_argument("--markdown-output", required=True)
    dispatch_parser.add_argument("--evidence-output", required=True)
    dispatch_parser.set_defaults(handler=command_dispatch)
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
