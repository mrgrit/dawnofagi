"""팀 오케스트레이터 — 부서 리더가 워커에게 위임한다.

AGENT_TEAM.md(L2)의 규칙을 실행 형태로 옮긴 것:

  · **리더는 무발화** — 분석 본문을 직접 쓰지 않는다. 라우팅·통합만 한다.
    (bastion `BASTION.md` 의 soc-lead 규칙을 전사로 일반화)
  · **검증자 ≠ 생산자** — 같은 에이전트가 자기 산출물을 통과시키지 못한다.
  · **phase / depends_on 으로 직렬화** — 같은 자산을 두 워커가 동시에 못 건드린다.

부서 **내부** 위임은 여기(CC Subagent 패턴). 부서 **간** 협업은 LangGraph 상태머신이
맡을 자리인데, P2 에서는 부서 간 핸드오프가 아직 없으므로 인터페이스만 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dawn_core import Registry

from .telemetry import OP_INVOKE_AGENT, Tracer, get_tracer
from .worker import Worker, WorkerRun


class OrchestratorError(Exception):
    pass


@dataclass
class Assignment:
    """워커 한 명에게 주는 일감."""

    agent_id: str
    task: str
    phase: str = "P1"
    depends_on: list[str] = field(default_factory=list)
    skills: list[tuple[str, dict]] = field(default_factory=list)
    role: str = "worker"  # worker | verifier
    touches_l3: bool = False


@dataclass
class TeamRun:
    team_id: str
    goal: str
    trace_id: str = ""
    runs: dict[str, WorkerRun] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    verification: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return bool(self.runs) and all(r.complete for r in self.runs.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team_id,
            "goal": self.goal,
            "trace_id": self.trace_id,
            "order": self.order,
            "skipped": self.skipped,
            "verification": self.verification,
            "complete": self.complete,
            "runs": {k: v.to_dict() for k, v in self.runs.items()},
        }


class TeamOrchestrator:
    """부서 하나의 오케스트레이터. **본문을 쓰지 않는다.**"""

    def __init__(
        self, team_id: str, *, registry: Registry | None = None, tracer: Tracer | None = None
    ) -> None:
        self.registry = registry or Registry.load()
        if team_id not in self.registry.teams:
            raise OrchestratorError(f"팀 없음: {team_id}")
        self.team_id = team_id
        self.team = self.registry.teams[team_id]
        self.tracer = tracer or get_tracer(self.registry.paths.root)
        self._workers: dict[str, Worker] = {}

    def worker(self, agent_id: str) -> Worker:
        if agent_id not in self._workers:
            if agent_id not in self.team.agent_ids:
                raise OrchestratorError(
                    f"{agent_id} 는 {self.team_id} 소속이 아니다 — 위임할 수 없다"
                )
            self._workers[agent_id] = Worker(agent_id, registry=self.registry, tracer=self.tracer)
        return self._workers[agent_id]

    # ── 위상 정렬 (phase / depends_on) ──────────────────────────────────
    @staticmethod
    def _order(assignments: list[Assignment]) -> list[Assignment]:
        by_id = {a.agent_id: a for a in assignments}
        done: set[str] = set()
        out: list[Assignment] = []
        remaining = list(assignments)
        guard = 0
        while remaining:
            guard += 1
            if guard > len(assignments) * len(assignments) + 10:
                raise OrchestratorError(f"의존 순환 — 남은 것: {[a.agent_id for a in remaining]}")
            progressed = False
            for a in list(remaining):
                if all(d in done or d not in by_id for d in a.depends_on):
                    out.append(a)
                    done.add(a.agent_id)
                    remaining.remove(a)
                    progressed = True
            if not progressed:
                raise OrchestratorError(f"의존 해소 불가 — {[a.agent_id for a in remaining]}")
        return sorted(out, key=lambda a: a.phase)

    # ── 실행 ────────────────────────────────────────────────────────────
    def delegate(self, goal: str, assignments: list[Assignment]) -> TeamRun:
        """리더가 워커들에게 위임한다. 리더 자신은 본문을 만들지 않는다."""
        tr = TeamRun(team_id=self.team_id, goal=goal)
        ordered = self._order(assignments)

        with self.tracer.span(
            OP_INVOKE_AGENT,
            **{
                "gen_ai.operation.name": OP_INVOKE_AGENT,
                "gen_ai.agent.name": f"orchestrator:{self.team_id}",
                "dawn.team": self.team_id,
                "dawn.orchestrator": True,
                "dawn.orchestrator.silent": True,  # 리더 무발화
                "dawn.assignments": len(ordered),
            },
        ) as root:
            tr.trace_id = root.trace_id
            for a in ordered:
                # 선행 작업이 실패했으면 건너뛴다 (같은 자산 동시 변경 방지)
                failed_deps = [d for d in a.depends_on if d in tr.runs and not tr.runs[d].complete]
                if failed_deps:
                    tr.skipped.append(a.agent_id)
                    root.event(
                        "skipped", agent=a.agent_id, reason=f"선행 실패: {', '.join(failed_deps)}"
                    )
                    continue

                w = self.worker(a.agent_id)
                task = a.task
                # 검증자는 생산자의 산출물을 입력으로 받는다
                if a.role == "verifier":
                    produced = [
                        f"### {aid} 의 산출물\n{r.output[:2500]}"
                        for aid, r in tr.runs.items()
                        if r.output
                    ]
                    if not produced:
                        tr.skipped.append(a.agent_id)
                        root.event("skipped", agent=a.agent_id, reason="검증할 산출물 없음")
                        continue
                    task = (
                        f"{a.task}\n\n다음 산출물을 **반증**하라. "
                        f"근거 없는 단정, 빠진 증거, 틀린 판정을 찾아라.\n\n"
                        + "\n\n".join(produced)
                    )

                run = w.run(task, touches_l3=a.touches_l3, extra_skills=a.skills)
                tr.runs[a.agent_id] = run
                tr.order.append(a.agent_id)
                if a.role == "verifier":
                    tr.verification[a.agent_id] = run.output[:1000]

            root.set(**{"dawn.team.complete": tr.complete, "dawn.team.skipped": len(tr.skipped)})
        return tr

    # ── 검증자 ≠ 생산자 (구조적 강제) ───────────────────────────────────
    @staticmethod
    def check_separation(assignments: list[Assignment]) -> list[str]:
        """같은 에이전트가 생산자이자 검증자면 위반이다."""
        producers = {a.agent_id for a in assignments if a.role == "worker"}
        verifiers = {a.agent_id for a in assignments if a.role == "verifier"}
        return sorted(producers & verifiers)
