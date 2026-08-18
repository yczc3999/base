from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts/base-update-ledger.py"


def run_tool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_release_manifests_are_complete_and_current():
    result = run_tool("validate", "--current", "3.2.0")
    assert result.returncode == 0, result.stderr
    assert "5 releases" in result.stdout


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
