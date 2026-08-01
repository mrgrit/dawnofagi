"""관제 콘솔 상태 — 픽셀 오피스와 CLI 가 읽는 단일 소스.

**모든 시각 요소는 여기 있는 실제 텔레메트리에 바인딩된다.** 임의 데이터로
채우는 필드는 하나도 없다 — 없으면 없는 대로 빈다 (01_aoc_architecture:
"장식 애니메이션 금지. 이벤트 발생 시에만 갱신").

섹터 배정: `Asset -LOCATED_IN-> Zone` 순회로 방을 정한다. pipe = 문(PEP).
3계층 뷰: 빌딩(4본부=4층) → 플로어(부서별 방) → 데스크(에이전트).
"""

from __future__ import annotations

import json
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

    kpis = kpi_mod.compute(runs, cases, queue, judges)
    reviews = [
        kpi_mod.review_autonomy(a["agent_id"], a["autonomy_declared"], kpis, cases)
        for a in agents
    ]

    return {
        "generated_at": kpi_mod.now_iso(),
        "collect": lake.stats(runs),
        "divisions": divisions,
        "zones": zones,
        "agents": agents,
        "runs": [r.to_dict() for r in runs[:80]],
        "cases": [c.to_dict() for c in cases],
        "kpis": [k.to_dict() for k in kpis],
        "autonomy_reviews": [r.to_dict() for r in reviews],
        "control": [asdict(s) for s in ks.all()],
        "hitl": [a.to_dict() for a in queue.list()[:40]],
        "responses": Responder(root).history(30),
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
