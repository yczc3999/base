from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts/base-update-ledger.py"
sys.path.insert(0, str(ROOT / "scripts"))

from lib.base_release import (  # noqa: E402
    BaseReleaseError,
    parse_core_semver,
    parse_project_text,
    redact,
    select_manifests,
)


def run_tool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_release_manifests_are_complete_and_current():
    current = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    release_count = len(list((ROOT / "releases").glob("base-v*.json")))
    result = run_tool("validate", "--current", current)
    assert result.returncode == 0, result.stderr
    assert f"{release_count} releases" in result.stdout


def test_cross_version_plan_lists_each_release_and_node():
    result = run_tool("plan", "--from", "3.0.0", "--to", "3.2.0")
    assert result.returncode == 0, result.stderr
    assert "v3.1.0" in result.stdout
    assert "v3.2.0" in result.stdout
    assert "bootstrap.project-one-command" in result.stdout
    assert "downstream.atomic-sync-record" in result.stdout
    assert "Declared Base file scope" in result.stdout


def test_current_can_read_committed_project_ledger():
    result = run_tool("current", "--project", "VERSION", "--ref", "HEAD")
    assert result.returncode != 0
    assert "BASE_UPSTREAM_VERSION" in result.stderr


def test_initialize_and_record_downstream_ledgers(tmp_path):
    project = tmp_path / "PROJECT.md"
    history = tmp_path / "BASE_UPDATES.md"
    commit_31 = "1" * 40
    commit_32 = "2" * 40

    initial = run_tool(
        "initialize",
        "--project", str(project),
        "--history", str(history),
        "--project-slug", "fixture_project",
        "--project-name", "Fixture Project",
        "--db-name", "fixture_project",
        "--db-user", "fixture_project_app",
        "--version", "3.1.0",
        "--commit", commit_31,
    )
    assert initial.returncode == 0, initial.stderr
    assert "BASE_UPSTREAM_VERSION=3.1.0" in project.read_text()
    assert "BASE_UPDATE_LEDGER=BASE_UPDATES.md" in project.read_text()
    assert "Initial Base adoption: v3.1.0" in history.read_text()

    update = run_tool(
        "record",
        "--project", str(project),
        "--history", str(history),
        "--from", "3.1.0",
        "--to", "3.2.0",
        "--commit", commit_32,
    )
    assert update.returncode == 0, update.stderr
    project_text = project.read_text()
    history_text = history.read_text()
    assert "BASE_UPSTREAM_VERSION=3.2.0" in project_text
    assert f"BASE_UPSTREAM_COMMIT={commit_32}" in project_text
    assert "BASE_NEXT_UPDATE_COMMAND=./scripts/sync-base-release.sh <TARGET_VERSION>" in project_text
    assert "Base update: v3.1.0 → v3.2.0" in history_text
    assert "downstream.update-plan-ledger" in history_text


def test_initialize_and_record_preserve_canonical_base_repository(tmp_path):
    project = tmp_path / "PROJECT.md"
    history = tmp_path / "BASE_UPDATES.md"

    initial = run_tool(
        "initialize",
        "--project", str(project),
        "--history", str(history),
        "--project-slug", "fixture_project",
        "--project-name", "Fixture Project",
        "--db-name", "fixture_project",
        "--db-user", "fixture_project_app",
        "--upstream-repository", "fixture-owner/base-platform",
        "--version", "3.1.0",
        "--commit", "1" * 40,
    )
    assert initial.returncode == 0, initial.stderr
    assert (
        "BASE_UPSTREAM_REPOSITORY=fixture-owner/base-platform"
        in project.read_text(encoding="utf-8")
    )

    update = run_tool(
        "record",
        "--project", str(project),
        "--history", str(history),
        "--from", "3.1.0",
        "--to", "3.2.0",
        "--commit", "2" * 40,
    )
    assert update.returncode == 0, update.stderr
    assert (
        project.read_text(encoding="utf-8").count(
            "BASE_UPSTREAM_REPOSITORY=fixture-owner/base-platform"
        )
        == 1
    )


def test_record_backfills_repository_from_legacy_git_upstream(tmp_path):
    repository = tmp_path / "downstream"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        [
            "git", "-C", str(repository), "remote", "add", "upstream",
            "https://github.com/fixture-owner/base-platform.git",
        ],
        check=True,
    )
    project = repository / "PROJECT.md"
    history = repository / "BASE_UPDATES.md"
    project.write_text("BASE_UPSTREAM_VERSION=3.1.0\n", encoding="utf-8")

    result = run_tool(
        "record",
        "--project", str(project),
        "--history", str(history),
        "--from", "3.1.0",
        "--to", "3.2.0",
        "--commit", "2" * 40,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "BASE_UPSTREAM_REPOSITORY=fixture-owner/base-platform"
        in project.read_text(encoding="utf-8")
    )


def test_record_blocks_legacy_git_ledger_without_trusted_upstream(tmp_path):
    repository = tmp_path / "downstream"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    project = repository / "PROJECT.md"
    project.write_text("BASE_UPSTREAM_VERSION=3.1.0\n", encoding="utf-8")

    result = run_tool(
        "record",
        "--project", str(project),
        "--history", str(repository / "BASE_UPDATES.md"),
        "--from", "3.1.0",
        "--to", "3.2.0",
        "--commit", "2" * 40,
    )

    assert result.returncode != 0
    assert "no trusted upstream remote" in result.stderr
    assert project.read_text(encoding="utf-8") == "BASE_UPSTREAM_VERSION=3.1.0\n"


@pytest.mark.parametrize(
    "remote",
    [
        "https://github.com/fixture-owner/base-platform.git",
        "git@github.com:fixture-owner/base-platform.git",
        "ssh://git@github.com/fixture-owner/base-platform.git",
        "git://github.com/fixture-owner/base-platform.git",
    ],
)
def test_normalize_repository_accepts_github_fetch_url_forms(remote):
    result = run_tool("normalize-repository", "--remote-url", remote)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "fixture-owner/base-platform\n"


@pytest.mark.parametrize(
    "remote",
    [
        "https://token@github.com/fixture-owner/base-platform.git",
        "https://user:token@github.com/fixture-owner/base-platform.git",
        "https://gitlab.com/fixture-owner/base-platform.git",
        "https://github.com/fixture-owner/nested/base-platform.git",
        "file:///tmp/base-platform.git",
    ],
)
def test_normalize_repository_rejects_non_public_or_non_github_identity(remote):
    result = run_tool("normalize-repository", "--remote-url", remote)
    assert result.returncode != 0
    assert "[REDACTED]" not in result.stdout


@pytest.mark.parametrize(
    "repository",
    [
        "https://github.com/fixture-owner/base-platform",
        "user:token@github.com/fixture-owner/base-platform",
        "fixture-owner/nested/base-platform",
        "fixture-owner/base-platform.git",
    ],
)
def test_initialize_rejects_noncanonical_repository_field(repository, tmp_path):
    result = run_tool(
        "initialize",
        "--project", str(tmp_path / "PROJECT.md"),
        "--history", str(tmp_path / "BASE_UPDATES.md"),
        "--project-slug", "fixture_project",
        "--project-name", "Fixture Project",
        "--db-name", "fixture_project",
        "--db-user", "fixture_project_app",
        "--upstream-repository", repository,
        "--version", "3.1.0",
        "--commit", "1" * 40,
    )
    assert result.returncode != 0
    assert not (tmp_path / "PROJECT.md").exists()


def test_record_rejects_ledger_version_mismatch(tmp_path):
    project = tmp_path / "PROJECT.md"
    project.write_text("BASE_UPSTREAM_VERSION=3.0.0\n")
    result = run_tool(
        "record",
        "--project", str(project),
        "--history", str(tmp_path / "BASE_UPDATES.md"),
        "--from", "3.1.0",
        "--to", "3.2.0",
    )
    assert result.returncode != 0
    assert "expected '3.1.0'" in result.stderr


def test_sync_script_commits_merge_and_ledgers_atomically():
    text = (ROOT / "scripts/sync-base-release.sh").read_text(encoding="utf-8")
    assert "status --porcelain --untracked-files=all" in text
    assert "merge --no-ff --no-commit" in text
    assert "--continue" in text and "MERGE_HEAD" in text
    assert "current --project PROJECT.md --ref HEAD" in text
    assert "restore --source=HEAD --staged --worktree" in text
    assert "plan" in text and "record" in text
    assert "diff --cached --check" in text
    assert "git -C \"$ROOT\" add PROJECT.md BASE_UPDATES.md" in text
    assert "merge-base --is-ancestor \"$CURRENT_TAG\" \"refs/tags/${TAG}\"" in text
    assert 'for option in "${@:2}"' in text
    assert "--install-deps" in text
    assert 'python3 -m venv "$ROOT/serve/.venv"' in text
    assert '-r "$ROOT/scripts/requirements.txt"' in text
    assert '-r "$ROOT/serve/requirements-dev.txt"' in text
    assert '(cd "$ROOT/admin" && npm ci)' in text


@pytest.mark.parametrize(
    "value",
    ["v3.2.0", "03.2.0", "3.02.0", "3.2.00", "3.2", "3.2.0-rc.1", "3.2.0\n"],
)
def test_shared_semver_parser_rejects_noncanonical_versions(value):
    with pytest.raises(BaseReleaseError, match="invalid stable SemVer"):
        parse_core_semver(value)


def test_shared_semver_parser_accepts_the_contract_length_boundary():
    major = "9" * 60
    value = f"{major}.1.1"
    assert len(value) == 64
    assert parse_core_semver(value) == (int(major), 1, 1)


@pytest.mark.parametrize(
    "value",
    [
        f"{'9' * 61}.1.1",
        f"1.{'9' * 61}.1",
        f"1.1.{'9' * 61}",
        f"{'9' * 5000}.0.0",
    ],
)
def test_shared_semver_parser_rejects_overlong_values_as_contract_errors(value):
    with pytest.raises(BaseReleaseError, match="invalid stable SemVer") as exc_info:
        parse_core_semver(value)
    assert not isinstance(exc_info.value, ValueError)
    assert len(str(exc_info.value)) < 200


def test_shared_manifest_selector_preserves_ledger_range_rules():
    manifests = {
        "3.0.0": {"version": "3.0.0"},
        "3.1.0": {"version": "3.1.0"},
        "3.2.0": {"version": "3.2.0"},
    }
    assert [item["version"] for item in select_manifests(manifests, "3.0.0", "3.2.0")] == [
        "3.1.0",
        "3.2.0",
    ]
    with pytest.raises(BaseReleaseError, match="must be newer"):
        select_manifests(manifests, "3.2.0", "3.2.0")
    assert [
        item["version"]
        for item in select_manifests(manifests, "2.9.0", "3.2.0")
    ] == ["3.0.0", "3.1.0", "3.2.0"]


def test_ledger_plan_accepts_valid_source_without_a_release_manifest():
    result = run_tool("plan", "--from", "0.9.0", "--to", "1.0.0")
    assert result.returncode == 0, result.stderr
    assert "Base update: v0.9.0 → v1.0.0" in result.stdout
    assert "### v1.0.0" in result.stdout


def test_project_parser_strict_mode_rejects_duplicate_keys_without_changing_legacy_mode():
    text = "BASE_UPSTREAM_VERSION=3.1.0\nBASE_UPSTREAM_VERSION=3.2.0\n"
    assert parse_project_text(text)["BASE_UPSTREAM_VERSION"] == "3.2.0"
    with pytest.raises(BaseReleaseError, match="duplicate key"):
        parse_project_text(text, strict=True)


def test_shared_redaction_recurses_and_covers_credential_shapes():
    known_token = "exact-provider-token-123"
    value = {
        "log": [
            f"provider returned {known_token}",
            "Authorization: Basic dXNlcjpwYXNz",
            "retry with Bearer opaque-token-1234",
            "remote=https://user:password@example.test/OWNER/REPO.git",
            'BASE_UPGRADE_GITHUB_TOKEN="github-token-value"',
            '"client_secret": "client-secret-value"',
            "database.password=hunter2",
            "api_key=api-key-value",
            "key=generic-key-value",
        ],
        "nested": ("private-key=private-key-value",),
    }
    result = redact(value, secrets=[known_token])
    rendered = repr(result)
    for secret in (
        known_token,
        "dXNlcjpwYXNz",
        "opaque-token-1234",
        "user:password",
        "github-token-value",
        "client-secret-value",
        "hunter2",
        "api-key-value",
        "generic-key-value",
        "private-key-value",
    ):
        assert secret not in rendered
    assert "https://example.test/OWNER/REPO.git" in rendered
    assert rendered.count("[REDACTED]") >= 8


@pytest.mark.parametrize(
    "value, secret",
    [
        ("Bearer A", "A"),
        ("Bearer abcdefgh", "abcdefgh"),
        ("bearer Alpha", "Alpha"),
        ("BEARER a-b", "a-b"),
        ("retry with BeArEr x_y", "x_y"),
        ("Bearer AZaz09-._~+/==", "AZaz09-._~+/=="),
    ],
)
def test_shared_redaction_covers_all_bearer_token68_credentials(value, secret):
    result = redact(value)
    assert "Bearer [REDACTED]" in result
    assert secret not in result.replace("Bearer [REDACTED]", "")


def test_shared_redaction_does_not_damage_ordinary_text():
    text = (
        "A certificate identifies its holder; monkey=value; key findings remain "
        "visible; password policy requires rotation; "
        "https://example.test/docs@v1 is a normal path."
    )
    assert redact(text) == text
