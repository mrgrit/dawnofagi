"""HITL 승인 큐 — 사람이 에이전트에 개입하는 주 통로.

행동 게이트가 `require_hitl` / `block` 을 내면 여기에 항목이 쌓이고,
워커는 **대기하거나 중단한다**. 사람이 승인하기 전에는 진행하지 않는다.

P4 그룹웨어가 이 큐를 UI 로 노출한다 (P4 지시문 §6). 지금은 파일 큐 —
같은 데이터를 웹이 읽으면 된다.

파일 하나 = 요청 하나. 상태는 파일 내용으로만 바뀐다 (감사 추적).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUSES = ("pending", "approved", "denied", "expired")


@dataclass
class Approval:
    id: str
    agent_id: str
    skill: str
    decision: str  # require_hitl | block
    reasons: list[str]
    args: dict[str, Any]
    severity: int
    severity_label: str
    assets: list[str]
    policies: list[str]
    trace_id: str = ""
    status: str = "pending"
    requested_at: str = ""
    decided_at: str = ""
    decided_by: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Approval:
        known = {k: v for k, v in d.items() if k in cls.__annotations__}
        return cls(**known)

    def line(self) -> str:
        icon = {"pending": "⏳", "approved": "✔", "denied": "✘", "expired": "⌛"}[self.status]
        return (
            f"{icon} {self.id}  {self.agent_id}  {self.skill}  "
            f"[{self.severity_label}/{self.severity}]  {self.status}"
        )


class ApprovalQueue:
    """파일 기반 승인 큐. var/hitl/<id>.json"""

    def __init__(self, root: Path) -> None:
        self.dir = Path(root) / "var" / "hitl"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, aid: str) -> Path:
        return self.dir / f"{aid}.json"

    # ── 쓰기 ────────────────────────────────────────────────────────────
    def request(
        self, *, agent_id: str, skill: str, gate_decision, args: dict[str, Any], trace_id: str = ""
    ) -> Approval:
        ap = Approval(
            id=f"hitl-{uuid.uuid4().hex[:10]}",
            agent_id=agent_id,
            skill=skill,
            decision=gate_decision.decision,
            reasons=list(gate_decision.reasons),
            args={k: _short(v) for k, v in args.items()},
            severity=gate_decision.severity,
            severity_label=gate_decision.severity_label,
            assets=list(gate_decision.assets),
            policies=list(gate_decision.policies),
            trace_id=trace_id,
            requested_at=_now(),
        )
        self._path(ap.id).write_text(
            json.dumps(ap.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return ap

    def decide(self, aid: str, *, approve: bool, by: str = "human", note: str = "") -> Approval:
        ap = self.get(aid)
        if ap.status != "pending":
            raise ValueError(f"{aid} 는 이미 {ap.status} 다 — 재판정 불가 (감사 추적)")
        ap.status = "approved" if approve else "denied"
        ap.decided_at = _now()
        ap.decided_by = by
        ap.note = note
        self._path(aid).write_text(
            json.dumps(ap.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return ap

    # ── 읽기 ────────────────────────────────────────────────────────────
    def get(self, aid: str) -> Approval:
        p = self._path(aid)
        if not p.is_file():
            raise KeyError(f"승인 요청 없음: {aid}")
        return Approval.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def list(self, status: str | None = None) -> list[Approval]:
        out = []
        for p in sorted(self.dir.glob("hitl-*.json")):
            ap = Approval.from_dict(json.loads(p.read_text(encoding="utf-8")))
            if status is None or ap.status == status:
                out.append(ap)
        return sorted(out, key=lambda a: a.requested_at, reverse=True)

    def pending(self) -> list[Approval]:
        return self.list("pending")

    def clear(self) -> int:
        n = 0
        for p in self.dir.glob("hitl-*.json"):
            p.unlink()
            n += 1
        return n


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _short(v: Any, n: int = 200) -> Any:
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def auto_approve_enabled() -> bool:
    """데모·테스트용 자동 승인. 운영에서는 절대 켜지 않는다."""
    return os.getenv("DAWN_AUTO_APPROVE", "").lower() in ("1", "true", "yes")
