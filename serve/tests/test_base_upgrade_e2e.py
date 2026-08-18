from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/test-base-upgrade-e2e.sh"


def test_dynamic_git_upgrade_receiver_e2e():
    result = subprocess.run(
        [str(SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert "base upgrade dynamic Git E2E: PASS" in result.stdout
