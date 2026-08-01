"""트리아지·대응 계층 (AOC 5계층의 [3]).

    심각도 = Asset.irreversibility × SecurityLevel.rank   ← EG 순회
    대응    일시중지/kill · 자격증명 회수 · 도구 차단 · 샌드박스 격리 ·
            산출물 롤백 · HITL 에스컬레이션 · 규제 보고

**kill switch 는 별도 제어 계층이다** — 에이전트가 수정할 수 없다 (killswitch.py).
`stop ≠ de-authorize`: 멈추는 것과 권한을 뺏는 것은 다른 행동이다.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dawn_core.eg.traverse import severity_band, severity_of

from .detect import Detection

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
RANK_SEVERITY = {v: k for k, v in SEVERITY_RANK.items()}

# 대응 플레이북 — 01_aoc_architecture §대응 플레이북
PLAYBOOKS = {
    "pause": {
        "name": "일시중지",
        "desc": "에이전트를 멈춘다. 권한은 그대로 (stop ≠ de-authorize).",
        "reversible": True,
    },
    "kill": {
        "name": "강제 종료",
        "desc": "실행 중단 + 자율화 A0 강등. 별도 제어 계층이 집행한다.",
        "reversible": False,
    },
    "revoke_credentials": {
        "name": "자격증명 회수",
        "desc": "이 에이전트의 자격증명을 무효화한다.",
        "reversible": False,
    },
    "block_tool": {
        "name": "도구 차단",
        "desc": "해당 도구를 이 에이전트에게서 뺏는다 (gate deny 추가 제안).",
        "reversible": True,
    },
    "isolate": {
        "name": "샌드박스 격리",
        "desc": "격리실로 이송. 외부 자산 접근 차단.",
        "reversible": True,
    },
    "rollback": {
        "name": "산출물 롤백",
        "desc": "이 실행이 만든 산출물을 되돌린다.",
        "reversible": True,
    },
    "escalate_hitl": {
        "name": "HITL 에스컬레이션",
        "desc": "사람 승인 큐로 올린다.",
        "reversible": True,
    },
    "report_regulator": {
        "name": "규제 보고",
        "desc": "EU AI Act 등 규제 보고 대상. 사람 전용.",
        "reversible": False,
    },
}


@dataclass
class Case:
    """관제 케이스 하나 — 탐지들을 묶은 단위."""

    id: str
    trace_id: str
    agent_id: str
    team: str = ""
    eg_org: str = ""
    zone: str = ""
    axis: str = "security"
    severity: str = "low"
    severity_score: int = 0
    severity_label: str = "낮음"
    title: str = ""
    detections: list[dict[str, Any]] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)
    status: str = "open"          # open | responding | closed
    opened_at: str = ""
    closed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def line(self) -> str:
        icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}[self.severity]
        return (
            f"{icon} {self.id}  [{self.axis}] {self.severity:<8} "
            f"{self.agent_id:<22} {self.title[:52]}"
        )


def asset_severity(eg_store, assets: list[str]) -> tuple[int, str]:
    """심각도 = 비가역성 × 보안등급 — EG 순회 (추측 없음)."""
    if eg_store is None or not assets:
        return 0, "낮음"
    worst = 0
    for aid in assets:
        if eg_store.node(aid) is None:
            continue
        worst = max(worst, severity_of(eg_store, aid).score)
    return worst, severity_band(worst)[0]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def triage(run, detections: list[Detection], *, eg_store=None) -> Case | None:
    """탐지들을 케이스로 접는다. 탐지가 없으면 케이스도 없다."""
    if not detections:
        return None

    worst = max(detections, key=lambda d: SEVERITY_RANK.get(d.severity, 0))
    score, label = asset_severity(eg_store, run.assets)
    # 자산 심각도가 높으면 탐지 심각도를 끌어올린다 (비가역 × 광역 = 최고)
    sev_rank = SEVERITY_RANK.get(worst.severity, 0)
    if score >= 6:
        sev_rank = max(sev_rank, 3)
    elif score >= 5:
        sev_rank = max(sev_rank, 2)

    c = Case(
        id=f"case-{uuid.uuid4().hex[:10]}",
        trace_id=run.trace_id,
        agent_id=run.agent_id,
        team=run.team,
        eg_org=run.eg_org,
        zone=run.zone,
        axis=worst.axis,
        severity=RANK_SEVERITY[sev_rank],
        severity_score=score,
        severity_label=label,
        title=worst.summary,
        detections=[d.to_dict() for d in detections],
        assets=list(run.assets),
        policies=list(run.policies),
        opened_at=_now(),
    )
    c.recommended = recommend(c, run)
    return c


def recommend(case: Case, run) -> list[str]:
    """이 케이스에 어떤 플레이북을 권고하나. **실행은 하지 않는다.**"""
    out: list[str] = []
    kinds = {d["kind"] for d in case.detections}

    if "data_leak" in kinds:
        out += ["pause", "rollback", "escalate_hitl"]
        if case.severity == "critical":
            out.append("report_regulator")
    if "prompt_injection" in kinds:
        out += ["pause", "escalate_hitl"]
    if "blocked_action" in kinds:
        out += ["block_tool", "escalate_hitl"]
    if kinds & {"step_explosion", "tool_loop", "long_running"}:
        out += ["pause"]
    if kinds & {"hallucination", "requirement_gap"}:
        out += ["rollback", "escalate_hitl"]
    if "goal_drift" in kinds:
        out += ["pause", "isolate", "escalate_hitl"]
    if "loop_violation" in kinds:
        out += ["escalate_hitl"]

    if case.severity == "critical":
        out += ["isolate", "kill"]
        if case.severity_score >= 6:
            out.append("revoke_credentials")

    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


class CaseStore:
    """케이스 저장소 — var/aoc/cases/<id>.json (append-only 상태 전이)."""

    def __init__(self, root: Path) -> None:
        self.dir = Path(root) / "var" / "aoc" / "cases"
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, case: Case) -> Path:
        p = self.dir / f"{case.id}.json"
        p.write_text(json.dumps(case.to_dict(), ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
        return p

    def get(self, cid: str) -> Case:
        p = self.dir / f"{cid}.json"
        if not p.is_file():
            raise KeyError(f"케이스 없음: {cid}")
        d = json.loads(p.read_text(encoding="utf-8"))
        return Case(**{k: v for k, v in d.items() if k in Case.__annotations__})

    def list(self, status: str | None = None) -> list[Case]:
        out = []
        for p in sorted(self.dir.glob("case-*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            c = Case(**{k: v for k, v in d.items() if k in Case.__annotations__})
            if status is None or c.status == status:
                out.append(c)
        return sorted(out, key=lambda c: (SEVERITY_RANK.get(c.severity, 0), c.opened_at),
                      reverse=True)

    def clear(self) -> int:
        n = 0
        for p in self.dir.glob("case-*.json"):
            p.unlink()
            n += 1
        return n
