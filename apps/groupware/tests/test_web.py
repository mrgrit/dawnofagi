"""P4 — 홈페이지·그룹웨어.

이 스위트가 지키는 것:

* **공개 사이트는 내부에 손이 닿지 않는다** (임포트 경로 자체가 없다).
* **권한 = 조직 × 능력.** 능력이 있어도 조직 밖은 승인할 수 없다.
* **EG 변경은 검증을 통과해야만 반영된다.** 실패하면 시드가 자동 롤백된다.
* **감사 로그에 비밀번호가 남지 않는다.**
* HITL 승인이 P2 큐에 **실제로** 반영된다.
"""

from __future__ import annotations

import re
import secrets
import warnings
from dataclasses import dataclass, field

import pytest
from dawn_core.paths import Paths
from dawn_groupware.app import build_portal, build_site
from dawn_groupware.audit import AuditLog
from dawn_groupware.auth import (
    CAPABILITIES,
    User,
    UserStore,
    can_approve,
    hash_password,
    org_chain,
    verify_password,
)
from dawn_groupware.egedit import EDITABLE, EGEditError, EGEditor
from dawn_groupware.render import Safe, h, join, markdownish, page
from dawn_groupware.store import Store, seed_if_empty

warnings.filterwarnings("ignore", category=DeprecationWarning)


@dataclass
class _GateStub:
    """P2 게이트 판정 흉내 — 승인 큐가 기대하는 최소 인터페이스."""

    decision: str = "require_hitl"
    reasons: list = field(default_factory=list)
    severity: int = 3
    severity_label: str = "높음"
    assets: list = field(default_factory=list)
    policies: list = field(default_factory=list)


def _gate(**kw) -> _GateStub:
    return _GateStub(**kw)



@pytest.fixture(scope="module")
def root():
    return Paths().root


def client(app):
    from starlette.testclient import TestClient

    return TestClient(app, follow_redirects=False)


# ── 렌더 — XSS 는 타입으로 막는다 ────────────────────────────────────────


def test_escaping_is_the_default():
    evil = '<script>alert(1)</script>'
    assert "<script>" not in str(h(evil))
    assert "&lt;script&gt;" in str(h(evil))


def test_safe_passes_through():
    assert str(h(Safe("<b>ok</b>"))) == "<b>ok</b>"


def test_markdownish_escapes_before_formatting():
    out = str(markdownish("## 제목\n<img src=x onerror=alert(1)>\n**굵게**"))
    assert "<h3>제목</h3>" in out
    assert "onerror=alert(1)" not in out or "&lt;img" in out
    assert "<img" not in out
    assert "<strong>굵게</strong>" in out


def test_page_escapes_title():
    assert "<script>" not in page("<script>x</script>", Safe(""))


def test_safe_concat_stays_safe():
    """`Safe + Safe` 가 평범한 str 이 되면 상위 join 에서 통째로 이스케이프된다.

    화면에 태그가 글자로 찍히는, 조용히 망가지는 종류의 버그다. 폼 전체가
    쓸모없어지면서도 500 은 안 난다 — 그래서 테스트로 고정한다.
    """
    frag = Safe("<div>") + Safe("<b>x</b>") + Safe("</div>")
    assert isinstance(frag, Safe)
    assert str(frag) == "<div><b>x</b></div>"
    assert "<div>" in str(join([frag]))


def test_safe_concat_escapes_untrusted_side():
    frag = Safe("<div>") + "<script>alert(1)</script>"
    assert "<script>" not in str(frag)
    assert str(frag).startswith("<div>")


def test_every_post_form_carries_csrf():
    """CSRF 토큰이 빠진 POST 폼이 하나라도 있으면 그 화면은 위조 가능하다."""
    import inspect
    import re

    import dawn_groupware.portal as pm

    src = inspect.getsource(pm)
    assert re.search(r'method="post"', src), "POST 폼을 못 찾았다 — 정규식이 낡았다"
    for fn_name in ("_login_page", "_decision_form", "_notice_form", "_document_form",
                    "_event_form", "eg_node", "admin_users", "admin_user_edit"):
        fn_src = inspect.getsource(getattr(pm, fn_name))
        assert "csrf_field" in fn_src, f"{fn_name} 에 CSRF 토큰이 없다"
    # 모든 POST 핸들러는 check_csrf 를 거친다
    for _name, fn in vars(pm).items():
        if not callable(fn) or not getattr(fn, "__module__", "").endswith("portal"):
            continue
        body = inspect.getsource(fn) if inspect.isfunction(fn) else ""
        if "await request.form()" in body:
            assert "check_csrf" in body, f"{_name} 이 CSRF 검사를 안 한다"


# ── 비밀번호 ─────────────────────────────────────────────────────────────


def test_password_hash_roundtrip():
    stored = hash_password("correct-horse-battery")
    assert "correct-horse" not in stored          # 원문이 남으면 안 된다
    assert verify_password("correct-horse-battery", stored)
    assert not verify_password("wrong", stored)


def test_short_password_rejected():
    with pytest.raises(ValueError):
        hash_password("short")


def test_unknown_capability_is_not_a_capability():
    u = User(username="x", name="x", org="org:dawn", capabilities=["hitl.aprove"])
    assert not u.can("hitl.aprove")               # 오타는 권한이 아니다
    assert not u.can("hitl.approve")


def test_disabled_user_can_nothing():
    u = User(username="x", name="x", org="org:dawn",
             capabilities=list(CAPABILITIES), disabled=True)
    assert not u.can("admin")


# ── 권한 = 조직 × 능력 ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def eg(root):
    from dawn_core import Registry
    from dawn_core.eg.cli import db_path
    from dawn_core.eg.store import EGStore

    db = db_path(Registry.load(root).paths)
    if not db.is_file():
        pytest.skip("EG DB 없음")
    return EGStore(db)


def test_org_chain_walks_to_root(eg):
    chain = org_chain(eg, "org:ga")
    assert chain[0] == "org:ga"
    assert "org:mgmt" in chain and "org:dawn" in chain


def test_approver_must_be_in_org_tree(eg):
    ga = User(username="ga", name="ga", org="org:ga",
              capabilities=["hitl.approve"])
    ok, why = can_approve(ga, agent_org="org:ga", severity=3, eg_store=eg)
    assert ok, why
    # 다른 본부 소관은 못 누른다 — 승인은 그 일을 아는 조직이 한다
    ok, why = can_approve(ga, agent_org="org:ccc", severity=3, eg_store=eg)
    assert not ok and "조직 밖" in why


def test_parent_org_can_approve_child(eg):
    boss = User(username="boss", name="boss", org="org:mgmt",
                capabilities=["hitl.approve"])
    ok, _ = can_approve(boss, agent_org="org:ga", severity=3, eg_store=eg)
    assert ok, "상위 조직은 하위 조직을 승인할 수 있어야 한다"
    # 반대 방향은 안 된다
    sub = User(username="sub", name="sub", org="org:ga", capabilities=["hitl.approve"])
    ok, _ = can_approve(sub, agent_org="org:mgmt", severity=3, eg_store=eg)
    assert not ok


def test_critical_needs_extra_capability(eg):
    u = User(username="u", name="u", org="org:ga", capabilities=["hitl.approve"])
    ok, why = can_approve(u, agent_org="org:ga", severity=6, eg_store=eg)
    assert not ok and "critical" in why
    u2 = User(username="u2", name="u2", org="org:ga",
              capabilities=["hitl.approve", "hitl.approve.critical"])
    assert can_approve(u2, agent_org="org:ga", severity=6, eg_store=eg)[0]


def test_no_capability_no_approval(eg):
    u = User(username="u", name="u", org="org:ga", capabilities=["portal.view"])
    assert not can_approve(u, agent_org="org:ga", severity=1, eg_store=eg)[0]


def test_missing_agent_org_blocks_approval(eg):
    u = User(username="u", name="u", org="org:dawn",
             capabilities=["hitl.approve", "hitl.approve.critical"])
    ok, why = can_approve(u, agent_org="", severity=1, eg_store=eg)
    assert not ok and "조직 정보가 없다" in why


# ── 감사 로그 ────────────────────────────────────────────────────────────


def test_audit_never_records_secrets(tmp_path):
    # 리터럴로 두면 gitleaks 가 잡는다 — 그게 맞다. 런타임에 조립한다.
    secret = "s3cret" + "-value"
    token = "abc" + "123"
    log = AuditLog(tmp_path)
    log.write("auth.login", actor="x", **{"password": secret},
              nested={"token": token, "ok": 1})
    text = (tmp_path / "var" / "groupware" / "audit.jsonl").read_text(encoding="utf-8")
    assert secret not in text
    assert token not in text
    assert '"ok": 1' in text or '"ok":1' in text


def test_audit_is_append_only(tmp_path):
    log = AuditLog(tmp_path)
    for i in range(3):
        log.write("test.event", actor=f"u{i}")
    assert len(log.tail(10)) == 3
    assert not hasattr(log, "delete") and not hasattr(log, "clear")


def test_audit_filters(tmp_path):
    log = AuditLog(tmp_path)
    log.write("auth.login", actor="a")
    log.write("hitl.decide", actor="b")
    assert len(log.tail(10, action_prefix="hitl.")) == 1
    assert len(log.tail(10, actor="a")) == 1


# ── 테넌트 격리 ──────────────────────────────────────────────────────────


def test_tenant_isolation_is_structural(tmp_path):
    a = Store(tmp_path, tenant=0)
    b = Store(tmp_path, tenant=7)
    a.add_notice(title="자사 공지", body="", author="x")
    b.add_notice(title="고객사 공지", body="", author="y")
    assert [n["title"] for n in a.notices()] == ["자사 공지"]
    assert [n["title"] for n in b.notices()] == ["고객사 공지"]
    # 조회 함수가 tenant 인자를 받지 않는다 — 잘못된 값이 들어갈 자리가 없다
    import inspect

    assert "tenant" not in inspect.signature(Store.notices).parameters
    assert "tenant" not in inspect.signature(Store.documents).parameters
    assert a.foreign_rows() == 1


def test_document_level_hides_the_row_not_just_the_body(tmp_path):
    s = Store(tmp_path, tenant=0)
    s.add_document(title="급여 테이블", body="비밀", author="hr", security_level="L3")
    s.add_document(title="사내 위키", body="공개", author="x", security_level="L1")
    titles = [d["title"] for d in s.documents(max_level="L1")]
    assert titles == ["사내 위키"], "L3 문서는 제목도 나오면 안 된다"
    assert s.document(1, max_level="L1") is None


def test_seed_uses_registry_not_invented_content(tmp_path, root):
    from dawn_core import Registry

    reg = Registry.load(root)
    s = Store(tmp_path, tenant=0)
    n = seed_if_empty(s, reg)
    assert n >= 1
    titles = [d["title"] for d in s.documents()]
    for b in reg.businesses.values():
        assert f'[사업] {b.data["name"]}' in titles
    assert seed_if_empty(s, reg) == 0          # 두 번 넣지 않는다


# ── 공개 사이트 격리 ─────────────────────────────────────────────────────


def test_site_cannot_reach_internals():
    """공개 프로세스에 내부 자산으로 가는 임포트 경로가 없어야 한다."""
    import inspect

    from dawn_groupware import site

    src = inspect.getsource(site)
    for forbidden in ("from .store", "from .auth", "from .egedit", "from .audit",
                      "EGStore", "UserStore", "ApprovalQueue"):
        assert forbidden not in src, f"공개 사이트가 {forbidden} 를 참조한다"


def test_site_pages_render(root):
    c = client(build_site(root))
    for path in ("/", "/business", "/org", "/contact", "/healthz", "/robots.txt"):
        r = c.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}"
    assert "AGI" in c.get("/").text


def test_site_shows_businesses_from_registry(root):
    from dawn_core import Registry

    reg = Registry.load(root)
    html = client(build_site(root)).get("/business").text
    for b in reg.businesses.values():
        assert b.data["name"] in html


def test_contact_honeypot_blocks_bots(root):
    c = client(build_site(root))
    r = c.post("/contact", data={"name": "봇", "email": "b@x.com",
                                 "message": "링크 좀 봐주세요 " * 3,
                                 "website": "http://spam", "ts": "0"})
    assert r.status_code == 200
    assert "전송에 실패" in r.text


def test_contact_validates_email(root):
    c = client(build_site(root))
    r = c.post("/contact", data={"name": "홍길동", "email": "not-an-email",
                                 "message": "도입 문의드립니다. 일정 협의 원합니다.",
                                 "website": "", "ts": "1"})
    assert "이메일 형식" in r.text


def test_contact_rejects_too_fast(root):
    import time

    c = client(build_site(root))
    r = c.post("/contact", data={"name": "홍길동", "email": "a@b.com",
                                 "message": "도입 문의드립니다. 일정 협의 원합니다.",
                                 "website": "", "ts": str(int(time.time()))})
    assert "너무 빨리" in r.text


# ── 그룹웨어 — 인증 ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def portal(root):
    return build_portal(root, office_url="http://localhost:8800/")


@pytest.fixture(scope="module")
def users(root):
    return UserStore(root)


def _csrf_of(text: str) -> str:
    import re

    m = re.search(r'name="_csrf" value="([^"]+)"', text)
    assert m is not None, "CSRF 토큰이 없다 — 폼이 렌더되지 않았다"
    return m.group(1)


def _login(app, username: str, password: str | None = None):
    """비밀번호를 **매 호출 임의로 새로 세팅**하고 로그인한다.

    테스트 픽스처에 비밀번호를 상수로 두면 그게 곧 저장소에 커밋된 자격증명이
    된다 (05_conventions #1). 실행 중인 포털의 계정이 리포지토리만 보고
    뚫리면 안 된다.
    """
    if password is None:
        password = "t-" + secrets.token_urlsafe(16)
        UserStore(Paths().root).set_password(username, password)
    c = client(app)
    r = c.get("/login")
    assert r.status_code == 200
    r = c.post("/login", data={"username": username, "password": password,
                               "_csrf": _csrf_of(r.text), "next": "/"})
    return c, r


def test_anonymous_is_redirected_to_login(portal):
    c = client(portal)
    for path in ("/", "/approvals", "/eg", "/aoc", "/admin/users"):
        r = c.get(path)
        assert r.status_code == 303 and "/login" in r.headers["location"]


def test_login_requires_csrf(portal):
    c = client(portal)
    c.get("/login")
    r = c.post("/login", data={"username": "admin", "password": "x"})
    assert r.status_code == 400


def test_bad_password_is_rejected(portal, users):
    if users.get("admin") is None:
        pytest.skip("admin 계정 없음 — dawn-web bootstrap")
    _c, r = _login(portal, "admin", "definitely-wrong-password")
    assert r.status_code == 401


def test_login_and_whoami(portal, users):
    if users.get("intern") is None:
        pytest.skip("테스트 계정 없음")
    c, r = _login(portal, "intern")
    assert r.status_code == 303
    me = c.get("/api/whoami").json()
    assert me["username"] == "intern"
    assert "password_hash" not in me, "해시가 API 로 새면 안 된다"


def test_low_privilege_account_is_blocked_everywhere(portal, users):
    """자기검증 ① — 권한 없는 계정으로 EG 조정·관제 접근 시도 → 차단."""
    if users.get("intern") is None:
        pytest.skip("테스트 계정 없음")
    c, _ = _login(portal, "intern")
    for path in ("/eg", "/aoc", "/admin/users", "/approvals"):
        r = c.get(path)
        assert r.status_code == 403, f"{path} 가 {r.status_code} 로 열렸다"
        assert "권한이 없다" in r.text


def test_denied_access_is_audited(portal, users, root):
    if users.get("intern") is None:
        pytest.skip("테스트 계정 없음")
    c, _ = _login(portal, "intern")
    c.get("/eg")
    recs = AuditLog(root).tail(20, action_prefix="access.")
    assert any(r["actor"] == "intern" and r["result"] == "denied" for r in recs)


def test_portal_pages_render_for_admin(portal, users):
    if users.get("admin") is None:
        pytest.skip("admin 계정 없음")
    c, r = _login(portal, "admin")
    assert r.status_code == 303
    for path in ("/", "/approvals", "/eg", "/notices", "/documents",
                 "/calendar", "/directory", "/audit", "/admin/users"):
        assert c.get(path).status_code == 200, path


def test_directory_lists_agents_too(portal, users, root):
    from dawn_core import Registry
    c, _ = _login(portal, "admin")
    html = c.get("/directory").text
    reg = Registry.load(root)
    for ag in reg.agents.values():
        assert ag.data["name"] in html, "조직도에 에이전트가 빠지면 인력의 절반이 안 보인다"


def test_open_redirect_is_blocked(portal, root):
    pw = "t-" + secrets.token_urlsafe(16)
    UserStore(root).set_password("admin", pw)
    c = client(portal)
    r = c.get("/login")
    r = c.post("/login", data={"username": "admin", "password": pw,
                               "_csrf": _csrf_of(r.text), "next": "//evil.example.com/"})
    assert r.headers["location"] == "/"


# ── EG 조정 — 검증 실패는 롤백 ───────────────────────────────────────────


def test_editable_scope_is_narrow():
    """조직도·자산·존은 UI 로 안 고친다 — 그건 정책이 아니라 인프라의 사실이다."""
    assert set(EDITABLE) == {"persona", "policy"}


def test_eg_editor_rejects_unknown_field(root):
    ed = EGEditor(root)
    with pytest.raises(EGEditError):
        ed.update("persona", "persona:company-default", {"id": "persona:evil"},
                  actor="test", reason="테스트")


def test_eg_invalid_change_rolls_back(root):
    """검증 실패 = 롤백. 시드도 DB 도 그대로여야 한다."""
    ed = EGEditor(root)
    path = root / EDITABLE["policy"]["file"]
    before = path.read_text(encoding="utf-8")
    res = ed.update("policy", "pol:no-cross-tenant",
                    {"severity": "존재하지-않는-등급"}, actor="test", reason="롤백 테스트")
    assert not res.ok, "스키마 위반이 통과했다"
    assert path.read_text(encoding="utf-8") == before, "시드가 되돌아가지 않았다"
    assert res.snapshot


def test_eg_valid_change_applies_and_reverts(root):
    """자기검증 ③ — 실제로 고쳐지고, 되돌려도 검증을 탄다."""
    ed = EGEditor(root)
    node = ed.get("persona", "persona:company-default")
    assert node is not None
    marker = "P4 테스트 — 이 원칙은 테스트가 넣고 지운다"
    original = list(node["principles"])

    res = ed.update("persona", "persona:company-default",
                    {"principles": [*original, marker]},
                    actor="test", reason="P4 테스트")
    try:
        assert res.ok, f"{res.error}\n{res.validation}"
        assert any(marker in ln for ln in res.diff)
        # DB 에 실제로 반영됐나
        from dawn_core import Registry
        from dawn_core.eg.cli import db_path
        from dawn_core.eg.store import EGStore

        eg = EGStore(db_path(Registry.load(root).paths))
        n = eg.node("persona:company-default")
        assert marker in (n.prop("principles") or [])
    finally:
        back = ed.update("persona", "persona:company-default",
                         {"principles": original}, actor="test", reason="P4 테스트 원복")
        assert back.ok, back.error


def test_eg_change_reaches_worker_prompt(root):
    """자기검증 ③ — EG 를 고치면 **워커 프롬프트**가 바뀐다 (코드 변경 0)."""
    from dawn_agents import Worker

    ed = EGEditor(root)
    node = ed.get("persona", "persona:secops")
    if node is None:
        pytest.skip("persona:secops 없음")
    original = list(node["prohibited"])
    marker = "P4 개입 실증 — 이 문장이 프롬프트에 나타나야 한다"

    res = ed.update("persona", "persona:secops",
                    {"prohibited": [marker, *original]}, actor="test", reason="P4 개입 실증")
    try:
        assert res.ok, res.error
        prompt = Worker("ccc-soc-triage-01").system_prompt()
        assert marker in prompt, "EG 변경이 워커 프롬프트에 전파되지 않았다"
    finally:
        back = ed.update("persona", "persona:secops", {"prohibited": original},
                         actor="test", reason="P4 개입 실증 원복")
        assert back.ok, back.error


def test_eg_edit_requires_reason(portal, root):
    c, _ = _login(portal, "admin")
    r = c.get("/eg/persona/persona:company-default")
    import re

    tok = re.search(r'name="_csrf" value="([^"]+)"', r.text).group(1)
    r = c.post("/eg/persona/persona:company-default",
               data={"_csrf": tok, "_reason": "", "tone": "x"})
    assert r.status_code == 400 and "사유가 필요" in r.text


def test_eg_edit_needs_capability(portal, users):
    if users.get("ccc-lead") is None:
        pytest.skip("테스트 계정 없음")
    c, _ = _login(portal, "ccc-lead")
    assert c.get("/eg").status_code == 200            # eg.view 는 있다
    r = c.post("/eg/persona/persona:company-default", data={"_reason": "x"})
    assert r.status_code == 403                        # eg.edit 는 없다


# ── HITL — P2 큐에 실제로 반영 ───────────────────────────────────────────


def test_approval_decision_lands_in_p2_queue(portal, root, users):
    """자기검증 ② — 그룹웨어에서 누르면 P2 승인 큐의 상태가 바뀐다."""
    import re

    from dawn_agents.hitl import ApprovalQueue

    q = ApprovalQueue(root)

    gate = _gate(decision="require_hitl", reasons=["P4 테스트 — 경비 처리 승인"], severity=3,
                 severity_label="높음", assets=["asset:ledger"], policies=["pol:irreversible-hitl"])

    ap = q.request(agent_id="corp-admin-clerk-01", skill="fin.expense_read",
                   gate_decision=gate, args={"request_id": "P4-TEST"},
                   trace_id="p4-test")
    assert q.get(ap.id).status == "pending"
    c, r = _login(portal, "ga-manager")
    assert r.status_code == 303

    page_html = c.get(f"/approvals/{ap.id}").text
    assert "승인" in page_html
    tok = re.search(r'name="_csrf" value="([^"]+)"', page_html).group(1)
    r = c.post(f"/approvals/{ap.id}",
               data={"_csrf": tok, "decision": "approve", "ack": "1",
                     "note": "P4 자기검증 — 3자 대조 확인"})
    assert r.status_code == 303

    after = q.get(ap.id)
    assert after.status == "approved"
    assert after.decided_by == "human:ga-manager"
    assert "P4 자기검증" in after.note

    recs = AuditLog(root).tail(20, action_prefix="hitl.")
    assert any(x["target"] == ap.id and x["result"] == "approved" for x in recs)


def test_out_of_org_approval_is_refused(portal, root):
    """조직 밖 승인은 UI 에서도 API 에서도 막힌다."""
    import re

    from dawn_agents.hitl import ApprovalQueue

    q = ApprovalQueue(root)

    gate = _gate(decision="require_hitl", reasons=["P4 테스트 — CCC 소관"], severity=4,
                 severity_label="높음", assets=["asset:fw-ips"], policies=[])

    ap = q.request(agent_id="ccc-soc-triage-01", skill="sec.trace_query",
                   gate_decision=gate, args={}, trace_id="p4-test-2")
    c, _ = _login(portal, "ga-manager")
    detail = c.get(f"/approvals/{ap.id}").text
    assert "조직 밖" in detail

    tok_page = c.get("/notices").text
    tok = re.search(r'name="_csrf" value="([^"]+)"', tok_page).group(1)
    r = c.post(f"/approvals/{ap.id}",
               data={"_csrf": tok, "decision": "approve", "ack": "1", "note": "몰래"})
    assert r.status_code == 403
    assert q.get(ap.id).status == "pending", "조직 밖 승인이 통과했다"


def test_decided_approval_cannot_be_redecided(portal, root):
    import re

    from dawn_agents.hitl import ApprovalQueue

    q = ApprovalQueue(root)

    gate = _gate(decision="require_hitl", reasons=["P4 테스트"], severity=2,
                 severity_label="보통", assets=["asset:ledger"], policies=[])

    ap = q.request(agent_id="corp-admin-clerk-01", skill="fin.expense_read",
                   gate_decision=gate, args={}, trace_id="p4-test-3")
    q.decide(ap.id, approve=False, by="human:someone", note="먼저 거부")
    c, _ = _login(portal, "ga-manager")
    tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/notices").text).group(1)
    r = c.post(f"/approvals/{ap.id}",
               data={"_csrf": tok, "decision": "approve", "ack": "1"})
    assert r.status_code == 409
    assert q.get(ap.id).status == "denied"


def test_approval_requires_acknowledgement(portal, root):
    import re

    from dawn_agents.hitl import ApprovalQueue

    q = ApprovalQueue(root)

    gate = _gate(decision="require_hitl", reasons=["P4 테스트"], severity=3,
                 severity_label="높음", assets=["asset:ledger"], policies=[])

    ap = q.request(agent_id="corp-admin-clerk-01", skill="fin.expense_read",
                   gate_decision=gate, args={}, trace_id="p4-test-4")
    c, _ = _login(portal, "ga-manager")
    tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/notices").text).group(1)
    c.post(f"/approvals/{ap.id}", data={"_csrf": tok, "decision": "approve"})
    assert q.get(ap.id).status == "pending", "확인 체크 없이 승인됐다"


def test_critical_approval_blocked_without_capability(portal, root):
    import re

    from dawn_agents.hitl import ApprovalQueue

    q = ApprovalQueue(root)

    gate = _gate(decision="block", reasons=["P4 테스트 — 최고 심각도"], severity=6,
                 severity_label="최고", assets=["asset:payment"], policies=["pol:irreversible-hitl"])

    ap = q.request(agent_id="corp-admin-clerk-01", skill="pay.execute",
                   gate_decision=gate, args={"amount": 1}, trace_id="p4-test-5")
    c, _ = _login(portal, "ga-manager")
    assert "critical" in c.get(f"/approvals/{ap.id}").text
    tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/notices").text).group(1)
    r = c.post(f"/approvals/{ap.id}",
               data={"_csrf": tok, "decision": "approve", "ack": "1"})
    assert r.status_code == 403
    assert q.get(ap.id).status == "pending"


# ── 관제 연동 ────────────────────────────────────────────────────────────


def test_aoc_view_needs_capability(portal, users):
    if users.get("eg-steward") is None:
        pytest.skip("테스트 계정 없음")
    c, _ = _login(portal, "eg-steward")
    assert c.get("/aoc").status_code == 403        # aoc.view 없음
    assert c.get("/eg").status_code == 200


def test_aoc_view_shows_real_kpis(portal, root):
    c, _ = _login(portal, "ccc-lead")
    html = c.get("/aoc").text
    assert "KPI" in html and "픽셀 오피스" in html
    assert "태스크 성공률" in html


def test_portal_survives_missing_session_key_env(root):
    """세션 키는 var/ 에 만들어 재사용한다 — 재시작마다 전원 로그아웃되면 안 된다."""
    key = root / "var" / "groupware" / "session.key"
    assert key.is_file()
    assert len(key.read_text(encoding="utf-8").strip()) >= 32
    assert oct(key.stat().st_mode)[-3:] == "600"


def test_session_key_is_not_committed(root):
    import subprocess

    out = subprocess.run(["git", "check-ignore", "var/groupware/session.key"],
                         cwd=root, capture_output=True, text=True)
    assert out.returncode == 0, "세션 키가 gitignore 되지 않는다"


def test_users_file_is_not_committed(root):
    import subprocess

    out = subprocess.run(["git", "check-ignore", "var/groupware/users.json"],
                         cwd=root, capture_output=True, text=True)
    assert out.returncode == 0


def test_user_file_permissions(root):
    p = root / "var" / "groupware" / "users.json"
    if not p.is_file():
        pytest.skip("계정 파일 없음")
    assert oct(p.stat().st_mode)[-3:] == "600"


def test_no_password_hash_in_any_page(portal, root):
    c, _ = _login(portal, "admin")
    for path in ("/admin/users", "/directory", "/audit"):
        assert "pbkdf2_sha256$" not in c.get(path).text, f"{path} 에 해시가 노출됐다"


def test_admin_cannot_lock_themselves_out(portal, root):
    import re
    c, _ = _login(portal, "admin")
    tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/admin/users").text).group(1)
    r = c.post("/admin/users/admin",
               data={"_csrf": tok, "capabilities": ["portal.view"]})
    assert r.status_code == 400
    assert UserStore(root).get("admin").can("admin")


def test_json_error_page_has_no_stacktrace(portal):
    c = client(portal)
    r = c.get("/no-such-page")
    assert r.status_code == 404
    assert "Traceback" not in r.text


# ── 작업 지시 결재 (P7 DoD-2) ────────────────────────────────────────────


@pytest.fixture
def order(root):
    """결재 라인이 2단계(본부장 → 대표이사)인 작업 지시 하나."""
    from dawn_biz.store import BizStore

    s = BizStore(root)
    wid = s.add_work_order(
        title="[테스트] 결재 흐름", body="본문", origin="external",
        requester="테스트", business="ax-consulting", division="ax",
        infra_tier="container")
    s.set_work_order_status(wid, "pending_approval")
    yield wid
    s.db.execute("DELETE FROM work_order WHERE id=?", (wid,))
    s.db.commit()


def _order_csrf(c, wid):
    r = c.get(f"/orders/{wid}")
    assert r.status_code == 200
    m = re.search(r'name="_csrf" value="([^"]+)"', r.text)
    return m.group(1) if m else None


def test_approval_chain_is_two_steps_for_external(portal, users, order):
    if users.get("lead-ax") is None or users.get("ceo") is None:
        pytest.skip("결재 계정 없음")
    c, _ = _login(portal, "lead-ax")
    body = c.get(f"/orders/{order}").text
    assert "AX본부장" in body and "대표이사" in body, "결재 라인이 안 보인다"
    assert "외부 고객 요청" in body, "대표이사가 붙은 이유가 안 보인다"


def test_only_the_current_approver_sees_the_form(portal, users, order):
    """능력(hitl.approve)이 있어도 **차례가 아니면** 결재할 수 없다.

    결재 라인이 능력으로만 통제되면 아무 본부장이나 남의 본부 건을 승인할 수 있다.
    """
    if users.get("ceo") is None:
        pytest.skip("ceo 계정 없음")
    c, _ = _login(portal, "ceo")          # 2단계 결재자 — 아직 차례가 아니다
    assert _order_csrf(c, order) is None, "차례가 아닌데 결재 폼이 보인다"


def test_out_of_line_user_cannot_approve(portal, users, order):
    from dawn_biz.store import BizStore

    if users.get("intern") is None:
        pytest.skip("intern 계정 없음")
    c, _ = _login(portal, "intern")
    r = c.post(f"/orders/{order}", data={"decision": "approve", "_csrf": "x"})
    assert r.status_code in (303, 403)
    assert BizStore(Paths().root).work_order_approvals(order) == [], "라인 밖 사람이 결재했다"


def test_sequential_approval_completes_and_cannot_be_redecided(portal, users, order):
    from dawn_biz.store import BizStore

    if users.get("lead-ax") is None or users.get("ceo") is None:
        pytest.skip("결재 계정 없음")
    s = BizStore(Paths().root)

    c, _ = _login(portal, "lead-ax")
    tok = _order_csrf(c, order)
    assert tok, "차례인 본부장에게 결재 폼이 없다"
    c.post(f"/orders/{order}", data={"decision": "approve", "note": "승인", "_csrf": tok})
    assert [a["decision"] for a in s.work_order_approvals(order)] == ["approved"]
    assert s.work_order(order)["status"] == "pending_approval", "아직 대표이사가 남았다"

    c, _ = _login(portal, "ceo")
    tok = _order_csrf(c, order)
    assert tok, "1단계가 끝났는데 대표이사에게 폼이 없다"
    c.post(f"/orders/{order}", data={"decision": "approve", "_csrf": tok})
    assert s.work_order(order)["status"] == "approved"
    assert len(s.work_order_approvals(order)) == 2

    # 재판정 불가 — 감사 추적
    c, _ = _login(portal, "ceo")
    assert _order_csrf(c, order) is None, "끝난 결재에 폼이 다시 보인다"


def test_rejection_stops_the_chain(portal, users, order):
    from dawn_biz.store import BizStore

    if users.get("lead-ax") is None:
        pytest.skip("lead-ax 계정 없음")
    s = BizStore(Paths().root)
    c, _ = _login(portal, "lead-ax")
    tok = _order_csrf(c, order)
    c.post(f"/orders/{order}", data={"decision": "reject", "note": "범위 밖", "_csrf": tok})
    assert s.work_order(order)["status"] == "rejected"

    c, _ = _login(portal, "ceo") if users.get("ceo") else (None, None)
    if c is not None:
        assert _order_csrf(c, order) is None, "반려됐는데 다음 결재자에게 폼이 보인다"


def test_internal_order_needs_no_business(portal, users, root):
    """사업 없이도 접수된다 — 경리·문의 응대·시스템 운영은 수익 사업이 아니지만
    누군가는 해야 하는 일이다. 사업을 필수로 두면 경영관리부처럼 어느 사업의
    소관도 아닌 본부는 작업 지시를 아예 못 만든다 (QUESTIONS Q12)."""
    from dawn_biz.store import BizStore

    c, _ = _login(portal, "admin")
    r = c.get("/orders")
    assert "내부 지원 업무" in r.text, "사업 없이 접수하는 길이 폼에 없다"
    tok = re.search(r'name="_csrf" value="([^"]+)"', r.text).group(1)

    c.post("/orders", data={"title": "[테스트] 내부 경리 처리", "business": "",
                            "division": "corp", "infra_tier": "none",
                            "body": "본문", "_csrf": tok})
    s = BizStore(root)
    row = next((w for w in s.work_orders()
                if w["title"] == "[테스트] 내부 경리 처리"), None)
    try:
        assert row is not None, "내부 업무가 접수되지 않았다"
        assert row["business"] == "" and row["division"] == "corp"
        assert row["status"] == "pending_approval"
    finally:
        if row:
            s.db.execute("DELETE FROM work_order WHERE id=?", (row["id"],))
            s.db.commit()


def test_internal_order_cannot_take_external_infrastructure(portal, users):
    """사업 없는 내부 업무가 외부 시스템 자원(vm/server)을 점유할 수 없다 —
    비용 귀속처가 없기 때문이다. 필요하면 사업을 붙여서 올린다."""
    c, _ = _login(portal, "admin")
    tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/orders").text).group(1)
    r = c.post("/orders", data={"title": "[테스트] 서버 달라", "business": "",
                                "division": "corp", "infra_tier": "vm",
                                "body": "", "_csrf": tok})
    assert "비용 귀속처" in r.text, "등급 제한이 화면에 안 보인다"


def test_internal_order_must_name_a_division(portal, users):
    """사업도 본부도 없으면 결재 라인을 못 만든다 — 받아 두면 갈 곳 없는 지시가 쌓인다."""
    c, _ = _login(portal, "admin")
    tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/orders").text).group(1)
    r = c.post("/orders", data={"title": "[테스트] 본부 없음", "business": "",
                                "division": "", "infra_tier": "none",
                                "body": "", "_csrf": tok})
    assert "담당 본부를 골라야" in r.text


# ── 통제 평면 웹 조정 ─────────────────────────────────────────────────────


def test_control_page_needs_the_capability(portal, users):
    """통제 평면은 에이전트의 행동을 바꾼다 — 아무나 못 본다."""
    c, _ = _login(portal, "admin")
    assert c.get("/control").status_code == 200

    plain = next((u for u in users.list() if not u.can("control.view")), None)
    if plain is None:
        pytest.skip("control.view 없는 계정이 없다")
    c2, _ = _login(portal, plain.username)
    assert c2.get("/control").status_code in (403, 302, 303)


def test_viewer_cannot_write(portal, users, root):
    """열람만 있는 사람이 POST 로 곧장 쏘면 막혀야 한다 — 화면에서 버튼을 숨기는
    것은 통제가 아니다."""
    from dawn_groupware.auth import UserStore

    st = UserStore(root)
    if st.get("cp-viewer") is None:
        st.create("cp-viewer", "12341234", name="[테스트] 열람자", org="test",
                  capabilities=["portal.view", "control.view"])
    try:
        c, _ = _login(portal, "cp-viewer")
        tok = re.search(r'name="_csrf" value="([^"]+)"',
                        c.get("/control/soul/ax-univ-diag-01").text)
        r = c.post("/control/soul/ax-univ-diag-01",
                   data={"text": "덮어쓰기", "_reason": "무단",
                         "_csrf": tok.group(1) if tok else "x"})
        assert r.status_code == 403
        assert "덮어쓰기" not in (root / "org" / "agents" / "ax-univ-diag-01"
                                / "SOUL.md").read_text(encoding="utf-8")
    finally:
        st.delete("cp-viewer")      # 알려진 비밀번호를 가진 계정을 남기지 않는다


def test_saving_a_widened_gate_is_refused_through_http(portal, users, root):
    """UI 를 거쳐도 경계는 넓어지지 않는다."""
    p = root / "org" / "divisions" / "ax" / "university" / "gate.yaml"
    before = p.read_text(encoding="utf-8")
    c, _ = _login(portal, "admin")
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    c.get("/control/gate/ax-university").text).group(1)
    r = c.post("/control/gate/ax-university",
               data={"text": before.replace("  allow:", "  allow:\n    - pay.execute"),
                     "_reason": "넓히기", "_csrf": tok})
    assert r.status_code == 400
    assert "저장하지 않았다" in r.text
    assert p.read_text(encoding="utf-8") == before, "파일이 바뀐 채 남았다"
