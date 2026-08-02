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
