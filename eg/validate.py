#!/usr/bin/env python3
"""EG 시드 검증 — 주입 전 무결성 + 핵심 순회 실증.

BOOTSTRAP.md 가 요구하는 검증기. **오류 0 이어야 주입 가능.**

  1. 스키마 정합   — 노드/엣지 타입이 schema.json 에 있나, 필수 속성이 있나, enum 이 맞나
  2. 참조 무결성   — 엣지의 from/to 가 실존하나, 방향(from 타입→to 타입)이 맞나
  3. 커버리지     — 모든 Asset 은 등급·존을 갖나, 모든 Zone 은 등급에 매핑되나,
                    모든 leaf OrgUnit 은 페르소나·모델·자율화를 갖나
  4. 핵심 순회 실증 — 심각도 / 게이트 / 개입지점

경고는 오류가 아니다. `[등급-존 괴리] asset:bastion` 은 의도된 것이다
(외부 존에 있는 고위험 도구 — 04_assets.json 의 _design_note_bastion 참조).

  python3 eg/validate.py             # 시드 파일 검증 (DB 불필요)
  python3 eg/validate.py --db PATH   # 주입된 DB 를 대상으로 검증
  python3 eg/validate.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# 저장소 안에서 실행하면 설치 없이도 dawn_core 를 쓸 수 있게 한다.
sys.path.insert(0, str(ROOT / "packages" / "dawn_core"))

from dawn_core.eg.store import (  # noqa: E402
    GOVERNANCE_EDGE_TYPES,
    GOVERNANCE_NODE_TYPES,
    EGStore,
)
from dawn_core.eg.traverse import (  # noqa: E402
    all_severities,
    autonomy_violations,
    gate_for,
    org_profile,
)

C = {
    "r": "\033[31m",
    "g": "\033[32m",
    "y": "\033[33m",
    "c": "\033[36m",
    "b": "\033[1m",
    "d": "\033[2m",
    "0": "\033[0m",
}
if not sys.stdout.isatty():
    C = dict.fromkeys(C, "")

REQUIRED_PROPS = {
    "OrgUnit": ["name", "type", "mission"],
    "Persona": ["role", "principles", "prohibited"],
    "Policy": ["statement", "category", "severity", "enforcement"],
    "SecurityLevel": ["rank", "label", "handling_rule"],
    "Zone": ["cidr", "sensitivity"],
    "Asset": ["name", "kind", "irreversibility"],
    "AutonomyLevel": ["level", "label", "gate_rule"],
    "ModelPolicy": ["model", "cost_tier"],
}

ENUMS = {
    ("OrgUnit", "type"): {"bureau", "dept", "team"},
    ("Policy", "category"): {"security", "privacy", "compliance", "quality", "financial", "hr"},
    ("Policy", "severity"): {"low", "medium", "high", "critical"},
    ("Policy", "enforcement"): {"block", "require_hitl", "warn", "log_only"},
    ("Asset", "kind"): {"data", "system", "tool", "mcp"},
    ("Asset", "irreversibility"): {"read", "write", "execute", "irreversible"},
    ("ModelPolicy", "cost_tier"): {"high", "mid", "low", "local"},
}

# 엣지 방향 — schema.json edge_types 와 일치해야 한다
EDGE_DIRECTION = {
    "PART_OF": ("OrgUnit", "OrgUnit"),
    "HAS_PERSONA": ("OrgUnit", "Persona"),
    "USES_MODEL": ("OrgUnit", "ModelPolicy"),
    "OPERATES_AT": ("OrgUnit", "AutonomyLevel"),
    "GOVERNED_BY": ("Persona", "Policy"),
    "APPLIES_TO": ("Policy", "SecurityLevel"),
    "CLASSIFIED_AS": ("Asset", "SecurityLevel"),
    "LOCATED_IN": ("Asset", "Zone"),
    "MAPS_TO": ("Zone", "SecurityLevel"),
    "ACTS_ON": ("Skill", "Asset"),
    "OWNED_BY": ("Asset", "OrgUnit"),
}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


# ── 시드에서 그래프 모델 만들기 ──────────────────────────────────────────


def load_from_seeds(seed_dir: Path) -> tuple[dict[str, dict], list[dict]]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for f in sorted(seed_dir.glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        for key, val in doc.items():
            if key.startswith("_") or key == "edges":
                continue
            for item in val:
                nodes[item["id"]] = {**item, "_type": key, "_seed": f.stem}
        for e in doc.get("edges", []):
            edges.append({**e, "_seed": f.stem})
    return nodes, edges


def load_from_db(db_path: Path) -> tuple[dict[str, dict], list[dict]]:
    store = EGStore(db_path)
    nodes = {
        n.id: {**n.content, "id": n.id, "_type": n.type, "_seed": n.meta.get("seed", "?")}
        for n in store.nodes(layer="governance")
    }
    gov = set(nodes)
    edges = [
        {"type": e.type, "from": e.src, "to": e.dst, "_seed": e.meta.get("seed", "?")}
        for e in store.edges()
        if e.src in gov and e.dst in gov
    ]
    return nodes, edges


# ── 검증 ────────────────────────────────────────────────────────────────


def validate(nodes: dict[str, dict], edges: list[dict]) -> Report:
    rep = Report()

    # 1. 스키마 정합
    for nid, n in nodes.items():
        ntype = n["_type"]
        if ntype not in GOVERNANCE_NODE_TYPES:
            rep.err(f"[스키마] 알 수 없는 노드 타입: {ntype} ({nid})")
            continue
        prefix = nid.split(":", 1)[0] if ":" in nid else ""
        expected = {
            "OrgUnit": "org",
            "Persona": "persona",
            "Policy": "pol",
            "SecurityLevel": "sec",
            "Zone": "zone",
            "Asset": "asset",
            "AutonomyLevel": "auto",
            "ModelPolicy": "model",
        }[ntype]
        if prefix != expected:
            rep.err(f"[스키마] id 접두사 불일치: {nid} — {ntype} 는 '{expected}:' 여야 한다")
        for prop in REQUIRED_PROPS.get(ntype, []):
            if prop not in n or n[prop] in (None, "", []):
                rep.err(f"[스키마] {nid}: 필수 속성 누락 — {prop}")
        for (t, prop), allowed in ENUMS.items():
            if t == ntype and prop in n and n[prop] not in allowed:
                rep.err(f"[스키마] {nid}.{prop}={n[prop]!r} — 허용: {sorted(allowed)}")

    # 2. 참조 무결성 + 방향
    seen: set[tuple[str, str, str]] = set()
    for e in edges:
        et = e["type"]
        if et not in GOVERNANCE_EDGE_TYPES:
            rep.err(f"[엣지] 알 수 없는 엣지 타입: {et}")
            continue
        for side in ("from", "to"):
            if e[side] not in nodes:
                rep.err(f"[참조] {et} 의 {side}='{e[side]}' 노드가 없다 ({e.get('_seed')})")
        if e["from"] in nodes and e["to"] in nodes:
            want_src, want_dst = EDGE_DIRECTION[et]
            got_src, got_dst = nodes[e["from"]]["_type"], nodes[e["to"]]["_type"]
            if got_src != want_src or got_dst != want_dst:
                rep.err(
                    f"[방향] {et}: {want_src}→{want_dst} 여야 하는데 "
                    f"{got_src}→{got_dst} ({e['from']}→{e['to']})"
                )
        key = (e["from"], e["to"], et)
        if key in seen:
            rep.err(f"[중복] 엣지 중복 — {et} {e['from']}→{e['to']}")
        seen.add(key)

    # 3. 커버리지
    by_type: dict[str, list[str]] = {}
    for nid, n in nodes.items():
        by_type.setdefault(n["_type"], []).append(nid)

    def has_edge(src: str, etype: str) -> bool:
        return any(e["from"] == src and e["type"] == etype for e in edges)

    for aid in by_type.get("Asset", []):
        if not has_edge(aid, "CLASSIFIED_AS"):
            rep.err(f"[커버리지] Asset {aid} 에 보안등급(CLASSIFIED_AS)이 없다")
        if not has_edge(aid, "LOCATED_IN"):
            rep.err(f"[커버리지] Asset {aid} 에 존(LOCATED_IN)이 없다")
        if not has_edge(aid, "OWNED_BY") and nodes[aid].get("owner_org"):
            rep.warn(
                f"[파생대상] Asset {aid}: owner_org={nodes[aid]['owner_org']} 속성만 있고 "
                f"OWNED_BY 엣지가 없다 — 주입 시 자동 파생된다(loader.derive_owned_by)"
            )

    for zid in by_type.get("Zone", []):
        if not has_edge(zid, "MAPS_TO"):
            rep.err(f"[커버리지] Zone {zid} 가 보안등급에 매핑되지 않았다")

    for pid in by_type.get("Policy", []):
        if not has_edge(pid, "APPLIES_TO"):
            rep.warn(f"[커버리지] Policy {pid} 가 어떤 보안등급에도 걸리지 않는다")

    # leaf 조직(자식이 없는 조직)은 페르소나·모델·자율화를 가져야 한다
    parents = {e["to"] for e in edges if e["type"] == "PART_OF"}
    for oid in by_type.get("OrgUnit", []):
        if oid in parents:
            continue
        for etype, what in (("USES_MODEL", "모델"), ("OPERATES_AT", "자율화 등급")):
            if not has_edge(oid, etype):
                rep.err(f"[커버리지] leaf 조직 {oid} 에 {what}({etype})가 없다")

    # 페르소나 도달성 — 상속(PART_OF)으로라도 닿아야 한다
    part_of = {e["from"]: e["to"] for e in edges if e["type"] == "PART_OF"}
    persona_of = {e["from"] for e in edges if e["type"] == "HAS_PERSONA"}
    for oid in by_type.get("OrgUnit", []):
        cur, hops = oid, 0
        while cur not in persona_of and cur in part_of and hops < 6:
            cur = part_of[cur]
            hops += 1
        if cur not in persona_of:
            rep.err(f"[커버리지] 조직 {oid} 가 페르소나에 도달하지 못한다(상속 포함)")

    # 등급-존 괴리 — 경고 (의도된 사례 있음)
    zone_sec = {e["from"]: e["to"] for e in edges if e["type"] == "MAPS_TO"}
    asset_sec = {e["from"]: e["to"] for e in edges if e["type"] == "CLASSIFIED_AS"}
    asset_zone = {e["from"]: e["to"] for e in edges if e["type"] == "LOCATED_IN"}
    for aid, zid in asset_zone.items():
        zs, as_ = zone_sec.get(zid), asset_sec.get(aid)
        if zs and as_ and zs != as_:
            zr = nodes[zs].get("rank", 0)
            ar = nodes[as_].get("rank", 0)
            if ar > zr:
                rep.warn(
                    f"[등급-존 괴리] {aid}: 자산 {as_}(rank {ar}) > 존 {zid}→{zs}(rank {zr}) "
                    f"— 낮은 민감도 존의 고등급 자산"
                )

    for t in sorted(GOVERNANCE_NODE_TYPES):
        rep.info.append(f"{t:15} {len(by_type.get(t, [])):3}")
    return rep


# ── 핵심 순회 실증 ───────────────────────────────────────────────────────


def demo_traversals(db_path: Path) -> str:
    store = EGStore(db_path)
    out: list[str] = []

    out.append(f"\n{C['b']}① 심각도 = 비가역성 + 보안등급rank{C['0']}")
    sevs = all_severities(store)
    for s in sevs[:5]:
        out.append("   " + s.line())
    out.append(f"   {C['d']}… 상위 5개 / 전체 {len(sevs)}개{C['0']}")
    for s in sevs[-2:]:
        out.append("   " + s.line())

    out.append(f"\n{C['b']}② 게이트 = 자산→등급→걸린 정책의 enforcement{C['0']}")
    for aid in ("asset:pii", "asset:payment", "asset:crm", "asset:web-search"):
        if store.node(aid) is None:
            continue
        g = gate_for(store, aid)
        out.append(
            f"   {g.asset_name:<22} → {g.sec_id or '?':<7} → 정책 {len(g.policies):2}개 "
            f"→ {{{', '.join(sorted(g.enforcements)) or '없음'}}}  "
            f"최강={C['r'] if g.strongest == 'block' else ''}{g.strongest}{C['0']}"
        )

    out.append(f"\n{C['b']}③ 개입 지점 = 조직 → 페르소나 → 정책{C['0']}")
    for oid in ("org:ccc", "org:hr", "org:ax-sec", "org:aoc-dev"):
        if store.node(oid) is None:
            continue
        p = org_profile(store, oid)
        models = ", ".join(m.prop("model") for m in p.models) or "—"
        out.append(
            f"   {p.org_name:<14} → {', '.join(x.id for x in p.personas) or '—':<42} "
            f"| {p.autonomy.id if p.autonomy else '—':<8} | {models}"
        )
        if p.policies:
            out.append(f"      {C['d']}적용 정책: {', '.join(x.id for x in p.policies)}{C['0']}")

    viol = autonomy_violations(store)
    if viol:
        out.append(f"\n{C['b']}④ 자율화 게이트 필요 조합 (pol:autonomy-gate){C['0']}")
        for v in viol:
            out.append(
                f"   {C['y']}⚠{C['0']} {v['org_name']}({v['autonomy']}, lvl {v['autonomy_level']}) "
                f"< {v['asset_name']}(rank {v['sec_rank']}) → HITL 필요"
            )
    return "\n".join(out)


# ── main ────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EG 시드·그래프 검증")
    ap.add_argument("--seeds", default=str(HERE / "seed"), help="시드 디렉터리")
    ap.add_argument("--db", default=None, help="주입된 DB 를 검증 (없으면 시드 파일 검증)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-demo", action="store_true", help="핵심 순회 실증 생략")
    args = ap.parse_args(argv)

    if args.db:
        db = Path(args.db)
        if not db.is_file():
            print(f"{C['r']}✘ DB 가 없다: {db}{C['0']}", file=sys.stderr)
            return 2
        nodes, edges = load_from_db(db)
        source = f"DB {db}"
    else:
        seed_dir = Path(args.seeds)
        if not seed_dir.is_dir():
            print(f"{C['r']}✘ 시드 디렉터리가 없다: {seed_dir}{C['0']}", file=sys.stderr)
            return 2
        nodes, edges = load_from_seeds(seed_dir)
        source = f"시드 {seed_dir}"

    rep = validate(nodes, edges)

    if args.json:
        print(
            json.dumps(
                {
                    "source": source,
                    "nodes": len(nodes),
                    "edges": len(edges),
                    "errors": rep.errors,
                    "warnings": rep.warnings,
                    "ok": rep.ok,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if rep.ok else 1

    print(f"\n{C['b']}EG 검증{C['0']}  —  {source}")
    print("─" * 62)
    for line in rep.info:
        print(f"  {line}")
    print("─" * 62)
    print(f"  {'노드':15} {len(nodes):3}\n  {'엣지':15} {len(edges):3}")
    print("─" * 62)

    if rep.errors:
        print(f"\n{C['r']}오류 {len(rep.errors)}건{C['0']}")
        for e in rep.errors:
            print(f"  {C['r']}✘{C['0']} {e}")
    if rep.warnings:
        print(f"\n{C['y']}경고 {len(rep.warnings)}건{C['0']} {C['d']}(오류 아님){C['0']}")
        for w in rep.warnings:
            print(f"  {C['y']}!{C['0']} {w}")

    if rep.ok and args.db and not args.no_demo:
        print(demo_traversals(Path(args.db)))

    print()
    if rep.ok:
        print(f"{C['g']}✔ 오류 0 — 주입 가능{C['0']}\n")
        return 0
    print(f"{C['r']}✘ 오류 {len(rep.errors)}건 — 주입 불가{C['0']}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
