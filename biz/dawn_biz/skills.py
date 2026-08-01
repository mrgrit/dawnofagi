"""업무 스킬 — P2 스킬 레지스트리에 `doc.* crm.* proj.* asset.*` 를 얹는다.

새 실행 계층을 만들지 않는다. **P2 워커 루프를 그대로 탄다**:

    ① eg_search → ② skill_preview → ③ skill_run → ④ eg_record

그래야 업무 에이전트도 행동 게이트를 통과하고, 스팬을 뱉고, P3 관제 대상이 된다.
업무 시스템만 따로 도는 순간 그 부분은 관제 밖이다.

**비가역 스킬은 실행부를 비운다** (`run=None`): `crm.contract_sign`(계약 체결),
`asset.dispose`(자산 폐기). 게이트가 막는 것과 별개로 **코드 경로가 없다.**
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dawn_agents.skills import SkillRegistry, SkillResult

from .store import BizStore


def _fmt(rows: list, cols: tuple[str, ...], empty: str = "(없음)") -> str:
    if not rows:
        return empty
    head = " | ".join(cols)
    body = "\n".join(" | ".join(str(r.get(c, "")) for c in cols) for r in rows)
    return f"{head}\n{body}"


def register(reg: SkillRegistry, *, root: Path, tenant: int = 0) -> SkillRegistry:
    """P2 레지스트리에 업무 스킬을 등록한다."""
    store = BizStore(root, tenant=tenant)

    # ── doc.* — 문서·지식 ───────────────────────────────────────────────
    def doc_search(query: str, limit: int = 10, max_level: str = "L2") -> SkillResult:
        rows = store.search_documents(query, max_level=max_level, limit=limit)
        return SkillResult(
            True,
            _fmt(rows, ("id", "security_level", "title"), "(검색 결과 없음)"),
            meta={"hits": len(rows), "query": query},
        )

    def doc_read(doc_id: int, max_level: str = "L2") -> SkillResult:
        row = store.document(int(doc_id), max_level=max_level)
        if row is None:
            # 없는 문서인지 등급 초과인지 구분해서 알려주지 않는다
            return SkillResult(False, "", f"문서 {doc_id} 를 읽을 수 없다")
        return SkillResult(
            True, f"# {row['title']}\n\n{row['body']}",
            meta={"revision": row["revision"], "level": row["security_level"]},
        )

    def doc_write(title: str, body: str, tags: str = "", author: str = "agent",
                  security_level: str = "L1") -> SkillResult:
        did = store.add_document(title=title, body=body, tags=tags, author=author,
                                 security_level=security_level)
        return SkillResult(True, f"문서 {did} 작성됨: {title}", meta={"document_id": did})

    reg.register("doc.search", doc_search, arg_names=["query", "limit", "max_level"],
                 touches=["asset:knowledge"])
    reg.register("doc.read", doc_read, arg_names=["doc_id", "max_level"],
                 touches=["asset:knowledge"])
    reg.register("doc.write", doc_write,
                 arg_names=["title", "body", "tags", "author", "security_level"],
                 touches=["asset:knowledge"])

    # ── crm.* — 고객·문의·계약 ──────────────────────────────────────────
    def crm_customer_read(name: str = "", limit: int = 20) -> SkillResult:
        rows = store.customers(limit=limit)
        if name:
            rows = [r for r in rows if name in str(r.get("name", ""))]
        return SkillResult(True, _fmt(rows, ("id", "name", "segment", "contact_email")),
                           meta={"count": len(rows)})

    def crm_customer_write(name: str, segment: str = "", contact_name: str = "",
                           contact_email: str = "", note: str = "") -> SkillResult:
        cid = store.add_customer(name=name, segment=segment, contact_name=contact_name,
                                 contact_email=contact_email, note=note)
        return SkillResult(True, f"고객 {cid} 등록: {name}", meta={"customer_id": cid})

    def crm_inquiry_read(inquiry_id: int = 0, status: str = "") -> SkillResult:
        if inquiry_id:
            row = store.inquiry(int(inquiry_id))
            if row is None:
                return SkillResult(False, "", f"문의 {inquiry_id} 없음")
            return SkillResult(
                True,
                (f"문의 #{row['id']} [{row['status']}]\n"
                 f"보낸이: {row['name']} <{row['email']}> {row['org_name']}\n"
                 f"경로: {row['source']}\n\n{row['message']}"),
                meta={"inquiry_id": row["id"], "status": row["status"]},
            )
        rows = store.inquiries(status=status, limit=30)
        return SkillResult(True, _fmt(rows, ("id", "status", "name", "org_name", "message")),
                           meta={"count": len(rows)})

    def crm_inquiry_draft(inquiry_id: int, draft: str, category: str = "",
                          author: str = "agent", trace_id: str = "") -> SkillResult:
        """응답 **초안**만 저장한다. 발송(`comm.external_send`)은 별도 게이트다."""
        row = store.inquiry(int(inquiry_id))
        if row is None:
            return SkillResult(False, "", f"문의 {inquiry_id} 없음")
        store.set_inquiry_draft(int(inquiry_id), draft=draft, category=category,
                                drafted_by=author, trace_id=trace_id)
        return SkillResult(
            True,
            f"문의 {inquiry_id} 응답 초안 저장 (분류: {category or '미분류'}). "
            f"발송은 사람이 한다.",
            meta={"inquiry_id": int(inquiry_id), "category": category},
        )

    def crm_contract_read(limit: int = 30) -> SkillResult:
        rows = store.contracts(limit=limit)
        return SkillResult(True, _fmt(rows, ("id", "status", "title", "amount_krw")),
                           meta={"count": len(rows)})

    reg.register("crm.customer_read", crm_customer_read, arg_names=["name", "limit"],
                 touches=["asset:crm"])
    reg.register("crm.customer_write", crm_customer_write,
                 arg_names=["name", "segment", "contact_name", "contact_email", "note"],
                 touches=["asset:crm"])
    reg.register("crm.inquiry_read", crm_inquiry_read,
                 arg_names=["inquiry_id", "status"], touches=["asset:crm"])
    reg.register("crm.inquiry_draft", crm_inquiry_draft,
                 arg_names=["inquiry_id", "draft", "category", "author", "trace_id"],
                 touches=["asset:crm"])
    reg.register("crm.contract_read", crm_contract_read, arg_names=["limit"],
                 touches=["asset:contract"])
    # 체결은 비가역 — 실행부 없음. 사람이 그룹웨어/CLI 로 한다.
    reg.register("crm.contract_sign", None, touches=["asset:contract"])

    # ── proj.* — 프로젝트·이슈 ──────────────────────────────────────────
    def proj_read(project_key: str = "", status: str = "") -> SkillResult:
        if project_key:
            prj = store.project_by_key(project_key)
            if prj is None:
                return SkillResult(False, "", f"프로젝트 없음: {project_key}")
            rows = store.tasks(project_id=prj["id"], status=status)
            return SkillResult(
                True,
                f"{prj['key']} {prj['name']} [{prj['status']}]\n"
                + _fmt(rows, ("id", "status", "phase", "assignee", "title")),
                meta={"project_id": prj["id"], "tasks": len(rows)},
            )
        rows = store.projects()
        return SkillResult(True, _fmt(rows, ("id", "key", "name", "status", "owner_team")),
                           meta={"count": len(rows)})

    def proj_task_write(project_key: str, title: str, body: str = "",
                        phase: str = "build", depends_on: str = "",
                        assignee: str = "") -> SkillResult:
        prj = store.project_by_key(project_key)
        if prj is None:
            return SkillResult(False, "", f"프로젝트 없음: {project_key}")
        tid = store.add_task(project_id=prj["id"], title=title, body=body, phase=phase,
                             depends_on=depends_on, assignee=assignee)
        return SkillResult(True, f"태스크 {tid} 생성: {title}", meta={"task_id": tid})

    def proj_close(task_id: int, result: str, trace_id: str = "") -> SkillResult:
        """종료에는 **근거가 필수**다. 근거 없는 완료는 완료가 아니다."""
        if not result.strip():
            return SkillResult(False, "", "종료 근거(result)가 비었다 — 근거 없이 닫지 않는다")
        store.update_task(int(task_id), status="done", result=result, trace_id=trace_id)
        return SkillResult(True, f"태스크 {task_id} 종료", meta={"task_id": int(task_id)})

    reg.register("proj.read", proj_read, arg_names=["project_key", "status"],
                 touches=["asset:project"])
    reg.register("proj.task_write", proj_task_write,
                 arg_names=["project_key", "title", "body", "phase", "depends_on",
                            "assignee"],
                 touches=["asset:project"])
    reg.register("proj.close", proj_close, arg_names=["task_id", "result", "trace_id"],
                 touches=["asset:project"])

    # ── asset.* — 자산 대장 ─────────────────────────────────────────────
    def asset_read(limit: int = 50) -> SkillResult:
        rows = store.fixed_assets(limit=limit)
        return SkillResult(True, _fmt(rows, ("id", "tag", "name", "holder", "status")),
                           meta={"count": len(rows)})

    def asset_write(tag: str, name: str, kind: str = "", holder: str = "",
                    acquired_on: str = "", amount_krw: int = 0) -> SkillResult:
        aid = store.add_fixed_asset(tag=tag, name=name, kind=kind, holder=holder,
                                    acquired_on=acquired_on, amount_krw=int(amount_krw))
        return SkillResult(True, f"자산 {aid} 등록: {tag} {name}", meta={"asset_id": aid})

    reg.register("asset.read", asset_read, arg_names=["limit"],
                 touches=["asset:fixed-asset"])
    reg.register("asset.write", asset_write,
                 arg_names=["tag", "name", "kind", "holder", "acquired_on", "amount_krw"],
                 touches=["asset:fixed-asset"])
    reg.register("asset.dispose", None, touches=["asset:fixed-asset"])  # 비가역

    # ── fin.* — P2 데모 픽스처를 **실제 업무 DB 로** 갈아끼운다 ──────────
    #
    # 같은 이름으로 다시 등록하면 교체된다. 업무 시스템이 생겼는데 에이전트가
    # 계속 데모 파일을 읽으면, 산출물의 숫자가 장부와 다르다 — 그게 제일 나쁘다.
    def fin_expense_read(request_id: str = "", limit: int = 20) -> SkillResult:
        if request_id:
            row = store.expense_by_request(request_id)
            if row is None:
                return SkillResult(False, "", f"경비 신청 없음: {request_id}")
            body = "\n".join(f"{k}: {row.get(k)}" for k in
                             ("request_id", "requester", "requester_org", "amount_krw",
                              "category", "receipt_id", "ledger_entry", "status",
                              "created_at"))
            return SkillResult(True, body, meta={"amount_krw": row["amount_krw"],
                                                 "request_id": row["request_id"]})
        rows = store.expenses(limit=limit)
        return SkillResult(True, _fmt(rows, ("request_id", "status", "amount_krw",
                                             "category", "receipt_id")),
                           meta={"count": len(rows)})

    def fin_ledger_read(limit: int = 30) -> SkillResult:
        rows = store.expenses(limit=limit)
        posted = [r for r in rows if r.get("ledger_entry")]
        return SkillResult(True, _fmt(posted, ("request_id", "ledger_entry", "amount_krw"),
                                      "(원장 기입된 건 없음 — 기입은 사람 승인 후)"),
                           meta={"count": len(posted)})

    def fin_expense_write(request_id: str, verdict: str, status: str = "drafted") -> SkillResult:
        """전표 **초안**만 쓴다. 원장 기입(fin.ledger_write)은 실행부가 없다."""
        if store.expense_by_request(request_id) is None:
            return SkillResult(False, "", f"경비 신청 없음: {request_id}")
        store.set_expense_verdict(request_id, verdict=verdict, status=status,
                                  processed_by="agent")
        return SkillResult(True, f"{request_id} 전표 초안 저장 (상태 {status}). "
                                 f"원장 기입은 사람이 한다.")

    reg.register("fin.expense_read", fin_expense_read,
                 arg_names=["request_id", "limit"], touches=["asset:ledger"])
    reg.register("fin.ledger_read", fin_ledger_read, arg_names=["limit"],
                 touches=["asset:ledger"])
    reg.register("fin.expense_write", fin_expense_write,
                 arg_names=["request_id", "verdict", "status"], touches=["asset:ledger"])

    return reg



def build_registry(root: Path, *, eg_store=None, tenant: int = 0) -> SkillRegistry:
    """P2 기본 스킬 + P5 업무 스킬. 워커가 이걸 쓴다."""
    from dawn_agents.skills import build_default_registry
    from dawn_core import Registry

    catalog = Registry.load(root).tool_catalog
    reg = build_default_registry(catalog, root=root, eg_store=eg_store)
    return register(reg, root=root, tenant=tenant)


def expense_facts(root: Path, request_id: str, *, tenant: int = 0) -> dict[str, Any]:
    """경비 1건의 사실 — 워커 프롬프트에 넣을 원본 필드.

    L3 다. 이 dict 가 클라우드 모델에 가면 `pol:l3-local-only` 위반이므로
    호출부는 반드시 `touches_l3=True` 로 워커를 돌려야 한다.
    """
    store = BizStore(root, tenant=tenant)
    row = store.expense_by_request(request_id)
    if row is None:
        return {}
    return {k: row.get(k) for k in
            ("request_id", "requester", "requester_org", "amount_krw", "category",
             "receipt_id", "ledger_entry", "status")}


__all__ = ["build_registry", "expense_facts", "register"]
