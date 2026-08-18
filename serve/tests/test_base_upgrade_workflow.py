import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml
from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "base-upgrade-receiver.yml"
RUNNER = ROOT / "scripts" / "run-base-upgrade.sh"
SCHEMAS = ROOT / "scripts" / "schemas"


def workflow_document():
    # BaseLoader preserves GitHub's literal `on` key instead of applying the
    # YAML 1.1 boolean coercion used by PyYAML's SafeLoader.
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_receiver_actions_are_pinned_to_immutable_commits():
    steps = workflow_document()["jobs"]["receive"]["steps"]
    uses = [step["uses"] for step in steps if "uses" in step]
    assert len(uses) == 4
    assert all(re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", value) for value in uses)


def result_validator():
    schema_path = SCHEMAS / "base-upgrade-result.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft7Validator(schema)


def receiver_ledger_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    fixture = tmp_path / "receiver-ledger"
    (fixture / "scripts" / "lib").mkdir(parents=True)
    (fixture / "scripts" / "schemas").mkdir(parents=True)
    shutil.copy2(RUNNER, fixture / "scripts" / RUNNER.name)
    shutil.copy2(ROOT / "scripts" / "base-update-ledger.py", fixture / "scripts")
    shutil.copy2(ROOT / "scripts" / "lib" / "base_release.py", fixture / "scripts" / "lib")
    shutil.copy2(SCHEMAS / "base-upgrade-result.schema.json", fixture / "scripts" / "schemas")
    sync = fixture / "scripts" / "sync-base-release.sh"
    sync.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    sync.chmod(0o755)
    commit = "a" * 40
    synced_at = "2026-08-18T12:34:56Z"
    (fixture / "PROJECT.md").write_text(
        "\n".join(
            [
                "PROJECT_SLUG=fixture_project",
                "BASE_UPSTREAM_REPOSITORY=fixture-owner/fixture-base",
                "BASE_UPSTREAM_VERSION=1.0.0",
                "BASE_UPSTREAM_TAG=base/v1.0.0",
                f"BASE_UPSTREAM_COMMIT={commit}",
                f"BASE_LAST_SYNCED_AT={synced_at}",
                "BASE_UPDATE_LEDGER=BASE_UPDATES.md",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (fixture / "BASE_UPDATES.md").write_text(
        "\n".join(
            [
                "# Base Upstream Update Ledger",
                "",
                "---",
                "",
                "## Initial Base adoption: v1.0.0",
                "",
                f"- Synced at: `{synced_at}`",
                f"- Base commit: `{commit}`",
                "- Verification result: PASS: fixture checks",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("CI", None)
    env.pop("GITHUB_ACTIONS", None)
    env.update(
        {
            "BASE_UPGRADE_FIXTURE_MODE": "1",
            # Deliberately invalid: a valid ledger must advance to repository validation.
            "GITHUB_REPOSITORY": "not-a-repository",
            "BASE_UPGRADE_DEFAULT_BRANCH": "main",
        }
    )
    return fixture, env


def run_receiver_ledger_fixture(fixture: Path, env: dict[str, str]):
    result = fixture / ".base-upgrade" / "result.json"
    completed = subprocess.run(
        [
            str(fixture / "scripts" / RUNNER.name),
            "--project-id",
            "fixture_project",
            "--target-version",
            "1.1.0",
            "--campaign-id",
            "campaign-ledger",
            "--allow-major",
            "false",
            "--result-file",
            str(result),
            "--summary-file",
            str(fixture / ".base-upgrade" / "summary.md"),
        ],
        cwd=fixture,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed, json.loads(result.read_text(encoding="utf-8"))


def test_receiver_workflow_dispatch_contract_is_exact_and_bounded():
    document = workflow_document()
    dispatch = document["on"]["workflow_dispatch"]
    assert set(dispatch["inputs"]) == {
        "project_id",
        "target_version",
        "campaign_id",
        "allow_major",
    }
    assert dispatch["inputs"]["allow_major"]["default"] == "false"
    assert dispatch["inputs"]["allow_major"]["type"] == "boolean"
    assert "campaign_id" in document["run-name"]
    assert "target_version" in document["run-name"]
    assert document["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
    }


def test_receiver_serializes_the_whole_repository_without_cancellation():
    concurrency = workflow_document()["concurrency"]
    assert concurrency == {
        "group": "base-upgrade-receiver-${{ github.repository }}",
        "cancel-in-progress": "false",
        "queue": "max",
    }
    assert "target_version" not in concurrency["group"]
    assert "campaign_id" not in concurrency["group"]


def test_receiver_uses_fresh_default_branch_checkout_and_fixed_toolchains():
    steps = workflow_document()["jobs"]["receive"]["steps"]
    checkout = next(
        step
        for step in steps
        if step.get("uses")
        == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    assert checkout["with"] == {
        "ref": "${{ github.event.repository.default_branch }}",
        "fetch-depth": "0",
    }
    python = next(
        step
        for step in steps
        if step.get("uses")
        == "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
    )
    node = next(
        step
        for step in steps
        if step.get("uses")
        == "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020"
    )
    assert python["with"]["python-version"] == "3.12"
    assert node["with"]["node-version"] == "22"
    assert workflow_document()["jobs"]["receive"]["timeout-minutes"] == "30"


def test_receiver_invocation_has_only_fixed_runner_arguments():
    steps = workflow_document()["jobs"]["receive"]["steps"]
    receiver = next(step for step in steps if step.get("id") == "receiver")
    command = receiver["run"]
    for option in (
        "--project-id",
        "--target-version",
        "--campaign-id",
        "--allow-major",
        "--result-file",
        "--summary-file",
    ):
        assert command.count(option) == 1
    for forbidden in ("--command", "--script", "--remote", "--branch", "--token"):
        assert forbidden not in command
    assert receiver["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "TOKEN" not in command
    assert "${{ inputs." not in command
    assert receiver["env"]["RESULT_FILE"] == "${{ runner.temp }}/base-upgrade-result.json"
    assert receiver["env"]["SUMMARY_FILE"] == "${{ runner.temp }}/base-upgrade-summary.md"


def test_result_and_summary_are_published_before_final_status_is_applied():
    steps = workflow_document()["jobs"]["receive"]["steps"]
    fallback_index = next(
        i
        for i, step in enumerate(steps)
        if step["name"] == "Preserve a machine-readable prerequisite failure"
    )
    summary_index = next(i for i, step in enumerate(steps) if step["name"] == "Publish receiver summary")
    artifact_index = next(
        i
        for i, step in enumerate(steps)
        if step.get("uses")
        == "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
    )
    final_index = next(i for i, step in enumerate(steps) if step["name"] == "Apply the receiver result")
    assert steps[summary_index]["if"] == "always()"
    assert steps[fallback_index]["if"] == "always()"
    assert steps[artifact_index]["if"] == "always()"
    assert steps[artifact_index]["with"]["path"] == "${{ runner.temp }}/base-upgrade-result.json"
    assert steps[artifact_index]["with"]["if-no-files-found"] == "error"
    assert steps[final_index]["if"] == "always()"
    assert fallback_index < summary_index < artifact_index < final_index


def test_workflow_fallback_emits_schema_valid_dispatch_failure_without_checkout(tmp_path):
    steps = workflow_document()["jobs"]["receive"]["steps"]
    fallback = next(
        step
        for step in steps
        if step["name"] == "Preserve a machine-readable prerequisite failure"
    )
    script = fallback["run"]
    assert script.startswith("python3 - <<'PY'\n")
    body = script.removeprefix("python3 - <<'PY'\n").removesuffix("PY\n")
    result = tmp_path / "result.json"
    summary = tmp_path / "summary.md"
    env = os.environ.copy()
    env.update(
        {
            "CHECKOUT_OUTCOME": "failure",
            "PYTHON_OUTCOME": "skipped",
            "NODE_OUTCOME": "skipped",
            "IDENTITY_OUTCOME": "skipped",
            "INSTALL_OUTCOME": "skipped",
            "RECEIVER_OUTCOME": "skipped",
            "GITHUB_REPOSITORY_VALUE": "fixture-owner/fixture-repo",
            "INPUT_PROJECT_ID": "fixture_project",
            "INPUT_TARGET_VERSION": "3.3.0",
            "INPUT_CAMPAIGN_ID": "campaign-001",
            "INPUT_ALLOW_MAJOR": "false",
            "RESULT_FILE": str(result),
            "SUMMARY_FILE": str(summary),
        }
    )
    completed = subprocess.run(
        ["python3", "-"], input=body, env=env, capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(result.read_text(encoding="utf-8"))
    assert list(result_validator().iter_errors(value)) == []
    assert value["status"] == "dispatch_failed"
    assert value["failed_stage"] == "checkout"
    assert value["branch"] is None
    assert value["pr_url"] is None
    assert "dispatch_failed" in summary.read_text(encoding="utf-8")


def test_workflow_fallback_never_overwrites_a_runner_result(tmp_path):
    steps = workflow_document()["jobs"]["receive"]["steps"]
    fallback = next(
        step
        for step in steps
        if step["name"] == "Preserve a machine-readable prerequisite failure"
    )
    body = fallback["run"].removeprefix("python3 - <<'PY'\n").removesuffix("PY\n")
    result = tmp_path / "result.json"
    original = b'{"runner":"authoritative"}\n'
    result.write_bytes(original)
    env = os.environ.copy()
    env.update(
        {
            "RESULT_FILE": str(result),
            "SUMMARY_FILE": str(tmp_path / "summary.md"),
            # The early return intentionally precedes all other env reads.
        }
    )
    completed = subprocess.run(
        ["python3", "-"], input=body, env=env, capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
    assert result.read_bytes() == original


def test_runner_has_valid_shell_syntax_and_no_force_or_command_override():
    completed = subprocess.run(
        ["bash", "-n", str(RUNNER)], cwd=ROOT, capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
    source = RUNNER.read_text(encoding="utf-8")
    assert '"$ROOT/scripts/sync-base-release.sh" "$target_version" --install-deps' in source
    assert 'readonly branch="chore/base-v${target_version}"' in source
    assert "--force" not in source
    assert "force-with-lease" not in source
    assert "eval " not in source
    assert "--command" not in source.split("while (( $# ))", 1)[1]
    assert source.index("emit_result || emit_rc=$?") < source.index("git merge --abort")
    assert "git diff --name-only -z --diff-filter=U" in source
    assert "Draft7Validator(schema).iter_errors(result)" in source
    assert "verify_atomic_upgrade_ref" in source
    assert "render_update_nodes" in source
    assert "### Cross-version update nodes" in source
    assert "### Verification evidence" in source
    assert "— **PASS**" in source
    assert "prospective_pr_rollback_command" in source
    assert "new_branch_pushed=1" in source
    assert "new_pr_created=1" in source
    assert "gh pr ready" not in source


def test_fixture_mode_fails_closed_in_ci_and_writes_schema_result(tmp_path):
    fixture = tmp_path / "receiver-fixture"
    (fixture / "scripts" / "lib").mkdir(parents=True)
    (fixture / "scripts" / "schemas").mkdir(parents=True)
    shutil.copy2(RUNNER, fixture / "scripts" / RUNNER.name)
    shutil.copy2(ROOT / "scripts" / "lib" / "base_release.py", fixture / "scripts" / "lib")
    shutil.copy2(
        SCHEMAS / "base-upgrade-result.schema.json",
        fixture / "scripts" / "schemas",
    )
    result = fixture / ".base-upgrade" / "result.json"
    summary = fixture / ".base-upgrade" / "summary.md"
    env = os.environ.copy()
    env.update(
        {
            "BASE_UPGRADE_FIXTURE_MODE": "1",
            "CI": "true",
            "GITHUB_ACTIONS": "false",
            "GITHUB_REPOSITORY": "fixture-owner/fixture-repo",
            "BASE_UPGRADE_DEFAULT_BRANCH": "main",
            "GH_TOKEN": "receiver-secret-that-must-not-leak",
        }
    )
    completed = subprocess.run(
        [
            str(fixture / "scripts" / RUNNER.name),
            "--project-id",
            "fixture_project",
            "--target-version",
            "3.2.0",
            "--campaign-id",
            "campaign-001",
            "--allow-major",
            "false",
            "--result-file",
            str(result),
            "--summary-file",
            str(summary),
        ],
        cwd=fixture,
        env=env,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    value = json.loads(result.read_text(encoding="utf-8"))
    assert list(result_validator().iter_errors(value)) == []
    assert value["status"] == "blocked"
    assert value["failed_stage"] == "input_validation"
    assert value["branch"] is None
    assert value["pr_url"] is None
    combined = completed.stdout + completed.stderr + result.read_text() + summary.read_text()
    assert "receiver-secret-that-must-not-leak" not in combined


def test_runner_rejects_unknown_arguments_and_output_escape(tmp_path):
    unknown = subprocess.run(
        [str(RUNNER), "--command", "echo fixture"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert unknown.returncode == 2
    escaped = subprocess.run(
        [
            str(RUNNER),
            "--project-id",
            "fixture_project",
            "--target-version",
            "3.2.0",
            "--campaign-id",
            "campaign-001",
            "--allow-major",
            "false",
            "--result-file",
            str(tmp_path / "outside.json"),
            "--summary-file",
            ".base-upgrade/summary.md",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert escaped.returncode == 2


def test_runner_rejects_contract_file_and_same_output_paths(tmp_path):
    common = [
        str(RUNNER),
        "--project-id",
        "fixture_project",
        "--target-version",
        "3.2.0",
        "--campaign-id",
        "campaign-001",
        "--allow-major",
        "false",
    ]
    protected = subprocess.run(
        [*common, "--result-file", "PROJECT.md", "--summary-file", ".base-upgrade/summary.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    same = subprocess.run(
        [*common, "--result-file", ".base-upgrade/result", "--summary-file", ".base-upgrade/result"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert protected.returncode == 2
    assert same.returncode == 2


def test_runner_rejects_overlong_target_before_shell_arithmetic():
    completed = subprocess.run(
        [
            str(RUNNER),
            "--project-id",
            "fixture_project",
            "--target-version",
            f"{'9' * 65}.1.0",
            "--campaign-id",
            "campaign-001",
            "--allow-major",
            "false",
            "--result-file",
            ".base-upgrade/overlong-result.json",
            "--summary-file",
            ".base-upgrade/overlong-summary.md",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "integer expression expected" not in completed.stderr


def test_runner_rejects_overlong_source_as_schema_valid_blocked_result(tmp_path):
    fixture = tmp_path / "overlong-source"
    (fixture / "scripts" / "lib").mkdir(parents=True)
    (fixture / "scripts" / "schemas").mkdir(parents=True)
    shutil.copy2(RUNNER, fixture / "scripts" / RUNNER.name)
    shutil.copy2(ROOT / "scripts" / "base-update-ledger.py", fixture / "scripts")
    shutil.copy2(ROOT / "scripts" / "lib" / "base_release.py", fixture / "scripts" / "lib")
    shutil.copy2(SCHEMAS / "base-upgrade-result.schema.json", fixture / "scripts" / "schemas")
    sync = fixture / "scripts" / "sync-base-release.sh"
    sync.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    sync.chmod(0o755)
    source = f"{'9' * 65}.1.0"
    (fixture / "PROJECT.md").write_text(
        "\n".join(
            [
                "PROJECT_SLUG=fixture_project",
                "BASE_UPSTREAM_REPOSITORY=fixture-owner/fixture-base",
                f"BASE_UPSTREAM_VERSION={source}",
                f"BASE_UPSTREAM_TAG=base/v{source}",
                f"BASE_UPSTREAM_COMMIT={'0' * 40}",
                "BASE_UPDATE_LEDGER=BASE_UPDATES.md",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (fixture / "BASE_UPDATES.md").write_text("# fixture\n", encoding="utf-8")
    result = fixture / ".base-upgrade" / "result.json"
    summary = fixture / ".base-upgrade" / "summary.md"
    env = os.environ.copy()
    env.pop("CI", None)
    env.pop("GITHUB_ACTIONS", None)
    env.update(
        {
            "BASE_UPGRADE_FIXTURE_MODE": "1",
            "GITHUB_REPOSITORY": "fixture-owner/fixture-repo",
            "BASE_UPGRADE_DEFAULT_BRANCH": "main",
        }
    )
    completed = subprocess.run(
        [
            str(fixture / "scripts" / RUNNER.name),
            "--project-id",
            "fixture_project",
            "--target-version",
            "3.2.0",
            "--campaign-id",
            "campaign-001",
            "--allow-major",
            "false",
            "--result-file",
            str(result),
            "--summary-file",
            str(summary),
        ],
        cwd=fixture,
        env=env,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    value = json.loads(result.read_text(encoding="utf-8"))
    assert list(result_validator().iter_errors(value)) == []
    assert value["source_version"] is None
    assert value["status"] == "blocked"
    assert value["failed_stage"] == "project_ledger"
    assert "integer expression expected" not in completed.stderr


def test_runner_accepts_exact_latest_ledger_entry_before_repository_validation(tmp_path):
    fixture, env = receiver_ledger_fixture(tmp_path)
    completed, value = run_receiver_ledger_fixture(fixture, env)
    assert completed.returncode == 1
    assert value["failed_stage"] == "repository_identity"


def test_runner_blocks_missing_latest_ledger_field(tmp_path):
    fixture, env = receiver_ledger_fixture(tmp_path)
    history = fixture / "BASE_UPDATES.md"
    history.write_text(
        history.read_text(encoding="utf-8").replace(
            "- Verification result: PASS: fixture checks\n", ""
        ),
        encoding="utf-8",
    )
    completed, value = run_receiver_ledger_fixture(fixture, env)
    assert completed.returncode == 1
    assert value["failed_stage"] == "project_ledger"


def test_runner_blocks_empty_latest_ledger_verification(tmp_path):
    fixture, env = receiver_ledger_fixture(tmp_path)
    history = fixture / "BASE_UPDATES.md"
    history.write_text(
        history.read_text(encoding="utf-8").replace(
            "- Verification result: PASS: fixture checks",
            "- Verification result:    ",
        ),
        encoding="utf-8",
    )
    completed, value = run_receiver_ledger_fixture(fixture, env)
    assert completed.returncode == 1
    assert value["failed_stage"] == "project_ledger"


def test_runner_blocks_latest_ledger_timestamp_drift(tmp_path):
    fixture, env = receiver_ledger_fixture(tmp_path)
    history = fixture / "BASE_UPDATES.md"
    history.write_text(
        history.read_text(encoding="utf-8").replace(
            "2026-08-18T12:34:56Z", "2026-08-18T12:34:57Z"
        ),
        encoding="utf-8",
    )
    completed, value = run_receiver_ledger_fixture(fixture, env)
    assert completed.returncode == 1
    assert value["failed_stage"] == "project_ledger"


def test_runner_blocks_noncanonical_matching_sync_timestamps(tmp_path):
    fixture, env = receiver_ledger_fixture(tmp_path)
    project = fixture / "PROJECT.md"
    history = fixture / "BASE_UPDATES.md"
    project.write_text(
        project.read_text(encoding="utf-8").replace(
            "2026-08-18T12:34:56Z", "2026-08-18 12:34:56Z"
        ),
        encoding="utf-8",
    )
    history.write_text(
        history.read_text(encoding="utf-8").replace(
            "2026-08-18T12:34:56Z", "2026-08-18 12:34:56Z"
        ),
        encoding="utf-8",
    )
    completed, value = run_receiver_ledger_fixture(fixture, env)
    assert completed.returncode == 1
    assert value["failed_stage"] == "project_ledger"


def test_runner_blocks_duplicate_latest_ledger_field(tmp_path):
    fixture, env = receiver_ledger_fixture(tmp_path)
    history = fixture / "BASE_UPDATES.md"
    history.write_text(
        history.read_text(encoding="utf-8")
        + "- Verification result: PASS: shadow result\n",
        encoding="utf-8",
    )
    completed, value = run_receiver_ledger_fixture(fixture, env)
    assert completed.returncode == 1
    assert value["failed_stage"] == "project_ledger"


def test_runner_blocks_duplicate_project_sync_timestamp(tmp_path):
    fixture, env = receiver_ledger_fixture(tmp_path)
    project = fixture / "PROJECT.md"
    project.write_text(
        project.read_text(encoding="utf-8")
        + "BASE_LAST_SYNCED_AT=2026-08-18T12:34:56Z\n",
        encoding="utf-8",
    )
    completed, value = run_receiver_ledger_fixture(fixture, env)
    assert completed.returncode == 1
    assert value["failed_stage"] == "project_ledger"
