from pathlib import Path

from app.version import BASE_VERSION


def test_backend_version_matches_root_version() -> None:
    root_version = (Path(__file__).resolve().parents[2] / "VERSION").read_text(
        encoding="utf-8"
    ).strip()
    assert BASE_VERSION == root_version
