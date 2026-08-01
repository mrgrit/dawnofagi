"""Control Readiness Score — 통제 평면 성숙도 점검.

loop-engineering 의 *Loop Ready Score* 를 이 회사에 맞춘 것.
"통제되지 않는 에이전트는 배포하지 않는다"를 CI에서 기계적으로 강제한다.

    문서 존재   25점  모든 활성 에이전트가 L1~L4 4계층을 갖췄나
    경계 정의   25점  gate 의 tools/autonomy/budget 이 명시됐나 (기본값 의존 아님)
    거버넌스    20점  HITL 조건·에스컬레이션·자율화 등급이 각 팀에 있나
    루프 무결성 20점  각 *_WORK.md 에 트리거·DoD·실패처리가 있나
    관측성      10점  텔레메트리 방출·관제 연동이 선언됐나

기본 합격선 80점. CI 는 이 점수와 컴파일 실패 0건을 함께 본다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .control_plane import compile_all
from .registry import Registry

PASS_THRESHOLD = 80

# 각 *_WORK.md 가 갖춰야 할 절 (loop-engineering: 루프는 시작·끝·실패가 정의돼야 산다)
WORK_REQUIRED_SECTIONS = {
    "트리거": re.compile(r"^##\s*\d*\.?\s*트리거", re.MULTILINE),
    "절차": re.compile(r"^##\s*\d*\.?\s*절차", re.MULTILINE),
    "완료 조건": re.compile(r"^##\s*\d*\.?\s*완료\s*조건", re.MULTILINE),
    "실패 시 처리": re.compile(r"^##\s*\d*\.?\s*실패", re.MULTILINE),
}


@dataclass
class Category:
    name: str
    max_points: int
    earned: float = 0.0
    findings: list[str] = field(default_factory=list)

    @property
    def pct(self) -> float:
        return 100.0 * self.earned / self.max_points if self.max_points else 100.0


@dataclass
class Report:
    categories: list[Category]
    compile_failures: dict[str, str]
    warnings: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        return round(sum(c.earned for c in self.categories))

    @property
    def passed(self) -> bool:
        return self.score >= PASS_THRESHOLD and not self.compile_failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "threshold": PASS_THRESHOLD,
            "passed": self.passed,
            "categories": [
                {
                    "name": c.name,
                    "earned": round(c.earned, 1),
                    "max": c.max_points,
                    "findings": c.findings,
                }
                for c in self.categories
            ],
            "compile_failures": self.compile_failures,
            "warnings": self.warnings,
        }


def _ratio(ok: int, total: int) -> float:
    return 1.0 if total == 0 else ok / total


def run(registry: Registry) -> Report:
    compiled, failures = compile_all(registry)
    active = [a for a in registry.agents.values() if a.is_active]
    warnings: list[str] = []
    for c in compiled.values():
        warnings.extend(f"[{c.agent_id}] {w}" for w in c.warnings)

    # ── 1. 문서 존재 (25) ───────────────────────────────────────────────
    docs = Category("문서 존재 (L1~L4)", 25)
    if not active:
        docs.earned = 0
        docs.findings.append("활성 에이전트가 하나도 없다 — 통제할 대상이 없음")
    else:
        ok = 0
        for a in active:
            if a.id in compiled:
                ok += 1
            else:
                docs.findings.append(f"{a.id}: 컴파일 실패 (4계층 미완)")
        docs.earned = 25 * _ratio(ok, len(active))
    if registry.paths.company_md.is_file():
        pass
    else:
        docs.findings.append("COMPANY.md 없음 — 전 계층 무효")
        docs.earned = 0

    # ── 2. 경계 정의 (25) ──────────────────────────────────────────────
    bounds = Category("경계 정의 (gate.yaml)", 25)
    if compiled:
        per = 25 / len(compiled)
        for aid, c in compiled.items():
            got = 0
            checks = {
                "tools.allow": bool(c.gate.allow),
                "tools.deny": bool(c.gate.deny),
                "budget": len(c.gate.budget) >= 2,
                "hitl.require_on": bool(c.gate.hitl_require_on),
                "model.policy": bool(c.gate.model_policy),
            }
            for label, passed in checks.items():
                if passed:
                    got += 1
                else:
                    bounds.findings.append(f"{aid}: {label} 미정의")
            bounds.earned += per * got / len(checks)
    else:
        bounds.findings.append("컴파일된 에이전트 없음")

    # ── 3. 거버넌스 (20) ───────────────────────────────────────────────
    gov = Category("거버넌스 (HITL·에스컬레이션·자율화)", 20)
    teams_with_agents = [t for t in registry.teams.values() if t.agent_ids]
    if teams_with_agents:
        per = 20 / len(teams_with_agents)
        for t in teams_with_agents:
            got, total = 0, 3
            team_md = t.dir / "AGENT_TEAM.md"
            text = team_md.read_text(encoding="utf-8") if team_md.is_file() else ""
            if re.search(r"에스컬레이션", text):
                got += 1
            else:
                gov.findings.append(f"{t.id}: AGENT_TEAM.md 에 에스컬레이션 경로 없음")
            if (t.dir / "gate.yaml").is_file():
                got += 1
            else:
                gov.findings.append(f"{t.id}: 팀 gate.yaml 없음 (전사 기본값에만 의존)")
            div = registry.divisions.get(t.division_id)
            if div and div.data.get("autonomy_default"):
                got += 1
            else:
                gov.findings.append(f"{t.division_id}: 본부 autonomy_default 미지정")
            gov.earned += per * got / total
    else:
        gov.findings.append("에이전트를 가진 팀이 없다")

    # ── 4. 루프 무결성 (20) ────────────────────────────────────────────
    loop = Category("루프 무결성 (*_WORK.md)", 20)
    referenced = {w for a in active for w in a.work_ids}
    works = [registry.works[w] for w in sorted(referenced) if w in registry.works]
    if works:
        per = 20 / len(works)
        for w in works:
            got = 0
            for label, pattern in WORK_REQUIRED_SECTIONS.items():
                if pattern.search(w.body):
                    got += 1
                else:
                    loop.findings.append(f"{w.id}: '{label}' 절 없음")
            loop.earned += per * got / len(WORK_REQUIRED_SECTIONS)
    else:
        loop.findings.append("활성 에이전트가 참조하는 업무 문서가 없다")

    # ── 5. 관측성 (10) ─────────────────────────────────────────────────
    obs = Category("관측성 (텔레메트리)", 10)
    if compiled:
        per = 10 / len(compiled)
        for aid, c in compiled.items():
            got, total = 0, 2
            if c.gate.telemetry.get("emit"):
                got += 1
            else:
                obs.findings.append(f"{aid}: gate telemetry.emit 미설정")
            if (registry.agents[aid].data.get("telemetry") or {}).get("service_name"):
                got += 1
            else:
                obs.findings.append(f"{aid}: 매니페스트 telemetry.service_name 없음")
            obs.earned += per * got / total
    else:
        obs.findings.append("컴파일된 에이전트 없음")

    return Report([docs, bounds, gov, loop, obs], failures, warnings)


def format_report(rep: Report) -> str:
    bar_w = 24
    lines = ["", "  Control Readiness Score", "  " + "─" * 46]
    for c in rep.categories:
        filled = int(bar_w * c.pct / 100)
        bar = "█" * filled + "░" * (bar_w - filled)
        lines.append(f"  {bar} {c.earned:5.1f}/{c.max_points:<3d} {c.name}")
    lines.append("  " + "─" * 46)
    verdict = "PASS" if rep.passed else "FAIL"
    lines.append(f"  총점 {rep.score:3d}/100   (합격선 {PASS_THRESHOLD})   → {verdict}")
    lines.append("")

    findings = [(c.name, f) for c in rep.categories for f in c.findings]
    if findings:
        lines.append("  개선 필요:")
        for name, f in findings:
            lines.append(f"    · [{name}] {f}")
        lines.append("")
    if rep.warnings:
        lines.append("  경고:")
        for w in rep.warnings:
            lines.append(f"    ! {w}")
        lines.append("")
    if rep.compile_failures:
        lines.append("  컴파일 실패 (기동 불가):")
        for aid, msg in rep.compile_failures.items():
            lines.append(f"    ✗ {aid}")
            for ln in msg.splitlines()[1:]:
                lines.append(f"      {ln}")
        lines.append("")
    return "\n".join(lines)
