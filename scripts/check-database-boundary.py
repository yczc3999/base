#!/usr/bin/env python3
"""Validate Base Platform's single database identity contract."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NAME = "base_platform"
EXPECTED_USER = "base_platform_app"


def fail(message: str) -> "NoReturn":
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def config_constants() -> dict[str, str]:
    path = ROOT / "serve/app/config.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                values[target.id] = node.value.value
    return values


def dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def main() -> int:
    constants = config_constants()
    if constants.get("BASE_DATABASE_NAME") != EXPECTED_NAME:
        fail("serve/app/config.py has the wrong Base database name")
    if constants.get("BASE_DATABASE_USER") != EXPECTED_USER:
        fail("serve/app/config.py has the wrong Base database role")

    config_text = (ROOT / "serve/app/config.py").read_text(encoding="utf-8")
    required_fields = {
        "DATABASE_NAME": "BASE_DATABASE_NAME",
        "DATABASE_USER": "BASE_DATABASE_USER",
    }
    for field, constant in required_fields.items():
        pattern = rf"^\s*{field}:\s*str\s*=\s*{constant}\s*$"
        if not re.search(pattern, config_text, flags=re.MULTILINE):
            fail(f"{field} must default through {constant}")

    example = dotenv_values(ROOT / "serve/.env.example")
    if example.get("DATABASE_NAME") != EXPECTED_NAME:
        fail("serve/.env.example DATABASE_NAME is out of contract")
    if example.get("DATABASE_USER") != EXPECTED_USER:
        fail("serve/.env.example DATABASE_USER is out of contract")
    if example.get("DATABASE_PASSWORD"):
        fail("serve/.env.example must not contain a database password")

    provision = (ROOT / "scripts/provision-base-database.sh").read_text(encoding="utf-8")
    for assignment in (
        f"readonly DB_NAME={EXPECTED_NAME}",
        f"readonly DB_ROLE={EXPECTED_USER}",
    ):
        if assignment not in provision:
            fail(f"provisioning script is missing fixed identity: {assignment}")

    shared_library = "scripts/lib/provision-postgres-database.sh"
    bootstrap_path = ROOT / "scripts/bootstrap-project.sh"
    bootstrap = bootstrap_path.read_text(encoding="utf-8")
    for relative, text in (
        ("scripts/provision-base-database.sh", provision),
        ("scripts/bootstrap-project.sh", bootstrap),
    ):
        if shared_library not in text or "provision_postgres_database" not in text:
            fail(f"{relative} must use the shared database provisioning core")
    for required in (
        "remote get-url upstream",
        "project remote must differ",
        "base_platform_app",
        "PROJECT.md",
        "BOOTSTRAP_SKIP_INSTALL",
    ):
        if required not in bootstrap:
            fail(f"project bootstrap is missing {required!r}")

    plan = subprocess.run(
        [str(bootstrap_path), "fixture_project", "Fixture Project", "--plan"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if plan.returncode or "fixture_project_app" not in plan.stdout:
        fail(plan.stderr.strip() or "project bootstrap plan check failed")

    boundary = (ROOT / "serve/docs/database-boundary.md").read_text(encoding="utf-8")
    for required in (EXPECTED_NAME, EXPECTED_USER, "REVOKE ALL ON DATABASE", "下游"):
        if required not in boundary:
            fail(f"database boundary document is missing {required!r}")

    ledgers = (
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "CHANGELOG.md",
        "UPSTREAM.md",
        "tofix.md",
        "serve/README.md",
        "serve/docs/project-bootstrap.md",
    )
    for relative in ledgers:
        text = (ROOT / relative).read_text(encoding="utf-8")
        if EXPECTED_NAME not in text or EXPECTED_USER not in text:
            fail(f"{relative} does not record the canonical database identity")

    print(f"Database boundary OK: {EXPECTED_USER}@{EXPECTED_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
