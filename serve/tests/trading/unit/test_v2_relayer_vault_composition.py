"""WP-06 production Relayer composition: Vault refs, bounded window, zero egress."""

from __future__ import annotations

import base64
import inspect
import json

import pytest
from eth_account import Account

from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.relayer_driver import fixture_relayer_transport
from app.services.polymarket.service import (
    PolymarketService,
    RelayerVaultRefs,
    VaultSecretVersionRef,
)
from app.services.vault import VaultAuthError
from app.services.vault import VaultService


class _Session:
    def __init__(self, state):
        self.state = state

    async def commit(self):
        self.state["commits"] += 1

    async def rollback(self):
        self.state["rollbacks"] += 1

    async def close(self):
        self.state["closes"] += 1


class _Sessions:
    def __init__(self):
        self.state = {"commits": 0, "rollbacks": 0, "closes": 0}

    def __call__(self):
        return _Session(self.state)


class _Vault:
    durable_failure_audit_configured = True

    def __init__(self, values, *, accepted_identity="chain-worker"):
        self.values = values
        self.accepted_identity = accepted_identity
        self.events = []

    async def read_secret(
        self, session, *, entry_id, version_id, purpose, identity, account=None
    ):
        if identity != self.accepted_identity:
            self.events.append(("AUTH_FAILED", entry_id, version_id, purpose))
            raise VaultAuthError("vault_runtime_identity_mismatch")
        self.events.append(("READ", entry_id, version_id, purpose, account))
        return self.values[(entry_id, version_id)]

    async def audit_consumer_failure(
        self, *, entry_id, version_id, identity, purpose, reason
    ):
        self.events.append(("READ_FAILED", entry_id, version_id, purpose, reason))


def _refs():
    return RelayerVaultRefs(
        signer=VaultSecretVersionRef(11, 101),
        builder=VaultSecretVersionRef(12, 102),
        account_context="fixture-account",
    )


def _builder_secret():
    return json.dumps({
        "relayer_api_key": "relayer-key",
        "builder_api_key": "builder-key",
        "builder_passphrase": "builder-passphrase",
        "builder_secret_base64": base64.urlsafe_b64encode(b"b" * 32).decode(),
    }, separators=(",", ":")).encode()


@pytest.mark.anyio
async def test_relayer_factory_reads_exact_vault_refs_in_independent_audited_windows():
    account = Account.from_key(b"a" * 32)
    vault = _Vault({(11, 101): b"a" * 32, (12, 102): _builder_secret()})
    sessions = _Sessions()
    wire = {"submit_headers": None}

    @fixture_relayer_transport
    async def transport(method, path, *, params=None, body=None, headers=None):
        if path == "/v1/account/transactions/params":
            return 200, json.dumps({"address": account.address.lower(), "nonce": "7"}).encode()
        if path == "/submit":
            wire["submit_headers"] = dict(headers)
            return 200, json.dumps({
                "transactionID": "fixture-transaction-1", "state": "STATE_NEW"
            }).encode()
        raise AssertionError("unexpected relayer route")

    service = PolymarketService(relayer_transport=transport)
    async with service.relayer_vault_window(
        sessions_factory=sessions,
        vault_service=vault,
        refs=_refs(),
        runtime_identity="chain-worker",
        expected_signing_identity=account.address,
        trusted_time_provider=lambda: 1_700_000_000,
    ) as driver:
        prepared = await driver.prepare_batch(
            from_address=account.address,
            to_address="0x00000000000Fb5C9ADea0298D729A0CB3823Cc07",
            deposit_wallet="0x00000000000Fb5C9ADea0298D729A0CB3823Cc07",
            calls=[{
                "target": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
                "value": "0",
                "data": "0x",
            }],
            metadata="pm-v2-settlement/v1",
        )
        outcome = await driver.submit_prepared(prepared)
        assert outcome.cls == "SUBMITTED" and outcome.state == "NEW"
        assert wire["submit_headers"]["POLY_BUILDER_API_KEY"] == "builder-key"
        assert wire["submit_headers"]["POLY_BUILDER_PASSPHRASE"] == "builder-passphrase"

    assert vault.events == [
        ("READ", 11, 101, "relayer_sign", "fixture-account"),
        ("READ", 12, 102, "relayer_builder", "fixture-account"),
    ]
    assert sessions.state == {"commits": 2, "rollbacks": 0, "closes": 2}
    # Driver boundary redacts the inner closed-window reason.
    with pytest.raises(PolymarketError, match="relayer_nonce_auth_failure"):
        await driver.get_nonce(account.address)


@pytest.mark.anyio
async def test_relayer_factory_rejection_is_audited_and_rolls_back_only_that_read():
    account = Account.from_key(b"a" * 32)
    vault = _Vault({}, accepted_identity="other-worker")
    sessions = _Sessions()
    service = PolymarketService()

    with pytest.raises(VaultAuthError, match="vault_runtime_identity_mismatch"):
        async with service.relayer_vault_window(
            sessions_factory=sessions,
            vault_service=vault,
            refs=_refs(),
            runtime_identity="chain-worker",
            expected_signing_identity=account.address,
            trusted_time_provider=lambda: 1_700_000_000,
        ):
            pass
    assert vault.events == [("AUTH_FAILED", 11, 101, "relayer_sign")]
    assert sessions.state == {"commits": 0, "rollbacks": 1, "closes": 1}


@pytest.mark.anyio
async def test_relayer_factory_decode_failure_has_independent_redacted_audit():
    account = Account.from_key(b"a" * 32)
    vault = _Vault({(11, 101): b"a" * 32, (12, 102): b"plaintext-canary-not-json"})
    sessions = _Sessions()

    with pytest.raises(PolymarketError, match="relayer_builder_credential_invalid") as error:
        async with PolymarketService().relayer_vault_window(
            sessions_factory=sessions,
            vault_service=vault,
            refs=_refs(),
            runtime_identity="chain-worker",
            expected_signing_identity=account.address,
            trusted_time_provider=lambda: 1_700_000_000,
        ):
            pass
    assert "plaintext-canary" not in str(error.value)
    assert vault.events[-1] == (
        "READ_FAILED", 12, 102, "relayer_builder", "credential_shape_invalid"
    )
    assert sessions.state == {"commits": 2, "rollbacks": 0, "closes": 2}


def test_relayer_production_factory_has_no_raw_credential_parameters():
    parameters = inspect.signature(PolymarketService.relayer_vault_window).parameters
    assert {"signer", "nonce_auth_provider", "builder_auth_provider"}.isdisjoint(parameters)
    with pytest.raises(ValueError, match="vault_secret_entry_ref_invalid"):
        VaultSecretVersionRef(0, 1)


@pytest.mark.anyio
async def test_vault_consumer_decode_failure_uses_independent_redacted_sink():
    events = []

    async def sink(event):
        events.append(dict(event))

    vault = VaultService(
        object(), {}, runtime_identity="chain-worker", failure_audit=sink
    )
    assert vault.durable_failure_audit_configured is True
    await vault.audit_consumer_failure(
        entry_id=12,
        version_id=102,
        identity="chain-worker",
        purpose="relayer_builder",
        reason="credential_shape_invalid",
    )
    assert events == [{
        "operation": "read",
        "entry_id": 12,
        "secret_version_id": 102,
        "identity": "chain-worker",
        "purpose": "relayer_builder",
        "key_version": None,
        "result": "READ_FAILED",
        "reason": "credential_shape_invalid",
    }]
    with pytest.raises(ValueError, match="vault_consumer_failure_reason_invalid"):
        await vault.audit_consumer_failure(
            entry_id=12,
            version_id=102,
            identity="chain-worker",
            purpose="relayer_builder",
            reason="secret=value",
        )
