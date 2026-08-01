"""EG CLI — 주입 · 조회 · 순회.

dawn eg load               시드 주입 (경로 A)
dawn eg stats              그래프 현황
dawn eg search "질의"      eg_search — FTS 전문 검색
dawn eg org <org-id>       조직 프로파일 (페르소나·정책·모델·자율화)
dawn eg severity           전 자산 심각도
dawn eg gate <asset-id>    이 자산의 게이트 결정
dawn eg model <org-id>     이 조직의 모델 라우팅 (--l3 로 L3 관여 가정)
dawn eg snapshot           스냅샷 저장
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..paths import Paths
from ..registry import Registry
from . import bridge as eg_bridge
from .loader import load as load_seeds
from .loader import snapshot as take_snapshot
from .store import EGStore
from .traverse import (
    all_severities,
    autonomy_violations,
    gate_for,
    model_for_org,
    org_profile,
)

C_OK, C_ERR, C_DIM, C_B, C_RST = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def _t(s: str, c: str) -> str:
    return f"{c}{s}{C_RST}" if sys.stdout.isatty() else s


def db_path(paths: Paths, explicit: str | None = None) -> Path:
    """EG DB 경로. --db > EG_DB_PATH/BASTION_GRAPH_DB > var/eg/bastion_graph.db"""
    if explicit:
        return Path(explicit).expanduser()
    for env in ("EG_DB_PATH", "BASTION_GRAPH_DB"):
        v = os.getenv(env, "").strip()
        if v:
            return Path(v).expanduser()
    return paths.root / "var" / "eg" / "bastion_graph.db"


def _store(args) -> EGStore:
    p = Paths(args.root)
    path = db_path(p, args.db)
    if not path.is_file():
        raise SystemExit(_t(f"✘ EG DB 가 없다: {path}\n  먼저 주입하라:  make eg-load", C_ERR))
    return EGStore(path)


# ── load ────────────────────────────────────────────────────────────────


def cmd_load(args) -> int:
    paths = Paths(args.root)
    target = db_path(paths, args.db)
    seed_dir = Path(args.seeds) if args.seeds else paths.root / "eg" / "seed"

    bastion_seed = None
    if args.from_bastion:
        cand = Path(args.from_bastion).expanduser()
        if cand.is_file():
            bastion_seed = cand
            print(f"{C_DIM}bastion 런타임 EG 위에 얹는다: {cand}{C_RST}")
        else:
            print(_t(f"! bastion 시드 DB 를 못 찾았다: {cand} — 새 DB 로 시작한다", C_DIM))

    res = load_seeds(
        seed_dir,
        target,
        replace=not args.append,
        from_bastion_seed=bastion_seed,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print(
            f"[dry-run] 노드 {res.nodes_upserted} · 엣지 {res.edges_upserted} "
            f"(+파생 {res.edges_derived}) — 주입하지 않음"
        )
        return 0

    print(_t("✔ 거버넌스 계층 주입 완료", C_OK))
    print(f"  DB        {res.db_path}")
    print(f"  시드      {', '.join(res.seeds)}")
    print(f"  노드      {res.nodes_upserted}")
    print(
        f"  엣지      {res.edges_upserted}"
        + (f"  (+ owner_org 파생 {res.edges_derived})" if res.edges_derived else "")
    )
    if res.removed_stale:
        print(f"  {C_DIM}이전 거버넌스 노드 {res.removed_stale}개 교체{C_RST}")

    s = res.stats
    print(f"\n  계층별   {s['nodes_by_layer']}")
    print(f"  노드타입 {s['nodes_by_type']}")
    return 0


# ── stats ───────────────────────────────────────────────────────────────


def cmd_stats(args) -> int:
    st = _store(args).stats()
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0
    print(f"{C_B}EG 현황{C_RST}  {st['db']}")
    print(f"  노드 {st['nodes_total']}  ·  엣지 {st['edges_total']}")
    print(f"\n  {C_B}계층{C_RST}")
    for k, v in st["nodes_by_layer"].items():
        print(f"    {k:12} {v:4}")
    print(f"\n  {C_B}노드 타입{C_RST}")
    for k, v in st["nodes_by_type"].items():
        print(f"    {k:16} {v:4}")
    print(f"\n  {C_B}엣지 타입{C_RST}")
    for k, v in st["edges_by_type"].items():
        print(f"    {k:16} {v:4}")
    return 0


# ── search (eg_search) ──────────────────────────────────────────────────


def cmd_search(args) -> int:
    store = _store(args)
    hits = store.search(args.query, type=args.type, limit=args.limit)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": h.id,
                        "type": h.type,
                        "name": h.name,
                        "layer": h.layer,
                        "content": h.content,
                    }
                    for h in hits
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not hits:
        print(f"{C_DIM}일치 없음: {args.query}{C_RST}")
        return 0
    print(f'{C_B}eg_search{C_RST} "{args.query}"  —  {len(hits)}건\n')
    for h in hits:
        print(f"  {_t(h.id, C_OK)}  {C_DIM}[{h.type}·{h.layer}]{C_RST}")
        print(f"    {h.name}")
        for key in ("mission", "statement", "role", "handling_rule", "gate_rule"):
            v = h.content.get(key)
            if isinstance(v, str) and v:
                print(f"    {C_DIM}{key}: {v[:110]}{C_RST}")
                break
    return 0


# ── org (개입 지점) ──────────────────────────────────────────────────────


def cmd_org(args) -> int:
    store = _store(args)
    prof = org_profile(store, args.org)
    if args.json:
        print(json.dumps(prof.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(f"{C_B}{prof.org_name}{C_RST}  ({prof.org_id})")
    print(f"  미션      {prof.mission}")
    print(f"  상위      {prof.parent.id if prof.parent else '—'}")
    print(
        f"  자율화    {prof.autonomy.id if prof.autonomy else '—'}"
        + (f"  {prof.autonomy.prop('label')}" if prof.autonomy else "")
    )
    models = ", ".join(f"{m.prop('model')} [{m.prop('cost_tier')}]" for m in prof.models)
    print(f"  모델      {models or '—'}" + ("  (로컬 전용)" if prof.is_local_only else ""))

    print(f"\n  {C_B}페르소나 — 사람이 고치는 곳{C_RST}")
    for p in prof.personas:
        print(f"    {_t(p.id, C_OK)}  {p.prop('role')}")
        for pr in (p.prop("principles") or [])[:3]:
            print(f"      · {pr}")
        n = len(p.prop("principles") or [])
        if n > 3:
            print(f"      {C_DIM}… 원칙 {n}개 / 금지 {len(p.prop('prohibited') or [])}개{C_RST}")

    print(f"\n  {C_B}적용 정책 (페르소나 경유){C_RST}")
    for pol in prof.policies:
        print(f"    {pol.id:<28} {pol.prop('enforcement'):<13} {pol.prop('statement')[:60]}")
    if not prof.policies:
        print(f"    {C_DIM}—{C_RST}")

    if prof.assets:
        print(f"\n  {C_B}소유 자산{C_RST}")
        for a in prof.assets:
            print(f"    {a.id:<20} {a.name}")
    return 0


# ── severity / gate / model ─────────────────────────────────────────────


def cmd_severity(args) -> int:
    store = _store(args)
    sevs = all_severities(store)
    if args.json:
        print(json.dumps([s.__dict__ for s in sevs], ensure_ascii=False, indent=2))
        return 0
    print(f"{C_B}심각도 = 비가역성 + 보안등급rank{C_RST}\n")
    for s in sevs:
        print("  " + s.line())
    return 0


def cmd_gate(args) -> int:
    store = _store(args)
    g = gate_for(store, args.asset)
    if args.json:
        print(json.dumps(g.to_dict(), ensure_ascii=False, indent=2))
        return 0
    from .traverse import severity_of

    sev = severity_of(store, args.asset)
    print(f"{C_B}{g.asset_name}{C_RST}  ({g.asset_id})")
    print(f"  심각도    {sev.icon}{sev.label}({sev.score}) = {sev.irreversibility} + {sev.sec_id}")
    print(f"  존        {sev.zone_id}  {sev.zone_room}")
    print(
        f"  게이트    {_t(g.strongest, C_ERR if g.strongest == 'block' else C_OK)}"
        f"   HITL 필요: {'예' if g.requires_hitl else '아니오'}"
    )
    print(f"\n  {C_B}걸린 정책 {len(g.policies)}개{C_RST}")
    for p in g.policies:
        print(f"    {p.prop('enforcement'):<13} {p.id:<28} {p.prop('statement')[:58]}")
    return 0


def cmd_model(args) -> int:
    store = _store(args)
    r = model_for_org(store, args.org, touches_l3=args.l3)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    mark = C_ERR if r["blocked"] else C_OK
    print(
        f"{C_B}모델 라우팅{C_RST}  {r['org']}"
        + ("  (L3 자산 관여 가정)" if r["touches_l3"] else "")
    )
    print(f"  모델      {_t(str(r['model']), mark)}  {C_DIM}{r.get('model_id') or ''}{C_RST}")
    print(f"  근거      {r['reason']}")
    if r.get("policy"):
        print(f"  정책      {r['policy']}")
    return 1 if r["blocked"] else 0


def cmd_autonomy(args) -> int:
    store = _store(args)
    v = autonomy_violations(store)
    if args.json:
        print(json.dumps(v, ensure_ascii=False, indent=2))
        return 0
    print(f"{C_B}자율화 게이트 필요 조합{C_RST}  (pol:autonomy-gate)\n")
    if not v:
        print(f"  {C_DIM}없음 — 모든 조직의 자율화 등급이 소유 자산 민감도 이상{C_RST}")
    for x in v:
        print(
            f"  ⚠ {x['org_name']:<14} {x['autonomy']}(lvl {x['autonomy_level']}) "
            f"< {x['asset_name']:<20} (rank {x['sec_rank']}) → HITL"
        )
    return 0


def cmd_bridge(args) -> int:
    reg = Registry.load(args.root)
    store = _store(args)
    rep = eg_bridge.check(reg, store)
    if args.json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(eg_bridge.format_report(rep))
    return 0 if rep.ok else 1


def cmd_routing(args) -> int:
    reg = Registry.load(args.root)
    store = _store(args)
    rows = eg_bridge.routing_table(reg, store)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{C_B}모델 라우팅 표{C_RST}  — 통제 평면 gate × EG USES_MODEL\n")
    print(f"  {'에이전트':<22} {'EG 조직':<14} {'gate':<11} {'평시':<22} {'L3 관여 시'}")
    print("  " + "─" * 88)
    for r in rows:
        l3 = r.get("model_on_l3") or ("차단" if r.get("l3_blocked") else "—")
        print(
            f"  {r['agent']:<22} {r['eg_org']!s:<14} {r['gate_policy']:<11} "
            f"{r.get('model_normal')!s:<22} {l3}"
        )
    return 0


def cmd_snapshot(args) -> int:
    paths = Paths(args.root)
    path = take_snapshot(
        db_path(paths, args.db),
        paths.root / "eg" / "snapshots",
        label=args.label,
    )
    print(_t(f"✔ 스냅샷 → {path}", C_OK))
    return 0


# ── parser ──────────────────────────────────────────────────────────────


def add_subparser(sub) -> None:
    eg = sub.add_parser("eg", help="Experience Graph — 주입·조회·순회")
    eg.add_argument("--db", default=None, help="EG DB 경로 (기본: var/eg/bastion_graph.db)")
    s = eg.add_subparsers(dest="eg_cmd", required=True)

    p = s.add_parser("load", help="시드 주입")
    p.add_argument("--seeds", default=None)
    p.add_argument("--append", action="store_true", help="기존 거버넌스 계층을 지우지 않는다")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--from-bastion", default=None, help="bastion 시드 DB 경로 — 런타임 EG 위에 얹는다"
    )
    p.set_defaults(func=cmd_load)

    p = s.add_parser("stats", help="그래프 현황")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stats)

    p = s.add_parser("search", help="eg_search — FTS 전문 검색")
    p.add_argument("query")
    p.add_argument("--type", default=None)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = s.add_parser("org", help="조직 프로파일 (개입 지점)")
    p.add_argument("org")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_org)

    p = s.add_parser("severity", help="전 자산 심각도")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_severity)

    p = s.add_parser("gate", help="자산의 게이트 결정")
    p.add_argument("asset")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_gate)

    p = s.add_parser("model", help="조직의 모델 라우팅")
    p.add_argument("org")
    p.add_argument("--l3", action="store_true", help="L3 자산 관여 가정")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_model)

    p = s.add_parser("autonomy", help="자율화 게이트 필요 조합")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_autonomy)

    p = s.add_parser("bridge", help="통제 평면 ↔ EG 정합성 대조")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_bridge)

    p = s.add_parser("routing", help="에이전트별 실효 모델 라우팅 표")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_routing)

    p = s.add_parser("snapshot", help="스냅샷 저장")
    p.add_argument("--label", default="governance")
    p.set_defaults(func=cmd_snapshot)
