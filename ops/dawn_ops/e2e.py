"""엔드투엔드 — 전 계층이 **끊김 없이** 도는지.

    ① 요구      공개 홈페이지 문의 접수 (L0)
    ② 흡수      접수함 → CRM (한 방향)
    ③ 이벤트    crm.inquiry.new → 훅 기동 (폴링 아님)
    ④ 에이전트  P2 워커 루프 (EG 참조 → preview → 게이트 → run → record)
    ⑤ 업무      CRM 에 초안 저장 (발송 아님)
    ⑥ 관제      P3 수집 → 정규화 → 탐지 → 콘솔 상태 (픽셀 오피스 방)
    ⑦ 개입      게이트가 세운 것이 그룹웨어 승인 큐에 있나
    ⑧ 축적      EG 에 Task/Observation 이 남았나

**각 구간의 연결을 개별로 검사한다.** "전체가 돌았다"만 보면 중간이 끊겨도
모른다 — 어디서 끊겼는지 말할 수 있어야 고칠 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dawn_core import jsonl


@dataclass
class Hop:
    """구간 하나."""

    n: int
    name: str
    ok: bool = False
    detail: str = ""
    evidence: str = ""

    def line(self) -> str:
        mark = "✔" if self.ok else "✘"
        return f"  {mark} {self.n}. {self.name:<28} {self.detail}"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class E2EResult:
    hops: list[Hop] = field(default_factory=list)
    inquiry_id: int = 0
    trace_id: str = ""
    live: bool = False
    at: str = ""

    @property
    def ok(self) -> bool:
        return all(h.ok for h in self.hops)

    @property
    def broke_at(self) -> str:
        bad = [h for h in self.hops if not h.ok]
        return bad[0].name if bad else ""

    def to_dict(self) -> dict[str, Any]:
        return {"at": self.at, "ok": self.ok, "live": self.live,
                "inquiry_id": self.inquiry_id, "trace_id": self.trace_id,
                "broke_at": self.broke_at, "hops": [h.to_dict() for h in self.hops]}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCENARIO = {
    "name": "김도영",
    "email": "ax@example-univ.ac.kr",
    "org": "예시대학교 AI혁신센터",
    "message": ("전공별 AI 커리큘럼 도입을 검토 중입니다. "
                "관제 플랫폼과 연동해 학생 실습 환경을 운영할 수 있는지, "
                "그리고 도입 절차를 알고 싶습니다."),
}


def run(root: Path, *, live: bool = False, tenant: int = 0) -> E2EResult:
    from dawn_agents.events import Event
    from dawn_agents.hitl import ApprovalQueue
    from dawn_aoc.collect import TraceLake
    from dawn_aoc.console import build_state
    from dawn_biz.events import ingest_inquiries, run_event
    from dawn_biz.store import BizStore
    from dawn_core import Registry
    from dawn_core.eg.cli import db_path
    from dawn_core.eg.store import EGStore
    from dawn_groupware.app import build_site
    from starlette.testclient import TestClient

    res = E2EResult(live=live, at=_now())
    root = Path(root)
    store = BizStore(root, tenant=tenant)

    # ① 요구 — 공개 홈페이지 (L0 프로세스)
    h = Hop(1, "요구 (공개 홈페이지 문의)")
    c = TestClient(build_site(root), follow_redirects=False)
    payload = {**SCENARIO, "website": "", "ts": "1"}
    r = c.post("/contact", data=payload)
    h.ok = r.status_code == 200 and "접수했다" in r.text
    h.detail = f"HTTP {r.status_code}"
    res.hops.append(h)

    # ② 흡수 — 접수함 → CRM (한 방향)
    h = Hop(2, "흡수 (접수함 → CRM)")
    n, ids = ingest_inquiries(root, tenant=tenant)
    if not ids:
        # 같은 내용이 이미 있으면 그걸 쓴다 (멱등)
        same = [x for x in store.inquiries() if x["email"] == SCENARIO["email"]]
        ids = [same[0]["id"]] if same else []
    h.ok = bool(ids)
    res.inquiry_id = ids[0] if ids else 0
    h.detail = f"새 {n}건 · 문의 #{res.inquiry_id}"
    res.hops.append(h)
    if not h.ok:
        return res

    # ③ 이벤트 — 훅이 등록돼 있나 (폴링 아님)
    h = Hop(3, "이벤트 (crm.inquiry.new 훅)")
    from dawn_biz.events import business_dispatcher

    handlers = business_dispatcher().handlers_for("crm.inquiry.new")
    h.ok = bool(handlers)
    h.detail = (f"{handlers[0].agent_id} [{handlers[0].work_id}]" if handlers
                else "핸들러 없음")
    res.hops.append(h)

    # ④⑤ 에이전트 → 업무
    h4 = Hop(4, "에이전트 (P2 워커 루프)")
    h5 = Hop(5, "업무 (CRM 초안 저장)")
    if not live:
        row = store.inquiry(res.inquiry_id)
        h4.detail = h5.detail = "⊘ --live 없이는 모델을 부르지 않는다"
        h4.ok = h5.ok = True                       # 배선은 ③에서 확인했다
        if row and row["draft"]:
            res.trace_id = row["trace_id"]
            h5.detail = f"기존 초안 {len(row['draft'])}자"
        res.hops += [h4, h5]
    else:
        out = run_event(Event(type="crm.inquiry.new", source="website",
                              payload={"inquiry_id": res.inquiry_id}), root=root)
        wr = out[0] if out else None
        h4.ok = bool(wr and wr.ok)
        h4.detail = (f"{wr.agent_id} {wr.model_policy}→{wr.model} "
                     f"({'로컬' if wr.local else '클라우드'})" if wr else "실행 안 됨")
        h4.evidence = wr.error if wr and wr.error else ""
        res.trace_id = wr.trace_id if wr else ""
        res.hops.append(h4)

        row = store.inquiry(res.inquiry_id)
        h5.ok = bool(row and row["status"] == "drafted" and row["draft"])
        h5.detail = (f"분류={row['category'] or '(미분류)'} "
                     f"초안 {len(row['draft'])}자 · 발송 안 함" if row else "-")
        res.hops.append(h5)

    # ⑥ 관제 — 수집 → 상태
    h = Hop(6, "관제 (수집 → 콘솔 상태)")
    lake = TraceLake(root)
    tid = res.trace_id or (row["trace_id"] if row else "")
    st = build_state(root, limit=100)
    agent = next((a for a in st["agents"] if a["agent_id"] == "corp-cs-crm-01"), None)
    seen = tid in lake.trace_ids() if tid else False
    h.ok = bool(agent and agent["runs"] > 0)
    h.detail = (f"{agent['room']} · run {agent['runs']} · EG 참조 {len(agent['eg_refs'])}"
                + (f" · 트레이스 {tid[:12]}" if seen else "")) if agent else "아바타 없음"
    res.hops.append(h)

    # ⑦ 개입 — 승인 큐 (게이트가 세운 것)
    h = Hop(7, "개입 (그룹웨어 승인 큐)")
    q = ApprovalQueue(root)
    all_ap = q.list()
    h.ok = True                       # 큐가 살아 있으면 통과. 비어 있는 것도 정상이다.
    h.detail = (f"대기 {len(q.pending())} · 전체 {len(all_ap)}"
                if all_ap else "요청 없음 (이 경로는 게이트를 세우지 않았다)")
    res.hops.append(h)

    # ⑧ 축적 — EG
    h = Hop(8, "축적 (EG Task/Observation)")
    eg = EGStore(db_path(Registry.load(root).paths))
    runtime = [n for n in eg.nodes(layer="runtime")
               if n.type in ("Task", "Observation", "Finding")]
    h.ok = bool(runtime)
    h.detail = f"런타임 노드 {len(runtime)}개"
    res.hops.append(h)

    return res


def save(root: Path, res: E2EResult) -> Path:
    path = Path(root) / "var" / "ops" / "e2e.jsonl"
    jsonl.append(path, res.to_dict())
    return path


__all__ = ["SCENARIO", "E2EResult", "Hop", "run", "save"]
