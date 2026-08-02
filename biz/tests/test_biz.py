"""P5 — 업무 시스템 (문서·CRM·프로젝트·경리).

모델을 부르지 않는다. 검증하는 것은 **구조**다:

* 업무 데이터가 EG 자산에 매여 있다 (관제가 심각도를 매길 수 있다).
* L3 는 로컬 모델로만 간다 (호출 전에 막힌다).
* 비가역 업무 스킬은 실행부가 없다 (계약 체결·자산 폐기).
* 테넌트 격리가 구조로 강제된다.
* 업무 에이전트가 P2 워커 루프를 탄다 (새 실행 경로 없음).
"""

from __future__ import annotations

import inspect

import pytest
from dawn_biz import egsync
from dawn_biz.events import ROUTES, business_dispatcher, ingest_inquiries, run_event
from dawn_biz.seed import seed_all
from dawn_biz.skills import build_registry
from dawn_biz.store import KIND_ASSET, KIND_LEVEL, BizStore
from dawn_biz.workers import CATEGORIES, assignable
from dawn_core import Registry, jsonl
from dawn_core.paths import Paths


@pytest.fixture(scope="module")
def root():
    return Paths().root


@pytest.fixture(scope="module")
def eg(root):
    from dawn_core.eg.cli import db_path
    from dawn_core.eg.store import EGStore

    db = db_path(Registry.load(root).paths)
    if not db.is_file():
        pytest.skip("EG DB 없음 — make eg-load")
    return EGStore(db)


@pytest.fixture
def store(tmp_path):
    return BizStore(tmp_path, tenant=0)


# ── JSONL — splitlines 버그 회귀 ─────────────────────────────────────────


def test_jsonl_splits_on_newline_only(tmp_path):
    """`\\u2028`·`\\x85` 는 줄 구분자가 아니다.

    `str.splitlines()` 는 이것들도 나눠서, 본문에 섞이면 레코드 하나가 여러 줄로
    쪼개지고 전부 파싱 실패한다 — 감사 로그가 소리 없이 빈다.
    """
    p = tmp_path / "a.jsonl"
    jsonl.append(p, {"msg": "앞 뒤", "n": 1})
    jsonl.append(p, {"msg": "정상", "n": 2})

    raw = p.read_text(encoding="utf-8")
    assert " " not in raw, "원문 U+2028 이 파일에 그대로 남았다 — 줄이 쪼개진다"
    assert raw.count("\n") == 2

    rows, bad = jsonl.read_counted(p)
    assert bad == 0, "이스케이프하지 않아 줄이 쪼개졌다"
    assert [r["n"] for r in rows] == [1, 2]
    assert rows[0]["msg"] == "앞 뒤", "왕복에서 문자가 바뀌면 안 된다"


def test_jsonl_reports_broken_lines(tmp_path):
    p = tmp_path / "b.jsonl"
    p.write_text('{"ok":1}\n깨진 줄\n{"ok":2}\n', encoding="utf-8")
    rows, bad = jsonl.read_counted(p)
    assert len(rows) == 2 and bad == 1, "깨진 줄 수를 숨기면 안 된다"


def test_jsonl_missing_file_is_empty(tmp_path):
    assert jsonl.read(tmp_path / "nope.jsonl") == []


# ── 업무 데이터 ↔ EG ─────────────────────────────────────────────────────


def test_every_kind_declares_an_asset():
    """자산을 선언하지 않은 업무 종류가 있으면 그건 관제 밖이다."""
    assert set(KIND_ASSET) == set(KIND_LEVEL)
    for kind, asset in KIND_ASSET.items():
        assert asset.startswith("asset:"), kind


def test_declared_assets_exist_in_eg(eg, store):
    """`egsync.check` 가 EG 부재를 잡는다."""
    checks = egsync.check(store, eg)
    problems = egsync.summary(checks)["problems"]
    assert not problems, "\n".join(problems)


def test_egcheck_catches_missing_asset(eg, store, monkeypatch):
    monkeypatch.setitem(KIND_ASSET, "phantom", "asset:does-not-exist")
    checks = egsync.check(store, eg)
    bad = [c for c in checks if c.asset_id == "asset:does-not-exist"]
    assert bad and not bad[0].ok
    assert "EG 에 없는 자산" in bad[0].problems[0]


def test_row_level_never_exceeds_asset_level(eg, store):
    """자산보다 민감한 데이터를 그 자산에 담고 있지 않은가."""
    for c in egsync.check(store, eg):
        assert not [p for p in c.problems if "자산 등급" in p], c.problems


def test_zone_rows_places_data_in_rooms(eg, root):
    """업무 데이터가 존에 배치돼야 픽셀 오피스가 방을 채운다."""
    live = BizStore(root, tenant=0)
    if not any(live.counts().values()):
        pytest.skip("업무 데이터 없음 — dawn-biz seed")
    zones = egsync.zone_rows(live, eg)
    assert zones and "(미배정)" not in zones, f"존 미배정 업무 데이터: {zones}"


# ── 테넌트 격리 ──────────────────────────────────────────────────────────


def test_queries_do_not_take_a_tenant_argument():
    for name in ("documents", "customers", "inquiries", "contracts", "projects",
                 "tasks", "expenses", "fixed_assets"):
        sig = inspect.signature(getattr(BizStore, name))
        assert "tenant" not in sig.parameters, f"{name} 이 tenant 를 인자로 받는다"


def test_tenant_isolation(tmp_path):
    a, b = BizStore(tmp_path, tenant=0), BizStore(tmp_path, tenant=9)
    a.add_customer(name="자사 고객")
    b.add_customer(name="다른 테넌트 고객")
    assert [r["name"] for r in a.customers()] == ["자사 고객"]
    assert [r["name"] for r in b.customers()] == ["다른 테넌트 고객"]
    assert a.foreign_rows() == 1


# ── 문서·지식 ────────────────────────────────────────────────────────────


def test_document_search_respects_level(store):
    store.add_document(title="급여 테이블 2026", body="기밀", author="hr",
                       security_level="L3")
    store.add_document(title="사내 위키 안내", body="공개 안내", author="x",
                       security_level="L1")
    hits = [r["title"] for r in store.search_documents("2026 안내", max_level="L1")]
    assert "급여 테이블 2026" not in hits, "등급 초과 문서가 검색에 걸렸다"


def test_document_revision_keeps_history(store):
    did = store.add_document(title="설계", body="1판", author="a")
    store.revise_document(did, title="설계", body="2판", author="b")
    revs = store.revisions(did)
    assert [r["revision"] for r in revs] == [2, 1]
    assert revs[-1]["body"] == "1판", "이전 판이 지워졌다 — 근거의 시점이 사라진다"
    assert store.document(did)["revision"] == 2


def test_search_survives_fts_syntax_in_query(store):
    store.add_document(title="테스트", body="본문", author="a")
    assert store.search_documents('"unclosed AND (') == [] or True   # 예외가 안 나면 된다


# ── CRM ─────────────────────────────────────────────────────────────────


def test_contract_signing_is_human_only(store):
    cid = store.add_customer(name="고객")
    con = store.add_contract(customer_id=cid, title="연간 유지보수", amount_krw=10_000_000)
    with pytest.raises(PermissionError):
        store.sign_contract(con, signed_by="corp-cs-crm-01")
    store.sign_contract(con, signed_by="human:mgmt-head")
    assert store.contracts()[0]["status"] == "signed"


def test_inquiry_draft_is_not_sent(store):
    iid = store.add_inquiry(name="홍", email="a@b.com", message="문의합니다")
    store.set_inquiry_draft(iid, draft="초안", category="도입문의", drafted_by="agent")
    row = store.inquiry(iid)
    assert row["status"] == "drafted", "발송 상태가 되면 안 된다"
    assert row["draft"] == "초안"


def test_categories_are_closed_set():
    assert len(CATEGORIES) == 5 and "기타" in CATEGORIES


# ── 프로젝트 — 의존 판정은 코드가 한다 ───────────────────────────────────


def test_assignable_respects_dependencies(store):
    pid = store.add_project(key="TEST", name="테스트")
    t1 = store.add_task(project_id=pid, title="1단계")
    t2 = store.add_task(project_id=pid, title="2단계", depends_on=str(t1))
    ready, blocked = assignable(store, "TEST")
    assert [t["id"] for t in ready] == [t1]
    assert [t["id"] for t, _ in blocked] == [t2]

    store.update_task(t1, status="done", result="근거")
    ready, blocked = assignable(store, "TEST")
    assert [t["id"] for t in ready] == [t2] and not blocked


def test_task_close_requires_evidence(root, eg):
    reg = build_registry(root, eg_store=eg)
    res = reg.get("proj.close").run(task_id=1, result="")
    assert not res.ok and "근거" in res.error


# ── 스킬 — 비가역은 실행부가 없다 ────────────────────────────────────────


IRREVERSIBLE = ("crm.contract_sign", "asset.dispose", "fin.ledger_write",
                "pay.execute", "fs.delete")


def test_irreversible_business_skills_have_no_implementation(root, eg):
    reg = build_registry(root, eg_store=eg)
    for name in IRREVERSIBLE:
        if name in reg:
            assert reg.get(name).run is None, f"{name} 에 실행부가 있다"


def test_business_skills_declare_assets(root, eg):
    """자산을 선언하지 않은 업무 스킬은 관제에서 방을 못 찾는다."""
    reg = build_registry(root, eg_store=eg)
    for name in reg.names():
        ns = name.split(".")[0]
        if ns in ("doc", "crm", "proj", "asset", "fin"):
            assert reg.get(name).touches, f"{name} 이 자산을 선언하지 않았다"


def test_read_skills_are_declared_read(root):
    """`risk` 와 `action` 은 다른 축이다 — MED 위험의 조회도 read 다."""
    from dawn_agents.skills import action_of

    catalog = Registry.load(root).tool_catalog
    for name, spec in catalog.tools.items():
        if spec.get("destructive"):
            assert action_of(spec) == "irreversible", name
        elif name.endswith(("_read", "_query", ".read", ".search")):
            assert action_of(spec) == "read", f"{name} 이 read 로 선언되지 않았다"


def test_fin_skills_read_the_business_db(root, eg, tmp_path):
    """업무 시스템이 생겼는데 에이전트가 데모 픽스처를 읽으면 장부와 숫자가 갈린다."""
    s = BizStore(root, tenant=0)
    rows = s.expenses(limit=1)
    if not rows:
        pytest.skip("경비 데이터 없음 — dawn-biz seed")
    reg = build_registry(root, eg_store=eg)
    res = reg.get("fin.expense_read").run(request_id=rows[0]["request_id"])
    assert res.ok
    assert str(rows[0]["amount_krw"]) in res.output


# ── 워커 — P2 루프를 탄다 ────────────────────────────────────────────────


def test_business_workers_use_the_p2_worker(root):
    import dawn_biz.workers as w

    src = inspect.getsource(w)
    assert "from dawn_agents import Worker" in src
    assert "w.run(" in src, "P2 워커 루프를 안 탄다면 관제 밖이다"
    # 자체 LLM 호출 경로를 만들지 않았다
    assert "anthropic" not in src and "urlopen" not in src


def test_expense_path_is_l3_local(root):
    """경비는 `touches_l3=True` 로 돈다 — 클라우드 경로를 호출 전에 막는다."""
    import dawn_biz.workers as w

    src = inspect.getsource(w.handle_expense)
    assert "touches_l3=True" in src


def test_expense_org_routes_local(root, eg):
    from dawn_core.eg.traverse import model_for_org

    for l3 in (True, False):
        r = model_for_org(eg, "org:ga", touches_l3=l3)
        assert (r["model_id"] and "local" in str(r).lower()) or r["forced_local"], r


def test_crm_org_has_no_cloud_model(eg):
    """문의 본문에는 고객 개인정보가 들어온다 — 경영관리부는 로컬만."""
    from dawn_core.eg.traverse import org_profile

    tiers = {m.prop("cost_tier") for m in org_profile(eg, "org:mgmt").models}
    assert tiers == {"local"}, f"클라우드 모델이 배정돼 있다: {tiers}"


# ── 이벤트 — 폴링이 아니다 ───────────────────────────────────────────────


def test_business_triggers_are_registered():
    d = business_dispatcher()
    for et in ROUTES:
        assert d.handlers_for(et), et


def test_unknown_event_does_nothing(root):
    from dawn_agents.events import Event

    assert run_event(Event(type="nothing.registered", source="test"), root=root) == []


def test_event_without_subject_fails_loudly(root):
    from dawn_agents.events import Event

    res = run_event(Event(type="crm.inquiry.new", source="test", payload={}), root=root)
    assert res and not res[0].ok and "대상이 없다" in res[0].error


def test_no_polling_loop_in_business_events():
    import dawn_biz.events as e

    src = inspect.getsource(e)
    assert "while True" not in src and "time.sleep" not in src


def test_ingest_is_one_way_and_idempotent(tmp_path):
    """홈페이지는 파일로 떨어뜨리고 사내가 당겨 온다. 두 번 당겨도 안 늘어난다."""
    p = tmp_path / "var" / "website" / "inquiries.jsonl"
    jsonl.append(p, {"at": "2026-08-01T00:00:00+00:00", "name": "홍",
                     "email": "a@b.com", "org": "예시", "message": "도입 문의드립니다"})
    n1, ids = ingest_inquiries(tmp_path, tenant=0)
    n2, _ = ingest_inquiries(tmp_path, tenant=0)
    assert n1 == 1 and n2 == 0
    assert BizStore(tmp_path, tenant=0).inquiry(ids[0])["source"] == "website"


def test_public_site_does_not_import_business_store():
    """공개 사이트가 업무 DB 로 가는 경로를 갖지 않는다 (P4 격리 유지)."""
    from dawn_groupware import site

    src = inspect.getsource(site)
    assert "dawn_biz" not in src and "BizStore" not in src


# ── 시드 — 레지스트리에서 나온다 ─────────────────────────────────────────


def test_seed_comes_from_registry(tmp_path, root):
    s = BizStore(tmp_path, tenant=0)
    n = seed_all(tmp_path, tenant=0)
    assert n > 0
    reg = Registry.load(root)
    keys = {p["key"] for p in s.projects()}
    for bid in reg.businesses:
        assert bid.upper().replace("-", "_") in keys, f"사업 {bid} 의 프로젝트가 없다"
    assert seed_all(tmp_path, tenant=0) == 0        # 두 번 넣지 않는다


def test_seed_documents_are_pointers_not_copies(tmp_path):
    seed_all(tmp_path, tenant=0)
    s = BizStore(tmp_path, tenant=0)
    docs = [d for d in s.documents() if "COMPANY.md" in d["body"]]
    assert docs, "통제 문서 포인터가 없다"
    assert len(docs[0]["body"]) < 1000, "본문 사본을 두면 두 벌이 갈라진다"


# ── 작업 지시 (P7 DoD-1·2) ───────────────────────────────────────────────


def test_work_order_is_separate_from_inquiry(tmp_path):
    """문의는 '물어봄', 작업 지시는 '실행 단위'다. 한 테이블에 섞으면 결재·
    프로비저닝·집계가 문의에도 붙어 버린다."""
    s = BizStore(tmp_path, tenant=0)
    wid = s.add_work_order(title="데모 환경", body="본문", origin="external",
                           business="ax-consulting", division="ax",
                           infra_tier="container")
    iid = s.add_inquiry(name="a", email="a@b.co", message="문의입니다")
    assert s.work_order(wid)["title"] == "데모 환경"
    assert s.inquiry(iid)["message"] == "문의입니다"
    assert s.work_order(iid + 999) is None


def test_work_order_rejects_unknown_values(tmp_path):
    s = BizStore(tmp_path, tenant=0)
    for kw in ({"origin": "hacker"}, {"infra_tier": "cloud"}, {"title": "  "}):
        with pytest.raises(ValueError):
            s.add_work_order(title=kw.pop("title", "제목"), body="", **kw)


def test_intake_choices_come_from_business_manifests(root):
    """폼 선택지가 하드코딩이면 사업을 추가해도 접수할 수 없다."""
    from dawn_core import Registry, workintake

    cs = {c.id: c for c in workintake.choices(root, include_planned=True)}
    reg = Registry.load(root)
    assert set(cs) == set(reg.businesses), "사업 목록이 매니페스트와 다르다"
    for bid, c in cs.items():
        allowed = (reg.businesses[bid].data.get("infra") or {}).get("allowed", [])
        assert c.tiers == allowed, f"{bid}: 등급 선택지가 매니페스트와 다르다"


def test_intake_rejects_tier_the_business_forbids(root):
    """등급 선택은 사업이 허용한 범위 안에서만 — 화면이 아니라 규칙이 막는다."""
    from dawn_core import workintake

    with pytest.raises(ValueError, match="허용하지 않는다"):
        workintake.validate(root, business="foundation-model", infra_tier="container")
    with pytest.raises(ValueError, match="알 수 없는 사업"):
        workintake.validate(root, business="nope", infra_tier="none")
    div, tier = workintake.validate(root, business="ax-consulting",
                                    infra_tier="container")
    assert (div, tier) == ("ax", "container")


def test_approval_chain_is_derived_not_hardcoded(root):
    """결재 라인은 규칙에서 나온다 — 등급·민감도·출처가 대표이사를 부른다."""
    from dawn_core import workintake

    def roles(**kw):
        return [c["role"] for c in workintake.approval_chain(root, **kw)]

    # 기본: 본부장 1단계
    assert roles(business="ax-consulting", infra_tier="container",
                 division="ax", origin="internal") == ["AX본부장"]
    # vm 이상 → 외부 시스템 자원 점유 → 대표이사
    assert roles(business="ax-consulting", infra_tier="vm",
                 division="ax", origin="internal") == ["AX본부장", "대표이사"]
    # 외부 고객 요청 → 대표이사
    assert roles(business="ax-consulting", infra_tier="container",
                 division="ax", origin="external") == ["AX본부장", "대표이사"]
    # L3 사업 → 대표이사
    assert roles(business="foundation-model", infra_tier="vm",
                 division="aoc", origin="internal") == ["AOC본부장", "대표이사"]


def test_approval_chain_points_at_real_portal_accounts(root):
    """결재자가 실재하지 않으면 결재가 영원히 안 끝난다."""
    import sys

    sys.path.insert(0, str(root / "apps" / "groupware"))
    from dawn_core import Registry, workintake
    from dawn_groupware.auth import UserStore

    users = {u.username for u in UserStore(root).list()}
    if not users:
        pytest.skip("계정 없음")
    for bid in Registry.load(root).businesses:
        for tier in ("none", "container", "vm", "server"):
            try:
                div, t = workintake.validate(root, business=bid, infra_tier=tier)
            except ValueError:
                continue
            for c in workintake.approval_chain(root, business=bid, infra_tier=t,
                                               division=div, origin="external"):
                assert c["portal_user"] in users, \
                    f"{bid}/{t}: 결재자 {c['portal_user']} 계정이 없다"
