import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import warnings
import zipfile
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "base-upgrade-campaign.py"
SCHEMAS = ROOT / "scripts" / "schemas"
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("base_upgrade_campaign_dispatch", SCRIPT)
campaign = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(campaign)


def project(project_id="fixture_alpha", repository="fixture-owner/fixture-alpha"):
    return {
        "project_id": project_id,
        "repository": repository,
        "default_branch": "main",
        "enabled": True,
        "channel": "stable",
        "provider": "github",
    }


def registry(*items):
    return {"schema_version": 1, "projects": list(items)}


def receiver_result(item, campaign_id, status="pr_opened"):
    is_pr = status == "pr_opened"
    return {
        "campaign_id": campaign_id,
        "project_id": item["project_id"],
        "source_version": "3.2.0",
        "target_version": "3.3.0",
        "status": status,
        "branch": "chore/base-v3.3.0" if is_pr else None,
        "pr_url": f"https://github.com/{item['repository']}/pull/7" if is_pr else None,
        "failed_stage": None if is_pr else "verification",
        "conflict_files": [],
        "verification_summary": "receiver checks PASS" if is_pr else "receiver checks failed",
        "retry_command": "rerun receiver",
        "rollback_command": "close only resources created by this run",
    }


def run_record(item, run_id):
    return {
        "id": run_id,
        "html_url": f"https://github.com/{item['repository']}/actions/runs/{run_id}",
        "url": f"https://api.github.com/repos/{item['repository']}/actions/runs/{run_id}",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": "a" * 40,
        "workflow_id": 91,
        "path": ".github/workflows/base-upgrade-receiver.yml",
        "repository": {"full_name": item["repository"]},
        "display_title": "Base upgrade campaign-001 to v3.3.0",
        "created_at": "2026-08-18T12:00:00Z",
        "updated_at": "2026-08-18T12:01:00Z",
        "status": "completed",
        "conclusion": "success",
    }


class FleetClient:
    def __init__(self, statuses, *, poll_failure=None):
        self.statuses = statuses
        self.poll_failure = poll_failure
        self.now_value = 1787054520.0
        self.open_pr_queries = []
        self.dispatches = []

    def now(self):
        return self.now_value

    def contents_text(self, item, ref):
        version = "3.3.0" if self.statuses[item["project_id"]] == "up_to_date" else "3.2.0"
        return (
            "BASE_UPSTREAM_REPOSITORY=fixture-owner/base\n"
            f"BASE_UPSTREAM_VERSION={version}\n"
        )

    def open_upgrade_prs(self, item, target):
        self.open_pr_queries.append((item["project_id"], target))
        if self.statuses[item["project_id"]] == "pr_opened":
            return [{"html_url": f"https://github.com/{item['repository']}/pull/7"}]
        return []

    def verify_existing_branch(self, item, target, base_repository):
        assert base_repository == "fixture-owner/base"
        return False

    def dispatch_receiver(self, item, **kwargs):
        self.dispatches.append((item["project_id"], kwargs["campaign_id"]))
        run_id = 100 + len(self.dispatches)
        run = run_record(item, run_id)
        run["display_title"] = f"Base upgrade {kwargs['campaign_id']} to v3.3.0"
        return run, "2026-08-18T12:00:00Z"

    def wait_for_run(self, item, run_id):
        if self.poll_failure == item["project_id"]:
            raise campaign.CampaignError("fixture poll failed")
        return run_record(item, run_id)

    def collect_result_artifact(self, item, **kwargs):
        status = self.statuses[item["project_id"]]
        result = receiver_result(item, kwargs["campaign_id"], status=status)
        raw = json.dumps(result, sort_keys=True).encode()
        return result, {
            "artifact_name": f"base-upgrade-result-{kwargs['campaign_id']}",
            "artifact_sha256": "b" * 64,
            "result_sha256": hashlib.sha256(raw).hexdigest(),
        }


def test_dispatch_campaign_three_fixture_states_and_evidence_contract():
    alpha = project("fixture_alpha", "fixture-owner/fixture-alpha")
    beta = project("fixture_beta", "fixture-owner/fixture-beta")
    gamma = project("fixture_gamma", "fixture-owner/fixture-gamma")
    client = FleetClient(
        {
            "fixture_alpha": "pr_opened",
            "fixture_beta": "up_to_date",
            "fixture_gamma": "verification_failed",
        }
    )
    batch, evidence = campaign.dispatch_campaign(
        registry(alpha, beta, gamma),
        target_version="3.3.0",
        campaign_id="campaign-001",
        channel="stable",
        allow_major=False,
        client=client,
    )
    assert {item["project_id"]: item["status"] for item in batch["results"]} == {
        "fixture_alpha": "pr_opened",
        "fixture_beta": "up_to_date",
        "fixture_gamma": "verification_failed",
    }
    assert [entry["project_id"] for entry in evidence["entries"]] == [
        "fixture_alpha",
        "fixture_gamma",
    ]
    campaign.validate_evidence(evidence, batch=batch, registry=registry(alpha, beta, gamma))
    assert list(
        campaign._validator(SCHEMAS / "base-upgrade-evidence.schema.json").iter_errors(
            evidence
        )
    ) == []


def test_dispatch_isolates_poll_failure_and_keeps_run_locator_evidence():
    alpha = project("fixture_alpha", "fixture-owner/fixture-alpha")
    gamma = project("fixture_gamma", "fixture-owner/fixture-gamma")
    client = FleetClient(
        {"fixture_alpha": "pr_opened", "fixture_gamma": "verification_failed"},
        poll_failure="fixture_alpha",
    )
    batch, evidence = campaign.dispatch_campaign(
        registry(alpha, gamma),
        target_version="3.3.0",
        campaign_id="campaign-001",
        channel="stable",
        allow_major=False,
        client=client,
    )
    statuses = {item["project_id"]: item for item in batch["results"]}
    assert statuses["fixture_alpha"]["status"] == "dispatch_failed"
    assert statuses["fixture_alpha"]["failed_stage"] == "run_poll"
    assert statuses["fixture_gamma"]["status"] == "verification_failed"
    failed = next(item for item in evidence["entries"] if item["project_id"] == "fixture_alpha")
    assert failed["run_id"] > 0
    assert failed["failure_stage"] == "run_poll"
    assert failed["artifact_name"] is None


def test_serial_dispatch_rejects_four_selected_projects_before_provider_calls():
    items = tuple(
        project(f"fixture_{index}", f"fixture-owner/fixture-{index}")
        for index in range(4)
    )

    class ProviderMustNotRun:
        def contents_text(self, *_args, **_kwargs):
            raise AssertionError("provider was called before the campaign size gate")

    with pytest.raises(campaign.CampaignError, match="selects 4 projects.*limit is 3"):
        campaign.dispatch_campaign(
            registry(*items),
            target_version="3.3.0",
            campaign_id="campaign-001",
            channel="stable",
            allow_major=False,
            client=ProviderMustNotRun(),
        )


def test_cross_campaign_idempotence_is_keyed_by_project_and_target():
    item = project()
    client = FleetClient({item["project_id"]: "pr_opened"})
    for campaign_id in ("campaign-001", "campaign-002"):
        batch, _ = campaign.dispatch_campaign(
            registry(item),
            target_version="3.3.0",
            campaign_id=campaign_id,
            channel="stable",
            allow_major=False,
            client=client,
        )
        assert batch["results"][0]["pr_url"].endswith("/pull/7")
    assert client.open_pr_queries == [
        (item["project_id"], "3.3.0"),
        (item["project_id"], "3.3.0"),
    ]
    assert len({receiver_result(item, cid)["pr_url"] for cid in ("campaign-001", "campaign-002")}) == 1


class Response:
    def __init__(self, body=b"", status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.closed = False

    def read(self, size):
        return self.body[:size]

    def close(self):
        self.closed = True


def http_error(status, headers=None):
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return HTTPError("https://api.github.com/fixture", status, "fixture", message, None)


def test_dispatch_uses_2026_api_200_run_details_then_authoritative_get():
    item = project()
    calls = []
    run = run_record(item, 501)
    queue = [
        Response(
            json.dumps(
                {
                    "workflow_run_id": 501,
                    "run_url": f"https://api.github.com/repos/{item['repository']}/actions/runs/501",
                    "html_url": f"https://github.com/{item['repository']}/actions/runs/501",
                }
            ).encode()
        ),
        Response(json.dumps(run).encode()),
    ]

    def opener(request, *, timeout):
        calls.append(request)
        return queue.pop(0)

    client = campaign.GitHubDispatchClient("operator-token", http_open=opener)
    value, _ = client.dispatch_receiver(
        item, campaign_id="campaign-001", target_version="3.3.0", allow_major=False
    )
    assert value["id"] == 501
    post = calls[0]
    body = json.loads(post.data)
    assert post.method == "POST"
    assert "return_run_details" not in body
    assert body["ref"] == "main"
    assert post.get_header("X-github-api-version") == "2026-03-10"
    assert post.get_header("Authorization") == "Bearer operator-token"
    assert calls[1].method == "GET"


def test_response_loss_recovers_before_retry_and_post_count_is_at_most_two():
    item = project()
    run = run_record(item, 601)
    attempts = []
    waits = []
    responses = [
        URLError("lost response"),
        Response(json.dumps({"workflow_runs": []}).encode()),
        Response(json.dumps({"workflow_runs": []}).encode()),
        Response(json.dumps({"workflow_runs": []}).encode()),
        Response(
            json.dumps(
                {
                    "workflow_run_id": 601,
                    "run_url": f"https://api.github.com/repos/{item['repository']}/actions/runs/601",
                    "html_url": f"https://github.com/{item['repository']}/actions/runs/601",
                }
            ).encode()
        ),
        Response(json.dumps(run).encode()),
    ]

    def opener(request, *, timeout):
        attempts.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    client = campaign.GitHubDispatchClient(
        "operator-token", http_open=opener, sleep=waits.append, now=lambda: 1776513600.0
    )
    recovered, _ = client.dispatch_receiver(
        item, campaign_id="campaign-001", target_version="3.3.0", allow_major=False
    )
    assert recovered["id"] == 601
    assert sum(request.method == "POST" for request in attempts) == 2
    assert waits == [1.0, 2.0, 1.0]


def test_ordinary_403_is_not_retried_and_headered_429_is_bounded():
    item = project()
    for failure, expected_posts in [
        (http_error(403), 1),
        (http_error(429, {"Retry-After": "1"}), 2),
    ]:
        calls = []
        responses = [
            failure,
        ]
        if expected_posts == 2:
            responses.append(http_error(429, {"Retry-After": "1"}))

        def opener(request, *, timeout):
            calls.append(request)
            value = responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        client = campaign.GitHubDispatchClient(
            "operator-token", http_open=opener, sleep=lambda _: None
        )
        with pytest.raises(campaign.ProviderHTTPError):
            client.dispatch_receiver(
                item,
                campaign_id="campaign-001",
                target_version="3.3.0",
                allow_major=False,
            )
        assert sum(request.method == "POST" for request in calls) == expected_posts


def zip_bytes(name, content, *, duplicate=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr(name, content)
        if duplicate:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                bundle.writestr(name, content)
    return buffer.getvalue()


class ArtifactClient(campaign.GitHubDispatchClient):
    def __init__(
        self, item, archive, result_digest, *, artifact_digest=None, artifact_updates=None
    ):
        super().__init__("operator-token")
        self.item = item
        self.archive = archive
        self.result_digest = result_digest
        self.artifact_digest = artifact_digest or hashlib.sha256(archive).hexdigest()
        self.artifact_updates = artifact_updates or {}
        self.redirect_request_headers = None
        self.signed_request_headers = None

    def api_json(self, method, path, **kwargs):
        artifact = {
            "id": 77,
            "name": "base-upgrade-result-campaign-001",
            "expired": False,
            "digest": f"sha256:{self.artifact_digest}",
            "archive_download_url": f"https://api.github.com/repos/{self.item['repository']}/actions/artifacts/77/zip",
            "workflow_run": {"id": 501},
        }
        artifact.update(self.artifact_updates)
        return {
            "total_count": 1,
            "artifacts": [artifact],
        }

    def _artifact_redirect(self, archive_url):
        assert archive_url.endswith("/actions/artifacts/77/zip")
        return "https://objects.githubusercontent.com/result.zip?sig=fixture"

    def _download_signed_artifact(self, location):
        return self.archive


def test_artifact_digest_single_entry_terminal_result_and_association():
    item = project()
    result = receiver_result(item, "campaign-001")
    raw = json.dumps(result, separators=(",", ":")).encode()
    archive = zip_bytes("base-upgrade-result.json", raw)
    client = ArtifactClient(item, archive, hashlib.sha256(raw).hexdigest())
    value, evidence = client.collect_result_artifact(
        item,
        run_id=501,
        campaign_id="campaign-001",
        target_version="3.3.0",
        expected_source_version="3.2.0",
        expected_pr_url=result["pr_url"],
    )
    assert value == result
    assert evidence["artifact_sha256"] == hashlib.sha256(archive).hexdigest()
    assert evidence["result_sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "archive_builder, error",
    [
        (lambda raw: zip_bytes("../base-upgrade-result.json", raw), "unsafe"),
        (lambda raw: zip_bytes("base-upgrade-result.json", raw, duplicate=True), "exactly one"),
        (lambda raw: b"not-a-zip", "invalid"),
    ],
)
def test_artifact_rejects_traversal_multiple_entries_and_invalid_zip(archive_builder, error):
    item = project()
    raw = json.dumps(receiver_result(item, "campaign-001")).encode()
    archive = archive_builder(raw)
    client = ArtifactClient(item, archive, hashlib.sha256(raw).hexdigest())
    with pytest.raises(campaign.CampaignError, match=error):
        client.collect_result_artifact(
            item,
            run_id=501,
            campaign_id="campaign-001",
            target_version="3.3.0",
            expected_source_version="3.2.0",
            expected_pr_url=None,
        )


def test_artifact_rejects_digest_mismatch_duplicate_json_and_nonterminal_status():
    item = project()
    good = receiver_result(item, "campaign-001")
    cases = [
        (zip_bytes("base-upgrade-result.json", json.dumps(good).encode()), "0" * 64, "digest"),
        (zip_bytes("base-upgrade-result.json", b'{"campaign_id":"a","campaign_id":"b"}'), None, "valid UTF-8 JSON"),
    ]
    planned = dict(good, status="planned", branch=None, pr_url=None, failed_stage=None)
    cases.append((zip_bytes("base-upgrade-result.json", json.dumps(planned).encode()), None, "non-terminal"))
    for archive, artifact_digest, message in cases:
        client = ArtifactClient(
            item,
            archive,
            "unused",
            artifact_digest=artifact_digest or hashlib.sha256(archive).hexdigest(),
        )
        with pytest.raises(campaign.CampaignError, match=message):
            client.collect_result_artifact(
                item,
                run_id=501,
                campaign_id="campaign-001",
                target_version="3.3.0",
                expected_source_version="3.2.0",
                expected_pr_url=None,
            )


def test_signed_artifact_download_never_forwards_bearer_token():
    captured = []

    def opener(request, *, timeout):
        captured.append(request)
        return Response(b"zip", status=200)

    client = campaign.GitHubDispatchClient("operator-token", http_open=opener)
    assert client._download_signed_artifact(
        "https://objects.githubusercontent.com/result.zip?sig=fixture"
    ) == b"zip"
    assert captured[0].get_header("Authorization") is None
    with pytest.raises(campaign.CampaignError, match="approved signed URL"):
        client._download_signed_artifact("https://evil.example/result.zip?sig=fixture")


def test_run_timeout_cancels_once_without_mutation_retry_and_waits_terminal():
    item = project()

    class TimeoutClient(campaign.GitHubDispatchClient):
        def __init__(self):
            self.clock = 0.0
            self.calls = []

        def now(self):
            return self.clock

        def sleep(self, seconds):
            self.clock += seconds

        def api_json(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs.get("max_attempts")))
            if path.endswith("/cancel"):
                return None
            run = run_record(item, 701)
            if any(call[1].endswith("/cancel") for call in self.calls):
                run["status"] = "completed"
                run["conclusion"] = "cancelled"
            else:
                run["status"] = "requested"
                run["conclusion"] = None
            return run

    client = TimeoutClient()
    with pytest.raises(campaign.CampaignError, match="exceeded 30 minutes"):
        client.wait_for_run(item, 701, max_wait_seconds=0)
    cancel = [call for call in client.calls if call[1].endswith("/cancel")]
    assert cancel == [("POST", f"/repos/{item['repository']}/actions/runs/701/cancel", 1)]


def test_response_loss_recovery_rejects_multiple_exact_runs():
    item = project()
    first = run_record(item, 801)
    second = run_record(item, 802)

    class RecoveryClient(campaign.GitHubDispatchClient):
        def __init__(self):
            super().__init__("operator-token", sleep=lambda _: None)

        def api_json(self, method, path, **kwargs):
            return {"workflow_runs": [first, second]}

    with pytest.raises(campaign.CampaignError, match="ambiguous"):
        RecoveryClient()._recover_dispatch_run(
            item,
            campaign_id="campaign-001",
            target_version="3.3.0",
            dispatched_after=1787054400.0,
        )


def test_artifact_redirect_is_manual_and_bearer_is_not_forwarded():
    captured = []
    headers = Message()
    headers["Location"] = "https://objects.githubusercontent.com/result.zip?sig=fixture"
    queue = [
        HTTPError("https://api.github.com/archive", 302, "redirect", headers, None),
        Response(b"zip", status=200),
    ]

    def opener(request, *, timeout):
        captured.append(request)
        value = queue.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    item = project()
    client = campaign.GitHubDispatchClient("operator-token", http_open=opener)
    archive_url = f"https://api.github.com/repos/{item['repository']}/actions/artifacts/77/zip"
    location = client._artifact_redirect(archive_url)
    assert client._download_signed_artifact(location) == b"zip"
    assert captured[0].get_header("Authorization") == "Bearer operator-token"
    assert captured[1].get_header("Authorization") is None


def test_branch_without_pr_requires_independent_tag_and_exact_two_ledger_parents():
    item = project()
    target_commit = "b" * 40
    branch_commit = "c" * 40
    default_commit = "d" * 40
    synced = "2026-08-18T12:00:00Z"

    class BranchClient(campaign.GitHubDispatchClient):
        def __init__(self, *, wrong_parent=False, wrong_ledger=False):
            super().__init__("operator-token")
            self.wrong_parent = wrong_parent
            self.wrong_ledger = wrong_ledger

        def api_json(self, method, path, **kwargs):
            if "/git/ref/heads/chore%2Fbase-v3.3.0" in path:
                return {"ref": "refs/heads/chore/base-v3.3.0", "object": {"type": "commit", "sha": branch_commit}}
            if path.endswith("/git/ref/tags/base%2Fv3.3.0"):
                return {
                    "ref": "refs/tags/base/v3.3.0",
                    "object": {"type": "tag", "sha": "e" * 40},
                }
            if path.endswith("/git/tags/" + "e" * 40):
                return {"object": {"type": "commit", "sha": target_commit}}
            if path.endswith("/git/ref/heads/main"):
                return {"object": {"type": "commit", "sha": default_commit}}
            if path.endswith("/git/commits/" + branch_commit):
                return {
                    "sha": branch_commit,
                    "parents": [
                        {"sha": "f" * 40 if self.wrong_parent else default_commit},
                        {"sha": target_commit},
                    ],
                }
            raise AssertionError(path)

        def contents_path_text(self, project, ref, filename):
            if filename == "PROJECT.md":
                return (
                    "BASE_UPSTREAM_REPOSITORY=fixture-owner/base\n"
                    "BASE_UPSTREAM_VERSION=3.3.0\n"
                    "BASE_UPSTREAM_TAG=base/v3.3.0\n"
                    f"BASE_UPSTREAM_COMMIT={target_commit}\n"
                    f"BASE_LAST_SYNCED_AT={synced}\n"
                    "BASE_UPDATE_LEDGER=BASE_UPDATES.md\n"
                )
            return (
                "## Base update: v3.2.0 → v3.3.0\n"
                f"- Base commit: `{'0' * 40 if self.wrong_ledger else target_commit}`\n"
                f"- Synced at: `{synced}`\n"
                "- Verification result: PASS: receiver checks\n"
            )

    assert BranchClient().verify_existing_branch(
        item, "3.3.0", "fixture-owner/base"
    ) is True
    for client in (BranchClient(wrong_parent=True), BranchClient(wrong_ledger=True)):
        with pytest.raises(campaign.CampaignError, match="ownership|history"):
            client.verify_existing_branch(item, "3.3.0", "fixture-owner/base")


def test_open_pr_preflight_rejects_fork_head_wrong_base_and_multiple():
    item = project()
    base_row = {
        "number": 7,
        "state": "open",
        "html_url": f"https://github.com/{item['repository']}/pull/7",
        "head": {"ref": "chore/base-v3.3.0", "sha": "a" * 40, "repo": {"full_name": item["repository"]}},
        "base": {"ref": "main"},
    }

    class PullClient(campaign.GitHubDispatchClient):
        def __init__(self, rows):
            super().__init__("token")
            self.rows = rows

        def api_json(self, method, path, **kwargs):
            return self.rows

    bad_fork = json.loads(json.dumps(base_row))
    bad_fork["head"]["repo"]["full_name"] = "other/fork"
    bad_base = json.loads(json.dumps(base_row))
    bad_base["base"]["ref"] = "develop"
    for rows in ([bad_fork], [bad_base], [base_row, dict(base_row, number=8, html_url=f"https://github.com/{item['repository']}/pull/8")]):
        with pytest.raises(campaign.CampaignError):
            PullClient(rows).open_upgrade_prs(item, "3.3.0")


def test_artifact_metadata_is_bound_to_run_expiry_and_repository_url():
    item = project()
    raw = json.dumps(receiver_result(item, "campaign-001")).encode()
    archive = zip_bytes("base-upgrade-result.json", raw)
    mutations = [
        {"expired": True},
        {"workflow_run": {"id": 999}},
        {"archive_download_url": "https://api.github.com/repos/other/repo/actions/artifacts/77/zip"},
    ]
    for updates in mutations:
        client = ArtifactClient(item, archive, "unused", artifact_updates=updates)
        with pytest.raises(campaign.CampaignError, match="metadata|repository-bound"):
            client.collect_result_artifact(
                item,
                run_id=501,
                campaign_id="campaign-001",
                target_version="3.3.0",
                expected_source_version="3.2.0",
                expected_pr_url=None,
            )


def unsafe_zip(info):
    encrypted = bool(info.flag_bits & 0x1)
    raw = json.dumps(receiver_result(project(), "campaign-001")).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr(info, raw)
    value = bytearray(buffer.getvalue())
    if encrypted:
        central = value.find(b"PK\x01\x02")
        local = value.find(b"PK\x03\x04")
        assert central >= 0 and local >= 0
        value[local + 6] |= 0x1
        value[central + 8] |= 0x1
    return bytes(value)


def test_artifact_rejects_symlink_and_encrypted_entries():
    item = project()
    symlink = zipfile.ZipInfo("base-upgrade-result.json")
    symlink.create_system = 3
    symlink.external_attr = (0o120777 << 16)
    encrypted = zipfile.ZipInfo("base-upgrade-result.json")
    encrypted.flag_bits |= 0x1
    for archive in (unsafe_zip(symlink), unsafe_zip(encrypted)):
        client = ArtifactClient(item, archive, "unused")
        with pytest.raises(campaign.CampaignError, match="unsafe"):
            client.collect_result_artifact(
                item,
                run_id=501,
                campaign_id="campaign-001",
                target_version="3.3.0",
                expected_source_version="3.2.0",
                expected_pr_url=None,
            )


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({"project_id": "other"}, "identity"),
        ({"source_version": "3.1.0"}, "identity"),
        ({"pr_url": "https://github.com/fixture-owner/fixture-alpha/pull/8"}, "existing PR"),
    ],
)
def test_artifact_rejects_result_identity_source_and_existing_pr_drift(mutation, message):
    item = project()
    result = receiver_result(item, "campaign-001")
    result.update(mutation)
    raw = json.dumps(result).encode()
    archive = zip_bytes("base-upgrade-result.json", raw)
    client = ArtifactClient(item, archive, "unused")
    with pytest.raises(campaign.CampaignError, match=message):
        client.collect_result_artifact(
            item,
            run_id=501,
            campaign_id="campaign-001",
            target_version="3.3.0",
            expected_source_version="3.2.0",
            expected_pr_url=f"https://github.com/{item['repository']}/pull/7",
        )


def test_evidence_runtime_rejects_duplicate_run_time_drift_and_status_drift():
    alpha = project("fixture_alpha", "fixture-owner/fixture-alpha")
    gamma = project("fixture_gamma", "fixture-owner/fixture-gamma")
    client = FleetClient({"fixture_alpha": "pr_opened", "fixture_gamma": "verification_failed"})
    batch, evidence = campaign.dispatch_campaign(
        registry(alpha, gamma), target_version="3.3.0", campaign_id="campaign-001",
        channel="stable", allow_major=False, client=client,
    )
    mutations = []
    duplicate = json.loads(json.dumps(evidence))
    duplicate["entries"][1]["run_id"] = duplicate["entries"][0]["run_id"]
    mutations.append((duplicate, "unique workflow run"))
    future = json.loads(json.dumps(evidence))
    future["entries"][0]["completed_at"] = "2027-08-18T12:00:00Z"
    mutations.append((future, "chronologically"))
    status = json.loads(json.dumps(evidence))
    status["entries"][0]["final_status"] = "blocked"
    mutations.append((status, "final status"))
    for document, message in mutations:
        with pytest.raises(campaign.CampaignError, match=message):
            campaign.validate_evidence(document, batch=batch, registry=registry(alpha, gamma))


def test_failed_evidence_rejects_failure_stage_drift_from_batch_result():
    item = project()
    client = FleetClient({item["project_id"]: "pr_opened"}, poll_failure=item["project_id"])
    batch, evidence = campaign.dispatch_campaign(
        registry(item), target_version="3.3.0", campaign_id="campaign-001",
        channel="stable", allow_major=False, client=client,
    )
    evidence["entries"][0]["failure_stage"] = "artifact_collection"
    with pytest.raises(campaign.CampaignError, match="failure stage.*batch result"):
        campaign.validate_evidence(evidence, batch=batch, registry=registry(item))


@pytest.mark.parametrize(
    "field,value",
    [
        ("artifact_name", "base-upgrade-result-campaign-001"),
        ("artifact_sha256", "a" * 64),
        ("result_sha256", "b" * 64),
        ("final_status", "dispatch_failed"),
    ],
)
def test_failed_evidence_rejects_retained_artifact_and_result_fields(field, value):
    item = project()
    client = FleetClient({item["project_id"]: "pr_opened"}, poll_failure=item["project_id"])
    batch, evidence = campaign.dispatch_campaign(
        registry(item), target_version="3.3.0", campaign_id="campaign-001",
        channel="stable", allow_major=False, client=client,
    )
    evidence["entries"][0][field] = value
    with pytest.raises(campaign.CampaignError, match="must not retain"):
        campaign.validate_evidence(evidence, batch=batch, registry=registry(item))


def test_run_poll_evidence_rejects_completion_timestamp():
    item = project()
    client = FleetClient({item["project_id"]: "pr_opened"}, poll_failure=item["project_id"])
    batch, evidence = campaign.dispatch_campaign(
        registry(item), target_version="3.3.0", campaign_id="campaign-001",
        channel="stable", allow_major=False, client=client,
    )
    evidence["entries"][0]["completed_at"] = "2026-08-18T12:01:00Z"
    with pytest.raises(campaign.CampaignError, match="run_poll.*completion timestamp"):
        campaign.validate_evidence(evidence, batch=batch, registry=registry(item))


def test_artifact_collection_failure_evidence_may_keep_completion_timestamp():
    item = project()

    class ArtifactFailureClient(FleetClient):
        def collect_result_artifact(self, item, **kwargs):
            raise campaign.CampaignError("fixture artifact failure")

    client = ArtifactFailureClient({item["project_id"]: "pr_opened"})
    batch, evidence = campaign.dispatch_campaign(
        registry(item), target_version="3.3.0", campaign_id="campaign-001",
        channel="stable", allow_major=False, client=client,
    )
    entry = evidence["entries"][0]
    assert entry["failure_stage"] == "artifact_collection"
    assert entry["completed_at"] == "2026-08-18T12:01:00Z"
    campaign.validate_evidence(evidence, batch=batch, registry=registry(item))


def test_dispatch_output_collision_and_evidence_secret_overlap_fail_closed(tmp_path):
    same = tmp_path / "same.json"
    with pytest.raises(campaign.CampaignError, match="different"):
        campaign._reject_output_path_collisions((str(same), str(same)), ())
    alpha = project()
    client = FleetClient({alpha["project_id"]: "pr_opened"})
    batch, evidence = campaign.dispatch_campaign(
        registry(alpha), target_version="3.3.0", campaign_id="campaign-001",
        channel="stable", allow_major=False, client=client,
    )
    with pytest.raises(campaign.CampaignError, match="overlaps campaign evidence"):
        campaign._sanitize_evidence(
            evidence, ("actions/runs",), batch=batch, registry=registry(alpha)
        )


@pytest.mark.parametrize("failed_replace", [2, 3])
def test_artifact_set_removes_all_old_and_new_outputs_when_publish_fails(
    tmp_path, monkeypatch, failed_replace
):
    paths = [
        tmp_path / "batch.json",
        tmp_path / "summary.md",
        tmp_path / "evidence.json",
    ]
    for path in paths:
        path.write_text(f"old {path.name}\n", encoding="utf-8")

    real_replace = campaign.os.replace
    replace_calls = 0

    def failing_replace(source, destination):
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == failed_replace:
            raise OSError("fixture publish failure")
        real_replace(source, destination)

    monkeypatch.setattr(campaign.os, "replace", failing_replace)
    with pytest.raises(campaign.CampaignError, match="cannot write output artifact set"):
        campaign._write_artifact_set(
            [(path, f"new {path.name}\n") for path in paths]
        )

    assert replace_calls == failed_replace
    assert all(not path.exists() for path in paths)
    assert not list(tmp_path.glob(".*.*"))


def test_artifact_set_stages_every_member_before_replacing_old_outputs(tmp_path):
    first = tmp_path / "batch.json"
    second = tmp_path / "summary.md"
    missing_parent_output = tmp_path / "missing" / "evidence.json"
    first.write_text("old batch\n", encoding="utf-8")
    second.write_text("old summary\n", encoding="utf-8")

    with pytest.raises(campaign.CampaignError, match="cannot write output artifact set"):
        campaign._write_artifact_set(
            [
                (first, "new batch\n"),
                (second, "new summary\n"),
                (missing_parent_output, "new evidence\n"),
            ]
        )

    assert first.read_text(encoding="utf-8") == "old batch\n"
    assert second.read_text(encoding="utf-8") == "old summary\n"
    assert not missing_parent_output.exists()
    assert not list(tmp_path.glob(".*.*"))


def test_example_workflow_has_120_minute_gate_and_three_artifact_final_validation():
    import yaml

    path = ROOT / "examples/github-actions/base-upgrade-campaign.yml"
    document = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    job = document["jobs"]["dispatch"]
    assert job["timeout-minutes"] == "120"
    validate = next(step for step in job["steps"] if step["name"] == "Validate complete campaign artifacts")
    assert validate["if"] == "always()"
    for filename in ("batch.json", "summary.md", "evidence.json"):
        assert filename in validate["run"]
    assert "campaign.load_registry(registry_path)" in validate["run"]
    assert "campaign.validate_batch(batch, registry=registry)" in validate["run"]
    assert "campaign.validate_evidence(evidence, batch=batch, registry=registry)" in validate["run"]
    assert validate["env"]["REGISTRY_PATH"] == "${{ github.workspace }}/fleet/projects.json"
    upload = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"


def _workflow_validation_script():
    import yaml

    path = ROOT / "examples/github-actions/base-upgrade-campaign.yml"
    document = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    step = next(
        item
        for item in document["jobs"]["dispatch"]["steps"]
        if item["name"] == "Validate complete campaign artifacts"
    )
    marker = "python3 - <<'PY'\n"
    assert step["run"].startswith(marker)
    return step["run"][len(marker):].rsplit("\nPY", 1)[0]


@pytest.mark.parametrize(
    "mutation,expected_success",
    [
        (None, True),
        (
            lambda batch, evidence, fleet: evidence["entries"][0].update(
                {"failure_stage": "artifact_collection"}
            ),
            False,
        ),
        (
            lambda batch, evidence, fleet: evidence["entries"][0].update(
                {"artifact_sha256": "a" * 64}
            ),
            False,
        ),
        (
            lambda batch, evidence, fleet: evidence["entries"][0].update(
                {"completed_at": "2026-08-18T12:01:00Z"}
            ),
            False,
        ),
        (
            lambda batch, evidence, fleet: fleet["projects"][0].update(
                {"repository": "fixture-owner/different-repository"}
            ),
            False,
        ),
    ],
    ids=(
        "valid",
        "failure-stage-drift",
        "failed-entry-retained-field",
        "run-poll-completed-at",
        "private-registry-repository-drift",
    ),
)
def test_example_workflow_gate_executes_released_runtime_validator(
    tmp_path, mutation, expected_success
):
    item = project()
    fleet = registry(item)
    batch, evidence = campaign.dispatch_campaign(
        fleet,
        target_version="3.3.0",
        campaign_id="campaign-001",
        channel="stable",
        allow_major=False,
        client=FleetClient({item["project_id"]: "pr_opened"}, poll_failure=item["project_id"]),
    )
    if mutation is not None:
        mutation(batch, evidence, fleet)
    output = tmp_path / "output"
    output.mkdir()
    registry_path = tmp_path / "fleet" / "projects.json"
    registry_path.parent.mkdir()
    registry_path.write_text(json.dumps(fleet), encoding="utf-8")
    (output / "batch.json").write_text(json.dumps(batch), encoding="utf-8")
    (output / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    (output / "summary.md").write_text("# Base upgrade campaign\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-c", _workflow_validation_script()],
        cwd=tmp_path,
        env={
            **os.environ,
            "OUTPUT_DIR": str(output),
            "TOOLS_DIR": str(ROOT),
            "REGISTRY_PATH": str(registry_path),
            "INPUT_CAMPAIGN_ID": "campaign-001",
            "INPUT_TARGET_VERSION": "3.3.0",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    if expected_success:
        assert completed.returncode == 0, completed.stderr
    else:
        assert completed.returncode != 0
        assert "campaign artifact validation failed" in completed.stderr


def test_cancel_409_race_is_an_accepted_nonretried_mutation_response():
    calls = []

    def opener(request, *, timeout):
        calls.append(request)
        raise http_error(409)

    client = campaign.GitHubDispatchClient("token", http_open=opener)
    value = client.api_json(
        "POST", "/repos/fixture-owner/fixture-alpha/actions/runs/9/cancel",
        expected=(202, 409), max_attempts=1,
    )
    assert value is None
    assert len(calls) == 1


def test_dispatch_keeps_authoritative_200_locator_when_detail_get_fails():
    item = project()
    calls = []
    responses = [
        Response(json.dumps({
            "workflow_run_id": 901,
            "run_url": f"https://api.github.com/repos/{item['repository']}/actions/runs/901",
            "html_url": f"https://github.com/{item['repository']}/actions/runs/901",
        }).encode()),
        URLError("detail unavailable"),
        URLError("detail unavailable"),
        URLError("detail unavailable"),
    ]

    def opener(request, *, timeout):
        calls.append(request)
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    client = campaign.GitHubDispatchClient(
        "token", http_open=opener, sleep=lambda _: None
    )
    locator, _ = client.dispatch_receiver(
        item, campaign_id="campaign-001", target_version="3.3.0", allow_major=False
    )
    assert locator == {
        "id": 901,
        "html_url": f"https://github.com/{item['repository']}/actions/runs/901",
    }
    assert sum(request.method == "POST" for request in calls) == 1


def test_workflow_run_path_accepts_exact_default_ref_suffix_only():
    item = project()
    run = run_record(item, 902)
    run["path"] = ".github/workflows/base-upgrade-receiver.yml@main"
    assert campaign.GitHubDispatchClient("token")._validate_run_identity(item, run)["id"] == 902
    run["path"] = ".github/workflows/base-upgrade-receiver.yml@other"
    with pytest.raises(campaign.CampaignError, match="identity"):
        campaign.GitHubDispatchClient("token")._validate_run_identity(item, run)


def test_t3_rate_limit_headers_distinguish_ordinary_forbidden():
    assert campaign._provider_rate_limit_delay(
        403,
        {"X-RateLimit-Remaining": "1", "X-RateLimit-Reset": "200"},
        now=lambda: 100.0,
    ) is None
    assert campaign._provider_rate_limit_delay(
        403,
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "120"},
        now=lambda: 100.0,
    ) == 20.0
    assert campaign._provider_rate_limit_delay(
        429, {"Retry-After": "7"}, now=lambda: 100.0
    ) == 7.0


def test_malformed_200_recovers_unique_run_without_second_post():
    item = project()
    run = run_record(item, 903)
    responses = [
        Response(b"{}"),
        Response(json.dumps({"workflow_runs": [run]}).encode()),
    ]
    calls = []

    def opener(request, *, timeout):
        calls.append(request)
        return responses.pop(0)

    client = campaign.GitHubDispatchClient(
        "token", http_open=opener, sleep=lambda _: None,
        now=lambda: 1787054400.0,
    )
    recovered, _ = client.dispatch_receiver(
        item, campaign_id="campaign-001", target_version="3.3.0", allow_major=False
    )
    assert recovered["id"] == 903
    assert sum(request.method == "POST" for request in calls) == 1


def test_known_dispatch_locator_is_retained_when_first_poll_fails():
    item = project()

    class LocatorClient(FleetClient):
        def dispatch_receiver(self, item, **kwargs):
            return {
                "id": 904,
                "html_url": f"https://github.com/{item['repository']}/actions/runs/904",
            }, "2026-08-18T12:00:00Z"

        def wait_for_run(self, item, run_id):
            raise campaign.CampaignError("poll unavailable")

    client = LocatorClient({item["project_id"]: "pr_opened"})
    batch, evidence = campaign.dispatch_campaign(
        registry(item), target_version="3.3.0", campaign_id="campaign-001",
        channel="stable", allow_major=False, client=client,
    )
    assert batch["results"][0]["failed_stage"] == "run_poll"
    assert evidence["entries"][0]["run_id"] == 904
    assert evidence["entries"][0]["failure_stage"] == "run_poll"


def test_artifact_accepts_concurrent_up_to_date_race_source_equal_target():
    item = project()
    result = campaign._up_to_date_dispatch_result(
        item, campaign_id="campaign-001", target_version="3.3.0"
    )
    raw = json.dumps(result).encode()
    archive = zip_bytes("base-upgrade-result.json", raw)
    value, _ = ArtifactClient(item, archive, "unused").collect_result_artifact(
        item,
        run_id=501,
        campaign_id="campaign-001",
        target_version="3.3.0",
        expected_source_version="3.2.0",
        expected_pr_url=f"https://github.com/{item['repository']}/pull/7",
    )
    assert value["status"] == "up_to_date"
    assert value["source_version"] == "3.3.0"
