"""업무 에이전트 실행 — **P2 워커 루프를 그대로 탄다.**

새 실행 경로를 만들지 않는다. `Worker` 에 업무 스킬 레지스트리를 끼워 넣을 뿐이다.
그래야 업무 에이전트도

* 행동 게이트를 통과하고 (비가역·L3 는 HITL),
* OTel 스팬을 뱉고 (P3 수집 계층의 입력),
* 픽셀 오피스의 자기 방에 나타난다.

업무 시스템만 따로 도는 순간 그 부분은 관제 밖이다.

## L3 경로

경비(`asset:ledger`, L3)를 다루는 워커는 `touches_l3=True` 로 돈다.
그러면 `llm.resolve()` 가 **호출 전에** 클라우드 모델을 막고(`pol:l3-local-only`),
경리총무팀 게이트의 `model.policy: local_only` 와 합쳐져 사내 GPU 로만 나간다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dawn_core.paths import Paths

from .skills import build_registry
from .store import BizStore


@dataclass
class WorkResult:
    """업무 처리 1건의 결과 — 업무 시스템이 읽는 형태."""

    ok: bool
    agent_id: str
    subject: str                       # 무엇을 처리했나 (문의 id · 경비 번호 …)
    trace_id: str = ""
    model: str = ""
    model_policy: str = ""
    local: bool = False
    output: str = ""
    hitl_ids: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def line(self) -> str:
        mark = "✔" if self.ok else "✘"
        loc = "로컬" if self.local else "클라우드"
        return (f"  {mark} {self.agent_id:<20} {self.subject:<22} "
                f"{self.model_policy}→{self.model} ({loc})  "
                f"HITL {len(self.hitl_ids)}  {self.error}")


def _worker(agent_id: str, root: Path, tenant: int):
    from dawn_agents import Worker
    from dawn_core import Registry
    from dawn_core.eg.cli import db_path
    from dawn_core.eg.store import EGStore

    registry = Registry.load(root)
    db = db_path(registry.paths)
    eg = EGStore(db) if db.is_file() else None
    skills = build_registry(root, eg_store=eg, tenant=tenant)
    return Worker(agent_id, registry=registry, eg_store=eg, skills=skills)


def _result(agent_id: str, subject: str, run, error: str = "") -> WorkResult:
    provider = getattr(run, "provider", "") or ""
    return WorkResult(
        ok=bool(run and run.complete and not run.error and not error),
        agent_id=agent_id, subject=subject,
        trace_id=getattr(run, "trace_id", ""),
        model=getattr(run, "model", ""), model_policy=getattr(run, "model_policy", ""),
        local=provider == "ollama",          # 사내 GPU 로 나갔나 (L3 경로 확인용)
        output=getattr(run, "output", "") or "",
        hitl_ids=list(getattr(run, "hitl_requests", []) or []),
        blocked=list(getattr(run, "blocked", []) or []),
        error=error or getattr(run, "error", "") or "",
    )


# ── CRM: 문의 처리 ───────────────────────────────────────────────────────

CRM_AGENT = "corp-cs-crm-01"


def handle_inquiry(inquiry_id: int, *, root: Path | None = None,
                   tenant: int = 0) -> WorkResult:
    """새 문의 1건을 처리한다. **초안까지.** 발송하지 않는다."""
    root = Path(root) if root else Paths().root
    store = BizStore(root, tenant=tenant)
    row = store.inquiry(int(inquiry_id))
    if row is None:
        return WorkResult(False, CRM_AGENT, f"inquiry:{inquiry_id}",
                          error=f"문의 {inquiry_id} 없음")

    w = _worker(CRM_AGENT, root, tenant)
    task = (
        f"고객 문의 #{row['id']} 를 corporate/crm-inquiry 절차대로 처리하라.\n"
        f"분류(도입문의·기술문의·견적요청·장애신고·기타)를 정하고, "
        f"산출물 템플릿에 맞춰 **응답 초안**을 작성하라.\n"
        f"금액·납기·기능 유무는 근거 문서가 있을 때만 쓴다. 발송하지 않는다."
    )
    run = w.run(task, touches_l3=False, extra_skills=[
        ("crm.inquiry_read", {"inquiry_id": int(inquiry_id)}),
        ("doc.search", {"query": row["message"][:60] or "도입", "limit": 5}),
    ])

    res = _result(CRM_AGENT, f"inquiry:{inquiry_id}", run)
    if res.ok and res.output:
        store.set_inquiry_draft(
            int(inquiry_id), draft=res.output,
            category=_extract_category(res.output),
            drafted_by=CRM_AGENT, trace_id=res.trace_id,
        )
    return res


CATEGORIES = ("도입문의", "기술문의", "견적요청", "장애신고", "기타")


def _extract_category(text: str) -> str:
    """산출물에서 분류를 읽는다. 못 읽으면 **비워 둔다** — 지어내지 않는다."""
    head = text[:600]
    for c in CATEGORIES:
        if c in head:
            return c
    return ""


# ── 경리: 경비 처리 (L3) ─────────────────────────────────────────────────

EXPENSE_AGENT = "corp-admin-clerk-01"


def handle_expense(request_id: str, *, root: Path | None = None,
                   tenant: int = 0) -> WorkResult:
    """경비 1건. **L3 다** — 로컬 모델 전용 + 금액 임계 HITL."""
    root = Path(root) if root else Paths().root
    store = BizStore(root, tenant=tenant)
    row = store.expense_by_request(request_id)
    if row is None:
        return WorkResult(False, EXPENSE_AGENT, f"expense:{request_id}",
                          error=f"경비 {request_id} 없음")

    w = _worker(EXPENSE_AGENT, root, tenant)
    task = (
        f"경비 신청 {request_id} 를 corporate/expense-processing 절차대로 처리하라.\n"
        f"금액 임계(10만원) 초과 여부를 판정하고 근거를 밝혀라. "
        f"3자 대조 결과와 판정 근거가 된 원본 필드를 명시하라.\n"
        f"원장 기입(fin.ledger_write)은 하지 않는다 — 초안까지가 끝이다."
    )
    # touches_l3=True — 클라우드 모델로 나가는 경로를 **호출 전에** 막는다
    run = w.run(task, touches_l3=True, extra_skills=[
        ("fin.expense_read", {"request_id": request_id}),
    ])

    res = _result(EXPENSE_AGENT, f"expense:{request_id}", run)
    over = int(row["amount_krw"]) > 100_000
    status = "needs_approval" if (over or res.hitl_ids) else "processed"
    store.set_expense_verdict(
        request_id, verdict=res.output[:8000], status=status,
        processed_by=EXPENSE_AGENT, hitl_id=(res.hitl_ids or [""])[0],
        trace_id=res.trace_id,
    )
    return res


# ── 프로젝트: 조율 ───────────────────────────────────────────────────────

PM_AGENT = "aoc-dev-pm-01"


def coordinate_project(project_key: str, *, root: Path | None = None,
                       tenant: int = 0) -> WorkResult:
    """프로젝트 1건을 조율한다. **오케스트레이터는 산출물을 만들지 않는다.**"""
    root = Path(root) if root else Paths().root
    store = BizStore(root, tenant=tenant)
    prj = store.project_by_key(project_key)
    if prj is None:
        return WorkResult(False, PM_AGENT, f"project:{project_key}",
                          error=f"프로젝트 {project_key} 없음")

    w = _worker(PM_AGENT, root, tenant)
    task = (
        f"프로젝트 {project_key} 를 engineering/project-coordination 절차대로 조율하라.\n"
        f"todo 태스크의 depends_on 을 보고 배정 가능/불가를 판정하고, "
        f"**배정하지 않은 것마다 이유를 써라**. 산출물 템플릿을 따르라.\n"
        f"너는 코드나 문서를 만들지 않는다."
    )
    run = w.run(task, touches_l3=False, extra_skills=[
        ("proj.read", {"project_key": project_key}),
    ])
    return _result(PM_AGENT, f"project:{project_key}", run)


def assignable(store: BizStore, project_key: str) -> tuple[list, list]:
    """배정 가능한 태스크와 막힌 태스크. **코드로 계산한다** — 모델에게 안 맡긴다.

    의존 그래프 판정은 결정적이어야 한다. 모델이 위상 정렬을 하면 가끔 틀리고,
    그 가끔이 잘못된 순서로 배포되는 순간이다.
    """
    prj = store.project_by_key(project_key)
    if prj is None:
        return [], []
    tasks = store.tasks(project_id=prj["id"])
    done = {t["id"] for t in tasks if t["status"] == "done"}
    ready, blocked = [], []
    for t in tasks:
        if t["status"] != "todo":
            continue
        deps = [int(x) for x in str(t["depends_on"]).split(",") if x.strip().isdigit()]
        missing = [d for d in deps if d not in done]
        (blocked if missing else ready).append(
            (t, missing) if missing else (t, [])
        )
    return [t for t, _ in ready], blocked


__all__ = [
    "CATEGORIES",
    "CRM_AGENT",
    "EXPENSE_AGENT",
    "PM_AGENT",
    "WorkResult",
    "assignable",
    "coordinate_project",
    "handle_expense",
    "handle_inquiry",
]
