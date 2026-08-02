"""P7 DoD-7 — 기록·정산.

여기서 지키는 것:

* **잰 것과 값 매긴 것을 갈라 놓는다.** 사용량은 사실, 원가는 판단이다.
* **0 과 `미정` 은 다르다.** 섞으면 로컬 모델이 공짜로 보이고 원가가 싸게 잡힌다.
* **반쪽 금액을 장부에 올리지 않는다.** 올리면 그게 사실로 굳는다.
* **판정 없음은 통과가 아니다.**
* **기록을 먼저 남기고 자원을 놓는다.**
"""

from __future__ import annotations

import pytest
from dawn_biz import records
from dawn_biz.records import Usage, rates, settle
from dawn_core.paths import Paths

ROOT = Paths().root


def _usage(**kw) -> Usage:
    base = {"order_id": 9001, "runs": 2, "completed": 2,
            "tokens_in": 1_000_000, "tokens_out": 200_000,
            "by_model": {"claude-opus-5": {"in": 1_000_000, "out": 200_000,
                                           "runs": 2, "local": 0}},
            "infra_tier": "none"}
    base.update(kw)
    return Usage(**base)


# ── 단가 ─────────────────────────────────────────────────────────────────


def test_ratecard_prices_cloud_tokens(tmp_path):
    """공개 API 단가 × 환율. 계산이 틀리면 원가가 통째로 틀린다."""
    rc = rates(ROOT)
    krw = rc["usd_krw"]
    s = settle(ROOT, _usage())
    # opus-5: in $5/1M · out $25/1M → (1.0 × 5 + 0.2 × 25) × krw = 10 × krw
    assert s.model_cost == round(10.0 * krw)
    assert s.complete and s.total == s.model_cost


def test_local_model_is_priced_by_time_not_tokens():
    """전용 GPU 는 쓰든 안 쓰든 같은 값이 든다. 토큰당으로 매기면 **같은 일의
    원가가 처리량 편차만큼 흔들린다** — 실측(2026-08-02)에서 같은 gpt-oss:120b 가
    3.7 ~ 12.8 tok/s 로 3.5배 차이 났다. 장비는 시간을 산다."""
    u = _usage(by_model={"gpt-oss:120b": {"in": 500_000, "out": 100_000,
                                          "runs": 1, "local": 1}},
               local_gpu_ms=3_600_000)
    s = settle(ROOT, u)
    assert not s.complete, "GPU 시간당 원가가 미정인데 완결로 보고했다"
    assert any("점유 1.00h" in x for x in s.unpriced), "잰 시간이 안 남았다"
    assert any("600,000 토큰" in x for x in s.unpriced), "쓴 양도 남아야 한다"


def test_local_use_without_measured_time_is_not_silently_free():
    """시간으로 잡기로 한 뒤 생긴 구멍 — 점유 시간이 없으면 0 원이 되어 사라진다."""
    u = _usage(by_model={"gpt-oss:120b": {"in": 1_000, "out": 1_000, "runs": 1,
                                          "local": 1}}, local_gpu_ms=0)
    s = settle(ROOT, u)
    assert not s.complete and any("점유 시간 미측정" in x for x in s.unpriced)


def test_hourly_rate_is_derived_not_typed_in():
    """시간당 원가를 사람이 직접 적게 두면 구입가를 바꿔도 안 따라온다.
    입력은 견적서·고지서에서 읽을 수 있는 것만 받는다."""
    from dawn_biz.records import UNSET, hourly

    rc = {"electricity_krw_kwh": 140,
          "hardware": {"g": {"구입가": 30_000_000, "수명연수": 5, "소비전력w": 1200}}}
    # 3천만 / (5×8760) = 685 · 1.2kW × 140 = 168  → 853
    assert round(hourly(rc, "g")) == 853
    assert hourly({**rc, "electricity_krw_kwh": UNSET}, "g") == UNSET,         "전기요금이 빠졌는데 0 으로 치고 계산했다"
    assert hourly(rc, "없는장비") == UNSET


def test_unknown_model_is_flagged_not_silently_zero():
    u = _usage(by_model={"어떤-신규-모델": {"in": 10, "out": 10, "runs": 1,
                                        "local": 0}})
    s = settle(ROOT, u)
    assert not s.complete and any("단가가 없는" in x for x in s.unpriced)


def test_infra_none_is_a_real_zero_not_unset():
    """아무것도 안 잡았으면 진짜 0 이다 — 미정으로 세면 영원히 미완결이 된다."""
    s = settle(ROOT, _usage(infra_tier="none"))
    assert s.infra_cost == 0 and not any("인프라" in x for x in s.unpriced)


def test_infra_tier_with_no_rate_is_unset():
    s = settle(ROOT, _usage(infra_tier="server"))
    assert not s.complete and any("인프라 server" in x for x in s.unpriced)


def test_total_is_a_lower_bound_when_anything_is_unpriced():
    """미정이 하나라도 있으면 합계는 **하한**이다. 보고서가 그렇게 말해야 한다."""
    u = _usage(by_model={"claude-opus-5": {"in": 1_000_000, "out": 0, "runs": 1,
                                           "local": 0},
                         "gpt-oss:120b": {"in": 9_000_000, "out": 0, "runs": 1,
                                          "local": 1}},
               local_gpu_ms=600_000)
    s = settle(ROOT, u)
    assert s.total > 0 and not s.complete


def test_real_ratecard_separates_cost_from_billing():
    """청구 단가가 원가표에 섞이면 원가가 그대로 견적이 되어 나간다."""
    rc = rates(ROOT)
    assert "model" in rc and "infra_hour" in rc
    flat = str(rc)
    assert "청구" not in flat and "billing" not in flat.lower(), \
        "원가표에 청구 단가가 들어왔다"


# ── 문서 ─────────────────────────────────────────────────────────────────


@pytest.fixture
def order(tmp_path):
    from dawn_biz.store import BizStore

    s = BizStore(tmp_path, tenant=0)
    wid = s.add_work_order(title="[테스트] 마감", body="본문", origin="internal",
                           business="", division="corp", infra_tier="none")
    s.set_work_order_status(wid, "approved")
    return s, wid


def test_no_verdict_is_written_as_no_verdict(order, monkeypatch):
    """판정이 없는 것을 **통과로 읽히게 두면 안 된다.**"""
    store, wid = order
    monkeypatch.setattr(records, "usage", lambda root, oid: _usage(order_id=oid))
    doc = store.document(records.report(store, ROOT, wid))
    assert "판정 없음" in doc["body"]
    assert "판정하지 않은 것" in doc["body"], "없음이 통과처럼 읽힌다"


def test_report_says_the_number_is_cost_not_a_quote(order, monkeypatch):
    store, wid = order
    monkeypatch.setattr(records, "usage", lambda root, oid: _usage(order_id=oid))
    doc = store.document(records.report(store, ROOT, wid))
    assert "청구액이 아니다" in doc["body"]


def test_worklog_records_what_ran(order, monkeypatch):
    store, wid = order
    monkeypatch.setattr(
        records, "usage",
        lambda root, oid: _usage(order_id=oid, agents=[f"wo{oid}-builder"],
                                 traces=["abc123"]))
    doc = store.document(records.worklog(store, ROOT, wid))
    assert f"wo{wid}-builder" in doc["body"] and "abc123" in doc["body"]


# ── 장부 ─────────────────────────────────────────────────────────────────


def test_partial_cost_is_not_booked(order, monkeypatch):
    """반쪽 금액을 장부에 올리면 그게 사실로 굳는다. 단가가 정해진 뒤에 다시 부른다."""
    store, wid = order
    monkeypatch.setattr(
        records, "usage",
        lambda root, oid: _usage(order_id=oid,
                                 by_model={"gpt-oss:120b": {"in": 1, "out": 1,
                                                            "runs": 1, "local": 1}}))
    assert records.record_cost(store, ROOT, wid) == 0
    assert not store.expenses(), "미확정 원가가 경비로 올라갔다"


def test_complete_cost_is_booked(order, monkeypatch):
    store, wid = order
    monkeypatch.setattr(records, "usage", lambda root, oid: _usage(order_id=oid))
    eid = records.record_cost(store, ROOT, wid)
    assert eid
    row = next(e for e in store.expenses() if e["id"] == eid)
    assert row["request_id"] == f"wo{wid}" and row["amount_krw"] > 0


# ── 마감 순서 ────────────────────────────────────────────────────────────


def test_close_records_before_releasing(order, monkeypatch):
    """반납을 먼저 하면 무엇을 썼는지 알 수 없게 된다 — 순서가 규칙이다."""
    import dawn_biz.provision as prov

    store, wid = order
    seen: list[str] = []
    monkeypatch.setattr(records, "usage", lambda root, oid: _usage(order_id=oid))

    def watch(name):
        real = getattr(records, name)

        def wrapped(*a, **k):
            seen.append(name)
            return real(*a, **k)

        monkeypatch.setattr(records, name, wrapped)

    for n in ("worklog", "report", "record_cost"):
        watch(n)

    def fake_release(*a, **k):
        seen.append("release")
        return None

    monkeypatch.setattr(prov, "deprovision", fake_release)
    out = records.close(store, ROOT, wid)
    assert seen.index("worklog") < seen.index("release")
    assert seen.index("report") < seen.index("release")
    assert store.work_order(wid)["status"] == "done"
    assert out["worklog_id"] and out["report_id"]
