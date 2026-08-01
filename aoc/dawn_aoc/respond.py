"""대응 실행기 (AOC 5계층의 [3] 뒷단).

트리아지가 **권고**한 플레이북을 실제로 집행한다.
권고와 집행을 나눈 이유: 비가역 액션(kill·자격증명 회수·규제 보고)은
사람 승인 없이 돌면 안 된다. 여기서 다시 게이트를 건다.

    reversible 액션   (pause · isolate · block_tool · rollback · escalate_hitl)
        → 자동 집행. 되돌릴 수 있으므로 관제가 즉시 실행한다.
    irreversible 액션 (kill · revoke_credentials · report_regulator)
        → **HITL 승인 큐로.** 사람이 누르기 전에는 집행하지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dawn_agents.hitl import ApprovalQueue
from dawn_core import jsonl

from .killswitch import KillSwitch
from .triage import PLAYBOOKS, Case


@dataclass
class ActionResult:
    playbook: str
    executed: bool
    reason: str = ""
    hitl_id: str = ""
    detail: str = ""
    at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def line(self) -> str:
        icon = "✔" if self.executed else ("✋" if self.hitl_id else "○")
        name = PLAYBOOKS.get(self.playbook, {}).get("name", self.playbook)
        tail = f"  → {self.hitl_id}" if self.hitl_id else (f"  {self.detail}" if self.detail else "")
        return f"  {icon} {self.playbook:<20} {name}{tail}"


class _GateShim:
    """HITL 큐가 기대하는 최소 인터페이스 (대응 액션용)."""

    def __init__(self, case: Case, playbook: str) -> None:
        self.decision = "require_hitl"
        self.reasons = [
            f"비가역 대응 액션 — {PLAYBOOKS[playbook]['desc']}",
            f"케이스 {case.id}: {case.title}",
        ]
        self.severity = case.severity_score
        self.severity_label = case.severity_label
        self.assets = case.assets
        self.policies = case.policies


class Responder:
    """대응 플레이북 집행기."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.ks = KillSwitch(root)
        self.queue = ApprovalQueue(root)
        self.log_path = self.root / "var" / "aoc" / "responses.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def execute(self, case: Case, playbooks: list[str] | None = None,
                *, by: str = "aoc") -> list[ActionResult]:
        """권고된 플레이북을 집행한다. 비가역은 승인 큐로 보낸다."""
        results: list[ActionResult] = []
        for pb in playbooks or case.recommended:
            spec = PLAYBOOKS.get(pb)
            if spec is None:
                results.append(ActionResult(pb, False, reason="알 수 없는 플레이북"))
                continue

            if not spec["reversible"]:
                ap = self.queue.request(
                    agent_id=case.agent_id, skill=f"aoc.{pb}",
                    gate_decision=_GateShim(case, pb), args={"case_id": case.id},
                    trace_id=case.trace_id,
                )
                results.append(ActionResult(
                    pb, False, reason="비가역 — 사람 승인 전에는 집행하지 않는다",
                    hitl_id=ap.id, at=_now(),
                ))
                continue

            results.append(self._run_reversible(case, pb, by))

        case.actions.extend(r.to_dict() for r in results)
        case.status = "responding" if any(r.executed or r.hitl_id for r in results) else case.status
        self._log(case, results)
        return results

    def _run_reversible(self, case: Case, pb: str, by: str) -> ActionResult:
        reason = f"[{case.id}] {case.title[:80]}"
        try:
            if pb == "pause":
                st = self.ks.pause(case.agent_id, reason=reason, by=by, case_id=case.id)
                return ActionResult(pb, True, detail=f"상태 {st.state} (권한은 유지)", at=_now())
            if pb == "isolate":
                st = self.ks.isolate(case.agent_id, reason=reason, by=by, case_id=case.id)
                return ActionResult(pb, True, detail="격리실 이송", at=_now())
            if pb == "block_tool":
                # 탐지가 명시한 대상만 차단한다. 요약문을 파싱하지 않는다.
                tools = sorted({
                    d.get("subject", "") for d in case.detections
                    if d.get("kind") == "blocked_action" and d.get("subject")
                })
                for t in tools:
                    self.ks.block_tool(case.agent_id, t, reason=reason, by=by, case_id=case.id)
                return ActionResult(pb, bool(tools),
                                    detail=f"차단: {', '.join(tools) or '(대상 없음)'}", at=_now())
            if pb == "rollback":
                n = self._rollback(case)
                return ActionResult(pb, True, detail=f"산출물 {n}건 격리 보관", at=_now())
            if pb == "escalate_hitl":
                ap = self.queue.request(
                    agent_id=case.agent_id, skill=f"aoc.{pb}",
                    gate_decision=_GateShim(case, pb), args={"case_id": case.id},
                    trace_id=case.trace_id,
                )
                return ActionResult(pb, True, hitl_id=ap.id, detail="승인 큐로", at=_now())
        except Exception as exc:
            return ActionResult(pb, False, reason=f"{type(exc).__name__}: {exc}", at=_now())
        return ActionResult(pb, False, reason="집행부 없음", at=_now())

    def _rollback(self, case: Case) -> int:
        """이 실행이 만든 산출물을 격리 보관한다 (지우지 않는다 — 증거다)."""
        src = self.root / "var" / "demo" / "drafts"
        dst = self.root / "var" / "aoc" / "quarantine" / case.id
        if not src.is_dir():
            return 0
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in src.glob("*"):
            if f.is_file():
                f.rename(dst / f.name)
                n += 1
        return n

    def _log(self, case: Case, results: list[ActionResult]) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "at": _now(), "case_id": case.id, "agent_id": case.agent_id,
                "severity": case.severity,
                "actions": [r.to_dict() for r in results],
            }, ensure_ascii=False) + "\n")

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.log_path.is_file():
            return []
        return jsonl.read(self.log_path, limit=limit)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
