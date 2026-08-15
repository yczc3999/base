"""后台运行时配置单测（模型 API key vault 存取 + pipeline AI 开关）。

覆盖：
- keyring loader：env:// hex/base64、file://、缺失/非法 → VaultKeyError；
- resolver 优先级：vault 命中 / 无 entry 回退 None（transport env 兜底）/
  全无 → ProviderError(model_credential_missing)；
- ``list_items`` 输出任何字段不含明文 key（含 ``sk-`` 假 key 断言，只回 last4）；
- flag 读取 DB 命中 / 回退 policy 默认（logic 与 pipeline ``_resolve_ai_enabled``）；
- controller body 校验：未知 provider 400、空 key 400、越界长度 400、
  keyring 未配置 503、extra=forbid。
纯本地 fake repo/session，无 DB/网络。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os

import pytest

from app.services.model_gateway import credentials
from app.services.model_gateway.contracts import ProviderError
from app.services.model_gateway.credentials import (
    MODEL_GATEWAY_IDENTITY,
    build_model_gateway_vault,
    resolve_model_credential,
)
from app.services.model_gateway.service import ModelGatewayService
from app.services.model_gateway.transport import build_transport_factory
from app.services.vault import VaultKeyError, VaultService
from app.services.vault.keyring_loader import load_keyring

MASTER_KEY = bytes.fromhex("11" * 32)
CANARY_KEY = "sk-test-FAKEKEY-canary-9876"


# ---------------- fakes ----------------


class _FakeVaultRepo:
    """内存 vault repo：覆盖 VaultService 全部依赖 + entry name 查询/生命周期。"""

    def __init__(self):
        self.entries: dict[int, dict] = {}
        self.versions: list[dict] = []
        self.events: list[dict] = []
        self._next_entry = 1
        self._next_version = 1

    async def insert_entry(self, session, *, name, secret_kind, runtime_identity, status="active"):
        e = {"id": self._next_entry, "name": name, "secret_kind": secret_kind,
             "runtime_identity": runtime_identity, "status": status, "created_at": "t0"}
        self._next_entry += 1
        self.entries[e["id"]] = e
        return dict(e)

    async def get_entry(self, session, *, entry_id):
        return dict(self.entries[entry_id]) if entry_id in self.entries else None

    async def get_entry_by_name(self, session, *, name):
        for e in self.entries.values():
            if e["name"] == name:
                return dict(e)
        return None

    async def next_version_no(self, session, *, entry_id):
        return max(
            (v["version_no"] for v in self.versions if v["entry_id"] == entry_id), default=0
        ) + 1

    async def insert_version(self, session, *, entry_id, version_no, key_id, key_version, nonce,
                             ciphertext, aad_context, aad_hash, ciphertext_hash, algorithm,
                             status, supersedes=None):
        v = {"id": self._next_version, "entry_id": entry_id, "version_no": version_no,
             "key_id": key_id, "key_version": key_version, "nonce": nonce,
             "ciphertext": ciphertext, "aad_context": aad_context, "aad_hash": aad_hash,
             "ciphertext_hash": ciphertext_hash, "algorithm": algorithm, "status": status,
             "supersedes": supersedes, "created_at": f"t{self._next_version}"}
        self._next_version += 1
        self.versions.append(v)
        return dict(v)

    async def get_version(self, session, *, version_id):
        for v in self.versions:
            if v["id"] == version_id:
                return dict(v)
        return None

    async def get_active_version(self, session, *, entry_id, for_update=False):
        actives = [v for v in self.versions
                   if v["entry_id"] == entry_id and v["status"] == "active"]
        if not actives:
            return None
        return dict(sorted(actives, key=lambda x: -x["version_no"])[0])

    async def mark_version_retired(self, session, *, version_id):
        for v in self.versions:
            if v["id"] == version_id and v["status"] == "active":
                v["status"] = "retired"
                return True
        return False

    async def mark_entry_disabled(self, session, *, entry_id):
        e = self.entries.get(entry_id)
        if e is None or e["status"] != "active":
            return False
        e["status"] = "disabled"
        return True

    async def mark_entry_active(self, session, *, entry_id):
        e = self.entries.get(entry_id)
        if e is None or e["status"] != "disabled":
            return False
        e["status"] = "active"
        return True

    async def insert_access_event(self, session, **kwargs):
        self.events.append(kwargs)


class _FakeFlagRepo:
    """内存 runtime_flags + append-only events。"""

    def __init__(self):
        self.flags: dict[str, dict] = {}
        self.events: list[dict] = []

    async def get_flag(self, session, *, flag_key):
        row = self.flags.get(flag_key)
        return dict(row) if row else None

    async def upsert_flag(self, session, *, flag_key, flag_value, actor):
        row = {"flag_key": flag_key, "flag_value": flag_value,
               "updated_by": actor, "updated_at": "t1"}
        self.flags[flag_key] = row
        return dict(row)

    async def insert_flag_event(self, session, *, flag_key, old_value, new_value, actor):
        self.events.append({"flag_key": flag_key, "old_value": old_value,
                            "new_value": new_value, "actor": actor})


class _Session:
    async def commit(self):
        pass


class _Auth:
    username = "tester"


def _make_vault(repo: _FakeVaultRepo) -> VaultService:
    return VaultService(
        repo, {("master", "v1"): MASTER_KEY},
        env="test", runtime_identity=MODEL_GATEWAY_IDENTITY,
    )


def _make_logic(monkeypatch, *, with_vault=True):
    """构造 RuntimeConfigLogic + 底层 fake；vault_repo 全局替换为 fake。"""
    from app.logics.trading.runtime_config import RuntimeConfigLogic

    vault_repo = _FakeVaultRepo()
    flag_repo = _FakeFlagRepo()
    monkeypatch.setattr(credentials, "vault_repo", vault_repo)
    vault = _make_vault(vault_repo) if with_vault else None
    return RuntimeConfigLogic(vault=vault, repo=flag_repo), vault_repo, flag_repo


# ---------------- keyring loader ----------------


def test_keyring_loader_env_hex_and_base64(monkeypatch):
    monkeypatch.setenv("PM_V2_TEST_KR_HEX", MASTER_KEY.hex())
    assert load_keyring("env://PM_V2_TEST_KR_HEX") == {("master", "v1"): MASTER_KEY}
    monkeypatch.setenv("PM_V2_TEST_KR_B64", base64.b64encode(MASTER_KEY).decode())
    assert load_keyring("env://PM_V2_TEST_KR_B64") == {("master", "v1"): MASTER_KEY}


def test_keyring_loader_file(tmp_path):
    path = tmp_path / "keyring"
    path.write_text(base64.b64encode(MASTER_KEY).decode())
    assert load_keyring(f"file://{path}") == {("master", "v1"): MASTER_KEY}
    raw = tmp_path / "keyring-raw"
    raw.write_bytes(MASTER_KEY)
    assert load_keyring(f"file://{raw}") == {("master", "v1"): MASTER_KEY}


def test_keyring_loader_fail_closed(monkeypatch, tmp_path):
    monkeypatch.delenv("PM_V2_TEST_KR_MISSING", raising=False)
    with pytest.raises(VaultKeyError):
        load_keyring("")
    with pytest.raises(VaultKeyError):
        load_keyring("env://PM_V2_TEST_KR_MISSING")
    with pytest.raises(VaultKeyError):
        load_keyring("kms://arn:aws:kms:xxx")
    monkeypatch.setenv("PM_V2_TEST_KR_BAD", "not-a-key")
    with pytest.raises(VaultKeyError) as excinfo:
        load_keyring("env://PM_V2_TEST_KR_BAD")
    assert "not-a-key" not in str(excinfo.value)
    with pytest.raises(VaultKeyError):
        load_keyring(f"file://{tmp_path}/absent")


# ---------------- resolver 优先级 ----------------


def test_resolve_credential_vault_hit(monkeypatch):
    logic, _vr, _fr = _make_logic(monkeypatch)
    s = _Session()
    asyncio.run(logic.set_credential(s, provider="deepseek", api_key=CANARY_KEY, actor="a"))
    resolved = asyncio.run(
        resolve_model_credential(s, logic._vault, "deepseek")
    )
    assert resolved == CANARY_KEY


def test_resolve_credential_no_entry_or_no_vault_returns_none(monkeypatch):
    monkeypatch.setattr(credentials, "vault_repo", _FakeVaultRepo())
    s = _Session()
    assert asyncio.run(resolve_model_credential(s, None, "deepseek")) is None
    vault = _make_vault(_FakeVaultRepo())
    assert asyncio.run(resolve_model_credential(s, vault, "deepseek")) is None


def test_transport_credential_override_priority(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        text = "{}"

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["headers"] = headers
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setenv("PM_V2_XAI_API_KEY", "sk-env-fallback")
    factory = build_transport_factory(credential_overrides={"xai": "sk-db-override"})
    asyncio.run(factory("xai")(endpoint="/v1/chat/completions", headers={}, json={}))
    assert captured["headers"]["Authorization"] == "Bearer sk-db-override"
    factory_env = build_transport_factory()
    asyncio.run(factory_env("xai")(endpoint="/v1/chat/completions", headers={}, json={}))
    assert captured["headers"]["Authorization"] == "Bearer sk-env-fallback"


def test_transport_no_credential_anywhere_fail_closed(monkeypatch):
    monkeypatch.delenv("PM_V2_DEEPSEEK_API_KEY", raising=False)
    factory = build_transport_factory(credential_overrides={"xai": "sk-other"})
    with pytest.raises(ProviderError, match="model_credential_missing"):
        factory("deepseek")


def test_service_credential_resolver_wiring():
    async def _no_resolver_case():
        svc = ModelGatewayService(lambda _p: None)
        assert await svc._resolve_credential(_Session(), "deepseek") is None

    async def _resolver_case():
        async def resolver(session, provider):
            return f"sk-db-{provider}"

        svc = ModelGatewayService(lambda _p: None, credential_resolver=resolver)
        assert await svc._resolve_credential(_Session(), "kimi") == "sk-db-kimi"

    asyncio.run(_no_resolver_case())
    asyncio.run(_resolver_case())


def test_build_model_gateway_vault_unconfigured_returns_none():
    from types import SimpleNamespace

    assert build_model_gateway_vault(SimpleNamespace(PM_V2_VAULT_KEYRING_REF="")) is None
    assert build_model_gateway_vault(
        SimpleNamespace(PM_V2_VAULT_KEYRING_REF="env://PM_V2_TEST_KR_ABSENT")
    ) is None


# ---------------- list_items 掩码 ----------------


def test_list_items_masks_and_never_leaks_plaintext(monkeypatch):
    logic, _vr, _fr = _make_logic(monkeypatch)
    monkeypatch.setenv("PM_V2_KIMI_API_KEY", "sk-env-kimi")
    monkeypatch.delenv("PM_V2_XAI_API_KEY", raising=False)
    monkeypatch.delenv("PM_V2_PACKY_API_KEY", raising=False)
    s = _Session()
    asyncio.run(logic.set_credential(s, provider="deepseek", api_key=CANARY_KEY, actor="a"))
    items = asyncio.run(logic.list_items(s))
    by_provider = {c["provider"]: c for c in items["credentials"]}
    assert by_provider["deepseek"]["source"] == "db"
    assert by_provider["deepseek"]["configured"] is True
    assert by_provider["deepseek"]["last4"] == CANARY_KEY[-4:]
    assert by_provider["deepseek"]["version_no"] == 1
    assert by_provider["kimi"]["source"] == "env"
    assert by_provider["kimi"]["last4"] is None
    assert by_provider["xai"]["source"] == "unset"
    assert items["flags"]["pipeline.ai_enabled"]["source"] == "default"
    blob = json.dumps(items, default=str)
    assert CANARY_KEY not in blob
    assert "sk-test-FAKEKEY" not in blob
    assert "sk-env-kimi" not in blob
    # last4 之外的 key 前缀绝不出现
    assert "sk-" not in blob


# ---------------- set/clear 校验与 rotation ----------------


def test_set_credential_validation(monkeypatch):
    logic, _vr, _fr = _make_logic(monkeypatch)
    logic_no_vault, _vr2, _fr2 = _make_logic(monkeypatch, with_vault=False)
    s = _Session()
    with pytest.raises(ValueError, match="runtime_config_provider_unknown"):
        asyncio.run(logic.set_credential(s, provider="gemini", api_key="x", actor="a"))
    with pytest.raises(ValueError, match="runtime_config_api_key_empty"):
        asyncio.run(logic.set_credential(s, provider="deepseek", api_key="   ", actor="a"))
    with pytest.raises(ValueError, match="runtime_config_api_key_too_long"):
        asyncio.run(logic.set_credential(s, provider="deepseek", api_key="k" * 513, actor="a"))
    with pytest.raises(ValueError, match="vault_keyring_not_configured"):
        asyncio.run(
            logic_no_vault.set_credential(s, provider="deepseek", api_key="sk-x", actor="a")
        )


def test_set_credential_rotation_and_clear(monkeypatch):
    logic, vault_repo, _fr = _make_logic(monkeypatch)
    s = _Session()
    r1 = asyncio.run(logic.set_credential(s, provider="xai", api_key="sk-old-0001", actor="a"))
    r2 = asyncio.run(logic.set_credential(s, provider="xai", api_key="sk-new-0002", actor="a"))
    assert (r1["version_no"], r2["version_no"]) == (1, 2)
    statuses = sorted(v["status"] for v in vault_repo.versions)
    assert statuses == ["active", "retired"]
    assert asyncio.run(resolve_model_credential(s, logic._vault, "xai")) == "sk-new-0002"

    monkeypatch.setenv("PM_V2_XAI_API_KEY", "sk-env-xai")
    cleared = asyncio.run(logic.clear_credential(s, provider="xai", actor="a"))
    assert cleared["source"] == "env"
    assert asyncio.run(resolve_model_credential(s, logic._vault, "xai")) is None
    assert any(e["result"] == "DISABLED" for e in vault_repo.events)
    with pytest.raises(ValueError, match="runtime_config_credential_not_set"):
        asyncio.run(logic.clear_credential(s, provider="xai", actor="a"))

    # clear 后重新 set：entry 重新激活，resolver 立即命中新 key
    asyncio.run(logic.set_credential(s, provider="xai", api_key="sk-back-0003", actor="a"))
    assert asyncio.run(resolve_model_credential(s, logic._vault, "xai")) == "sk-back-0003"


# ---------------- flag：DB 命中 / 回退默认 ----------------


def test_flag_set_and_audit_events(monkeypatch):
    logic, _vr, flag_repo = _make_logic(monkeypatch)
    s = _Session()
    out = asyncio.run(logic.set_ai_enabled(s, enabled=True, actor="op"))
    assert out["value"] is True and out["source"] == "db"
    asyncio.run(logic.set_ai_enabled(s, enabled=False, actor="op"))
    assert [e["new_value"] for e in flag_repo.events] == ["true", "false"]
    assert flag_repo.events[1]["old_value"] == "true"
    status = asyncio.run(logic.list_items(s))["flags"]["pipeline.ai_enabled"]
    assert status["value"] is False and status["source"] == "db"


def _pipeline_driver(monkeypatch, *, policy_ai: bool, flag_row):
    from runtimes.trading.pipeline import PipelineDriver, PipelinePolicy

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _FlagRepo:
        async def get_flag(self, session, *, flag_key):
            if isinstance(flag_row, Exception):
                raise flag_row
            return flag_row

    driver = PipelineDriver(
        sessions_factory=lambda _name: (lambda: _FakeSession()),
        policy=PipelinePolicy(ai_enabled=policy_ai, sense_enabled=False, screen_enabled=False),
    )
    monkeypatch.setattr(driver, "_runtime_config_repo", _FlagRepo())
    return driver


def test_pipeline_ai_flag_db_hit_overrides_policy(monkeypatch):
    driver = _pipeline_driver(
        monkeypatch, policy_ai=False, flag_row={"flag_value": "true"}
    )
    summary = asyncio.run(driver.run_once())
    assert summary["opportunities"]["reason"] == "ai_gated"
    driver_off = _pipeline_driver(
        monkeypatch, policy_ai=True, flag_row={"flag_value": "false"}
    )
    assert asyncio.run(driver_off.run_once()) == {}


def test_pipeline_ai_flag_fallback_to_policy(monkeypatch):
    # 无行 → policy 冻结值
    driver = _pipeline_driver(monkeypatch, policy_ai=True, flag_row=None)
    assert "opportunities" in asyncio.run(driver.run_once())
    driver_no = _pipeline_driver(monkeypatch, policy_ai=False, flag_row=None)
    assert asyncio.run(driver_no.run_once()) == {}
    # 查询异常 → 回退 policy，不阻断
    driver_err = _pipeline_driver(
        monkeypatch, policy_ai=True, flag_row=RuntimeError("db_down")
    )
    assert "opportunities" in asyncio.run(driver_err.run_once())


# ---------------- controller body 校验 ----------------


def test_controller_validation(monkeypatch):
    from app.controllers.admin.trading import runtime_config as rc
    from app.logics.trading.runtime_config import RuntimeConfigLogic

    monkeypatch.setattr(credentials, "vault_repo", _FakeVaultRepo())
    rc.reset_runtime_config_logic(
        RuntimeConfigLogic(vault=None, repo=_FakeFlagRepo())
    )
    try:
        s = _Session()
        resp = asyncio.run(rc.set_credential(
            provider="gemini", body=rc.CredentialBody(api_key="sk-x"),
            session=s, auth=_Auth(),
        ))
        assert resp["code"] == 400 and resp["msg"] == "runtime_config_provider_unknown"
        resp = asyncio.run(rc.set_credential(
            provider="deepseek", body=rc.CredentialBody(api_key=" "),
            session=s, auth=_Auth(),
        ))
        assert resp["code"] == 400 and resp["msg"] == "runtime_config_api_key_empty"
        resp = asyncio.run(rc.set_credential(
            provider="deepseek", body=rc.CredentialBody(api_key="k" * 513),
            session=s, auth=_Auth(),
        ))
        assert resp["code"] == 400 and resp["msg"] == "runtime_config_api_key_too_long"
        resp = asyncio.run(rc.set_credential(
            provider="deepseek", body=rc.CredentialBody(api_key="sk-x"),
            session=s, auth=_Auth(),
        ))
        assert resp["code"] == 503 and resp["msg"] == "vault_keyring_not_configured"
        resp = asyncio.run(rc.clear_credential(
            provider="deepseek", session=s, auth=_Auth(),
        ))
        assert resp["code"] == 404 and resp["msg"] == "runtime_config_credential_not_set"
        with pytest.raises(Exception):  # pydantic extra=forbid
            rc.AiEnabledBody(enabled=True, bogus=1)
        resp = asyncio.run(rc.set_pipeline_ai_enabled(
            body=rc.AiEnabledBody(enabled=True), session=s, auth=_Auth(),
        ))
        assert resp["code"] == 0 and resp["data"]["value"] is True
    finally:
        rc.reset_runtime_config_logic()
