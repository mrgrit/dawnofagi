"""관제 콘솔 상태 — 픽셀 오피스와 CLI 가 읽는 단일 소스.

**모든 시각 요소는 여기 있는 실제 텔레메트리에 바인딩된다.** 임의 데이터로
채우는 필드는 하나도 없다 — 없으면 없는 대로 빈다 (01_aoc_architecture:
"장식 애니메이션 금지. 이벤트 발생 시에만 갱신").

섹터 배정: `Asset -LOCATED_IN-> Zone` 순회로 방을 정한다. pipe = 문(PEP).
3계층 뷰: 빌딩(4본부=4층) → 플로어(부서별 방) → 데스크(에이전트).

**점유(occupancy)** — 에이전트가 언제 어느 섹터에 있었는지는 추측하지 않는다.
스팬 하나가 곧 체류 구간 하나다:

* `dawn.assets` 가 있는 스팬 → 그 자산이 `LOCATED_IN` 한 존이 있을 곳이다.
  자산을 여럿 건드렸으면 **가장 깊은 존**(민감도 높은 쪽)에 있는 것으로 본다.
* 자산이 없는 스팬(chat·eg.search) → 자기 자리(홈 존)에서 하는 일이다.
* **스팬이 없는 시각 = 대기실.** 일하지 않는 에이전트를 섹터에 세워 두면
  "지금 저기서 뭔가 하고 있다"는 거짓말이 된다.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dawn_agents.hitl import ApprovalQueue
from dawn_core import Registry
from dawn_core.eg.cli import db_path as eg_db_path
from dawn_core.eg.store import EGStore

from . import kpi as kpi_mod
from .collect import TraceLake
from .detect import action_gate_from_run, anomalies, judge_to_detections
from .killswitch import KillSwitch
from .respond import Responder
from .triage import CaseStore, triage

# 존 → 픽셀 오피스 방 (EG Zone.pixel_room 이 권위, 여기는 폴백)
ZONE_FALLBACK = {
    "ext": "Zone 0 · 로비", "user": "Zone 1 · 내부", "dmz": "Zone 2 · 제한",
    "int": "Zone 3 · 통제", "pipe": "존 사이의 문",
}
ZONE_ORDER = ["ext", "pipe", "user", "dmz", "int"]

# 대기실 — el34 존이 아니다. "진행 중인 스팬이 없다"는 상태 그 자체를 그리는 방.
LOUNGE = {
    "id": "lounge",
    "name": "대기실 · 휴게실",
    "note": "진행 중인 스팬이 없는 에이전트가 있는 곳 — 업무 중이 아니다",
}


def build_state(root: Path, *, limit: int = 200, judges: dict | None = None) -> dict[str, Any]:
    """콘솔·픽셀오피스가 읽는 상태 스냅샷을 만든다."""
    reg = Registry.load(root)
    db = eg_db_path(reg.paths)
    eg = EGStore(db) if db.is_file() else None

    lake = TraceLake(root)
    runs = lake.all_runs(limit=limit)
    lake.persist(runs)

    cases_store = CaseStore(root)
    ks = KillSwitch(root)
    queue = ApprovalQueue(root)
    cases = cases_store.list()

    # ── 조직 = 빌딩/층/방 ───────────────────────────────────────────────
    divisions = []
    for did, div in sorted(reg.divisions.items()):
        teams = []
        for tid in div.data.get("teams", []):
            t = reg.teams[tid]
            agents = t.agent_ids
            t_runs = [r for r in runs if r.team == tid]
            teams.append({
                "team_id": tid,
                "name": t.data["name"],
                "eg_org": t.data.get("eg_org", ""),
                "zone": t.data.get("zone", div.data.get("zone", "")),
                "agents": agents,
                "runs": len(t_runs),
                "activity": sum(r.steps for r in t_runs),
                # ── 업무·권한 (매니페스트가 권위) ──────────────────────
                "mission": t.data.get("mission", ""),
                "persona_default": t.data.get("persona_default", ""),
                "work_domains": list(t.data.get("work_domains", [])),
                "max_sensitivity": t.data.get("max_sensitivity", ""),
                "escalation_to": t.data.get("escalation_to") or "",
            })
        d_runs = [r for r in runs if r.division == did]
        divisions.append({
            "division_id": did,
            "name": div.data["name"],
            "color": div.data.get("color", "#888888"),
            "zone": div.data.get("zone", ""),
            "teams": teams,
            "runs": len(d_runs),
            "activity": sum(r.steps for r in d_runs),   # 밝기 = 활동량
        })

    # ── 존 = 섹터 (EG Zone 노드가 권위) ────────────────────────────────
    zones = []
    if eg is not None:
        for z in sorted(eg.nodes(type="Zone"), key=lambda n: ZONE_ORDER.index(n.id.split(":")[-1])
                        if n.id.split(":")[-1] in ZONE_ORDER else 99):
            zid = z.id.split(":")[-1]
            assets = [
                {"id": a.id, "name": a.name, "kind": a.prop("kind", ""),
                 "irreversibility": a.prop("irreversibility", "read")}
                for a in eg.inc(z.id, "LOCATED_IN")
            ]
            sec = next((s.id for s in eg.out(z.id, "MAPS_TO")), "")
            zones.append({
                "zone_id": z.id, "short": zid, "cidr": z.prop("cidr", ""),
                "sensitivity": z.prop("sensitivity", ""),
                "is_gate": bool(z.prop("is_gate", False)),
                "pixel_room": z.prop("pixel_room", ZONE_FALLBACK.get(zid, zid)),
                "security_level": sec,
                "assets": assets,
            })

    # ── 에이전트 = 아바타 ───────────────────────────────────────────────
    agents = kpi_mod.registry_view(reg, eg, runs, cases, ks)
    for a in agents:
        z = a.get("zone") or ""
        a["room"] = next((zz["pixel_room"] for zz in zones if zz["short"] == z),
                         ZONE_FALLBACK.get(z, "미배정"))
        a["division_color"] = next(
            (d["color"] for d in divisions if d["division_id"] == a["division"]), "#888888"
        )
        # 아바타 인코딩: 몸색=본부, 배지=모델, 모자=자율화, 이펙트=상태
        a["badge"] = _model_badge(a.get("last_model", ""))
        a["hat"] = a["autonomy"]
        a["effect"] = _effect(a)
        # 말풍선 대신 EG 아이콘 — 이 에이전트가 참조 중인 EG 노드
        mine = [r for r in runs if r.agent_id == a["agent_id"]]
        a["eg_refs"] = sorted({
            p for r in mine[:5] for p in (r.policies + r.assets)
        })[:6]
        a["last_trace"] = mine[0].trace_id if mine else ""

    # ── 권한 = 컴파일된 실효 경계 (문장이 아니라 기계가 강제하는 값) ──
    for a in agents:
        a["authority"] = _authority(reg, a["agent_id"])

    # ── 점유 = 스팬. 없는 시각은 대기실 ────────────────────────────────
    occupancy = _occupancy(runs, zones, {a["agent_id"]: a.get("zone", "") for a in agents})
    for a in agents:
        mine = [s for s in occupancy if s["agent_id"] == a["agent_id"]]
        a["busy_ms"] = round(sum(s["end_ns"] - s["start_ns"] for s in mine) / 1e6, 1)
        a["sectors"] = sorted({s["zone"] for s in mine if s["kind"] == "work"})

    kpis = kpi_mod.compute(runs, cases, queue, judges)
    reviews = [
        kpi_mod.review_autonomy(a["agent_id"], a["autonomy_declared"], kpis, cases)
        for a in agents
    ]

    return {
        "generated_at": kpi_mod.now_iso(),
        "now_ns": time.time_ns(),
        "collect": lake.stats(runs),
        "divisions": divisions,
        "zones": zones,
        "floorplan": _floorplan(divisions, zones, agents, occupancy, runs, cases,
                                queue, reg),
        "occupancy": occupancy,
        "window": _window(occupancy),
        "agents": agents,
        "runs": [r.to_dict() for r in runs[:80]],
        "cases": [c.to_dict() for c in cases],
        "kpis": [k.to_dict() for k in kpis],
        "autonomy_reviews": [r.to_dict() for r in reviews],
        "control": [asdict(s) for s in ks.all()],
        "hitl": [a.to_dict() for a in queue.list()[:40]],
        "responses": Responder(root).history(30),
    }


def _sec_rank(zone: dict[str, Any]) -> int:
    """보안등급 숫자. `sec:L2` → 2. 층 안에서 섹터를 리스크 순으로 세우는 기준."""
    sec = str(zone.get("security_level", ""))
    return int(sec.split("L")[-1]) if "L" in sec else 9


def _occupancy(runs, zones: list[dict[str, Any]],
               home: dict[str, str]) -> list[dict[str, Any]]:
    """스팬 → 섹터 체류 구간. **한 줄도 만들어내지 않는다** (스팬 하나 = 구간 하나).

    `dawn.assets` 가 그 스팬이 어느 존에 있었는지를 말해 준다. 자산을 여럿
    건드렸으면 가장 깊은 존을 택한다 — 낮은 쪽에 세우면 실제보다 안전해 보인다.
    """
    asset_zone = {a["id"]: z["short"] for z in zones for a in z["assets"]}
    depth = {z["short"]: (_sec_rank(z), i) for i, z in enumerate(zones)}

    segs: list[dict[str, Any]] = []
    for r in runs:
        if r.is_orchestrator:
            continue
        desk = home.get(r.agent_id) or r.zone
        for s in r.spans:
            if s.get("name") == "invoke_agent":     # run 을 접는 껍데기 스팬
                continue
            at = s.get("attributes") or {}
            touched = [a for a in str(at.get("dawn.assets", "")).split(",") if a]
            in_zone = [asset_zone[a] for a in touched if a in asset_zone]
            zone = max(in_zone, key=lambda z: depth.get(z, (-1, -1))) if in_zone else ""
            start = int(s.get("start_ns", 0) or 0)
            segs.append({
                "agent_id": r.agent_id,
                "trace_id": r.trace_id,
                "zone": zone or desk,
                "kind": "work" if zone else "desk",   # desk = 자기 자리에서 생각·조회
                "span": str(s.get("name", "")),
                "tool": str(at.get("gen_ai.tool.name", "")),
                "asset": next((a for a in touched if asset_zone.get(a) == zone), ""),
                "gate": str(at.get("dawn.gate.decision", "")),
                "severity": int(at.get("dawn.severity", 0) or 0),
                "status": str(s.get("status", "OK")),
                "start_ns": start,
                "end_ns": int(s.get("end_ns", 0) or start),
            })
    segs.sort(key=lambda x: (x["start_ns"], x["agent_id"]))
    return segs


# org/tools.yaml: action 생략 시 risk 에서 추정한다 (LOW=read / MED=write / HIGH=execute)
_ACTION_FALLBACK = {"LOW": "read", "MED": "write", "HIGH": "execute"}


def _authority(reg, agent_id: str) -> dict[str, Any]:
    """이 에이전트의 **실효 경계**. 문장이 아니라 게이트 체인을 병합한 결과다.

    통제 평면(L1→L4)이 단조 축소되며 겹쳐진 최종값이고, 에이전트는 이걸 넘을 수
    없다. 콘솔에 이 값을 그대로 띄우는 이유: "무엇을 할 수 있나"를 문서에서
    유추하게 두면 아무도 확인하지 않는다.
    """
    from dawn_core.control_plane import compile_agent

    try:
        c = compile_agent(reg, agent_id)
    except Exception as e:                       # 컴파일 실패도 표시해야 할 사실이다
        return {"error": f"{type(e).__name__}: {e}"}
    g = c.gate.to_dict(c.declared_tools)
    return {
        "allow": g["tools"]["allow_patterns"],
        "deny": g["tools"]["deny_patterns"],
        "effective": g["tools"].get("effective", []),
        "declared": c.declared_tools,
        "autonomy": g["autonomy"],
        "hitl_require_on": g["hitl"]["require_on"],
        "amount_threshold_krw": g["hitl"].get("amount_threshold_krw"),
        "budget": g["budget"],
        "model_policy": g["model"]["policy"],
        "model_pinned": g["model"].get("pinned", ""),
        "force_local_when": g["model"]["force_local_when"],
        "sources": g["sources"],                 # 어느 계층이 이 경계를 좁혔나
        "layers": [{"level": ly.level, "label": ly.label} for ly in c.layers],
        "works": list(c.works),                  # L3 — 수행하는 업무 SOP
        "role": c.role,
        "warnings": list(c.warnings),
    }


def _floorplan(divisions: list[dict[str, Any]], zones: list[dict[str, Any]],
               agents: list[dict[str, Any]], occ: list[dict[str, Any]],
               runs, cases, queue, reg) -> dict[str, Any]:
    """빌딩 = 층(본부) × 섹터(존, 리스크 순) + 대기실.

    층에 그리는 섹터는 **그 본부가 실제로 쓰는 존만**이다: 팀 매니페스트가
    선언한 존 ∪ 에이전트의 홈 존 ∪ 텔레메트리가 실제로 들어간 존. 안 쓰는 방을
    그려 두면 빈 방이 정상인지 장애인지 구분이 안 된다.

    층마다 **업무(무엇이 실제로 돌았나)** 와 **권한(무엇을 할 수 있나)** 을 같이
    싣는다. 둘을 나란히 놔야 "권한은 있는데 안 쓴다 / 쓰는데 권한이 아슬아슬하다"가
    보인다.
    """
    div_of = {a["agent_id"]: a["division"] for a in agents}
    catalog = reg.tool_catalog.tools

    visits: dict[tuple[str, str], int] = {}
    for s in occ:
        d = div_of.get(s["agent_id"], "")
        if d:
            visits[(d, s["zone"])] = visits.get((d, s["zone"]), 0) + 1

    floors = []
    for level, d in enumerate(divisions):
        did = d["division_id"]
        mine = [a for a in agents if a["division"] == did]
        # **모든 존을 층에 세운다.** 안 쓰는 존을 빼면 그 존이 화면에서 사라져
        # "우리 회사에 없는 구역"처럼 보인다 — 안 쓴다는 사실 자체가 봐야 할 정보다.
        used = {t["zone"] for t in d["teams"] if t["zone"]}
        used |= {a["zone"] for a in mine if a.get("zone")}
        used |= {z for (dv, z) in visits if dv == did}
        sectors = [
            {**z, "visits": visits.get((did, z["short"]), 0),
             "used": z["short"] in used,
             "teams": [t["team_id"] for t in d["teams"] if t["zone"] == z["short"]],
             **_sector_work(did, z["short"], mine, occ, catalog)}
            for z in zones if not z["is_gate"]
        ]
        sectors.sort(key=lambda z: (_sec_rank(z), z["short"]))

        floors.append({
            "level": level, "division_id": did, "name": d["name"], "color": d["color"],
            "zone": d["zone"], "runs": d["runs"], "activity": d["activity"],
            "sectors": sectors,
            "agents": [a["agent_id"] for a in mine],
            "work": _floor_work(did, mine, occ, runs, cases, queue, catalog),
            "authority": _floor_authority(mine, d["teams"]),
        })

    return {
        "floors": floors,
        "lounge": LOUNGE,
        # pipe = 방이 아니라 섹터 사이의 문. 층마다 섹터 경계에 세운다.
        "gates": [z for z in zones if z["is_gate"]],
    }


def _sector_work(did: str, short: str, mine: list[dict[str, Any]],
                 occ: list[dict[str, Any]], catalog: dict[str, Any]) -> dict[str, Any]:
    """이 방에서 **실제로 무슨 업무가 돌았나**.

    두 가지를 구분해서 싣는다 — 섞으면 어느 쪽이 사실인지 알 수 없다:

    * `tools` / `visitors` — 텔레메트리. 실제로 부른 도구와 들어온 사람이다.
    * `works` — 선언. 여기 들어온 에이전트가 맡은 L3 업무 SOP 다. 어느 호출이
      어느 SOP 였는지는 스팬에 없으므로 **에이전트 단위로만** 붙인다.
    """
    by_id = {a["agent_id"]: a for a in mine}
    segs = [s for s in occ if s["zone"] == short and s["agent_id"] in by_id]

    tools: dict[str, dict[str, Any]] = {}
    for s in segs:
        if not s["tool"]:
            continue
        meta = catalog.get(s["tool"], {})
        risk = meta.get("risk", "")
        t = tools.setdefault(s["tool"], {
            "tool": s["tool"], "calls": 0, "gate": {}, "risk": risk,
            "action": meta.get("action") or _ACTION_FALLBACK.get(risk, ""),
            "destructive": bool(meta.get("destructive", False)),
        })
        t["calls"] += 1
        if s["gate"]:
            t["gate"][s["gate"]] = t["gate"].get(s["gate"], 0) + 1

    visitors = []
    for aid in sorted({s["agent_id"] for s in segs}):
        a = by_id[aid]
        visitors.append({
            "agent_id": aid, "name": a["name"], "team": a["team"],
            "calls": sum(1 for s in segs if s["agent_id"] == aid),
            "works": a["authority"].get("works", []),
        })
    homed = [a for a in mine if a.get("zone") == short]
    return {
        "tools": sorted(tools.values(), key=lambda t: (-t["calls"], t["tool"])),
        "visitors": visitors,
        "entries": len(segs),
        "works": sorted({w for v in visitors for w in v["works"]}
                        | {w for a in homed for w in a["authority"].get("works", [])}),
        "homed": [a["agent_id"] for a in homed],
        "division_id": did,
    }


def _floor_work(did: str, mine: list[dict[str, Any]], occ: list[dict[str, Any]],
                runs, cases, queue, catalog: dict[str, Any]) -> dict[str, Any]:
    """이 층에서 **실제로 돌아간 업무**. 전부 텔레메트리·케이스·승인 큐에서 온다."""
    ids = {a["agent_id"] for a in mine}
    segs = [s for s in occ if s["agent_id"] in ids]
    my_runs = [r for r in runs if r.agent_id in ids]

    tools: dict[str, dict[str, Any]] = {}
    for s in segs:
        if not s["tool"]:
            continue
        meta = catalog.get(s["tool"], {})
        risk = meta.get("risk", "")
        t = tools.setdefault(s["tool"], {
            "tool": s["tool"], "calls": 0, "gate": {}, "zones": [], "risk": risk,
            # action 이 생략된 도구는 risk 에서 추정한다 (org/tools.yaml 의 보수적 폴백)
            "action": meta.get("action") or _ACTION_FALLBACK.get(risk, ""),
            "destructive": bool(meta.get("destructive", False)),
        })
        t["calls"] += 1
        if s["gate"]:
            t["gate"][s["gate"]] = t["gate"].get(s["gate"], 0) + 1
        if s["zone"] not in t["zones"]:
            t["zones"].append(s["zone"])
    ranked = sorted(tools.values(), key=lambda t: (-t["calls"], t["tool"]))

    decisions: dict[str, int] = {}
    for r in my_runs:
        for k, v in r.gate_decisions.items():
            decisions[k] = decisions.get(k, 0) + v

    return {
        "runs": len(my_runs),
        "complete": sum(1 for r in my_runs if r.complete),
        "tokens": sum(r.tokens for r in my_runs),
        "tools": ranked,
        "gate_decisions": decisions,
        "blocked": sorted({b for r in my_runs for b in r.blocked}),
        "assets": sorted({s["asset"] for s in segs if s["asset"]}),
        "hitl": [a.to_dict() for a in queue.list() if a.agent_id in ids][:12],
        "cases": [c.to_dict() for c in cases if c.agent_id in ids][:12],
        "recent": [
            {"trace_id": r.trace_id, "agent_id": r.agent_id, "started_ns": r.started_ns,
             "steps": r.steps, "status": r.status, "complete": r.complete,
             "tools": r.tool_sequence[:8], "model": r.model,
             "max_severity": r.max_severity}
            for r in my_runs[:8]
        ],
        "division_id": did,
    }


def _floor_authority(mine: list[dict[str, Any]],
                     teams: list[dict[str, Any]]) -> dict[str, Any]:
    """이 층이 **가진 권한**. 에이전트별 실효 경계를 층 단위로 접는다."""
    auths = [a["authority"] for a in mine if not a["authority"].get("error")]
    return {
        "agents": [
            {"agent_id": a["agent_id"], "name": a["name"], "team": a["team"],
             "autonomy": a["authority"].get("autonomy", a["autonomy"]),
             "effective": a["authority"].get("effective", []),
             "deny": a["authority"].get("deny", []),
             "hitl_require_on": a["authority"].get("hitl_require_on", []),
             "amount_threshold_krw": a["authority"].get("amount_threshold_krw"),
             "budget": a["authority"].get("budget", {}),
             "model_policy": a["authority"].get("model_policy", ""),
             "sources": a["authority"].get("sources", []),
             "works": a["authority"].get("works", []),
             "layers": a["authority"].get("layers", []),
             "error": a["authority"].get("error", "")}
            for a in mine
        ],
        # 층 전체가 쓸 수 있는 도구 = 합집합. 못 쓰는 것 = 전원 공통 deny.
        "effective_union": sorted({t for x in auths for t in x.get("effective", [])}),
        "deny_common": sorted(set.intersection(
            *[set(x.get("deny", [])) for x in auths]) if auths else set()),
        "work_domains": sorted({w for t in teams for w in t.get("work_domains", [])}),
        "max_sensitivity": max((t.get("max_sensitivity", "") for t in teams), default=""),
    }


def _window(occ: list[dict[str, Any]]) -> dict[str, int]:
    """오피스 시계가 훑는 구간 — 첫 스팬부터 마지막 스팬까지. 그 밖은 전원 대기실."""
    if not occ:
        return {"start_ns": 0, "end_ns": 0, "segments": 0}
    return {
        "start_ns": min(s["start_ns"] for s in occ),
        "end_ns": max(s["end_ns"] for s in occ),
        "segments": len(occ),
    }


def _model_badge(model: str) -> str:
    m = (model or "").lower()
    if "opus" in m:
        return "OPUS"
    if "sonnet" in m:
        return "SONNET"
    if "haiku" in m:
        return "HAIKU"
    if m:
        return "LOCAL"
    return ""


def _effect(agent: dict[str, Any]) -> str:
    if agent["control_state"] == "killed":
        return "killed"
    if agent["control_state"] == "isolated":
        return "isolated"
    if agent["control_state"] == "paused":
        return "paused"
    if agent.get("worst_case") in ("critical", "high"):
        return "alert"
    if agent["runs"] == 0:
        return "idle"
    return "working"


def write_state(root: Path, **kw) -> Path:
    st = build_state(root, **kw)
    out = Path(root) / "var" / "aoc" / "state.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


# ── 파이프라인 — 수집 → 탐지 → 트리아지 (대응은 별도 호출) ──────────────


def scan(root: Path, *, with_judge: bool = False, limit: int = 50) -> dict[str, Any]:
    """관제 1회전. 새 케이스를 만들고 저장한다."""
    reg = Registry.load(root)
    db = eg_db_path(reg.paths)
    eg = EGStore(db) if db.is_file() else None

    lake = TraceLake(root)
    runs = lake.all_runs(limit=limit)
    store = CaseStore(root)
    existing = {c.trace_id + "|" + c.agent_id for c in store.list()}

    new_cases, judged = [], {}
    for r in runs:
        dets = list(anomalies(r))
        dets += action_gate_from_run(r).detections

        if with_judge and r.chat_calls and not r.is_orchestrator:
            from .detect import judge

            task, out = _task_and_output(r)
            if out:
                jr = judge(task or (r.agent_name or r.agent_id), out,
                           watched_policy_id=r.model_policy, eg_store=eg)
                judged[r.trace_id] = jr
                dets += judge_to_detections(r, jr)

        if not dets:
            continue
        key = r.trace_id + "|" + r.agent_id
        if key in existing:
            continue
        c = triage(r, dets, eg_store=eg)
        if c:
            store.save(c)
            new_cases.append(c)

    return {
        "runs_scanned": len(runs),
        "new_cases": [c.to_dict() for c in new_cases],
        "judged": {k: v.to_dict() for k, v in judged.items()},
        "_judge_objs": judged,
    }


def _task_and_output(run) -> tuple[str, str]:
    """judge 에 넘길 (요구한 업무, 산출물).

    둘 다 chat 스팬의 semconv 이벤트에서 꺼낸다 — `gen_ai.user.message` 가 지시,
    `gen_ai.choice` 가 응답이다. 업무를 안 주면 judge 는 "무엇에 비추어" 완결성을
    볼 기준이 없어져 근거 점수만 매기게 된다.
    """
    task = out = ""
    for s in run.spans:
        if s["name"] != "chat":
            continue
        for ev in s.get("events", []):
            c = str(ev.get("attributes", {}).get("content", ""))
            if ev.get("name") == "gen_ai.user.message" and not task:
                task = c
            elif ev.get("name") == "gen_ai.choice":
                out = c              # 마지막 응답이 산출물이다
    return task, out
