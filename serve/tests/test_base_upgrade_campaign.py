import base64
import importlib.util
import json
import os
import subprocess
import sys
from copy import deepcopy
from email.message import Message
from http.client import BadStatusLine, IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "base-upgrade-campaign.py"
SCHEMAS = ROOT / "scripts" / "schemas"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
REGISTRY = FIXTURES / "base-downstream-registry.json"
PROJECT_STATES = FIXTURES / "base-project-states.json"
RESULTS = FIXTURES / "base-upgrade-results.json"

sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("base_upgrade_campaign", SCRIPT)
campaign = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(campaign)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_cli(*args, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def project(project_id="fixture_project", repository="fixture-owner/fixture-repo"):
    return {
        "project_id": project_id,
        "repository": repository,
        "default_branch": "main",
        "enabled": True,
        "channel": "stable",
        "provider": "github",
    }


def registry(*projects):
    return {"schema_version": 1, "projects": list(projects)}


def result(**updates):
    value = {
        "campaign_id": "campaign-001",
        "project_id": "fixture_project",
        "source_version": "3.1.0",
        "target_version": "3.2.0",
        "status": "planned",
        "branch": None,
        "pr_url": None,
        "failed_stage": None,
        "conflict_files": [],
        "verification_summary": "one release manifest",
        "retry_command": "retry campaign",
        "rollback_command": "no action required",
    }
    value.update(updates)
    return value


class Response:
    def __init__(self, body):
        self.body = body
        self.closed = False
        self.read_size = None

    def read(self, size):
        self.read_size = size
        return self.body[:size]

    def close(self):
        self.closed = True


def contents_response(text):
    encoded = base64.b64encode(text.encode()).decode()
    return Response(
        json.dumps({"type": "file", "encoding": "base64", "content": encoded}).encode()
    )


def http_error(status, headers=None):
    values = Message()
    for key, value in (headers or {}).items():
        values[key] = value
    return HTTPError("https://api.github.com/fixture", status, "failure", values, None)


def test_batch_schema_is_valid_and_resolves_single_result_schema():
    schema = load_json(SCHEMAS / "base-upgrade-batch.schema.json")
    Draft7Validator.check_schema(schema)
    validator = campaign._validator(SCHEMAS / "base-upgrade-batch.schema.json")
    batch = {
        "schema_version": 1,
        "campaign_id": "campaign-001",
        "target_version": "3.2.0",
        "results": [result()],
    }
    assert list(validator.iter_errors(batch)) == []
    batch["results"][0]["token"] = "fixture-secret"
    assert list(validator.iter_errors(batch))
    batch["results"] = []
    assert any(error.validator == "minItems" for error in validator.iter_errors(batch))


def test_registry_fixture_validates_and_runtime_rejects_duplicate_ids(tmp_path):
    loaded = campaign.load_registry(REGISTRY)
    assert len(loaded["projects"]) == 7
    duplicate = registry(project("same", "owner/repo-a"), project("same", "owner/repo-b"))
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(campaign.CampaignError, match="duplicate registry project_id"):
        campaign.load_registry(path)


def test_registry_repositories_are_unique_after_case_normalization(tmp_path):
    duplicate = registry(
        project("project_a", "Fixture-Owner/Fixture-Repo"),
        project("project_b", "fixture-owner/fixture-repo"),
    )
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(campaign.CampaignError, match="case normalization"):
        campaign.load_registry(path)


def test_validate_registry_rejects_unknown_secret_field_without_printing_value(tmp_path):
    document = registry(project())
    document["projects"][0]["token"] = "secret-value-that-must-not-appear"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    completed = run_cli("validate-registry", "--registry", str(path))
    assert completed.returncode == 2
    assert "secret-value-that-must-not-appear" not in completed.stderr


def test_dry_run_plan_covers_versions_failures_filters_and_is_deterministic():
    args = (
        "plan",
        "--registry",
        str(REGISTRY),
        "--project-state-fixture",
        str(PROJECT_STATES),
        "--target-version",
        "3.2.0",
        "--channel",
        "stable",
        "--dry-run",
    )
    first = run_cli(*args)
    second = run_cli(*args)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    batch = json.loads(first.stdout)
    assert batch["campaign_id"] == "plan-v3.2.0"
    assert [item["project_id"] for item in batch["results"]] == sorted(
        item["project_id"] for item in batch["results"]
    )
    statuses = {item["project_id"]: item["status"] for item in batch["results"]}
    assert statuses == {
        "fixture_alpha_current": "up_to_date",
        "fixture_bravo_one_release": "planned",
        "fixture_charlie_multi_release": "blocked",
        "fixture_delta_missing_ledger": "blocked",
        "fixture_echo_invalid_version": "blocked",
    }
    assert "fixture_foxtrot_disabled" not in statuses
    assert "fixture_golf_preview" not in statuses
    blocked = [item for item in batch["results"] if item["status"] == "blocked"]
    completed = [item for item in batch["results"] if item["status"] != "blocked"]
    assert all(item["retry_command"].startswith("rerun") for item in blocked)
    assert all(item["retry_command"].startswith("no retry required") for item in completed)
    assert all(
        item["rollback_command"]
        == "no rollback required; read-only campaign planning created no changes"
        for item in batch["results"]
    )


def test_cross_major_requires_explicit_approval():
    args = (
        "plan",
        "--registry",
        str(REGISTRY),
        "--project-state-fixture",
        str(PROJECT_STATES),
        "--target-version",
        "3.2.0",
        "--dry-run",
        "--allow-major",
    )
    completed = run_cli(*args)
    assert completed.returncode == 0, completed.stderr
    results = {
        item["project_id"]: item for item in json.loads(completed.stdout)["results"]
    }
    assert results["fixture_charlie_multi_release"]["status"] == "planned"


def test_live_mode_requires_campaign_id_and_fixture_is_dry_run_only():
    missing = run_cli(
        "plan", "--registry", str(REGISTRY), "--target-version", "3.2.0"
    )
    assert missing.returncode == 2
    assert "--campaign-id is required" in missing.stderr
    forbidden = run_cli(
        "plan",
        "--registry",
        str(REGISTRY),
        "--target-version",
        "3.2.0",
        "--campaign-id",
        "campaign-001",
        "--project-state-fixture",
        str(PROJECT_STATES),
    )
    assert forbidden.returncode == 2
    assert "only allowed with --dry-run" in forbidden.stderr


def test_target_must_have_a_release_manifest():
    completed = run_cli(
        "plan",
        "--registry",
        str(REGISTRY),
        "--target-version",
        "9.9.9",
        "--project-state-fixture",
        str(PROJECT_STATES),
        "--dry-run",
    )
    assert completed.returncode == 2
    assert "missing target release manifest" in completed.stderr


def test_one_live_discovery_failure_does_not_stop_other_projects():
    document = registry(
        project("project_a", "owner/repo-a"), project("project_b", "owner/repo-b")
    )

    def reader(item, *, token):
        if item["project_id"] == "project_a":
            raise campaign.CampaignError("provider failed")
        return "BASE_UPSTREAM_VERSION=3.1.0\n"

    batch = campaign.plan_campaign(
        document,
        target_version="3.2.0",
        campaign_id="campaign-001",
        project_reader=reader,
    )
    assert [(item["project_id"], item["status"]) for item in batch["results"]] == [
        ("project_a", "blocked"),
        ("project_b", "planned"),
    ]


@pytest.mark.parametrize(
    "failure",
    [campaign.CampaignError("provider failed"), RuntimeError("unexpected provider bug")],
)
def test_every_project_reader_exception_is_isolated(failure):
    document = registry(
        project("project_a", "owner/repo-a"), project("project_b", "owner/repo-b")
    )

    def reader(item, *, token):
        if item["project_id"] == "project_a":
            raise failure
        return "BASE_UPSTREAM_VERSION=3.1.0\n"

    batch = campaign.plan_campaign(
        document,
        target_version="3.2.0",
        campaign_id="campaign-001",
        project_reader=reader,
    )
    assert [item["status"] for item in batch["results"]] == ["blocked", "planned"]


def test_contents_api_uses_default_branch_token_and_15_second_timeout():
    captured = {}
    response = contents_response("BASE_UPSTREAM_VERSION=3.1.0\n")

    def opener(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return response

    text = campaign.read_github_project_md(
        project(repository="Fixture-Owner/Fixture-Repo"),
        token="fixture-token",
        http_open=opener,
    )
    assert text == "BASE_UPSTREAM_VERSION=3.1.0\n"
    assert captured["timeout"] == 15
    assert captured["request"].get_header("Authorization") == "Bearer fixture-token"
    assert captured["request"].get_header("X-github-api-version") == "2026-03-10"
    assert captured["request"].full_url.endswith("contents/PROJECT.md?ref=main")
    assert response.read_size == campaign.MAX_PROVIDER_RESPONSE_BYTES + 1
    assert response.closed is True


@pytest.mark.parametrize(
    "body",
    [
        json.dumps([]).encode(),
        json.dumps({"type": "file", "encoding": "base64", "content": 1}).encode(),
        json.dumps({"type": "file", "encoding": "base64", "content": "***"}).encode(),
        json.dumps(
            {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(b"\xff").decode(),
            }
        ).encode(),
    ],
)
def test_provider_malformed_shapes_base64_and_utf8_are_campaign_errors(body):
    with pytest.raises(campaign.CampaignError, match="invalid GitHub"):
        campaign.read_github_project_md(
            project(), token=None, http_open=lambda request, timeout: Response(body)
        )


def test_provider_unexpected_transport_failure_is_a_campaign_error():
    def opener(request, *, timeout):
        raise RuntimeError("transport implementation failed")

    with pytest.raises(campaign.CampaignError, match="unexpected failure"):
        campaign.read_github_project_md(project(), token=None, http_open=opener)


def test_provider_raw_and_decoded_size_limits_are_enforced():
    oversized_raw = b"x" * (campaign.MAX_PROVIDER_RESPONSE_BYTES + 1)
    with pytest.raises(campaign.CampaignError, match="size limit"):
        campaign.read_github_project_md(
            project(),
            token=None,
            http_open=lambda request, timeout: Response(oversized_raw),
        )

    oversized_project = b"x" * (campaign.MAX_PROJECT_MD_BYTES + 1)
    payload = json.dumps(
        {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(oversized_project).decode(),
        }
    ).encode()
    assert len(payload) < campaign.MAX_PROVIDER_RESPONSE_BYTES
    with pytest.raises(campaign.CampaignError, match="invalid GitHub"):
        campaign.read_github_project_md(
            project(),
            token=None,
            http_open=lambda request, timeout: Response(payload),
        )


@pytest.mark.parametrize(
    ("first_error", "expected_wait"),
    [
        (http_error(500), 1.0),
        (http_error(429, {"Retry-After": "7"}), 7.0),
        (http_error(403, {"X-RateLimit-Reset": "120"}), 20.0),
        (URLError("temporary fixture failure"), 1.0),
    ],
)
def test_provider_retries_are_bounded_and_header_driven(first_error, expected_wait):
    attempts = []
    waits = []

    def opener(request, *, timeout):
        attempts.append(timeout)
        if len(attempts) == 1:
            raise first_error
        return contents_response("BASE_UPSTREAM_VERSION=3.1.0\n")

    campaign.read_github_project_md(
        project(),
        token=None,
        http_open=opener,
        sleep=waits.append,
        now=lambda: 100.0,
    )
    assert attempts == [15, 15]
    assert waits == [expected_wait]
    if isinstance(first_error, HTTPError):
        assert first_error.closed is True


@pytest.mark.parametrize(
    "failure",
    [IncompleteRead(b"partial response"), BadStatusLine("truncated status line")],
)
def test_provider_http_protocol_failures_retry_then_succeed(failure):
    attempts = []
    waits = []
    interrupted_responses = []

    class InterruptedResponse:
        def __init__(self):
            self.closed = False

        def read(self, size):
            raise failure

        def close(self):
            self.closed = True

    def opener(request, *, timeout):
        attempts.append(timeout)
        if len(attempts) == 1:
            response = InterruptedResponse()
            interrupted_responses.append(response)
            return response
        return contents_response("BASE_UPSTREAM_VERSION=3.1.0\n")

    text = campaign.read_github_project_md(
        project(), token=None, http_open=opener, sleep=waits.append
    )
    assert text == "BASE_UPSTREAM_VERSION=3.1.0\n"
    assert attempts == [15, 15]
    assert waits == [1.0]
    assert interrupted_responses[0].closed is True


def test_provider_http_protocol_failures_stop_after_three_attempts():
    attempts = []
    waits = []

    class InterruptedResponse:
        def read(self, size):
            raise IncompleteRead(b"partial response")

        def close(self):
            pass

    def opener(request, *, timeout):
        attempts.append(timeout)
        return InterruptedResponse()

    with pytest.raises(campaign.CampaignError, match="network failure"):
        campaign.read_github_project_md(
            project(), token=None, http_open=opener, sleep=waits.append
        )
    assert attempts == [15, 15, 15]
    assert waits == [1.0, 2.0]


def test_provider_closes_every_http_error_before_retry_decision():
    attempts = []
    waits = []
    errors = []

    class ClearingHTTPError(HTTPError):
        def close(self):
            super().close()
            self.headers.clear()

    def opener(request, *, timeout):
        attempts.append(timeout)
        headers = Message()
        headers["Retry-After"] = "7"
        error = ClearingHTTPError(
            "https://api.github.com/fixture", 503, "failure", headers, None
        )
        errors.append(error)
        raise error

    with pytest.raises(campaign.CampaignError, match="HTTP 503"):
        campaign.read_github_project_md(
            project(), token=None, http_open=opener, sleep=waits.append
        )
    assert attempts == [15, 15, 15]
    assert waits == [7.0, 7.0]
    assert all(error.closed for error in errors)


@pytest.mark.parametrize("status", [400, 401, 404, 403, 429])
def test_provider_does_not_retry_ordinary_4xx_or_unheaded_rate_limits(status):
    attempts = []

    def opener(request, *, timeout):
        attempts.append(timeout)
        raise http_error(status)

    with pytest.raises(campaign.CampaignError, match=f"HTTP {status}"):
        campaign.read_github_project_md(
            project(), token=None, http_open=opener, sleep=lambda seconds: None
        )
    assert attempts == [15]


def test_provider_network_retries_stop_after_three_attempts():
    attempts = []
    waits = []

    def opener(request, *, timeout):
        attempts.append(timeout)
        raise URLError("fixture-token-must-not-be-forwarded")

    with pytest.raises(campaign.CampaignError, match="network failure"):
        campaign.read_github_project_md(
            project(), token=None, http_open=opener, sleep=waits.append
        )
    assert attempts == [15, 15, 15]
    assert waits == [1.0, 2.0]


def test_provider_retry_attempt_override_cannot_exceed_production_cap():
    attempts = []

    def opener(request, *, timeout):
        attempts.append(timeout)
        raise URLError("temporary fixture failure")

    with pytest.raises(campaign.CampaignError, match="network failure"):
        campaign.read_github_project_md(
            project(),
            token=None,
            http_open=opener,
            sleep=lambda seconds: None,
            max_attempts=1000,
        )
    assert attempts == [15, 15, 15]

    for invalid in (0, -1, True, 1.5):
        with pytest.raises(campaign.CampaignError, match="positive integer"):
            campaign.read_github_project_md(
                project(), token=None, http_open=opener, max_attempts=invalid
            )


def test_provider_retry_headers_are_finite_and_capped():
    assert campaign._retry_delay({"Retry-After": "999999999999"}) == 60.0
    assert campaign._retry_delay({"Retry-After": "9" * 10000}) == 60.0
    assert campaign._retry_delay({"X-RateLimit-Reset": "inf"}) is None
    assert campaign._retry_delay({"X-RateLimit-Reset": "nan"}) is None
    assert campaign._retry_delay({"Retry-After": "not-a-date"}) is None


def test_summarize_produces_stable_json_and_markdown_artifacts(tmp_path):
    json_output = tmp_path / "batch.json"
    markdown_output = tmp_path / "summary.md"
    completed = run_cli(
        "summarize",
        "--results",
        str(RESULTS),
        "--registry",
        str(REGISTRY),
        "--json-output",
        str(json_output),
        "--markdown-output",
        str(markdown_output),
    )
    assert completed.returncode == 0, completed.stderr
    batch = load_json(json_output)
    assert [item["project_id"] for item in batch["results"]] == sorted(
        item["project_id"] for item in batch["results"]
    )
    markdown = markdown_output.read_text(encoding="utf-8")
    assert "# Base upgrade campaign `fixture-campaign-001`" in markdown
    assert "conflict=1, pr_opened=1, up_to_date=1" in markdown


def test_markdown_summary_neutralizes_html_backticks_pipes_and_links():
    item = result(
        status="blocked",
        failed_stage="<img src=x>|`code`![track](https://example.test)",
        verification_summary="blocked",
    )
    batch = {
        "schema_version": 1,
        "campaign_id": "campaign-001",
        "target_version": "3.2.0",
        "results": [item],
    }
    campaign.validate_batch(batch)
    markdown = campaign.render_markdown(batch)
    assert "<img" not in markdown
    assert "`code`" not in markdown
    assert "![track]" not in markdown
    assert "&#x3C;img" in markdown
    assert "&#x7C;" in markdown
    assert "https://" not in markdown
    assert "&#x3A;" in markdown
    assert "&#x5F;" in markdown


def test_batch_rejects_mixed_campaign_target_and_duplicate_projects():
    for mutation, message in [
        (
            lambda items: items.append(result(project_id="other", campaign_id="campaign-2")),
            "different campaign_id",
        ),
        (
            lambda items: items.append(result(project_id="other", target_version="3.1.0")),
            "different target_version",
        ),
        (lambda items: items.append(deepcopy(items[0])), "duplicate project_id"),
    ]:
        items = [result()]
        mutation(items)
        batch = {
            "schema_version": 1,
            "campaign_id": "campaign-001",
            "target_version": "3.2.0",
            "results": sorted(items, key=lambda item: item["project_id"]),
        }
        with pytest.raises(campaign.CampaignError, match=message):
            campaign.validate_batch(batch)


def test_batch_rejects_up_to_date_and_branch_target_inconsistency():
    up_to_date = result(
        status="up_to_date",
        source_version="3.1.0",
        verification_summary="already current",
    )
    branch_mismatch = result(
        status="pr_opened",
        branch="chore/base-v3.1.0",
        pr_url="https://github.com/fixture-owner/fixture-repo/pull/1",
        verification_summary="tests PASS",
    )
    for item, message in [
        (up_to_date, "matching versions"),
        (branch_mismatch, "branch version"),
    ]:
        batch = {
            "schema_version": 1,
            "campaign_id": "campaign-001",
            "target_version": "3.2.0",
            "results": [item],
        }
        with pytest.raises(campaign.CampaignError, match=message):
            campaign.validate_batch(batch)


def test_summarize_registry_association_checks_project_and_pr_repository():
    batch = {
        "schema_version": 1,
        "campaign_id": "campaign-001",
        "target_version": "3.2.0",
        "results": [
            result(
                status="pr_opened",
                branch="chore/base-v3.2.0",
                pr_url="https://github.com/other-owner/other-repo/pull/1",
                verification_summary="tests PASS",
            )
        ],
    }
    with pytest.raises(campaign.CampaignError, match="does not match registry"):
        campaign.validate_batch(batch, registry=registry(project()))
    batch["results"][0]["project_id"] = "missing_project"
    with pytest.raises(campaign.CampaignError, match="absent from registry"):
        campaign.validate_batch(batch, registry=registry(project()))


def test_all_output_paths_apply_shared_redaction(monkeypatch, tmp_path):
    secret = "fixture-super-secret-token"
    document = load_json(RESULTS)
    document[0]["verification_summary"] = f"provider said {secret}"
    results = tmp_path / "results.json"
    results.write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / "batch.json"
    environment = dict(os.environ)
    environment["BASE_UPGRADE_GITHUB_TOKEN"] = secret
    completed = run_cli(
        "summarize",
        "--results",
        str(results),
        "--json-output",
        str(output),
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    assert secret not in output.read_text(encoding="utf-8")
    assert secret not in completed.stdout + completed.stderr
    campaign.validate_batch(load_json(output))


def test_secret_overlap_with_structural_fields_fails_without_invalid_artifact(tmp_path):
    output = tmp_path / "batch.json"
    environment = dict(os.environ)
    environment["BASE_UPGRADE_GITHUB_TOKEN"] = "3.2.0"
    completed = run_cli(
        "plan",
        "--registry",
        str(REGISTRY),
        "--project-state-fixture",
        str(PROJECT_STATES),
        "--target-version",
        "3.2.0",
        "--dry-run",
        "--output",
        str(output),
        env=environment,
    )
    assert completed.returncode == 2
    assert not output.exists()
    assert "3.2.0" not in completed.stdout + completed.stderr


@pytest.mark.parametrize("secret", ["status", "null"])
def test_secret_overlap_with_artifact_structure_fails_closed(tmp_path, secret):
    output = tmp_path / "batch.json"
    environment = dict(os.environ)
    environment["BASE_UPGRADE_GITHUB_TOKEN"] = secret
    completed = run_cli(
        "plan",
        "--registry",
        str(REGISTRY),
        "--project-state-fixture",
        str(PROJECT_STATES),
        "--target-version",
        "3.2.0",
        "--dry-run",
        "--output",
        str(output),
        env=environment,
    )
    assert completed.returncode == 2
    assert not output.exists()
    assert secret not in completed.stdout + completed.stderr


def test_invalid_campaign_channel_and_empty_selection_fail_before_provider_call():
    calls = []

    def reader(item, *, token):
        calls.append(item["project_id"])
        return "BASE_UPSTREAM_VERSION=3.1.0\n"

    document = registry(project())
    for campaign_id, channel, message in [
        ("bad id", "stable", "campaign ID"),
        ("campaign-001", "Bad", "channel is invalid"),
        ("campaign-001", "preview", "no enabled projects"),
    ]:
        with pytest.raises(campaign.CampaignError, match=message):
            campaign.plan_campaign(
                document,
                target_version="3.2.0",
                campaign_id=campaign_id,
                channel=channel,
                project_reader=reader,
            )
    assert calls == []


def test_overlong_source_isolated_and_overlong_target_rejected_before_provider():
    document = registry(
        project("project_a", "owner/repo-a"), project("project_b", "owner/repo-b")
    )

    def reader(item, *, token):
        if item["project_id"] == "project_a":
            return f"BASE_UPSTREAM_VERSION={'9' * 5000}.1.0\n"
        return "BASE_UPSTREAM_VERSION=3.1.0\n"

    batch = campaign.plan_campaign(
        document,
        target_version="3.2.0",
        campaign_id="campaign-001",
        project_reader=reader,
    )
    assert [item["status"] for item in batch["results"]] == ["blocked", "planned"]

    calls = []
    with pytest.raises(campaign.CampaignError, match="canonical core SemVer"):
        campaign.plan_campaign(
            registry(project()),
            target_version=f"{'9' * 5000}.1.0",
            campaign_id="campaign-001",
            project_reader=lambda *args, **kwargs: calls.append(True),
        )
    assert calls == []


def test_summarize_rejects_colliding_output_paths(tmp_path):
    output = tmp_path / "artifact"
    completed = run_cli(
        "summarize",
        "--results",
        str(RESULTS),
        "--json-output",
        str(output),
        "--markdown-output",
        str(output),
    )
    assert completed.returncode == 2
    assert not output.exists()

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_text("existing", encoding="utf-8")
    os.link(first, second)
    completed = run_cli(
        "summarize",
        "--results",
        str(RESULTS),
        "--json-output",
        str(first),
        "--markdown-output",
        str(second),
    )
    assert completed.returncode == 2
    assert first.read_text(encoding="utf-8") == "existing"


def test_output_paths_cannot_overwrite_inputs(tmp_path):
    results = tmp_path / "results.json"
    results.write_text(RESULTS.read_text(encoding="utf-8"), encoding="utf-8")
    before = results.read_text(encoding="utf-8")
    completed = run_cli(
        "summarize",
        "--results",
        str(results),
        "--json-output",
        str(results),
    )
    assert completed.returncode == 2
    assert results.read_text(encoding="utf-8") == before

    registry_path = tmp_path / "registry.json"
    registry_path.write_text(REGISTRY.read_text(encoding="utf-8"), encoding="utf-8")
    before = registry_path.read_text(encoding="utf-8")
    completed = run_cli(
        "plan",
        "--registry",
        str(registry_path),
        "--project-state-fixture",
        str(PROJECT_STATES),
        "--target-version",
        "3.2.0",
        "--dry-run",
        "--output",
        str(registry_path),
    )
    assert completed.returncode == 2
    assert registry_path.read_text(encoding="utf-8") == before
