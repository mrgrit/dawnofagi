"""이벤트 구동 — 에이전트는 **훅으로 기동한다. 상시 폴링이 아니다.**

P2 지시문 §5: "상시 시뮬레이션이 아니라 훅/이벤트로 기동. 반복 업무는 큐로."
`*_WORK.md` 의 트리거 절이 여기 등록된다.

두 개의 진입점:
    Dispatcher.emit(event)   외부 시스템이 이벤트를 밀어 넣는다 (웹훅·SIEM·그룹웨어)
    Queue                    반복 업무를 쌓아 두고 워커가 꺼내 간다

**폴링 루프는 이 파일에 없다.** 있으면 그건 결함이다 — 큐를 소비하는 것은
외부 스케줄러(Temporal/Celery, P5)이거나 사람이다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Event:
    """업무를 촉발하는 사건 하나."""

    type: str  # 예: siem.alert / expense.submitted
    source: str  # wazuh | groupware | manual | …
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = ""
    at: str = ""

    def __post_init__(self) -> None:
        self.id = self.id or f"evt-{uuid.uuid4().hex[:10]}"
        self.at = self.at or datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "at": self.at,
            "payload": self.payload,
        }


@dataclass
class Handler:
    """어떤 이벤트가 어느 업무를 깨우나 (`*_WORK.md` 의 트리거 절)."""

    event_type: str
    work_id: str
    agent_id: str
    build_task: Callable[[Event], str]
    build_skills: Callable[[Event], list[tuple[str, dict]]] | None = None
    touches_l3: bool = False


class Dispatcher:
    """이벤트 → 핸들러 → 워커 기동. 폴링 없음."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self.log: list[dict[str, Any]] = []

    def on(self, handler: Handler) -> None:
        self._handlers.setdefault(handler.event_type, []).append(handler)

    def handlers_for(self, event_type: str) -> list[Handler]:
        return list(self._handlers.get(event_type, []))

    def registered(self) -> dict[str, list[str]]:
        return {k: [h.work_id for h in v] for k, v in sorted(self._handlers.items())}

    def emit(self, event: Event, *, run_worker: Callable[[Handler, Event], Any] | None = None):
        """이벤트 하나를 처리한다. 등록된 핸들러가 없으면 아무 일도 안 일어난다."""
        hs = self.handlers_for(event.type)
        self.log.append({"event": event.to_dict(), "handlers": [h.work_id for h in hs]})
        if not hs or run_worker is None:
            return []
        return [run_worker(h, event) for h in hs]


class WorkQueue:
    """반복 업무 큐 — 파일 기반. 소비는 외부 스케줄러/사람이 한다."""

    def __init__(self, root: Path) -> None:
        self.dir = Path(root) / "var" / "queue"
        self.dir.mkdir(parents=True, exist_ok=True)

    def push(self, event: Event) -> Path:
        p = self.dir / f"{event.at.replace(':', '')}-{event.id}.json"
        p.write_text(
            json.dumps(event.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return p

    def peek(self, limit: int = 20) -> list[Event]:
        out = []
        for p in sorted(self.dir.glob("*.json"))[:limit]:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append(Event(**d))
        return out

    def pop(self) -> Event | None:
        files = sorted(self.dir.glob("*.json"))
        if not files:
            return None
        p = files[0]
        d = json.loads(p.read_text(encoding="utf-8"))
        p.unlink()
        return Event(**d)

    def depth(self) -> int:
        return len(list(self.dir.glob("*.json")))


# ── 기본 핸들러 — `*_WORK.md` 의 트리거 절을 코드로 ──────────────────────


def default_dispatcher() -> Dispatcher:
    """P2 데모가 쓰는 트리거 등록."""
    d = Dispatcher()

    # security/alert-triage §2 트리거
    for et in ("siem.alert", "ips.signature", "waf.block", "aoc.anomaly", "manual.report"):
        d.on(
            Handler(
                event_type=et,
                work_id="security/alert-triage",
                agent_id="ccc-soc-triage-01",
                build_task=lambda e: (
                    f"알럿 트리아지: {e.payload.get('summary', e.type)}\n"
                    f"소스={e.source} alert_id={e.payload.get('alert_id', '?')} "
                    f"자산={e.payload.get('assets', [])}\n"
                    f"원문: {json.dumps(e.payload, ensure_ascii=False)[:800]}"
                ),
                build_skills=lambda e: [("sec.siem_query", {"limit": 5})],
            )
        )

    # corporate/expense-processing §2 트리거
    for et in ("expense.submitted", "card.statement", "evidence.uploaded"):
        d.on(
            Handler(
                event_type=et,
                work_id="corporate/expense-processing",
                agent_id="corp-admin-clerk-01",
                build_task=lambda e: (
                    f"경비 처리: 신청 {e.payload.get('request_id', '?')}\n"
                    f"금액={e.payload.get('amount_krw', '?')}원 "
                    f"거래처={e.payload.get('vendor', '?')} "
                    f"증빙={e.payload.get('evidence_ids', [])}\n"
                    f"신청 내용: {json.dumps(e.payload, ensure_ascii=False)[:800]}"
                ),
                build_skills=lambda e: [
                    ("fin.expense_read", {"request_id": e.payload.get("request_id", "")}),
                ],
                touches_l3=True,
            )
        )

    # engineering/feature-build 트리거
    d.on(
        Handler(
            event_type="dod.assigned",
            work_id="engineering/feature-build",
            agent_id="aoc-dev-builder-01",
            build_task=lambda e: (
                f"기능 구현: {e.payload.get('dod_item', '?')} ({e.payload.get('phase', '?')})\n"
                f"범위: {e.payload.get('scope', '')}"
            ),
        )
    )
    return d
