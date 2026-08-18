import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = ROOT / "scripts" / "schemas"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

REGISTRY_SCHEMA_PATH = SCHEMAS_DIR / "base-downstream-registry.schema.json"
RESULT_SCHEMA_PATH = SCHEMAS_DIR / "base-upgrade-result.schema.json"

VALID_REGISTRY_PATH = FIXTURES_DIR / "base-downstream-registry.valid.json"
INVALID_REGISTRY_PATH = FIXTURES_DIR / "base-downstream-registry.invalid.json"
VALID_RESULT_PATH = FIXTURES_DIR / "base-upgrade-result.valid.json"
INVALID_RESULT_PATH = FIXTURES_DIR / "base-upgrade-result.invalid.json"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_validator(path: Path):
    schema = load_json(path)
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


@pytest.fixture(scope="module")
def registry_validator():
    return load_validator(REGISTRY_SCHEMA_PATH)


@pytest.fixture(scope="module")
def result_validator():
    return load_validator(RESULT_SCHEMA_PATH)


def format_errors(errors):
    return [
        {
            "path": list(error.absolute_path),
            "validator": error.validator,
            "message": error.message,
        }
        for error in errors
    ]


def assert_valid(document, validator):
    errors = list(validator.iter_errors(document))
    assert not errors, format_errors(errors)


def assert_invalid(document, validator, expected_error=None):
    errors = list(validator.iter_errors(document))
    assert errors, "document unexpectedly passed schema validation"
    if expected_error is None:
        return

    expected_path = expected_error["path"]
    expected_validator = expected_error["validator"]
    assert any(
        list(error.absolute_path) == expected_path
        and error.validator == expected_validator
        for error in errors
    ), {
        "expected": expected_error,
        "actual": format_errors(errors),
    }


def registry_with(repository="fixture-owner/fixture-repo-alpha"):
    return {
        "schema_version": 1,
        "projects": [
            {
                "project_id": "fixture_project_alpha",
                "repository": repository,
                "default_branch": "main",
                "enabled": True,
                "channel": "stable",
                "provider": "github",
            }
        ],
    }


def result_for_status(status):
    result = load_json(VALID_RESULT_PATH)
    if status == "pr_opened":
        return result

    result.update(
        {
            "status": status,
            "branch": None,
            "pr_url": None,
            "failed_stage": None,
            "conflict_files": [],
            "verification_summary": None,
            "retry_command": "retry campaign",
            "rollback_command": "no action required",
        }
    )
    if status == "up_to_date":
        result["source_version"] = result["target_version"]
        result["verification_summary"] = "project already at target version"
    elif status == "conflict":
        result["failed_stage"] = "merge"
        result["conflict_files"] = ["serve/app/config.py"]
    elif status == "verification_failed":
        result["failed_stage"] = "verification"
        result["verification_summary"] = "pytest FAIL"
    elif status == "blocked":
        result["failed_stage"] = "version_gate"
        result["verification_summary"] = "major update requires approval"
    elif status == "dispatch_failed":
        result["failed_stage"] = "provider"
    return result


def test_schema_files_are_valid_draft_7_json_schemas():
    for path in (REGISTRY_SCHEMA_PATH, RESULT_SCHEMA_PATH):
        schema = load_json(path)
        assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
        Draft7Validator.check_schema(schema)


def test_valid_fixtures_pass(registry_validator, result_validator):
    assert_valid(load_json(VALID_REGISTRY_PATH), registry_validator)
    assert_valid(load_json(VALID_RESULT_PATH), result_validator)


@pytest.mark.parametrize(
    ("fixture_path", "validator_fixture"),
    [
        (INVALID_REGISTRY_PATH, "registry_validator"),
        (INVALID_RESULT_PATH, "result_validator"),
    ],
)
def test_invalid_fixture_cases_fail_for_the_declared_reason(
    request, fixture_path, validator_fixture
):
    validator = request.getfixturevalue(validator_fixture)
    fixture = load_json(fixture_path)
    cases = fixture["cases"]
    assert cases, f"{fixture_path.name} must contain cases"
    assert len({case["name"] for case in cases}) == len(cases), "case names must be unique"
    for case in cases:
        assert set(case) == {"name", "document", "expected_error"}, case["name"]
        assert set(case["expected_error"]) == {"path", "validator"}, case["name"]
        assert_invalid(case["document"], validator, case["expected_error"])


@pytest.mark.parametrize(
    "repository",
    [
        "a/r",
        f"{'a' * 39}/repo",
        f"owner/{'r' * 100}",
        "fixture-owner/.github",
        "Fixture-Owner/fixture_repo.v1",
    ],
)
def test_registry_accepts_canonical_github_owner_repo_slugs(
    registry_validator, repository
):
    assert_valid(registry_with(repository), registry_validator)


@pytest.mark.parametrize(
    "repository",
    [
        "https://github.com/fixture-owner/fixture-repo-alpha",
        "http://github.com/fixture-owner/fixture-repo-alpha",
        "git@github.com:fixture-owner/fixture-repo-alpha.git",
        "fixture-owner/fixture-repo-alpha.git",
        "fixture-owner/fixture-repo-alpha.GIT",
        "https://user:pass@github.com/fixture-owner/fixture-repo-alpha",
        "fixture-owner/group/fixture-repo-alpha",
        "fixture-owner/.",
        "fixture-owner/..",
        f"{'a' * 40}/repo",
        f"owner/{'r' * 101}",
        "fixture-owner/repo\n",
    ],
)
def test_registry_rejects_noncanonical_or_credential_bearing_repository(
    registry_validator, repository
):
    assert_invalid(registry_with(repository), registry_validator)


@pytest.mark.parametrize(
    "default_branch",
    [".hidden", "feature/.hidden", "feature..next", "feature//next", "main.lock"],
)
def test_registry_rejects_unsafe_git_ref_shapes(registry_validator, default_branch):
    document = registry_with()
    document["projects"][0]["default_branch"] = default_branch
    assert_invalid(document, registry_validator)


def test_registry_rejects_exact_duplicate_projects(registry_validator):
    document = registry_with()
    document["projects"].append(deepcopy(document["projects"][0]))
    assert_invalid(document, registry_validator)


@pytest.mark.parametrize(
    "extra_field",
    [
        {"current_version": "3.1.0"},
        {"token": "ghp_fixture_secret"},
        {"access_token": "ghp_fixture_secret"},
        {"database_password": "fixture-secret"},
        {"secret": "fixture-secret"},
    ],
)
def test_registry_rejects_version_override_and_secret_fields(
    registry_validator, extra_field
):
    document = registry_with()
    document["projects"][0].update(extra_field)
    assert_invalid(document, registry_validator)


def test_registry_rejects_invalid_provider_enum(registry_validator):
    document = registry_with()
    document["projects"][0]["provider"] = "gitlab"
    assert_invalid(document, registry_validator)


@pytest.mark.parametrize(
    "status",
    [
        "planned",
        "dispatched",
        "up_to_date",
        "pr_opened",
        "conflict",
        "verification_failed",
        "blocked",
        "dispatch_failed",
    ],
)
def test_result_accepts_each_declared_status_with_its_invariants(
    result_validator, status
):
    assert_valid(result_for_status(status), result_validator)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("branch", None),
        ("branch", "feature/base-v3.2.0"),
        ("branch", "chore/base-v03.2.0"),
        ("pr_url", None),
        ("failed_stage", "merge"),
        ("conflict_files", ["serve/app/config.py"]),
        ("verification_summary", None),
        ("verification_summary", "   "),
    ],
)
def test_pr_opened_rejects_each_broken_invariant_independently(
    result_validator, field, value
):
    document = result_for_status("pr_opened")
    document[field] = value
    assert_invalid(document, result_validator)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("branch", "chore/base-v3.2.0"),
        ("pr_url", "https://github.com/fixture-owner/fixture-repo-alpha/pull/1"),
        ("failed_stage", None),
        ("failed_stage", "verification"),
        ("conflict_files", []),
        ("conflict_files", ["serve/app/config.py", "serve/app/config.py"]),
    ],
)
def test_conflict_rejects_each_broken_invariant_independently(
    result_validator, field, value
):
    document = result_for_status("conflict")
    document[field] = value
    assert_invalid(document, result_validator)


@pytest.mark.parametrize(
    "conflict_file",
    [
        "/serve/app/config.py",
        "C:/serve/app/config.py",
        "serve\\app\\config.py",
        "serve//app/config.py",
        "serve/./app/config.py",
        "serve/../app/config.py",
        "serve/app/\x00config.py",
        "serve/app/\tconfig.py",
        "serve/app/\x85config.py",
        "serve/app/config.py/",
    ],
)
def test_conflict_rejects_non_repo_relative_or_unsafe_paths(
    result_validator, conflict_file
):
    document = result_for_status("conflict")
    document["conflict_files"] = [conflict_file]
    assert_invalid(document, result_validator)


@pytest.mark.parametrize(
    "status", ["verification_failed", "blocked", "dispatch_failed"]
)
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("branch", "chore/base-v3.2.0"),
        ("pr_url", "https://github.com/fixture-owner/fixture-repo-alpha/pull/1"),
        ("failed_stage", None),
        ("failed_stage", "   "),
        ("conflict_files", ["serve/app/config.py"]),
    ],
)
def test_failed_statuses_reject_each_broken_invariant_independently(
    result_validator, status, field, value
):
    document = result_for_status(status)
    document[field] = value
    assert_invalid(document, result_validator)


@pytest.mark.parametrize("status", ["planned", "dispatched", "up_to_date"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("branch", "chore/base-v3.2.0"),
        ("pr_url", "https://github.com/fixture-owner/fixture-repo-alpha/pull/1"),
        ("failed_stage", "provider"),
        ("conflict_files", ["serve/app/config.py"]),
    ],
)
def test_nonfailure_statuses_reject_failure_or_pr_artifacts(
    result_validator, status, field, value
):
    document = result_for_status(status)
    document[field] = value
    assert_invalid(document, result_validator)


@pytest.mark.parametrize(
    "pr_url",
    [
        "https://TOKEN@github.com/fixture-owner/fixture-repo-alpha/pull/1",
        "http://github.com/fixture-owner/fixture-repo-alpha/pull/1",
        "https://evil.example/fixture-owner/fixture-repo-alpha/pull/1",
        "https://github.com/fixture-owner/fixture-repo-alpha/pull/0",
        "https://github.com/fixture-owner/fixture-repo-alpha/pull/01",
        "https://github.com/fixture-owner/fixture-repo-alpha/pull/1/",
        "https://github.com/fixture-owner/fixture-repo-alpha/pull/1?token=TOKEN",
        "https://github.com/fixture-owner/fixture-repo-alpha.git/pull/1",
        "https://github.com/fixture-owner/../pull/1",
        f"https://github.com/{'a' * 40}/repo/pull/1",
        f"https://github.com/owner/{'r' * 101}/pull/1",
        "https://github.com/fixture-owner/fixture-repo-alpha/pull/1\n",
    ],
)
def test_result_rejects_noncanonical_or_credential_bearing_pr_url(
    result_validator, pr_url
):
    document = result_for_status("pr_opened")
    document["pr_url"] = pr_url
    assert_invalid(document, result_validator)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_version", "01.2.3"),
        ("source_version", "v1.2.3"),
        ("target_version", "1.02.3"),
        ("target_version", "1.2.03"),
        ("target_version", "1.2.3-rc.1"),
        ("target_version", "1.2.3\n"),
    ],
)
def test_result_rejects_noncanonical_core_semver(result_validator, field, value):
    document = result_for_status("pr_opened")
    document[field] = value
    assert_invalid(document, result_validator)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("campaign_id", "campaign-1\n"),
        ("project_id", "fixture_project\n"),
        ("failed_stage", "verification\ncredential"),
        ("conflict_files", ["serve/app/config.py\nTOKEN"]),
        ("verification_summary", "pytest PASS\nTOKEN"),
        ("retry_command", "retry\nTOKEN=value"),
        ("retry_command", "   "),
        ("rollback_command", "rollback\nTOKEN=value"),
        ("rollback_command", "   "),
    ],
)
def test_result_rejects_multiline_or_blank_artifact_strings(
    result_validator, field, value
):
    document = result_for_status("verification_failed")
    document[field] = value
    assert_invalid(document, result_validator)


@pytest.mark.parametrize(
    "field", ["verification_summary", "retry_command", "rollback_command"]
)
@pytest.mark.parametrize(
    "credential_url",
    [
        "https://fixture-user:fixture-pass@example.test/task",
        "https://fixture-token@example.test/task",
        "https://:fixture-pass@example.test/task",
        "ssh://fixture-user:fixture-pass@example.test/task",
    ],
)
def test_result_artifact_strings_reject_credential_bearing_urls(
    result_validator, field, credential_url
):
    document = result_for_status("pr_opened")
    document[field] = f"run {credential_url}"
    assert_invalid(document, result_validator)


@pytest.mark.parametrize(
    "field", ["verification_summary", "retry_command", "rollback_command"]
)
@pytest.mark.parametrize(
    "public_text",
    [
        "https://example.test/task",
        "see https://example.test/users/fixture-user@example.test",
        "https://example.test?contact=fixture-user@example.test",
        "https://example.test#fixture-user@example.test",
    ],
)
def test_result_artifact_strings_allow_urls_without_authority_credentials(
    result_validator, field, public_text
):
    document = result_for_status("pr_opened")
    document[field] = public_text
    assert_valid(document, result_validator)


@pytest.mark.parametrize(
    "secret_field",
    [
        {"token": "ghp_fixture_secret"},
        {"access_token": "ghp_fixture_secret"},
        {"database_password": "fixture-secret"},
        {"secret": "fixture-secret"},
    ],
)
def test_result_rejects_secret_fields(result_validator, secret_field):
    document = result_for_status("pr_opened")
    document.update(secret_field)
    assert_invalid(document, result_validator)
