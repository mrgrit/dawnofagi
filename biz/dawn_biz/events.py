"""업무 이벤트 → 에이전트 기동. **상시 폴링이 아니다.**

P2 `events.py` 의 디스패처를 그대로 쓰고 업무 트리거만 얹는다.
`*_WORK.md` §2(트리거) 절에 적힌 것이 여기 등록된다.

## 홈페이지 문의는 왜 "당겨" 오나

공개 홈페이지(L0)는 사내 DB 에 쓰지 않는다 — 쓰기 시작하면 dmz 앞단 프로세스가
내부 저장소로 가는 경로를 갖게 된다. 그래서 홈페이지는 **파일로 떨어뜨리고**
(`var/website/inquiries.jsonl`) 사내 쪽에서 `ingest_inquiries()` 로 당겨 온다.
방향이 한쪽뿐이면 실수로 뚫릴 자리가 없다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dawn_agents.events import Dispatcher, Event, Handler
from dawn_core import jsonl

from .store import BizStore
from .workers import (
    CRM_AGENT,
    EXPENSE_AGENT,
    PM_AGENT,
    WorkResult,
    coordinate_project,
    handle_expense,
    handle_inquiry,
)

# 이벤트 → 처리 함수. 디스패처의 Handler 는 "누가·무슨 업무"를 선언하고,
# 실제 실행은 업무 함수가 한다 (업무 함수가 저장소 갱신까지 책임진다).
ROUTES = {
    "crm.inquiry.new": ("inquiry", CRM_AGENT, "corporate/crm-inquiry", False),
    "crm.inquiry.reclassify": ("inquiry", CRM_AGENT, "corporate/crm-inquiry", False),
    "expense.submitted": ("expense", EXPENSE_AGENT, "corporate/expense-processing", True),
    "card.statement": ("expense", EXPENSE_AGENT, "corporate/expense-processing", True),
    "proj.task.changed": ("project", PM_AGENT, "engineering/project-coordination", False),
    "proj.coordinate": ("project", PM_AGENT, "engineering/project-coordination", False),
}


def business_dispatcher() -> Dispatcher:
    """업무 트리거만 등록된 디스패처 (P2 보안·개발 트리거와 별개)."""
    d = Dispatcher()
    for event_type, (kind, agent_id, work_id, l3) in ROUTES.items():
        d.on(Handler(
            event_type=event_type,
            work_id=work_id,
            agent_id=agent_id,
            build_task=_task_builder(kind),
            build_skills=None,
            touches_l3=l3,
        ))
    return d


def _task_builder(kind: str):
    def build(e: Event) -> str:
        return f"[{kind}] {json.dumps(e.payload, ensure_ascii=False)[:400]}"

    return build


def _subject(kind: str, payload: dict[str, Any]) -> Any:
    if kind == "inquiry":
        return payload.get("inquiry_id")
    if kind == "expense":
        return payload.get("request_id")
    return payload.get("project_key")


def run_event(event: Event, *, root: Path, tenant: int = 0) -> list[WorkResult]:
    """이벤트 하나로 해당 업무 에이전트를 기동한다.

    등록되지 않은 이벤트는 **아무 일도 일으키지 않는다** — 조용히 무시하지 않고
    빈 리스트를 돌려주어 호출부가 알 수 있게 한다.
    """
    route = ROUTES.get(event.type)
    if route is None:
        return []
    kind, agent_id, _work, _l3 = route
    subject = _subject(kind, event.payload)
    if subject in (None, ""):
        return [WorkResult(False, agent_id, f"{kind}:?",
                           error=f"이벤트 payload 에 대상이 없다 ({event.type})")]
    fn = {"inquiry": handle_inquiry, "expense": handle_expense,
          "project": coordinate_project}[kind]
    return [fn(subject, root=root, tenant=tenant)]


def ingest_inquiries(root: Path, *, tenant: int = 0) -> tuple[int, list[int]]:
    """홈페이지 문의 접수함 → CRM. 이미 들어온 것은 다시 넣지 않는다."""
    path = Path(root) / "var" / "website" / "inquiries.jsonl"
    if not path.is_file():
        return 0, []
    store = BizStore(root, tenant=tenant)
    seen = {(r["email"], r["message"][:120]) for r in store.inquiries(limit=1000)}
    new_ids: list[int] = []
    for rec in jsonl.read(path):
        key = (rec.get("email", ""), str(rec.get("message", ""))[:120])
        if key in seen:
            continue
        seen.add(key)
        new_ids.append(store.add_inquiry(
            name=rec.get("name", ""), email=rec.get("email", ""),
            org_name=rec.get("org", ""), message=rec.get("message", ""),
            source="website",
        ))
    return len(new_ids), new_ids


def ingest_work_requests(root: Path, *, tenant: int = 0) -> tuple[int, list[int]]:
    """홈페이지 작업 요청 접수함 → 작업 지시. 문의와 같은 방향이다.

    공개 사이트(zone:ext)는 사내 DB 에 손대지 않는다. 파일로 떨어뜨리고 **사내에서
    당겨 온다.** 방향이 반대면 공개면 취약점 하나가 내부 데이터까지 닿는다.

    승격된 작업 지시는 곧바로 `pending_approval` 이다 — 접수 자체가 결재 요청이다.
    """
    from dawn_core import workintake

    path = Path(root) / "var" / "website" / "work_requests.jsonl"
    if not path.is_file():
        return 0, []
    store = BizStore(root, tenant=tenant)
    seen = {(r["contact"], r["title"]) for r in store.work_orders(limit=1000)}
    new_ids: list[int] = []
    for rec in jsonl.read(path):
        key = (rec.get("email", ""), str(rec.get("title", "")))
        if key in seen or not rec.get("title"):
            continue
        try:                       # 접수 이후 매니페스트가 바뀌었을 수 있다
            division, tier = workintake.validate(
                root, business=rec.get("business", ""),
                infra_tier=rec.get("infra_tier", "none"))
        except ValueError:
            continue               # 규칙에 안 맞는 접수는 승격하지 않는다 (파일엔 남는다)
        seen.add(key)
        wid = store.add_work_order(
            title=rec.get("title", ""), body=rec.get("message", ""), origin="external",
            requester=rec.get("name", ""), requester_org=rec.get("org", ""),
            contact=rec.get("email", ""), business=rec.get("business", ""),
            division=division, infra_tier=tier)
        store.set_work_order_status(wid, "pending_approval")
        new_ids.append(wid)
    return len(new_ids), new_ids


__all__ = ["ROUTES", "business_dispatcher", "ingest_inquiries",
           "ingest_work_requests", "run_event"]
