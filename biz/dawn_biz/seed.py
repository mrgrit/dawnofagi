"""데모 업무 데이터 — **레지스트리와 EG 에서 끌어온다.**

지어낸 고객·프로젝트를 넣지 않는다. 사업(`org/businesses/*.yaml`)의 로드맵이
프로젝트가 되고, 그 사업의 대상 세그먼트가 고객 구분이 된다. 사업을 추가하면
프로젝트가 따라 붙는다 — "사업은 플러그인"이 여기서도 유지된다.

문서는 P0~P4 가 실제로 만든 통제 문서를 가리킨다 (사본이 아니라 요약 + 경로).
"""

from __future__ import annotations

from pathlib import Path

from dawn_core import Registry

from .store import BizStore

# 자사(테넌트 #0)의 첫 고객은 자기 자신이다 — "우리가 먼저 AX 된다"(헌장 원리 #3).
SELF_CUSTOMER = {
    "name": "the dawn of AGI (자사)",
    "segment": "internal",
    "contact_name": "경영관리부",
    "note": "테넌트 #0 = 레퍼런스 구현. 모든 제품은 여기서 먼저 검증한다.",
    "owner_org": "org:mgmt",
}

# 문서는 실제 파일을 가리킨다. 본문에 사본을 만들면 두 벌이 갈라진다.
DOC_POINTERS = [
    ("회사 헌법 — COMPANY.md", "COMPANY.md", "거버넌스,L1,헌장", "L1",
     "전사 에이전트 헌법. 모든 에이전트의 시스템 프롬프트에 주입된다."),
    ("통제 평면 사용법", "docs/governance/CONTROL_PLANE.md", "거버넌스,게이트", "L1",
     "L1~L4 4계층과 gate.yaml 병합 규칙. 에이전트 행동을 바꾸는 법."),
    ("관제 운영 안내", "docs/governance/AOC_OPERATIONS.md", "관제,운영", "L1",
     "픽셀 오피스 읽는 법, 케이스 대응, 킬 스위치."),
    ("그룹웨어 사용법", "docs/governance/PORTAL_GUIDE.md", "그룹웨어,승인,EG", "L1",
     "승인 큐·EG 조정·권한 모델."),
    ("경비 처리 절차", "work/corporate/EXPENSE_PROCESSING_WORK.md", "경리,L3,SOP", "L2",
     "L3 데이터. 로컬 모델 전용, 10만원 임계 HITL."),
    ("고객 문의 처리 절차", "work/corporate/CRM_INQUIRY_WORK.md", "CRM,SOP", "L1",
     "분류 5종, 초안까지. 발송·계약·금액 약속 금지."),
]


def seed_all(root: Path, *, tenant: int = 0, force: bool = False) -> int:
    root = Path(root)
    store = BizStore(root, tenant=tenant)
    counts = store.counts()
    if any(counts.values()) and not force:
        return 0

    reg = Registry.load(root)
    n = 0

    # ── 문서 — 실제 통제 문서를 가리킨다 ────────────────────────────────
    for title, path, tags, level, summary in DOC_POINTERS:
        f = root / path
        body = (
            f"{summary}\n\n"
            f"**원본**: `{path}`" + (f" ({f.stat().st_size:,}바이트)" if f.is_file()
                                    else "  ⚠ 파일 없음") + "\n\n"
            "이 항목은 **포인터**다. 본문 사본을 두지 않는다 — 두 벌이 되면 갈라진다.\n"
            "고칠 때는 원본을 고치고, 그 변경은 리뷰를 거친다."
        )
        store.add_document(title=title, body=body, tags=tags, author="system",
                           org="org:biz-support", security_level=level)
        n += 1

    # ── 고객 — 자사 + 사업별 대상 세그먼트 ──────────────────────────────
    store.add_customer(**SELF_CUSTOMER)
    n += 1
    segs: dict[str, list[str]] = {}
    for b in reg.businesses.values():
        for s in b.data.get("target_segments") or []:
            segs.setdefault(s, []).append(b.data["name"])
    for seg, bizs in sorted(segs.items()):
        store.add_customer(
            name=f"(잠재) {seg} 세그먼트",
            segment=seg,
            note=f"대상 사업: {', '.join(sorted(set(bizs)))}. "
                 f"실제 고객이 아니라 사업 레지스트리에서 파생된 **세그먼트 자리**다.",
            owner_org="org:mgmt",
        )
        n += 1

    # ── 프로젝트 — 사업 로드맵이 곧 프로젝트다 ──────────────────────────
    for bid, b in sorted(reg.businesses.items()):
        d = b.data
        owner = (d.get("owning_divisions") or ["aoc"])[0]
        team = next((t for t in reg.teams.values()
                     if t.data["division"] == owner), None)
        pid = store.add_project(
            key=bid.upper().replace("-", "_"),
            name=d.get("name", bid),
            business=bid,
            owner_team=team.data["id"] if team else "",
        )
        n += 1
        prev = 0
        for phase in d.get("roadmap") or []:
            tid = store.add_task(
                project_id=pid,
                title=phase.get("phase", ""),
                body=phase.get("goal", ""),
                phase="build",
                depends_on=str(prev) if prev else "",
            )
            if phase.get("status") == "in_progress":
                store.update_task(tid, status="doing")
            prev = tid
            n += 1

    # ── 경비 — P2 데모가 쓰던 신청 번호와 같은 것 (사본이 아니라 같은 사실) ──
    store.add_expense(request_id="EXP-2026-0801-001", requester="corp-admin",
                      requester_org="org:ga", amount_krw=87_000, category="교통비",
                      receipt_id="RC-2026-0801-77")
    store.add_expense(request_id="EXP-2026-0801-002", requester="corp-admin",
                      requester_org="org:ga", amount_krw=1_250_000, category="장비",
                      receipt_id="RC-2026-0801-78")
    n += 2

    # ── 자산 대장 ───────────────────────────────────────────────────────
    store.add_fixed_asset(tag="SRV-GPU-01", name="사내 GPU 서버 (ollama)",
                          kind="server", holder="org:it-dc",
                          acquired_on="2026-01-15", amount_krw=0)
    store.add_fixed_asset(tag="LAB-EL34", name="el34 보안 실습·운영 인프라",
                          kind="infra", holder="org:it-dc",
                          acquired_on="2025-11-01", amount_krw=0)
    n += 2

    return n


__all__ = ["DOC_POINTERS", "seed_all"]
