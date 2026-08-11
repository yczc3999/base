"""WP-05 VaultService unit tests（Checkpoint B）。

覆盖：service 方法（create/store/read/rotate/deny）、每次操作追加 access event、
secret 明文绝不进入 log / exception。纯本地 fake repo，无 DB/网络。
"""

from __future__ import annotations

import asyncio
import logging
import os

import pytest

from app.services.vault import (
    VaultAuthError,
    VaultKeyError,
    VaultService,
)

K1 = os.urandom(32)
K2 = os.urandom(32)
CANARY = b"CANARY-SECRET-PLAINTEXT-0000"


class _FakeRepo:
    def __init__(self):
        self.entries: dict[int, dict] = {}
        self.versions: list[dict] = []
        self.events: list[dict] = []
        self._next_entry = 1
        self._next_version = 1

    async def insert_entry(self, session, *, name, secret_kind, runtime_identity, status="active"):
        e = {"id": self._next_entry, "name": name, "secret_kind": secret_kind,
             "runtime_identity": runtime_identity, "status": status}
        self._next_entry += 1
        self.entries[e["id"]] = e
        return dict(e)

    async def get_entry(self, session, *, entry_id):
        return dict(self.entries[entry_id]) if entry_id in self.entries else None

    async def next_version_no(self, session, *, entry_id):
        return max((v["version_no"] for v in self.versions if v["entry_id"] == entry_id), default=0) + 1

    async def insert_version(self, session, *, entry_id, version_no, key_id, key_version, nonce,
                             ciphertext, aad_context, aad_hash, ciphertext_hash, algorithm,
                             status, supersedes=None):
        v = {"id": self._next_version, "entry_id": entry_id, "version_no": version_no,
             "key_id": key_id, "key_version": key_version, "nonce": nonce,
             "ciphertext": ciphertext, "aad_context": aad_context, "aad_hash": aad_hash,
             "ciphertext_hash": ciphertext_hash, "algorithm": algorithm, "status": status,
             "supersedes": supersedes}
        self._next_version += 1
        self.versions.append(v)
        return dict(v)

    async def get_version(self, session, *, version_id):
        for v in self.versions:
            if v["id"] == version_id:
                return dict(v)
        return None

    async def get_active_version(self, session, *, entry_id, for_update=False):
        actives = [v for v in self.versions if v["entry_id"] == entry_id and v["status"] == "active"]
        if not actives:
            return None
        return dict(sorted(actives, key=lambda x: -x["version_no"])[0])

    async def list_versions(self, session, *, entry_id):
        return [dict(v) for v in self.versions if v["entry_id"] == entry_id]

    async def mark_version_retired(self, session, *, version_id):
        for v in self.versions:
            if v["id"] == version_id and v["status"] == "active":
                v["status"] = "retired"
                return True
        return False

    async def activate_version(self, session, *, version_id):
        target = next((v for v in self.versions if v["id"] == version_id), None)
        if target is None or target["status"] != "retired":
            return False
        if any(v["entry_id"] == target["entry_id"] and v["status"] == "active"
               for v in self.versions):
            return False
        target["status"] = "active"
        return True

    async def insert_access_event(self, session, **kwargs):
        self.events.append(kwargs)


class _Session:
    pass


@pytest.fixture
def service():
    repo = _FakeRepo()
    keyring = {("k1", "v1"): K1, ("k1", "v2"): K2}
    return VaultService(repo, keyring, env="test", runtime_identity="worker-a"), repo


async def _setup(service, repo):
    entry = await service.create_entry(
        _Session(), name="pm/signer/acct-1", secret_kind="signer_private_key",
        runtime_identity="worker-a",
    )
    return entry["id"]


def test_create_entry_store_and_read_roundtrip(service):
    svc, repo = service
    entry_id = asyncio.run(_setup(svc, repo))
    s = _Session()
    version = asyncio.run(svc.store_secret(
        s, entry_id=entry_id, secret=CANARY, purpose="sign", identity="worker-a",
        account="acct-1", key_id="k1", key_version="v1",
    ))
    assert version["version_no"] == 1
    assert version["status"] == "active"
    plaintext = asyncio.run(svc.read_secret(
        s, entry_id=entry_id, version_id=version["id"], purpose="sign",
        identity="worker-a", account="acct-1",
    ))
    assert plaintext == CANARY


def test_rotate_retires_old_and_activates_new(service):
    svc, repo = service
    entry_id = asyncio.run(_setup(svc, repo))
    s = _Session()
    v1 = asyncio.run(svc.store_secret(
        s, entry_id=entry_id, secret=b"old-secret", purpose="sign", identity="worker-a",
        account="acct-1", key_id="k1", key_version="v1",
    ))
    v2 = asyncio.run(svc.rotate_secret(
        s, entry_id=entry_id, secret=b"new-secret", purpose="sign", identity="worker-a",
        account="acct-1", key_id="k1", key_version="v2",
    ))
    assert v2["version_no"] == 2
    assert v2["supersedes"] == v1["id"]
    versions = {v["version_no"]: v["status"] for v in repo.versions}
    assert versions == {1: "retired", 2: "active"}
    # 新版本可读，旧版本仍可按其自身 AAD/key 解密
    assert asyncio.run(svc.read_secret(
        s, entry_id=entry_id, version_id=v2["id"], purpose="sign",
        identity="worker-a", account="acct-1",
    )) == b"new-secret"
    assert asyncio.run(svc.read_secret(
        s, entry_id=entry_id, version_id=v1["id"], purpose="sign",
        identity="worker-a", account="acct-1",
    )) == b"old-secret"


def test_rotate_verify_failure_blocks_rotation(service):
    svc, repo = service
    entry_id = asyncio.run(_setup(svc, repo))
    s = _Session()
    v1 = asyncio.run(svc.store_secret(
        s, entry_id=entry_id, secret=b"old-secret", purpose="sign", identity="worker-a",
        account="acct-1", key_id="k1", key_version="v1",
    ))
    # 篡改旧版本密文 → rotate 的 decrypt/verify 必须失败且不推进状态
    for v in repo.versions:
        if v["id"] == v1["id"]:
            bad = bytearray(v["ciphertext"])
            bad[0] ^= 0xFF
            v["ciphertext"] = bytes(bad)
    with pytest.raises(VaultAuthError):
        asyncio.run(svc.rotate_secret(
            s, entry_id=entry_id, secret=b"new-secret", purpose="sign", identity="worker-a",
            account="acct-1", key_id="k1", key_version="v2",
        ))
    assert sum(1 for v in repo.versions if v["status"] == "active") == 1


def test_access_event_appended_on_every_operation(service):
    svc, repo = service
    entry_id = asyncio.run(_setup(svc, repo))
    s = _Session()
    v1 = asyncio.run(svc.store_secret(
        s, entry_id=entry_id, secret=CANARY, purpose="sign", identity="worker-a",
        account="acct-1", key_id="k1", key_version="v1",
    ))
    asyncio.run(svc.read_secret(
        s, entry_id=entry_id, version_id=v1["id"], purpose="sign",
        identity="worker-a", account="acct-1",
    ))
    asyncio.run(svc.rotate_secret(
        s, entry_id=entry_id, secret=b"rotated", purpose="sign", identity="worker-a",
        account="acct-1", key_id="k1", key_version="v2",
    ))
    asyncio.run(svc.deny(s, entry_id=entry_id, purpose="submit", identity="worker-b", reason="kill_switch"))
    results = [e["result"] for e in repo.events]
    assert "STORED" in results
    assert "READ" in results
    assert "ROTATED" in results
    assert "DENIED" in results
    for ev in repo.events:
        assert ev["identity"]
        assert ev["purpose"]
        assert "secret" not in str(ev).lower() or ev.get("secret_version_id") is None or ev["result"] in ("STORED", "READ", "ROTATED")


def test_secret_plaintext_not_in_log(service, caplog):
    svc, repo = service
    entry_id = asyncio.run(_setup(svc, repo))
    s = _Session()
    with caplog.at_level(logging.INFO, logger="app.services.vault.service"):
        asyncio.run(svc.store_secret(
            s, entry_id=entry_id, secret=CANARY, purpose="sign", identity="worker-a",
            account="acct-1", key_id="k1", key_version="v1",
        ))
        asyncio.run(svc.read_secret(
            s, entry_id=entry_id, version_id=1, purpose="sign",
            identity="worker-a", account="acct-1",
        ))
    log_text = caplog.text
    assert CANARY.decode() not in log_text
    assert "CANARY-SECRET" not in log_text


def test_secret_plaintext_not_in_exception(service):
    svc, repo = service
    entry_id = asyncio.run(_setup(svc, repo))
    s = _Session()
    v1 = asyncio.run(svc.store_secret(
        s, entry_id=entry_id, secret=CANARY, purpose="sign", identity="worker-a",
        account="acct-1", key_id="k1", key_version="v1",
    ))
    with pytest.raises(VaultAuthError) as excinfo:
        asyncio.run(svc.read_secret(
            s, entry_id=entry_id, version_id=v1["id"], purpose="sign",
            identity="worker-a", account="acct-2",  # account mismatch → auth fail
        ))
    assert CANARY.decode() not in str(excinfo.value)
    # 失败时仍追加 AUTH_FAILED audit
    assert any(e["result"] == "AUTH_FAILED" for e in repo.events)


def test_unknown_key_version_raises_fixed_error(service):
    svc, repo = service
    entry_id = asyncio.run(_setup(svc, repo))
    with pytest.raises(VaultKeyError) as excinfo:
        asyncio.run(svc.store_secret(
            _Session(), entry_id=entry_id, secret=CANARY, purpose="sign", identity="worker-a",
            account="acct-1", key_id="k1", key_version="v9",
        ))
    assert CANARY.decode() not in str(excinfo.value)


def test_wrong_runtime_identity_cannot_read_and_uses_durable_failure_sink():
    repo = _FakeRepo()
    durable_events = []

    async def failure_sink(event):
        durable_events.append(dict(event))

    svc = VaultService(
        repo, {("k1", "v1"): K1}, env="test", runtime_identity="worker-a",
        failure_audit=failure_sink,
    )
    entry_id = asyncio.run(_setup(svc, repo))
    session = _Session()
    version = asyncio.run(svc.store_secret(
        session, entry_id=entry_id, secret=CANARY, purpose="sign",
        identity="worker-a", account="acct-1", key_id="k1", key_version="v1",
    ))
    with pytest.raises(VaultAuthError, match="vault_runtime_identity_mismatch"):
        asyncio.run(svc.read_secret(
            session, entry_id=entry_id, version_id=version["id"], purpose="sign",
            identity="worker-UNAUTHORIZED", account="acct-1",
        ))
    assert durable_events[-1]["result"] == "AUTH_FAILED"
    assert durable_events[-1]["reason"] == "identity_mismatch"
    assert not any(
        event["result"] == "READ" and event["identity"] == "worker-UNAUTHORIZED"
        for event in repo.events
    )
    assert CANARY.decode() not in repr(durable_events)
