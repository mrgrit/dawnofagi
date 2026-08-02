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
    # 이 요청이 **실업무인가 훈련인가.** run 과 같은 값을 쓴다 (RUN_PURPOSES).
    # 없으면 KPI 때와 같은 오염이 난다 — 실측으로 대기 2633건 중 2303건이
    # 인시던트 리허설이라 사람이 볼 것을 못 찾았다.
    purpose: str = "unknown"
    # **승인이 집행으로 이어지는가.** 이 시스템의 HITL 은 "멈추고 기록"이지
    # "기다렸다 재개"가 아니다 — 워커는 승인 요청을 넣고 그 자리에서 run 을
    # 끝낸다(`return decision, None`). 큐를 지켜보는 것이 없으므로 나중에 눌러도
    # 그 행동은 실행되지 않는다.
    #
    # 이걸 화면이 말하지 않으면 사람이 **집행됐다고 믿는다.** 실측으로 `aoc.kill`
    # 2건이 승인돼 있었지만 아무 에이전트도 죽지 않았다 — 다행이었지만, 반대
    # 방향으로 틀렸다면(집행됐는데 안 됐다고 믿음) 훨씬 나빴다.
    run_ended: bool = False
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

    @property
    def decides_execution(self) -> bool:
        """지금 누르면 그 행동이 실제로 일어나는가."""
        return not self.run_ended

    @property
    def is_work(self) -> bool:
        """사람이 실제로 판단해야 하는 건가. 훈련은 큐에 쌓이되 앞에 안 온다."""
        return self.purpose == "work"

    def line(self) -> str:
        icon = {"pending": "⏳", "approved": "✔", "denied": "✘", "expired": "⌛"}[self.status]
        tag = "" if self.is_work else f"  ({self.purpose})"
        if self.run_ended and self.status == "pending":
            tag += "  [run 종료 — 승인해도 집행 안 됨]"
        return (
            f"{icon} {self.id}  {self.agent_id}  {self.skill}  "
            f"[{self.severity_label}/{self.severity}]  {self.status}{tag}"
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
        self, *, agent_id: str, skill: str, gate_decision, args: dict[str, Any],
        trace_id: str = "", purpose: str = "unknown"
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
            purpose=purpose,
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

    def list(self, status: str | None = None, *,
             purpose: str | None = None) -> list[Approval]:
        out = []
        for p in sorted(self.dir.glob("hitl-*.json")):
            ap = Approval.from_dict(json.loads(p.read_text(encoding="utf-8")))
            if status is not None and ap.status != status:
                continue
            if purpose is not None and ap.purpose != purpose:
                continue
            out.append(ap)
        return sorted(out, key=lambda a: a.requested_at, reverse=True)

    def pending(self, *, purpose: str | None = None) -> list[Approval]:
        """대기 중인 요청. `purpose="work"` 면 **사람이 실제로 볼 것만.**

        훈련을 안 지우고 걸러내는 이유: 리허설이 게이트를 제대로 때렸다는 사실
        자체가 증거라 지우면 안 되고, 그렇다고 섞어 두면 사람이 실업무를 못 찾는다.
        """
        return self.list("pending", purpose=purpose)

    def counts(self) -> dict[str, int]:
        """목적별 대기 수. 화면이 "실업무 3 · 훈련 2300" 을 말할 수 있게."""
        out: dict[str, int] = {}
        for ap in self.list("pending"):
            out[ap.purpose] = out.get(ap.purpose, 0) + 1
        return out

    def mark_run_ended(self, aid: str) -> None:
        """이 요청을 낸 run 이 끝났다 — 승인해도 집행되지 않는다는 사실을 남긴다.

        요청을 지우거나 만료시키지 **않는다.** 사람의 판단은 여전히 의미가 있다:
        "이런 행동을 허용할 것인가"는 다음 실행 정책(EG·게이트)의 근거가 되고,
        그 판단 이력이 곧 조직의 기억이다. 다만 **집행과 판단을 구별해야** 한다.
        """
        try:
            ap = self.get(aid)
        except KeyError:
            return
        ap.run_ended = True
        self._path(aid).write_text(
            json.dumps(ap.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    def expire(self, *, purpose: str | None = None, before: str = "",
               note: str = "") -> list[str]:
        """대기 요청을 **만료**시킨다. 승인도 거부도 아니다.

        왜 세 번째 상태가 필요한가 — 실측으로 대기 2636건 중 2303건이 인시던트
        리허설이었고 요청 내용은 `aoc.kill` 701 · `aoc.revoke_credentials` 703 ·
        `aoc.report_regulator` 249 였다. **승인하면 진짜로 집행된다.**

        그렇다고 `denied` 로 쓰면 거짓말이 된다 — 거부는 사람이 보고 아니라고
        판단했다는 뜻이다. 아무도 본 적 없는 요청에 그 기록을 남기면 나중에
        "이 사람은 kill 을 700번 거부했다"는 잘못된 판단 이력이 만들어진다.

        Args:
            purpose: 이 목적만 (예: `drill`). None 이면 전부.
            before: 이 시각(ISO) 이전 요청만.
            note: 왜 만료시켰는지. **비우지 마라** — 감사 기록이다.
        """
        if not note.strip():
            raise ValueError("만료 사유가 필요하다 — 왜 사람이 안 보기로 했는지 남긴다")
        done = []
        for ap in self.list("pending"):
            if purpose is not None and ap.purpose != purpose:
                continue
            if before and ap.requested_at >= before:
                continue
            ap.status = "expired"
            ap.decided_at = _now()
            ap.decided_by = "system"
            ap.note = note[:500]
            self._path(ap.id).write_text(
                json.dumps(ap.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            done.append(ap.id)
        return done

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
