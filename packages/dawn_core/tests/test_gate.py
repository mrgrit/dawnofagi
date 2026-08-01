"""게이트 병합의 안전 불변식 — 단조 축소(monotonic narrowing).

이 테스트가 깨지면 하위 문서가 권한을 늘릴 수 있다는 뜻이다.
회사의 통제 평면 전체가 무의미해지므로 **절대 완화하지 마라.**
"""

from __future__ import annotations

import pytest
from dawn_core.gate import Gate, check_narrowing, matches, merge, subsumes

COMPANY = {
    "tools": {
        "allow": ["eg.*", "skill.*", "fs.read", "fs.write", "sec.*", "fin.*"],
        "deny": ["ctl.*", "pay.*"],
    },
    "autonomy": "A2",
    "hitl": {"require_on": ["destructive"]},
    "budget": {"max_steps": 60, "max_tokens": 400000},
    "model": {"policy": "from_eg"},
    "telemetry": {"emit": True},
}


# ── 패턴 ────────────────────────────────────────────────────────────────


def test_matches_glob_and_exact():
    assert matches("sec.siem_query", ["sec.*"])
    assert matches("fs.read", ["fs.read"])
    assert not matches("fin.ledger_read", ["sec.*", "fs.read"])


def test_subsumes():
    parent = ["sec.*", "fs.read"]
    assert subsumes(parent, "sec.siem_query")  # 정확한 이름이 부모 글롭 안
    assert subsumes(parent, "sec.*")  # 같은 글롭
    assert subsumes(parent, "fs.read")
    assert not subsumes(parent, "fin.*")  # 부모에 없는 네임스페이스
    assert not subsumes(parent, "*")  # 전체 확장 시도


# ── 병합 ────────────────────────────────────────────────────────────────


def test_allow_is_narrowed_not_widened():
    g = merge(
        [
            ("company", COMPANY),
            ("team", {"tools": {"allow": ["sec.siem_query", "eg.*"]}}),
        ]
    )
    assert g.permits("sec.siem_query")
    assert g.permits("eg.search")
    assert not g.permits("fin.ledger_read")  # 팀이 좁혔으므로 빠진다
    assert not g.permits("fs.read")


def test_child_cannot_escape_parent_allow():
    """하위가 상위 범위 밖 도구를 allow 해도 병합 결과에 들어오지 않는다."""
    g = merge(
        [
            ("company", COMPANY),
            ("team", {"tools": {"allow": ["hr.*", "eg.search"]}}),  # hr 은 상위에 없음
        ]
    )
    assert g.permits("eg.search")
    assert not g.permits("hr.data_read")


def test_deny_is_union_and_wins_over_allow():
    g = merge(
        [
            ("company", COMPANY),
            ("team", {"tools": {"allow": ["sec.*"], "deny": ["sec.firewall_change"]}}),
        ]
    )
    assert g.permits("sec.siem_query")
    assert not g.permits("sec.firewall_change")
    assert not g.permits("pay.execute")  # 전사 deny 는 계속 유효


def test_deny_cannot_be_unset_downstream():
    g = merge(
        [
            ("company", COMPANY),
            ("team", {"tools": {"allow": ["pay.execute", "eg.*"]}}),
        ]
    )
    assert not g.permits("pay.execute")


def test_autonomy_takes_the_lower():
    assert merge([("c", COMPANY), ("t", {"autonomy": "A0"})]).autonomy == "A0"
    # 하위가 올리려 해도 병합은 더 낮은 쪽을 택한다
    assert merge([("c", COMPANY), ("t", {"autonomy": "A3"})]).autonomy == "A2"


def test_budget_takes_the_minimum():
    g = merge([("c", COMPANY), ("t", {"budget": {"max_steps": 25, "max_tokens": 999999}})])
    assert g.budget["max_steps"] == 25
    assert g.budget["max_tokens"] == 400000


def test_model_policy_takes_the_stricter():
    assert (
        merge([("c", COMPANY), ("t", {"model": {"policy": "local_only"}})]).model_policy
        == "local_only"
    )
    assert (
        merge([("c", COMPANY), ("t", {"model": {"policy": "cloud_ok"}})]).model_policy == "from_eg"
    )


def test_hitl_conditions_only_grow():
    g = merge(
        [
            ("c", COMPANY),
            ("t", {"hitl": {"require_on": ["payment"], "amount_threshold_krw": 100000}}),
        ]
    )
    assert g.requires_hitl("destructive")
    assert g.requires_hitl("payment")
    assert g.amount_threshold_krw == 100000


def test_telemetry_cannot_be_turned_off_downstream():
    g = merge([("c", COMPANY), ("t", {"telemetry": {"emit": False}})])
    assert g.telemetry["emit"] is True


# ── 위반 탐지 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "child, needle",
    [
        ({"tools": {"allow": ["hr.*"]}}, "상위 허용 범위 밖"),
        ({"tools": {"allow": ["pay.execute"]}}, "deny 된 도구"),
        ({"autonomy": "A3"}, "자율화 등급을 올리려"),
        ({"budget": {"max_steps": 100}}, "예산을 늘리려"),
        ({"model": {"policy": "cloud_ok"}}, "모델 정책을 느슨하게"),
        ({"telemetry": {"emit": False}}, "텔레메트리를 끄려"),
    ],
)
def test_check_narrowing_catches_widening(child, needle):
    parent = merge([("company", COMPANY)])
    violations = check_narrowing(parent, child, "team")
    assert violations, f"위반을 잡지 못했다: {child}"
    assert any(needle in v for v in violations), violations


def test_check_narrowing_allows_legitimate_narrowing():
    parent = merge([("company", COMPANY)])
    child = {
        "tools": {"allow": ["sec.siem_query"], "deny": ["fin.*"]},
        "autonomy": "A1",
        "budget": {"max_steps": 25},
        "model": {"policy": "local_only"},
        "telemetry": {"emit": True},
    }
    assert check_narrowing(parent, child, "team") == []


# ── 조회 ────────────────────────────────────────────────────────────────


def test_filter_and_rejected():
    g = merge([("company", COMPANY), ("team", {"tools": {"allow": ["eg.*", "fs.read"]}})])
    declared = ["eg.search", "fs.read", "sec.siem_query"]
    assert g.filter(declared) == {"eg.search", "fs.read"}
    assert g.rejected(declared) == {"sec.siem_query"}


def test_forces_local_model():
    assert Gate(model_policy="local_only").forces_local_model()
    assert Gate(force_local_when={"always"}).forces_local_model()
    assert Gate(force_local_when={"l3_data"}).forces_local_model("l3_data")
    assert not Gate(force_local_when={"l3_data"}).forces_local_model("public")


def test_unset_allow_inherits_rather_than_opening():
    """allow 를 선언하지 않은 계층은 '제한 없음'이 아니라 상속이다."""
    g = merge([("company", COMPANY), ("team", {"autonomy": "A1"})])
    assert g.permits("sec.siem_query")
    assert not g.permits("hr.data_read")
