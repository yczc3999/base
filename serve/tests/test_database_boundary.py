from pathlib import Path
import subprocess
import sys

from app.config import BASE_DATABASE_NAME, BASE_DATABASE_USER, Settings


ROOT = Path(__file__).resolve().parents[2]


def test_database_identity_defaults(monkeypatch):
    monkeypatch.delenv("DATABASE_NAME", raising=False)
    monkeypatch.delenv("DATABASE_USER", raising=False)
    settings = Settings(_env_file=None)

    assert BASE_DATABASE_NAME == "base_platform"
    assert BASE_DATABASE_USER == "base_platform_app"
    assert settings.DATABASE_NAME == BASE_DATABASE_NAME
    assert settings.DATABASE_USER == BASE_DATABASE_USER


def test_repository_database_boundary_contract():
    result = subprocess.run(
        [sys.executable, "scripts/check-database-boundary.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "base_platform_app@base_platform" in result.stdout
