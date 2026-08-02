"""거버넌스·개선 계층 (AOC 5계층의 [4]).

에이전트 레지스트리 + KPI + 자율화 등급 관리.

KPI 는 COMPANY.md §3 의 목표와 1:1 대응한다 — 대시보드가 헌장과 다른 걸 재면
그 대시보드는 회사를 운영하는 데 못 쓴다.

    HITL 개입률      ↓   승인 요청 / 게이트 통과 시도
    오탐율           ↓   승인 큐에서 거부된 비율 (올릴 필요 없던 것)
    MTTD / MTTR      ↓   탐지까지 / 대응까지
    할루시네이션율   ↓   judge groundedness < 70 비율
    태스크 성공률    ↑   complete=True 비율
    가드레일 적중    —   차단·HITL 건수 (많다고 좋은 것도 나쁜 것도 아니다)

자율화 승급은 **KPI 충족 시에만** — 감(感)으로 올리지 않는다 (COMPANY.md §3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dawn_agents.hitl import ApprovalQueue
from dawn_core.gate import AUTONOMY_ORDER

from .triage import Case


@dataclass
class KPI:
    name: str
    value: float
    unit: str
    direction: str            # up | down
    target: float | None = None
    sample: int = 0
    note: str = ""

    @property
    def meets_target(self) -> bool | None:
        if self.target is None or self.sample == 0:
            return None
        return self.value <= self.target if self.direction == "down" else self.value >= self.target

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "meets_target": self.meets_target}

    def line(self) -> str:
        m = self.meets_target
        icon = "—" if m is None else ("✔" if m else "✘")
        tgt = f"  목표 {'≤' if self.direction == 'down' else '≥'}{self.target}{self.unit}" \
            if self.target is not None else ""
        return (f"  {icon} {self.name:<22} {self.value:>8.1f}{self.unit}{tgt}"
                f"   (n={self.sample})")


def compute(runs, cases: list[Case], queue: ApprovalQueue,
            judges: dict[str, Any] | None = None) -> list[KPI]:
    """실측치로 KPI 를 낸다. 표본이 없으면 0 이 아니라 n=0 으로 표시된다.

    **실업무(`purpose == "work"`) 만 센다.** 드릴·레드팀 run 은 일부러 차단되어
    ④ eg_record 에 도달하지 않으므로, 같이 집계하면 "레드팀이 잘 막혔다"가
    "일을 못 한다"로 뒤집혀 읽힌다 (실측: 미완 25건 중 21건이 레드팀 시도였다).
    목적이 안 붙은 옛 트레이스(`unknown`)도 제외하고, 몇 건을 뺐는지 표시한다 —
    조용히 빼면 그 자체가 거짓말이다.
    """
    judges = judges or {}
    approvals = queue.list()

    all_runs = list(runs)
    runs = [r for r in all_runs if r.purpose == "work"]
    excluded = len(all_runs) - len(runs)
    n = len(runs)
    drop_note = ""
    if excluded:
        kinds: dict[str, int] = {}
        for r in all_runs:
            if r.purpose != "work":
                kinds[r.purpose] = kinds.get(r.purpose, 0) + 1
        drop_note = "실업무 외 " + ", ".join(
            f"{k} {v}" for k, v in sorted(kinds.items())) + " 제외"

    # 태스크 성공률
    complete = sum(1 for r in runs if r.complete)
    success = KPI("태스크 성공률", 100.0 * complete / n if n else 0.0, "%", "up",
                  target=90.0, sample=n, note=drop_note)

    # HITL 개입률 — 게이트 판정 중 HITL/차단이 걸린 비율
    total_gates = sum(sum(r.gate_decisions.values()) for r in runs)
    hitl_gates = sum(
        r.gate_decisions.get("require_hitl", 0) + r.gate_decisions.get("block", 0)
        for r in runs
    )
    intervention = KPI("HITL 개입률", 100.0 * hitl_gates / total_gates if total_gates else 0.0,
                       "%", "down", target=10.0, sample=total_gates, note=drop_note)

    # 오탐율 — 사람이 거부한 승인 요청 = 올릴 필요 없던 것.
    # 드릴이 올린 승인은 **일부러 거부하는 것**이라 같이 세면 오탐율이 부풀려진다.
    work_traces = {r.trace_id for r in runs}
    decided = [
        a for a in approvals
        if a.status in ("approved", "denied") and (not a.trace_id or a.trace_id in work_traces)
    ]
    denied = sum(1 for a in decided if a.status == "denied")
    false_pos = KPI("오탐율(승인 거부)", 100.0 * denied / len(decided) if decided else 0.0,
                    "%", "down", target=5.0, sample=len(decided),
                    note="실업무 run 의 승인만" if excluded else "")

    # 할루시네이션율
    jr = [v for v in judges.values() if getattr(v, "verdict", "unknown") != "unknown"]
    halluc_n = sum(1 for v in jr if v.groundedness < 70)
    halluc = KPI("할루시네이션율", 100.0 * halluc_n / len(jr) if jr else 0.0, "%", "down",
                 target=2.0, sample=len(jr))

    # MTTD — 실행 시작에서 탐지까지. 우리 파이프라인은 배치라 run 종료 시점이 하한.
    mttd_vals = [r.duration_ms / 1000 for r in runs if any(c.trace_id == r.trace_id for c in cases)]
    mttd = KPI("MTTD(탐지까지)", sum(mttd_vals) / len(mttd_vals) if mttd_vals else 0.0,
               "s", "down", target=300.0, sample=len(mttd_vals), note=drop_note)

    # MTTR — 케이스 개시에서 대응 액션까지
    mttr_vals: list[float] = []
    for c in cases:
        if not c.actions or not c.opened_at:
            continue
        try:
            t0 = datetime.fromisoformat(c.opened_at)
            ts = [datetime.fromisoformat(a["at"]) for a in c.actions if a.get("at")]
            if ts:
                mttr_vals.append((min(ts) - t0).total_seconds())
        except ValueError:
            continue
    mttr = KPI("MTTR(대응까지)", sum(mttr_vals) / len(mttr_vals) if mttr_vals else 0.0,
               "s", "down", target=1800.0, sample=len(mttr_vals))

    # 탐지·대응 지표는 **드릴을 포함해서** 센다 — 드릴은 탐지력을 재는 정당한 표본이다.
    guard = KPI("가드레일 적중", float(sum(len(c.detections) for c in cases)), "건", "down",
                sample=len(cases),
                note="드릴 포함 — 많고 적음보다 미탐이 0 인지가 중요하다")

    return [success, intervention, false_pos, halluc, mttd, mttr, guard]


# ── 자율화 등급 관리 ─────────────────────────────────────────────────────

PROMOTION_RULES = {
    # 현재 등급 → (다음 등급, 필요 조건)
    "A0": ("A1", {"태스크 성공률": (">=", 80.0)}),
    "A1": ("A2", {"오탐율(승인 거부)": ("<=", 5.0), "태스크 성공률": (">=", 90.0)}),
    "A2": ("A3", {"HITL 개입률": ("<=", 2.0), "할루시네이션율": ("<=", 2.0)}),
}


@dataclass
class AutonomyReview:
    agent_id: str
    current: str
    proposed: str = ""
    eligible: bool = False
    reasons: list[str] = field(default_factory=list)
    demotion: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def line(self) -> str:
        if self.demotion:
            return f"  ⬇ {self.agent_id:<24} {self.current} → {self.proposed}  ({'; '.join(self.reasons)})"
        if self.eligible:
            return f"  ⬆ {self.agent_id:<24} {self.current} → {self.proposed}  승급 조건 충족"
        return f"  = {self.agent_id:<24} {self.current}  ({'; '.join(self.reasons) or '유지'})"


def review_autonomy(agent_id: str, current: str, kpis: list[KPI],
                    cases: list[Case]) -> AutonomyReview:
    """승급은 KPI 충족 시에만. **강등은 인시던트 발생 즉시 자동** (COMPANY.md §6.4)."""
    rv = AutonomyReview(agent_id=agent_id, current=current)

    # 강등 먼저 — critical 인시던트가 있으면 무조건 A0
    crit = [c for c in cases if c.agent_id == agent_id and c.severity == "critical"]
    if crit:
        rv.demotion = True
        rv.proposed = "A0"
        rv.reasons = [f"critical 인시던트 {len(crit)}건 — 즉시 강등 (case {crit[0].id})"]
        return rv

    rule = PROMOTION_RULES.get(current)
    if rule is None:
        rv.reasons = ["최고 등급"]
        return rv
    nxt, conds = rule
    by_name = {k.name: k for k in kpis}
    unmet: list[str] = []
    for name, (op, target) in conds.items():
        k = by_name.get(name)
        if k is None or k.sample == 0:
            unmet.append(f"{name}: 표본 없음")
            continue
        ok = k.value <= target if op == "<=" else k.value >= target
        if not ok:
            unmet.append(f"{name} {k.value:.1f}{k.unit} ({op}{target} 필요)")
    if unmet:
        rv.reasons = unmet
        return rv
    rv.eligible = True
    rv.proposed = nxt
    return rv


def registry_view(registry, eg_store, runs, cases: list[Case],
                  killswitch) -> list[dict[str, Any]]:
    """에이전트 레지스트리 — EG OrgUnit/Asset 과 실측 활동을 합친 것."""
    out = []
    for aid, agent in sorted(registry.agents.items()):
        team = registry.teams[agent.team_id]
        mine = [r for r in runs if r.agent_id == aid]
        my_cases = [c for c in cases if c.agent_id == aid]
        st = killswitch.get(aid)
        out.append({
            "agent_id": aid,
            "name": agent.data["name"],
            "team": agent.team_id,
            "division": team.data["division"],
            "eg_org": team.data.get("eg_org", ""),
            "persona": agent.data["persona"],
            "autonomy": st.autonomy_override or agent.data["autonomy"],
            "autonomy_declared": agent.data["autonomy"],
            "zone": agent.data.get("zone") or team.data.get("zone", ""),
            "status": agent.data["status"],
            "control_state": st.state,
            "credentials_revoked": st.credentials_revoked,
            "blocked_tools": st.blocked_tools,
            "runs": len(mine),
            "complete": sum(1 for r in mine if r.complete),
            "tokens": sum(r.tokens for r in mine),
            "cases": len(my_cases),
            "worst_case": max((c.severity for c in my_cases),
                              key=lambda s: ["low", "medium", "high", "critical"].index(s),
                              default=""),
            "last_model": next((r.model for r in mine if r.model), ""),
            "last_seen_ns": max((r.started_ns for r in mine), default=0),
        })
    return out


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "AUTONOMY_ORDER",
    "KPI",
    "PROMOTION_RULES",
    "AutonomyReview",
    "compute",
    "now_iso",
    "registry_view",
    "review_autonomy",
]
