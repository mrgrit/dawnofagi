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

# 큐에 실제로 남는 목적. 워커의 RUN_PURPOSES 에 "test" 를 더한 것이다 —
# 워커가 test 목적으로 도는 일은 없어야 하지만, 테스트 픽스처가 **실 큐에**
# 레코드를 남기는 일은 실제로 일어난다(실측 26건, apps/groupware/tests).
# 그걸 unknown 으로 두면 사람이 볼 실업무와 섞인다.
QUEUE_PURPOSES = ("work", "drill", "redteam", "demo", "test")

# 이 저장소가 **스스로 짓는** 합성 트레이스 접두어. 추론이 아니라 출처가 있다 —
# 바깥에서 들어온 트레이스에는 이런 접두어가 없으므로 여기 걸리지 않는다.
SYNTHETIC_TRACE_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("rehearsal-", "drill", "ops/dawn_ops/rehearsal.py"),
    ("redteam-", "redteam", "ops/dawn_ops/redteam.py"),
    ("demo-", "demo", "scripts/lib/demo_two_orgs.py"),
    ("p4-test", "test", "apps/groupware/tests/test_web.py"),
    ("test-", "test", "테스트 픽스처"),
)


def purpose_from_trace_id(trace_id: str) -> tuple[str, str]:
    """트레이스 ID 만으로 목적을 판별한다. 모르면 ("", "")."""
    for prefix, purpose, origin in SYNTHETIC_TRACE_PREFIXES:
        if trace_id.startswith(prefix):
            return purpose, f"트레이스 접두어 {prefix!r} ({origin})"
    return "", ""


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

    def backfill_purpose(self, *, resolver=None, apply: bool = False) -> dict[str, Any]:
        """`purpose == "unknown"` 인 과거 요청에 목적을 채운다.

        왜 필요한가 — 목적 태그는 2026-08-02 에 들어왔고 그 전 레코드는 전부
        unknown 이다(실측 211/212). 화면은 `pending(purpose="work")` 로 실업무만
        걸러 주지만, **걸러낼 근거가 레코드에 없으면 필터가 아무 일도 하지 않는다.**
        태그를 넣어 놓고 백필을 안 하면 넣은 적 없는 것과 같다.

        **증거 순서로만 정한다. 추측으로 채우지 않는다:**

          1. `resolver` — 케이스·트레이스 레이크 조회 (호출자가 주입한다.
             이 모듈은 `dawn_aoc` 를 임포트할 수 없다 — aoc 가 agents 에
             의존하므로 반대로 부르면 순환이다.)
          2. 트레이스 접두어 — 이 저장소가 직접 지은 이름
          3. 못 정하면 **unknown 으로 남긴다.** 틀린 태그는 필터를 조용히
             망가뜨리므로 빈 값보다 나쁘다.

        `apply=False` 면 아무것도 쓰지 않고 계획만 돌려준다.
        """
        changed: list[dict[str, str]] = []
        unresolved: list[dict[str, str]] = []
        for ap in self.list():
            if ap.purpose in QUEUE_PURPOSES:
                continue
            purpose = source = ""
            if resolver is not None:
                purpose, source = resolver(ap)
            if not purpose:
                purpose, source = purpose_from_trace_id(ap.trace_id)
            row = {"id": ap.id, "trace_id": ap.trace_id, "skill": ap.skill,
                   "status": ap.status, "purpose": purpose, "source": source}
            if not purpose:
                unresolved.append(row)
                continue
            changed.append(row)
            if apply:
                ap.purpose = purpose
                self._path(ap.id).write_text(
                    json.dumps(ap.to_dict(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")

        by: dict[str, int] = {}
        for r in changed:
            by[r["purpose"]] = by.get(r["purpose"], 0) + 1
        return {"applied": apply, "total": len(self.list()),
                "changed": changed, "unresolved": unresolved, "by_purpose": by}

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
