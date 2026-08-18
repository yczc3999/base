from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/test-base-upgrade-e2e.sh"


def test_dynamic_git_upgrade_receiver_e2e_ignores_callers_merge_head(tmp_path):
    outer = tmp_path / "outer-merge"
    subprocess.run(["git", "init", "-q", str(outer)], check=True)
    merge_head = outer / ".git" / "MERGE_HEAD"
    merge_head.write_text("a" * 40 + "\n", encoding="utf-8")
    result = subprocess.run(
        [str(SCRIPT)],
        cwd=outer,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert "base upgrade dynamic Git E2E: PASS" in result.stdout
    assert merge_head.is_file(), "the harness must not inspect or alter its caller's merge"
