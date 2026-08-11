"""WP-05 Checkpoint A —— execution-readiness spec 与 P5 fixture 自洽性单元测试（无 DB）。

覆盖：
- ``p_execution_readiness_spec_v1.json`` 自洽：frozen_at 早于今天、content_hash 自洽、
  必需 policy 子对象齐备、spec_key/schema_version/spec_version 固定。
- ``spec_policy_hashes()`` 与冻结快照一致（冻结的 canonical hash）。
- sdk_source_manifest 的 version/tag/commit 与 spec 一致；golden hash 一致。
- official heartbeat drift fixture：frozen 与 observed contract 不同且已记录，无双发/fallback。
- order transition table：全部转移合法、append-only、禁止倒退/重复 effect、UNKNOWN 禁盲重发。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain.trading.hashing import canonical_hash
from tests.trading.fixtures.p5_execution.p5_helpers import (
    frozen_scenario,
    frozen_spec,
    load_p5_spec,
    sdk_golden_hash,
    spec_policy_hashes,
)

# 冻结快照：spec_policy_hashes() 十个 policy 的 canonical hash（2026-08-11 冻结）。
FROZEN_SPEC_POLICY_HASHES = {
    "sdk_hash": "4edfa0f31e17cf1f3f68510f216380bd63f070732fe44cf613cb77783a9ccdea",
    "type3_hash": "a773be0fe973ab33331d6a179c4573937d544b303b5dad591530f4eb57336c70",
    "heartbeat_hash": "f6eba1adb732184b48f1b6ce5158754d7549f97761745d58a8bce0aea668100a",
    "reconcile_hash": "b47547e158448759914b1f97df37cd30ffbd20a8b5153429f4102913ebed4a9d",
    "order_transition_hash": "41521c3a572d93b3aed328be4b8125ee672d2f97e599d53938781086c931255c",
    "unknown_retry_hash": "5a3076401b921bec20bc0b78c27048b3d7e384d8dffc8a9965e22c78b088cb8d",
    "reservation_hash": "29072c964ba769d12c376272f2e7eacc51add932f68c8145a6104293161e49b5",
    "vault_aad_hash": "dd91bbfd2f45ef19bdc419a42b65cc7a61cc6bb039f55459277373001a2bb4e1",
    "kill_switch_hash": "a6427fae2f1ef71bd3f9d38f0057f42a7edd0415bc18431ac80ef36c9210e031",
    "fake_only_hash": "fb6c1aa22af03ca3aced814443de13ef82cd2f127662d5959197be10482104b2",
}

# 必需顶层 policy 子对象（spec_policy_hashes 依赖它们）。
REQUIRED_POLICY_KEYS = [
    "sdk",
    "type3_identity",
    "heartbeats",
    "user_ws_rest_reconcile",
    "order_transition_table",
    "unknown_retry_matrix",
    "reservation",
    "vault_aad",
    "kill_switch_matrix",
    "fake_only",
]

# 期望 SDK 锁定值（requirements 与 sdk_source_manifest 必须与 spec 一致）。
EXPECTED_SDK = {
    "package": "polymarket-client",
    "version": "0.5.0",
    "tag": "polymarket-client-v0.5.0",
    "tag_commit": "974d2e22ca92445d8ab7ecd7715a247f1ea7d65a",
}


def test_spec_frozen_before_today_and_self_consistent():
    spec = frozen_spec()  # asserts content_hash self-consistency
    frozen_at = datetime.fromisoformat(spec["frozen_at"].replace("Z", "+00:00"))
    assert frozen_at.tzinfo is not None
    assert frozen_at < datetime.now(timezone.utc), (
        "frozen_at must precede the first account/reservation/envelope/order attempt"
    )
    assert spec["schema_version"] == "p5/execution-readiness-spec/v1"
    assert spec["spec_key"] == "p5-execution-readiness-spec-v1"
    assert spec["spec_version"] == 1
    assert len(spec["content_hash"]) == 64


def test_spec_upstream_hashes_and_alembic_head():
    spec = load_p5_spec()
    upstream = spec["upstream"]
    assert upstream["wp03_manifest_sha256"].startswith("996869e2")
    assert upstream["wp04_manifest_sha256"].startswith("c22daa47")
    assert upstream["alembic_head"] == "b1000041"
    assert upstream["p2_execution_spec"]["spec_key"] == "p2-execution-spec-v1"
    assert upstream["p3_evaluation_spec"]["spec_key"] == "p3-evaluation-spec-v1"


def test_spec_policy_hashes_match_frozen_snapshot():
    assert spec_policy_hashes() == FROZEN_SPEC_POLICY_HASHES


def test_all_required_policy_objects_present():
    spec = load_p5_spec()
    for key in REQUIRED_POLICY_KEYS:
        assert isinstance(spec.get(key), dict), f"missing policy object: {key}"


def test_sdk_source_manifest_matches_spec():
    spec_sdk = load_p5_spec()["sdk"]
    manifest = frozen_scenario("sdk_source")
    m_sdk = manifest["sdk"]
    for field, expected in EXPECTED_SDK.items():
        assert spec_sdk[field] == expected, field
        assert m_sdk[field] == expected, field
    # golden hash 一致且必须是真实非零向量，禁止占位自洽。
    assert spec_sdk["golden_sha256"] == sdk_golden_hash()
    assert m_sdk["golden_sha256"] == sdk_golden_hash()
    assert sdk_golden_hash() != "0" * 64
    # type-3 identity 在 manifest 与 spec 中一致。
    m_type3 = manifest["type3_wire_golden"]
    spec_type3 = load_p5_spec()["type3_identity"]
    assert m_type3["signature_type"] == spec_type3["signature_type"]
    assert m_type3["maker"] == spec_type3["maker"]
    assert m_type3["funder"] == spec_type3["funder"]
    assert m_type3["wire_signer"] == spec_type3["wire_signer"]
    assert m_type3["signing_actor"] == spec_type3["signing_actor"]


def test_heartbeat_drift_fixture_records_contract_difference():
    drift = frozen_scenario("heartbeat_drift")
    frozen = drift["frozen_contract"]
    observed = drift["observed_contract"]
    assert drift["observed_at"] == "2026-08-11T00:00:00Z"
    assert drift["url"] == "https://docs.polymarket.com/api-reference/trade/send-heartbeat"
    assert frozen["endpoint"] == "POST /v1/heartbeats"
    assert frozen["id_chain"] == "no_skip"
    assert observed["endpoint"] == "POST /heartbeats"
    assert observed["heartbeat_id_chain"] == "not_documented"
    assert frozen["endpoint"] != observed["endpoint"], "drift must be recorded, not silent"
    assert "diff_reason" in drift and drift["diff_reason"]
    # 不得静默切路径/双发/fallback。
    behavior = drift["behavior"]
    assert behavior["no_silent_switch_to_observed_path"] is True
    assert behavior["no_double_send_of_both_paths"] is True
    assert behavior["no_fallback_guess"] is True
    assert behavior["real_provider_activation_blocked"] is True


def _assert_legal_transition_edges(spec):
    table = spec["order_transition_table"]
    states = set(table["states"])
    for edge in table["transitions"]:
        assert edge["from"] in states, edge
        assert edge["to"] in states, edge


def test_order_transition_table_legal_and_append_only():
    spec = load_p5_spec()
    table = spec["order_transition_table"]
    assert table["append_only"] is True
    assert table["no_rollback"] is True
    assert table["no_duplicate_effect"] is True
    _assert_legal_transition_edges(spec)
    # 禁止倒退：不存在任何 transition 的 to 早于 from（用固定顺序索引校验）。
    order = ["INTENT", "SUBMITTED", "ACK", "PARTIAL", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN", "RECONCILED"]
    index = {s: i for i, s in enumerate(order)}
    for edge in table["transitions"]:
        assert index[edge["to"]] >= index[edge["from"]], edge
    # 语义上 FINAL 终态（FILLED/CANCELLED/REJECTED/RECONCILED）不能是任何 from。
    terminal = {"FILLED", "CANCELLED", "REJECTED", "RECONCILED"}
    for edge in table["transitions"]:
        assert edge["from"] not in terminal, edge
    # UNKNOWN 只能到 RECONCILED（禁盲重发）；不存在 UNKNOWN→SUBMITTED/ACK 等。
    unknown_edges = [e for e in table["transitions"] if e["from"] == "UNKNOWN"]
    assert unknown_edges == [{"from": "UNKNOWN", "to": "RECONCILED"}]


def test_unknown_retry_matrix_forbids_blind_resubmit():
    spec = load_p5_spec()
    matrix = spec["unknown_retry_matrix"]
    assert matrix["forbid_blind_resubmit"] is True
    assert set(matrix["forbid_rotation"]) == {"salt", "timestamp", "signature", "body"}
    assert "rest_proves_not_in_book" in matrix["new_attempt_requires"]
    assert "frozen_reason_allows" in matrix["new_attempt_requires"]
    assert matrix["old_attempt"] == "permanently_preserved"


def test_kill_switch_and_fake_only_boundary():
    spec = load_p5_spec()
    kill = spec["kill_switch_matrix"]["kill_switch_or_authorized_capital_0"]
    assert kill["exposure_increasing_submit"] == 0
    assert kill["reduce_close_cancel_reconcile"] == "allowed"
    fake = spec["fake_only"]
    assert fake["authority"] == "FAKE_CONFORMANCE"
    assert fake["permission_mode"] == "shadow"
    assert fake["authorized_capital"] == 0
    assert fake["network"] == 0
    assert fake["accounts"] == "FIXTURE_ONLY"


def test_scenario_fixtures_all_frozen_and_self_consistent():
    for name in ["sdk_source", "heartbeat_drift", "event_log", "snapshot", "clob_golden", "user_ws_reconcile"]:
        scenario = frozen_scenario(name)  # asserts content_hash self-consistency
        assert len(scenario["content_hash"]) == 64
        stripped = dict(scenario)
        stripped.pop("content_hash", None)
        assert canonical_hash(stripped) == scenario["content_hash"]
