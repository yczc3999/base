from pathlib import Path
import os
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/bootstrap-project.sh"


def run_bootstrap(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def create_bootstrap_git_fixture(tmp_path: Path, upstream_url: str) -> Path:
    fixture = tmp_path / "bootstrap-project"
    (fixture / "scripts" / "lib").mkdir(parents=True)
    (fixture / "releases").mkdir()
    (fixture / "serve" / ".venv" / "bin").mkdir(parents=True)
    (fixture / "admin").mkdir()
    shutil.copy2(SCRIPT, fixture / "scripts" / SCRIPT.name)
    shutil.copy2(ROOT / "scripts" / "base-update-ledger.py", fixture / "scripts")
    shutil.copy2(
        ROOT / "scripts" / "lib" / "base_release.py",
        fixture / "scripts" / "lib",
    )
    shutil.copy2(ROOT / "releases" / "base-v3.2.0.json", fixture / "releases")
    (fixture / "scripts" / "lib" / "provision-postgres-database.sh").write_text(
        """#!/usr/bin/env bash
db_die() { printf 'ERROR: %s\\n' "$*" >&2; exit 1; }
provision_postgres_database() { :; }
""",
        encoding="utf-8",
    )
    (fixture / "VERSION").write_text("3.2.0\n", encoding="utf-8")
    (fixture / "admin" / ".env.production").write_text(
        "VITE_APP_TITLE=Base\n", encoding="utf-8"
    )
    python_stub = fixture / "serve" / ".venv" / "bin" / "python"
    python_stub.write_text("#!/usr/bin/env bash\nexec python3 \"$@\"\n", encoding="utf-8")
    python_stub.chmod(0o755)
    subprocess.run(["git", "init", "-q", str(fixture)], check=True)
    subprocess.run(
        ["git", "-C", str(fixture), "config", "user.email", "fixture@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(fixture), "config", "user.name", "Fixture"], check=True
    )
    subprocess.run(
        ["git", "-C", str(fixture), "remote", "add", "upstream", upstream_url],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(fixture),
            "remote",
            "add",
            "origin",
            "https://github.com/fixture-owner/fixture-project.git",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(fixture), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(fixture), "commit", "-qm", "fixture base"], check=True
    )
    return fixture


def run_bootstrap_fixture(fixture: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({"BOOTSTRAP_SKIP_INSTALL": "1", "BOOTSTRAP_SKIP_CHECKS": "1"})
    return subprocess.run(
        [str(fixture / "scripts" / "bootstrap-project.sh"), "fixture_project", "Fixture Project"],
        cwd=fixture,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_bootstrap_help():
    result = run_bootstrap("--help")
    assert result.returncode == 0
    assert "PROJECT_SLUG" in result.stdout


def test_bootstrap_plan_derives_isolated_identity():
    result = run_bootstrap("fixture_project", "Fixture Project", "--plan")
    assert result.returncode == 0, result.stderr
    assert "database=fixture_project" in result.stdout
    assert "role=fixture_project_app" in result.stdout
    assert "base_platform_app" not in result.stdout


def test_bootstrap_rejects_reserved_and_invalid_slugs():
    for slug in ("base", "base_platform", "BaseProject", "project-name", "a"):
        result = run_bootstrap(slug, "--plan")
        assert result.returncode != 0, slug


def test_bootstrap_and_base_provision_share_database_core():
    library = "scripts/lib/provision-postgres-database.sh"
    for relative in ("scripts/bootstrap-project.sh", "scripts/provision-base-database.sh"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert library in text
        assert "provision_postgres_database" in text


def test_bootstrap_initializes_downstream_version_ledgers():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "base-update-ledger.py" in text
    assert "PROJECT.md" in text
    assert "BASE_UPDATES.md" in text
    assert "normalize-repository" in text
    assert '--upstream-repository "$base_upstream_repository"' in text
    assert '"BASE_UPSTREAM_REPOSITORY": os.environ["BASE_REPOSITORY_VALUE"]' in text
    assert "PROJECT.md contains duplicate key" in text


@pytest.mark.parametrize(
    "upstream_url",
    [
        "https://github.com/BaseOrg/BaseRepo.git",
        "ssh://git@github.com/BaseOrg/BaseRepo.git",
        "git://github.com/BaseOrg/BaseRepo.git",
        "git@github.com:BaseOrg/BaseRepo.git",
    ],
)
def test_bootstrap_real_git_fixture_canonicalizes_github_upstream(
    tmp_path, upstream_url
):
    fixture = create_bootstrap_git_fixture(tmp_path, upstream_url)
    result = run_bootstrap_fixture(fixture)
    assert result.returncode == 0, result.stderr
    project = (fixture / "PROJECT.md").read_text(encoding="utf-8")
    assert "BASE_UPSTREAM_REPOSITORY=BaseOrg/BaseRepo" in project
    assert (fixture / "BASE_UPDATES.md").is_file()


def test_bootstrap_real_git_fixture_accepts_matching_existing_ledger(tmp_path):
    fixture = create_bootstrap_git_fixture(
        tmp_path, "https://github.com/BaseOrg/BaseRepo.git"
    )
    first = run_bootstrap_fixture(fixture)
    assert first.returncode == 0, first.stderr
    original_project = (fixture / "PROJECT.md").read_text(encoding="utf-8")
    original_history = (fixture / "BASE_UPDATES.md").read_text(encoding="utf-8")

    second = run_bootstrap_fixture(fixture)
    assert second.returncode == 0, second.stderr
    assert (fixture / "PROJECT.md").read_text(encoding="utf-8") == original_project
    assert (fixture / "BASE_UPDATES.md").read_text(encoding="utf-8") == original_history


def test_bootstrap_real_git_fixture_rejects_existing_ledger_upstream_mismatch(tmp_path):
    fixture = create_bootstrap_git_fixture(
        tmp_path, "https://github.com/BaseOrg/BaseRepo.git"
    )
    first = run_bootstrap_fixture(fixture)
    assert first.returncode == 0, first.stderr
    subprocess.run(
        [
            "git",
            "-C",
            str(fixture),
            "remote",
            "set-url",
            "upstream",
            "https://github.com/OtherOrg/OtherBase.git",
        ],
        check=True,
    )

    mismatch = run_bootstrap_fixture(fixture)
    assert mismatch.returncode == 1
    assert "Base upstream repository does not match upstream remote" in mismatch.stderr
