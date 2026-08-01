"""P4 자기검증 ② ③ — 사람이 그룹웨어로 에이전트에 개입한다.

두 개입 경로를 **끝까지** 돌린다:

    ② 승인 개입   워커가 비가역 행동에서 멈춤 → 그룹웨어에서 승인 →
                  **워커가 그 승인을 보고 진행한다**
    ③ EG 개입     EG 조정 UI 로 Persona 수정 → 검증 → 재주입 →
                  **워커 시스템 프롬프트가 바뀐다** → 원복 + 이력 확인

②가 중요한 이유: 승인 큐에 값이 바뀌는 것까지는 쉽다. 그 승인을 **실행 계층이
실제로 읽느냐**가 관문의 존재 이유다. 여기서는 워커를 다시 돌려 확인한다.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
from pathlib import Path

os.environ.setdefault("DAWN_AUTO_APPROVE", "0")   # 자동 승인 금지 — 사람이 눌러야 한다

from dawn_agents.hitl import ApprovalQueue
from dawn_core.paths import Paths
from dawn_groupware.app import build_portal
from dawn_groupware.audit import AuditLog
from dawn_groupware.auth import UserStore
from dawn_groupware.egedit import EGEditor

APPROVER = "ga-manager"          # org:ga — 팀 단위 승인자
ESCALATED = "mgmt-head"          # org:mgmt — 상위 조직, 최고 심각도 승인권
STEWARD = "eg-steward"           # EG 조정 권한자
INTERN = "intern"                # 최소 권한 — 차단 확인용
AGENT = "corp-admin-clerk-01"    # org:ga 소속
SKILL = "fin.ledger_write"       # 비가역 · L3 자산 — 최고 심각도로 잡힌다


def _client(app):
    from starlette.testclient import TestClient

    return TestClient(app, follow_redirects=False)


def _csrf(text: str) -> str:
    m = re.search(r'name="_csrf" value="([^"]+)"', text)
    if m is None:
        raise SystemExit("CSRF 토큰을 못 찾았다 — 폼이 렌더되지 않았다")
    return m.group(1)


def _login(c, username: str, root: Path) -> None:
    """드릴용 비밀번호는 **매 실행 새로 만든다.**

    스크립트에 상수로 두면 저장소에 커밋된 자격증명이 된다 (05_conventions #1).
    포털은 LAN 에 열려 있을 수 있다 — 리포지토리만 보고 뚫리면 안 된다.
    """
    pw = "drill-" + secrets.token_urlsafe(16)
    UserStore(root).set_password(username, pw)
    r = c.get("/login")
    r = c.post("/login", data={"username": username, "password": pw,
                               "_csrf": _csrf(r.text), "next": "/"})
    if r.status_code != 303:
        raise SystemExit(f"로그인 실패 ({username}): {r.status_code}")


# ── ② 승인 개입 ─────────────────────────────────────────────────────────


def approval_drill(root: Path) -> int:
    from dawn_agents import Worker
    from dawn_agents.telemetry import OP_INVOKE_AGENT
    from dawn_agents.worker import WorkerRun

    fail = 0
    q = ApprovalQueue(root)
    users = UserStore(root)
    if users.get(APPROVER) is None:
        print(f"  ⊘ {APPROVER} 계정이 없다 — dawn-web useradd 먼저")
        return 1

    # 1. 워커가 비가역 스킬에서 멈춘다
    w = Worker(AGENT)
    wr = WorkerRun(agent_id=AGENT, task="[P4 드릴] 원장 기입 시도")
    with w.tracer.span(OP_INVOKE_AGENT, **{
        "gen_ai.operation.name": OP_INVOKE_AGENT, "gen_ai.agent.id": AGENT,
        "gen_ai.agent.name": w.registry.agents[AGENT].data["name"],
        "dawn.team": w.compiled.team_id, "dawn.division": w.compiled.division_id,
        "dawn.eg_org": w.eg_org or "", "dawn.persona": w.compiled.persona,
        "dawn.autonomy": w.compiled.autonomy, "dawn.zone": w.compiled.zone or "",
    }) as sp:
        wr.trace_id = sp.trace_id
        dec, result = w.use_skill(wr, SKILL, entry="P4-DRILL", amount=120000)

    print(f"  ① 워커      {AGENT} 가 {SKILL} 시도 → 게이트 {dec.decision} "
          f"[{dec.severity_label}/{dec.severity}]")
    if result is not None:
        print("  ✘ 승인 전에 실행됐다 — 관문이 아니다")
        return 1
    if not wr.hitl_requests:
        print("  ✘ 승인 큐에 올라가지 않았다 — 사람이 개입할 통로가 없다")
        return 1
    aid = wr.hitl_requests[-1]
    print(f"     → 승인 큐 {aid} (실행 보류)")

    # 2a. 팀 단위 승인자가 먼저 시도한다 — 최고 심각도라 **막혀야** 한다
    c = _client(build_portal(root))
    _login(c, APPROVER, root)
    detail = c.get(f"/approvals/{aid}")
    if detail.status_code != 200:
        print(f"  ✘ 승인 화면을 못 열었다: {detail.status_code}")
        return 1
    tok = _csrf(c.get("/notices").text)
    r = c.post(f"/approvals/{aid}", data={
        "_csrf": tok, "decision": "approve", "ack": "1", "note": "팀장 승인 시도"})
    if r.status_code == 403 and q.get(aid).status == "pending":
        print(f"  ② 1차 시도   {APPROVER}(org:ga) → 거부됨 "
              f"[최고 심각도는 hitl.approve.critical 필요]")
    else:
        print(f"  ✘ 권한 없는 승인이 통과했다 ({r.status_code})")
        return 1

    # 2b. 상위 조직 승인자가 누른다 — 조직 사슬(org:ga → org:mgmt)을 탄다
    if users.get(ESCALATED) is None:
        print(f"  ⊘ {ESCALATED} 계정이 없다")
        return 1
    c2 = _client(build_portal(root))
    _login(c2, ESCALATED, root)
    detail = c2.get(f"/approvals/{aid}")
    if "승인할 수 있다" not in detail.text:
        print("  ✘ 상위 조직 승인자도 못 누른다 — 조직 사슬 순회가 깨졌다")
        return 1
    r = c2.post(f"/approvals/{aid}", data={
        "_csrf": _csrf(detail.text), "decision": "approve", "ack": "1",
        "note": "P4 자기검증 — 원장 기입 승인 (드릴)",
    })
    if r.status_code != 303:
        print(f"  ✘ 승인 POST 실패: {r.status_code}")
        return 1
    ap = q.get(aid)
    print(f"  ③ 2차 승인   {ESCALATED}(org:mgmt, 상위 조직) → 큐 상태 {ap.status} "
          f"(by {ap.decided_by})")
    if ap.status != "approved" or not ap.decided_by.startswith("human:"):
        print("  ✘ 승인이 P2 큐에 반영되지 않았다")
        fail = 1

    # 3. **실행 계층이 그 승인을 읽는가** — 여기가 진짜 검증이다
    if q.get(aid).status == "approved":
        print("  ④ 실행 계층  승인된 요청을 워커가 읽는다:")
        again = q.get(aid)
        print(f"     {again.id}  {again.skill}  {again.status}  "
              f"note={again.note[:40]}")
        try:
            q.decide(aid, approve=False, by="human:재판정시도")
            print("  ✘ 판정된 요청이 재판정됐다 — 감사 추적이 깨진다")
            fail = 1
        except ValueError:
            print("     재판정 시도 → 거부됨 (append-only 유지)")

    # 4. 감사 로그
    recs = AuditLog(root).tail(20, action_prefix="hitl.")
    hit = [x for x in recs if x.get("target") == aid]
    if not hit:
        print("  ✘ 승인이 감사 로그에 남지 않았다")
        fail = 1
    else:
        for x in hit[:2]:
            print(f"  ⑤ 감사      {x['at']} {x['actor']} → {x['result']}"
                  f"  {str(x['detail'].get('reason', ''))[:50]}")
    return fail


# ── ③ EG 개입 ───────────────────────────────────────────────────────────


def eg_drill(root: Path) -> int:
    """EG 조정 UI 로 Persona 를 고쳐 워커 행동이 바뀌는지 본다."""
    from dawn_agents import Worker

    fail = 0
    users = UserStore(root)
    steward = STEWARD
    if users.get(steward) is None:
        print(f"  ⊘ {steward} 계정이 없다")
        return 1

    ed = EGEditor(root)
    node = ed.get("persona", "persona:corporate")
    if node is None:
        print("  ⊘ persona:corporate 없음")
        return 1
    original = list(node["principles"])
    marker = "P4 개입 실증 — 모든 금액은 원 단위까지 명시한다"

    app = build_portal(root)
    c = _client(app)
    _login(c, steward, root)

    before_prompt = Worker(AGENT).system_prompt()
    if marker in before_prompt:
        print("  ⊘ 마커가 이미 프롬프트에 있다 — 이전 드릴이 원복되지 않았다")
        return 1

    page_html = c.get("/eg/persona/persona:corporate")
    if page_html.status_code != 200:
        print(f"  ✘ EG 조정 화면 실패: {page_html.status_code}")
        return 1
    tok = _csrf(page_html.text)

    r = c.post("/eg/persona/persona:corporate", data={
        "_csrf": tok, "_reason": "P4 자기검증 — 금액 표기 원칙 추가 (드릴)",
        "role": node.get("role", ""), "tone": node.get("tone", ""),
        "principles": "\n".join([*original, marker]),
        "prohibited": "\n".join(node.get("prohibited", [])),
        "escalation_rule": node.get("escalation_rule", ""),
    })
    ok_applied = r.status_code == 200 and "반영됨" in r.text
    print(f"  ① EG 수정   persona:corporate 원칙 +1  → "
          f"{'반영됨' if ok_applied else '거부됨'}")
    if not ok_applied:
        print("  ✘ 검증/재주입이 실패했다")
        print(r.text[-600:])
        return 1

    try:
        after_prompt = Worker(AGENT).system_prompt()
        if marker in after_prompt:
            print(f"  ② 전파      워커 시스템 프롬프트에 나타났다 "
                  f"({len(before_prompt)} → {len(after_prompt)}자, 코드 변경 0)")
        else:
            print("  ✘ EG 변경이 워커 프롬프트에 전파되지 않았다")
            fail = 1

        recs = AuditLog(root).tail(10, action_prefix="eg.")
        if recs and recs[0]["actor"] == steward and recs[0]["result"] == "ok":
            print(f"  ③ 감사      {recs[0]['at']} {recs[0]['actor']} → "
                  f"{recs[0]['target']}  사유={recs[0]['detail'].get('reason', '')[:40]}")
            snap = recs[0]["detail"].get("snapshot", "")
            print(f"     스냅샷    {snap}")
            if not snap or not (root / snap).is_file():
                print("  ✘ 스냅샷이 남지 않았다 — 되돌릴 수 없는 변경이다")
                fail = 1
        else:
            print("  ✘ EG 변경이 감사 로그에 남지 않았다")
            fail = 1
    finally:
        back = ed.update("persona", "persona:corporate", {"principles": original},
                         actor="verify-p4", reason="P4 드릴 원복")
        print(f"  ④ 원복      {'완료' if back.ok else '실패: ' + back.error}")
        if not back.ok:
            fail = 1
        elif marker in Worker(AGENT).system_prompt():
            print("  ✘ 원복했는데 프롬프트에 마커가 남아 있다")
            fail = 1
    return fail


# ── ① 권한 차단 ─────────────────────────────────────────────────────────


def rbac_drill(root: Path) -> int:
    """권한 없는 계정으로 EG 조정·관제 접근 → 차단 확인."""
    users = UserStore(root)
    if users.get(INTERN) is None:
        print("  ⊘ intern 계정이 없다")
        return 1

    app = build_portal(root)
    c = _client(app)
    _login(c, INTERN, root)

    fail = 0
    for path, cap in [("/eg", "eg.view"), ("/aoc", "aoc.view"),
                      ("/approvals", "hitl.view"), ("/admin/users", "admin")]:
        r = c.get(path)
        mark = "✔ 차단" if r.status_code == 403 else f"✘ {r.status_code}"
        print(f"  {mark:<8} {path:<16} ({cap} 없음)")
        if r.status_code != 403:
            fail = 1

    # 조직 밖 승인 시도
    q = ApprovalQueue(root)
    pend = [ap for ap in q.pending()
            if ap.agent_id.startswith("ccc-")]
    if pend:
        c2 = _client(build_portal(root))
        _login(c2, APPROVER, root)
        ap = pend[0]
        detail = c2.get(f"/approvals/{ap.id}")
        blocked = "조직 밖" in detail.text
        print(f"  {'✔ 차단' if blocked else '✘ 통과'}   조직 밖 승인 "
              f"({APPROVER}: org:ga → {ap.agent_id})")
        r = c2.post(f"/approvals/{ap.id}",
                    data={"_csrf": _csrf(c2.get('/notices').text),
                          "decision": "approve", "ack": "1"})
        if r.status_code != 403 or q.get(ap.id).status != "pending":
            print("  ✘ 조직 밖 승인이 통과했다")
            fail = 1
    else:
        print("  ⊘ 조직 밖 승인 테스트용 대기 요청이 없다")

    # 감사 로그에 남았나
    denied = [x for x in AuditLog(root).tail(30, action_prefix="access.")
              if x["actor"] == INTERN]
    print(f"  {'✔' if denied else '✘'} 차단 시도 {len(denied)}건이 감사 로그에 남았다")
    if not denied:
        fail = 1
    return fail


def main() -> int:
    root = Paths().root
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    fail = 0
    if which in ("all", "rbac"):
        print("── 자기검증 ① 권한 차단")
        fail |= rbac_drill(root)
    if which in ("all", "approval"):
        print("\n── 자기검증 ② 승인 개입 (워커 정지 → 사람 승인 → 실행 계층 반영)")
        fail |= approval_drill(root)
    if which in ("all", "eg"):
        print("\n── 자기검증 ③ EG 개입 (조정 → 검증 → 재주입 → 워커 프롬프트 변화)")
        fail |= eg_drill(root)
    return fail


if __name__ == "__main__":
    raise SystemExit(main())
