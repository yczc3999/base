"""P6 chain-settlement + Polygon/Relayer fixture helper（WP-06 Checkpoint A）。

- ``load_fixture / fixture_sha256 / frozen_fixture``：读取冻结 fixture（解析 dict /
  原始字节 SHA-256 / content_hash 自洽校验），口径与 P5 ``p5_helpers`` 一致。
- ``code_keccak``：对 ``0x`` 满长 hex 字节计算 keccak-256（keccak，非 sha3-256）。
- ``selector``：Solidity 函数选择器 = keccak256(signature)[:4]。
- ``slot32``：地址左填充为 32-byte slot 值。
- ``registry_entry_for``：按 name 从 registry 取条目，并从 RPC golden 复核
  runtime_keccak / resolved_code_keccak（EIP-1967 / Beacon 双路径）。
- ``recovery_matrix``：返回 recovery 矩阵场景列表。

本模块不包含任何 secret 明文；Builder/Relayer secret 只以 ref 与 expected signature
出现，driver 侧由注入式 signer/HMAC 回调处理。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.domain.trading.hashing import canonical_hash  # noqa: F401  (re-export)
from eth_utils import keccak as _keccak  # noqa: F401

FIXTURE_DIR = Path(__file__).resolve().parent

FIXTURES: dict[str, str] = {
    "chain_settlement_spec": "chain_settlement_spec_v1.json",
    "contract_registry": "contract_registry_polygon_v1.json",
    "provider_source": "provider_source_v1.json",
    "polygon_rpc_golden": "polygon_rpc_golden_v1.json",
    "relayer_golden": "relayer_deposit_wallet_golden_v1.json",
    "settlement_sources": "settlement_sources_v1.json",
    "chain_recovery": "chain_recovery_v1.json",
}


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _file_sha256(path: Path) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _without_content_hash(obj: dict) -> dict:
    stripped = dict(obj)
    stripped.pop("content_hash", None)
    return stripped


def load_fixture(name: str) -> dict:
    """读取冻结 fixture（含 content_hash 字段）。"""
    if name not in FIXTURES:
        raise KeyError(f"unknown p6 fixture: {name!r}; expected {sorted(FIXTURES)}")
    return _load_json(FIXTURE_DIR / FIXTURES[name])


def fixture_sha256(name: str) -> str:
    """指定 fixture 原始字节 SHA-256（manifest 记录口径）。"""
    if name not in FIXTURES:
        raise KeyError(f"unknown p6 fixture: {name!r}")
    return _file_sha256(FIXTURE_DIR / FIXTURES[name])


def frozen_fixture(name: str) -> dict:
    """返回 fixture dict，断言 content_hash 自洽。"""
    fixture = load_fixture(name)
    stored = fixture.get("content_hash")
    assert isinstance(stored, str) and len(stored) == 64, (
        f"p6 fixture {name} content_hash missing/malformed"
    )
    recomputed = canonical_hash(_without_content_hash(fixture))
    assert recomputed == stored, (
        f"p6 fixture {name} content_hash mismatch: file={stored} recomputed={recomputed}"
    )
    return fixture


def spec() -> dict:
    """chain_settlement_spec_v1（content_hash 自洽）。"""
    return frozen_fixture("chain_settlement_spec")


def registry() -> dict:
    """contract_registry_polygon_v1（content_hash 自洽）。"""
    return frozen_fixture("contract_registry")


def rpc_golden() -> dict:
    """polygon_rpc_golden_v1（content_hash 自洽）。"""
    return frozen_fixture("polygon_rpc_golden")


def relayer_golden() -> dict:
    """relayer_deposit_wallet_golden_v1（content_hash 自洽）。"""
    return frozen_fixture("relayer_golden")


def settlement_sources() -> dict:
    """settlement_sources_v1（content_hash 自洽）。"""
    return frozen_fixture("settlement_sources")


def recovery_matrix() -> dict:
    """chain_recovery_v1（content_hash 自洽）。"""
    return frozen_fixture("chain_recovery")


def code_keccak(code_hex: str) -> str:
    """对 ``0x`` 满长 hex 字节计算 keccak-256（拒绝非满长/非 hex/奇偶错）。"""
    if not isinstance(code_hex, str) or not code_hex.startswith("0x"):
        raise ValueError(f"code_hex must start with 0x, got {code_hex!r}")
    body = code_hex[2:]
    if not body or len(body) % 2 != 0:
        raise ValueError(f"code_hex must be full-length even hex, got length {len(body)}")
    try:
        raw = bytes.fromhex(body)
    except ValueError as exc:
        raise ValueError(f"code_hex contains non-hex: {exc}") from exc
    if not raw:
        raise ValueError("code_hex must be non-empty bytecode")
    return "0x" + _keccak(raw).hex()


def selector(signature: str) -> str:
    """Solidity 函数选择器：keccak256(signature)[:4]。"""
    return "0x" + _keccak(signature.encode()).hex()[:8]


def slot32(address: str) -> str:
    """地址左填充为 32-byte slot value（``0x`` + 64 hex）。"""
    if not address.startswith("0x") or len(address) != 42:
        raise ValueError(f"invalid address {address!r}")
    return "0x" + address[2:].rjust(64, "0")


def verify_three_rpc_agreement() -> None:
    """golden 中每个 response 三个 RPC 节点必须逐项一致。"""
    golden = rpc_golden()
    nodes = golden["rpc_nodes"]
    for key, resp in golden["responses"].items():
        values = {json.dumps(resp[n], sort_keys=True) for n in nodes}
        assert len(values) == 1, f"rpc response {key} differs across nodes"


def _entry_responses(golden: dict, key: str) -> dict:
    node = golden["rpc_nodes"][0]
    return golden["responses"][key][node]


def registry_runtime_keccak(name: str) -> str:
    """按 name 返回 registry 条目，并从 RPC golden 复核 address 处 runtime code keccak。

    EIP-1967：读 implementation slot → 取实现 code keccak（resolved_code_keccak）。
    Beacon：读 beacon slot → eth_call implementation() → 取 beacon 与实现 code keccak。
    proxy-only hash 不算通过：runtime_keccak（proxy 自身）与 resolved_code_keccak
    （实现/beacon 处代码）都必须全长可复核。
    """
    reg = registry()
    golden = rpc_golden()
    entries = {e["name"]: e for e in reg["entries"]}
    if name not in entries:
        raise KeyError(f"registry has no entry {name!r}")
    entry = entries[name]
    rk = {
        "deposit_wallet": "deposit_wallet",
        "ctf_adapter_standard": "ctf_adapter",
        "neg_risk_adapter": "neg_risk_adapter",
    }.get(name, name)
    proxy_code = _entry_responses(golden, f"eth_getCode_{rk}")["result"]
    assert code_keccak(proxy_code) == entry["runtime_keccak"], (
        f"{name} runtime_keccak mismatch"
    )
    kind = entry["proxy_kind"]
    if kind == "none":
        assert entry["resolved_implementation_or_beacon"] is None
        assert entry["resolved_code_keccak"] == entry["runtime_keccak"]
        return entry["runtime_keccak"]
    if kind == "eip1967":
        impl_addr = entry["resolved_implementation_or_beacon"]
        assert isinstance(impl_addr, str) and impl_addr.startswith("0x")
        slot_val = _entry_responses(golden, f"eth_getStorageAt_{rk}_impl")["result"]
        assert slot_val == slot32(impl_addr), f"{name} implementation slot mismatch"
        impl_code = _entry_responses(golden, f"eth_getCode_{rk}_impl")["result"]
        assert code_keccak(impl_code) == entry["resolved_code_keccak"], (
            f"{name} resolved_code_keccak mismatch"
        )
        return entry["resolved_code_keccak"]
    if kind == "beacon":
        extra = entry["extra"]
        beacon_addr = entry["resolved_implementation_or_beacon"]
        slot_val = _entry_responses(golden, "eth_getStorageAt_ctf_adapter_beacon")["result"]
        assert slot_val == slot32(beacon_addr), f"{name} beacon slot mismatch"
        beacon_code = _entry_responses(golden, "eth_getCode_beacon")["result"]
        assert code_keccak(beacon_code) == extra["beacon_runtime_keccak"]
        impl_call = _entry_responses(golden, "eth_call_beacon_implementation")["result"]
        assert impl_call == slot32(extra["beacon_implementation"]), (
            f"{name} beacon implementation() mismatch"
        )
        impl_code = _entry_responses(golden, "eth_getCode_beacon_impl")["result"]
        assert code_keccak(impl_code) == extra["beacon_implementation_code_keccak"]
        assert entry["resolved_code_keccak"] == extra["beacon_implementation_code_keccak"]
        return entry["resolved_code_keccak"]
    raise AssertionError(f"{name} unknown proxy_kind {kind!r}")


def verify_registry_all() -> None:
    """对 registry 全部条目复核 runtime/resolved keccak（proxy/beacon/implementation）。"""
    for entry in registry()["entries"]:
        registry_runtime_keccak(entry["name"])


def verify_relayer_wire() -> None:
    """复核 relayer golden 的 exact body / HMAC input / expected signature。

    secret 不以明文存在 fixture；expected signature 由确定性 fake key 生成，供 contract
    测试核对 driver 的 hmac_input 构造与 signer 回调输入（signer 本体由测试注入）。
    """
    golden = relayer_golden()
    import base64
    import hashlib as _hl
    import hmac as _hmac

    body = golden["submit"]["body"]
    exact = json.dumps(body, separators=(",", ":")).encode().decode()
    assert exact == golden["submit"]["exact_serialized_body"]
    assert _hl.sha256(exact.encode()).hexdigest() == golden["submit"]["exact_serialized_body_sha256"]
    hmac_input = golden["submit"]["hmac"]["input"]
    expected = f"{golden['deadline']['trusted_now']}POST/submit" + exact
    assert hmac_input == expected
    # 确定性 fake key 生成的 expected signature 可复算（不把 key 写入 fixture）。
    fake_secret = _hl.sha256(b"pm-v2/fixture/builder-secret/v1").digest()
    computed = base64.urlsafe_b64encode(
        _hmac.new(fake_secret, hmac_input.encode(), _hl.sha256).digest()
    ).decode()
    assert computed == golden["submit"]["hmac"]["expected_signature_b64"]
    assert golden["submit"]["hmac"]["secret_not_in_fixture"] is True
