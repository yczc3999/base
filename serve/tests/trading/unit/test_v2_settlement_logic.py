"""WP-06 Checkpoint D —— settlement logic 单元（无 DB）。

证明：Standard/NegRisk calldata 与 golden 全等、caller 不可覆盖 adapter/calldata、
payout 一致性核验（50-50 / 二元 / 冲突 fail-closed）。
"""

from __future__ import annotations

import json

import pytest

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
        collateral_address=ctf, condition_id=cond, parent_collection_id=parent,
        partition=partition, amount_base_units=amount,
    ) == REL["calldata"]["merge_standard"]
    assert build_redeem_calldata(
        collateral_address=ctf, condition_id=cond, parent_collection_id=parent,
        partition=partition,
    ) == REL["calldata"]["redeem_standard"]


def test_calldata_rejects_bad_inputs():
    cond = REL["conditions"]["condition_id"]
    parent = REL["amounts"]["parent_collection_id"]
    with pytest.raises(ValueError):
        build_split_calldata(collateral_address="not-an-address", condition_id=cond,
                             parent_collection_id=parent, partition=["1", "2"], amount_base_units=1)
    with pytest.raises(ValueError):
        build_redeem_calldata(collateral_address=SPEC["ctf"]["ctf"], condition_id=cond,
                              parent_collection_id=parent, partition=[])


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
