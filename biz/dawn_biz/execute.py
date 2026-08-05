"""작업 지시 착수와 단계별 검수 (P7 DoD-5).

    착수 → 단계 산출물 → 검수 게이트 → 다음 단계

**실행기를 새로 만들지 않는다.** 편성된 에이전트를 P2 오케스트레이터에 넘긴다 —
`phase`/`depends_on` 위상 정렬과 자산 분리 검사가 이미 거기 있다. 여기서 하는 일은
**배치를 만들고 산출물을 검수하는 것**뿐이다.

## 검수는 세 겹이다

한 겹만 두면 각각의 구멍이 그대로 통과한다:

1. **기계** — 루프를 지켰나, 게이트에 막힌 게 있나, 산출물이 비었나.
   모델 없이 판정된다. 여기서 걸리면 나머지를 볼 필요도 없다.
2. **judge** — 근거·완결성·경로 (P3 품질 축 그대로). 게이트가 아무것도 안 막아도
   **조용히 잘못할 수 있다** — 그걸 잡는 겹이다. 모델(GPU)이 필요하다.
3. **사람** — 위험도가 높거나 judge 가 fail 이면 결재 라인으로 올린다.

**검수를 통과 못 한 산출물로 다음 단계가 시작되지 않는다.** 그게 이 모듈의 존재
이유다. 통과 못 했는데 진행하면 단계 구분이 장식이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 기계 검수 — 무엇을 보는가. SOP 본문 파싱은 하지 않는다(문서 형식에 묶이면 깨진다).
# 대신 **워커 루프가 남긴 사실**만 본다.
MACHINE_CHECKS = (
    ("loop_start", "① eg_search 로 시작했나 (근거 없이 착수하지 않았나)"),
    ("loop_end", "④ eg_record 로 마쳤나 (기록 없이 끝내지 않았나)"),
    ("not_blocked", "게이트에 막힌 도구 호출이 없나"),
    ("has_output", "산출물이 비어 있지 않나"),
)


@dataclass
class Verdict:
    """단계 하나의 검수 결과."""

    agent_id: str
    trace_id: str = ""
    machine: dict[str, bool] = field(default_factory=dict)
    quality: dict[str, Any] | None = None      # judge 3축 (없으면 미판정)
    needs_human: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def machine_ok(self) -> bool:
        return all(self.machine.values()) if self.machine else False

    @property
    def passed(self) -> bool:
        """다음 단계로 갈 수 있나. **사람 승인이 걸리면 아직 아니다.**"""
        if not self.machine_ok or self.needs_human:
            return False
        if self.quality is None:
            return True                         # 판정 못 했으면 막지 않는다 (미판정 ≠ 실패)
        return self.quality.get("verdict") != "fail"

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "machine_ok": self.machine_ok, "passed": self.passed}

    def line(self) -> str:
        icon = "✔" if self.passed else ("✋" if self.needs_human else "✘")
        q = ""
        if self.quality:
            q = ("  근거 {groundedness} 완결 {completeness} 경로 {trajectory} "
                 "{verdict}").format(**{**{"groundedness": 0, "completeness": 0,
                                           "trajectory": 0, "verdict": "?"},
                                        **self.quality})
        return f"  {icon} {self.agent_id:<22}{q}  {'; '.join(self.reasons)}"


def machine_review(run) -> dict[str, bool]:
    """모델 없이 판정되는 것들. 실행이 **남긴 사실**만 본다.

    두 가지 모양을 다 받는다 — 실행 직후의 `WorkerRun`(steps 를 갖는다)과
    사후 정규화된 `dawn_aoc.collect.Run`(tools_used 를 갖는다). 같은 실행을
    보는 두 시점이라 판정이 달라지면 안 된다.
    """
    steps = list(getattr(run, "steps", []) or [])
    if steps and hasattr(steps[0], "kind"):
        searched = any(s.kind == "eg_search" for s in steps)
    else:
        searched = "eg.search" in list(getattr(run, "tools_used", []) or [])
    return {
        "loop_start": searched,
        "loop_end": bool(getattr(run, "complete", False)),
        "not_blocked": not list(getattr(run, "blocked", []) or []),
        "has_output": bool(str(getattr(run, "output", "") or "").strip()),
    }


def review(run, *, eg_store=None, with_judge: bool = True,
           high_risk: bool = False) -> Verdict:
    """단계 하나를 검수한다. 세 겹 전부.

    Args:
        with_judge: 모델을 부를 것인가. GPU 가 없으면 False 로 두면 기계 검수만 한다.
        high_risk: 이 단계가 비가역·L3 를 건드리나 → 통과해도 사람이 본다.
    """
    v = Verdict(agent_id=getattr(run, "agent_id", ""),
                trace_id=getattr(run, "trace_id", ""))
    v.machine = machine_review(run)
    for key, desc in MACHINE_CHECKS:
        if not v.machine.get(key):
            v.reasons.append(desc)

    if not v.machine_ok:
        return v                                # 기계에서 걸리면 모델을 부를 이유가 없다

    if with_judge:
        from dawn_aoc.detect import judge

        # run 이 L3 를 건드렸는지 그대로 넘긴다. 없으면 True(보수적) —
        # 모르는데 클라우드로 보내는 것보다 느린 편이 낫다.
        jr = judge(getattr(run, "task", ""), str(getattr(run, "output", "")),
                   watched_policy_id=getattr(run, "model_policy", ""), eg_store=eg_store,
                   touches_l3=bool(getattr(run, "touches_l3", True)))
        if jr.error or jr.verdict == "unknown":
            v.reasons.append(f"품질 판정 불가 — {jr.error or 'JSON 파싱 실패'}")
        else:
            v.quality = jr.to_dict()
            if jr.failed:
                v.reasons.append("품질 판정 fail — " + "; ".join(jr.issues[:2]))

    # 사람이 봐야 하는 경우: 위험한 단계이거나, 품질이 떨어졌거나.
    if high_risk:
        v.needs_human = True
        v.reasons.append("비가역·L3 단계 — 사람 확인 필요")
    elif v.quality and v.quality.get("verdict") == "fail":
        v.needs_human = True

    return v


def stage_plan(root: Path, *, order_id: int) -> list[dict[str, Any]]:
    """편성된 에이전트를 오케스트레이터 배치로 편다.

    hand-off 냐 병렬이냐는 **매니페스트가 정한다** — SOUL 에 적힌 `phase`/`depends_on`
    을 그대로 읽는다. 여기서 새로 판단하지 않는다.
    """
    import yaml
    from dawn_core.crew import formed

    out = []
    for aid in formed(root, order_id=order_id):
        f = Path(root) / "org" / "agents" / aid / "agent.yaml"
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        out.append({
            "agent_id": aid,
            "team": doc.get("team", ""),
            "works": list(doc.get("works") or []),
            "tools": list(doc.get("tools") or []),
            "phase": str(doc.get("phase") or "P1"),
            "depends_on": list(doc.get("depends_on") or []),
        })
    # **위상 순으로 편다.** formed() 는 파일 이름 순이라 그대로 두면 P3 검증자가
    # P2 구현자보다 먼저 돈다 — 아직 없는 산출물을 검증하는 셈이다.
    out.sort(key=_phase_key)
    return out


def _phase_key(stage: dict[str, Any]) -> tuple[int, str]:
    """정렬 키 — 위상 숫자, 그 안에서는 이름.

    같은 위상은 서로 기다리지 않는다(설계상 병렬). 지금 집행부는 순차로 돌지만
    순서가 결과를 바꾸지 않아야 하므로 이름으로 고정한다 — 실행마다 순서가
    달라지면 재현이 안 된다.
    """
    raw = str(stage.get("phase") or "P1")
    try:
        n = int(raw.lstrip("Pp") or 1)
    except ValueError:
        n = 1
    return (n, stage.get("agent_id", ""))


def can_start(store, order_id: int) -> tuple[bool, str]:
    """착수해도 되는가. **승인·편성이 끝나야 한다.**"""
    from dawn_core.crew import formed
    from dawn_core.paths import Paths

    r = store.work_order(order_id)
    if r is None:
        return False, f"작업 지시 없음: {order_id}"
    if r["status"] not in ("approved", "provisioning", "in_progress"):
        return False, f"결재가 끝나지 않았다 (지금 {r['status']})"

    # 환경이 필요한 작업인데 아직 안 잡혔으면 착수하지 않는다. 착수시켜 놓고
    # 자원이 없어 실패하면 그건 작업의 실패로 기록돼 KPI 가 거짓말을 한다.
    if r["infra_tier"] != "none":
        from dawn_core.infrapool import allocation_of

        a = allocation_of(Paths().root, order_id)
        if a is None or a.state != "ready":
            why = a.reason if a else "할당 이력이 없다"
            return False, f"인프라가 준비되지 않았다 ({r['infra_tier']}) — {why}"

    if not formed(Paths().root, order_id=order_id):
        return False, "편성된 에이전트가 없다 — 먼저 편성하라"
    return True, ""


__all__ = ["MACHINE_CHECKS", "Verdict", "can_start", "machine_review", "review",
           "stage_plan"]
