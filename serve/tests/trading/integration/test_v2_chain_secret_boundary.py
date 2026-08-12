"""WP-06 secret, artifact, and fake-egress boundary acceptance."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.trading.integration.wp06_runtime_fixture import (
    PK,
    build_runtime,
    query,
    request,
    seed_authority,
    seed_real_position_lineage,
    seed_settlement,
    upgrade,
)

SERVE_DIR = Path(__file__).resolve().parents[3]
_SENSITIVE_PATTERNS = (
    re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    re.compile(
        r"(?i)(?:api[_-]?key|passphrase|builder[_-]?secret|relayer[_-]?secret)"
        r"\s*[:=]\s*['\"][A-Za-z0-9+/=_\-]{20,}"
    ),
    re.compile(r"signature\s*[:=]\s*['\"]0x[a-f0-9]{128}['\"]"),
)
_PRODUCTION_FILES = (
    "alembic/versions/b1000052_v2_0052_chain_settlement.py",
    "app/services/polymarket/polygon_driver.py",
    "app/services/polymarket/relayer_driver.py",
    "app/services/polymarket/geoblock_driver.py",
    "app/logics/trading/settlement.py",
    "app/repositories/trading/settlement.py",
    "app/schemas/polymarket/chain.py",
    "app/domain/trading/payout.py",
    "runtimes/trading/evaluation.py",
)


def test_wp06_production_source_and_offline_sql_have_no_secret_plaintext():
    hits = []
    for rel in _PRODUCTION_FILES:
        material = (SERVE_DIR / rel).read_text(encoding="utf-8")
        for pattern in _SENSITIVE_PATTERNS:
            hits.extend((rel, pattern.pattern, match.start()) for match in pattern.finditer(material))
    assert hits == []

    completed = subprocess.run(
        [str(SERVE_DIR / ".venv/bin/alembic"), "upgrade", "b1000052", "--sql"],
        cwd=SERVE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-1000:]
    assert all(not pattern.search(completed.stdout) for pattern in _SENSITIVE_PATTERNS)


@pytest.mark.anyio
async def test_real_runtime_flow_persists_no_credentials_and_uses_fake_egress_only(
    temp_pg_db, tmp_path
):
    url = temp_pg_db.url
    upgrade(url)
    ids = seed_authority(url)
    runtime, store, calls, engine = build_runtime(url, tmp_path / "cas", ids)
    ids = await seed_real_position_lineage(runtime._sessions, url, ids)
    try:
        await seed_settlement(runtime, store, ids)
        submitted = await runtime.submit_redeem(request(ids, "secret"))
        finalized = await runtime.recover_chain_operation(
            operation_id=submitted["operation_id"], fencing_token=1
        )
        assert finalized["status"] == "FINALIZED"

        # The injected transports exercised nonce/sign/submit/status/RPC/geoblock,
        # while every driver remained fixture-marked and counted zero real calls.
        assert runtime._polygon.fake_calls > 0
        assert runtime._relayer.fake_calls > 0
        assert runtime._geoblock.fixture_only is True
        assert runtime._geoblock.transport_calls > 0
        assert runtime._polygon.real_calls == 0
        assert runtime._relayer.real_calls == 0
        assert calls["submit_calls"] == 1

        permission = query(
            url,
            "SELECT authorized_capital FROM trading.capital_permission_manifests "
            "WHERE id=:id",
            {"id": ids["permission"]},
        )
        assert permission == [{"authorized_capital": 0}]

        # Scan all persisted text/json facts plus artifact bytes for exact fixture
        # credentials. Public signer/wallet addresses are intentionally not secrets.
        forbidden = (
            PK.lower(),
            "fixture-key",
            "fixture-builder",
            "fixture-passphrase",
            "pm-v2/fixture/builder-secret/v1",
        )
        text_facts = query(
            url,
            "SELECT table_name,column_name FROM information_schema.columns "
            "WHERE table_schema='trading' AND data_type IN "
            "('text','json','jsonb','character varying')",
        )
        db_hits = []
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool

        sync_engine = create_engine(url, poolclass=NullPool)
        try:
            with sync_engine.connect() as connection:
                for fact in text_facts:
                    values = connection.execute(text(
                        f'SELECT "{fact["column_name"]}"::text AS value '
                        f'FROM trading."{fact["table_name"]}" '
                        f'WHERE "{fact["column_name"]}" IS NOT NULL'
                    )).scalars()
                    for value in values:
                        lowered = str(value).lower()
                        for marker in forbidden:
                            if marker in lowered:
                                db_hits.append((fact["table_name"], fact["column_name"], marker))
        finally:
            sync_engine.dispose()
        assert db_hits == []

        artifact_hits = []
        for path in (tmp_path / "cas").rglob("*"):
            if path.is_file():
                material = path.read_bytes().lower()
                for marker in forbidden:
                    if marker.encode() in material:
                        artifact_hits.append((str(path), marker))
        assert artifact_hits == []

        attempts = query(
            url,
            "SELECT driver,endpoint,request_hash,response_hash FROM "
            "trading.external_call_attempts ORDER BY id",
        )
        assert attempts
        assert all(len(row["request_hash"]) == len(row["response_hash"]) == 64 for row in attempts)
    finally:
        await engine.dispose()
