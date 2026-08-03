"""판단 기록 (P8 수집 계층) — 무엇을 판단으로 세고 무엇을 안 세나.

이 테스트가 지키는 것은 하나다: **틀린 표본이 들어오지 않는 것.**
표본 20건 전에는 예측을 켜지 않기로 했는데(P8 §4-⑤), 그 20건에 시스템이
막은 줄이 섞이면 세는 것 자체가 무의미하다.
"""

from __future__ import annotations

import json

import pytest
from dawn_core.eg import judgment
from dawn_core.eg.store import EGStore


def _rec(action="hitl.decide", actor="ceo", target="hitl-1",
         at="2026-08-03T00:00:00+00:00", **detail):
    return {"at": at, "action": action, "actor": actor, "target": target,
            "result": "ok", "ip": "-", "detail": detail}


@pytest.fixture(autouse=True)
def _collect_on(monkeypatch):
    """conftest 가 세션 전체에서 수집을 꺼 두므로 여기서만 되켠다.

    끄는 이유(테스트가 실 트리를 오염시킨다)는 이 파일에 해당하지 않는다 —
    전부 임시 DB 를 쓴다.
    """
    monkeypatch.setenv("DAWN_JUDGMENT_COLLECT", "1")


@pytest.fixture()
def store(tmp_path):
    return EGStore(tmp_path / "g.db")


# ── 무엇이 판단인가 ──────────────────────────────────────────────────────
def test_수집을_끄면_적재하지_않는다(store, monkeypatch):
    """테스트가 실 트리에서 도는 저장소라 이 스위치가 말뭉치를 지킨다."""
    monkeypatch.setenv("DAWN_JUDGMENT_COLLECT", "0")
    assert judgment.record(store, _rec(decision="approved", note="사유")) is None
    assert store.nodes(type="Judgment") == []


def test_결정과_사유가_둘_다_있어야_판단이다():
    assert judgment.is_judgment(_rec(decision="approved", note="한도 안이다"))
    assert not judgment.is_judgment(_rec(note="한도 안이다"))          # 결정 없음
    assert not judgment.is_judgment(_rec(decision="approved"))          # 사유 없음
    assert not judgment.is_judgment(_rec(action="auth.login",
                                         decision="ok", note="x"))      # 경로 아님


def test_시스템이_막은_줄은_판단이_아니다():
    """승인 화면은 권한이 없을 때도 사유를 남긴다. 사람은 누른 적이 없다.

    이걸 세면 트윈은 "이 사람은 거부한다"를 배운다 — 누를 기회조차 없었는데.
    가르는 표시는 `decision` 키의 유무다.
    """
    blocked = _rec(result="denied", reason="A2 등급으로는 심각도 6 을 승인할 수 없다")
    assert not judgment.is_judgment(blocked)


def test_op_가_액션에_붙어도_잡는다():
    """`control.agent.create` 처럼 동작이 이름에 붙는 경로가 있다."""
    assert judgment.action_source("control.agent.create") == \
        judgment.action_source("control.agent")
    assert judgment.action_source("control.agentX") is None   # 접두어 오탐 금지
    assert judgment.action_source("auth.login") is None


def test_사유는_이름이_달라도_모은다():
    for key in ("note", "reason", "_reason", "why"):
        assert judgment.is_judgment(_rec(decision="edited", **{key: "왜냐하면"}))


# ── 노드 모양 ────────────────────────────────────────────────────────────
def test_노드는_판단_계층이고_L3_다():
    """거버넌스에 두면 eg-load 가 delete_layer('governance') 로 지운다."""
    n = judgment.to_node(_rec(decision="approved", note="한도 안"))
    assert n["meta"]["layer"] == "judgment" != "governance"
    assert n["meta"]["sensitivity"] == "L3"
    assert n["content"]["reason"] == "한도 안"
    assert n["content"]["source"] == "hitl"


def test_같은_판단은_같은_id_라_재적재해도_안_늘어난다(store):
    rec = _rec(decision="approved", note="한도 안", assets=["asset:ledger"])
    a = judgment.record(store, rec)
    b = judgment.record(store, rec)
    assert a == b
    assert len(store.nodes(type="Judgment")) == 1


def test_판단이_아니면_적재하지_않는다(store):
    assert judgment.record(store, _rec(note="사유만 있다")) is None
    assert store.nodes(type="Judgment") == []


# ── 엣지 ────────────────────────────────────────────────────────────────
def test_없는_노드로는_엣지를_걸지_않는다(store):
    """죽은 링크를 만들면 순회가 그 위에서 넘어진다."""
    judgment.record(store, _rec(decision="approved", note="x",
                                assets=["asset:없는것"]))
    assert store.edges(type="ABOUT") == []


def test_있는_자산에는_엣지를_건다(store):
    store.upsert_node("asset:ledger", "Asset", "장부", {}, {"layer": "governance"})
    jid = judgment.record(store, _rec(decision="approved", note="x",
                                      assets=["asset:ledger"]))
    edges = store.edges(type="ABOUT")
    assert [(e.src, e.dst) for e in edges] == [(jid, "asset:ledger")]


# ── 열람과 삭제 (§4-④ 본인 권리) ────────────────────────────────────────
def test_본인_것만_보고_본인_것만_지운다(store):
    judgment.record(store, _rec(actor="ceo", target="h1", decision="approved", note="a"))
    judgment.record(store, _rec(actor="lead", target="h2", decision="denied", note="b"))

    assert len(judgment.judgments(store, actor="ceo")) == 1
    assert judgment.forget(store, "ceo") == 1
    assert len(store.nodes(type="Judgment")) == 1          # lead 것은 남는다
    assert judgment.judgments(store, actor="lead")


def test_actor_없이는_전체_삭제가_안_된다(store):
    with pytest.raises(judgment.JudgmentError):
        judgment.forget(store, "")


# ── 백필 (복구용) ────────────────────────────────────────────────────────
def test_백필은_기본이_미적용이다(store, tmp_path):
    log = tmp_path / "audit.jsonl"
    log.write_text(json.dumps(_rec(decision="approved", note="한도 안")) + "\n",
                   encoding="utf-8")

    res = judgment.backfill(store, log)
    assert res["recorded"] == 1 and not res["applied"]
    assert store.nodes(type="Judgment") == []              # 아직 안 썼다

    judgment.backfill(store, log, apply=True)
    assert len(store.nodes(type="Judgment")) == 1


def test_백필은_건너뛴_이유를_말한다(store, tmp_path):
    log = tmp_path / "audit.jsonl"
    log.write_text("\n".join([
        json.dumps(_rec(decision="approved")),      # 사유 없음
        json.dumps(_rec(note="사유만")),             # 결정 없음
        json.dumps(_rec(action="auth.login")),      # 경로 아님 — 세지 않는다
        "{깨진 줄",
    ]) + "\n", encoding="utf-8")

    res = judgment.backfill(store, log)
    assert res["recorded"] == 0
    assert res["skipped"] == {"사유 없음": 1, "결정 없음": 1, "깨진 줄": 1}


# ── 판례 조회 ────────────────────────────────────────────────────────────
def test_판례는_판단만_돌려준다(store):
    store.upsert_node("persona:cfo", "Persona", "재무 원칙 한도 준수",
                      {"summary": "한도를 넘지 않는다"}, {"layer": "governance"})
    judgment.record(store, _rec(decision="denied", note="한도를 넘었다"))

    hits = judgment.precedents(store, "한도")
    assert hits and all(h.type == "Judgment" for h in hits)


def test_빈_질문에는_판례가_없다(store):
    assert judgment.precedents(store, "  ") == []


# ── 감사 로그와의 결합 ───────────────────────────────────────────────────
def test_감사에_쓰면_판단도_쌓인다(tmp_path, monkeypatch):
    """수집 계층의 요점 — 사람이 따로 입력하지 않는다."""
    from dawn_groupware.audit import AuditLog

    db = tmp_path / "var" / "eg" / "bastion_graph.db"
    db.parent.mkdir(parents=True)
    EGStore(db)
    monkeypatch.setenv("EG_DB_PATH", str(db))

    log = AuditLog(tmp_path)
    log.write("hitl.decide", actor="ceo", target="hitl-9",
              decision="approved", note="한도 안이다")

    rows = judgment.judgments(EGStore(db))
    assert len(rows) == 1
    assert rows[0].content["actor"] == "ceo"


def test_EG_가_없어도_감사는_성공한다(tmp_path, monkeypatch):
    """감사는 법적 기록이다. EG 적재는 부수 효과라 실패해도 막으면 안 된다."""
    from dawn_groupware.audit import AuditLog

    monkeypatch.setenv("EG_DB_PATH", str(tmp_path / "없는곳" / "g.db"))
    rec = AuditLog(tmp_path).write("hitl.decide", actor="ceo",
                                   decision="approved", note="x")
    assert rec["action"] == "hitl.decide"
    assert (tmp_path / "var" / "groupware" / "audit.jsonl").is_file()


# ── 테스트 잔재가 말뭉치에 들어오지 않는다 ────────────────────────────────


def test_test_client_audit_lines_are_not_judgments():
    """`conftest.py` 는 테스트 중 **적재**를 끄지만, 백필은 그 스위치가 생기기
    전의 이력을 읽는다. 실측(2026-08-03): 백필이 가져온 3건이 전부 픽스처였고
    사유가 "승인" · "범위 밖" · "P4 자기검증" 이었다 — 감사 줄의 `ip` 가 전부
    `testclient`. 스위치는 앞을 막고 이 검사는 뒤를 막는다.
    """
    rec = {"action": "portal.order.decide", "actor": "lead-ax", "ip": "testclient",
           "target": "work_order:208", "at": "2026-08-03T12:50:03",
           "detail": {"decision": "approved", "note": "승인"}}
    assert not judgment.is_judgment(rec), "테스트 클라이언트가 판단으로 셌다"

    human = {**rec, "ip": "192.168.0.50"}
    assert judgment.is_judgment(human), "사람의 요청까지 막으면 안 된다"


def test_backfill_counts_what_it_skipped_and_why(tmp_path):
    """무엇을 왜 건너뛰었는지 안 보이면 "0건"이 정상인지 고장인지 모른다."""
    import json as _json

    audit = tmp_path / "audit.jsonl"
    audit.write_text("\n".join(_json.dumps(r, ensure_ascii=False) for r in [
        {"action": "portal.order.decide", "actor": "a", "ip": "testclient",
         "target": "w:1", "at": "2026-08-03T00:00:00",
         "detail": {"decision": "approved", "note": "승인"}},
        {"action": "portal.order.decide", "actor": "b", "ip": "10.0.0.9",
         "target": "w:2", "at": "2026-08-03T00:00:01",
         "detail": {"decision": "approved", "note": "예산 안이다"}},
    ]) + "\n", encoding="utf-8")

    res = judgment.backfill(None, audit, apply=False)
    assert res["recorded"] == 1, "사람 판단만 세야 한다"
    assert res["skipped"].get("테스트 클라이언트") == 1
