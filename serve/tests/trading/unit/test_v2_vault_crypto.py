"""WP-05 vault AES-256-GCM envelope crypto unit tests（Checkpoint B）。

覆盖：roundtrip、nonce uniqueness、AAD/identity/account/purpose mismatch 认证失败、
ciphertext/tag tamper、未知 key version、rotation/old version、concurrent activation、
deny audit。纯本地，无网络/DB。
"""

from __future__ import annotations

import os

import pytest

from app.services.vault import (
    VaultAuthError,
    VaultCryptoError,
    VaultKeyError,
    VaultService,
    build_aad,
    decrypt,
    encrypt,
    new_nonce,
    resolve_key,
)

K1 = os.urandom(32)
K2 = os.urandom(32)


def _aad(**overrides):
    fields = {
        "env": "test",
        "entry": "entry-1",
        "secret_kind": "l2_secret",
        "account": "acct-1",
        "runtime_identity": "worker-a",
        "purpose": "sign",
        "secret_version": 1,
        "key_id": "k1",
        "key_version": "v1",
    }
    fields.update(overrides)
    return build_aad(**fields)


def test_roundtrip():
    nonce = new_nonce()
    aad = _aad()
    plaintext = b"super-secret-value-12345"
    packed = encrypt(plaintext, key=K1, key_id="k1", key_version="v1", nonce=nonce, aad=aad)
    out = decrypt(packed, key=K1, key_id="k1", key_version="v1", nonce=nonce, aad=aad)
    assert out == plaintext


def test_nonce_uniqueness_and_different_ciphertext():
    aad = _aad()
    nonce1 = new_nonce()
    nonce2 = new_nonce()
    assert nonce1 != nonce2
    c1 = encrypt(b"same", key=K1, key_id="k1", key_version="v1", nonce=nonce1, aad=aad)
    c2 = encrypt(b"same", key=K1, key_id="k1", key_version="v1", nonce=nonce2, aad=aad)
    assert c1 != c2


def test_aad_account_mismatch_fails():
    nonce = new_nonce()
    aad = _aad(account="acct-1")
    packed = encrypt(b"secret", key=K1, key_id="k1", key_version="v1", nonce=nonce, aad=aad)
    with pytest.raises(VaultAuthError):
        decrypt(packed, key=K1, key_id="k1", key_version="v1", nonce=nonce, aad=_aad(account="other"))


def test_aad_identity_mismatch_fails():
    nonce = new_nonce()
    packed = encrypt(b"secret", key=K1, key_id="k1", key_version="v1",
                    nonce=nonce, aad=_aad(runtime_identity="worker-a"))
    with pytest.raises(VaultAuthError):
        decrypt(packed, key=K1, key_id="k1", key_version="v1", nonce=nonce,
                aad=_aad(runtime_identity="worker-b"))


def test_aad_purpose_mismatch_fails():
    nonce = new_nonce()
    packed = encrypt(b"secret", key=K1, key_id="k1", key_version="v1",
                    nonce=nonce, aad=_aad(purpose="sign"))
    with pytest.raises(VaultAuthError):
        decrypt(packed, key=K1, key_id="k1", key_version="v1", nonce=nonce,
                aad=_aad(purpose="withdraw"))


def test_ciphertext_tamper_fails():
    nonce = new_nonce()
    aad = _aad()
    packed = bytearray(encrypt(b"tamper-me-secret", key=K1, key_id="k1", key_version="v1",
                               nonce=nonce, aad=aad))
    packed[3] ^= 0xFF
    with pytest.raises(VaultAuthError):
        decrypt(bytes(packed), key=K1, key_id="k1", key_version="v1", nonce=nonce, aad=aad)


def test_tag_tamper_fails():
    nonce = new_nonce()
    aad = _aad()
    packed = bytearray(encrypt(b"tamper-tag-secret", key=K1, key_id="k1", key_version="v1",
                               nonce=nonce, aad=aad))
    packed[-1] ^= 0xFF  # flip last byte of the 128-bit tag
    with pytest.raises(VaultAuthError):
        decrypt(bytes(packed), key=K1, key_id="k1", key_version="v1", nonce=nonce, aad=aad)


def test_truncated_ciphertext_fails():
    nonce = new_nonce()
    aad = _aad()
    packed = encrypt(b"secret", key=K1, key_id="k1", key_version="v1", nonce=nonce, aad=aad)
    with pytest.raises(VaultAuthError):
        decrypt(packed[:10], key=K1, key_id="k1", key_version="v1", nonce=nonce, aad=aad)


def test_unknown_key_version_fails():
    keyring = {("k1", "v1"): K1}
    assert resolve_key(keyring, "k1", "v1") == K1
    with pytest.raises(VaultKeyError):
        resolve_key(keyring, "k1", "v2")
    with pytest.raises(VaultKeyError):
        resolve_key(keyring, "k2", "v1")


def test_rotation_old_version_still_decryptable():
    nonce1 = new_nonce()
    aad1 = _aad(secret_version=1, key_version="v1")
    old_packed = encrypt(b"old-secret", key=K1, key_id="k1", key_version="v1",
                         nonce=nonce1, aad=aad1)
    nonce2 = new_nonce()
    aad2 = _aad(secret_version=2, key_version="v2")
    new_packed = encrypt(b"new-secret", key=K2, key_id="k1", key_version="v2",
                         nonce=nonce2, aad=aad2)
    assert decrypt(old_packed, key=K1, key_id="k1", key_version="v1",
                   nonce=nonce1, aad=aad1) == b"old-secret"
    assert decrypt(new_packed, key=K2, key_id="k1", key_version="v2",
                   nonce=nonce2, aad=aad2) == b"new-secret"
    # wrong old key on new ciphertext must fail
    with pytest.raises(VaultAuthError):
        decrypt(new_packed, key=K1, key_id="k1", key_version="v1",
                nonce=nonce2, aad=aad2)


# ---------- service-level：concurrent activation / deny audit ----------


class _FakeRepo:
    """In-memory vault repo；``activate_version`` 强制单 active（等价 DB deferred trigger）。"""

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
            return False  # 单 active 冲突 → CAS 失败
        target["status"] = "active"
        return True

    async def insert_access_event(self, session, **kwargs):
        self.events.append(kwargs)


class _Session:
    """占位 session（fake repo 不依赖真实 session）。"""


@pytest.fixture
def svc():
    repo = _FakeRepo()
    keyring = {("k1", "v1"): K1, ("k1", "v2"): K2}
    return VaultService(repo, keyring, env="test", runtime_identity="worker-a"), repo


async def _make_entry(svc):
    entry = await svc.create_entry(
        _Session(), name="pm/signer/acct-1", secret_kind="signer_private_key",
        runtime_identity="worker-a",
    )
    return entry["id"]


def test_service_concurrent_activation_single_active(svc):
    service, repo = svc
    import asyncio

    entry_id = asyncio.run(_make_entry(service))
    s = _Session()
    asyncio.run(service.store_secret(
        s, entry_id=entry_id, secret=b"v1-secret", purpose="sign", identity="worker-a",
        account="acct-1", key_id="k1", key_version="v1",
    ))
    asyncio.run(service.rotate_secret(
        s, entry_id=entry_id, secret=b"v2-secret", purpose="sign", identity="worker-a",
        account="acct-1", key_id="k1", key_version="v2",
    ))
    versions = repo.versions
    assert [v["status"] for v in sorted(versions, key=lambda x: x["version_no"])] == ["retired", "active"]
    active_count = sum(1 for v in versions if v["status"] == "active")
    assert active_count == 1
    # 试图把已 retired 的旧版本重新激活：已有 active → CAS 失败
    old_id = next(v["id"] for v in versions if v["version_no"] == 1)
    assert asyncio.run(repo.activate_version(s, version_id=old_id)) is False
    assert sum(1 for v in repo.versions if v["status"] == "active") == 1


def test_service_deny_appends_audit(svc):
    import asyncio

    service, repo = svc
    entry_id = asyncio.run(_make_entry(service))
    asyncio.run(service.deny(
        _Session(), entry_id=entry_id, purpose="submit", identity="worker-b", reason="kill_switch",
    ))
    assert len(repo.events) == 1
    ev = repo.events[0]
    assert ev["result"] == "DENIED"
    assert ev["result_reason"] == "kill_switch"
    assert ev["identity"] == "worker-b"
    assert "secret" not in str(ev).lower() or ev["secret_version_id"] is None


def test_service_unknown_key_version_on_store(svc):
    import asyncio

    service, _ = svc
    entry_id = asyncio.run(_make_entry(service))
    with pytest.raises(VaultKeyError):
        asyncio.run(service.store_secret(
            _Session(), entry_id=entry_id, secret=b"x", purpose="sign", identity="worker-a",
            account="acct-1", key_id="k9", key_version="v9",
        ))


def test_service_account_mismatch_on_read_fails(svc):
    import asyncio

    service, _ = svc
    entry_id = asyncio.run(_make_entry(service))
    s = _Session()
    v = asyncio.run(service.store_secret(
        s, entry_id=entry_id, secret=b"bound-to-acct-1", purpose="sign", identity="worker-a",
        account="acct-1", key_id="k1", key_version="v1",
    ))
    with pytest.raises(VaultAuthError):
        asyncio.run(service.read_secret(
            s, entry_id=entry_id, version_id=v["id"], purpose="sign",
            identity="worker-a", account="acct-2",
        ))
