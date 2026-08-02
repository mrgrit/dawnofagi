"""dawn-ops — 통합·검증·운영 CLI (P6).

    dawn-ops e2e [--live]      엔드투엔드 — 요구→에이전트→업무→관제→축적
    dawn-ops redteam [--live]  오펜시브 레드팀 + 탐지 커버리지
    dawn-ops rehearsal         인시던트 3종 리허설 + 비가역 대응 실증
    dawn-ops tenant            멀티테넌트 준비 점검
    dawn-ops status            전 계층 현황 한 장
    dawn-ops kpi               자율화 A1 운영 KPI (관제 대시보드)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dawn_agents import load_dotenv
from dawn_core.paths import Paths

from . import e2e, redteam, rehearsal, tenant

B, D, G, R, Y, Z = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def _root() -> Path:
    return Paths().root


def _t(s: str, c: str) -> str:
    return f"{c}{s}{Z}" if sys.stdout.isatty() else s


# ── E2E ─────────────────────────────────────────────────────────────────


def cmd_e2e(args) -> int:
    root = _root()
    res = e2e.run(root, live=args.live)
    e2e.save(root, res)
    if args.json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0 if res.ok else 1
    print(f"{B}엔드투엔드{Z}  {D}요구 → 에이전트 → 업무 → 관제 → 축적{Z}  live={args.live}")
    for h in res.hops:
        print(h.line())
        if h.evidence:
            print(f"      {_t(h.evidence[:110], R)}")
    if res.ok:
        print(f"\n  {_t('✔ 전 구간 연결', G)}   문의 #{res.inquiry_id}  "
              f"트레이스 {res.trace_id[:12] or '-'}")
    else:
        print(f"\n  {_t('✘ ' + res.broke_at + ' 에서 끊겼다', R)}")
    return 0 if res.ok else 1


# ── 레드팀 ──────────────────────────────────────────────────────────────


def cmd_redteam(args) -> int:
    root = _root()
    print(f"{B}오펜시브 레드팀{Z}  {D}스코프: 자사 에이전트 (persona:offensive){Z}")
    print(f"  {D}공격 {len(redteam.ATTACKS)}종 · 계열 {len(redteam.FAMILIES)}{Z}\n")

    results = redteam.static_scan()
    print(f"{B}정적 — 입력·출력 게이트{Z}")
    for r in results:
        print(r.line())

    if args.live:
        print(f"\n{B}실전 — 실제 워커 (모델 호출){Z}  "
              f"{D}정적에서 잡힌 것은 다시 안 돌린다{Z}")
        live = redteam.live_scan(root, limit=args.limit)
        by_id = {r.attack_id: r for r in results}
        for lr in live:
            print(lr.line())
            if lr.note:
                print(f"      {_t(lr.note, Y)}")
            by_id[lr.attack_id] = lr           # 실전 결과가 정적 결과를 대체한다
        results = list(by_id.values())

    cov = redteam.coverage(results)
    props = redteam.hardening_proposals(results)
    redteam.save_report(root, results)

    print(f"\n{B}탐지 커버리지{Z}")
    print(f"  전체        {cov['detected']}/{cov['total']}  {cov['coverage_pct']}%")
    print(f"  게이트만    {cov['gate_coverage_pct']}%  "
          f"{D}(모델 거절에 기댄 것 제외 — 모델이 바뀌면 뚫린다){Z}")
    for fam, s in sorted(cov["by_family"].items()):
        bar = "█" * s["detected"] + "░" * (s["total"] - s["detected"])
        print(f"  {fam:<18} {bar}  {s['detected']}/{s['total']}")

    if props:
        print(f"\n{B}보강 제안{Z}  {_t('놓친 것이 본체다', Y)}")
        for p in props:
            print(f"  ✘ {p['attack_id']:<11} {p['family']:<18} {p['why']}")
            print(f"      {D}어디를: {p['where']}{Z}")
            print(f"      {D}페이로드: {p['payload_excerpt']}{Z}")
    else:
        print(f"\n  {_t('보강 제안 없음 — 전부 게이트가 잡았다', G)}")

    if args.json:
        print(json.dumps({"coverage": cov, "proposals": props}, ensure_ascii=False,
                         indent=2))
    return 0 if not props else (0 if args.allow_gaps else 1)


# ── 리허설 ──────────────────────────────────────────────────────────────


def cmd_rehearsal(args) -> int:
    root = _root()
    print(f"{B}인시던트 리허설{Z}  {D}보안·품질·정합성 3축 + 비가역 대응 3종{Z}\n")
    res = rehearsal.run_all(root, keep=args.keep)

    print(f"{B}탐지 → 트리아지 → 대응 → 리플레이{Z}")
    for r in res["_objs"]:
        print(r.line())
        print(f"      탐지기   {', '.join(r.detectors) or '-'}")
        print(f"      권고     {', '.join(r.recommended) or '-'}")
        print(f"      집행     {', '.join(r.executed) or '-'}")
        print(f"      승인대기 {', '.join(r.queued) or '-'}  "
              f"{D}(비가역은 사람이 누르기 전엔 안 돈다){Z}")
        print(f"      리플레이 {'가능' if r.replayable else _t('불가', R)}")

    print(f"\n{B}비가역 대응 실증{Z}  "
          f"{D}리허설에서 안 눌러본 버튼은 사고 때도 안 눌린다{Z}")
    for h in res["_hard"]:
        print(h.line())

    ok = res["ok"]
    print(f"\n  {_t('✔ 리허설 완료' if ok else '✘ 리허설 실패', G if ok else R)}")
    if args.json:
        print(json.dumps({k: v for k, v in res.items() if not k.startswith("_")},
                         ensure_ascii=False, indent=2))
    return 0 if ok else 1


# ── 멀티테넌트 ──────────────────────────────────────────────────────────


def cmd_tenant(args) -> int:
    root = _root()
    rep = tenant.run(root)
    if args.json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
        return 0 if rep.ok else 1
    print(f"{B}멀티테넌트 준비 점검{Z}  "
          f"{D}자사가 정말 '테넌트 하나'인가 — 실제로 #{tenant.CUSTOMER_TENANT} 를 "
          f"만들어 보고 지운다{Z}")
    for c in rep.checks:
        print(c.line())
        if not c.ok and c.fix:
            print(f"      {_t('→ ' + c.fix, Y)}")
    print(f"\n  {_t('✔ 고객 테넌트 추가 가능' if rep.ok else '✘ 확장 불가', G if rep.ok else R)}")
    print(f"\n{B}고객 온보딩 절차{Z}")
    for step, desc in tenant.ONBOARDING_STEPS:
        print(f"  {step}")
        print(f"      {D}{desc}{Z}")
    return 0 if rep.ok else 1


# ── 현황 ────────────────────────────────────────────────────────────────


def cmd_status(args) -> int:
    root = _root()
    from dawn_aoc.console import build_state
    from dawn_biz.store import BizStore
    from dawn_core import Registry

    reg = Registry.load(root)
    st = build_state(root, limit=100)
    biz = BizStore(root, tenant=0)

    if args.json:
        print(json.dumps({"agents": st["agents"], "kpis": st["kpis"],
                          "cases": len(st["cases"]), "biz": biz.counts()},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"{B}the dawn of AGI — 현황{Z}   {st['generated_at']}")
    print(f"\n{B}조직{Z}  본부 {len(reg.divisions)} · 팀 {len(reg.teams)} · "
          f"사업 {len(reg.businesses)} · 에이전트 {len(reg.agents)}")
    print(f"\n{B}에이전트{Z}")
    for a in st["agents"]:
        print(f"  {a['agent_id']:<22} {a['eg_org']:<14} {a['autonomy']}  "
              f"{a['room']:<16} {a['control_state']:<9} run {a['runs']:<4} "
              f"{a['last_model'] or '-'}")
    print(f"\n{B}업무 데이터{Z}  " +
          " · ".join(f"{k} {v}" for k, v in biz.counts().items() if v))

    from dawn_core.infrapool import summary as pool_summary

    pool = pool_summary(root)
    print(f"\n{B}인프라{Z}  장비 {pool['hosts_total']}대 "
          f"(vm {pool['hosts_free']['vm']} · server {pool['hosts_free']['server']} 가용) · "
          f"컨테이너 {pool['container_used']}/{pool['container_max']} · "
          f"할당 {pool['allocated']}건")
    for w in pool["waiting"]:
        print(f"  {_t('준비대기', Y)} #{w['order_id']} {w['tier']} — {w['reason'][:70]}")
    print(f"\n{B}관제{Z}  케이스 {len(st['cases'])} · "
          f"승인 대기 {len([x for x in st['hitl'] if x['status'] == 'pending'])} · "
          f"수집 {st['collect']['spans']} 스팬 / {st['collect']['tokens']:,} 토큰")
    crit = [c for c in st["cases"] if c["severity"] in ("critical", "high")]
    if crit:
        print(f"  {_t(f'고심각 케이스 {len(crit)}건', R)}")
    return 0


def cmd_kpi(args) -> int:
    """자율화 A1 운영 KPI — 관제 대시보드와 같은 수치."""
    from dawn_aoc.cli import cmd_kpi as aoc_kpi

    return aoc_kpi(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dawn-ops", description="통합·검증·운영")
    s = p.add_subparsers(dest="cmd", required=True)

    x = s.add_parser("e2e", help="엔드투엔드 경로")
    x.add_argument("--live", action="store_true", help="모델을 실제로 부른다")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_e2e)

    x = s.add_parser("redteam", help="오펜시브 레드팀 + 탐지 커버리지")
    x.add_argument("--live", action="store_true")
    x.add_argument("--limit", type=int, default=4, help="실전 시도 개수")
    x.add_argument("--allow-gaps", action="store_true",
                   help="미탐이 있어도 0 으로 종료 (관찰용)")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_redteam)

    x = s.add_parser("rehearsal", help="인시던트 3종 리허설")
    x.add_argument("--keep", action="store_true", help="원상복구하지 않는다")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_rehearsal)

    x = s.add_parser("tenant", help="멀티테넌트 준비 점검 + 온보딩 절차")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_tenant)

    x = s.add_parser("status", help="전 계층 현황")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_status)

    x = s.add_parser("kpi", help="자율화 A1 운영 KPI")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_kpi)
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
