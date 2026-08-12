"""WP-06 Checkpoint A —— chain-settlement spec 与 P6 fixture 自洽性单元测试（无 DB）。

覆盖：
- ``chain_settlement_spec_v1.json`` 自洽：frozen_at 早于今天、content_hash 自洽、
  链/Relayer/CTF/finality 必需子对象齐备、authorized_capital=0、fake authority。
- P5 capability/economic hash 与 P5 spec 可复算一致（冻结值）。
- operation 状态机：全部状态合法、terminal 禁止 mutation、经济 effect 只发生在 FINALIZED、
  恢复只读集合正确。
- settlement source 精确五元组；冲突 effect=0。
- recovery 矩阵：每个场景 expected state 合法、blind resend=false。
- registry 全部条目 runtime/resolved code keccak 可全长复核（EIP-1967 + Beacon 双路径）。
- 七份 fixture content_hash 自洽、无 TBD/占位。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain.trading.hashing import canonical_hash
from tests.trading.fixtures.p6_settlement import p6_helpers as _ph
from tests.trading.fixtures.p6_settlement.p6_helpers import (
    FIXTURES,
    frozen_fixture,
    relayer_golden,
    recovery_matrix,
    registry,
    registry_runtime_keccak,
)

CHAIN_ID = 137
SNAPSHOT_BLOCK = 91842167
DEPOSIT_WALLET = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"
CTF_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
NEG_RISK_ADAPTER = "0xadA2005600Dec949baf300f4C6120000bDB6eAab"
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# 冻结 spec 中 P5 capability/economic hash（2026-08-11；与 p5 spec 复算一致）。
FROZEN_CAPABILITY_HASH = "fe44405a48ae517f51eea8af98ee33ca37789523c39a6d5ffef3aa7db745f196"
FROZEN_ECONOMIC_HASH = "db37b682001fb47c6e22b70663d9ae1eab9f701843b6ff2924dab7f0e62ee8c6"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_spec_content_hash_self_consistent() -> None:
    spec = frozen_fixture("chain_settlement_spec")
    assert spec["spec_key"] == "chain_settlement_v1"
    assert spec["schema_version"] == "1"
    frozen = datetime.fromisoformat(spec["frozen_at"].replace("Z", "+00:00"))
    assert frozen <= _now(), "spec frozen_at must not be in the future"


def test_all_p6_fixtures_content_hash_consistent() -> None:
    for name in FIXTURES:
        frozen_fixture(name)


def test_no_tbd_or_placeholder_in_fixtures() -> None:
    import re
    from pathlib import Path

    raw = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (Path(__file__).resolve().parents[1] / "fixtures" / "p6_settlement").glob("*.json")
    )
    for bad in ("TBD", "TODO", "placeholder", "…"):
        assert bad not in raw, f"found forbidden token {bad!r} in p6 fixtures"
    # 每个 content_hash 都是 64-hex（含 provider_source 的 per-source 内嵌 content_hash）
    content_hashes = re.findall(r'"content_hash":\s*"([0-9a-f]{64})"', raw)
    assert len(content_hashes) >= len(list(FIXTURES))


def test_chain_constants_frozen() -> None:
    spec = frozen_fixture("chain_settlement_spec")
    chain = spec["chain"]
    assert chain["chain_id"] == CHAIN_ID
    assert chain["name"] == "polygon_pos"
    assert chain["clob"] == "v2"
    assert chain["snapshot_block_number"] == SNAPSHOT_BLOCK
    assert chain["snapshot_block_hash"] == (
        "0x16d35ed4cc72f20c141efcc38d8c0362d4ba95482f3aa96071e85fd06857a47f"
    )
    assert chain["finality_mode"] == "finalized"
    assert len(chain["eip1967_implementation_slot"]) == 66
    assert len(chain["eip1967_beacon_slot"]) == 66
    assert spec["deposit_wallet"]["address"] == DEPOSIT_WALLET
    assert spec["deposit_wallet"]["raw_contract_only"] is True
    assert spec["deposit_wallet"]["safe_proxy_capability"] is False
    assert spec["adapters"]["standard"]["address"] == CTF_ADAPTER
    assert spec["adapters"]["neg_risk"]["address"] == NEG_RISK_ADAPTER
    assert spec["ctf"]["pusd"] == PUSD
    assert spec["ctf"]["ctf"] == CTF
    assert spec["ctf"]["pusd_decimals"] == 6
    assert spec["ctf"]["partition"] == ["1", "2"]


def test_fake_only_and_authorized_capital() -> None:
    spec = frozen_fixture("chain_settlement_spec")
    assert spec["fake_authority"] == "FAKE_CONFORMANCE"
    assert spec["authorized_capital"] == 0
    assert spec["not_supported"]["other_conversion"] is True
    assert spec["not_supported"]["other_chain"] is True
    assert spec["not_supported"]["clob_v1"] is True
    assert spec["not_supported"]["safe_proxy_raw_flow"] is True


def test_p5_capability_and_economic_hash() -> None:
    import json
    from pathlib import Path

    from tests.trading.fixtures.p5_execution.p5_helpers import load_p5_spec

    spec = frozen_fixture("chain_settlement_spec")
    assert spec["p5"]["capability_hash"] == FROZEN_CAPABILITY_HASH
    assert spec["p5"]["economic_hash"] == FROZEN_ECONOMIC_HASH
    assert spec["p5"]["must_match_p5_manifest"] is True
    p5 = load_p5_spec()
    assert canonical_hash(p5["capability_cost_hashes"]) == FROZEN_CAPABILITY_HASH
    assert canonical_hash({
        "reservation": p5["reservation"],
        "order_transition_table": p5["order_transition_table"],
        "kill_switch_matrix": p5["kill_switch_matrix"],
    }) == FROZEN_ECONOMIC_HASH


def test_operation_state_machine_valid() -> None:
    spec = frozen_fixture("chain_settlement_spec")
    sm = spec["operation_state_machine"]
    states = set(sm["states"])
    assert len(states) == len(sm["states"]), "states must be unique"
    assert {"PREPARED", "SUBMITTING", "UNKNOWN", "RELAYER_NEW", "EXECUTED", "MINED",
            "RELAYER_CONFIRMED", "MINED_PROVISIONAL", "FINALIZED", "INVALID", "FAILED",
            "REORGED", "SETTLEMENT_CONFLICT", "REVERSED"} == states
    assert set(sm["terminal"]) <= states
    assert sm["economic_effect_only_on"] == ["FINALIZED"]
    assert sm["no_illegal_rollback"] is True
    assert sm["no_terminal_mutation"] is True
    assert sm["no_skipping_finality"] is True
    # recovery 状态必须是合法状态子集且不含 terminal
    assert set(sm["recovery_only_states"]) <= states
    assert set(sm["recovery_only_states"]) & set(sm["terminal"]) == set()


def test_settlement_sources_exact_set() -> None:
    spec = frozen_fixture("chain_settlement_spec")
    sources = spec["settlement_sources"]
    assert sources["required_kinds"] == [
        "gamma_clob_closed", "ctf_payout", "data_api_redeemable",
        "clob_winner_5050", "label_audit",
    ]
    assert len(sources["required_kinds"]) == len(set(sources["required_kinds"]))
    assert sources["missing_or_conflict_action"] == "SETTLEMENT_CONFLICT"
    assert set(sources["effects_zero_on_conflict"]) == {
        "G8", "score", "learning", "redeem", "ledger",
    }


def test_finality_fail_closed() -> None:
    spec = frozen_fixture("chain_settlement_spec")
    fin = spec["finality"]
    assert fin["relayer_confirmed_is_not_finality"] is True
    assert fin["required_receipt_status"] == "0x1"
    assert fin["receipt_requires_tx_hash"] is True
    assert fin["block_must_match_canonical"] is True
    assert fin["finalized_tag"] == "finalized"
    assert fin["finalized_must_exceed_receipt_block"] is True
    assert fin["receipt_missing_reorg_removed_unknown"] is True
    assert fin["fail_closed_on_finalized_unsupported"] is True


def test_recovery_matrix_scenarios_valid() -> None:
    rec = recovery_matrix()
    assert len(rec["matrix"]) >= 12
    assert rec["invariants"]["logical_operation"] == 1
    assert rec["invariants"]["no_duplicate_effect"] is True
    assert rec["invariants"]["no_blind_resend"] is True
    spec = frozen_fixture("chain_settlement_spec")
    valid_states = set(spec["operation_state_machine"]["states"]) | {"RECOVER", "HARD_STOP_SETTLEMENT"}
    for scenario in rec["matrix"]:
        assert "scenario" in scenario and "expected" in scenario
        exp = scenario["expected"]
        assert exp["state"] in valid_states, scenario["scenario"]
        assert exp.get("blind_resend", False) is False
    # 关键场景必须存在
    keys = {s["scenario"] for s in rec["matrix"]}
    for required in ("submit_timeout", "relayer_confirmed_without_tx_hash", "reorg_removed_log",
                     "finalized_evidence_conflict", "rpc_finalized_unsupported"):
        assert required in keys, required


def test_registry_all_entries_recompute_keccak() -> None:
    reg = registry()
    assert reg["registry_version"] == "polygon-mainnet-v1"
    assert reg["chain_id"] == CHAIN_ID
    assert reg["snapshot_block_number"] == SNAPSHOT_BLOCK
    assert reg["active_only"] is True
    names = {e["name"] for e in reg["entries"]}
    assert names == {"pusd", "ctf", "deposit_wallet", "ctf_adapter_standard", "neg_risk_adapter"}
    for entry in reg["entries"]:
        registry_runtime_keccak(entry["name"])


def test_relayer_wire_golden() -> None:
    _ph.verify_relayer_wire()
    golden = relayer_golden()
    assert golden["base_url"] == "https://relayer-v2.polymarket.com"
    assert golden["chain_id"] == CHAIN_ID
    assert golden["submit"]["hmac"]["algorithm"] == "hmac-sha256"
    assert golden["submit"]["hmac"]["secret_not_in_fixture"] is True
    assert golden["status"]["legacy_frozen_as"] == "DRIFT_NOT_USED"
    assert golden["status"]["states"]["success_terminal"] == ["CONFIRMED"]
    assert golden["status"]["states"]["confirmed_is_not_finality"] is True
    assert golden["eip712"]["domain"]["name"] == "DepositWallet"
    assert golden["eip712"]["domain"]["version"] == "1"
    assert golden["eip712"]["domain"]["chainId"] == CHAIN_ID
    assert golden["eip712"]["domain"]["verifyingContract"] == DEPOSIT_WALLET
    assert golden["eip712"]["primary_type"] == "Batch"


def test_relayer_hmac_input_and_signature_reproducible() -> None:
    import base64
    import hashlib
    import hmac as _hmac

    golden = relayer_golden()
    body = golden["submit"]["body"]
    exact = __import__("json").dumps(body, separators=(",", ":")).encode().decode()
    hmac_input = golden["submit"]["hmac"]["input"]
    assert hmac_input == f"{golden['deadline']['trusted_now']}POST/submit" + exact
    fake_secret = hashlib.sha256(b"pm-v2/fixture/builder-secret/v1").digest()
    computed = base64.urlsafe_b64encode(
        _hmac.new(fake_secret, hmac_input.encode(), hashlib.sha256).digest()
    ).decode()
    assert computed == golden["submit"]["hmac"]["expected_signature_b64"]


def test_selector_derivation_matches_abi() -> None:
    from tests.trading.fixtures.p6_settlement.p6_helpers import selector

    golden = relayer_golden()
    sel = golden["selector"]
    assert sel["split"] == selector("splitPosition(address,bytes32,bytes32,uint256[],uint256)")
    assert sel["merge"] == selector("mergePositions(address,bytes32,bytes32,uint256[],uint256)")
    assert sel["redeem"] == selector("redeemPositions(address,bytes32,bytes32,uint256[])")
    assert sel["implementation"] == "0x5c60da1b"
    assert sel["balance_of"] == "0x70a08231"
    assert sel["allowance"] == "0xdd62ed3e"
    assert sel["approve"] == "0x095ea7b3"
