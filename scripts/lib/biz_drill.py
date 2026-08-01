"""P5 자기검증 — 업무 에이전트가 실제로 업무를 처리하고 관제에 나타나는가.

    ① 문의 이벤트 → CRM 에이전트 기동 → 정형 응답 초안 → 관제 콘솔에 활동 표시
    ② 경리 에이전트에 L3(경비) 작업 → **로컬 모델** + HITL 게이트
    ③ 업무 자산이 EG 에 실재하고 관제 섹터(존)에 배치됨

`--live` 없이는 모델을 부르지 않고 구조만 본다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dawn_agents.events import Event
from dawn_biz import egsync
from dawn_biz.events import ingest_inquiries, run_event
from dawn_biz.store import BizStore
from dawn_core import Registry
from dawn_core.eg.cli import db_path
from dawn_core.eg.store import EGStore
from dawn_core.paths import Paths

G, R, Y, D, Z = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _eg(root: Path):
    db = db_path(Registry.load(root).paths)
    return EGStore(db) if db.is_file() else None


# ── ① CRM ───────────────────────────────────────────────────────────────


def crm_drill(root: Path, live: bool) -> int:
    store = BizStore(root, tenant=0)
    n, ids = ingest_inquiries(root, tenant=0)
    if n:
        print(f"  흡수      홈페이지 접수함 → CRM  새 {n}건 {ids}")

    pending = [r for r in store.inquiries() if r["status"] == "new"]
    target = pending[0] if pending else (store.inquiries() or [None])[0]
    if target is None:
        print("  ⊘ 처리할 문의가 없다 — 홈페이지 문의 폼으로 하나 넣어라")
        return 1

    if not live:
        print(f"  ⊘ --live 없이는 모델을 부르지 않는다 (문의 #{target['id']} 대기)")
        _report_inquiry(store, target["id"])
        return 0

    print(f"  ① 이벤트   crm.inquiry.new  문의 #{target['id']} "
          f"({target['name']} / {target['org_name']})")
    results = run_event(Event(type="crm.inquiry.new", source="website",
                              payload={"inquiry_id": target["id"]}), root=root)
    if not results:
        print("  ✘ 이벤트에 핸들러가 없다 — 훅 등록이 빠졌다")
        return 1
    res = results[0]
    print(f"  ② 에이전트 {res.line().strip()}")
    if not res.ok:
        print(f"  ✘ 처리 실패: {res.error}")
        return 1
    return _report_inquiry(store, target["id"])


def _report_inquiry(store: BizStore, iid: int) -> int:
    row = store.inquiry(iid)
    fail = 0
    print(f"  ③ 결과     상태={row['status']}  분류={row['category'] or '(미분류)'}  "
          f"작성자={row['drafted_by'] or '-'}")
    if row["status"] == "drafted":
        if not row["draft"]:
            print("  ✘ 초안이 비었다")
            fail = 1
        else:
            head = row["draft"].strip().splitlines()[0][:70]
            print(f"     초안 {len(row['draft'])}자 · trace {row['trace_id'][:12]}")
            print(f"     {D}{head}{Z}")
        # **발송되지 않았다** — 이게 이 업무의 경계다
        if row["status"] in ("sent", "closed"):
            print("  ✘ 발송 상태가 됐다 — 초안까지가 이 업무의 끝이다")
            fail = 1
    return fail


# ── ② 경리 (L3) ─────────────────────────────────────────────────────────


def expense_drill(root: Path, live: bool) -> int:
    store = BizStore(root, tenant=0)
    over = [r for r in store.expenses() if int(r["amount_krw"]) > 100_000]
    if not over:
        print("  ⊘ 임계 초과 경비가 없다 — dawn-biz seed")
        return 1
    row = over[0]
    print(f"  대상      {row['request_id']}  {int(row['amount_krw']):,}원 "
          f"({row['category']})  [{row['security_level']}]")

    # 라우팅은 모델을 부르지 않고도 확인할 수 있다
    from dawn_core.eg.traverse import model_for_org

    eg = _eg(root)
    fail = 0
    for l3 in (False, True):
        r = model_for_org(eg, "org:ga", touches_l3=l3)
        tag = "L3 관여" if l3 else "평시"
        print(f"  라우팅    {tag:<8} → {r['model'] or '차단'}  "
              f"{'(로컬 강제)' if r['forced_local'] else ''}")
        if not r["forced_local"]:
            print(f"  ✘ 경리 조직이 {tag} 에 클라우드로 나간다 — pol:l3-local-only 위반")
            fail = 1

    if not live:
        print("  ⊘ --live 없이는 모델을 부르지 않는다")
        return fail

    os.environ["DAWN_AUTO_APPROVE"] = "1"    # 사람이 승인 버튼을 누른 것과 같은 효과
    print(f"  {Y}⚠ HITL 자동 승인 (검증용){Z}")
    results = run_event(Event(type="expense.submitted", source="groupware",
                              payload={"request_id": row["request_id"]}), root=root)
    if not results:
        print("  ✘ expense.submitted 핸들러가 없다")
        return 1
    res = results[0]
    print(f"  실행      {res.line().strip()}")
    if not res.local:
        print("  ✘ L3 인데 로컬 모델로 가지 않았다")
        fail = 1
    if not res.hitl_ids:
        print("  ✘ L3 인데 HITL 게이트가 걸리지 않았다")
        fail = 1
    else:
        print(f"  게이트    HITL {len(res.hitl_ids)}건 — {', '.join(res.hitl_ids)}")

    after = store.expense_by_request(row["request_id"])
    print(f"  판정      상태={after['status']}  처리자={after['processed_by']}  "
          f"판정문 {len(after['verdict'])}자")
    if after["status"] != "needs_approval":
        print(f"  ✘ 임계 초과인데 상태가 {after['status']} 다 — 승인 대기여야 한다")
        fail = 1
    if after["ledger_entry"]:
        print("  ✘ 원장에 기입됐다 — 초안까지가 끝이다")
        fail = 1
    return fail


# ── ③ EG 자산 · 관제 섹터 ───────────────────────────────────────────────


def asset_drill(root: Path) -> int:
    store, eg = BizStore(root, tenant=0), _eg(root)
    if eg is None:
        print("  ✘ EG DB 없음")
        return 1
    checks = egsync.check(store, eg)
    summ = egsync.summary(checks)
    for c in checks:
        print(c.line())
        for p in c.problems:
            print(f"      {R}✘ {p}{Z}")
    zones = egsync.zone_rows(store, eg)
    print("\n  존별 배치  " + ", ".join(f"{z}={n}" for z, n in sorted(zones.items())))
    fail = 0
    if summ["problems"]:
        fail = 1
    if "(미배정)" in zones:
        print("  ✘ 존에 배치되지 않은 업무 데이터가 있다 — 픽셀 오피스에 방이 없다")
        fail = 1

    # 관제가 이 자산들의 심각도를 실제로 계산하는가
    from dawn_core.eg.traverse import severity_of

    print()
    for c in checks:
        if not c.exists:
            continue
        sev = severity_of(eg, c.asset_id)
        print(f"  심각도    {c.asset_id:<22} {sev.icon}{sev.label}({sev.score}) "
              f"= {sev.irreversibility} × {sev.sec_id}")
    return fail


# ── ④ 관제 반영 ─────────────────────────────────────────────────────────


def aoc_drill(root: Path) -> int:
    """업무 에이전트의 활동이 P3 관제에 나타나는가."""
    from dawn_aoc.collect import TraceLake
    from dawn_aoc.console import build_state

    lake = TraceLake(root)
    biz_agents = {"corp-cs-crm-01", "corp-admin-clerk-01", "aoc-dev-pm-01"}
    runs = [r for r in lake.all_runs(limit=100) if r.agent_id in biz_agents]
    if not runs:
        print("  ⊘ 업무 에이전트의 트레이스가 없다 — --live 로 먼저 돌려라")
        return 1
    print(f"  수집      업무 에이전트 run {len(runs)}건")
    fail = 0
    st = build_state(root, limit=100)
    for aid in sorted({r.agent_id for r in runs}):
        a = next((x for x in st["agents"] if x["agent_id"] == aid), None)
        if a is None:
            print(f"  ✘ {aid} 가 관제 상태에 없다")
            fail = 1
            continue
        print(f"  아바타    {aid:<22} {a['room']:<16} {a['badge'] or '-':<7} "
              f"{a['effect']:<9} run {a['runs']}  EG {len(a['eg_refs'])}")
        if a["runs"] == 0:
            print(f"  ✘ {aid} 의 실행이 관제에 안 잡혔다")
            fail = 1
        if not a["eg_refs"]:
            print(f"  ! {aid} 의 EG 참조가 비었다 — 픽셀 오피스에 아이콘이 안 뜬다")
    return fail


def main() -> int:
    root = Paths().root
    live = "--live" in sys.argv
    which = next((a for a in sys.argv[1:] if not a.startswith("-")), "all")
    fail = 0
    if which in ("all", "crm"):
        print("── 자기검증 ① 문의 이벤트 → CRM 에이전트 → 초안 (발송 아님)")
        fail |= crm_drill(root, live)
    if which in ("all", "expense"):
        print("\n── 자기검증 ② 경리 L3 → 로컬 모델 + HITL")
        fail |= expense_drill(root, live)
    if which in ("all", "asset"):
        print("\n── 자기검증 ③ 업무 자산 EG 등록 + 관제 섹터 배치")
        fail |= asset_drill(root)
    if which in ("all", "aoc"):
        print("\n── 자기검증 ④ 업무 에이전트 활동이 관제에 나타나는가")
        fail |= aoc_drill(root)
    return fail


if __name__ == "__main__":
    raise SystemExit(main())
