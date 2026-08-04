"""dawn-biz — 업무 시스템 CLI.

    dawn-biz seed                    데모 업무 데이터 주입 (레지스트리 기반)
    dawn-biz docs [--search Q]       문서·지식
    dawn-biz crm                     고객·문의·계약
    dawn-biz proj [KEY]              프로젝트·태스크
    dawn-biz acct                    경비·자산 대장 (L3)
    dawn-biz egcheck                 업무 데이터 ↔ EG 자산 정합성
    dawn-biz run inquiry <id>        문의 처리 에이전트 기동 (모델 호출)
    dawn-biz run expense <req-id>    경비 처리 에이전트 기동 (L3 — 로컬 모델)
    dawn-biz run project <key>       프로젝트 조율 에이전트 기동
    dawn-biz emit <type> --payload   업무 이벤트 발생 (훅 기동)
    dawn-biz intake                  홈페이지 문의 접수함 → CRM 으로 흡수
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dawn_agents import load_dotenv
from dawn_core.paths import Paths

from . import egsync
from .store import BizStore

B, D, G, R, Y, Z = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def _c(text: str, color: str) -> str:
    return f"{color}{text}{Z}"


def _root() -> Path:
    return Paths().root


def _t(s: str, c: str) -> str:
    return f"{c}{s}{Z}" if sys.stdout.isatty() else s


def _store(args) -> BizStore:
    return BizStore(_root(), tenant=getattr(args, "tenant", 0))


def _eg():
    from dawn_core import Registry
    from dawn_core.eg.cli import db_path
    from dawn_core.eg.store import EGStore

    db = db_path(Registry.load(_root()).paths)
    return EGStore(db) if db.is_file() else None


def _table(rows, cols: tuple[str, ...], empty: str = "(없음)") -> None:
    if not rows:
        print(f"  {D}{empty}{Z}")
        return
    widths = [max(len(c), *(len(str(r.get(c, ""))[:40]) for r in rows)) for c in cols]
    print("  " + D + "  ".join(c.ljust(w) for c, w in zip(cols, widths, strict=True)) + Z)
    for r in rows:
        print("  " + "  ".join(str(r.get(c, ""))[:40].ljust(w)
                               for c, w in zip(cols, widths, strict=True)))


# ── 조회 ────────────────────────────────────────────────────────────────


def cmd_docs(args) -> int:
    s = _store(args)
    rows = s.search_documents(args.search) if args.search else s.documents()
    if args.json:
        print(json.dumps([r.to_dict() for r in rows], ensure_ascii=False, indent=2))
        return 0
    label = f"검색 '{args.search}'" if args.search else "전체"
    print(f"{B}문서·지식{Z}  {label}  ({len(rows)}건)")
    _table(rows, ("id", "security_level", "revision", "title", "author"))
    if args.id:
        row = s.document(args.id)
        if row is None:
            print(f"  {_t('열 수 없다', R)}")
            return 1
        print(f"\n{B}{row['title']}{Z}  rev{row['revision']}  [{row['security_level']}]")
        print(row["body"][:4000])
        revs = s.revisions(args.id)
        if len(revs) > 1:
            print(f"\n  {D}개정 {len(revs)}판 — 이전 판은 지우지 않는다{Z}")
    return 0


def cmd_crm(args) -> int:
    s = _store(args)
    if args.json:
        print(json.dumps({
            "customers": [r.to_dict() for r in s.customers()],
            "inquiries": [r.to_dict() for r in s.inquiries()],
            "contracts": [r.to_dict() for r in s.contracts()],
        }, ensure_ascii=False, indent=2))
        return 0
    print(f"{B}고객{Z}")
    _table(s.customers(), ("id", "name", "segment", "contact_email", "owner_org"))
    print(f"\n{B}문의{Z}")
    _table(s.inquiries(status=args.status), ("id", "status", "category", "name",
                                             "org_name", "drafted_by"))
    print(f"\n{B}계약{Z}  {D}체결은 사람만 — crm.contract_sign 은 실행부가 없다{Z}")
    _table(s.contracts(), ("id", "status", "title", "amount_krw", "signed_by"))
    if args.id:
        row = s.inquiry(args.id)
        if row is not None:
            print(f"\n{B}문의 #{row['id']}{Z}  [{row['status']}] {row['category']}")
            print(f"  {row['name']} <{row['email']}> {row['org_name']}")
            print(f"\n  {D}원문{Z}\n  {row['message'][:800]}")
            if row["draft"]:
                print(f"\n  {B}응답 초안{Z} (by {row['drafted_by']}, "
                      f"trace {row['trace_id'][:12]}) — {_t('발송 전', Y)}")
                print("  " + row["draft"][:3000].replace("\n", "\n  "))
    return 0


def cmd_proj(args) -> int:
    s = _store(args)
    if args.key:
        prj = s.project_by_key(args.key)
        if prj is None:
            print(f"프로젝트 없음: {args.key}", file=sys.stderr)
            return 1
        tasks = s.tasks(project_id=prj["id"])
        if args.json:
            print(json.dumps({"project": prj.to_dict(),
                              "tasks": [t.to_dict() for t in tasks]},
                             ensure_ascii=False, indent=2))
            return 0
        print(f"{B}{prj['key']}{Z}  {prj['name']}  [{prj['status']}]  "
              f"{prj['owner_team']}")
        _table(tasks, ("id", "status", "phase", "depends_on", "assignee", "title"))

        from .workers import assignable

        ready, blocked = assignable(s, args.key)
        print(f"\n  {B}배정 가능{Z} {len(ready)}  ·  {B}의존 대기{Z} {len(blocked)}")
        for t in ready:
            print(f"    ▶ #{t['id']} {t['title']}")
        for t, missing in blocked:
            print(f"    ⏸ #{t['id']} {t['title']}  {D}(대기: {missing}){Z}")
        return 0
    rows = s.projects()
    if args.json:
        print(json.dumps([r.to_dict() for r in rows], ensure_ascii=False, indent=2))
        return 0
    print(f"{B}프로젝트{Z}")
    _table(rows, ("id", "key", "name", "business", "owner_team", "status"))
    return 0


def cmd_acct(args) -> int:
    s = _store(args)
    if args.json:
        print(json.dumps({
            "expenses": [r.to_dict() for r in s.expenses()],
            "fixed_assets": [r.to_dict() for r in s.fixed_assets()],
        }, ensure_ascii=False, indent=2))
        return 0
    print(f"{B}경비{Z}  {_t('L3 — 로컬 모델 전용 + 금액 임계 HITL', Y)}")
    _table(s.expenses(status=args.status),
           ("id", "request_id", "status", "amount_krw", "category", "hitl_id"))
    print(f"\n{B}자산 대장{Z}")
    _table(s.fixed_assets(), ("id", "tag", "name", "kind", "holder", "status"))
    if args.id:
        row = s.expense_by_request(str(args.id))
        if row is not None and row["verdict"]:
            print(f"\n{B}{row['request_id']} 판정{Z} (by {row['processed_by']}, "
                  f"trace {row['trace_id'][:12]})")
            print("  " + row["verdict"][:3000].replace("\n", "\n  "))
    return 0


# ── EG 정합성 ───────────────────────────────────────────────────────────


def cmd_egcheck(args) -> int:
    s, eg = _store(args), _eg()
    if eg is None:
        print("EG DB 가 없다 — make eg-load", file=sys.stderr)
        return 1
    checks = egsync.check(s, eg)
    summ = egsync.summary(checks)
    if args.json:
        print(json.dumps({"checks": [c.to_dict() for c in checks], "summary": summ},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"{B}업무 데이터 ↔ EG 자산{Z}")
    print(f"  {D}자산 id            이름                     존         등급    비가역성       행{Z}")
    for c in checks:
        print(c.line())
        for p in c.problems:
            print(f"      {_t('✘ ' + p, R)}")
    print("\n  존별 업무 데이터: " + ", ".join(
        f"{z}={n}" for z, n in sorted(egsync.zone_rows(s, eg).items())))
    ok = summ["ok"] == summ["assets"]
    print(f"\n  {_t('✔ 정합' if ok else '✘ 불일치', G if ok else R)}  "
          f"자산 {summ['ok']}/{summ['assets']}  ·  업무 행 {summ['rows']}")
    return 0 if ok else 1


# ── 에이전트 기동 ───────────────────────────────────────────────────────


def cmd_run(args) -> int:
    import os

    from . import workers

    if args.auto_approve:
        # **사람이 승인 버튼을 누른 것과 같은 효과다.** 데모·검증용.
        # 실제 운영에서는 그룹웨어 승인 큐에서 사람이 누른다 (P4).
        os.environ["DAWN_AUTO_APPROVE"] = "1"
        print(f"  {Y}⚠ --auto-approve: HITL 게이트를 자동 통과시킨다 (데모용){Z}")

    fn = {"inquiry": workers.handle_inquiry, "expense": workers.handle_expense,
          "project": workers.coordinate_project}[args.what]
    subject = int(args.subject) if args.what == "inquiry" else args.subject
    print(f"{B}업무 에이전트 기동{Z}  {args.what} {subject}")
    res = fn(subject, root=_root(), tenant=args.tenant)
    print(res.line())
    if args.json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0 if res.ok else 1
    if res.blocked:
        print(f"  {_t('차단된 도구: ' + ', '.join(res.blocked), Y)}")
    if res.hitl_ids:
        print(f"  {_t('승인 대기: ' + ', '.join(res.hitl_ids), Y)}"
              f"  {D}→ 그룹웨어 승인 큐{Z}")
    if res.output:
        print(f"\n{B}산출물{Z}\n" + res.output[:4000])
    return 0 if res.ok else 1


def cmd_emit(args) -> int:
    """업무 이벤트 → 훅 기동. **상시 폴링이 아니다.**"""
    from dawn_agents.events import Event

    from .events import business_dispatcher

    payload = json.loads(args.payload) if args.payload else {}
    d = business_dispatcher()
    ev = Event(type=args.type, source=args.source, payload=payload)
    handlers = d.handlers_for(ev.type)
    print(f"{B}이벤트{Z} {ev.type}  {D}{ev.id}{Z}")
    if not handlers:
        print(f"  {D}등록된 핸들러 없음 — 아무 일도 일어나지 않는다{Z}")
        print(f"  {D}등록된 트리거: {', '.join(sorted(d.registered()))}{Z}")
        return 0
    for h in handlers:
        print(f"  → {h.agent_id}  [{h.work_id}]  l3={h.touches_l3}")
    if args.dry_run:
        print(f"  {D}--dry-run — 기동하지 않았다{Z}")
        return 0
    from .events import run_event

    results = run_event(ev, root=_root(), tenant=args.tenant)
    for r in results:
        print(r.line())
    return 0 if all(r.ok for r in results) else 1


def cmd_intake(args) -> int:
    """공개 홈페이지 문의 접수함 → CRM 으로 흡수.

    홈페이지(L0 프로세스)는 사내 DB 에 손대지 않는다. 파일로 떨어뜨리고,
    **사내 쪽에서 당겨 온다.** 방향이 반대면 공개 프로세스가 내부에 쓰게 된다.
    """
    from .events import ingest_inquiries, ingest_work_requests

    n, ids = ingest_inquiries(_root(), tenant=args.tenant)
    print(f"{B}문의 흡수{Z}  새 {n}건  {D}(중복 제외){Z}")
    for i in ids:
        print(f"    문의 #{i}")
    if n and not args.no_run:
        print(f"  {D}처리하려면: dawn-biz run inquiry <id>{Z}")

    m, wids = ingest_work_requests(_root(), tenant=args.tenant)
    print(f"{B}작업 요청 흡수{Z}  새 {m}건  {D}(중복·규칙위반 제외){Z}")
    for i in wids:
        print(f"    작업 지시 #{i}  {D}결재 대기{Z}")
    if m:
        print(f"  {D}확인: dawn-biz orders{Z}")
    return 0


def cmd_orders(args) -> int:
    """작업 지시 — 접수부터 완료까지의 실행 단위 (P7)."""
    from dawn_core import workintake

    s = _store(args)
    if args.id:
        r = s.work_order(args.id)
        if r is None:
            print(f"작업 지시 없음: {args.id}")
            return 2
        print(f"{B}작업 지시 #{r['id']}{Z}  {r['title']}")
        for k in ("status", "origin", "requester", "requester_org", "contact",
                  "business", "division", "work_domain", "zone", "infra_tier",
                  "created_at"):
            print(f"    {k:<14} {r[k]}")
        chain = workintake.approval_chain(
            _root(), business=r["business"], infra_tier=r["infra_tier"],
            division=r["division"], origin=r["origin"])
        print(f"  {B}결재 라인{Z}")
        for i, c in enumerate(chain, 1):
            print(f"    {i}. {c['role']:<10} {c['portal_user']:<12} {D}{c['reason']}{Z}")
        if not chain:
            print(f"    {D}(담당 본부에 lead 가 없다 — division.yaml 확인){Z}")
        if r["body"]:
            print(f"  {B}내용{Z}\n    " + r["body"].replace("\n", "\n    ")[:800])
        return 0

    rows = s.work_orders(status=args.status, origin=args.origin)
    print(f"{B}작업 지시{Z} (테넌트 {args.tenant})")
    if not rows:
        print(f"  {D}없다{Z}")
        return 0
    print(f"  {D}id   상태               출처        사업              환경        제목{Z}")
    for r in rows:
        biz = r["business"] or f"내부:{r['division']}"     # 사업 없는 내부 지원 업무
        print(f"  {r['id']:<4} {r['status']:<18} {r['origin']:<10} "
              f"{biz:<17} {r['infra_tier']:<10} {r['title'][:40]}")
    return 0


def cmd_close(args) -> int:
    """작업 지시 마감 (P7 DoD-7).

    **기록을 먼저 남기고 자원을 놓는다.** 반대로 하면 반납 뒤에 무엇을 썼는지 모른다.
    """
    from .records import close, settle, usage

    root = _root()
    s = _store(args)
    r = s.work_order(args.id)
    if r is None:
        print(f"작업 지시 없음: {args.id}")
        return 2

    u = usage(root, args.id)
    st = settle(root, u)
    print(f"{B}#{args.id}{Z} {r['title']}")
    print(f"  실행    run {u.runs}건 (완료 {u.completed}) · 도구 {u.tool_calls} · "
          f"차단 {u.blocked}")
    print(f"  토큰    in {u.tokens_in:,} / out {u.tokens_out:,} · "
          f"소요 {u.wall_ms / 1000:.1f}s")
    print(f"  편성    {', '.join(u.agents) or '없음'}")
    for ln in st.lines:
        print(f"  원가    {ln['what']:<20} {ln['krw']:>10,} {st.currency}")
    print(f"  {B}합계    {st.total:>28,} {st.currency}{Z}"
          + ("" if st.complete else _c("   ← 하한 (미정 있음)", Y)))
    for x in st.unpriced:
        print(f"  {D}미정    {x}{Z}")
    print(f"  {D}원가다. 청구액이 아니다 — 단가는 org/ratecard.yaml{Z}")

    if args.dry_run:
        print(f"  {D}(dry-run — 아무것도 쓰지 않았다){Z}")
        return 0

    out = close(s, root, args.id, release=not args.keep)
    print(f"\n{B}마감{Z}")
    print(f"  일지 #{out['worklog_id']} · 보고서 #{out['report_id']}"
          + (f" · 경비 #{out['expense_id']}" if out["expense_id"]
             else f"  {D}(원가 미확정 — 경비 미기표){Z}"))
    if out.get("released"):
        print(f"  반납 {out['released'].get('host_id') or out['released'].get('container')}")
    print(f"  편성 회수 {len(out['disbanded'])}명 · 상태 → "
          f"{s.work_order(args.id)['status']}")
    return 0


def cmd_standing(args) -> int:
    """상시 작업 (P7 DoD-6).

    **상시 작업도 작업 지시다** — 기록이 똑같이 남는다. 다른 점은 결재가 최초 1회뿐.
    스케줄러는 바깥(cron·systemd)이고 여기는 **무엇이 지금 차례인가**만 정한다.
    """
    from . import standing

    root = _root()
    s = _store(args)

    if args.register:
        made = standing.register(root, s)
        print(f"{B}상시 작업 등록{Z}  {len(made)}건")
        for sid, wid in made.items():
            st = s.work_order(wid)["status"]
            mark = G if st != "pending_approval" else Y
            print(f"  #{wid:<4} {sid:<14} {_c(st, mark)}")
        print(f"  {D}결재: 그룹웨어 /orders — 최초 1회만 받는다{Z}")
        return 0

    if args.tick:
        ticks = standing.tick(root, s, only=args.only)
        if not ticks:
            print(f"{B}상시 작업{Z}  {D}지금 차례인 것이 없다{Z}")
            return 0
        print(f"{B}상시 작업 {len(ticks)}건{Z}")
        for t in ticks:
            print(t.line())
        return 0 if all(t.ok for t in ticks) else 1

    items, st = standing.load(root), standing.state(root)
    ok = standing.approved_orders(root, s)
    due = {i.id for i in standing.due(root)}
    print(f"{B}상시 작업{Z}  {len(items)}건")
    print(f"  {D}id             주기      마지막         결재    상태{Z}")
    for it in items:
        last = (st.get(it.id) or {}).get("at", "")
        gate = "승인" if it.id in ok else _c("미결재", Y)
        mark = ("차례" if it.id in due else "대기")
        if last and not (st.get(it.id) or {}).get("ok", True):
            mark = _c("실패", R)
        print(f"  {it.id:<14} {it.every:>5}분  "
              f"{(last[5:16].replace('T', ' ') if last else '없음'):<13} {gate:<8} {mark}")
    if not ok:
        print(f"  {D}결재 전에는 돌지 않는다 — dawn-biz standing --register{Z}")
    return 0


def cmd_infra(args) -> int:
    """인프라 풀 (P7 DoD-3).

    **할당은 비가역 행동이다** — 결재가 끝난 지시에만 붙는다.
    풀이 비면 실패가 아니라 `waiting_infra` 다. 장비를 넣거나 누가 반납하면 이어서 돈다.
    """
    from dawn_core.infrapool import PoolError, plan, summary

    from .provision import confirm_executed, deprovision, provision, retry_waiting

    root = _root()
    s = _store(args)

    if args.retry:
        out = retry_waiting(s, root)
        print(f"{B}준비대기 재시도{Z}  {len(out)}건")
        for x in out:
            print(f"  {R}✗ {x}{Z}" if isinstance(x, Exception) else x.line())
        return 0

    if args.id is None:
        d = summary(root)
        print(f"{B}인프라 풀{Z}")
        print(f"  장비        {d['hosts_total']}대  "
              f"vm {d['hosts_free']['vm']}/{d['hosts_by_kind']['vm']} 가용 · "
              f"server {d['hosts_free']['server']}/{d['hosts_by_kind']['server']} 가용")
        print(f"  컨테이너     {d['container_used']}/{d['container_max']}  "
              f"{D}(도커 접근 {'가능' if d['docker'] else '불가 — 사람이 집행'}){Z}")
        print(f"  할당 중      {d['allocated']}건")
        if d["waiting"]:
            print(f"  {Y}준비대기{Z}     {len(d['waiting'])}건")
            for w in d["waiting"]:
                print(f"    #{w['order_id']} {w['tier']} — {w['reason']}")
        if not d["hosts_total"]:
            print(f"  {D}풀이 비어 있다 — infra/pool.yaml 에 장비를 등록하면 "
                  f"vm·server 등급이 돈다{Z}")
        return 0

    r = s.work_order(args.id)
    if r is None:
        print(f"작업 지시 없음: {args.id}")
        return 2

    if args.confirm:
        try:
            a = confirm_executed(s, root, args.id, by=args.confirm)
        except PoolError as e:
            print(f"{R}확인 거부{Z}  {e}")
            return 2
        print(f"{B}집행 확인{Z}  {a.line()}")
        print(f"  {D}작업 지시 상태 → {s.work_order(args.id)['status']}{Z}")
        return 0

    if args.release:
        a = deprovision(s, root, args.id)
        if a is None:
            print(f"{D}#{args.id} 에 잡힌 자원이 없다{Z}")
            return 0
        print(f"{B}반납{Z}  {a.line()}")
        if a.command:
            print(f"  {Y}실물 정리는 사람이 한다{Z}: {a.command}")
        return 0

    if args.dry_run:
        from dawn_core import workintake

        zone = r["zone"] or workintake.zone_for(root, r["division"])
        print(f"{B}할당 계획{Z} (쓰지 않는다)")
        print(plan(root, order_id=args.id, tier=r["infra_tier"], zone=zone,
                   business=r["business"]).line())
        return 0

    try:
        a = provision(s, root, args.id)
    except (PoolError, ValueError) as e:
        print(f"{R}할당 거부{Z}  {e}")
        return 2
    print(f"{B}할당{Z}  {a.line()}")
    if a.command and a.state != "ready":
        print(f"  {Y}집행 명령{Z}: {a.command}")
        print(f"  {D}실행 후: dawn-biz infra --retry{Z}")
    print(f"  {D}작업 지시 상태 → {s.work_order(args.id)['status']}{Z}")
    return 0


def cmd_hire(args) -> int:
    """작업 지시를 읽고 편성 **초안**을 만든다 (P7 — 자동 고용).

    초안일 뿐이다. 본부장이 파일을 고치고 승인해야 에이전트가 생긴다 —
    **편성은 권한을 만드는 행위**라 모델이 읽은 대로 바로 만들면 안 된다.
    """
    from dawn_core import hire, workintake

    root = _root()
    s2 = _store(args)
    r = s2.work_order(args.id)
    if r is None:
        print(f"작업 지시 없음: {args.id}")
        return 2

    # 결재 상태 — 초안은 결재 전에도 만들 수 있다(미리 보는 게 결재에 도움이 된다).
    # 승인(=실제 생성)만 결재를 요구한다.
    chain = workintake.approval_chain(root, business=r["business"],
                                      infra_tier=r["infra_tier"],
                                      division=r["division"], origin=r["origin"])
    decided = s2.work_order_approvals(args.id)
    order_approved = r["status"] in ("approved", "provisioning", "in_progress") or (
        bool(chain) and workintake.next_approver(chain, decided) is None
        and all(d["decision"] == "approved" for d in decided))

    try:
        if args.approve:
            d, made = hire.approve(root, args.id, by=args.by,
                                   approved=order_approved)
            print(f"{B}편성 완료{Z}  작업 지시 #{args.id}  {D}승인 {d.approved_by}{Z}")
            for aid in made:
                print(f"  {G}+{Z} {aid}")
            lead = d.lead
            print(f"  {D}팀장: {lead['role_key'] if lead else '없음 — 결정은 전부 본부장에게 올라간다'}{Z}")
            return 0

        if args.show:
            d = hire.load(root, args.id)
        else:
            print(f"{D}지시문을 읽는 중… (모델 호출){Z}")
            d = hire.propose(root, dict(r.data), team=args.team)
            path = hire.save(root, d)
            print(f"{B}편성 초안{Z}  {path}")

        print(f"  {D}{d.title}  ·  {d.division}/{d.team}  ·  {d.proposed_by}{Z}")
        if d.notes:
            print(f"  {D}{d.notes}{Z}")
        print()
        for m in d.members:
            tag = f" {Y}[팀장]{Z}" if m.get("lead") else ""
            print(f"  {B}{m['role_key']}{Z}{tag}  {m['name']}  {D}{m.get('phase')}{Z}")
            if m.get("mission"):
                print(f"      {m['mission']}")
            print(f"      도구: {', '.join(m['tools'])}")
            if m.get("dropped_tools"):
                print(f"      {Y}게이트 밖이라 뺌: {', '.join(m['dropped_tools'])}{Z}")
        print()
        print(f"  상태 {d.status} · 지시 결재 {'완료' if order_approved else R + '미완' + Z}")
        if d.status == "draft":
            lead = next((c["portal_user"] for c in chain), "본부장")
            print(f"  {D}고친 뒤:  dawn-biz hire {args.id} --approve --by {lead}{Z}")
        return 0
    except hire.HireError as e:
        # 규칙·형식 문제는 사람이 읽을 메시지로 낸다. 그 밖의 예외는 삼키지
        # 않는다 — 편성은 권한을 만드는 일이라 조용히 실패하면 안 된다.
        print(f"{R}✘{Z} {e}")
        return 1


def cmd_crew(args) -> int:
    """작업 지시에 에이전트를 편성한다 (P7 DoD-4).

    **결재가 끝나야 만든다.** 편성은 권한을 만드는 행위다.
    """
    from dawn_core import Registry, workintake
    from dawn_core.crew import CrewError, Member, disband, form, formed

    root = _root()
    s = _store(args)
    r = s.work_order(args.id)
    if r is None:
        print(f"작업 지시 없음: {args.id}")
        return 2

    if args.disband:
        gone = disband(root, order_id=args.id)
        print(f"{B}편성 회수{Z}  {len(gone)}명  {D}{', '.join(gone) or '없음'}{Z}")
        return 0

    have = formed(root, order_id=args.id)
    if have and not args.force:
        print(f"{B}이미 편성됨{Z}  {', '.join(have)}")
        print(f"  {D}회수: dawn-biz crew {args.id} --disband{Z}")
        return 0

    chain = workintake.approval_chain(root, business=r["business"],
                                      infra_tier=r["infra_tier"],
                                      division=r["division"], origin=r["origin"])
    decided = s.work_order_approvals(args.id)
    approved = r["status"] == "approved" or (
        bool(chain) and workintake.next_approver(chain, decided) is None
        and all(d["decision"] == "approved" for d in decided))

    reg = Registry.load(root)
    team = args.team or next(
        (t for t in reg.divisions[r["division"]].data.get("teams", [])
         if (reg.teams[t].dir / "AGENT_TEAM.md").is_file()), "")
    if not team:
        print(f"{R}편성할 팀이 없다{Z} — {r['division']} 본부의 팀 중 "
              "AGENT_TEAM.md(L2) 가 있는 팀이 없다")
        return 2

    works = [args.work] if args.work else []
    m = Member(role_key=args.role, name=f"{r['title'][:24]} 담당", team=team,
               persona=reg.teams[team].data.get("persona_default", "corporate"),
               works=works, tools=args.tools.split(",") if args.tools else
               ["eg.search", "eg.record", "skill.preview", "doc.search"],
               zone=r["zone"] or "", mission=r["title"])

    if args.dry_run:
        from dawn_core.crew import plan

        print(f"{B}편성안{Z} (작업 지시 #{args.id}) {D}— 쓰지 않는다{Z}")
        for p2 in plan(root, order_id=args.id, members=[m]):
            over = f"  {R}경계 밖: {', '.join(p2['over_scope'])}{Z}" if p2["over_scope"] else ""
            print(f"  {p2['agent_id']:<22} {p2['team']:<14} {', '.join(p2['tools'])}{over}")
        print(f"  결재 {'완료' if approved else R + '미완' + Z}")
        return 0

    try:
        made = form(root, order_id=args.id, members=[m], approved=approved)
    except CrewError as e:
        print(f"{R}편성 실패{Z}\n  {e}")
        return 2
    s.set_work_order_status(args.id, "in_progress")
    print(f"{B}편성{Z}  {', '.join(made)}")
    print(f"  {D}착수: dawn-biz start {args.id}{Z}")
    return 0


def cmd_start(args) -> int:
    """편성된 에이전트를 돌리고 산출물을 검수한다 (P7 DoD-5)."""
    from dawn_agents import Worker

    from dawn_biz.execute import can_start, review, stage_plan

    root = _root()
    s = _store(args)
    ok, why = can_start(s, args.id)
    if not ok:
        print(f"{R}착수할 수 없다{Z} — {why}")
        return 2

    r = s.work_order(args.id)
    stages = stage_plan(root, order_id=args.id)
    print(f"{B}작업 지시 #{args.id}{Z}  {r['title']}")
    print(f"  {D}편성 {len(stages)}명 · 환경 {r['infra_tier']}{Z}\n")

    verdicts = []
    for st in stages:
        print(f"  {B}{st['agent_id']}{Z} 착수…")
        w = Worker(st["agent_id"])
        run = w.run(f"{r['title']}\n\n{r['body']}", purpose="work")
        v = review(run, eg_store=w.eg, with_judge=not args.no_judge,
                   high_risk=r["infra_tier"] in ("vm", "server"))
        verdicts.append(v)
        print(v.line())

    passed = all(v.passed for v in verdicts)
    s.set_work_order_status(args.id, "done" if passed else "reviewing_output")
    print(f"\n  {B}검수{Z}  {'전부 통과 → 완료' if passed else '미통과 → 산출물 검토'}")
    if not passed:
        print(f"  {D}통과 못 한 산출물로 다음 단계는 시작하지 않는다{Z}")
    return 0 if passed else 1


def cmd_seed(args) -> int:
    from .seed import seed_all

    n = seed_all(_root(), tenant=args.tenant, force=args.force)
    print(f"{B}업무 데모 데이터{Z}  {n}건 주입")
    s = _store(args)
    for k, v in s.counts().items():
        print(f"    {k:<14} {v}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dawn-biz", description="업무 시스템")
    p.add_argument("--tenant", type=int, default=0)
    s = p.add_subparsers(dest="cmd", required=True)

    x = s.add_parser("docs", help="문서·지식")
    x.add_argument("id", nargs="?", type=int)
    x.add_argument("--search", default="")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_docs)

    x = s.add_parser("crm", help="고객·문의·계약")
    x.add_argument("id", nargs="?", type=int)
    x.add_argument("--status", default="")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_crm)

    x = s.add_parser("proj", help="프로젝트·태스크")
    x.add_argument("key", nargs="?", default="")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_proj)

    x = s.add_parser("acct", help="경비·자산 (L3)")
    x.add_argument("id", nargs="?")
    x.add_argument("--status", default="")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_acct)

    x = s.add_parser("egcheck", help="업무 데이터 ↔ EG 자산 정합성")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_egcheck)

    x = s.add_parser("run", help="업무 에이전트 기동 (모델 호출)")
    x.add_argument("what", choices=["inquiry", "expense", "project"])
    x.add_argument("subject")
    x.add_argument("--auto-approve", action="store_true",
                   help="HITL 을 자동 승인 (데모용 — 실제로는 그룹웨어에서 사람이 누른다)")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_run)

    x = s.add_parser("emit", help="업무 이벤트 → 훅 기동")
    x.add_argument("type")
    x.add_argument("--payload", default="")
    x.add_argument("--source", default="manual")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(func=cmd_emit)

    x = s.add_parser("intake", help="홈페이지 접수함(문의·작업 요청) → 사내 DB")
    x.add_argument("--no-run", action="store_true")
    x.set_defaults(func=cmd_intake)

    x = s.add_parser("orders", help="작업 지시 — 접수→결재→환경→착수 (P7)")
    x.add_argument("id", nargs="?", type=int)
    x.add_argument("--status", default="")
    x.add_argument("--origin", default="", choices=["", "external", "internal", "standing"])
    x.set_defaults(func=cmd_orders)

    x = s.add_parser("close", help="작업 지시 마감 — 일지·보고서·원가·반납 (P7 DoD-7)")
    x.add_argument("id", type=int)
    x.add_argument("--dry-run", action="store_true", help="사용량·원가만 보고 안 쓴다")
    x.add_argument("--keep", action="store_true", help="자원을 반납하지 않는다")
    x.set_defaults(func=cmd_close)

    x = s.add_parser("standing", help="상시 작업 — 등록·현황·1회전 (P7 DoD-6)")
    x.add_argument("--register", action="store_true",
                   help="상시 작업마다 작업 지시를 만든다 (최초 1회 결재용)")
    x.add_argument("--tick", action="store_true", help="지금 차례인 것을 돌린다")
    x.add_argument("--only", default="", help="특정 항목만")
    x.set_defaults(func=cmd_standing)

    x = s.add_parser("infra", help="인프라 풀 — 할당·반납·현황 (P7 DoD-3)")
    x.add_argument("id", nargs="?", type=int, help="작업 지시 id (없으면 풀 현황)")
    x.add_argument("--release", action="store_true", help="반납")
    x.add_argument("--retry", action="store_true", help="준비대기 중인 지시를 다시 시도")
    x.add_argument("--confirm", metavar="WHO",
                   help="사람이 집행했다고 선언 (시스템이 검증한 것은 아니다)")
    x.add_argument("--dry-run", action="store_true", help="계획만 보고 잡지 않는다")
    x.set_defaults(func=cmd_infra)

    x = s.add_parser("hire", help="작업 지시를 읽고 편성 초안 생성 (P7)")
    x.add_argument("id", type=int)
    x.add_argument("--team", default="", help="팀 지정 (기본: 본부의 L2 있는 팀)")
    x.add_argument("--show", action="store_true", help="기존 초안 보기 (모델 안 부름)")
    x.add_argument("--approve", action="store_true", help="본부장 승인 → 실제 편성")
    x.add_argument("--by", default="", help="승인자 계정")
    x.set_defaults(func=cmd_hire)

    x = s.add_parser("crew", help="작업 지시에 에이전트 편성 (P7)")
    x.add_argument("id", type=int)
    x.add_argument("--role", default="builder")
    x.add_argument("--team", default="")
    x.add_argument("--work", default="")
    x.add_argument("--tools", default="")
    x.add_argument("--dry-run", action="store_true", help="편성안만 보고 쓰지 않는다")
    x.add_argument("--disband", action="store_true", help="편성 회수")
    x.add_argument("--force", action="store_true")
    x.set_defaults(func=cmd_crew)

    x = s.add_parser("start", help="편성된 에이전트 착수 + 산출물 검수 (P7)")
    x.add_argument("id", type=int)
    x.add_argument("--no-judge", action="store_true", help="품질 판정 생략 (GPU 없이)")
    x.set_defaults(func=cmd_start)

    x = s.add_parser("seed", help="데모 업무 데이터")
    x.add_argument("--force", action="store_true")
    x.set_defaults(func=cmd_seed)
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 0
    except (ValueError, KeyError, OSError, PermissionError) as exc:
        print(_t(f"✘ {type(exc).__name__}: {exc}", R), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
