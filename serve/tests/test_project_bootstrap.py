from pathlib import Path
import subprocess


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
