"""WP-06 Checkpoint D —— settlement logic 单元（无 DB）。

证明：Standard/NegRisk calldata 与 golden 全等、caller 不可覆盖 adapter/calldata、
payout 一致性核验（50-50 / 二元 / 冲突 fail-closed）。
"""

from __future__ import annotations

import json

import pytest
from eth_abi import decode as abi_decode

from app.domain.trading.payout import (
    build_merge_calldata,
    build_redeem_calldata,
    build_split_calldata,
    verify_payout_consistency,
)

REL = json.load(open("tests/trading/fixtures/p6_settlement/relayer_deposit_wallet_golden_v1.json"))
SPEC = json.load(open("tests/trading/fixtures/p6_settlement/chain_settlement_spec_v1.json"))


def test_calldata_split_merge_redeem_match_golden():
    cond = REL["conditions"]["condition_id"]
    parent = REL["amounts"]["parent_collection_id"]
    partition = REL["amounts"]["partition"]
    amount = REL["amounts"]["pusd_base_units_per_pair"]
    pusd = SPEC["ctf"]["pusd"]
    ctf = SPEC["ctf"]["ctf"]

    assert build_split_calldata(
        collateral_address=pusd, condition_id=cond, parent_collection_id=parent,
        partition=partition, amount_base_units=amount,
    ) == REL["calldata"]["split_standard"]
    assert build_merge_calldata(
        collateral_address=pusd, condition_id=cond, parent_collection_id=parent,
        partition=partition, amount_base_units=amount,
    ) == REL["calldata"]["merge_standard"]
    assert build_redeem_calldata(
        collateral_address=pusd, condition_id=cond, parent_collection_id=parent,
        partition=partition,
    ) == REL["calldata"]["redeem_standard"]


def test_calldata_roundtrip_uses_official_abi_head_order():
    """Independent ABI decode catches swapped parent/condition and bad dynamic offsets."""
    condition = bytes.fromhex(REL["conditions"]["condition_id"][2:])
    parent = bytes.fromhex(REL["amounts"]["parent_collection_id"][2:])
    partition = tuple(int(item) for item in REL["amounts"]["partition"])
    amount = REL["amounts"]["pusd_base_units_per_pair"]
    for name, collateral in (
        ("split_standard", SPEC["ctf"]["pusd"]),
        ("merge_standard", SPEC["ctf"]["pusd"]),
    ):
        decoded = abi_decode(
            ["address", "bytes32", "bytes32", "uint256[]", "uint256"],
            bytes.fromhex(REL["calldata"][name][10:]),
        )
        assert decoded == (collateral.lower(), parent, condition, partition, amount)
    decoded = abi_decode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        bytes.fromhex(REL["calldata"]["redeem_standard"][10:]),
    )
    assert decoded == (SPEC["ctf"]["pusd"].lower(), parent, condition, partition)


def test_calldata_rejects_bad_inputs():
    cond = REL["conditions"]["condition_id"]
    parent = REL["amounts"]["parent_collection_id"]
    with pytest.raises(ValueError):
        build_split_calldata(collateral_address="not-an-address", condition_id=cond,
                             parent_collection_id=parent, partition=["1", "2"], amount_base_units=1)
    with pytest.raises(ValueError):
        build_redeem_calldata(collateral_address=SPEC["ctf"]["ctf"], condition_id=cond,
                              parent_collection_id=parent, partition=[])
    with pytest.raises(ValueError, match="selector_override_forbidden"):
        build_redeem_calldata(
            collateral_address=SPEC["ctf"]["ctf"], condition_id=cond,
            parent_collection_id=parent, partition=["1", "2"],
            selector_override="0xdeadbeef",
        )


def test_payout_consistency_binary():
    assert verify_payout_consistency(ctf_payout_outcome="YES", ctf_numerator="1",
                                     ctf_denominator="1", clob_winner="YES",
                                     clob_is_50_50=False) is True
    assert verify_payout_consistency(ctf_payout_outcome="NO", ctf_numerator="1",
                                     ctf_denominator="1", clob_winner="NO",
                                     clob_is_50_50=False) is True
    # 非 winner 面 payout 0/1
    assert verify_payout_consistency(ctf_payout_outcome="NO", ctf_numerator="0",
                                     ctf_denominator="1", clob_winner="YES",
                                     clob_is_50_50=False) is True


def test_payout_consistency_50_50():
    assert verify_payout_consistency(ctf_payout_outcome="YES", ctf_numerator="1",
                                     ctf_denominator="2", clob_winner=None,
                                     clob_is_50_50=True) is True


def test_payout_consistency_conflicts_fail_closed():
    # winner 与 payout 冲突
    assert verify_payout_consistency(ctf_payout_outcome="YES", ctf_numerator="1",
                                     ctf_denominator="1", clob_winner="NO",
                                     clob_is_50_50=False) is False
    # 缺 50-50 信号
    assert verify_payout_consistency(ctf_payout_outcome="YES", ctf_numerator="1",
                                     ctf_denominator="1", clob_winner="YES",
                                     clob_is_50_50=None) is False
    # 50-50 但 payout 非 1/2
    assert verify_payout_consistency(ctf_payout_outcome="YES", ctf_numerator="1",
                                     ctf_denominator="1", clob_winner=None,
                                     clob_is_50_50=True) is False


@pytest.mark.parametrize("bad_selector", ["deadbeef", "0x123", "0x123456789", "0xzzzzzzzz", 1, True])
def test_selector_override_has_strict_shape_and_cannot_bypass(bad_selector):
    with pytest.raises(ValueError, match="selector_override_invalid"):
        build_redeem_calldata(
            collateral_address=SPEC["ctf"]["pusd"],
            condition_id=REL["conditions"]["condition_id"],
            parent_collection_id=REL["amounts"]["parent_collection_id"],
            partition=["1", "2"],
            selector_override=bad_selector,
        )


def test_exact_official_selector_override_is_idempotent():
    expected = REL["calldata"]["redeem_standard"]
    assert build_redeem_calldata(
        collateral_address=SPEC["ctf"]["pusd"],
        condition_id=REL["conditions"]["condition_id"],
        parent_collection_id=REL["amounts"]["parent_collection_id"],
        partition=["1", "2"],
        selector_override=expected[:10].upper().replace("0X", "0x"),
    ) == expected


@pytest.mark.parametrize("bad_uint", [True, False, 1.5, object(), -1, 1 << 256])
def test_calldata_uint256_rejects_bool_float_nonint_and_out_of_range(bad_uint):
    with pytest.raises(ValueError, match="payout_uint"):
        build_split_calldata(
            collateral_address=SPEC["ctf"]["pusd"],
            condition_id=REL["conditions"]["condition_id"],
            parent_collection_id=REL["amounts"]["parent_collection_id"],
            partition=["1", "2"],
            amount_base_units=bad_uint,
        )
